#!/usr/bin/env python3
"""
Smart Token Lookup - Auto-resolve token name, symbol, and contract address

Session 87: Created to eliminate the tedious "Token NAME SYMBOL" format.
Session 88: Added Contract Address verification to prevent entity resolution errors.
Session 267: Migrated from scripts/helpers/token_lookup.py to src/data/token_lookup.py

The Problem:
    - User types: python scripts/tge_pipeline.py --token "Irys IRYS"
    - But sometimes it's "IRYS Irys" or just "IRYS"
    - API failures when name doesn't match
    - CRITICAL: "POWER" could resolve to "Power Protocol" instead of "Fableborne"

The Solution:
    - User types: python scripts/tge/full_analysis.py IRYS
    - We auto-lookup the full name from CoinGecko/CryptoRank
    - Contract Address (CA) used as primary identifier when available
    - Returns standardized {symbol, name, coingecko_id, contract_address, ...}

Usage:
    from src.data.token_lookup import lookup_token, lookup_by_contract

    # By symbol (uses local cache first)
    info = lookup_token("IRYS")
    # Returns: {"symbol": "IRYS", "name": "Irys", "coingecko_id": "irys", ...}

    # By contract address (most reliable)
    info = lookup_by_contract("0x50f41F589aFACa2EF41FDF590FE7b90cD26DEe64")
    # Returns: {"symbol": "IRYS", "name": "Irys", "contract_address": "0x...", ...}

    info = lookup_token("irys")  # Case-insensitive
    # Same result
"""

import json
import os
import requests
from pathlib import Path
from typing import Dict, Optional, Any
from functools import lru_cache

PROJECT_ROOT = Path(__file__).parent.parent


def _search_coingecko(query: str) -> Optional[Dict[str, Any]]:
    """Search CoinGecko for a token."""
    try:
        # Try direct ID lookup first (faster)
        url = f"https://api.coingecko.com/api/v3/coins/{query.lower()}"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return {
                "symbol": data.get("symbol", "").upper(),
                "name": data.get("name"),
                "coingecko_id": data.get("id"),
                "market_cap_rank": data.get("market_cap_rank"),
                "source": "coingecko_direct"
            }
    except Exception:
        pass

    # Fallback to search
    try:
        url = f"https://api.coingecko.com/api/v3/search?query={query}"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            coins = data.get("coins", [])
            if coins:
                # Find best match (exact symbol match preferred)
                for coin in coins:
                    if coin.get("symbol", "").upper() == query.upper():
                        return {
                            "symbol": coin.get("symbol", "").upper(),
                            "name": coin.get("name"),
                            "coingecko_id": coin.get("id"),
                            "market_cap_rank": coin.get("market_cap_rank"),
                            "source": "coingecko_search"
                        }
                # Return first result if no exact match
                coin = coins[0]
                return {
                    "symbol": coin.get("symbol", "").upper(),
                    "name": coin.get("name"),
                    "coingecko_id": coin.get("id"),
                    "market_cap_rank": coin.get("market_cap_rank"),
                    "source": "coingecko_search"
                }
    except Exception:
        pass

    return None


def _search_cryptorank(query: str) -> Optional[Dict[str, Any]]:
    """Search CryptoRank for a token."""
    api_key = os.getenv("CRYPTORANK_API_KEY")
    if not api_key:
        return None

    try:
        url = f"https://api.cryptorank.io/v1/currencies?search={query}&limit=5"
        headers = {"X-Api-Key": api_key}
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            currencies = data.get("data", [])
            if currencies:
                # Find best match
                for curr in currencies:
                    if curr.get("symbol", "").upper() == query.upper():
                        return {
                            "symbol": curr.get("symbol", "").upper(),
                            "name": curr.get("name"),
                            "cryptorank_id": curr.get("slug"),
                            "cryptorank_key": curr.get("key"),
                            "source": "cryptorank"
                        }
                # Return first result
                curr = currencies[0]
                return {
                    "symbol": curr.get("symbol", "").upper(),
                    "name": curr.get("name"),
                    "cryptorank_id": curr.get("slug"),
                    "cryptorank_key": curr.get("key"),
                    "source": "cryptorank"
                }
    except Exception:
        pass

    return None


def _check_local_data(symbol: str) -> Optional[Dict[str, Any]]:
    """Check if we already have local data for this token."""
    token_dir = PROJECT_ROOT / "data" / "tokens" / symbol.upper()
    consolidated = token_dir / "consolidated.json"

    if consolidated.exists():
        try:
            with open(consolidated) as f:
                data = json.load(f)
            return {
                "symbol": data.get("symbol") or data.get("token_symbol") or symbol.upper(),
                "name": data.get("token_name") or data.get("name"),
                "coingecko_id": data.get("coingecko_id"),
                "contract_address": data.get("contract_address"),
                "blockchain": data.get("blockchain"),
                "source": "local_cache"
            }
        except Exception:
            pass

    return None


def _is_valid_contract_address(address: str) -> bool:
    """Check if string looks like a valid contract address."""
    if not address or not isinstance(address, str):
        return False
    # Ethereum-style: 0x + 40 hex chars
    if address.startswith("0x") and len(address) == 42:
        try:
            int(address[2:], 16)
            return True
        except ValueError:
            return False
    # Solana-style: base58, typically 32-44 chars
    if len(address) >= 32 and len(address) <= 44:
        return True
    return False


def lookup_by_contract(contract_address: str) -> Dict[str, Any]:
    """
    Look up token by contract address - most reliable method.

    Args:
        contract_address: The token's contract address (e.g., "0x...")

    Returns:
        Dict with: symbol, name, contract_address, source, etc.
        If not found: {"contract_address": address, "source": "not_found"}
    """
    if not _is_valid_contract_address(contract_address):
        return {"contract_address": contract_address, "source": "invalid_address"}

    # Check local cache first - scan all tokens for matching CA
    tokens_dir = PROJECT_ROOT / "data" / "tokens"
    if tokens_dir.exists():
        for token_dir in tokens_dir.iterdir():
            if not token_dir.is_dir():
                continue
            consolidated = token_dir / "consolidated.json"
            if consolidated.exists():
                try:
                    with open(consolidated) as f:
                        data = json.load(f)
                    local_ca = data.get("contract_address", "")
                    # Case-insensitive comparison for Ethereum addresses
                    if local_ca and local_ca.lower() == contract_address.lower():
                        return {
                            "symbol": data.get("symbol") or data.get("token_symbol"),
                            "name": data.get("token_name") or data.get("name"),
                            "coingecko_id": data.get("coingecko_id"),
                            "contract_address": local_ca,
                            "blockchain": data.get("blockchain"),
                            "source": "local_cache_by_ca"
                        }
                except Exception:
                    continue

    # Try CoinGecko contract lookup
    try:
        # Ethereum mainnet
        url = f"https://api.coingecko.com/api/v3/coins/ethereum/contract/{contract_address}"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return {
                "symbol": data.get("symbol", "").upper(),
                "name": data.get("name"),
                "coingecko_id": data.get("id"),
                "contract_address": contract_address,
                "blockchain": "Ethereum",
                "source": "coingecko_contract"
            }
    except Exception:
        pass

    # Try other chains (Base, Arbitrum, etc.) if Ethereum fails
    for chain in ["base", "arbitrum-one", "polygon-pos", "optimistic-ethereum"]:
        try:
            url = f"https://api.coingecko.com/api/v3/coins/{chain}/contract/{contract_address}"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                return {
                    "symbol": data.get("symbol", "").upper(),
                    "name": data.get("name"),
                    "coingecko_id": data.get("id"),
                    "contract_address": contract_address,
                    "blockchain": chain,
                    "source": "coingecko_contract"
                }
        except Exception:
            continue

    return {"contract_address": contract_address, "source": "not_found"}


def lookup_token(query: str, use_cache: bool = True) -> Dict[str, Any]:
    """
    Look up token information from symbol or name.

    Args:
        query: Token symbol (IRYS) or name (Irys) - case insensitive
        use_cache: Check local data first (default True)

    Returns:
        Dict with: symbol, name, coingecko_id, source, etc.
        If not found: {"symbol": query.upper(), "name": None, "source": "not_found"}
    """
    query = query.strip()
    symbol = query.upper()

    # ALWAYS check local cache first - it has the correct token for our analysis
    local = _check_local_data(symbol)
    if local and local.get("name"):
        return local

    # Only hit APIs if no local cache
    if not use_cache:
        # Try CoinGecko (free, no API key needed)
        cg_result = _search_coingecko(query)
        if cg_result:
            return cg_result

        # Try CryptoRank (needs API key)
        cr_result = _search_cryptorank(query)
        if cr_result:
            return cr_result

    # Not found in cache and APIs skipped or failed - return minimal
    return {
        "symbol": symbol,
        "name": None,
        "source": "not_found"
    }


def get_token_arg(symbol: str, search_api: bool = True) -> str:
    """
    Get the proper token argument for tge_pipeline.py

    Args:
        symbol: Token symbol (e.g., "IRYS")
        search_api: If True, search APIs when no local cache

    Returns:
        String in format "Name SYMBOL" for tge_pipeline.py --token argument
    """
    # First check local cache (always)
    info = lookup_token(symbol, use_cache=True)

    # If not found locally and search_api is True, try APIs
    if info.get("source") == "not_found" and search_api:
        info = lookup_token(symbol, use_cache=False)

    name = info.get("name")
    sym = info.get("symbol") or symbol.upper()

    # If we have a name, use "Name SYMBOL" format
    if name:
        return f"{name} {sym}"

    # Fallback: just use symbol twice
    return f"{sym} {sym}"


def verify_token_identity(symbol: str, expected_ca: Optional[str] = None) -> Dict[str, Any]:
    """
    Verify token identity using contract address cross-reference.

    This is the SAFEST lookup method - prevents the POWER/Power Protocol confusion.

    Args:
        symbol: Token symbol to look up
        expected_ca: Contract address to verify against (from user or local cache)

    Returns:
        Dict with verification result:
        - verified: True if CA matches or no CA to verify
        - token_info: The looked up token info
        - warning: If CA mismatch detected
    """
    # First get local info
    local_info = _check_local_data(symbol)
    local_ca = local_info.get("contract_address") if local_info else None

    # Use expected_ca if provided, otherwise use local
    verify_ca = expected_ca or local_ca

    # Get API info
    api_info = lookup_token(symbol, use_cache=False)

    # If we have a CA to verify and API returned something
    if verify_ca and api_info.get("source") not in ["not_found", "local_cache"]:
        # Check if API result has matching CA
        api_ca = api_info.get("contract_address")
        if api_ca and api_ca.lower() != verify_ca.lower():
            return {
                "verified": False,
                "token_info": local_info or api_info,
                "warning": f"CA MISMATCH: Local={verify_ca}, API={api_ca}. Using local data.",
                "used_local": True
            }

    # If we have local data with CA, prefer it
    if local_info and local_ca:
        return {
            "verified": True,
            "token_info": local_info,
            "warning": None,
            "used_local": True
        }

    return {
        "verified": True,
        "token_info": api_info,
        "warning": None if api_info.get("source") != "not_found" else "Token not found",
        "used_local": False
    }


def main():
    """CLI for testing token lookup."""
    import sys

    if len(sys.argv) < 2:
        print("Usage: python scripts/helpers/token_lookup.py <SYMBOL_OR_CA>")
        print("Examples:")
        print("  python scripts/helpers/token_lookup.py IRYS")
        print("  python scripts/helpers/token_lookup.py 0x50f41F589aFACa2EF41FDF590FE7b90cD26DEe64")
        print("  python scripts/helpers/token_lookup.py POWER --verify")
        sys.exit(1)

    query = sys.argv[1]
    verify_mode = "--verify" in sys.argv

    print(f"Looking up: {query}")

    # Check if query is a contract address
    if _is_valid_contract_address(query):
        print("Detected contract address format - using CA lookup...")
        result = lookup_by_contract(query)
        print(json.dumps(result, indent=2))
        return

    # Symbol lookup
    if verify_mode:
        print("Running verified lookup (cross-references CA)...")
        result = verify_token_identity(query)
        print(json.dumps(result, indent=2))
        if result.get("warning"):
            print(f"\n⚠️  WARNING: {result['warning']}")
    else:
        result = lookup_token(query, use_cache=False)
        print(json.dumps(result, indent=2))

    print(f"\nToken arg for pipeline: {get_token_arg(query)}")


if __name__ == "__main__":
    main()
