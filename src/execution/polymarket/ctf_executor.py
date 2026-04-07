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
from web3.middleware import ExtraDataToPOAMiddleware
from eth_account import Account
from decimal import Decimal
from src.execution.polymarket.nonce_registry import NonceRegistry
from src.polymarket.credentials import resolve_private_key

logger = logging.getLogger(__name__)

# CTF Exchange ABI (minimal for split/merge + order book operations)
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
    },
    # Order book functions for direct chain submission (EIP-712 signed orders)
    {
        "inputs": [
            {"internalType": "bytes32", "name": "orderHash", "type": "bytes32"}
        ],
        "name": "cancelOrder",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "bytes32[]", "name": "orderHashes", "type": "bytes32[]"}
        ],
        "name": "cancelOrders",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "uint256", "name": "salt", "type": "uint256"},
                    {"internalType": "address", "name": "maker", "type": "address"},
                    {"internalType": "address", "name": "signer", "type": "address"},
                    {"internalType": "address", "name": "taker", "type": "address"},
                    {"internalType": "uint256", "name": "tokenId", "type": "uint256"},
                    {"internalType": "uint256", "name": "makerAmount", "type": "uint256"},
                    {"internalType": "uint256", "name": "takerAmount", "type": "uint256"},
                    {"internalType": "uint256", "name": "expiration", "type": "uint256"},
                    {"internalType": "uint256", "name": "nonce", "type": "uint256"},
                    {"internalType": "uint256", "name": "feeRateBps", "type": "uint256"},
                    {"internalType": "uint8", "name": "side", "type": "uint8"},
                    {"internalType": "uint8", "name": "signatureType", "type": "uint8"},
                    {"internalType": "bytes", "name": "signature", "type": "bytes"}
                ],
                "internalType": "struct Order",
                "name": "order",
                "type": "tuple"
            },
            {"internalType": "bool", "name": "invert", "type": "bool"}
        ],
        "name": "fillOrder",
        "outputs": [
            {"internalType": "uint256", "name": "", "type": "uint256"}
        ],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "uint256", "name": "salt", "type": "uint256"},
                    {"internalType": "address", "name": "maker", "type": "address"},
                    {"internalType": "address", "name": "signer", "type": "address"},
                    {"internalType": "address", "name": "taker", "type": "address"},
                    {"internalType": "uint256", "name": "tokenId", "type": "uint256"},
                    {"internalType": "uint256", "name": "makerAmount", "type": "uint256"},
                    {"internalType": "uint256", "name": "takerAmount", "type": "uint256"},
                    {"internalType": "uint256", "name": "expiration", "type": "uint256"},
                    {"internalType": "uint256", "name": "nonce", "type": "uint256"},
                    {"internalType": "uint256", "name": "feeRateBps", "type": "uint256"},
                    {"internalType": "uint8", "name": "side", "type": "uint8"},
                    {"internalType": "uint8", "name": "signatureType", "type": "uint8"},
                    {"internalType": "bytes", "name": "signature", "type": "bytes"}
                ],
                "internalType": "struct Order[]",
                "name": "orders",
                "type": "tuple[]"
            },
            {"internalType": "bytes32", "name": "matchers", "type": "bytes32"}
        ],
        "name": "matchOrders",
        "outputs": [],
        "stateMutability": "nonpayable",
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

    DISALLOWED_RPC_MARKERS = (
        "rpc.ankr.com",
        "polygon-rpc.com",
        "polygon.llamarpc.com",
        "1rpc.io/polygon",
        "public.blastapi.io",
    )
    
    # Stable public RPC fallbacks (unauthenticated endpoints).
    RPC_FALLBACKS = [
        "https://polygon-bor-rpc.publicnode.com",
    ]

    # Polygon Private Mempool RPC (MEV protection, launched April 2026)
    # Submits transactions directly to validator set, bypassing public mempool
    POLYGON_PRIVATE_MEMPOOL = "https://polygon-priv-mainnet.g.alchemy.com/v2/demo"
    PRIVATE_MEMPOOL_ENABLED = False

    @classmethod
    def _sanitize_rpc_urls(cls, urls: List[str]) -> List[str]:
        sanitized: List[str] = []
        for url in urls:
            candidate = (url or "").strip()
            if not candidate:
                continue
            candidate_lc = candidate.lower()
            if any(marker in candidate_lc for marker in cls.DISALLOWED_RPC_MARKERS):
                logger.warning("Skipping disallowed Polygon RPC provider: %s", candidate)
                continue
            if candidate not in sanitized:
                sanitized.append(candidate)
        return sanitized

    def __init__(self, config: dict):
        self.config = config
        primary_rpc = os.getenv("POLYGON_RPC_URL")
        fallback_env = os.getenv("POLYGON_RPC_FALLBACKS", "")
        if fallback_env.strip():
            fallback_urls = [u.strip() for u in fallback_env.split(",") if u.strip()]
        else:
            fallback_urls = list(self.RPC_FALLBACKS)

        self.rpc_urls = self._sanitize_rpc_urls([primary_rpc] if primary_rpc else [])
        for url in self._sanitize_rpc_urls(fallback_urls):
            if url and url not in self.rpc_urls:
                self.rpc_urls.append(url)
        if not self.rpc_urls:
            self.rpc_urls = list(self.RPC_FALLBACKS)
        
        self.current_rpc_index = 0
        self._last_rpc_failure_class = None
        self._last_rpc_failure_message = None
        self._rpc_degraded = False
        self.w3 = Web3(Web3.HTTPProvider(self.rpc_urls[0]))
        self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        
        self.account = None
        self.address = None
        # Best-effort eager load for valid keys; invalid values are ignored until first use.
        resolved_key = resolve_private_key(os.getenv("POLY_WALLET_PRIVATE_KEY"))
        pk = resolved_key.value
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
        self.w3 = Web3(Web3.HTTPProvider(new_rpc, request_kwargs={"timeout": 3.0}))
        self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        self._init_contracts()

    async def _call_with_rpc_retry(self, func, *args, **kwargs):
        """Execute a web3 call with automatic RPC rotation on failure."""
        max_retries = len(self.rpc_urls)
        last_failure_class = None
        for attempt in range(max_retries):
            try:
                if asyncio.iscoroutinefunction(func):
                    result = await func(*args, **kwargs)
                else:
                    result = await asyncio.to_thread(func, *args, **kwargs)
                self._rpc_degraded = False
                self._last_rpc_failure_class = None
                self._last_rpc_failure_message = None
                return result
            except Exception as e:
                msg = str(e)
                failure_class = self._classify_rpc_error(msg)
                self._last_rpc_failure_class = failure_class
                self._last_rpc_failure_message = msg
                last_failure_class = failure_class
                if attempt < max_retries - 1 and self._should_rotate_rpc_on_error(msg):
                    self._rotate_rpc()
                    continue
                self._rpc_degraded = failure_class == "missing_block"
                if failure_class == "missing_block":
                    logger.warning(
                        "CTF Executor RPC missing-block drift after %d attempts: %s",
                        attempt + 1,
                        e,
                    )
                else:
                    logger.error(f"CTF Executor RPC call failed after {attempt+1} attempts: {e}")
                raise e
        self._rpc_degraded = last_failure_class == "missing_block"

    @staticmethod
    def _should_rotate_rpc_on_error(message: str) -> bool:
        """Return True when failure likely comes from transport/provider instability."""
        msg = (message or "").lower()

        # Deterministic execution errors should not trigger provider rotation.
        non_recoverable_markers = (
            "execution reverted",
            "insufficient funds",
            "nonce too low",
            "replacement transaction underpriced",
            "already known",
            "invalid signature",
            "invalid sender",
        )
        if any(marker in msg for marker in non_recoverable_markers):
            return False

        recoverable_markers = (
            "block with id",
            "header not found",
            "block not found",
            "401",
            "429",
            "unauthorized",
            "too many requests",
            "ssl",
            "eof occurred in violation of protocol",
            "httpsconnectionpool",
            "max retries exceeded",
            "timed out",
            "connection aborted",
            "connection reset",
            "temporary failure",
            "service unavailable",
            "bad gateway",
            "gateway timeout",
            "502",
            "503",
            "504",
        )
        return any(marker in msg for marker in recoverable_markers)

    @staticmethod
    def _classify_rpc_error(message: str) -> str:
        msg = (message or "").lower()
        if any(marker in msg for marker in ("block with id", "header not found", "block not found")):
            return "missing_block"
        if any(
            marker in msg
            for marker in (
                "401",
                "429",
                "unauthorized",
                "too many requests",
                "ssl",
                "timed out",
                "connection aborted",
                "connection reset",
                "temporary failure",
                "service unavailable",
                "bad gateway",
                "gateway timeout",
                "502",
                "503",
                "504",
            )
        ):
            return "transport"
        if any(
            marker in msg
            for marker in (
                "execution reverted",
                "insufficient funds",
                "nonce too low",
                "replacement transaction underpriced",
                "already known",
                "invalid signature",
                "invalid sender",
            )
        ):
            return "deterministic"
        return "unknown"

    @staticmethod
    def _normalize_checked_error_message(message: str) -> str:
        msg = (message or "").strip()
        if not msg:
            return "unknown error"
        if "execution reverted" in msg.lower():
            return "execution reverted"
        return msg

    def _is_shadow_mode(self) -> bool:
        return self.mode == "SHADOW"

    def _ensure_account(self) -> bool:
        """Lazily load signing account on first live execution call."""
        if self.account is not None:
            self.address = self.address or getattr(self.account, "address", None)
            if self.address is not None:
                return True
        resolved_key = resolve_private_key(os.getenv("POLY_WALLET_PRIVATE_KEY"))
        pk = resolved_key.value
        if not pk:
            if resolved_key.error:
                logger.error("Failed to resolve POLY_WALLET_PRIVATE_KEY: %s", resolved_key.error)
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
        """Allocate a monotonic nonce cursor for this signer via process-wide registry."""
        if not self._ensure_account() or not self.address:
            raise RuntimeError("Signer not configured")

        key = f"polygon:{self.address.lower()}"

        async def _fetch_chain_nonce() -> int:
            return int(await self._call_with_rpc_retry(lambda: self.w3.eth.get_transaction_count(self.address)))

        return await NonceRegistry.next_nonce(key, _fetch_chain_nonce)

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
                lambda: self.ctf_contract.functions.splitPosition(
                    self.USDC_E,
                    parent_collection_id,
                    condition_id,
                    partition,
                    amount_raw
                ).build_transaction(
                    {
                        'from': self.address,
                        'nonce': nonce,
                        'gas': 300000,
                        'gasPrice': gas_price
                    }
                )
            )

            # Sign and send
            signed_tx = self.account.sign_transaction(tx)
            tx_hash = await self._call_with_rpc_retry(lambda: self.w3.eth.send_raw_transaction(signed_tx.rawTransaction))
            
            logger.info(f"splitPosition sent: {tx_hash.hex()}")
            
            # Wait for receipt
            receipt = await self._call_with_rpc_retry(lambda: self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60))

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
                lambda: self.ctf_contract.functions.mergePositions(
                    self.USDC_E,
                    parent_collection_id,
                    condition_id,
                    partition,
                    amount_raw
                ).build_transaction(
                    {
                        'from': self.address,
                        'nonce': nonce,
                        'gas': 300000,
                        'gasPrice': gas_price
                    }
                )
            )

            signed_tx = self.account.sign_transaction(tx)
            tx_hash = await self._call_with_rpc_retry(lambda: self.w3.eth.send_raw_transaction(signed_tx.rawTransaction))
            
            logger.info(f"mergePositions sent: {tx_hash.hex()}")
            
            receipt = await self._call_with_rpc_retry(lambda: self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60))

            tx_hash_hex = tx_hash.hex()
            if int(self._receipt_value(receipt, "status", 0) or 0) == 1:
                self._write_gas_telemetry(tx_hash_hex, receipt)
                return {"status": "success", "tx_hash": tx_hash_hex, "receipt": receipt}
            else:
                return {"status": "error", "error": "Transaction reverted", "receipt": receipt}

        except Exception as e:
            logger.error(f"mergePositions failed: {e}")
            return {"status": "error", "error": str(e)}

    async def get_matic_balance(self) -> Optional[float]:
        """Fetch native MATIC balance of the signer EOA."""
        if not self._ensure_account() or not self.address:
            return None
        try:
            wei = await self._call_with_rpc_retry(lambda: self.w3.eth.get_balance(self.address))
            return float(self.w3.from_wei(wei, "ether"))
        except Exception as e:
            logger.warning(f"Failed to fetch MATIC balance: {e}")
            return None

    async def get_position_id(
        self,
        condition_id: str,
        index_set: int,
        parent_collection_id: str = "0x" + "0" * 64
    ) -> int:
        """Calculate the ERC1155 token ID for a specific conditional outcome."""
        return await self._call_with_rpc_retry(
            lambda: self.ctf_contract.functions.getPositionId(
                self.USDC_E,
                parent_collection_id,
                condition_id,
                [index_set]
            ).call()
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
                lambda: self.ctf_contract.functions.balanceOf(
                    self.address,
                    pos_id
                ).call()
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
                lambda: self.ctf_contract.functions.balanceOf(self.address, pos_id).call()
            )
            return {
                "status": "OK",
                "balance": float(raw_bal) / 1_000_000,
                "error": None,
            }
        except Exception as e:
            logger.error(f"Failed to fetch conditional balance (checked): {e}")
            return {
                "status": "UNKNOWN",
                "balance": 0.0,
                "error": self._normalize_checked_error_message(str(e)),
            }

    def enable_private_mempool(self, api_key: Optional[str] = None):
        """
        Enable Polygon Private Mempool for MEV protection.

        Private mempool submits transactions directly to validator set,
        bypassing the public mempool where MEV bots can front-run.

        Requires: POLYGON_PRIVATE_MEMPOOL_API_KEY env var or api_key param

        Args:
            api_key: Optional Alchemy/Infura API key for private mempool access
        """
        if api_key:
            self.PRIVATE_MEMPOOL_ENABLED = True
            self.POLYGON_PRIVATE_MEMPOOL = f"https://polygon-priv-mainnet.g.alchemy.com/v2/{api_key}"
            logger.info(f"Private mempool enabled with API key")
        elif os.getenv("POLYGON_PRIVATE_MEMPOOL_API_KEY"):
            api_key = os.getenv("POLYGON_PRIVATE_MEMPOOL_API_KEY")
            self.PRIVATE_MEMPOOL_ENABLED = True
            self.POLYGON_PRIVATE_MEMPOOL = f"https://polygon-priv-mainnet.g.alchemy.com/v2/{api_key}"
            logger.info(f"Private mempool enabled from env var")
        else:
            logger.warning("Private mempool requested but no API key provided")

    def _use_private_mempool(self) -> bool:
        """Check if private mempool should be used for next transaction."""
        return self.PRIVATE_MEMPOOL_ENABLED and self._rpc_degraded

    def _get_w3_for_submission(self) -> Web3:
        """Get Web3 instance for transaction submission."""
        if self._use_private_mempool():
            logger.info("Using private mempool for MEV-protected submission")
            return Web3(Web3.HTTPProvider(self.POLYGON_PRIVATE_MEMPOOL))
        return self.w3

    # EIP-712 domain and order types for Polymarket CTF Exchange
    ORDER_TYPES = {
        "Order": [
            {"name": "salt", "type": "uint256"},
            {"name": "maker", "type": "address"},
            {"name": "signer", "type": "address"},
            {"name": "taker", "type": "address"},
            {"name": "tokenId", "type": "uint256"},
            {"name": "makerAmount", "type": "uint256"},
            {"name": "takerAmount", "type": "uint256"},
            {"name": "expiration", "type": "uint256"},
            {"name": "nonce", "type": "uint256"},
            {"name": "feeRateBps", "type": "uint256"},
            {"name": "side", "type": "uint8"},
            {"name": "signatureType", "type": "uint8"},
        ]
    }

    CTF_EXCHANGE_DOMAIN = {
        "name": "Polymarket CTF Exchange",
        "version": "1",
        "chainId": 137,  # Polygon mainnet
        "verifyingContract": CTF_EXCHANGE,
    }

    def create_eip712_order(
        self,
        salt: int,
        maker: str,
        signer: str,
        taker: str,
        token_id: int,
        maker_amount: int,
        taker_amount: int,
        expiration: int,
        nonce: int,
        fee_rate_bps: int,
        side: int,  # 0=BUY, 1=SELL
        signature_type: int = 0,  # 0=EOA, 2=POLY_PROXY
    ) -> Dict[str, Any]:
        """
        Create an EIP-712 signed order for Polymarket CTF Exchange.

        Args:
            salt: Random salt for order uniqueness
            maker: Maker address (wallet address)
            signer: Signer address (may differ for proxy signing)
            taker: Taker address (0x0 for open orders)
            token_id: ERC1155 token ID for the outcome share
            maker_amount: Amount maker is selling (in USDC.e * 1e6)
            taker_amount: Amount taker is buying (in USDC.e * 1e6)
            expiration: Unix timestamp for order expiry
            nonce: Order nonce (prevents replay)
            fee_rate_bps: Fee rate in basis points (e.g., 315 = 3.15%)
            side: 0 for BUY, 1 for SELL
            signature_type: 0 for EOA, 2 for Poly Proxy

        Returns:
            Order dict ready for signing
        """
        return {
            "salt": salt,
            "maker": maker,
            "signer": signer,
            "taker": taker,
            "tokenId": token_id,
            "makerAmount": maker_amount,
            "takerAmount": taker_amount,
            "expiration": expiration,
            "nonce": nonce,
            "feeRateBps": fee_rate_bps,
            "side": side,
            "signatureType": signature_type,
        }

    def sign_order_eip712(self, order: Dict[str, Any]) -> str:
        """
        Sign an order using EIP-712 typed data.

        Args:
            order: Order dict from create_eip712_order()

        Returns:
            Hex-encoded signature
        """
        if not self.account:
            raise RuntimeError("Signing account not configured")

        # Convert camelCase keys to snake_case for eth_account
        order_data = {
            "salt": order["salt"],
            "maker": order["maker"],
            "signer": order["signer"],
            "taker": order["taker"],
            "tokenId": order["tokenId"],
            "makerAmount": order["makerAmount"],
            "takerAmount": order["takerAmount"],
            "expiration": order["expiration"],
            "nonce": order["nonce"],
            "feeRateBps": order["feeRateBps"],
            "side": order["side"],
            "signatureType": order["signatureType"],
        }

        # Sign using eth_account's sign_typed_data
        signature = self.account.sign_typed_data(
            domain=self.CTF_EXCHANGE_DOMAIN,
            message_types=self.ORDER_TYPES,
            message=order_data,
        )

        return signature.signature.hex()

    async def submit_order_to_chain(
        self,
        order: Dict[str, Any],
        signature: str,
        is_ioc: bool = True,
        price_limit: Optional[float] = None,
        current_price: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Submit a signed order directly to the CTF Exchange contract.

        Args:
            order: Signed order dict
            signature: Hex-encoded EIP-712 signature
            is_ioc: If True, use Immediate-Or-Cancel (no resting order)
            price_limit: The maximum acceptable price (for BUY) or minimum (for SELL)
            current_price: The observed price at the time of the signal
        """
        if price_limit is not None and current_price is not None:
            side = order.get("side") # 0=BUY, 1=SELL
            if side == 0: # BUY
                if current_price > price_limit:
                    logger.warning(f"Slippage Veto: current_price {current_price} > limit {price_limit}")
                    return {"status": "error", "error": f"Slippage limit exceeded: {current_price} > {price_limit}"}
            elif side == 1: # SELL
                if current_price < price_limit:
                    logger.warning(f"Slippage Veto: current_price {current_price} < limit {price_limit}")
                    return {"status": "error", "error": f"Slippage limit exceeded: {current_price} < {price_limit}"}

        if self._is_shadow_mode():
            logger.info(f"[SHADOW] submit_order: side={order['side']}, makerAmount={order['makerAmount']}")
            return {"status": "success", "tx_hash": "shadow_tx", "shadow": True}

        if not self._ensure_account():
            return {"status": "error", "error": "Private key not configured"}

        try:
            # Prepare order tuple for contract call
            # Note: signature is appended to order struct
            order_tuple = (
                order["salt"],
                order["maker"],
                order["signer"],
                order["taker"],
                order["tokenId"],
                order["makerAmount"],
                order["takerAmount"],
                order["expiration"],
                order["nonce"],
                order["feeRateBps"],
                order["side"],
                order["signatureType"],
                Web3.to_bytes(hexstr=signature),
            )

            # Get Web3 instance (may use private mempool)
            w3_submit = self._get_w3_for_submission()
            contract = w3_submit.eth.contract(address=self.CTF_EXCHANGE, abi=CTF_EXCHANGE_ABI)

            # Prepare fillOrder call
            # invert=False means we're the taker accepting the maker's price
            invert = False

            # Estimate gas
            gas_estimate = await self._call_with_rpc_retry(
                lambda: contract.functions.fillOrder(order_tuple, invert).estimate_gas({
                    "from": self.address,
                })
            )

            # Get nonce
            nonce = await self._get_next_nonce()

            # Build transaction
            tx = contract.functions.fillOrder(order_tuple, invert).build_transaction({
                "from": self.address,
                "nonce": nonce,
                "gas": int(gas_estimate * 1.2),  # 20% buffer
                "gasPrice": await self._call_with_rpc_retry(lambda: w3_submit.eth.gas_price),
            })

            # Sign and send
            signed_tx = self.account.sign_transaction(tx)
            tx_hash = await self._call_with_rpc_retry(
                lambda: w3_submit.eth.send_raw_transaction(signed_tx.raw_transaction)
            )

            logger.info(f"Order submitted: tx_hash={tx_hash.hex()}")

            # Wait for receipt if not IOC
            if not is_ioc:
                receipt = await self._call_with_rpc_retry(
                    lambda: w3_submit.eth.wait_for_transaction_receipt(tx_hash)
                )
                if receipt["status"] == 1:
                    return {
                        "status": "success",
                        "tx_hash": tx_hash.hex(),
                        "gas_used": receipt["gasUsed"],
                        "block_number": receipt["blockNumber"],
                    }
                else:
                    return {
                        "status": "error",
                        "tx_hash": tx_hash.hex(),
                        "error": "Transaction reverted",
                        "receipt": receipt,
                    }

            return {
                "status": "pending",
                "tx_hash": tx_hash.hex(),
            }

        except Exception as e:
            logger.error(f"Order submission failed: {e}")
            return {"status": "error", "error": str(e)}

    async def match_orders_atomic(
        self,
        order_a: Dict[str, Any],
        signature_a: str,
        order_b: Dict[str, Any],
        signature_b: str,
    ) -> Dict[str, Any]:
        """
        Atomically match two opposing orders using matchOrders().

        This is the preferred method for cross-platform arbitrage:
        1. Detect price discrepancy between venues
        2. Create opposing orders (buy low, sell high)
        3. Submit atomically - both legs execute or neither does

        Args:
            order_a: First order (e.g., BUY)
            signature_a: Signature for order_a
            order_b: Second order (e.g., SELL, opposing side)
            signature_b: Signature for order_b

        Returns:
            Transaction result dict
        """
        if self._is_shadow_mode():
            logger.info(f"[SHADOW] match_orders: {order_a['makerAmount']} vs {order_b['makerAmount']}")
            return {"status": "success", "tx_hash": "shadow_tx", "shadow": True}

        if not self._ensure_account():
            return {"status": "error", "error": "Private key not configured"}

        try:
            # Convert orders to tuples
            order_a_tuple = (
                order_a["salt"], order_a["maker"], order_a["signer"], order_a["taker"],
                order_a["tokenId"], order_a["makerAmount"], order_a["takerAmount"],
                order_a["expiration"], order_a["nonce"], order_a["feeRateBps"],
                order_a["side"], order_a["signatureType"],
                Web3.to_bytes(hexstr=signature_a),
            )
            order_b_tuple = (
                order_b["salt"], order_b["maker"], order_b["signer"], order_b["taker"],
                order_b["tokenId"], order_b["makerAmount"], order_b["takerAmount"],
                order_b["expiration"], order_b["nonce"], order_b["feeRateBps"],
                order_b["side"], order_b["signatureType"],
                Web3.to_bytes(hexstr=signature_b),
            )

            # matchers: bytes32 indicating which orders to match
            # For 2 orders, we use 0x01 to match first pair
            matchers = "0x01" + "00" * 31

            w3_submit = self._get_w3_for_submission()
            contract = w3_submit.eth.contract(address=self.CTF_EXCHANGE, abi=CTF_EXCHANGE_ABI)

            gas_estimate = await self._call_with_rpc_retry(
                lambda: contract.functions.matchOrders(
                    [order_a_tuple, order_b_tuple],
                    matchers
                ).estimate_gas({"from": self.address})
            )

            nonce = await self._get_next_nonce()

            tx = contract.functions.matchOrders(
                [order_a_tuple, order_b_tuple],
                matchers
            ).build_transaction({
                "from": self.address,
                "nonce": nonce,
                "gas": int(gas_estimate * 1.2),
                "gasPrice": await self._call_with_rpc_retry(lambda: w3_submit.eth.gas_price),
            })

            signed_tx = self.account.sign_transaction(tx)
            tx_hash = await self._call_with_rpc_retry(
                lambda: w3_submit.eth.send_raw_transaction(signed_tx.raw_transaction)
            )

            logger.info(f"Atomic match submitted: tx_hash={tx_hash.hex()}")

            return {
                "status": "pending",
                "tx_hash": tx_hash.hex(),
            }

        except Exception as e:
            logger.error(f"Atomic match failed: {e}")
            return {"status": "error", "error": str(e)}
