"""
On-Chain Supply Fetcher

Session 282: Fallback data source for circulating supply when CoinGecko/CryptoRank fail.
Uses public RPC endpoints to query ERC-20 token contracts directly.

Supported chains:
- Ethereum (ETH)
- BNB Smart Chain (BSC)
- Polygon (MATIC)
- Arbitrum (ARB)
- Base
- Monad (when mainnet launches)

Usage:
    from dacle_core.data.fetchers.onchain_supply_fetcher import OnChainSupplyFetcher

    fetcher = OnChainSupplyFetcher()
    result = fetcher.get_circulating_supply("0x1aD7052BB331A0529c1981c3EC2bC4663498A110", "ethereum")
"""

import json
import logging
import requests
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

# ERC-20 function signatures (first 4 bytes of keccak256 hash)
ERC20_TOTAL_SUPPLY = "0x18160ddd"  # totalSupply()
ERC20_BALANCE_OF = "0x70a08231"    # balanceOf(address)
ERC20_DECIMALS = "0x313ce567"      # decimals()
ERC20_NAME = "0x06fdde03"          # name()
ERC20_SYMBOL = "0x95d89b41"        # symbol()

# Public RPC endpoints (free, no API key required)
# Ordered by reliability
RPC_ENDPOINTS = {
    "ethereum": [
        "https://eth.llamarpc.com",
        "https://rpc.ankr.com/eth",
        "https://ethereum.publicnode.com",
        "https://1rpc.io/eth",
    ],
    "bsc": [
        "https://bsc-dataseed.binance.org",
        "https://bsc-dataseed1.defibit.io",
        "https://rpc.ankr.com/bsc",
    ],
    "polygon": [
        "https://polygon-rpc.com",
        "https://rpc.ankr.com/polygon",
        "https://polygon.llamarpc.com",
    ],
    "arbitrum": [
        "https://arb1.arbitrum.io/rpc",
        "https://rpc.ankr.com/arbitrum",
        "https://arbitrum.llamarpc.com",
    ],
    "base": [
        "https://mainnet.base.org",
        "https://base.llamarpc.com",
        "https://rpc.ankr.com/base",
    ],
    "avalanche": [
        "https://api.avax.network/ext/bc/C/rpc",
        "https://rpc.ankr.com/avalanche",
    ],
    "optimism": [
        "https://mainnet.optimism.io",
        "https://rpc.ankr.com/optimism",
    ],
    # Monad mainnet (launched Nov 24, 2025)
    "monad": [
        "https://rpc.monad.xyz",
        "https://rpc1.monad.xyz",  # Alchemy
        "https://rpc3.monad.xyz",  # Ankr
    ],
}

# Known burn/dead addresses to exclude from circulating supply
DEAD_ADDRESSES = [
    "0x0000000000000000000000000000000000000000",  # Zero address
    "0x000000000000000000000000000000000000dEaD",  # Dead address
    "0xdead000000000000000000000000000000000000",  # Alternative dead
]

# Chain name normalization
CHAIN_ALIASES = {
    "eth": "ethereum",
    "mainnet": "ethereum",
    "bnb": "bsc",
    "binance": "bsc",
    "matic": "polygon",
    "arb": "arbitrum",
    "avax": "avalanche",
    "op": "optimism",
}


@dataclass
class SupplyData:
    """On-chain supply data result."""
    total_supply: float
    circulating_supply: float
    burned_supply: float
    float_percent: float
    decimals: int
    token_name: Optional[str]
    token_symbol: Optional[str]
    chain: str
    contract_address: str
    fetched_at: str
    source: str = "onchain"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_supply": self.total_supply,
            "circulating_supply": self.circulating_supply,
            "burned_supply": self.burned_supply,
            "float_percent": self.float_percent,
            "decimals": self.decimals,
            "token_name": self.token_name,
            "token_symbol": self.token_symbol,
            "chain": self.chain,
            "contract_address": self.contract_address,
            "fetched_at": self.fetched_at,
            "_source": "onchain_rpc",
        }


class OnChainSupplyFetcher:
    """
    Fetches token supply data directly from blockchain RPC endpoints.

    This is a fallback when CoinGecko/CryptoRank don't have circulating supply data.
    """

    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self._rpc_cache: Dict[str, str] = {}  # Chain -> working RPC URL

    def _normalize_chain(self, chain: str) -> str:
        """Normalize chain name to standard format."""
        chain_lower = chain.lower().strip()
        return CHAIN_ALIASES.get(chain_lower, chain_lower)

    def _get_rpc_url(self, chain: str) -> Optional[str]:
        """Get a working RPC URL for the chain."""
        chain = self._normalize_chain(chain)

        # Return cached working RPC
        if chain in self._rpc_cache:
            return self._rpc_cache[chain]

        # Get RPC list for chain
        rpc_list = RPC_ENDPOINTS.get(chain, [])
        if not rpc_list:
            logger.warning(f"No RPC endpoints configured for chain: {chain}")
            return None

        # Test each RPC until one works
        for rpc_url in rpc_list:
            try:
                # Simple test call
                response = requests.post(
                    rpc_url,
                    json={
                        "jsonrpc": "2.0",
                        "method": "eth_blockNumber",
                        "params": [],
                        "id": 1
                    },
                    timeout=5
                )
                if response.status_code == 200:
                    result = response.json()
                    if "result" in result:
                        self._rpc_cache[chain] = rpc_url
                        logger.debug(f"Using RPC for {chain}: {rpc_url}")
                        return rpc_url
            except Exception as e:
                logger.debug(f"RPC {rpc_url} failed: {e}")
                continue

        logger.warning(f"All RPC endpoints failed for chain: {chain}")
        return None

    def _eth_call(self, rpc_url: str, contract: str, data: str) -> Optional[str]:
        """Make an eth_call to the contract."""
        try:
            response = requests.post(
                rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "method": "eth_call",
                    "params": [
                        {"to": contract, "data": data},
                        "latest"
                    ],
                    "id": 1
                },
                timeout=self.timeout
            )

            if response.status_code == 200:
                result = response.json()
                if "result" in result and result["result"] != "0x":
                    return result["result"]
            return None
        except Exception as e:
            logger.debug(f"eth_call failed: {e}")
            return None

    def _decode_uint256(self, hex_value: str) -> int:
        """Decode a uint256 from hex."""
        if not hex_value or hex_value == "0x":
            return 0
        return int(hex_value, 16)

    def _decode_string(self, hex_value: str) -> Optional[str]:
        """Decode a string from ABI-encoded hex."""
        if not hex_value or hex_value == "0x" or len(hex_value) < 130:
            return None
        try:
            # ABI encoding: offset (32 bytes) + length (32 bytes) + data
            # Skip "0x" prefix and first 64 chars (offset)
            length_hex = hex_value[66:130]
            length = int(length_hex, 16)
            if length == 0 or length > 100:  # Sanity check
                return None

            # Get string bytes
            data_start = 130
            data_end = data_start + (length * 2)
            data_hex = hex_value[data_start:data_end]

            return bytes.fromhex(data_hex).decode('utf-8', errors='ignore').strip()
        except Exception:
            return None

    def _get_balance(self, rpc_url: str, contract: str, address: str) -> int:
        """Get token balance for an address."""
        # Pad address to 32 bytes
        padded_address = "0x" + address.lower().replace("0x", "").zfill(64)
        data = ERC20_BALANCE_OF + padded_address[2:]  # Remove 0x from padded address

        result = self._eth_call(rpc_url, contract, data)
        if result:
            return self._decode_uint256(result)
        return 0

    def get_token_info(self, contract_address: str, chain: str) -> Optional[Dict[str, Any]]:
        """
        Get basic token info (name, symbol, decimals, total supply).

        Args:
            contract_address: Token contract address (0x...)
            chain: Blockchain name (ethereum, bsc, polygon, etc.)

        Returns:
            Dict with token info or None if failed
        """
        chain = self._normalize_chain(chain)
        rpc_url = self._get_rpc_url(chain)

        if not rpc_url:
            return None

        contract = contract_address.lower()

        # Get decimals
        decimals_result = self._eth_call(rpc_url, contract, ERC20_DECIMALS)
        decimals = self._decode_uint256(decimals_result) if decimals_result else 18

        # Get total supply
        supply_result = self._eth_call(rpc_url, contract, ERC20_TOTAL_SUPPLY)
        if not supply_result:
            logger.warning(f"Failed to get totalSupply for {contract} on {chain}")
            return None

        total_supply_raw = self._decode_uint256(supply_result)
        total_supply = total_supply_raw / (10 ** decimals)

        # Get name and symbol (optional)
        name_result = self._eth_call(rpc_url, contract, ERC20_NAME)
        symbol_result = self._eth_call(rpc_url, contract, ERC20_SYMBOL)

        return {
            "contract_address": contract_address,
            "chain": chain,
            "decimals": decimals,
            "total_supply": total_supply,
            "total_supply_raw": total_supply_raw,
            "name": self._decode_string(name_result),
            "symbol": self._decode_string(symbol_result),
        }

    def get_circulating_supply(
        self,
        contract_address: str,
        chain: str,
        excluded_addresses: Optional[List[str]] = None
    ) -> Optional[SupplyData]:
        """
        Calculate circulating supply by subtracting burned/locked tokens from total.

        Args:
            contract_address: Token contract address (0x...)
            chain: Blockchain name (ethereum, bsc, polygon, etc.)
            excluded_addresses: Additional addresses to exclude (team wallets, vesting contracts)

        Returns:
            SupplyData object or None if failed
        """
        chain = self._normalize_chain(chain)
        rpc_url = self._get_rpc_url(chain)

        if not rpc_url:
            logger.error(f"No working RPC for chain: {chain}")
            return None

        contract = contract_address.lower()

        # Get basic token info
        token_info = self.get_token_info(contract_address, chain)
        if not token_info:
            return None

        decimals = token_info["decimals"]
        total_supply_raw = token_info["total_supply_raw"]
        total_supply = token_info["total_supply"]

        # Calculate burned supply (tokens sent to dead addresses)
        burned_raw = 0
        all_excluded = DEAD_ADDRESSES + (excluded_addresses or [])

        for addr in all_excluded:
            balance = self._get_balance(rpc_url, contract, addr)
            burned_raw += balance
            if balance > 0:
                logger.debug(f"Found {balance / (10 ** decimals):.2f} tokens at {addr}")

        burned_supply = burned_raw / (10 ** decimals)
        circulating_supply = total_supply - burned_supply

        # Calculate float percent
        float_percent = (circulating_supply / total_supply * 100) if total_supply > 0 else 0

        return SupplyData(
            total_supply=total_supply,
            circulating_supply=circulating_supply,
            burned_supply=burned_supply,
            float_percent=round(float_percent, 2),
            decimals=decimals,
            token_name=token_info.get("name"),
            token_symbol=token_info.get("symbol"),
            chain=chain,
            contract_address=contract_address,
            fetched_at=datetime.utcnow().isoformat() + "Z",
        )

    def fetch_for_token(
        self,
        token_symbol: str,
        contract_address: str,
        chain: str,
        excluded_addresses: Optional[List[str]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch on-chain supply data and return in DACLE-compatible format.

        This is the main entry point for integration with the data pipeline.

        Args:
            token_symbol: Token symbol (e.g., "ALLOCA")
            contract_address: Token contract address
            chain: Blockchain name
            excluded_addresses: Additional addresses to exclude

        Returns:
            Dict compatible with DACLE's consolidated.json format
        """
        logger.info(f"Fetching on-chain supply for {token_symbol} on {chain}")

        if not contract_address:
            logger.warning(f"No contract address for {token_symbol}")
            return None

        supply_data = self.get_circulating_supply(
            contract_address,
            chain,
            excluded_addresses
        )

        if not supply_data:
            logger.warning(f"Failed to fetch on-chain supply for {token_symbol}")
            return None

        logger.info(
            f"On-chain supply for {token_symbol}: "
            f"total={supply_data.total_supply:,.0f}, "
            f"circulating={supply_data.circulating_supply:,.0f}, "
            f"float={supply_data.float_percent}%"
        )

        # Return DACLE-compatible format
        return {
            "token_symbol": token_symbol,
            "total_supply": supply_data.total_supply,
            "circulating_supply": supply_data.circulating_supply,
            "circulating_supply_at_tge": supply_data.circulating_supply,  # Best estimate
            "float_percent": supply_data.float_percent,
            "burned_supply": supply_data.burned_supply,
            "decimals": supply_data.decimals,
            "chain": supply_data.chain,
            "contract_address": supply_data.contract_address,
            "_source": "onchain_rpc",
            "_fetched_at": supply_data.fetched_at,
            "_data_confidence": 90,  # High confidence for on-chain data
        }


# Convenience function for quick lookups
def get_onchain_supply(
    contract_address: str,
    chain: str = "ethereum"
) -> Optional[Dict[str, Any]]:
    """
    Quick helper to get on-chain supply data.

    Example:
        data = get_onchain_supply("0x1aD7052BB331A0529c1981c3EC2bC4663498A110", "ethereum")
        print(f"Float: {data['float_percent']}%")
    """
    fetcher = OnChainSupplyFetcher()
    return fetcher.get_circulating_supply(contract_address, chain)


if __name__ == "__main__":
    # Test with a known token
    import sys

    logging.basicConfig(level=logging.INFO)

    # Test addresses
    test_cases = [
        # Ethereum tokens
        ("USDT", "0xdAC17F958D2ee523a2206206994597C13D831ec7", "ethereum"),
        ("USDC", "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "ethereum"),
    ]

    fetcher = OnChainSupplyFetcher()

    for symbol, address, chain in test_cases:
        print(f"\n{'='*50}")
        print(f"Testing {symbol} on {chain}")
        print(f"{'='*50}")

        result = fetcher.fetch_for_token(symbol, address, chain)
        if result:
            print(f"Total Supply: {result['total_supply']:,.0f}")
            print(f"Circulating: {result['circulating_supply']:,.0f}")
            print(f"Float %: {result['float_percent']}%")
        else:
            print("Failed to fetch data")
