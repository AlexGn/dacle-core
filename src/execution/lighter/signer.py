"""
DACLE Lighter Signer
Handles EIP-712 signing for Lighter.xyz transactions.
Requires a private key.
"""

import logging
import re
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

CHAIN_ID_MAINNET = 304
CHAIN_ID_TESTNET = 300
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
_ETH_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


def _load_account_from_key(private_key: str) -> Any:
    """Load eth_account lazily so imports do not fail at module import time."""
    from eth_account import Account

    return Account.from_key(private_key)


def _is_eth_address(value: str) -> bool:
    return bool(_ETH_ADDRESS_RE.match(value or ""))


class LighterSigner:
    def __init__(
        self,
        private_key: str,
        chain_id: int = CHAIN_ID_MAINNET,
        verifying_contract: Optional[str] = None,
        network: str = "mainnet",
        account_loader: Callable[[str], Any] = _load_account_from_key,
    ):
        if chain_id not in (CHAIN_ID_MAINNET, CHAIN_ID_TESTNET):
            raise ValueError(
                f"Unsupported chain_id={chain_id}. Expected {CHAIN_ID_MAINNET} (mainnet) or {CHAIN_ID_TESTNET} (testnet)."
            )

        normalized_network = (network or "").strip().lower()
        expected_chain_id = {"mainnet": CHAIN_ID_MAINNET, "testnet": CHAIN_ID_TESTNET}.get(normalized_network)
        if expected_chain_id is not None and chain_id != expected_chain_id:
            raise ValueError(
                f"chain_id={chain_id} does not match network='{normalized_network}' (expected {expected_chain_id})."
            )

        if not verifying_contract:
            raise ValueError("verifying_contract is required for EIP-712 domain signing.")
        if not _is_eth_address(verifying_contract):
            raise ValueError(f"Invalid verifying_contract: {verifying_contract}")

        checksum_contract = verifying_contract.lower()
        if checksum_contract.lower() == ZERO_ADDRESS:
            raise ValueError("verifying_contract cannot be the zero address.")

        self.account = account_loader(private_key)
        self.chain_id = chain_id
        self.verifying_contract = checksum_contract
        self.network = normalized_network or "mainnet"
        self.address = self.account.address
        self.domain: Dict[str, Any] = {
            "name": "Lighter",
            "version": "1",
            "chainId": self.chain_id,
            "verifyingContract": self.verifying_contract,
        }
        logger.info(f"LighterSigner initialized for address: {self.address}")

    def sign_order(self, order_data: dict) -> str:
        """
        Signs an order using EIP-712.
        Domain is strict-validated at init for fail-fast safety.

        If order_data contains a ``deadline`` key, the Order EIP-712 type is
        extended with an optional ``deadline`` field (uint256).  This keeps
        backward compatibility: orders without deadline use the 5-field type.
        """
        from eth_account.messages import encode_typed_data

        order_fields = [
            {"name": "marketId", "type": "uint32"},
            {"name": "side", "type": "uint8"},
            {"name": "price", "type": "uint256"},
            {"name": "size", "type": "uint256"},
            {"name": "nonce", "type": "uint32"},
        ]

        # 5.10: Extend type with deadline when present in order_data.
        if "deadline" in order_data:
            order_fields.append({"name": "deadline", "type": "uint256"})

        types = {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "Order": order_fields,
        }

        structured_data = {
            "types": types,
            "domain": self.domain,
            "primaryType": "Order",
            "message": order_data,
        }

        signed_msg = self.account.sign_message(encode_typed_data(full_message=structured_data))
        return signed_msg.signature.hex()
