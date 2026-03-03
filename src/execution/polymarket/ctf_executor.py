"""
DACLE Polymarket CTF Executor
Direct contract interaction for CTF Exchange on Polygon.
Handles: splitPosition (minting YES/NO from USDC.e) and mergePositions (merging YES/NO to USDC.e).
"""

import logging
import os
import time
import asyncio
from typing import Any, Dict, Optional, List
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
    
    def __init__(self, config: dict):
        self.config = config
        rpc_url = os.getenv("POLYGON_RPC_URL") or "https://polygon-rpc.com"
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        
        pk = os.getenv("POLY_WALLET_PRIVATE_KEY")
        if pk:
            self.account = Account.from_key(pk)
            self.address = self.account.address
        else:
            self.account = None
            self.address = None

        self.mode = config.get("mode", "SHADOW").upper()
        self.ctf_contract = self.w3.eth.contract(address=self.CTF_EXCHANGE, abi=CTF_EXCHANGE_ABI)
        self.usdc_contract = self.w3.eth.contract(address=self.USDC_E, abi=ERC20_ABI)

    def _is_shadow_mode(self) -> bool:
        return self.mode == "SHADOW"

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

        if not self.account:
            return {"status": "error", "error": "Private key not configured"}

        amount_raw = int(amount_usdc * 1_000_000)
        
        # Partition for Binary markets: [1, 2] where 1=YES, 2=NO
        partition = [1, 2]

        try:
            nonce = await asyncio.to_thread(self.w3.eth.get_transaction_count, self.address)
            
            # Prepare transaction
            tx = await asyncio.to_thread(
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
                    'gasPrice': await asyncio.to_thread(self.w3.eth.gas_price)
                }
            )

            # Sign and send
            signed_tx = self.account.sign_transaction(tx)
            tx_hash = await asyncio.to_thread(self.w3.eth.send_raw_transaction, signed_tx.rawTransaction)
            
            logger.info(f"splitPosition sent: {tx_hash.hex()}")
            
            # Wait for receipt
            receipt = await asyncio.to_thread(self.w3.eth.wait_for_transaction_receipt, tx_hash, timeout=60)
            
            if receipt.status == 1:
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

        if not self.account:
            return {"status": "error", "error": "Private key not configured"}

        amount_raw = int(amount_shares * 1_000_000)
        partition = [1, 2]

        try:
            nonce = await asyncio.to_thread(self.w3.eth.get_transaction_count, self.address)
            
            tx = await asyncio.to_thread(
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
                    'gasPrice': await asyncio.to_thread(self.w3.eth.gas_price)
                }
            )

            signed_tx = self.account.sign_transaction(tx)
            tx_hash = await asyncio.to_thread(self.w3.eth.send_raw_transaction, signed_tx.rawTransaction)
            
            logger.info(f"mergePositions sent: {tx_hash.hex()}")
            
            receipt = await asyncio.to_thread(self.w3.eth.wait_for_transaction_receipt, tx_hash, timeout=60)
            
            if receipt.status == 1:
                return {"status": "success", "tx_hash": tx_hash.hex(), "receipt": receipt}
            else:
                return {"status": "error", "error": "Transaction reverted", "receipt": receipt}

        except Exception as e:
            logger.error(f"mergePositions failed: {e}")
            return {"status": "error", "error": str(e)}
