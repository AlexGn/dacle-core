"""
DACLE Polymarket CTF Executor
Direct contract interaction for CTF Exchange on Polygon.
Handles: splitPosition (minting YES/NO from USDC.e) and mergePositions (merging YES/NO to USDC.e).
"""

import logging
import os
import time
import asyncio
import json
from typing import Any, Dict, Optional, List
from datetime import datetime, timezone
from web3 import Web3
from eth_account import Account
from decimal import Decimal

logger = logging.getLogger(__name__)

# CTF Exchange ABI (minimal for split/merge)
CTF_EXCHANGE_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "collateralToken", "type": "address"},
            {"internalType": "bytes32", "name": "parentCollectionId", "type": "bytes32"},
            {"internalType": "bytes32", "name": "conditionId", "type": "bytes32"},
            {"internalType": "uint256[]", "name": "partition", "type": "uint256[]"},
            {"internalType": "uint256", "name": "amount", "type": "uint256"}
        ],
        "name": "splitPosition",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "address", "name": "collateralToken", "type": "address"},
            {"internalType": "bytes32", "name": "parentCollectionId", "type": "bytes32"},
            {"internalType": "bytes32", "name": "conditionId", "type": "bytes32"},
            {"internalType": "uint256[]", "name": "partition", "type": "uint256[]"},
            {"internalType": "uint256", "name": "amount", "type": "uint256"}
        ],
        "name": "mergePositions",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "address", "name": "account", "type": "address"},
            {"internalType": "uint256", "name": "id", "type": "uint256"}
        ],
        "name": "balanceOf",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "address", "name": "collateralToken", "type": "address"},
            {"internalType": "bytes32", "name": "parentCollectionId", "type": "bytes32"},
            {"internalType": "bytes32", "name": "conditionId", "type": "bytes32"},
            {"internalType": "uint256[]", "name": "indexSets", "type": "uint256[]"}
        ],
        "name": "getPositionId",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "pure",
        "type": "function"
    }
]

# ERC20 ABI (minimal for allowance)
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function"
    },
    {
        "constant": False,
        "inputs": [
            {"name": "_spender", "type": "address"},
            {"name": "_value", "type": "uint256"}
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function"
    }
]

class PolymarketCTFExecutor:
    # Constants for Polymarket on Polygon
    CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
    USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
    
    # Stable public RPC fallbacks (unauthenticated endpoints).
    RPC_FALLBACKS = [
        "https://polygon.llamarpc.com",
        "https://1rpc.io/polygon",
        "https://polygon-bor-rpc.publicnode.com",
        "https://polygon-mainnet.public.blastapi.io",
    ]

    def __init__(self, config: dict):
        self.config = config
        primary_rpc = os.getenv("POLYGON_RPC_URL")
        fallback_env = os.getenv("POLYGON_RPC_FALLBACKS", "")
        if fallback_env.strip():
            fallback_urls = [u.strip() for u in fallback_env.split(",") if u.strip()]
        else:
            fallback_urls = list(self.RPC_FALLBACKS)

        self.rpc_urls = [primary_rpc] if primary_rpc else []
        for url in fallback_urls:
            if url and url not in self.rpc_urls:
                self.rpc_urls.append(url)
        if not self.rpc_urls:
            self.rpc_urls = ["https://polygon.llamarpc.com"]
        
        self.current_rpc_index = 0
        self.w3 = Web3(Web3.HTTPProvider(self.rpc_urls[0]))
        
        self.account = None
        self.address = None
        # Best-effort eager load for valid keys; invalid values are ignored until first use.
        pk = os.getenv("POLY_WALLET_PRIVATE_KEY")
        if pk:
            try:
                self.account = Account.from_key(pk)
                self.address = self.account.address
            except Exception:
                self.account = None
                self.address = None
        self.journal_path = config.get("state", {}).get(
            "journal_path",
            "data/audit/polymarket_trade_journal.jsonl",
        )

        self.mode = config.get("mode", "SHADOW").upper()
        self._nonce_lock = asyncio.Lock()
        self._pending_nonce: Optional[int] = None
        self._init_contracts()

    def _init_contracts(self):
        """Initialize contract objects with current w3 provider."""
        self.ctf_contract = self.w3.eth.contract(address=self.CTF_EXCHANGE, abi=CTF_EXCHANGE_ABI)
        self.usdc_contract = self.w3.eth.contract(address=self.USDC_E, abi=ERC20_ABI)

    def _rotate_rpc(self):
        """Rotate to the next available RPC provider on failure."""
        self.current_rpc_index = (self.current_rpc_index + 1) % len(self.rpc_urls)
        new_rpc = self.rpc_urls[self.current_rpc_index]
        logger.warning(f"RPC failure detected. Rotating to fallback provider: {new_rpc}")
        self.w3 = Web3(Web3.HTTPProvider(new_rpc))
        self._init_contracts()

    async def _call_with_rpc_retry(self, func, *args, **kwargs):
        """Execute a web3 call with automatic RPC rotation on failure."""
        max_retries = len(self.rpc_urls)
        for attempt in range(max_retries):
            try:
                if asyncio.iscoroutinefunction(func):
                    return await func(*args, **kwargs)
                else:
                    return await asyncio.to_thread(func, *args, **kwargs)
            except Exception as e:
                if "401" in str(e) or "429" in str(e) or "Too Many Requests" in str(e) or "Unauthorized" in str(e):
                    if attempt < max_retries - 1:
                        self._rotate_rpc()
                        continue
                logger.error(f"CTF Executor RPC call failed after {attempt+1} attempts: {e}")
                raise e

    def _is_shadow_mode(self) -> bool:
        return self.mode == "SHADOW"

    def _ensure_account(self) -> bool:
        """Lazily load signing account on first live execution call."""
        if self.account is not None:
            self.address = self.address or getattr(self.account, "address", None)
            if self.address is not None:
                return True
        pk = os.getenv("POLY_WALLET_PRIVATE_KEY")
        if not pk:
            return False
        try:
            self.account = Account.from_key(pk)
            self.address = self.account.address
            return True
        except Exception as e:
            logger.error(f"Failed to load POLY_WALLET_PRIVATE_KEY: {e}")
            self.account = None
            self.address = None
            return False

    async def _get_next_nonce(self) -> int:
        """Allocate a monotonic nonce cursor for this signer to avoid local collisions."""
        if not self._ensure_account() or not self.address:
            raise RuntimeError("Signer not configured")

        async with self._nonce_lock:
            chain_nonce = int(
                await self._call_with_rpc_retry(self.w3.eth.get_transaction_count, self.address)
            )
            if self._pending_nonce is None or chain_nonce > self._pending_nonce:
                self._pending_nonce = chain_nonce
            nonce = self._pending_nonce
            self._pending_nonce += 1
            return int(nonce)

    @staticmethod
    def _receipt_value(receipt: Any, key: str, default: Any = None) -> Any:
        if isinstance(receipt, dict):
            return receipt.get(key, default)
        return getattr(receipt, key, default)

    def _write_gas_telemetry(self, tx_hash: str, receipt: Any) -> None:
        """Best-effort gas telemetry for merge executions."""
        try:
            gas_used_raw = self._receipt_value(receipt, "gasUsed")
            gas_price_raw = self._receipt_value(receipt, "effectiveGasPrice")
            if gas_used_raw is None or gas_price_raw is None:
                logger.warning("mergePositions gas telemetry unavailable for tx=%s", tx_hash)
                return

            gas_used = int(gas_used_raw)
            gas_price_wei = int(gas_price_raw)
            entry = {
                "entry_type": "gas_used",
                "tx_hash": tx_hash,
                "gas_used": gas_used,
                "gas_price_gwei": float(gas_price_wei) / 1_000_000_000.0,
                "gas_cost_matic": float(gas_used * gas_price_wei) / 1_000_000_000_000_000_000.0,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
            os.makedirs(os.path.dirname(self.journal_path), exist_ok=True)
            with open(self.journal_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.warning("Failed to write merge gas telemetry for tx=%s: %s", tx_hash, e)

    async def split_position(
        self, 
        condition_id: str, 
        amount_usdc: float,
        parent_collection_id: str = "0x" + "0" * 64
    ) -> Dict[str, Any]:
        """
        Atomic mint: Split USDC.e into YES/NO shares for a specific condition.
        amount_usdc: Amount of USDC.e to split (6 decimals).
        """
        if self._is_shadow_mode():
            logger.info(f"[SHADOW] splitPosition: {amount_usdc} USDC.e for condition {condition_id}")
            return {"status": "success", "tx_hash": "shadow_tx", "shadow": True}

        if not self._ensure_account():
            return {"status": "error", "error": "Private key not configured"}

        amount_raw = int(amount_usdc * 1_000_000)
        
        # Partition for Binary markets: [1, 2] where 1=YES, 2=NO
        partition = [1, 2]

        try:
            nonce = await self._get_next_nonce()
            gas_price = await self._call_with_rpc_retry(lambda: self.w3.eth.gas_price)
            
            # Prepare transaction
            tx = await self._call_with_rpc_retry(
                self.ctf_contract.functions.splitPosition(
                    self.USDC_E,
                    parent_collection_id,
                    condition_id,
                    partition,
                    amount_raw
                ).build_transaction,
                {
                    'from': self.address,
                    'nonce': nonce,
                    'gas': 300000,
                    'gasPrice': gas_price
                }
            )

            # Sign and send
            signed_tx = self.account.sign_transaction(tx)
            tx_hash = await self._call_with_rpc_retry(self.w3.eth.send_raw_transaction, signed_tx.rawTransaction)
            
            logger.info(f"splitPosition sent: {tx_hash.hex()}")
            
            # Wait for receipt
            receipt = await self._call_with_rpc_retry(self.w3.eth.wait_for_transaction_receipt, tx_hash, timeout=60)

            if int(self._receipt_value(receipt, "status", 0) or 0) == 1:
                return {"status": "success", "tx_hash": tx_hash.hex(), "receipt": receipt}
            else:
                return {"status": "error", "error": "Transaction reverted", "receipt": receipt}

        except Exception as e:
            logger.error(f"splitPosition failed: {e}")
            return {"status": "error", "error": str(e)}

    async def merge_positions(
        self,
        condition_id: str,
        amount_shares: float,
        parent_collection_id: str = "0x" + "0" * 64
    ) -> Dict[str, Any]:
        """
        Atomic merge: Combine YES + NO shares back into USDC.e.
        amount_shares: Number of shares to merge (6 decimals usually).
        """
        if self._is_shadow_mode():
            logger.info(f"[SHADOW] mergePositions: {amount_shares} shares for condition {condition_id}")
            return {"status": "success", "tx_hash": "shadow_tx", "shadow": True}

        if not self._ensure_account():
            return {"status": "error", "error": "Private key not configured"}

        amount_raw = int(amount_shares * 1_000_000)
        partition = [1, 2]

        try:
            nonce = await self._get_next_nonce()
            gas_price = await self._call_with_rpc_retry(lambda: self.w3.eth.gas_price)
            
            tx = await self._call_with_rpc_retry(
                self.ctf_contract.functions.mergePositions(
                    self.USDC_E,
                    parent_collection_id,
                    condition_id,
                    partition,
                    amount_raw
                ).build_transaction,
                {
                    'from': self.address,
                    'nonce': nonce,
                    'gas': 300000,
                    'gasPrice': gas_price
                }
            )

            signed_tx = self.account.sign_transaction(tx)
            tx_hash = await self._call_with_rpc_retry(self.w3.eth.send_raw_transaction, signed_tx.rawTransaction)
            
            logger.info(f"mergePositions sent: {tx_hash.hex()}")
            
            receipt = await self._call_with_rpc_retry(self.w3.eth.wait_for_transaction_receipt, tx_hash, timeout=60)

            tx_hash_hex = tx_hash.hex()
            if int(self._receipt_value(receipt, "status", 0) or 0) == 1:
                self._write_gas_telemetry(tx_hash_hex, receipt)
                return {"status": "success", "tx_hash": tx_hash_hex, "receipt": receipt}
            else:
                return {"status": "error", "error": "Transaction reverted", "receipt": receipt}

        except Exception as e:
            logger.error(f"mergePositions failed: {e}")
            return {"status": "error", "error": str(e)}

    async def get_position_id(
        self,
        condition_id: str,
        index_set: int,
        parent_collection_id: str = "0x" + "0" * 64
    ) -> int:
        """Calculate the ERC1155 token ID for a specific conditional outcome."""
        return await self._call_with_rpc_retry(
            self.ctf_contract.functions.getPositionId(
                self.USDC_E,
                parent_collection_id,
                condition_id,
                [index_set]
            ).call
        )

    async def get_conditional_balance(
        self,
        condition_id: str,
        index_set: int,
        parent_collection_id: str = "0x" + "0" * 64
    ) -> float:
        """Read settled on-chain balance for a specific outcome share."""
        if not self.address:
            return 0.0
            
        try:
            pos_id = await self.get_position_id(condition_id, index_set, parent_collection_id)
            raw_bal = await self._call_with_rpc_retry(
                self.ctf_contract.functions.balanceOf(
                    self.address,
                    pos_id
                ).call
            )
            return float(raw_bal) / 1_000_000
        except Exception as e:
            logger.error(f"Failed to fetch conditional balance: {e}")
            return 0.0

    async def get_conditional_balance_checked(
        self,
        condition_id: str,
        index_set: int,
        parent_collection_id: str = "0x" + "0" * 64,
    ) -> Dict[str, Any]:
        """Typed conditional-balance fetch to avoid silent error masking."""
        if not self.address:
            return {
                "status": "UNKNOWN",
                "balance": 0.0,
                "error": "wallet address unavailable",
            }

        try:
            pos_id = await self.get_position_id(condition_id, index_set, parent_collection_id)
            raw_bal = await self._call_with_rpc_retry(
                self.ctf_contract.functions.balanceOf(self.address, pos_id).call
            )
            return {
                "status": "OK",
                "balance": float(raw_bal) / 1_000_000,
                "error": None,
            }
        except Exception as e:
            logger.error(f"Failed to fetch conditional balance (checked): {e}")
            return {"status": "UNKNOWN", "balance": 0.0, "error": str(e)}
