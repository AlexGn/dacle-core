#!/usr/bin/env python3
"""
Primary Source Fetcher - Session 79H (Updated 79K, 86A)

Session 267: Migrated from scripts/helpers/primary_source_fetcher.py to src/data/primary_source_fetcher.py

Fetches token data from primary sources BEFORE falling back to Perplexity/OpenAI.

Session 86A (SEEK Learnings):
- Added check_token_live_status() - CRITICAL first check before TGE analysis
- If CoinGecko has live price data, token has already launched (TGE happened)
- Prevents stale TGE dates from ICODrops/Perplexity causing incorrect analysis
- Learning: CoinGecko is gold standard for POST-TGE data

Priority Order:
0. CoinGecko LIVE CHECK (Session 86A) - Is token already trading?
1. CryptoRank API - Primary TGE discovery source
1.5 CryptoRank Web Scrape - Fallback when API fails (Session 79K)
2. Dropstab - Web scraping (vesting, funding, investors)
3. CoinGecko API - Free, no auth (FDV, market cap, contract_address)
4. CoinMarketCap API - Limited to contract_address lookups (333/day limit)

Only after all 5 primary sources are exhausted should we call AI APIs.

Usage:
    from src.data.primary_source_fetcher import fetch_from_primary_sources
    from src.data.primary_source_fetcher import check_token_live_status

    # FIRST: Check if token is already live
    live_status = check_token_live_status("SEEK", "Talisman")
    if live_status["is_live"]:
        print(f"Token is LIVE! Price: ${live_status['current_price']}")
        # Skip TGE analysis, use live data instead

    # Fetch missing fields from primary sources
    result = fetch_from_primary_sources("IRYS", ["contract_address", "investors"])

Created: 2025-11-30 (Session 79H - Data Source Priority System)
Updated: 2025-12-05 (Session 86A - SEEK TGE Date Learning)
"""

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

# Session 148: Phase 2 & 3 enhancements (retry logic + data normalization)
# Session 267: Updated import to new location
from src.utils.network_resilience import (
    fetch_with_retry,
    post_with_retry,
    RetryableError,
    DataNormalizer,
    GLOBAL_RATE_LIMITER  # Session 340 Part 4: Rate limiting for parallel fetches
)

logger = logging.getLogger(__name__)

# Session 79I: Rate limiting for CoinGecko API
# Session 339 (P0.2): Reduced from 1.0s to 0.5s - CoinGecko free tier allows 10-30 req/min
COINGECKO_RATE_LIMIT_DELAY = 0.5  # 0.5 seconds between calls

# Session 340: DexScreener API endpoint (no rate limits, faster)
DEXSCREENER_SEARCH_URL = "https://api.dexscreener.com/latest/dex/search"


def rate_limited(delay: float):
    """Decorator to add delay between calls to respect rate limits."""
    def decorator(func):
        last_call = [0.0]
        @wraps(func)
        def wrapper(*args, **kwargs):
            elapsed = time.time() - last_call[0]
            if elapsed < delay:
                sleep_time = delay - elapsed
                logger.debug(f"Rate limiting: sleeping {sleep_time:.2f}s")
                time.sleep(sleep_time)
            result = func(*args, **kwargs)
            last_call[0] = time.time()
            return result
        return wrapper
    return decorator


def verify_token_identity(
    searched_token: str,
    result: Dict,
    known_contract: Optional[str] = None
) -> bool:
    """
    Session 79I: Verify fetched data is for the correct token.

    Prevents merging data for wrong token when symbol is ambiguous
    (e.g., "CORE" → CoreDAO vs cVault.finance).

    Priority:
    1. Contract address match (if we have one) - definitive
    2. Exact symbol match - strong signal
    """
    # If we have a known contract, verify it matches
    if known_contract:
        fetched_contract = result.get("contract_address", "").lower()
        if fetched_contract and fetched_contract != known_contract.lower():
            logger.warning(f"Contract mismatch: expected {known_contract}, got {fetched_contract}")
            return False

    # Verify symbol matches
    fetched_symbol = result.get("token_symbol", "").upper()
    if fetched_symbol and fetched_symbol != searched_token.upper():
        # Session 397: Allow match if searched token has a multiplier prefix (e.g. 1000BONK -> BONK)
        normalized_searched = normalize_token_symbol(searched_token)
        if fetched_symbol != normalized_searched:
            logger.warning(f"Symbol mismatch: searched {searched_token}, got {fetched_symbol}")
            return False

    return True

def normalize_token_symbol(symbol: str) -> str:
    """
    Session 397: Normalize token symbol by removing common multiplier prefixes.
    e.g. 1000BONK -> BONK, 1BONK -> BONK
    """
    if not symbol:
        return symbol
    s = str(symbol).upper()
    if s.startswith("1000") and len(s) > 4 and s[4].isalpha():
        return s[4:]
    if s.startswith("100") and len(s) > 3 and s[3].isalpha():
        return s[3:]
    if s.startswith("1") and len(s) > 1 and s[1].isalpha():
        return s[1:]
    return s

def get_symbol_multiplier(symbol: str) -> float:
    """
    Session 397: Extract numeric multiplier from symbol prefix.
    e.g. 1000BONK -> 1000.0, 1BONK -> 1.0, BONK -> 1.0
    """
    if not symbol:
        return 1.0
    s = str(symbol).upper()
    import re
    match = re.match(r'^(\d+)', s)
    if match:
        prefix = match.group(1)
        # Verify it's a multiplier (followed by alpha)
        if len(s) > len(prefix) and s[len(prefix)].isalpha():
            try:
                return float(prefix)
            except ValueError:
                pass
    return 1.0

# Project root for saving files
PROJECT_ROOT = Path(__file__).parent.parent.parent


# ============================================================================
# SESSION 86A: TOKEN LIVE STATUS CHECK (SEEK Learning)
# ============================================================================
# CRITICAL: This check MUST run first before any TGE analysis.
# If token is live on CoinGecko, the TGE has already happened.
# This prevents using stale/incorrect TGE dates from ICODrops/Perplexity.
#
# SEEK Case Study (Dec 5, 2025):
# - ICODrops had wrong TGE date (May 30, 2025)
# - Perplexity couldn't find correct date
# - CoinGecko showed live price data → Token was actually live TODAY
# - Solution: Check CoinGecko first, if price exists → status=Live
# ============================================================================


def check_token_live_status_fast(
    token_symbol: str,
    token_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Session 340: Fast live status check using DexScreener (NO rate limits).

    DexScreener has:
    - No rate limits (vs CoinGecko 10-30 req/min)
    - Fast response (<500ms vs CoinGecko 2-3s when rate limited)
    - Same data: price, FDV, market cap

    Use this instead of check_token_live_status() for faster pipeline execution.

    Args:
        token_symbol: Token symbol (e.g., "POWER")
        token_name: Token name for better matching (optional)

    Returns:
        Dict with is_live, status, current_price, fdv, etc.
    """
    try:
        # Session 397: Normalize symbol for lookup
        normalized_symbol = normalize_token_symbol(token_symbol)
        
        # Search DexScreener (no rate limits)
        search_url = f"{DEXSCREENER_SEARCH_URL}?q={normalized_symbol}"
        response = requests.get(search_url, timeout=10)

        if response.status_code != 200:
            logger.warning(f"DexScreener search failed: {response.status_code}")
            return {
                "is_live": None,
                "status": "Unknown",
                "token_symbol": token_symbol.upper(),
                "error": f"DexScreener error: {response.status_code}",
                "source": "dexscreener"
            }

        data = response.json()
        pairs = data.get("pairs", [])

        if not pairs:
            # Token not found on DEX - could be pre-TGE or CEX-only
            logger.info(f"Live check: {normalized_symbol} (original: {token_symbol}) not found on DexScreener")
            return {
                "is_live": None,  # Unknown - might be CEX-only
                "status": "Unknown",
                "token_symbol": token_symbol.upper(),
                "reason": "Token not found on DexScreener (may be CEX-only or pre-TGE)",
                "source": "dexscreener"
            }

        # Find best matching pair by symbol
        best_pair = None
        for pair in pairs:
            base_token = pair.get("baseToken", {})
            pair_symbol = base_token.get("symbol", "").upper()
            if pair_symbol == normalized_symbol.upper() or pair_symbol == token_symbol.upper():
                # Prefer pairs with higher liquidity
                if best_pair is None or (pair.get("liquidity", {}).get("usd", 0) or 0) > \
                   (best_pair.get("liquidity", {}).get("usd", 0) or 0):
                    best_pair = pair

        if not best_pair:
            # Symbol not matched in any pair
            return {
                "is_live": None,
                "status": "Unknown",
                "token_symbol": token_symbol.upper(),
                "reason": "No matching pair found",
                "source": "dexscreener"
            }

        # Extract data from best pair
        price_usd = float(best_pair.get("priceUsd") or 0)
        fdv = float(best_pair.get("fdv") or 0)
        market_cap = float(best_pair.get("marketCap") or 0)
        liquidity = best_pair.get("liquidity", {}).get("usd", 0)

        # Session 397: Scale price by symbol multiplier (e.g. 1000BONK = 1000 * BONK price)
        multiplier = get_symbol_multiplier(token_symbol)
        if multiplier > 1.0 and price_usd > 0:
            logger.info(f"📐 Scaling DexScreener price by {multiplier}x for {token_symbol}")
            price_usd *= multiplier

        if price_usd > 0:
            # Token is LIVE!
            logger.info(f"🟢 LIVE CHECK: {token_symbol} is LIVE! Price: ${price_usd}, FDV: ${fdv:,.0f}")
            return {
                "is_live": True,
                "status": "Live",
                "token_symbol": token_symbol.upper(),
                "current_price": price_usd,
                "fdv": fdv,
                "market_cap": market_cap,
                "liquidity_usd": liquidity,
                "chain": best_pair.get("chainId"),
                "dex_name": best_pair.get("dexId"),
                "pair_address": best_pair.get("pairAddress"),
                "tge_detected_at": datetime.now().isoformat(),
                "source": "dexscreener"
            }
        else:
            return {
                "is_live": False,
                "status": "Pre-TGE",
                "token_symbol": token_symbol.upper(),
                "reason": "Token found but no price data",
                "source": "dexscreener"
            }

    except requests.Timeout:
        logger.warning(f"DexScreener timeout for {token_symbol}")
        return {
            "is_live": None,
            "status": "Unknown",
            "token_symbol": token_symbol.upper(),
            "error": "DexScreener timeout",
            "source": "dexscreener"
        }
    except Exception as e:
        logger.error(f"DexScreener error for {token_symbol}: {e}")
        return {
            "is_live": None,
            "status": "Unknown",
            "token_symbol": token_symbol.upper(),
            "error": str(e),
            "source": "dexscreener"
        }


@rate_limited(COINGECKO_RATE_LIMIT_DELAY)
def check_token_live_status(
    token_symbol: str,
    token_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Session 86A: Check if token is already live/trading on CoinGecko.

    THIS IS THE FIRST CHECK that should run before TGE analysis.
    If CoinGecko has live price data, the TGE has already happened.

    Why this matters (SEEK case study):
    - ICODrops had TGE date of May 30, 2025 (WRONG)
    - Token actually launched Dec 5, 2025 (TODAY)
    - CoinGecko showed live price $0.355 → Immediately knew token was live
    - This check would have caught the error instantly

    Args:
        token_symbol: Token symbol (e.g., "SEEK")
        token_name: Token name for better matching (e.g., "Talisman")

    Returns:
        Dict with:
        - is_live: bool - True if token has live trading data
        - status: str - "Live", "Pre-TGE", or "Unknown"
        - current_price: float or None
        - fdv: float or None
        - market_cap: float or None
        - total_supply: float or None
        - contract_address: str or None
        - tge_detected_at: str - ISO timestamp when live status detected
        - coingecko_id: str or None
        - source: "coingecko"

    Example:
        >>> status = check_token_live_status("SEEK", "Talisman")
        >>> if status["is_live"]:
        ...     print(f"SEEK is LIVE at ${status['current_price']}")
        ...     print(f"Detected at: {status['tge_detected_at']}")
    """
    try:
        # Session 397: Normalize symbol for lookup
        normalized_symbol = normalize_token_symbol(token_symbol)
        
        # Search for token on CoinGecko
        search_query = token_name if token_name else normalized_symbol
        search_url = f"https://api.coingecko.com/api/v3/search?query={search_query}"
        headers = {"accept": "application/json"}

        # Session 148: Use retry logic with exponential backoff
        try:
            response = fetch_with_retry(search_url, headers, timeout=10)
        except RetryableError as e:
            logger.warning(f"CoinGecko search failed after retries: {e}")
            return {
                "is_live": False,
                "status": "Unknown",
                "error": f"API error after retries: {str(e)}",
                "source": "coingecko"
            }

        if response.status_code != 200:
            logger.warning(f"CoinGecko search failed: {response.status_code}")
            return {
                "is_live": False,
                "status": "Unknown",
                "error": f"API error: {response.status_code}",
                "source": "coingecko"
            }

        search_data = response.json()
        coins = search_data.get("coins", [])

        # Find matching coin
        coin_id = None
        matched_name = None
        for coin in coins:
            coin_symbol = coin.get("symbol", "").upper()
            symbol_match = (coin_symbol == normalized_symbol.upper() or coin_symbol == token_symbol.upper())
            if token_name:
                name_match = token_name.lower() in coin.get("name", "").lower()
                if symbol_match and name_match:
                    coin_id = coin.get("id")
                    matched_name = coin.get("name")
                    break
            elif symbol_match:
                coin_id = coin.get("id")
                matched_name = coin.get("name")
                break

        if not coin_id:
            logger.info(f"Live check: {token_symbol} not found on CoinGecko (Pre-TGE or unlisted)")
            return {
                "is_live": False,
                "status": "Pre-TGE",
                "token_symbol": token_symbol.upper(),
                "reason": "Token not found on CoinGecko",
                "source": "coingecko"
            }

        # Rate limit before fetching details
        time.sleep(COINGECKO_RATE_LIMIT_DELAY)

        # Get market data to check if trading
        coin_url = f"https://api.coingecko.com/api/v3/coins/{coin_id}"
        params = {
            "localization": "false",
            "tickers": "false",
            "market_data": "true",
            "community_data": "false",
            "developer_data": "false"
        }

        # Session 148: Use retry logic
        try:
            response = fetch_with_retry(coin_url, headers, timeout=15, params=params)
        except RetryableError as e:
            logger.warning(f"CoinGecko coin data failed after retries: {e}")
            return {
                "is_live": False,
                "token_symbol": token_symbol.upper(),
                "error": f"API error after retries: {str(e)}",
            }

        if response.status_code != 200:
            return {
                "is_live": False,
                "status": "Unknown",
                "token_symbol": token_symbol.upper(),
                "coingecko_id": coin_id,
                "error": f"Coin data error: {response.status_code}",
                "source": "coingecko"
            }

        coin_data = response.json()
        market_data = coin_data.get("market_data", {})

        # Check for live trading indicators
        current_price = market_data.get("current_price", {}).get("usd")
        fdv = market_data.get("fully_diluted_valuation", {}).get("usd")
        market_cap = market_data.get("market_cap", {}).get("usd")
        total_supply = market_data.get("total_supply")
        circulating_supply = market_data.get("circulating_supply")

        # Session 397: Scale price by symbol multiplier (e.g. 1000BONK = 1000 * BONK price)
        multiplier = get_symbol_multiplier(token_symbol)
        if multiplier > 1.0 and current_price:
            logger.info(f"📐 Scaling CoinGecko price by {multiplier}x for {token_symbol}")
            current_price *= multiplier

        # Extract contract address
        platforms = coin_data.get("platforms", {})
        contract_address = None
        blockchain = None
        for platform_name, address in platforms.items():
            if address and address.startswith("0x") and len(address) == 42:
                contract_address = address
                blockchain = platform_name
                break

        # Determine if token is live
        # Token is considered LIVE if it has a current price > 0
        is_live = current_price is not None and current_price > 0

        result = {
            "is_live": is_live,
            "status": "Live" if is_live else "Pre-TGE",
            "token_symbol": token_symbol.upper(),
            "token_name": matched_name or coin_data.get("name"),
            "coingecko_id": coin_id,
            "current_price": current_price,
            "fdv": fdv,
            "market_cap": market_cap,
            "total_supply": total_supply,
            "circulating_supply": circulating_supply,
            "contract_address": contract_address,
            "blockchain": blockchain,
            "source": "coingecko",
            "checked_at": datetime.utcnow().isoformat() + "Z"
        }

        if is_live:
            result["tge_detected_at"] = datetime.utcnow().isoformat() + "Z"
            logger.info(
                f"🟢 LIVE CHECK: {token_symbol} is LIVE! "
                f"Price: ${current_price:.4f}, FDV: ${fdv:,.0f}" if fdv else
                f"🟢 LIVE CHECK: {token_symbol} is LIVE! Price: ${current_price:.4f}"
            )
        else:
            logger.info(f"⚪ LIVE CHECK: {token_symbol} - No trading data (Pre-TGE)")

        return result

    except Exception as e:
        logger.error(f"Live status check error for {token_symbol}: {e}")
        return {
            "is_live": False,
            "status": "Unknown",
            "error": str(e),
            "source": "coingecko"
        }


def validate_tge_date_against_live_status(
    token_symbol: str,
    claimed_tge_date: str,
    token_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Session 86A: Cross-validate a claimed TGE date against live trading status.

    This catches cases like SEEK where ICODrops had wrong date (May 30)
    but the token was actually live (Dec 5).

    Args:
        token_symbol: Token symbol
        claimed_tge_date: TGE date from ICODrops/Perplexity (ISO format)
        token_name: Optional token name for better matching

    Returns:
        Dict with:
        - valid: bool - True if claimed date is consistent with live status
        - conflict: str or None - Description of conflict if any
        - recommended_action: str - What to do
        - live_status: dict - Result from check_token_live_status
    """
    from datetime import datetime as dt

    # Check live status
    live_status = check_token_live_status(token_symbol, token_name)

    # Parse claimed TGE date
    try:
        if claimed_tge_date:
            # Handle various date formats
            if "T" in claimed_tge_date:
                claimed_dt = dt.fromisoformat(claimed_tge_date.replace("Z", "+00:00"))
            else:
                claimed_dt = dt.strptime(claimed_tge_date, "%Y-%m-%d")
            claimed_dt = claimed_dt.replace(tzinfo=None)
        else:
            claimed_dt = None
    except Exception as e:
        logger.warning(f"Could not parse TGE date '{claimed_tge_date}': {e}")
        claimed_dt = None

    today = dt.utcnow()

    # Analyze conflicts
    result = {
        "token_symbol": token_symbol,
        "claimed_tge_date": claimed_tge_date,
        "live_status": live_status,
        "checked_at": today.isoformat() + "Z"
    }

    if live_status["is_live"]:
        # Token is live - verify claimed date makes sense
        if claimed_dt:
            days_since_claimed = (today - claimed_dt).days

            if days_since_claimed < -1:
                # Claimed date is in the future but token is already live
                result["valid"] = False
                result["conflict"] = (
                    f"TGE date conflict: Claimed TGE is {abs(days_since_claimed)} days "
                    f"in FUTURE ({claimed_tge_date}), but token is ALREADY LIVE on CoinGecko!"
                )
                result["recommended_action"] = "UPDATE_TGE_DATE_TO_TODAY"
                result["corrected_tge_date"] = today.strftime("%Y-%m-%dT%H:%M:%SZ")
                logger.warning(f"🚨 {result['conflict']}")

            elif days_since_claimed > 180:
                # Claimed date is very old but token just appeared live
                # This is the SEEK scenario - ICODrops had May 30, token launched Dec 5
                result["valid"] = False
                result["conflict"] = (
                    f"TGE date conflict: Claimed TGE was {days_since_claimed} days ago "
                    f"({claimed_tge_date}), but token just appeared LIVE. "
                    f"Likely stale data from source."
                )
                result["recommended_action"] = "UPDATE_TGE_DATE_TO_TODAY"
                result["corrected_tge_date"] = today.strftime("%Y-%m-%dT%H:%M:%SZ")
                logger.warning(f"🚨 {result['conflict']}")

            else:
                # Date seems reasonable
                result["valid"] = True
                result["conflict"] = None
                result["recommended_action"] = "NONE"
        else:
            # No claimed date but token is live
            result["valid"] = False
            result["conflict"] = "No TGE date provided but token is LIVE"
            result["recommended_action"] = "SET_TGE_DATE_TO_TODAY"
            result["corrected_tge_date"] = today.strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        # Token is not live yet
        if claimed_dt and claimed_dt < today:
            # Claimed date is in the past but token not live
            result["valid"] = False
            result["conflict"] = (
                f"TGE date passed ({claimed_tge_date}) but token not live on CoinGecko. "
                f"Either TGE delayed or token not yet listed."
            )
            result["recommended_action"] = "VERIFY_TGE_DATE"
        else:
            result["valid"] = True
            result["conflict"] = None
            result["recommended_action"] = "NONE"

    return result


# ============================================================================
# FIELD MAPPING: Which source provides which fields
# ============================================================================

CRYPTORANK_FIELDS = {
    # CRITICAL fields
    "tge_date", "float_percent", "fdv", "fdv_low", "fdv_high",
    "total_supply", "circulating_supply_at_tge", "listing_price",
    "listing_price_low", "listing_price_high",
    "listing_exchanges", "investors", "funding_raised_usd",
    "contract_address",
    # IMPORTANT fields (Session 79I: Added tokenomics extraction)
    "token_allocation", "vesting_schedule", "category", "blockchain",
    "initial_market_cap_low", "initial_market_cap_high",
    "whitepaper_url", "farming_sources",  # Session 85: Added automated extraction
    # Additional fields
    "vc_investors", "funding_rounds", "total_funding", "lead_investors",
    "tge_unlock_pct"
}

# Session 79K: CryptoRank Web Scraper as fallback when API doesn't have the token
# Gets ~28% data from __NEXT_DATA__ JSON blob (JS-rendered fields not available)
CRYPTORANK_WEB_FIELDS = {
    "token_name", "total_supply", "circulating_supply_at_tge",
    "float_percent", "category", "blockchain", "project_description",
    # May get these from __NEXT_DATA__ if available
    "investors", "funding_raised_usd", "token_allocation"
}

DROPSTAB_FIELDS = {
    "investors", "funding_rounds", "total_funding", "funding_raised_usd",
    "vesting_schedule", "token_allocation", "total_supply",
    "circulating_supply_percent", "float_percent",
    "whitepaper_url", "farming_sources"  # Session 85: Added automated extraction
}

ICODROPS_FIELDS = {
    # Session 85 Phase 2: High-quality source for key fields
    "whitepaper_url", "vesting_schedule", "farming_sources",
    "token_allocation", "total_supply", "investors", "funding_rounds",
    "float_percent"
}

COINGECKO_FIELDS = {
    "contract_address", "fdv", "market_cap", "current_price",
    # Session 79I: New IMPORTANT fields from CoinGecko
    "whitepaper_url", "project_description", "categories",
    "website", "website_url", "twitter_handle", "twitter_url", "telegram_channel"
}

# Session 339 (L093B): Symbol aliases for tokens where DACLE symbol != CoinGecko symbol
# Format: "DACLE_SYMBOL": ("COINGECKO_SYMBOL", "coingecko_id", "token_name")
# Use when CoinGecko lists a token under a different symbol than we track
COINGECKO_SYMBOL_ALIASES = {
    "H": ("HSK", "hashkey-ecopoints", "HashKey Platform Token"),
    "REZ": ("REZ", "renzo", "Renzo"),
    # Add more aliases as needed:
    # "DACLE_SYMBOL": ("CG_SYMBOL", "coingecko-id", "name"),
}

CMC_FIELDS = {
    "contract_address", "circulating_supply", "market_cap"
}

# Session 88: OTC/Pre-launch data from Whales Market + Hyperliquid
OTC_FIELDS = {
    "otc_price", "otc_premium", "otc_volume", "otc_available",
    "otc_volume_trend", "otc_platforms"
}


def needs_source(missing_fields: List[str], source_fields: set) -> bool:
    """Check if any missing field can be provided by this source."""
    return bool(set(missing_fields) & source_fields)


def _process_coingecko_data(coin_data: Dict, token_symbol: str) -> Dict[str, Any]:
    """
    Process CoinGecko API response into standardized format.

    Session 339 (L093B): Extracted for reuse by alias and normal fetch paths.

    Args:
        coin_data: Raw response from CoinGecko /coins/{id} endpoint
        token_symbol: The DACLE token symbol (may differ from CoinGecko symbol)

    Returns:
        Dict with standardized token data fields
    """
    market_data = coin_data.get("market_data", {})
    links = coin_data.get("links", {})

    # Extract contract addresses from platforms
    platforms = coin_data.get("platforms", {})
    contract_address = None
    for platform, address in platforms.items():
        if address and address.startswith("0x") and len(address) == 42:
            contract_address = address
            break

    # Extract whitepaper URL from links
    whitepaper_url = None
    whitepaper_links = links.get("whitepaper") or []
    if whitepaper_links:
        whitepaper_url = whitepaper_links if isinstance(whitepaper_links, str) else whitepaper_links[0] if whitepaper_links else None

    # Extract homepage/website
    website = None
    homepage_links = links.get("homepage", [])
    if homepage_links:
        for hp in homepage_links:
            if hp and hp.strip():
                website = hp.strip()
                break

    # Extract social links
    twitter_handle = links.get("twitter_screen_name")
    telegram_channel = links.get("telegram_channel_identifier")

    # Extract description
    description = coin_data.get("description", {})
    project_description = description.get("en") if isinstance(description, dict) else description

    # Truncate description if too long
    if project_description and len(project_description) > 500:
        project_description = project_description[:497] + "..."

    # Extract categories
    categories = coin_data.get("categories", [])
    categories = [c for c in categories if c]

    # Build twitter URL from handle
    twitter_url = f"https://twitter.com/{twitter_handle}" if twitter_handle else None

    result = {
        "token_symbol": token_symbol.upper(),
        "coingecko_id": coin_data.get("id"),
        "name": coin_data.get("name"),
        "contract_address": contract_address,
        "fdv": market_data.get("fully_diluted_valuation", {}).get("usd"),
        "market_cap": market_data.get("market_cap", {}).get("usd"),
        "current_price": market_data.get("current_price", {}).get("usd"),
        "circulating_supply": market_data.get("circulating_supply"),
        "total_supply": market_data.get("total_supply"),
        "whitepaper_url": whitepaper_url,
        "website_url": website,
        "website": website,
        "project_description": project_description,
        "categories": categories if categories else None,
        "twitter_handle": twitter_handle,
        "twitter_url": twitter_url,
        "telegram_channel": telegram_channel,
        "_source": "coingecko",
        "_fetched_at": datetime.utcnow().isoformat() + "Z"
    }

    # Remove None values
    result = {k: v for k, v in result.items() if v is not None}

    # Log what we found
    found_fields = [k for k in ["whitepaper_url", "project_description", "categories", "twitter_handle"]
                    if result.get(k)]
    logger.info(f"CoinGecko: Processed {token_symbol} (coingecko_id: {coin_data.get('id')}, "
                f"contract: {contract_address or 'N/A'}, extra fields: {found_fields or 'none'})")
    return result


# ============================================================================
# COINGECKO FETCHER (Free, no auth)
# ============================================================================

@rate_limited(COINGECKO_RATE_LIMIT_DELAY)
def fetch_coingecko(token: str, token_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Fetch token data from CoinGecko API.

    Session 79I: Enhanced to extract additional IMPORTANT fields:
    - whitepaper_url (from links.whitepaper)
    - project_description (from description.en)
    - categories (from categories[])
    - website, twitter_handle, telegram_channel (from links)

    Session 84: Added token_name parameter for better identity verification.
    Session 339 (L093B): Added COINGECKO_SYMBOL_ALIASES for mismatched symbols.

    CoinGecko provides (FREE):
    - contract_address (via platforms field)
    - fdv (fully_diluted_valuation)
    - market_cap
    - current_price
    - whitepaper_url, description, categories, social links

    Args:
        token: Token symbol (e.g., "SEEK")
        token_name: Optional token name for disambiguation (e.g., "TALISMAN")

    No API key required for basic endpoints.
    Rate limited to 1 call/second to avoid 429s.
    """
    try:
        # Session 339 (L093B): Check symbol aliases FIRST
        # This handles tokens where DACLE uses a different symbol than CoinGecko
        token_upper = token.upper()
        if token_upper in COINGECKO_SYMBOL_ALIASES:
            cg_symbol, coin_id, aliased_name = COINGECKO_SYMBOL_ALIASES[token_upper]
            logger.info(f"CoinGecko: Symbol alias {token_upper} → {cg_symbol} ({coin_id})")
            # Skip search - directly fetch the known coin_id
            time.sleep(COINGECKO_RATE_LIMIT_DELAY)
            coin_url = f"https://api.coingecko.com/api/v3/coins/{coin_id}"
            params = {
                "localization": "false",
                "tickers": "false",
                "market_data": "true",
                "community_data": "true",
                "developer_data": "true"
            }
            try:
                response = fetch_with_retry(coin_url, {"accept": "application/json"}, params=params, timeout=15)
            except RetryableError as e:
                logger.warning(f"CoinGecko alias fetch failed: {e}")
                return None
            if response.status_code == 200:
                data = response.json()
                # Process and return data (reuse logic from below)
                return _process_coingecko_data(data, token_upper)
            else:
                logger.warning(f"CoinGecko alias fetch failed: {response.status_code}")
                return None

        # Search for the token first (use name if provided for better results)
        search_query = token_name if token_name else token
        search_url = f"https://api.coingecko.com/api/v3/search?query={search_query}"
        headers = {"accept": "application/json"}

        # Session 148: Use retry logic
        try:
            response = fetch_with_retry(search_url, headers, timeout=10)
        except RetryableError as e:
            logger.warning(f"CoinGecko search failed after retries: {e}")
            return None

        if response.status_code != 200:
            logger.warning(f"CoinGecko search failed: {response.status_code}")
            return None

        search_data = response.json()
        coins = search_data.get("coins", [])

        # Find matching coin - prefer exact symbol match with name verification
        coin_id = None
        matched_name = None
        for coin in coins:
            symbol_match = coin.get("symbol", "").upper() == token.upper()
            # If we have token_name, verify it matches too (CRITICAL for disambiguation)
            if token_name:
                name_similarity = token_name.lower() in coin.get("name", "").lower()
                if symbol_match and name_similarity:
                    coin_id = coin.get("id")
                    matched_name = coin.get("name")
                    logger.info(f"CoinGecko: Matched {coin.get('name')} ({coin.get('symbol')}) via name+symbol")
                    break
            elif symbol_match:
                coin_id = coin.get("id")
                matched_name = coin.get("name")
                logger.info(f"CoinGecko: Matched {coin.get('name')} ({coin.get('symbol')}) via symbol")
                break

        # CRITICAL FIX: If token_name was provided but no name match found, DON'T use fallback
        # This prevents wrong token (e.g., ORBITX instead of RateX for RTX symbol)
        if not coin_id and coins:
            if token_name:
                # User explicitly provided a name - don't return wrong token
                logger.warning(
                    f"CoinGecko: Symbol {token} found but name '{token_name}' not matched. "
                    f"Available: {[c.get('name') for c in coins[:3]]}. REJECTING to avoid wrong token."
                )
                return None
            else:
                # No name provided - use first result with warning
                coin_id = coins[0].get("id")
                matched_name = coins[0].get("name")
                logger.warning(f"CoinGecko: Using first result: {coins[0].get('name')} - may not be exact match")

        if not coin_id:
            logger.info(f"CoinGecko: Token {token} not found")
            return None

        # Rate limit before second call
        time.sleep(COINGECKO_RATE_LIMIT_DELAY)

        # Get detailed coin data
        # Session 79I: Enable community_data and developer_data for whitepaper, links
        coin_url = f"https://api.coingecko.com/api/v3/coins/{coin_id}"
        params = {
            "localization": "false",
            "tickers": "false",
            "market_data": "true",
            "community_data": "true",   # Session 79I: Enable for social links
            "developer_data": "true"    # Session 79I: Enable for whitepaper, github
        }

        # Session 148: Use retry logic
        try:
            response = fetch_with_retry(coin_url, headers, timeout=15, params=params)
        except RetryableError as e:
            logger.warning(f"CoinGecko coin data failed after retries: {e}")
            return None

        if response.status_code != 200:
            logger.warning(f"CoinGecko coin data failed: {response.status_code}")
            return None

        # Session 339 (L093B): Use helper function to process data (avoids duplication)
        coin_data = response.json()
        return _process_coingecko_data(coin_data, token)

    except Exception as e:
        logger.error(f"CoinGecko fetch error: {e}")
        return None


# ============================================================================
# DROPSTAB FETCHER (Web scraping)
# ============================================================================

def fetch_dropstab(token: str) -> Optional[Dict[str, Any]]:
    """
    Fetch token data from Dropstab via web scraping.

    Uses local scraping which works reliably on VPS and local development.

    Args:
        token: Token symbol
    """
    try:
        from src.data.fetchers.dropstab_fetcher import fetch_dropstab_data

        data = fetch_dropstab_data(token)
        if data:
            data["_source"] = "dropstab"
            data["_fetched_at"] = datetime.utcnow().isoformat() + "Z"
            logger.info(f"Dropstab (local): Found {token}")
            return data

        logger.info(f"Dropstab (local): Token {token} not found")
        return None

    except ImportError:
        logger.warning("Dropstab fetcher not available")
        return None
    except Exception as e:
        logger.error(f"Dropstab fetch error: {e}")
        return None


def fetch_icodrops(token_symbol: str, token_name: str = None) -> Optional[Dict[str, Any]]:
    """
    Fetch token data from ICODrops.com (Session 85 - Phase 2)

    ICODrops provides high-quality structured data for:
    - Vesting schedules (often cleaner than Dropstab)
    - Whitepaper URLs
    - Farming/Launchpad platforms
    - Token allocation

    Args:
        token_symbol: Token symbol (e.g., "SEEK")
        token_name: Token name (e.g., "Talisman") - helps with URL matching

    Returns:
        Dict with token data or None if not found

    Example:
        data = fetch_icodrops("SEEK", "Talisman")
        if data:
            print(f"Whitepaper: {data['whitepaper_url']}")
    """
    try:
        from src.data.fetchers.icodrops_fetcher import fetch_icodrops_data

        logger.info(f"Fetching ICODrops data for {token_symbol}...")
        result = fetch_icodrops_data(token_symbol, token_name)

        if result and result.get("_data_confidence", 0) > 0:
            # Add metadata
            result["_fetched_at"] = datetime.utcnow().isoformat() + "Z"
            logger.info(f"ICODrops: Found {token_symbol}")
            return result
        else:
            logger.info(f"ICODrops: No data found for {token_symbol}")
            return None

    except ImportError:
        logger.warning("ICODrops fetcher not available")
        return None
    except Exception as e:
        logger.error(f"Failed to fetch from ICODrops: {e}")
        return None


# ============================================================================
# CRYPTORANK FETCHER (API with key)
# Session 79I: Enhanced to extract TGE-specific data:
#   - ICO/TGE endpoint for listing dates, prices, exchanges
#   - Funding rounds and investors
#   - Tokenomics (allocation, vesting)
# ============================================================================

CRYPTORANK_API_V1 = "https://api.cryptorank.io/v1"
CRYPTORANK_API_V2 = "https://api.cryptorank.io/v2"


def fetch_cryptorank(token: str, token_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Fetch comprehensive token data from CryptoRank API.

    Session 79I: Enhanced to extract CRITICAL TGE fields:
    - tge_date, listing_price, listing_exchanges (from /ico endpoint)
    - investors, funding_raised_usd (from /currencies with funding data)
    - token_allocation, vesting_schedule (from tokenomics)

    Args:
        token: Token symbol (e.g., "RTX")
        token_name: Optional token name for ICO search (e.g., "RateX")

    Requires CRYPTORANK_API_KEY environment variable.

    API Endpoints Used:
    1. /v1/currencies - Basic token info, find slug
    2. /v1/currencies/{slug} - Detailed data with funding/tokenomics
    3. /v1/ico - TGE-specific data (dates, exchanges, prices)
    """
    api_key = os.getenv("CRYPTORANK_API_KEY")
    if not api_key:
        logger.warning("CRYPTORANK_API_KEY not set, skipping CryptoRank fetch")
        return None

    try:
        result = {
            "token_symbol": token.upper(),
            "_source": "cryptorank",
            "_fetched_at": datetime.utcnow().isoformat() + "Z"
        }

        # Step 1: Find token slug/key via search
        slug = _cryptorank_find_slug(token, api_key)
        if not slug:
            # Try ICO endpoint directly as fallback (with name search)
            ico_data = _cryptorank_fetch_ico(token, api_key, token_name=token_name)
            if ico_data:
                result.update(ico_data)
                logger.info(f"CryptoRank: Found {token} via ICO endpoint")
                return {k: v for k, v in result.items() if v is not None}
            return None

        # Step 2: Get detailed currency data (includes funding, tokenomics)
        currency_data = _cryptorank_fetch_currency_details(slug, api_key)
        if currency_data:
            result.update(currency_data)

        # Step 3: Get ICO/TGE specific data (dates, exchanges, prices)
        ico_data = _cryptorank_fetch_ico(token, api_key, token_name=token_name)
        if ico_data:
            # ICO data takes precedence for TGE-specific fields
            for key in ["tge_date", "listing_price_low", "listing_price_high",
                        "listing_exchanges", "fdv_low", "fdv_high"]:
                if ico_data.get(key) and not result.get(key):
                    result[key] = ico_data[key]
            # Also merge other ICO fields
            for key, value in ico_data.items():
                if not key.startswith("_") and not result.get(key) and value:
                    result[key] = value

        # Remove None values
        result = {k: v for k, v in result.items() if v is not None}

        # Log what we found
        critical_found = [k for k in ["tge_date", "listing_exchanges", "investors",
                                       "funding_raised_usd", "token_allocation"]
                         if result.get(k)]
        logger.info(f"CryptoRank: Found {token} (slug: {slug}, "
                    f"critical fields: {critical_found or 'none'})")
        return result

    except Exception as e:
        logger.error(f"CryptoRank fetch error: {e}")
        return None


def _cryptorank_find_slug(token: str, api_key: str) -> Optional[str]:
    """Find CryptoRank currency slug by symbol."""
    try:
        url = f"{CRYPTORANK_API_V1}/currencies"
        params = {
            "api_key": api_key,
            "symbols": token.upper(),
            "limit": 5
        }

        # Session 148: Use retry logic
        try:
            response = fetch_with_retry(url, headers={}, timeout=15, params=params)
        except RetryableError as e:
            logger.debug(f"CryptoRank search failed after retries: {e}")
            return None

        if response.status_code != 200:
            logger.debug(f"CryptoRank search failed: {response.status_code}")
            return None

        data = response.json()
        currencies = data.get("data", [])

        if not currencies:
            return None

        # Find exact symbol match
        for currency in currencies:
            if currency.get("symbol", "").upper() == token.upper():
                return currency.get("key") or currency.get("slug")

        # Fallback to first result
        return currencies[0].get("key") or currencies[0].get("slug")

    except Exception as e:
        logger.debug(f"CryptoRank slug lookup failed: {e}")
        return None


def _cryptorank_fetch_currency_details(slug: str, api_key: str) -> Optional[Dict[str, Any]]:
    """
    Fetch detailed currency data including funding and tokenomics.

    Extracts:
    - Basic: name, symbol, fdv, market_cap, total_supply
    - Funding: investors, funding_raised_usd, funding_rounds
    - Tokenomics: token_allocation, vesting_schedule, float_percent
    """
    try:
        url = f"{CRYPTORANK_API_V1}/currencies/{slug}"
        params = {"api_key": api_key}

        # Session 148: Use retry logic
        try:
            response = fetch_with_retry(url, headers={}, timeout=15, params=params)
        except RetryableError as e:
            logger.debug(f"CryptoRank currency details failed after retries: {e}")
            return None

        if response.status_code != 200:
            logger.debug(f"CryptoRank currency details failed: {response.status_code}")
            return None

        data = response.json().get("data", {})

        result = {
            "name": data.get("name"),
            "token_symbol": data.get("symbol", "").upper(),
            "fdv": data.get("fullyDilutedValuation"),
            "market_cap": data.get("marketCap"),
            "circulating_supply": data.get("circulatingSupply"),
            "total_supply": data.get("totalSupply"),
            "current_price": data.get("price", {}).get("USD") if isinstance(data.get("price"), dict) else data.get("price"),
        }

        # Extract funding data
        fundraising = data.get("fundraising", []) or data.get("ico", {}).get("rounds", [])
        if fundraising:
            investors = set()
            lead_investors = []
            total_raised = 0.0
            funding_rounds = []

            for round_data in fundraising:
                round_type = round_data.get("name") or round_data.get("type", "Unknown")
                amount = round_data.get("raisedAmount") or round_data.get("amount") or 0

                # Extract investors
                round_investors = []
                for inv in round_data.get("investors", []):
                    inv_name = inv.get("name") if isinstance(inv, dict) else str(inv)
                    if inv_name:
                        round_investors.append(inv_name)
                        investors.add(inv_name)
                        # Check if lead investor
                        if isinstance(inv, dict) and (inv.get("isLead") or inv.get("lead")):
                            lead_investors.append(inv_name)

                if amount:
                    total_raised += float(amount)

                funding_rounds.append({
                    "round_type": round_type,
                    "amount_usd": amount,
                    "valuation": round_data.get("valuation"),
                    "date": round_data.get("date") or round_data.get("endDate"),
                    "investors": round_investors
                })

            result["investors"] = list(investors)
            result["lead_investors"] = lead_investors
            result["funding_raised_usd"] = total_raised if total_raised > 0 else None
            result["funding_rounds"] = funding_rounds

        # Extract tokenomics
        tokenomics = data.get("tokenomics") or data.get("tokenDistribution", {})
        if tokenomics:
            # Token allocation
            allocation = tokenomics.get("allocation", {}) or tokenomics.get("distribution", [])
            if allocation:
                result["token_allocation"] = _normalize_cryptorank_allocation(allocation)

            # Vesting schedule
            vesting = tokenomics.get("vesting") or tokenomics.get("vestingSchedule")
            if vesting:
                result["vesting_schedule"] = vesting

            # TGE unlock percentage
            tge_unlock = tokenomics.get("tgeUnlock") or tokenomics.get("tgeUnlockPercent")
            if tge_unlock:
                result["tge_unlock_pct"] = tge_unlock

            # Float percentage at TGE
            initial_circ = tokenomics.get("initialCirculatingSupply")
            total = data.get("totalSupply")
            if initial_circ and total and float(total) > 0:
                result["float_percent"] = round((float(initial_circ) / float(total)) * 100, 2)
                result["circulating_supply_at_tge"] = initial_circ

        # Category
        category = data.get("category") or data.get("type")
        if category:
            result["category"] = category.get("name") if isinstance(category, dict) else category

        # Blockchain
        platforms = data.get("platforms", []) or data.get("tokens", [])
        if platforms:
            platform = platforms[0] if isinstance(platforms, list) else platforms
            result["blockchain"] = platform.get("name") or platform.get("platform")
            # Also extract contract address
            addr = platform.get("address") or platform.get("tokenAddress")
            if addr and addr.startswith("0x") and len(addr) == 42:
                result["contract_address"] = addr

        # Session 85: Extract whitepaper URL
        whitepaper = (data.get("whitepaper") or
                     data.get("whitepaperUrl") or
                     data.get("links", {}).get("whitepaper"))
        if whitepaper:
            result["whitepaper_url"] = whitepaper

        return result

    except Exception as e:
        logger.debug(f"CryptoRank currency details error: {e}")
        return None


def _cryptorank_fetch_ico(token: str, api_key: str, token_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Fetch ICO/TGE specific data.

    Extracts:
    - tge_date: Launch/listing date
    - listing_exchanges: Where token will be listed
    - listing_price_low/high: Initial price estimates
    - fdv_low/high: Initial FDV estimates
    """
    try:
        url = f"{CRYPTORANK_API_V1}/ico"
        icos = []

        # Try 1: Search by symbol
        params = {
            "api_key": api_key,
            "symbols": token.upper(),
            "limit": 10
        }

        try:
            response = fetch_with_retry(url, headers={}, timeout=15, params=params)
            if response.status_code == 200:
                data = response.json()
                icos = data.get("data", [])
        except RetryableError as e:
            logger.debug(f"CryptoRank ICO symbol search failed: {e}")

        # Try 2: Search by name if symbol search failed and name provided
        if not icos and token_name:
            params_name = {
                "api_key": api_key,
                "search": token_name,
                "limit": 10
            }
            try:
                response = fetch_with_retry(url, headers={}, timeout=15, params=params_name)
                if response.status_code == 200:
                    data = response.json()
                    icos = data.get("data", [])
                    logger.info(f"CryptoRank ICO found via name search: {token_name}")
            except RetryableError as e:
                logger.debug(f"CryptoRank ICO name search failed: {e}")

        # Try 3: Search by slug (lowercase token name)
        if not icos and token_name:
            slug = token_name.lower().replace(" ", "-")
            params_slug = {
                "api_key": api_key,
                "keys": slug,
                "limit": 10
            }
            try:
                response = fetch_with_retry(url, headers={}, timeout=15, params=params_slug)
                if response.status_code == 200:
                    data = response.json()
                    icos = data.get("data", [])
                    if icos:
                        logger.info(f"CryptoRank ICO found via slug: {slug}")
            except RetryableError as e:
                logger.debug(f"CryptoRank ICO slug search failed: {e}")

        if not icos:
            return None

        # Find exact match by symbol first, then by name
        ico_data = None
        for ico in icos:
            if ico.get("symbol", "").upper() == token.upper():
                ico_data = ico
                break

        if not ico_data and token_name:
            for ico in icos:
                if token_name.lower() in ico.get("name", "").lower():
                    ico_data = ico
                    break

        if not ico_data:
            ico_data = icos[0]

        result = {
            "name": ico_data.get("name"),
            "token_symbol": ico_data.get("symbol", "").upper(),
        }

        # TGE/Listing date
        tge_date = (ico_data.get("startDate") or ico_data.get("listingDate") or
                    ico_data.get("releaseDate") or ico_data.get("when"))
        if tge_date:
            result["tge_date"] = tge_date

        # Listing price
        price = ico_data.get("price") or ico_data.get("salePrice")
        if price:
            result["listing_price_low"] = price
            result["listing_price_high"] = price  # Same if only one price given

        # FDV
        fdv = ico_data.get("fdv") or ico_data.get("fullyDilutedValuation")
        if fdv:
            result["fdv_low"] = fdv
            result["fdv_high"] = fdv

        # Initial market cap
        init_cap = ico_data.get("initialCap") or ico_data.get("initialMarketCap")
        if init_cap:
            result["initial_market_cap_low"] = init_cap
            result["initial_market_cap_high"] = init_cap

        # Listing exchanges (from launchpads or platforms)
        launchpads = ico_data.get("launchpads", []) or ico_data.get("platforms", [])
        if launchpads:
            exchanges = []
            for lp in launchpads:
                name = lp.get("name") if isinstance(lp, dict) else str(lp)
                if name:
                    exchanges.append(name)
            if exchanges:
                result["listing_exchanges"] = exchanges

        # Raise amount
        raise_amount = ico_data.get("raise") or ico_data.get("raiseAmount")
        if raise_amount:
            result["funding_raised_usd"] = raise_amount

        # Session 85: Extract farming/launchpad sources
        farming_platforms = (ico_data.get("farmingPlatforms", []) or
                           ico_data.get("rewardPrograms", []) or
                           ico_data.get("stakingPlatforms", []))
        if farming_platforms:
            farming_sources = []
            for fp in farming_platforms:
                platform_name = fp.get("name") if isinstance(fp, dict) else str(fp)
                if platform_name:
                    farming_sources.append(platform_name)
            if farming_sources:
                result["farming_sources"] = farming_sources

        return result

    except Exception as e:
        logger.debug(f"CryptoRank ICO fetch error: {e}")
        return None


def _normalize_cryptorank_allocation(allocation: Any) -> Dict[str, float]:
    """
    Normalize CryptoRank token allocation to standard format.

    Handles:
    - List format: [{"name": "Team", "percent": 20}]
    - Dict format: {"team": 20, "investors": 30}
    """
    normalized = {}

    if isinstance(allocation, list):
        for item in allocation:
            if isinstance(item, dict):
                name = (item.get("name") or item.get("category") or
                        item.get("type") or item.get("allocation_type"))
                pct = (item.get("percent") or item.get("percentage") or
                       item.get("value") or item.get("share"))
                if name and pct is not None:
                    try:
                        pct = float(pct)
                        if pct > 0 and pct < 1:  # Decimal format
                            pct = pct * 100
                        normalized[name.title()] = round(pct, 2)
                    except (ValueError, TypeError):
                        continue

    elif isinstance(allocation, dict):
        for key, value in allocation.items():
            if key.startswith("_"):
                continue
            try:
                pct = float(value)
                if pct > 0 and pct < 1:
                    pct = pct * 100
                normalized[key.title()] = round(pct, 2)
            except (ValueError, TypeError):
                continue

    return normalized if normalized else None


# ============================================================================
# COINMARKETCAP FETCHER (API - contract_address only)
# ============================================================================

def fetch_coinmarketcap(token: str) -> Optional[Dict[str, Any]]:
    """
    Fetch contract_address from CoinMarketCap API.

    User Decision (Session 79H): Use CMC API ONLY for contract_address lookups.
    Rationale: 333 calls/day limit, contract_address is hardest to find elsewhere.

    Requires COINMARKETCAP_API_KEY environment variable.
    """
    api_key = os.getenv("COINMARKETCAP_API_KEY")
    if not api_key:
        logger.warning("COINMARKETCAP_API_KEY not set, skipping CMC fetch")
        return None

    try:
        # Get token info including platform/contract
        url = "https://pro-api.coinmarketcap.com/v2/cryptocurrency/info"
        headers = {
            "X-CMC_PRO_API_KEY": api_key,
            "Accept": "application/json"
        }
        params = {"symbol": token.upper()}

        # Session 148: Use retry logic
        try:
            response = fetch_with_retry(url, headers, timeout=15, params=params)
        except RetryableError as e:
            logger.warning(f"CMC API failed after retries: {e}")
            return None

        if response.status_code != 200:
            logger.warning(f"CMC API failed: {response.status_code}")
            return None

        data = response.json()
        tokens = data.get("data", {}).get(token.upper(), [])

        if not tokens:
            logger.info(f"CMC: Token {token} not found")
            return None

        # Handle both list and dict response formats
        token_info = tokens[0] if isinstance(tokens, list) else tokens

        # Extract contract address from platform info
        contract_address = None
        platform = token_info.get("platform")
        if platform and platform.get("token_address"):
            addr = platform.get("token_address")
            if addr.startswith("0x") and len(addr) == 42:
                contract_address = addr

        if not contract_address:
            # Check contract_address field directly
            contracts = token_info.get("contract_address", [])
            for contract in contracts:
                addr = contract.get("contract_address", "")
                if addr.startswith("0x") and len(addr) == 42:
                    contract_address = addr
                    break

        if not contract_address:
            logger.info(f"CMC: No contract_address for {token}")
            return None

        result = {
            "token_symbol": token.upper(),
            "name": token_info.get("name"),
            "contract_address": contract_address,
            "_source": "coinmarketcap",
            "_fetched_at": datetime.utcnow().isoformat() + "Z"
        }

        logger.info(f"CMC: Found {token} contract: {contract_address}")
        return result

    except Exception as e:
        logger.error(f"CMC fetch error: {e}")
        return None


def fetch_coinmarketcap_full(token: str, token_name: Optional[str] = None, cmc_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """
    Fetch FULL token data from CoinMarketCap API.

    Use this for tokens not found on CoinGecko/CryptoRank (e.g., RTX/RateX).
    Gets: price, market cap, supply, category, contract address, etc.

    Args:
        token: Token symbol (e.g., "TRUTH")
        token_name: Optional name for disambiguation when multiple symbols match
        cmc_id: Optional CMC ID for direct lookup (from identity lock)

    Note: Uses more API credits than fetch_coinmarketcap() - use sparingly.
    """
    api_key = os.getenv("COINMARKETCAP_API_KEY")
    if not api_key:
        logger.warning("COINMARKETCAP_API_KEY not set, skipping CMC full fetch")
        return None

    try:
        headers = {
            "X-CMC_PRO_API_KEY": api_key,
            "Accept": "application/json"
        }

        # Step 1: Get token info (name, category, contract, description)
        info_url = "https://pro-api.coinmarketcap.com/v2/cryptocurrency/info"
        # Direct ID lookup if available (avoids ambiguous symbol matching)
        if cmc_id:
            info_params = {"id": str(cmc_id)}
        else:
            info_params = {"symbol": token.upper()}

        try:
            info_response = fetch_with_retry(info_url, headers, timeout=15, params=info_params)
        except RetryableError as e:
            logger.warning(f"CMC info API failed after retries: {e}")
            return None

        if info_response.status_code != 200:
            logger.warning(f"CMC info API failed: {info_response.status_code}")
            return None

        info_data = info_response.json()

        # Response keyed by ID when using id param, by symbol when using symbol param
        if cmc_id:
            token_info = info_data.get("data", {}).get(str(cmc_id))
            if not token_info:
                logger.info(f"CMC Full: Token ID {cmc_id} not found")
                return None
        else:
            tokens = info_data.get("data", {}).get(token.upper(), [])

            if not tokens:
                logger.info(f"CMC Full: Token {token} not found")
                return None

            # Handle both list and dict response formats
            # If multiple tokens match, prefer active + highest rank, then try name match
            token_info = None
            if isinstance(tokens, list):
                # First: prefer active tokens with a rank
                active_ranked = [t for t in tokens if t.get("is_active") == 1 or t.get("status") == 1]
                if token_name and active_ranked:
                    for t in active_ranked:
                        if token_name.lower() in t.get("name", "").lower():
                            token_info = t
                            break
                if not token_info and active_ranked:
                    token_info = active_ranked[0]
                if not token_info and token_name:
                    for t in tokens:
                        if token_name.lower() in t.get("name", "").lower():
                            token_info = t
                            break
                if not token_info:
                    token_info = tokens[0]
            else:
                token_info = tokens

        cmc_id = token_info.get("id")

        # Extract contract address
        contract_address = None
        blockchain = None
        platform = token_info.get("platform")
        if platform:
            if platform.get("token_address"):
                addr = platform.get("token_address")
                if addr.startswith("0x") and len(addr) == 42:
                    contract_address = addr
            blockchain = platform.get("name")

        if not contract_address:
            contracts = token_info.get("contract_address", [])
            for contract in contracts:
                addr = contract.get("contract_address", "")
                if addr.startswith("0x") and len(addr) == 42:
                    contract_address = addr
                    blockchain = contract.get("platform", {}).get("name")
                    break

        # Extract category from tags
        category = None
        tags = token_info.get("tags", [])
        if tags:
            tag_to_category = {
                "defi": "DeFi", "decentralized-finance": "DeFi",
                "layer-1": "L1", "layer-2": "L2",
                "gaming": "Gaming", "play-to-earn": "Gaming",
                "ai-big-data": "AI", "artificial-intelligence": "AI",
                "memes": "Meme", "meme": "Meme",
                "nft": "NFT", "collectibles-nfts": "NFT",
                "dex": "DEX", "decentralized-exchange": "DEX",
                "derivatives": "Perp", "perpetuals": "Perp",
                "infrastructure": "Infrastructure",
            }
            for tag in tags:
                tag_lower = tag.lower() if isinstance(tag, str) else ""
                if tag_lower in tag_to_category:
                    category = tag_to_category[tag_lower]
                    break
            # Also try category field directly
            if not category and token_info.get("category"):
                category = token_info.get("category")

        # Step 2: Get quotes (price, market cap, supply)
        quotes_url = "https://pro-api.coinmarketcap.com/v2/cryptocurrency/quotes/latest"
        quotes_params = {"id": cmc_id, "convert": "USD"}

        current_price = None
        market_cap = None
        fdv = None
        circulating_supply = None
        total_supply = None
        max_supply = None
        volume_24h = None
        percent_change_24h = None
        cmc_rank = None

        try:
            quotes_response = fetch_with_retry(quotes_url, headers, timeout=15, params=quotes_params)
            if quotes_response.status_code == 200:
                quotes_data = quotes_response.json()
                quote_info = quotes_data.get("data", {}).get(str(cmc_id), {})
                quote = quote_info.get("quote", {}).get("USD", {})

                current_price = quote.get("price")
                market_cap = quote.get("market_cap")
                fdv = quote.get("fully_diluted_market_cap")
                volume_24h = quote.get("volume_24h")
                percent_change_24h = quote.get("percent_change_24h")

                circulating_supply = quote_info.get("circulating_supply")
                total_supply = quote_info.get("total_supply")
                max_supply = quote_info.get("max_supply")
                cmc_rank = quote_info.get("cmc_rank")
        except Exception as e:
            logger.warning(f"CMC quotes fetch failed: {e}")

        # Calculate float percent
        float_percent = None
        if circulating_supply and total_supply and total_supply > 0:
            float_percent = round((circulating_supply / total_supply) * 100, 2)

        result = {
            "token_symbol": token.upper(),
            "name": token_info.get("name"),
            "description": token_info.get("description"),
            "category": category,
            "blockchain": blockchain,
            "contract_address": contract_address,
            "website": token_info.get("urls", {}).get("website", [None])[0] if token_info.get("urls") else None,
            "twitter_url": token_info.get("urls", {}).get("twitter", [None])[0] if token_info.get("urls") else None,
            "current_price": current_price,
            "market_cap": market_cap,
            "fdv": fdv,
            "circulating_supply": circulating_supply,
            "total_supply": total_supply,
            "max_supply": max_supply,
            "float_percent": float_percent,
            "volume_24h": volume_24h,
            "percent_change_24h": percent_change_24h,
            "cmc_rank": cmc_rank,
            "cmc_id": cmc_id,
            "cmc_slug": token_info.get("slug"),
            "logo": token_info.get("logo"),
            "tags": tags,
            "_source": "coinmarketcap_full",
            "_fetched_at": datetime.utcnow().isoformat() + "Z"
        }

        # Remove None values
        result = {k: v for k, v in result.items() if v is not None}

        logger.info(f"CMC Full: Found {token} - {result.get('name')} @ ${current_price:.4f}" if current_price else f"CMC Full: Found {token} - {result.get('name')}")
        return result

    except Exception as e:
        logger.error(f"CMC full fetch error: {e}")
        return None


# ============================================================================
# OTC DATA FETCHER (Session 88: Whales Market + Hyperliquid)
# ============================================================================

def fetch_otc_data(token: str) -> Optional[Dict[str, Any]]:
    """
    Fetch OTC/pre-launch data for a specific token (Session 88).

    Checks both Whales Market and Hyperliquid for pre-market pricing.
    This data is critical for:
    - Detecting "fading interest" patterns (volume decline → dump)
    - OTC premium analysis (high premium → overvalued)
    - Pre-TGE sentiment tracking

    Learning: MET case - OTC volume declined 62.5% pre-TGE, token dumped 60%.

    Args:
        token: Token symbol (e.g., "MONAD", "STABLE")

    Returns:
        Dict with OTC data or None if not available
    """
    try:
        # Try Hyperliquid first (API - faster, free)
        from src.integrations.hyperliquid.otc_api import HyperliquidOTCAPI

        api = HyperliquidOTCAPI()
        hyperps = api.get_all_hyperps()

        # Find matching token
        token_upper = token.upper()
        for hyperp in hyperps:
            symbol = hyperp.get("project_symbol", "").upper()
            if symbol == token_upper or token_upper in symbol:
                logger.info(f"Found {token} on Hyperliquid: ${hyperp.get('price', 'N/A')}")
                return {
                    "token_symbol": token_upper,
                    "otc_available": True,
                    "otc_price": hyperp.get("price"),
                    "otc_volume": hyperp.get("volume_24h"),
                    "otc_premium": hyperp.get("premium"),
                    "otc_platforms": ["hyperliquid"],
                    "_source": "hyperliquid",
                    "_fetched_at": datetime.utcnow().isoformat() + "Z"
                }

    except ImportError:
        logger.debug("Hyperliquid API not available")
    except Exception as e:
        logger.warning(f"Hyperliquid fetch error: {e}")

    # Try Whales Market (requires Playwright - slower)
    try:
        from src.integrations.whalesmarket.scanner import WhalesMarketScanner
        from src.knowledge.supabase_client import SupabaseKnowledgeBase

        kb = SupabaseKnowledgeBase()
        scanner = WhalesMarketScanner(knowledge_base=kb)

        # Scan for specific token
        tokens = scanner.scan_premarket_tokens(max_results=50, use_browser=True)

        token_upper = token.upper()
        for t in tokens:
            symbol = t.get("project_symbol", "").upper()
            if symbol == token_upper or token_upper in symbol:
                logger.info(f"Found {token} on Whales Market: ${t.get('floor_price', 'N/A')}")
                return {
                    "token_symbol": token_upper,
                    "otc_available": True,
                    "otc_price": t.get("floor_price"),
                    "otc_volume": t.get("volume"),
                    "otc_premium": t.get("premium"),
                    "otc_platforms": ["whales_market"],
                    "_source": "whales_market",
                    "_fetched_at": datetime.utcnow().isoformat() + "Z"
                }

    except ImportError:
        logger.debug("Whales Market scanner not available")
    except Exception as e:
        logger.warning(f"Whales Market fetch error: {e}")

    # Not found on either platform
    logger.info(f"No OTC data found for {token} on Hyperliquid or Whales Market")
    return {
        "token_symbol": token.upper(),
        "otc_available": False,
        "otc_platforms": [],
        "_source": "none",
        "_fetched_at": datetime.utcnow().isoformat() + "Z"
    }


# ============================================================================
# MAIN ORCHESTRATOR
# ============================================================================

def save_to_sources(token: str, filename: str, data: Dict) -> Path:
    """Save fetched data to token's sources/raw/ directory."""
    token_dir = PROJECT_ROOT / "data" / "tokens" / token.upper()
    raw_dir = token_dir / "sources" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    output_file = raw_dir / filename
    with open(output_file, "w") as f:
        json.dump(data, f, indent=2)

    logger.info(f"Saved: {output_file}")
    return output_file


# ============================================================================
# SESSION 275: PARALLEL FETCHING OPTIMIZATION
# SESSION 340 Part 4: Added GlobalRateLimiter integration (Gemini recommendation)
# ============================================================================
# Reduces token addition time from 15-20s to 4-5s by fetching sources in parallel.
#
# Phase 1 (parallel): CryptoRank, Dropstab, ICODrops, CoinGecko run concurrently
# Phase 2 (sequential): CryptoRank Web fallback (only if API failed)
# Phase 3 (conditional): CMC (only if contract_address missing or no major source)
# Phase 4 (conditional): OTC data (only if needed)
#
# Session 340 Part 4 Enhancement (Gemini):
# - Even with 4 threads, we never exceed X requests/sec across the entire process
# - CryptoRank/DropsTab use Cloudflare protection with IP-based rate limits
# - Without limiter: 4 threads × instant requests = rate limit hit (429 errors)
# - With limiter: Controlled request pacing, no 429 errors
# ============================================================================

def _fetch_source_wrapper(
    source_name: str,
    fetch_func: Callable,
    token: str,
    token_name: Optional[str] = None,
    **kwargs
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """
    Wrapper for parallel source fetching with error handling.

    Session 340 Part 4: Added GlobalRateLimiter integration to prevent
    hitting API rate limits during parallel fetches. Each source acquires
    permission from the rate limiter before making the request.

    Returns:
        Tuple of (source_name, data or None)
    """
    try:
        # Session 340 Part 4: Acquire rate limit before fetching
        # This ensures we don't exceed API rate limits even with parallel threads
        GLOBAL_RATE_LIMITER.acquire(source_name)

        # Session 397: Implement retry with normalized symbol if original fails
        normalized_symbol = normalize_token_symbol(token)
        retried = False
        
        if source_name in ["cryptorank", "coingecko", "coinmarketcap_full", "icodrops"]:
            data = fetch_func(token, token_name=token_name)
            # If original fails and we have a different normalized symbol, retry
            if not data and normalized_symbol != token:
                logger.info(f"Retrying {source_name} with normalized symbol: {normalized_symbol}")
                data = fetch_func(normalized_symbol, token_name=token_name)
                retried = True
        else:
            data = fetch_func(token)
            if not data and normalized_symbol != token:
                logger.info(f"Retrying {source_name} with normalized symbol: {normalized_symbol}")
                data = fetch_func(normalized_symbol)
                retried = True
                
        if data:
            # Session 397: Ensure original token_symbol is preserved in merged results
            data["token_symbol"] = token.upper()
            
            # Session 397: Scale price fields if retried with normalized symbol (e.g. 1000BONK)
            if retried:
                multiplier = get_symbol_multiplier(token)
                if multiplier > 1.0:
                    logger.info(f"📐 Scaling {source_name} price fields by {multiplier}x for {token}")
                    for price_field in ["current_price", "listing_price_low", "listing_price_high", "ath_price", "otc_price"]:
                        val = data.get(price_field)
                        if val and isinstance(val, (int, float)):
                            data[price_field] = val * multiplier
                            
        return (source_name, data)
    except Exception as e:
        logger.warning(f"{source_name} fetch failed: {e}")
        return (source_name, None)


def fetch_from_primary_sources(
    token: str,
    missing_fields: List[str],
    skip_sources: Optional[List[str]] = None,
    token_name: Optional[str] = None,
    parallel: bool = True
) -> Dict[str, Any]:
    """
    Attempt to fill missing fields from primary sources BEFORE calling AI APIs.

    Session 275: Now uses parallel fetching by default for 3-4x speedup.
    Session 434: PARALLEL_FETCH_ENABLED env var gate (set to "false" to disable).

    Args:
        token: Token symbol (e.g., "RTX")
        missing_fields: List of fields to fetch
        skip_sources: Sources to skip
        token_name: Optional token name for disambiguation (e.g., "RateX")
        parallel: Use parallel fetching (default: True). Set False for debugging.

    Priority Order (Session 88 Update):
    1. CryptoRank API (most comprehensive TGE data)
    1.5. CryptoRank Web Scrape (fallback when API fails)
    2. Dropstab web scrape (vesting, funding, investors)
    2.5. ICODrops web scrape (vesting, whitepaper, farming, tokenomics)
    3. CoinGecko API (free - FDV, contract_address)
    4. CoinMarketCap API (contract_address only - 333/day limit)
    5. OTC Data (Hyperliquid API + Whales Market Playwright)

    Returns:
        Dict with all fetched data merged together
    """
    skip_sources = skip_sources or []
    results = {}
    sources_tried = []
    sources_succeeded = []

    # Session 434: env var gate — set PARALLEL_FETCH_ENABLED=false to force sequential
    env_parallel = os.environ.get("PARALLEL_FETCH_ENABLED", "true").lower()
    if env_parallel == "false":
        parallel = False

    start_time = time.time()
    logger.info(f"Primary source fetch for {token}: missing {missing_fields} (parallel={parallel})")

    if parallel:
        # =====================================================================
        # PHASE 1: Parallel fetching of independent sources
        # =====================================================================
        parallel_tasks = []

        # Prepare tasks for parallel execution
        if "cryptorank" not in skip_sources and needs_source(missing_fields, CRYPTORANK_FIELDS):
            parallel_tasks.append(("cryptorank", fetch_cryptorank, "cryptorank.json"))
            sources_tried.append("cryptorank")

        if "dropstab" not in skip_sources and needs_source(missing_fields, DROPSTAB_FIELDS):
            parallel_tasks.append(("dropstab", fetch_dropstab, "dropstab.json"))
            sources_tried.append("dropstab")

        if "icodrops" not in skip_sources and needs_source(missing_fields, ICODROPS_FIELDS):
            def _fetch_icodrops(t, token_name=None):
                try:
                    from src.data.fetchers.icodrops_fetcher import fetch_icodrops_data
                    return fetch_icodrops_data(t, name=token_name)
                except ImportError:
                    logger.warning("icodrops_fetcher not available")
                    return None
            parallel_tasks.append(("icodrops", _fetch_icodrops, "icodrops.json"))
            sources_tried.append("icodrops")

        if "coingecko" not in skip_sources and needs_source(missing_fields, COINGECKO_FIELDS):
            parallel_tasks.append(("coingecko", fetch_coingecko, "coingecko.json"))
            sources_tried.append("coingecko")

        # Session 495: Always include CMC Full in parallel if key is present
        if "coinmarketcap" not in skip_sources and os.getenv("COINMARKETCAP_API_KEY"):
            parallel_tasks.append(("coinmarketcap_full", fetch_coinmarketcap_full, "coinmarketcap_full.json"))
            sources_tried.append("coinmarketcap_full")

        # Execute parallel fetches
        if parallel_tasks:
            # Use max 5 workers to accommodate CMC
            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = {}
                for source_name, fetch_func, filename in parallel_tasks:
                    future = executor.submit(
                        _fetch_source_wrapper,
                        source_name,
                        fetch_func,
                        token,
                        token_name
                    )
                    futures[future] = (source_name, filename)

                # Collect results as they complete
                for future in as_completed(futures):
                    source_name, filename = futures[future]
                    try:
                        _, data = future.result(timeout=30)
                        if data:
                            # Validate data confidence for icodrops
                            if source_name == "icodrops" and data.get("_data_confidence", 0) == 0:
                                continue
                            save_to_sources(token, filename, data)
                            results.update(data)
                            
                            # Session 495: Auto-calculate float_percent if missing
                            if not results.get("float_percent"):
                                circ = results.get("circulating_supply") or results.get("circulating_supply_at_tge")
                                total = results.get("total_supply")
                                if circ and total and total > 0:
                                    try:
                                        # Handle string numeric values
                                        c_val = float(circ) if isinstance(circ, (str, int, float)) else 0
                                        t_val = float(total) if isinstance(total, (str, int, float)) else 0
                                        if t_val > 0:
                                            results["float_percent"] = round((c_val / t_val) * 100, 2)
                                            logger.info(f"Auto-calculated float_percent: {results['float_percent']}%")
                                    except (ValueError, TypeError):
                                        pass

                            sources_succeeded.append(source_name)
                            if source_name == "icodrops":
                                logger.info(f"ICODrops: {data.get('_data_confidence', 0)}% confidence")
                    except Exception as e:
                        logger.warning(f"{source_name} parallel fetch error: {e}")

        # =====================================================================
        # PHASE 2: CryptoRank Web fallback (only if API failed)
        # =====================================================================
        if ("cryptorank" not in sources_succeeded and
            "cryptorank_web" not in skip_sources and
            needs_source(missing_fields, CRYPTORANK_WEB_FIELDS)):
            sources_tried.append("cryptorank_web")
            try:
                from src.data.fetchers.cryptorank_web_fetcher import fetch_cryptorank_web
                cr_web_data = fetch_cryptorank_web(token)
                if cr_web_data and cr_web_data.get("_data_confidence", 0) > 0:
                    save_to_sources(token, "cryptorank_web.json", cr_web_data)
                    results.update(cr_web_data)
                    sources_succeeded.append("cryptorank_web")
                    logger.info(f"CryptoRank web scrape: {cr_web_data.get('_data_confidence', 0)}% confidence")
            except ImportError:
                logger.warning("cryptorank_web_fetcher not available")
            except Exception as e:
                logger.warning(f"CryptoRank web scrape failed: {e}")

    else:
        # =====================================================================
        # SEQUENTIAL FALLBACK (for debugging or when parallel=False)
        # =====================================================================
        # 1. CryptoRank API refresh
        if "cryptorank" not in skip_sources and needs_source(missing_fields, CRYPTORANK_FIELDS):
            sources_tried.append("cryptorank")
            cr_data = fetch_cryptorank(token, token_name=token_name)
            if cr_data:
                save_to_sources(token, "cryptorank.json", cr_data)
                results.update(cr_data)
                sources_succeeded.append("cryptorank")

        # 1.5 CryptoRank Web Scrape fallback
        if ("cryptorank" not in sources_succeeded and
            "cryptorank_web" not in skip_sources and
            needs_source(missing_fields, CRYPTORANK_WEB_FIELDS)):
            sources_tried.append("cryptorank_web")
            try:
                from src.data.fetchers.cryptorank_web_fetcher import fetch_cryptorank_web
                cr_web_data = fetch_cryptorank_web(token)
                if cr_web_data and cr_web_data.get("_data_confidence", 0) > 0:
                    save_to_sources(token, "cryptorank_web.json", cr_web_data)
                    results.update(cr_web_data)
                    sources_succeeded.append("cryptorank_web")
                    logger.info(f"CryptoRank web scrape: {cr_web_data.get('_data_confidence', 0)}% confidence")
            except ImportError:
                logger.warning("cryptorank_web_fetcher not available")
            except Exception as e:
                logger.warning(f"CryptoRank web scrape failed: {e}")

        # 2. Dropstab web scrape
        if "dropstab" not in skip_sources and needs_source(missing_fields, DROPSTAB_FIELDS):
            sources_tried.append("dropstab")
            ds_data = fetch_dropstab(token)
            if ds_data:
                save_to_sources(token, "dropstab.json", ds_data)
                results.update(ds_data)
                sources_succeeded.append("dropstab")

        # 2.5 ICODrops web scrape
        if "icodrops" not in skip_sources and needs_source(missing_fields, ICODROPS_FIELDS):
            sources_tried.append("icodrops")
            try:
                from src.data.fetchers.icodrops_fetcher import fetch_icodrops_data
                ico_data = fetch_icodrops_data(token, name=token_name)
                if ico_data and ico_data.get("_data_confidence", 0) > 0:
                    save_to_sources(token, "icodrops.json", ico_data)
                    results.update(ico_data)
                    sources_succeeded.append("icodrops")
                    logger.info(f"ICODrops: {ico_data.get('_data_confidence', 0)}% confidence")
            except ImportError:
                logger.warning("icodrops_fetcher not available")
            except Exception as e:
                logger.warning(f"ICODrops fetch failed: {e}")

        # 3. CoinGecko API
        if "coingecko" not in skip_sources and needs_source(missing_fields, COINGECKO_FIELDS):
            sources_tried.append("coingecko")
            cg_data = fetch_coingecko(token, token_name=token_name)
            if cg_data:
                save_to_sources(token, "coingecko.json", cg_data)
                results.update(cg_data)
                sources_succeeded.append("coingecko")

    # Session 495: Global auto-calculate float_percent if still missing after all primary sources
    if not results.get("float_percent"):
        circ = results.get("circulating_supply") or results.get("circulating_supply_at_tge")
        total = results.get("total_supply")
        if circ and total and total > 0:
            try:
                c_val = float(circ) if isinstance(circ, (str, int, float)) else 0
                t_val = float(total) if isinstance(total, (str, int, float)) else 0
                if t_val > 0:
                    results["float_percent"] = round((c_val / t_val) * 100, 2)
                    logger.info(f"Global auto-calculate float_percent: {results['float_percent']}%")
            except (ValueError, TypeError):
                pass

    # =====================================================================
    # PHASE 3: Conditional CMC fetch (always sequential - rate limited)
    # =====================================================================
    # 4. CoinMarketCap API (ONLY for contract_address - respect 333/day limit)
    if "coinmarketcap" not in skip_sources and "contract_address" in missing_fields:
        # Only call CMC if we still don't have contract_address
        if not results.get("contract_address"):
            sources_tried.append("coinmarketcap")
            cmc_data = fetch_coinmarketcap(token)
            if cmc_data:
                save_to_sources(token, "coinmarketcap.json", cmc_data)
                results.update(cmc_data)
                sources_succeeded.append("coinmarketcap")

    # 4b. CoinMarketCap FULL fetch (for tokens not on CoinGecko/CryptoRank OR when name mismatch)
    if "coinmarketcap" not in skip_sources:
        has_price = results.get("current_price") or results.get("listing_price")
        has_fdv = results.get("fdv") or results.get("market_cap")
        no_major_source = not any(s in sources_succeeded for s in ["cryptorank", "coingecko", "dropstab"])
        coingecko_rejected = "coingecko" in sources_tried and "coingecko" not in sources_succeeded
        name_was_provided = token_name is not None
        missing_pricing = not has_price or not has_fdv

        if no_major_source or missing_pricing or (coingecko_rejected and name_was_provided):
            reason = (
                "missing pricing data" if missing_pricing
                else "CoinGecko rejected name mismatch" if coingecko_rejected
                else "no major sources found"
            )
            logger.info(f"Trying CMC full fetch for {token} ({reason})")
            sources_tried.append("coinmarketcap_full")
            # Use identity lock's CMC ID for direct lookup (avoids ambiguous symbol matching)
            _cmc_id = None
            try:
                from src.data.token_identity_lock import get_identity_lock
                _lock = get_identity_lock(token)
                if _lock and str(_lock.get("source", "")).lower() == "coinmarketcap":
                    _cmc_id = int(_lock["external_id"])
            except Exception:
                pass
            cmc_full_data = fetch_coinmarketcap_full(token, token_name, cmc_id=_cmc_id)
            if cmc_full_data:
                if token_name and cmc_full_data.get("name"):
                    cmc_name = cmc_full_data.get("name", "").lower()
                    if token_name.lower() not in cmc_name and cmc_name not in token_name.lower():
                        logger.warning(
                            f"CMC Full: Name mismatch - wanted '{token_name}', got '{cmc_full_data.get('name')}'. "
                            f"Using anyway as best available data."
                        )
                save_to_sources(token, "coinmarketcap_full.json", cmc_full_data)
                results.update(cmc_full_data)
                sources_succeeded.append("coinmarketcap_full")
                logger.info(f"CMC full fetch success: {cmc_full_data.get('name')} @ ${cmc_full_data.get('current_price')}")

    # =====================================================================
    # PHASE 4: OTC Data (conditional)
    # =====================================================================
    if "otc" not in skip_sources and needs_source(missing_fields, OTC_FIELDS):
        sources_tried.append("otc")
        otc_data = fetch_otc_data(token)
        if otc_data and otc_data.get("otc_available"):
            save_to_sources(token, "otc.json", otc_data)
            results.update(otc_data)
            sources_succeeded.append(f"otc:{otc_data.get('_source', 'unknown')}")
        elif otc_data:
            save_to_sources(token, "otc.json", otc_data)

    elapsed = time.time() - start_time
    logger.info(f"Primary sources: tried={sources_tried}, succeeded={sources_succeeded}, elapsed={elapsed:.1f}s")

    # Add metadata
    results["token_symbol"] = token.upper()
    results["_primary_fetch_metadata"] = {
        "token": token,
        "missing_fields_requested": missing_fields,
        "sources_tried": sources_tried,
        "sources_succeeded": sources_succeeded,
        "fetched_at": datetime.utcnow().isoformat() + "Z",
        "elapsed_seconds": round(elapsed, 2),
        "parallel_mode": parallel
    }

    return results


# ============================================================================
# CLI for testing
# ============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Fetch token data from primary sources",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Fetch contract_address for IRYS
  python primary_source_fetcher.py IRYS --fields contract_address

  # Fetch multiple fields
  python primary_source_fetcher.py MONAD --fields contract_address investors fdv

  # Skip specific sources
  python primary_source_fetcher.py IRYS --fields contract_address --skip cryptorank
        """
    )

    parser.add_argument("token", help="Token symbol (e.g., IRYS, MONAD)")
    parser.add_argument(
        "--fields", "-f",
        nargs="+",
        default=["contract_address"],
        help="Fields to fetch"
    )
    parser.add_argument(
        "--skip", "-s",
        nargs="*",
        default=[],
        help="Sources to skip (cryptorank, dropstab, coingecko, coinmarketcap)"
    )
    parser.add_argument(
        "--no-parallel",
        action="store_true",
        help="Disable parallel fetching (for debugging)"
    )
    parser.add_argument(
        "--name", "-n",
        help="Token name for disambiguation (e.g., 'RateX' for RTX)"
    )

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    result = fetch_from_primary_sources(
        args.token,
        args.fields,
        skip_sources=args.skip,
        token_name=args.name,
        parallel=not args.no_parallel
    )

    print("\n" + "=" * 60)
    print(f"RESULT: {args.token}")
    print("=" * 60)
    for key, value in result.items():
        if not key.startswith("_"):
            print(f"  {key}: {value}")

    meta = result.get("_primary_fetch_metadata", {})
    print(f"\n  Sources tried: {meta.get('sources_tried', [])}")
    print(f"  Sources succeeded: {meta.get('sources_succeeded', [])}")
    print(f"  Elapsed: {meta.get('elapsed_seconds', 'N/A')}s")
    print(f"  Parallel mode: {meta.get('parallel_mode', True)}")
