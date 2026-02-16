#!/usr/bin/env python3
"""
TGE Data Loading Functions

Handles loading TGE data from multiple sources (manual JSON, CryptoRank).
Extracted from run_tge_analysis.py (Phase 3: Code Cleanup)
Session 267: Migrated from scripts/helpers/tge_data_loaders.py to src/data/tge_data_loaders.py

Usage:
    from src.data.tge_data_loaders import load_manual_tge_data, fetch_tge_data_from_cryptorank

    # Load from 3-source structure (new)
    data = load_manual_tge_data("data/tokens/IRYS/final/IRYS_latest.json", "IRYS")

    # Load from legacy archive (if needed)
    data = load_manual_tge_data("data/archive/legacy_tge/GAIB.json", "GAIB")

    # Fetch from CryptoRank
    data = fetch_tge_data_from_cryptorank("MONAD")

Created: 2025-11-19 (Phase 3: Large File Refactoring)
Updated: 2025-11-24 (Session 49: Legacy data archival - data/tge/ → data/archive/legacy_tge/)
"""

import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# Session 290: Redis caching for API data fetching
try:
    from src.utils.redis_cache import get_redis_cache
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logging.warning("Redis cache not available - caching disabled")

# Initialize logger
logger = logging.getLogger(__name__)

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.integrations.cryptorank.scanner import TGEScanner

# Import from tge_output module (still in scripts/helpers)
try:
    from src.utils.tge_output import print_info, print_success, print_error, print_warning
except ImportError:
    # Fallback: define simple print functions
    def print_info(msg): print(f"INFO: {msg}")
    def print_success(msg): print(f"SUCCESS: {msg}")
    def print_error(msg): print(f"ERROR: {msg}")
    def print_warning(msg): print(f"WARNING: {msg}")


def load_manual_tge_data(json_path: str, token: str) -> Optional[Dict[str, Any]]:
    """
    Load TGE data from manual JSON file

    Args:
        json_path: Path to JSON file with TGE data
        token: Token symbol for validation

    Returns:
        Dict with TGE data if valid, None otherwise
    """
    print_info(f"📁 Loading manual TGE data from: {json_path}")

    try:
        json_file = Path(json_path)
        if not json_file.exists():
            print_error(f"File not found: {json_path}")
            return None

        with open(json_file, 'r') as f:
            data = json.load(f)

        # Validate required fields
        required_fields = ["token_symbol", "token_name", "tge_date", "total_supply"]
        missing = [f for f in required_fields if f not in data]

        if missing:
            print_error(f"Missing required fields: {', '.join(missing)}")
            return None

        # Validate token symbol matches
        if data["token_symbol"].upper() != token.upper():
            print_warning(f"Token mismatch: JSON has {data['token_symbol']}, expected {token}")

        print_success(f"Loaded manual data for {data['token_symbol']}")
        print_info(f"  Name: {data.get('token_name', 'N/A')}")
        print_info(f"  TGE Date: {data.get('tge_date', 'N/A')}")

        # Format total supply with commas if it's a number
        total_supply = data.get('total_supply', 'N/A')
        if isinstance(total_supply, (int, float)):
            print_info(f"  Total Supply: {total_supply:,}")
        else:
            print_info(f"  Total Supply: {total_supply}")

        # Convert to pipeline format (matching CryptoRank scanner output)
        pipeline_data = {
            "name": data.get("token_name", token),
            "symbol": data.get("token_symbol", token),
            "tge_date": data.get("tge_date"),
            "ido_date": data.get("tge_date"),  # Alias for tge_date
            "blockchain": data.get("blockchain"),
            "total_supply": data.get("total_supply"),
            "raise_amount": data.get("funding_raised_usd"),
            "funding_raised_usd": data.get("funding_raised_usd"),  # Also keep original field name
            "category": data.get("category"),
            "_source": "manual_json",
        }

        # Add FDV (use midpoint if range provided, or single value)
        fdv_low = data.get("fdv_low")
        fdv_high = data.get("fdv_high")
        if fdv_low and fdv_high:
            pipeline_data["fdv"] = (fdv_low + fdv_high) / 2
            pipeline_data["fdv_low"] = fdv_low
            pipeline_data["fdv_high"] = fdv_high
        elif fdv_low:
            pipeline_data["fdv"] = fdv_low
        elif data.get("fdv"):
            pipeline_data["fdv"] = data.get("fdv")

        # Add initial market cap (use midpoint if range provided)
        mc_low = data.get("initial_market_cap_low")
        mc_high = data.get("initial_market_cap_high")
        if mc_low and mc_high:
            pipeline_data["initial_cap"] = (mc_low + mc_high) / 2
            pipeline_data["market_cap"] = (mc_low + mc_high) / 2  # Also set market_cap for scorer
            pipeline_data["initial_cap_low"] = mc_low
            pipeline_data["initial_cap_high"] = mc_high
        elif mc_low:
            pipeline_data["initial_cap"] = mc_low
            pipeline_data["market_cap"] = mc_low
        elif data.get("initial_market_cap"):
            pipeline_data["initial_cap"] = data.get("initial_market_cap")
            pipeline_data["market_cap"] = data.get("initial_market_cap")
        elif data.get("market_cap"):
            # CRITICAL: Also check for market_cap directly (GAIB has this)
            pipeline_data["market_cap"] = data.get("market_cap")
            pipeline_data["initial_cap"] = data.get("market_cap")

        # Add listing price (use midpoint if range provided)
        price_low = data.get("listing_price_low")
        price_high = data.get("listing_price_high")
        if price_low and price_high:
            pipeline_data["sale_price"] = (price_low + price_high) / 2
            pipeline_data["ido_price"] = (price_low + price_high) / 2
            pipeline_data["sale_price_low"] = price_low
            pipeline_data["sale_price_high"] = price_high
        elif price_low:
            pipeline_data["sale_price"] = price_low
            pipeline_data["ido_price"] = price_low
        elif data.get("listing_price"):
            pipeline_data["sale_price"] = data.get("listing_price")
            pipeline_data["ido_price"] = data.get("listing_price")

        # Add float percent and circulating supply
        if data.get("float_percent") is not None:
            pipeline_data["float_percent"] = data.get("float_percent")
        if data.get("circulating_supply_at_tge"):
            pipeline_data["circulating_supply"] = data.get("circulating_supply_at_tge")
            pipeline_data["circulating_supply_at_tge"] = data.get("circulating_supply_at_tge")  # Also keep original field name

        # Add token allocation and vesting
        if data.get("token_allocation"):
            pipeline_data["allocations"] = data.get("token_allocation")
            pipeline_data["token_allocation"] = data.get("token_allocation")

        if data.get("vesting_schedule"):
            pipeline_data["vesting"] = data.get("vesting_schedule")
            pipeline_data["vesting_schedule"] = data.get("vesting_schedule")

        # Add listing exchanges
        if data.get("listing_exchanges"):
            pipeline_data["exchanges"] = data.get("listing_exchanges")
            pipeline_data["listing_exchanges"] = data.get("listing_exchanges")

        # Add investor data
        if data.get("investors"):
            pipeline_data["investors"] = data.get("investors")
        if data.get("investor_tier"):
            pipeline_data["investor_tier"] = data.get("investor_tier")

        # Add FDV/MC ratio if provided
        if data.get("fdv_mc_ratio_low") and data.get("fdv_mc_ratio_high"):
            pipeline_data["fdv_mc_ratio"] = (data.get("fdv_mc_ratio_low") + data.get("fdv_mc_ratio_high")) / 2
            pipeline_data["fdv_mc_ratio_low"] = data.get("fdv_mc_ratio_low")  # Keep original field names
            pipeline_data["fdv_mc_ratio_high"] = data.get("fdv_mc_ratio_high")
        elif data.get("fdv_mc_ratio"):
            pipeline_data["fdv_mc_ratio"] = data.get("fdv_mc_ratio")

        print_info(f"  FDV: ${pipeline_data.get('fdv', 0):,.0f}")
        print_info(f"  Float: {pipeline_data.get('float_percent', 0)}%")
        print_info(f"  Listing Price: ${pipeline_data.get('sale_price', 0)}")

        return pipeline_data

    except json.JSONDecodeError as e:
        print_error(f"Invalid JSON: {str(e)}")
        return None
    except Exception as e:
        print_error(f"Error loading manual data: {str(e)}")
        return None


def load_existing_consolidated(token: str) -> Optional[Dict[str, Any]]:
    """
    Load existing consolidated.json if available.

    This function implements the "consolidated.json first" optimization:
    - If consolidated.json exists and is valid, use it directly
    - Skip expensive CryptoRank API calls
    - Works for post-TGE tokens that are no longer on CryptoRank

    Args:
        token: Token symbol (e.g., "SEEK")

    Returns:
        Dict with consolidated data, or None if not found/invalid
    """
    # Session 290: Redis caching (1h TTL - file reads are expensive)
    if REDIS_AVAILABLE:
        redis_cache = get_redis_cache()
        cache_key = f"consolidated:{token.upper()}"

        # Try to get from cache
        cached = redis_cache.get(cache_key, namespace="tge_data")
        if cached is not None:
            logger.debug(f"✅ Cache HIT: load_existing_consolidated({token})")
            return cached

        logger.debug(f"❌ Cache MISS: load_existing_consolidated({token}) - reading file")

    consolidated_path = Path(__file__).parent.parent.parent / "data" / "tokens" / token.upper() / "consolidated.json"

    if not consolidated_path.exists():
        logging.debug(f"No consolidated.json found for {token}")
        return None

    try:
        with open(consolidated_path, "r") as f:
            data = json.load(f)

        # Minimal validation - check required fields exist
        required = ["tge_date"]
        missing = [f for f in required if not data.get(f)]
        if missing:
            logging.warning(f"consolidated.json missing required fields for {token}: {missing}")
            return None

        # Check token symbol matches (handle both field names)
        token_symbol = data.get("token_symbol") or data.get("symbol")
        if token_symbol and token_symbol.upper() != token.upper():
            logging.warning(f"Token mismatch: file has {token_symbol}, expected {token}")
            return None

        logging.info(f"Loaded existing consolidated.json for {token}")

        # Session 290: Cache result for 1h
        if REDIS_AVAILABLE:
            redis_cache = get_redis_cache()
            cache_key = f"consolidated:{token.upper()}"
            redis_cache.set(cache_key, data, ttl_seconds=3600, namespace="tge_data")
            logger.debug(f"💾 Cached consolidated({token}) for 1h")

        return data

    except json.JSONDecodeError as e:
        logging.warning(f"Failed to parse consolidated.json: {e}")
        return None
    except IOError as e:
        logging.warning(f"Failed to read consolidated.json: {e}")
        return None


def fetch_tge_via_websearch(token: str) -> Optional[Dict[str, Any]]:
    """
    Use WebSearch to fetch complete TGE data when CryptoRank/scraping fails.

    NOTE: This function uses WebFetch to query web sources directly since
    WebSearch is a Claude Code tool not available inside Python scripts.

    Queries these sources in order:
    1. CoinMarketCap (CMC)
    2. CoinGecko
    3. Token's official website (if findable)
    4. ICODrops / ICOAnalytics

    Args:
        token: Token symbol to search for

    Returns:
        Dict with complete TGE data matching CryptoRank format, or None if not found

    Added: Session 48+ (WebFetch fallback for Agent 0 data fetching)
    """
    import requests
    from bs4 import BeautifulSoup

    try:
        print_info(f"🌐 WebFetch: Searching for {token} TGE data...")

        # Try CryptoRank direct page first (handles tokens not in "upcoming" API)
        print_info(f"  Trying CryptoRank direct page...")
        cryptorank_data = _fetch_from_cryptorank_page(token)
        if cryptorank_data:
            return cryptorank_data

        # Try CoinMarketCap
        print_info(f"  Trying CoinMarketCap...")
        cmc_data = _fetch_from_coinmarketcap(token)
        if cmc_data:
            return cmc_data

        # Try CoinGecko
        print_info(f"  Trying CoinGecko...")
        coingecko_data = _fetch_from_coingecko(token)
        if coingecko_data:
            return coingecko_data

        print_warning(f"WebFetch: Could not extract complete data for {token}")
        return None

    except Exception as e:
        print_error(f"WebFetch failed for {token}: {str(e)}")
        return None


def _estimate_fdv_from_funding(
    total_supply: int,
    funding_data: Dict[str, Any],
    token_name: str
) -> Optional[Dict[str, Any]]:
    """
    Estimate FDV for pre-launch tokens using funding round valuations.

    Strategy:
    1. Use typical funding-to-valuation ratios for crypto projects:
       - Seed: Funding × 15x = Post-money Valuation
       - Series A: Funding × 7x = Post-money Valuation
       - Series B+: Funding × 4x = Post-money Valuation
    2. Apply typical TGE markup: Last Valuation × 5x = TGE FDV
    3. Conservative approach: Use lower end of ranges

    This provides a rough FDV estimate for conviction scoring when:
    - Token hasn't listed yet (no market price)
    - No IDO price available
    - But funding data exists from CryptoRank

    Args:
        total_supply: Total token supply
        funding_data: Funding data from CryptoRank (with rounds)
        token_name: Token name for logging

    Returns:
        Dict with estimated FDV and metadata, or None if estimation not possible

    Example Output:
        {
            "fdv": 500000000,  # $500M estimated FDV
            "source": "estimated_from_funding",
            "confidence": 0.4,  # Low confidence (40%)
            "method": "funding_to_valuation_ratio",
            "inputs": {
                "total_funding": 10000000,
                "last_round_amount": 5000000,
                "estimated_valuation": 100000000,
                "tge_markup": 5.0
            }
        }

    Created: 2025-11-25 (Session 51.5 Continuation)
    Priority: Data Quality Improvement - FDV Estimation for Pre-Launch Tokens
    """
    try:
        total_funding = funding_data.get("total_raised", 0)
        rounds = funding_data.get("rounds", [])

        if not total_funding or not rounds:
            logger.debug(f"Cannot estimate FDV - no funding data")
            return None

        # Identify last round (most recent or largest)
        # Use largest round as proxy for valuation since we don't have dates
        last_round = max(rounds, key=lambda r: r.get("amount", 0))
        last_round_amount = last_round.get("amount", 0)
        round_type = last_round.get("round", "Unknown").lower()

        # Funding-to-valuation ratios based on typical crypto project patterns
        # Conservative estimates (lower end of industry ranges)
        funding_to_val_ratio = 15.0  # Default: Seed stage

        if "series a" in round_type or "pre-series a" in round_type:
            funding_to_val_ratio = 7.0
        elif "series b" in round_type or "series c" in round_type:
            funding_to_val_ratio = 4.0
        elif "seed" in round_type or "pre-seed" in round_type:
            funding_to_val_ratio = 15.0
        elif "strategic" in round_type or "private" in round_type:
            funding_to_val_ratio = 10.0

        # Estimate last round valuation
        estimated_valuation = last_round_amount * funding_to_val_ratio

        # TGE markup: Last valuation → TGE FDV
        # Conservative: 5x (industry average is 3-8x)
        tge_markup = 5.0
        estimated_tge_fdv = estimated_valuation * tge_markup

        logger.info(f"💡 Estimating FDV for {token_name} (pre-launch):")
        logger.info(f"   Last Round: ${last_round_amount:,.0f} ({round_type})")
        logger.info(f"   Estimated Valuation: ${estimated_valuation:,.0f} ({funding_to_val_ratio}x funding)")
        logger.info(f"   TGE FDV Estimate: ${estimated_tge_fdv:,.0f} ({tge_markup}x valuation)")
        logger.warning(f"   ⚠️  Low confidence estimate (40%) - verify with announcements")

        return {
            "fdv": int(estimated_tge_fdv),
            "source": "estimated_from_funding",
            "confidence": 0.4,  # Low confidence (40%)
            "method": "funding_to_valuation_ratio",
            "inputs": {
                "total_funding": total_funding,
                "last_round_amount": last_round_amount,
                "last_round_type": round_type,
                "funding_to_val_ratio": funding_to_val_ratio,
                "estimated_valuation": int(estimated_valuation),
                "tge_markup": tge_markup
            }
        }

    except Exception as e:
        logger.debug(f"FDV estimation from funding failed: {e}")
        return None


def _fetch_from_cryptorank_page(token: str) -> Optional[Dict[str, Any]]:
    """
    Fetch token data directly from CryptoRank price page
    This handles tokens that are not in the "upcoming" TGEs API (e.g., funding stage)
    """
    import requests
    from bs4 import BeautifulSoup

    try:
        # Known token name mappings (symbol -> CryptoRank slug)
        known_mappings = {
            "IRYS": "bundlr-network",  # IRYS is listed as Bundlr Network on CryptoRank
        }

        # Try common URL patterns
        url_patterns = []

        # Add known mapping first if exists
        if token.upper() in known_mappings:
            url_patterns.append(f"https://cryptorank.io/price/{known_mappings[token.upper()]}")

        # Add standard patterns
        url_patterns.extend([
            f"https://cryptorank.io/price/{token.lower()}",
            f"https://cryptorank.io/price/{token.lower()}-network",
            f"https://cryptorank.io/price/{token.lower()}-token",
        ])

        for url in url_patterns:
            try:
                logger.debug(f"Trying URL: {url}")
                headers = {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
                }
                response = requests.get(url, headers=headers, timeout=15)
                logger.debug(f"Response status: {response.status_code}")

                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, "html.parser")
                    import json

                    extracted = {
                        "symbol": token.upper(),
                        "name": None,
                        "blockchain": None,  # Blockchain/category name
                        "total_supply": None,
                        "float_percent": None,
                        "tge_date": None,
                        "tge_date_type": None,  # "confirmed_listing", "expected_listing", or "vesting_estimate"
                        "tge_date_needs_verification": False,  # True if estimate/expected
                        "circ_supply": None,
                        "fdv": None,
                        "mc": None,
                        "next_unlock_percent": None,
                        "next_unlock_date": None,
                        "funding": None,  # VC funding data
                        "sale_price": None,  # IDO/IEO listing price
                        "ido_price": None,  # Alias for sale_price
                        "exchanges": None,  # List of exchanges/launchpads
                        "token_allocation": None,  # Token allocation breakdown by category
                        "vc_investors": None,  # List of VC investor names
                        "_source": "cryptorank_page"
                    }

                    # Method 1: Extract from Next.js JSON data (most reliable)
                    # CryptoRank embeds data in <script id="__NEXT_DATA__" type="application/json">
                    next_data_script = soup.find("script", {"id": "__NEXT_DATA__", "type": "application/json"})

                    if next_data_script:
                        logger.debug(f"Found __NEXT_DATA__ script for {url}")
                        try:
                            next_data = json.loads(next_data_script.string)

                            # Navigate to the token data structure
                            # Structure: props.pageProps.coin and props.pageProps.priceStatistics
                            page_props = next_data.get("props", {}).get("pageProps", {})
                            coin_data = page_props.get("coin", {})
                            price_stats = page_props.get("priceStatistics", {})

                            if coin_data and price_stats:
                                # Extract fields from coin_data
                                extracted["name"] = coin_data.get("name")
                                extracted["symbol"] = coin_data.get("symbol", token.upper())

                                # Blockchain/Category
                                category_data = coin_data.get("category", {})
                                if isinstance(category_data, dict):
                                    extracted["blockchain"] = category_data.get("name")
                                elif isinstance(category_data, str):
                                    # Sometimes category is a string directly (e.g., "Chain", "DeFi")
                                    extracted["blockchain"] = category_data

                                # FDV (Fully Diluted Valuation) - available in both, prefer price_stats
                                fdv = price_stats.get("fullyDilutedMarketCap") or coin_data.get("fullyDilutedMarketCap")
                                if fdv:
                                    extracted["fdv"] = int(float(fdv))

                                # Market Cap
                                mc = price_stats.get("marketCap") or coin_data.get("marketCap")
                                if mc:
                                    extracted["mc"] = int(float(mc))

                                # Total supply - check both price_stats and coin_data
                                total_supply = price_stats.get("totalSupply") or price_stats.get("maxSupply") or coin_data.get("totalSupply")
                                if total_supply:
                                    extracted["total_supply"] = int(float(total_supply))

                                # TGE/Listing Date - prioritize actual listing date over vesting date
                                # CRITICAL: For perpetual futures execution, we need the EXCHANGE LISTING date,
                                # not the internal vesting schedule start date
                                listing_date = None
                                tge_date_type = None

                                # 1. Check if token is already traded (best indicator of listing date)
                                if coin_data.get("isTraded") and coin_data.get("listingDate"):
                                    listing_date = coin_data["listingDate"]
                                    tge_date_type = "confirmed_listing"

                                # 2. Check priceStatistics for listing date
                                elif price_stats.get("listingDate"):
                                    listing_date = price_stats["listingDate"]
                                    tge_date_type = "confirmed_listing"

                                # 3. For pre-launch tokens without confirmed listing, use vesting as ESTIMATE
                                elif not coin_data.get("isTraded") and coin_data.get("vesting"):
                                    vesting_data = coin_data.get("vesting", {})
                                    listing_date = vesting_data.get("tge_start_date") or vesting_data.get("total_start_date")
                                    tge_date_type = "vesting_estimate"  # Flag as estimate, not confirmed

                                    # Log warning for vesting estimates
                                    if listing_date:
                                        logger.warning(
                                            f"⚠️ Using vesting start date as TGE estimate for {coin_data.get('name', token)}. "
                                            f"Actual exchange listing may differ. Verify with announcements for perpetual futures execution."
                                        )

                                if listing_date:
                                    extracted["tge_date"] = listing_date
                                    extracted["tge_date_type"] = tge_date_type
                                    extracted["tge_date_needs_verification"] = (tge_date_type == "vesting_estimate")

                                # Next unlock data (separate from TGE float - this is a future unlock event)
                                next_unlock_percent = price_stats.get("nextUnlockPercent")
                                next_unlock_date = price_stats.get("nextUnlockDate")
                                if next_unlock_percent:
                                    extracted["next_unlock_percent"] = float(next_unlock_percent)
                                if next_unlock_date:
                                    extracted["next_unlock_date"] = next_unlock_date

                                # TGE Float percentage - check coin_data first, then price_stats
                                # This is the % unlocked AT TGE, not next unlock
                                circ_percent = coin_data.get("percentOfCircSupply") or price_stats.get("percentOfCircSupply")
                                if circ_percent:
                                    extracted["float_percent"] = float(circ_percent)
                                elif mc and fdv and fdv > 0:
                                    # Calculate float from MC/FDV ratio if token has launched
                                    extracted["float_percent"] = (mc / fdv) * 100

                                # Calculate circulating supply if we have total and percentage
                                if extracted["total_supply"] and extracted["float_percent"]:
                                    extracted["circ_supply"] = int(extracted["total_supply"] * (extracted["float_percent"] / 100))

                                # Extract Token Allocation from icoData
                                ico_data = coin_data.get("icoData", {})
                                allocation_data = ico_data.get("allocation", [])
                                if allocation_data and isinstance(allocation_data, list):
                                    # Initialize allocation categories
                                    token_allocation = {
                                        "team_pct": 0.0,
                                        "investors_pct": 0.0,
                                        "community_pct": 0.0,
                                        "ecosystem_pct": 0.0,
                                        "treasury_pct": 0.0,
                                        "liquidity_pct": 0.0,
                                        "other_pct": 0.0
                                    }

                                    # Parse allocation array and categorize
                                    for item in allocation_data:
                                        if not isinstance(item, dict):
                                            continue

                                        name = item.get("name", "").lower()
                                        percent = item.get("percent", 0)

                                        try:
                                            percent = float(percent)
                                        except (ValueError, TypeError):
                                            continue

                                        # Categorize based on name patterns (case-insensitive)
                                        if any(keyword in name for keyword in ["team", "core", "founder"]):
                                            token_allocation["team_pct"] += percent
                                        elif any(keyword in name for keyword in ["investor", "private", "seed", "strategic", "sale"]):
                                            token_allocation["investors_pct"] += percent
                                        elif any(keyword in name for keyword in ["community", "public", "airdrop", "reward"]):
                                            token_allocation["community_pct"] += percent
                                        elif any(keyword in name for keyword in ["ecosystem", "development", "grant"]):
                                            token_allocation["ecosystem_pct"] += percent
                                        elif any(keyword in name for keyword in ["treasury", "reserve"]):
                                            token_allocation["treasury_pct"] += percent
                                        elif any(keyword in name for keyword in ["liquidity", "dex", "cex", "market"]):
                                            token_allocation["liquidity_pct"] += percent
                                        else:
                                            token_allocation["other_pct"] += percent

                                    # Only set if we found any allocations
                                    if sum(token_allocation.values()) > 0:
                                        extracted["token_allocation"] = token_allocation
                                        logger.debug(f"Extracted token allocation: {token_allocation}")

                                # Extract ICO/IEO/IDO data from crowdsales array (PRIORITY)
                                # This has structured data for funding, prices, dates, exchanges
                                ico_data = coin_data.get("icoData", {})
                                crowdsales = coin_data.get("crowdsales", [])

                                # Track exchanges/launchpads
                                exchanges = []

                                if crowdsales:
                                    logger.debug(f"Found {len(crowdsales)} crowdsale rounds in structured data")
                                    funding_rounds = []

                                    for sale in crowdsales:
                                        sale_type = sale.get("type", "Unknown")  # IEO, IDO, Seed, Private
                                        amount_raised = sale.get("raise", {}).get("USD")
                                        price_usd = sale.get("price", {}).get("USD")
                                        platform = sale.get("idoPlatformKey")
                                        start_date = sale.get("start")
                                        end_date = sale.get("end")
                                        status = sale.get("status")  # active, past, upcoming

                                        # Extract funding round data
                                        if amount_raised:
                                            funding_rounds.append({
                                                "round": sale_type,
                                                "amount": int(amount_raised),
                                                "price": price_usd,
                                                "start_date": start_date,
                                                "end_date": end_date,
                                                "status": status,
                                                "platform": platform
                                            })

                                        # Extract listing price (prefer public sales: IEO > IDO > Public)
                                        # Priority: active IEO > active IDO > any IEO > any IDO > Private/Seed
                                        if price_usd and not extracted.get("sale_price"):
                                            if sale_type in ["IEO", "IDO", "Public"]:
                                                extracted["sale_price"] = float(price_usd)
                                                extracted["ido_price"] = float(price_usd)
                                                logger.debug(f"Extracted listing price: ${price_usd} from {sale_type}")
                                        elif price_usd and sale_type in ["IEO", "IDO"]:
                                            # Override if we find a public sale price
                                            extracted["sale_price"] = float(price_usd)
                                            extracted["ido_price"] = float(price_usd)

                                        # Extract TGE date from IEO/IDO end dates (when token launches)
                                        # Priority: active IEO > active IDO > upcoming IEO/IDO
                                        if not extracted.get("tge_date") or extracted.get("tge_date_type") != "confirmed_listing":
                                            if sale_type in ["IEO", "IDO"] and end_date:
                                                if status == "active":
                                                    # Active sales: end date = listing date (HIGH CONFIDENCE)
                                                    extracted["tge_date"] = end_date
                                                    extracted["tge_date_type"] = "confirmed_listing"
                                                    extracted["tge_date_needs_verification"] = False
                                                    logger.debug(f"Extracted TGE date from active {sale_type}: {end_date}")
                                                elif status == "upcoming" and not extracted.get("tge_date"):
                                                    # Upcoming sales: end date = expected listing (MEDIUM CONFIDENCE)
                                                    extracted["tge_date"] = end_date
                                                    extracted["tge_date_type"] = "expected_listing"
                                                    extracted["tge_date_needs_verification"] = True
                                                    logger.debug(f"Extracted expected TGE date from upcoming {sale_type}: {end_date}")

                                        # Extract exchange/launchpad
                                        if platform:
                                            # Convert platform key to readable name
                                            platform_name = platform.replace("-", " ").title()
                                            if platform_name not in exchanges:
                                                exchanges.append(platform_name)

                                    if funding_rounds:
                                        extracted["funding"] = {
                                            "total_raised": sum(r["amount"] for r in funding_rounds),
                                            "rounds": funding_rounds,
                                            "rounds_count": len(funding_rounds)
                                        }
                                        logger.debug(f"Extracted {len(funding_rounds)} funding rounds, total: ${extracted['funding']['total_raised']:,}")

                                # Fallback: Parse additionalLinks for funding if crowdsales empty/incomplete
                                if ico_data and (not extracted.get("funding") or not exchanges):
                                    additional_links = ico_data.get("additionalLinks", [])

                                    # Extract exchanges from additionalLinks titles
                                    for link in additional_links:
                                        title = link.get("title", "")
                                        href = link.get("href", "")

                                        # Look for exchange/launchpad names
                                        import re
                                        if re.search(r'(IEO|IDO|Launchpad|Spotlight|Launchpool)', title, re.IGNORECASE):
                                            # Extract platform name (e.g., "KuCoin", "Binance")
                                            platform_match = re.search(r'On\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)', title, re.IGNORECASE)
                                            if platform_match:
                                                platform_name = platform_match.group(1).strip()
                                                if platform_name not in exchanges:
                                                    exchanges.append(platform_name)

                                        # Fallback funding parsing (only if crowdsales missed it)
                                        if not extracted.get("funding"):
                                            funding_patterns = [
                                                r'[Rr]aises?\s+\$\s*([\d.]+)\s*([BMK])?(?:\s*[Mm]illion)?(?:\s*([Ss]eed|[Ss]eries\s+[A-Z]|[Pp]rivate|[Pp]ublic))?',
                                                r'\$\s*([\d.]+)\s*([BMK])?\s*(?:[Mm]illion)?\s*([Ss]eed|[Ss]eries\s+[A-Z]|[Pp]rivate|[Pp]ublic)',
                                                r'([Ss]eed|[Ss]eries\s+[A-Z]|[Pp]rivate)\s+[Rr]ound.*?\$\s*([\d.]+)\s*([BMK])?'
                                            ]

                                            for pattern in funding_patterns:
                                                match = re.search(pattern, title, re.IGNORECASE)
                                                if match:
                                                    groups = match.groups()
                                                    if len(groups) >= 2:
                                                        amount = float(groups[0])
                                                        multiplier = groups[1] if len(groups) > 1 else None
                                                        round_type = groups[2] if len(groups) > 2 else "Unknown"

                                                        if multiplier:
                                                            if multiplier.upper() == 'M':
                                                                amount *= 1_000_000
                                                            elif multiplier.upper() == 'B':
                                                                amount *= 1_000_000_000
                                                            elif multiplier.upper() == 'K':
                                                                amount *= 1_000
                                                        elif 'million' in title.lower():
                                                            amount *= 1_000_000

                                                        if not extracted.get("funding"):
                                                            extracted["funding"] = {
                                                                "total_raised": 0,
                                                                "rounds": [],
                                                                "rounds_count": 0
                                                            }

                                                        extracted["funding"]["rounds"].append({
                                                            "round": round_type if round_type else "Unknown",
                                                            "amount": int(amount),
                                                            "source": href,
                                                            "title": title
                                                        })
                                                        extracted["funding"]["total_raised"] += int(amount)
                                                        extracted["funding"]["rounds_count"] += 1
                                                        break

                                # Store exchanges list
                                if exchanges:
                                    extracted["exchanges"] = exchanges
                                    logger.debug(f"Extracted {len(exchanges)} exchanges/launchpads: {', '.join(exchanges)}")

                                # Extract VC Investors from multiple sources
                                vc_investors = []

                                # Source 1: Check crowdsales for investors list
                                for sale in crowdsales:
                                    investors_list = sale.get("investors", [])
                                    if investors_list:
                                        for investor in investors_list:
                                            if isinstance(investor, dict):
                                                investor_name = investor.get("name")
                                            elif isinstance(investor, str):
                                                investor_name = investor
                                            else:
                                                continue

                                            if investor_name and investor_name not in vc_investors:
                                                vc_investors.append(investor_name)

                                # Source 2: Check icoData partners
                                if ico_data:
                                    partners = ico_data.get("partners", [])
                                    if partners:
                                        for partner in partners:
                                            if isinstance(partner, dict):
                                                partner_name = partner.get("name")
                                            elif isinstance(partner, str):
                                                partner_name = partner
                                            else:
                                                continue

                                            if partner_name and partner_name not in vc_investors:
                                                vc_investors.append(partner_name)

                                    # Source 3: Parse additionalLinks for investor mentions
                                    additional_links = ico_data.get("additionalLinks", [])
                                    import re
                                    for link in additional_links:
                                        title = link.get("title", "")

                                        # Look for "led by X", "backed by Y", "with participation from Z"
                                        investor_patterns = [
                                            r'led by\s+([A-Z][a-zA-Z0-9\s&]+?)(?:\s+(?:and|,|;)|\s*$)',
                                            r'backed by\s+([A-Z][a-zA-Z0-9\s&]+?)(?:\s+(?:and|,|;)|\s*$)',
                                            r'(?:with|from)\s+([A-Z][a-zA-Z0-9\s&]+?)\s+(?:Capital|Ventures|Labs|Partners|VC|Fund)',
                                            r'investors?\s+(?:include|such as)\s+([A-Z][a-zA-Z0-9\s&,]+?)(?:\s+(?:and|\.|\s*$))',
                                            r'participation from\s+([A-Z][a-zA-Z0-9\s&]+?)(?:\s+(?:and|,|;)|\s*$)',
                                        ]

                                        for pattern in investor_patterns:
                                            matches = re.finditer(pattern, title, re.IGNORECASE)
                                            for match in matches:
                                                investor_text = match.group(1).strip()
                                                # Split on common delimiters
                                                for delimiter in [',', ' and ', ';']:
                                                    if delimiter in investor_text:
                                                        investor_names = [name.strip() for name in investor_text.split(delimiter)]
                                                        for name in investor_names:
                                                            if name and name not in vc_investors:
                                                                vc_investors.append(name)
                                                        break
                                                else:
                                                    # No delimiter found, use full text
                                                    if investor_text and investor_text not in vc_investors:
                                                        vc_investors.append(investor_text)

                                # Store VC investors if found
                                if vc_investors:
                                    extracted["vc_investors"] = vc_investors
                                    logger.debug(f"Extracted {len(vc_investors)} VC investors: {', '.join(vc_investors[:5])}")

                                # Also check priceStatistics for listing price fallback
                                if not extracted.get("sale_price"):
                                    crowdsale_price = price_stats.get("crowdsalePrice")
                                    if crowdsale_price:
                                        extracted["sale_price"] = float(crowdsale_price)
                                        extracted["ido_price"] = float(crowdsale_price)
                                        logger.debug(f"Extracted listing price from priceStatistics: ${crowdsale_price}")

                                # Check icoFullyDilutedMarketCap for FDV (pre-launch tokens)
                                if not extracted.get("fdv"):
                                    ico_fdv = coin_data.get("icoFullyDilutedMarketCap")
                                    if ico_fdv:
                                        extracted["fdv"] = int(float(ico_fdv))
                                        logger.debug(f"Extracted FDV from icoFullyDilutedMarketCap: ${extracted['fdv']:,}")

                                # Check initialMarketCap for MC (pre-launch tokens)
                                if not extracted.get("mc"):
                                    initial_mc = coin_data.get("initialMarketCap")
                                    if initial_mc:
                                        extracted["mc"] = int(float(initial_mc))
                                        logger.debug(f"Extracted MC from initialMarketCap: ${extracted['mc']:,}")

                                # Estimate FDV from funding if missing (pre-launch tokens)
                                if extracted["fdv"] is None and extracted["total_supply"] and extracted.get("funding"):
                                    estimated_fdv = _estimate_fdv_from_funding(
                                        total_supply=extracted["total_supply"],
                                        funding_data=extracted["funding"],
                                        token_name=extracted.get("name", token)
                                    )
                                    if estimated_fdv:
                                        extracted["fdv"] = estimated_fdv["fdv"]
                                        extracted["fdv_source"] = estimated_fdv["source"]
                                        extracted["fdv_estimation_confidence"] = estimated_fdv["confidence"]
                                        extracted["fdv_estimation_method"] = estimated_fdv["method"]
                                        extracted["fdv_estimation_inputs"] = estimated_fdv["inputs"]

                                # Calculate data confidence
                                fields_found = sum([
                                    1 if extracted["name"] else 0,
                                    1 if extracted["total_supply"] else 0,
                                    1 if extracted["float_percent"] else 0,
                                    1 if extracted["tge_date"] else 0,
                                    1 if extracted["fdv"] else 0,
                                    1 if extracted["mc"] else 0,
                                    1 if extracted["funding"] else 0,
                                    1 if extracted.get("sale_price") else 0,
                                    1 if extracted.get("exchanges") else 0,
                                    1 if extracted.get("blockchain") else 0,
                                    1 if extracted.get("token_allocation") else 0,
                                    1 if extracted.get("vc_investors") else 0
                                ])
                                extracted["_data_confidence"] = (fields_found / 12) * 100  # 12 key fields now

                                # Success if we got at least name and supply
                                logger.debug(f"Extraction complete. Name: {extracted.get('name')}, Supply: {extracted.get('total_supply')}")
                                if extracted["name"] and extracted["total_supply"]:
                                    details = [f"{extracted['total_supply']:,} supply"]
                                    if extracted.get("blockchain"):
                                        details.append(f"Chain: {extracted['blockchain']}")
                                    if extracted["float_percent"]:
                                        details.append(f"{extracted['float_percent']:.1f}% float")
                                    if extracted.get("sale_price"):
                                        details.append(f"Price: ${extracted['sale_price']}")
                                    if extracted["fdv"]:
                                        details.append(f"FDV: ${extracted['fdv']:,.0f}")
                                    if extracted["tge_date"]:
                                        tge_label = "TGE"
                                        if extracted.get("tge_date_type") == "vesting_estimate":
                                            tge_label = "TGE (est)"
                                        elif extracted.get("tge_date_type") == "expected_listing":
                                            tge_label = "TGE (expected)"
                                        details.append(f"{tge_label}: {extracted['tge_date']}")
                                    if extracted["funding"]:
                                        total_raised = extracted["funding"]["total_raised"]
                                        rounds = extracted["funding"]["rounds_count"]
                                        details.append(f"Raised: ${total_raised:,.0f} ({rounds} rounds)")
                                    if extracted.get("exchanges"):
                                        exchanges_str = ", ".join(extracted["exchanges"][:3])  # Show first 3
                                        if len(extracted["exchanges"]) > 3:
                                            exchanges_str += f" +{len(extracted['exchanges']) - 3} more"
                                        details.append(f"Exchanges: {exchanges_str}")
                                    if extracted.get("token_allocation"):
                                        alloc = extracted["token_allocation"]
                                        # Show top 2 allocation categories
                                        top_allocs = sorted([(k.replace("_pct", ""), v) for k, v in alloc.items() if v > 0],
                                                          key=lambda x: x[1], reverse=True)[:2]
                                        if top_allocs:
                                            alloc_str = ", ".join([f"{name}: {pct:.1f}%" for name, pct in top_allocs])
                                            details.append(f"Alloc: {alloc_str}")
                                    if extracted.get("vc_investors"):
                                        vc_count = len(extracted["vc_investors"])
                                        if vc_count > 0:
                                            vc_str = extracted["vc_investors"][0] if vc_count == 1 else f"{extracted['vc_investors'][0]} +{vc_count - 1}"
                                            details.append(f"VCs: {vc_str}")
                                    print_success(f"✓ CryptoRank JSON: Found {extracted['name']} ({extracted['_data_confidence']:.0f}% conf) - {', '.join(details)}")

                                    # Show warning if using vesting estimate or expected date
                                    if extracted.get("tge_date_needs_verification"):
                                        print_warning(f"⚠️  TGE date needs verification - verify actual listing date for perpetual futures execution")

                                    return extracted

                        except (json.JSONDecodeError, KeyError, TypeError) as e:
                            # JSON parsing failed, fall back to regex
                            logger.debug(f"JSON parsing failed for {url}: {type(e).__name__}: {str(e)}")
                            pass

                    # Method 2: Fallback to regex extraction if JSON parsing fails
                    page_text = soup.get_text()
                    import re

                    # Extract FDV and Market Cap (look for $ values)
                    fdv_patterns = [
                        r'FDV.*?\$\s*([\d,.]+)\s*([BMK])?',
                        r'Fully\s+Diluted.*?\$\s*([\d,.]+)\s*([BMK])?',
                        r'Diluted\s+Valuation.*?\$\s*([\d,.]+)\s*([BMK])?'
                    ]
                    for pattern in fdv_patterns:
                        fdv_match = re.search(pattern, page_text, re.IGNORECASE | re.DOTALL)
                        if fdv_match:
                            value = float(fdv_match.group(1).replace(',', ''))
                            multiplier = fdv_match.group(2)
                            if multiplier:
                                if multiplier.upper() == 'B':
                                    value *= 1_000_000_000
                                elif multiplier.upper() == 'M':
                                    value *= 1_000_000
                                elif multiplier.upper() == 'K':
                                    value *= 1_000
                            extracted["fdv"] = int(value)
                            break

                    # Extract Market Cap
                    mc_patterns = [
                        r'Market\s+Cap.*?\$\s*([\d,.]+)\s*([BMK])?',
                        r'Market\s+Capitalization.*?\$\s*([\d,.]+)\s*([BMK])?',
                        r'MC.*?\$\s*([\d,.]+)\s*([BMK])?'
                    ]
                    for pattern in mc_patterns:
                        mc_match = re.search(pattern, page_text, re.IGNORECASE | re.DOTALL)
                        if mc_match:
                            value = float(mc_match.group(1).replace(',', ''))
                            multiplier = mc_match.group(2)
                            if multiplier:
                                if multiplier.upper() == 'B':
                                    value *= 1_000_000_000
                                elif multiplier.upper() == 'M':
                                    value *= 1_000_000
                                elif multiplier.upper() == 'K':
                                    value *= 1_000
                            extracted["mc"] = int(value)
                            break

                    # Extract total supply (look for patterns like "10,000,000,000" or "10 billion")
                    supply_patterns = [
                        r'Total\s+Supply[:\s]*(\d{1,3}(?:,\d{3})+|\d+\.?\d*)\s*([BMK])?',
                        r'Max\s+Supply[:\s]*(\d{1,3}(?:,\d{3})+|\d+\.?\d*)\s*([BMK])?',
                        r'Supply[:\s]*(\d{1,3}(?:,\d{3})+|\d+\.?\d*)\s*([BMK])?(?=\s*(?:tokens?|coins?))'
                    ]
                    supply_match = None
                    for pattern in supply_patterns:
                        supply_match = re.search(pattern, page_text, re.IGNORECASE)
                        if supply_match:
                            break
                    if supply_match:
                        supply_str = supply_match.group(1).replace(',', '')
                        multiplier = supply_match.group(2) if len(supply_match.groups()) > 1 else None

                        # Convert to number
                        try:
                            value = float(supply_str)
                            if multiplier:
                                if multiplier.upper() == 'B':
                                    value *= 1_000_000_000
                                elif multiplier.upper() == 'M':
                                    value *= 1_000_000
                                elif multiplier.upper() == 'K':
                                    value *= 1_000
                            extracted["total_supply"] = int(value)
                        except (ValueError, AttributeError):
                            pass

                    # Extract listing/sale price (IDO/IEO price)
                    listing_price_patterns = [
                        r'(?:IDO|IEO|Public|Sale|Listing)\s+Price[:\s]*\$\s*([\d,.]+)',
                        r'Price[:\s]*\$\s*([\d,.]+)(?=\s*(?:USD|per\s+token))',
                        r'Token\s+Price[:\s]*\$\s*([\d,.]+)',
                        r'\$\s*([\d,.]+)\s+(?:IDO|IEO|Public|Sale)',
                    ]
                    for pattern in listing_price_patterns:
                        price_match = re.search(pattern, page_text, re.IGNORECASE | re.DOTALL)
                        if price_match:
                            price_str = price_match.group(1).replace(',', '')
                            try:
                                extracted["sale_price"] = float(price_str)
                                extracted["ido_price"] = float(price_str)
                                logger.debug(f"Regex: Extracted listing price: ${extracted['sale_price']}")
                                break
                            except ValueError:
                                pass

                    # Extract float % (circulating at TGE / total supply)
                    # Pattern: "Circ. Supply" or "Circulating Supply" followed by percentage
                    float_patterns = [
                        r'(?:Circ|Circulating).*?Supply.*?(\d+(?:\.\d+)?)\s*%',
                        r'Float.*?(\d+(?:\.\d+)?)\s*%',
                        r'Initial\s+Circulation.*?(\d+(?:\.\d+)?)\s*%',
                        r'TGE\s+Unlock.*?(\d+(?:\.\d+)?)\s*%'
                    ]
                    for pattern in float_patterns:
                        float_match = re.search(pattern, page_text, re.IGNORECASE | re.DOTALL)
                        if float_match:
                            extracted["float_percent"] = float(float_match.group(1))
                            break

                    # Extract circulating supply if shown
                    circ_patterns = [
                        r'Circ(?:ulating)?\s+Supply[:\s]*(\d{1,3}(?:,\d{3})+|\d+\.?\d*)\s*([BMK])?',
                        r'Circulating[:\s]*(\d{1,3}(?:,\d{3})+|\d+\.?\d*)\s*([BMK])?',
                        r'Initial\s+Supply[:\s]*(\d{1,3}(?:,\d{3})+|\d+\.?\d*)\s*([BMK])?'
                    ]
                    circ_match = None
                    for pattern in circ_patterns:
                        circ_match = re.search(pattern, page_text, re.IGNORECASE | re.DOTALL)
                        if circ_match:
                            break
                    if circ_match:
                        circ_str = circ_match.group(1).replace(',', '')
                        multiplier = circ_match.group(2) if len(circ_match.groups()) > 1 else None

                        try:
                            value = float(circ_str)
                            if multiplier:
                                if multiplier.upper() == 'B':
                                    value *= 1_000_000_000
                                elif multiplier.upper() == 'M':
                                    value *= 1_000_000
                                elif multiplier.upper() == 'K':
                                    value *= 1_000
                            extracted["circ_supply"] = int(value)
                        except (ValueError, AttributeError):
                            pass

                    # Calculate float % from circ_supply / total_supply if not found directly
                    if not extracted["float_percent"] and extracted["circ_supply"] and extracted["total_supply"]:
                        extracted["float_percent"] = (extracted["circ_supply"] / extracted["total_supply"]) * 100

                    # Extract TGE date with more comprehensive patterns
                    # PRIORITY: Look for IEO/IDO ending dates (these are listing dates)
                    date_patterns = [
                        # IEO/IDO ending patterns (HIGHEST PRIORITY - these are listing dates)
                        (r'(?:IEO|IDO).*?ending\s+((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},?\s+\d{4})', 1, "ieo_ido_ending"),
                        (r'(?:IEO|IDO).*?(?:ends?|closes?|finishes?)[:\s]+((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},?\s+\d{4})', 1, "ieo_ido_ending"),
                        (r'(?:IEO|IDO).*?ending\s+(\d{4}-\d{2}-\d{2})', 1, "ieo_ido_ending"),
                        # Month-only dates (e.g., "TGE Date: Nov 2025")
                        (r'(?:TGE|Launch|Listing).*?(?:Date|Time)?[:\s]*((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{4})', 1, "month_year"),
                        # ISO format: 2025-01-01 or 2025-01-01T14:00:00Z
                        (r'(?:TGE|Launch|Listing)\s*(?:Date|Time)?[:\s]*(\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}Z)?)', 1, "explicit_tge"),
                        # Month name: January 1, 2025 or Jan 1, 2025
                        (r'(?:TGE|Launch|Listing)\s*(?:Date|Time)?[:\s]*((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},?\s+\d{4})', 1, "explicit_tge"),
                        # Slash format: 01/01/2025
                        (r'(?:TGE|Launch|Listing)\s*(?:Date|Time)?[:\s]*(\d{1,2}/\d{1,2}/\d{4})', 1, "explicit_tge"),
                    ]
                    for pattern, group, date_type in date_patterns:
                        date_match = re.search(pattern, page_text, re.IGNORECASE)
                        if date_match:
                            extracted["tge_date"] = date_match.group(group)
                            # Mark IEO/IDO ending dates as confirmed (these ARE listing dates)
                            if date_type == "ieo_ido_ending":
                                extracted["tge_date_type"] = "confirmed_listing"
                                extracted["tge_date_needs_verification"] = False
                                logger.debug(f"Regex: Found IEO/IDO ending date (listing): {extracted['tge_date']}")
                            elif date_type == "month_year":
                                extracted["tge_date_type"] = "expected_listing"
                                extracted["tge_date_needs_verification"] = True
                                logger.debug(f"Regex: Found month-only TGE date (needs verification): {extracted['tge_date']}")
                            else:
                                extracted["tge_date_type"] = "expected_listing"
                                extracted["tge_date_needs_verification"] = True
                                logger.debug(f"Regex: Found TGE date: {extracted['tge_date']}")
                            break

                    # Extract token name from title or h1
                    title = soup.find("title")
                    if title:
                        # Usually format: "Token Name (SYMBOL) Price | CryptoRank"
                        title_match = re.search(r'([^(]+)\s*\(', title.get_text())
                        if title_match:
                            extracted["name"] = title_match.group(1).strip()

                    if not extracted["name"]:
                        h1 = soup.find("h1")
                        if h1:
                            extracted["name"] = h1.get_text(strip=True)

                    # Calculate data confidence based on fields extracted
                    fields_found = sum([
                        1 if extracted["name"] else 0,
                        1 if extracted["total_supply"] else 0,
                        1 if extracted["float_percent"] else 0,
                        1 if extracted["tge_date"] else 0,
                        1 if extracted["fdv"] else 0,
                        1 if extracted["mc"] else 0,
                        1 if extracted["funding"] else 0,
                        1 if extracted.get("sale_price") else 0,
                        1 if extracted.get("exchanges") else 0
                    ])
                    extracted["_data_confidence"] = (fields_found / 9) * 100  # 9 key fields

                    # If we got at least name and supply, it's a success
                    if extracted["name"] and extracted["total_supply"]:
                        details = [f"{extracted['total_supply']:,} supply"]
                        if extracted["float_percent"]:
                            details.append(f"{extracted['float_percent']:.1f}% float")
                        if extracted.get("sale_price"):
                            details.append(f"Price: ${extracted['sale_price']}")
                        if extracted["fdv"]:
                            details.append(f"FDV: ${extracted['fdv']:,.0f}")
                        if extracted["tge_date"]:
                            tge_label = "TGE"
                            if extracted.get("tge_date_type") == "expected_listing":
                                tge_label = "TGE (expected)"
                            details.append(f"{tge_label}: {extracted['tge_date']}")
                        if extracted["funding"]:
                            total_raised = extracted["funding"]["total_raised"]
                            rounds = extracted["funding"]["rounds_count"]
                            details.append(f"Raised: ${total_raised:,.0f} ({rounds} rounds)")
                        if extracted.get("exchanges"):
                            exchanges_str = ", ".join(extracted["exchanges"][:3])
                            if len(extracted["exchanges"]) > 3:
                                exchanges_str += f" +{len(extracted['exchanges']) - 3} more"
                            details.append(f"Exchanges: {exchanges_str}")
                        print_success(f"✓ CryptoRank Regex: Found {extracted['name']} ({extracted['_data_confidence']:.0f}% conf) - {', '.join(details)}")

                        # Show warning if TGE date needs verification
                        if extracted.get("tge_date_needs_verification"):
                            print_warning(f"⚠️  TGE date needs verification - verify actual listing date")

                        return extracted

            except requests.RequestException:
                continue

        return None

    except Exception as e:
        return None


def _fetch_from_coinmarketcap(token: str) -> Optional[Dict[str, Any]]:
    """Fetch token data from CoinMarketCap"""
    import requests
    from bs4 import BeautifulSoup

    try:
        # Try the currencies page
        url = f"https://coinmarketcap.com/currencies/{token.lower()}/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        }

        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code != 200:
            # Try searching
            search_url = f"https://coinmarketcap.com/search/?q={token}"
            response = requests.get(search_url, headers=headers, timeout=15)
            if response.status_code != 200:
                return None

        soup = BeautifulSoup(response.text, "html.parser")

        # Extract basic data from CMC
        extracted = {
            "symbol": token.upper(),
            "name": None,
            "total_supply": None,
            "circulating_supply": None,
            "fdv": None,
            "market_cap": None,
            "_source": "coinmarketcap"
        }

        # Extract token name from h1 or title
        name_elem = soup.find("h1", class_="sc-65e7f566-0")
        if not name_elem:
            name_elem = soup.find("h2", class_="sc-65e7f566-0")
        if name_elem:
            # Extract name (usually format: "TokenName price")
            full_text = name_elem.get_text(strip=True)
            extracted["name"] = full_text.split(" price")[0] if " price" in full_text else full_text

        # Extract stats from the stats table
        # CMC uses a dl/dt/dd structure for stats
        stats_section = soup.find_all("dl", class_="sc-b3fc6b7-0")

        for dl in stats_section:
            dt_elements = dl.find_all("dt")
            dd_elements = dl.find_all("dd")

            for dt, dd in zip(dt_elements, dd_elements):
                label = dt.get_text(strip=True).lower()
                value = dd.get_text(strip=True)

                # Remove currency symbols and commas
                value_clean = value.replace("$", "").replace(",", "").split()[0]

                try:
                    if "market cap" in label and "fully" not in label:
                        extracted["market_cap"] = float(value_clean) if value_clean.replace(".", "").isdigit() else None
                    elif "fully diluted" in label or "fdv" in label:
                        extracted["fdv"] = float(value_clean) if value_clean.replace(".", "").isdigit() else None
                    elif "circulating supply" in label:
                        extracted["circulating_supply"] = float(value_clean) if value_clean.replace(".", "").isdigit() else None
                    elif "total supply" in label or "max supply" in label:
                        extracted["total_supply"] = float(value_clean) if value_clean.replace(".", "").isdigit() else None
                except (ValueError, AttributeError):
                    continue

        # If we got at least name and one metric, consider it a success
        if extracted["name"] and (extracted["fdv"] or extracted["market_cap"] or extracted["total_supply"]):
            print_success(f"✓ CoinMarketCap: Found data for {extracted['name']}")
            return extracted

        return None

    except Exception as e:
        return None


def _fetch_from_coingecko(token: str) -> Optional[Dict[str, Any]]:
    """Fetch token data from CoinGecko API"""
    import requests

    try:
        # CoinGecko has a free API
        url = f"https://api.coingecko.com/api/v3/search?query={token}"
        response = requests.get(url, timeout=15)

        if response.status_code != 200:
            return None

        data = response.json()
        coins = data.get("coins", [])

        if not coins:
            return None

        # Get the first matching coin
        coin = coins[0]
        coin_id = coin.get("id")

        # Fetch detailed data
        detail_url = f"https://api.coingecko.com/api/v3/coins/{coin_id}"
        detail_response = requests.get(detail_url, timeout=15)

        if detail_response.status_code != 200:
            return None

        detail_data = detail_response.json()

        # Extract data
        extracted = {
            "symbol": token.upper(),
            "name": detail_data.get("name"),
            "total_supply": detail_data.get("market_data", {}).get("total_supply"),
            "circulating_supply": detail_data.get("market_data", {}).get("circulating_supply"),
            "fdv": detail_data.get("market_data", {}).get("fully_diluted_valuation", {}).get("usd"),
            "market_cap": detail_data.get("market_data", {}).get("market_cap", {}).get("usd"),
            "_source": "coingecko"
        }

        if extracted["symbol"] and (extracted["total_supply"] or extracted["fdv"]):
            return extracted

        return None

    except Exception as e:
        return None


def _parse_websearch_results(
    token: str,
    basic_results: str,
    tokenomics_results: str,
    vc_results: str,
    listing_results: str
) -> Optional[Dict[str, Any]]:
    """
    Parse WebSearch results to extract TGE data.

    Uses pattern matching and keyword extraction to find:
    - TGE date (look for date patterns near "TGE", "launch", "listing")
    - Total supply (look for numbers near "total supply", "max supply")
    - FDV (look for "$X billion" or "$X million" near "FDV", "valuation")
    - Float % (look for percentage near "circulating", "float", "unlock")

    Returns:
        Dict with extracted TGE data, or None if insufficient data found
    """
    import re
    from datetime import datetime

    extracted = {
        "symbol": token.upper(),
        "name": None,
        "tge_date": None,
        "total_supply": None,
        "circulating_supply": None,
        "fdv": None,
        "float_percent": None,
        "allocations": {},
        "funding_rounds": [],
        "listing_venues": [],
        "_source": "websearch"
    }

    # Combine all results for easier parsing
    all_text = f"{basic_results}\n{tokenomics_results}\n{vc_results}\n{listing_results}"

    # Extract token name (look for "token name" near token symbol)
    name_patterns = [
        rf"{token}\s+\(([\w\s]+)\)",  # e.g., "IRYS (Irys Network)"
        rf"([\w\s]+)\s+\({token}\)",   # e.g., "Irys Network (IRYS)"
        rf"{token}\s+(?:token|cryptocurrency|project):\s+([\w\s]+)"
    ]
    for pattern in name_patterns:
        match = re.search(pattern, all_text, re.IGNORECASE)
        if match:
            extracted["name"] = match.group(1).strip()
            break

    if not extracted["name"]:
        extracted["name"] = token  # Fallback to symbol

    # Extract TGE date (look for date patterns)
    date_patterns = [
        r"TGE.*?(\d{4}-\d{2}-\d{2})",
        r"launch.*?(\d{4}-\d{2}-\d{2})",
        r"listing.*?(\d{4}-\d{2}-\d{2})",
        r"(\d{2}/\d{2}/\d{4})",  # MM/DD/YYYY
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4}"
    ]
    for pattern in date_patterns:
        match = re.search(pattern, all_text, re.IGNORECASE)
        if match:
            date_str = match.group(1)
            try:
                # Try parsing different date formats
                for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y"]:
                    try:
                        parsed_date = datetime.strptime(date_str, fmt)
                        extracted["tge_date"] = parsed_date.isoformat()
                        break
                    except Exception:
                        continue
                if extracted["tge_date"]:
                    break
            except Exception:
                continue

    # Extract total supply (look for large numbers near "total supply")
    supply_patterns = [
        r"total supply[:\s]+(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(billion|million|B|M)?",
        r"max supply[:\s]+(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(billion|million|B|M)?"
    ]
    for pattern in supply_patterns:
        match = re.search(pattern, all_text, re.IGNORECASE)
        if match:
            number = float(match.group(1).replace(",", ""))
            multiplier = match.group(2)
            if multiplier:
                if "billion" in multiplier.lower() or multiplier.upper() == "B":
                    number *= 1_000_000_000
                elif "million" in multiplier.lower() or multiplier.upper() == "M":
                    number *= 1_000_000
            extracted["total_supply"] = int(number)
            break

    # Extract FDV (look for valuation numbers)
    fdv_patterns = [
        r"FDV[:\s]+\$(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(billion|million|B|M)?",
        r"fully diluted valuation[:\s]+\$(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(billion|million|B|M)?"
    ]
    for pattern in fdv_patterns:
        match = re.search(pattern, all_text, re.IGNORECASE)
        if match:
            number = float(match.group(1).replace(",", ""))
            multiplier = match.group(2)
            if multiplier:
                if "billion" in multiplier.lower() or multiplier.upper() == "B":
                    number *= 1_000_000_000
                elif "million" in multiplier.lower() or multiplier.upper() == "M":
                    number *= 1_000_000
            extracted["fdv"] = int(number)
            break

    # Extract float % (look for percentage near "float" or "circulating")
    float_patterns = [
        r"float[:\s]+(\d+(?:\.\d+)?)\s*%",
        r"circulating[:\s]+(\d+(?:\.\d+)?)\s*%",
        r"TGE unlock[:\s]+(\d+(?:\.\d+)?)\s*%"
    ]
    for pattern in float_patterns:
        match = re.search(pattern, all_text, re.IGNORECASE)
        if match:
            extracted["float_percent"] = float(match.group(1))
            break

    # Extract listing venues (look for exchange names)
    exchanges = ["Binance", "Coinbase", "Bybit", "OKX", "Gate.io", "KuCoin", "Huobi", "Kraken", "MEXC"]
    for exchange in exchanges:
        if re.search(rf"\b{exchange}\b", all_text, re.IGNORECASE):
            extracted["listing_venues"].append(exchange)

    # Only return if we have at least symbol and one useful field
    if extracted["symbol"] and (extracted["tge_date"] or extracted["total_supply"] or extracted["fdv"]):
        return extracted

    return None


def fetch_tge_data_from_cryptorank(token: str) -> Optional[Dict[str, Any]]:
    """
    Fetch TGE data from CryptoRank for a specific token

    Args:
        token: Token symbol to search for

    Returns:
        Dict with TGE data if found, None otherwise
    """
    # Session 290: Redis caching (6h TTL - API data changes infrequently)
    if REDIS_AVAILABLE:
        redis_cache = get_redis_cache()
        cache_key = f"cryptorank:{token.upper()}"

        # Try to get from cache
        cached = redis_cache.get(cache_key, namespace="tge_data")
        if cached is not None:
            logger.debug(f"✅ Cache HIT: fetch_tge_data_from_cryptorank({token})")
            print_info(f"✅ Using cached CryptoRank data for {token}")
            return cached

        logger.debug(f"❌ Cache MISS: fetch_tge_data_from_cryptorank({token}) - fetching from API")

    print_info(f"🔍 Fetching TGE data from CryptoRank for: {token}")

    try:
        # Initialize scanner (using basic parsing from __NEXT_DATA__)
        scanner = TGEScanner()

        # Scan upcoming TGEs
        tges = scanner.scan_upcoming_tges(max_results=100)

        # Known token name mappings (symbol -> alternative names to search)
        # Used for rebranded tokens or tokens with different names on CryptoRank
        known_mappings = {
            "IRYS": ["bundlr-network", "bundlr", "irys"],  # IRYS rebranded from Bundlr Network
        }

        # Build list of names to search for
        search_terms = [token.upper()]
        if token.upper() in known_mappings:
            search_terms.extend([name.upper() for name in known_mappings[token.upper()]])

        # Search for matching token by symbol or name variations
        for tge in tges:
            tge_symbol = tge.get("symbol", "").upper()
            tge_name = tge.get("project_name", "").upper()

            # Check if any search term matches symbol or name
            if any(term in [tge_symbol, tge_name] or term in tge_name.replace("-", " ") for term in search_terms):
                print_success(f"Found {token} on CryptoRank (matched: {tge.get('project_name', 'N/A')})")
                print_info(f"  Name: {tge.get('project_name', 'N/A')}")
                print_info(f"  Symbol: {tge.get('symbol', 'N/A')}")
                print_info(f"  TGE Date: {tge.get('ido_date', 'N/A')}")
                print_info(f"  Launchpad: {tge.get('launchpad', 'N/A')}")

                # Convert scanner format to pipeline format
                result = {
                    "name": tge.get("project_name", token),
                    "symbol": tge.get("symbol", token),
                    "tge_date": tge.get("ido_date"),
                    "sale_price": tge.get("sale_price"),
                    "launchpad": tge.get("launchpad"),
                    "blockchain": tge.get("blockchain"),
                    "initial_cap": tge.get("initial_cap"),
                    "raise_amount": tge.get("raise_amount"),
                }

                # Session 290: Cache result for 6h
                if REDIS_AVAILABLE:
                    redis_cache = get_redis_cache()
                    cache_key = f"cryptorank:{token.upper()}"
                    redis_cache.set(cache_key, result, ttl_seconds=21600, namespace="tge_data")
                    logger.debug(f"💾 Cached cryptorank({token}) for 6h")

                return result

        # Token not found in CryptoRank upcoming API - try direct page fetch
        print_warning(f"Token {token} not found in CryptoRank upcoming API")
        print_info("🔍 Trying direct CryptoRank page fetch (handles already-launched tokens)...")

        # Try fetching from CryptoRank price page directly
        page_data = _fetch_from_cryptorank_page(token)
        if page_data:
            # Session 290: Cache result for 6h
            if REDIS_AVAILABLE:
                redis_cache = get_redis_cache()
                cache_key = f"cryptorank:{token.upper()}"
                redis_cache.set(cache_key, page_data, ttl_seconds=21600, namespace="tge_data")
                logger.debug(f"💾 Cached cryptorank_page({token}) for 6h")
            return page_data

        # Still not found - try WebSearch fallback
        print_info("🔍 Trying WebSearch fallback to find complete TGE data...")

        websearch_data = fetch_tge_via_websearch(token)
        if websearch_data:
            # Session 290: Cache result for 6h
            if REDIS_AVAILABLE:
                redis_cache = get_redis_cache()
                cache_key = f"cryptorank:{token.upper()}"
                redis_cache.set(cache_key, websearch_data, ttl_seconds=21600, namespace="tge_data")
                logger.debug(f"💾 Cached websearch({token}) for 6h")
            return websearch_data

        print_error(f"Token {token} not found via CryptoRank or WebSearch")
        print_info("Possible reasons:")
        print_info("  1. Token not yet listed on CryptoRank or public sources")
        print_info("  2. TGE already completed (check past events)")
        print_info("  3. Incorrect token symbol (check spelling)")
        print_info("")
        print_info("Alternatives:")
        print_info("  - Wait for CryptoRank listing")
        print_info("  - Manually provide TGE data via JSON")
        print_info("  - Check ICODrops, ICOAnalytics for data")

        return None

    except Exception as e:
        print_error(f"Error fetching CryptoRank data: {str(e)}")
        print_info("🔍 Trying WebSearch fallback...")

        try:
            websearch_data = fetch_tge_via_websearch(token)
            if websearch_data:
                # Session 290: Cache result for 6h
                if REDIS_AVAILABLE:
                    redis_cache = get_redis_cache()
                    cache_key = f"cryptorank:{token.upper()}"
                    redis_cache.set(cache_key, websearch_data, ttl_seconds=21600, namespace="tge_data")
                    logger.debug(f"💾 Cached websearch_fallback({token}) for 6h")
                return websearch_data
        except Exception as ws_error:
            print_error(f"WebSearch fallback also failed: {str(ws_error)}")

        return None


def consolidate_tge_data_if_available(token: str, automated_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Automatically consolidate automated data with manual Perplexity data if available.

    This function integrates the data quality improvement workflow (Priority #2-4) into the
    agent pipeline, running validation and consolidation automatically during data retrieval.

    Workflow:
    1. Check if Perplexity data exists in sources/ directory
    2. If exists: Run Perplexity validator (Priority #2)
    3. If exists: Run TGE date validator (Priority #3)
    4. If exists: Run consolidator (Priority #4)
    5. Return consolidated data (or original if no manual data)

    Args:
        token: Token symbol (e.g., "IRYS")
        automated_data: Data from _fetch_from_cryptorank_page or fetch_tge_data_from_cryptorank

    Returns:
        Dict with consolidated data (or original automated data if no manual sources)

    Created: Session 51.5 (Agent Workflow Integration)
    """
    from pathlib import Path
    import json

    # Session 86: Early exit if data is already consolidated (avoid re-processing)
    if automated_data.get("_consolidation_metadata"):
        logging.debug(f"Data for {token} already consolidated - skipping re-consolidation")
        return automated_data

    PROJECT_ROOT = Path(__file__).parent.parent.parent
    sources_dir = PROJECT_ROOT / "data" / "tokens" / token.upper() / "sources"

    # Check for Perplexity data
    perplexity_files = list(sources_dir.glob("1_perplexity_*.json")) if sources_dir.exists() else []

    if not perplexity_files:
        print_info(f"   No Perplexity data found for {token} - using automated data only")
        print_info(f"   Data confidence: {automated_data.get('_data_confidence', 0):.0f}%")
        return automated_data

    # Load most recent Perplexity file
    perplexity_file = max(perplexity_files, key=lambda p: p.stat().st_mtime)
    print_info(f"   Found Perplexity data: {perplexity_file.name}")

    try:
        with perplexity_file.open("r") as f:
            manual_data = json.load(f)

        # Phase 1.5: Run Perplexity validator (Priority #2)
        print_info("   Running Perplexity validator...")
        try:
            from src.data.validation.perplexity_validator import validate_perplexity_data
            validation_result = validate_perplexity_data(manual_data, token)

            if validation_result["errors"]:
                print_error(f"   ⚠️  Perplexity validation errors detected:")
                for error in validation_result["errors"]:
                    print_error(f"      • {error}")
            elif validation_result["warnings"]:
                print_warning(f"   ⚠️  Perplexity validation warnings:")
                for warning in validation_result["warnings"]:
                    print_warning(f"      • {warning}")
            else:
                print_success(f"   ✅ Perplexity data validated (no errors)")
        except ImportError:
            print_warning("   ⚠️  Perplexity validator not available - skipping validation")
        except Exception as e:
            print_warning(f"   ⚠️  Perplexity validation failed: {str(e)}")

        # Phase 1.55: Fetch Dropstab data (3rd source for cross-validation)
        print_info("   Fetching Dropstab data for cross-validation...")
        dropstab_data = None
        try:
            from src.data.fetchers.dropstab_fetcher import fetch_dropstab_data
            dropstab_data = fetch_dropstab_data(token)

            if dropstab_data:
                confidence = dropstab_data.get("_data_confidence", 0)
                print_success(f"   ✅ Dropstab data fetched ({confidence}% confidence)")
                if dropstab_data.get("total_funding"):
                    print_info(f"      Funding: ${dropstab_data['total_funding']:,}")
                if dropstab_data.get("investors"):
                    print_info(f"      Investors: {len(dropstab_data['investors'])} found")
                if dropstab_data.get("vesting_schedule"):
                    print_info(f"      Vesting events: {len(dropstab_data['vesting_schedule'])} unlocks")

                # Save Dropstab data to sources directory for future reference
                from datetime import datetime
                dropstab_file = sources_dir / f"2_dropstab_{datetime.utcnow().strftime('%Y-%m-%d')}.json"
                sources_dir.mkdir(parents=True, exist_ok=True)
                with dropstab_file.open("w") as f:
                    json.dump(dropstab_data, f, indent=2)
                print_info(f"      Saved to: {dropstab_file.name}")
            else:
                print_warning(f"   ⚠️  No Dropstab data found for {token}")
        except ImportError:
            print_warning("   ⚠️  Dropstab fetcher not available")
        except Exception as e:
            print_warning(f"   ⚠️  Dropstab fetch failed: {str(e)}")

        # Phase 1.6: Run TGE date validator (Priority #3)
        print_info("   Running TGE date validator...")
        try:
            from src.data.exchange_listing_verifier import validate_tge_date
            tge_validation = validate_tge_date(
                token_symbol=token,
                automated_date=automated_data.get("tge_date"),
                automated_date_type=automated_data.get("tge_date_type"),
                manual_date=manual_data.get("tge_date")
            )

            if tge_validation["requires_verification"]:
                print_warning(f"   ⚠️  TGE DATE VERIFICATION REQUIRED")
                print_warning(f"      {tge_validation['reason']}")
            elif tge_validation["conflict_detected"]:
                print_info(f"   ✅ TGE date conflict resolved: using {tge_validation['tge_date']} ({tge_validation['date_source']})")
                print_info(f"      Date difference: {tge_validation['date_diff_days']} days")
            else:
                print_success(f"   ✅ TGE date validated: {tge_validation['tge_date']} ({tge_validation['date_source']})")
        except ImportError:
            print_warning("   ⚠️  TGE validator not available - skipping validation")
        except Exception as e:
            print_warning(f"   ⚠️  TGE validation failed: {str(e)}")

        # Phase 1.7: Simple consolidation - merge automated + manual data
        # Use validated TGE date from Phase 1.6, prefer manual for other fields
        print_info("   Running simple data merge...")

        # Start with automated data as base
        consolidated = automated_data.copy()

        # Override with validated TGE date if we have it
        if tge_validation and tge_validation.get("validated"):
            consolidated["tge_date"] = tge_validation["tge_date"]
            consolidated["tge_date_type"] = "confirmed_listing"  # Mark as validated
            consolidated["_tge_validation"] = tge_validation

        # Merge manual data fields (manual wins for most fields)
        manual_priority_fields = [
            # Core tokenomics (SESSION 79: Added CRITICAL fields for quality gate)
            "fdv", "fdv_low", "fdv_high",  # FDV range (CRITICAL)
            "float_percent", "total_supply", "mc",
            "circ_supply", "circulating_supply_at_tge",  # Circulating supply at TGE (CRITICAL)
            "funding", "total_funding", "funding_raised_usd",  # Funding (CRITICAL)
            "exchanges", "listing_exchanges",  # Listing exchanges (CRITICAL)
            "listing_price_low", "listing_price_high",  # Listing price range (CRITICAL)
            "fdv_mc_ratio", "fdv_mc_ratio_low", "fdv_mc_ratio_high",  # FDV/MC ratio (CRITICAL)
            "market_cap", "initial_market_cap", "initial_market_cap_low", "initial_market_cap_high",
            "contract_address",  # Contract address (CRITICAL)

            # Conviction scoring fields (VC analysis + IMPORTANT fields)
            "investors", "vc_investors", "funding_rounds",
            "vc_markup", "vc_markup_tier", "vc_markup_conviction_impact",
            "tier_1_vc_count", "tier_1_vcs_list", "vc_tier_assessment", "investor_tier",
            "vc_profit_incentive", "latest_pre_tge_valuation",
            "category", "token_allocation", "vesting_schedule",  # IMPORTANT fields
            "whitepaper_url", "tokenomics_model",  # IMPORTANT fields

            # Conviction scoring fields (market analysis)
            "btc_market_structure", "eth_market_structure", "macro_market_conditions",
            "listing_ta_confluence", "listing_vs_otc_premium_pct",
            "btc_structure_reasoning", "eth_structure_reasoning", "listing_ta_reasoning",

            # Conviction scoring fields (pattern matching)
            "historical_pattern", "comparable_tges", "pattern_confidence",

            # Conviction scoring fields (social/sentiment)
            "alpha_caller_mentions", "alpha_callers_list", "social_validation_tier",
            "social_conviction", "vs_major_tge_benchmark",

            # Conviction scoring fields (OTC/derivatives)
            "otc_volume_trend", "otc_platforms", "otc_conviction_impact",
            "oi_data", "orderbook_data"
        ]

        conflicts = []
        agreements = []

        for field in manual_priority_fields:
            manual_value = manual_data.get(field)
            auto_value = automated_data.get(field)

            if manual_value and auto_value:
                # Both have data - check if they agree
                try:
                    if isinstance(manual_value, (int, float)) and isinstance(auto_value, (int, float)):
                        diff_pct = abs(manual_value - auto_value) / max(manual_value, auto_value) * 100
                        if diff_pct < 5:  # <5% difference = agreement
                            agreements.append(field)
                        else:
                            conflicts.append({
                                "field": field,
                                "manual": manual_value,
                                "automated": auto_value,
                                "diff_pct": diff_pct
                            })
                    consolidated[field] = manual_value  # Prefer manual
                except Exception:
                    consolidated[field] = manual_value
            elif manual_value:
                # Only manual has data
                consolidated[field] = manual_value
            # If only automated has data, it's already in consolidated (from copy())

        # ====================================================================
        # SESSION 79: CRITICAL FIELD MAPPING & DERIVATION
        # ====================================================================
        # Handle field name mismatches between Perplexity and schema
        # Derive calculable fields from existing data
        # ====================================================================

        # Map: total_funding → funding_raised_usd
        if not consolidated.get("funding_raised_usd") and consolidated.get("total_funding"):
            total_funding = consolidated["total_funding"]
            # Parse string like "13500000" to int
            if isinstance(total_funding, str):
                total_funding = int(total_funding.replace(",", "").replace("$", "").replace("M", "000000"))
            consolidated["funding_raised_usd"] = total_funding
            logger.debug(f"   Mapped total_funding → funding_raised_usd: ${total_funding:,}")

        # Map: exchanges → listing_exchanges
        if not consolidated.get("listing_exchanges") and consolidated.get("exchanges"):
            exchanges = consolidated["exchanges"]

            # Normalize exchanges format (dict list → string list) for pipeline compatibility
            if isinstance(exchanges, list) and all(isinstance(ex, dict) for ex in exchanges):
                # Perplexity format: [{"name": "Binance", "type": "Alpha"}]
                # Pipeline expects: ["Binance", "MEXC"]
                normalized_exchanges = [ex.get("name") for ex in exchanges if ex.get("name")]
                if normalized_exchanges:
                    consolidated["listing_exchanges"] = normalized_exchanges
                    consolidated["exchanges"] = normalized_exchanges  # Also update exchanges for backward compat
                    logger.debug(f"   Normalized {len(normalized_exchanges)} exchanges to string list")
            elif isinstance(exchanges, list):
                # Already a string list
                consolidated["listing_exchanges"] = exchanges
                logger.debug(f"   Mapped exchanges → listing_exchanges: {len(exchanges)} exchanges")

        # Derive: fdv_mc_ratio_low and fdv_mc_ratio_high
        if not consolidated.get("fdv_mc_ratio_low") and consolidated.get("fdv_low") and consolidated.get("initial_market_cap_high"):
            # fdv_mc_ratio_low = fdv_low / initial_market_cap_high (best case for shorts)
            fdv_low = consolidated["fdv_low"]
            mc_high = consolidated["initial_market_cap_high"]
            if fdv_low and mc_high and mc_high > 0:
                consolidated["fdv_mc_ratio_low"] = round(fdv_low / mc_high, 2)
                logger.debug(f"   Derived fdv_mc_ratio_low: {consolidated['fdv_mc_ratio_low']}x")

        if not consolidated.get("fdv_mc_ratio_high") and consolidated.get("fdv_high") and consolidated.get("initial_market_cap_low"):
            # fdv_mc_ratio_high = fdv_high / initial_market_cap_low (worst case for shorts)
            fdv_high = consolidated["fdv_high"]
            mc_low = consolidated["initial_market_cap_low"]
            if fdv_high and mc_low and mc_low > 0:
                consolidated["fdv_mc_ratio_high"] = round(fdv_high / mc_low, 2)
                logger.debug(f"   Derived fdv_mc_ratio_high: {consolidated['fdv_mc_ratio_high']}x")

        # Map: vc_tier_assessment → investor_tier (IMPORTANT field)
        if not consolidated.get("investor_tier") and consolidated.get("vc_tier_assessment"):
            vc_tier = consolidated["vc_tier_assessment"]
            # Parse tier from assessment string (e.g., "WEAK tier 1 backing..." → "Tier 1")
            if isinstance(vc_tier, str):
                if "tier 1" in vc_tier.lower() or "tier-1" in vc_tier.lower():
                    consolidated["investor_tier"] = "Tier 1"
                elif "tier 2" in vc_tier.lower() or "tier-2" in vc_tier.lower():
                    consolidated["investor_tier"] = "Tier 2"
                elif "tier 3" in vc_tier.lower() or "tier-3" in vc_tier.lower():
                    consolidated["investor_tier"] = "Tier 3"
                else:
                    consolidated["investor_tier"] = "Mixed"
                logger.debug(f"   Mapped vc_tier_assessment → investor_tier: {consolidated['investor_tier']}")

        # SESSION 79B: Map Dropstab vesting_schedule → vesting_schedule (IMPORTANT field)
        if not consolidated.get("vesting_schedule") and dropstab_data and dropstab_data.get("vesting_schedule"):
            vesting_events = dropstab_data["vesting_schedule"]

            # Format: Convert array of events to human-readable schedule
            if vesting_events and isinstance(vesting_events, list):
                # Calculate summary
                tge_unlock = consolidated.get("tge_unlock_pct", 0)
                total_months = len([v for v in vesting_events if v.get("unlock_pct")])

                # Format: "TGE: 16%, Linear over 12 months (0.74%/month)"
                if total_months > 0:
                    monthly_avg = sum(v.get("unlock_pct", 0) for v in vesting_events) / total_months
                    vesting_schedule = f"TGE: {tge_unlock}%, Linear over {total_months} months ({monthly_avg:.2f}%/month)"
                else:
                    vesting_schedule = f"TGE: {tge_unlock}%"

                consolidated["vesting_schedule"] = vesting_schedule
                logger.debug(f"   Mapped Dropstab vesting_schedule: {vesting_schedule}")

        # SESSION 79B: Extract whitepaper_url from Perplexity data_sources_used (IMPORTANT field)
        if not consolidated.get("whitepaper_url") and manual_data:
            # Check if Perplexity has data_sources_used array
            data_sources = manual_data.get("data_sources_used", [])

            if data_sources and isinstance(data_sources, list):
                # Search for whitepaper reference
                import re
                for source in data_sources:
                    if isinstance(source, str) and "whitepaper" in source.lower():
                        # Extract URL pattern: "web:20 - irys.xyz whitepaper (...)"
                        # Pattern 1: Full URL (https://...)
                        # Pattern 2: Domain with path (domain.com/path)
                        # Pattern 3: Domain only (domain.com, domain.xyz, etc.)
                        url_match = re.search(r'(https?://[^\s]+|[\w\.-]+\.\w+(?:/[\w/\.-]*)?)', source)
                        if url_match:
                            url = url_match.group(1)
                            # Ensure it's a full URL
                            if not url.startswith("http"):
                                # Append /whitepaper path if just domain
                                if "/" not in url:
                                    url = f"https://{url}/whitepaper"
                                else:
                                    url = f"https://{url}"
                            consolidated["whitepaper_url"] = url
                            logger.debug(f"   Extracted whitepaper_url from Perplexity sources: {url}")
                            break

        # SESSION 79B: Map partial token_allocation from Perplexity (IMPORTANT field - partial data)
        if not consolidated.get("token_allocation") and manual_data:
            allocation_parts = []

            # Extract available allocation data
            if manual_data.get("community_allocation_pct"):
                allocation_parts.append(f"Community {manual_data['community_allocation_pct']}%")

            # Check for team/investor percentages if available
            if manual_data.get("team_allocation_pct"):
                allocation_parts.append(f"Team {manual_data['team_allocation_pct']}%")

            if manual_data.get("investor_allocation_pct"):
                allocation_parts.append(f"Investors {manual_data['investor_allocation_pct']}%")

            if allocation_parts:
                consolidated["token_allocation"] = ", ".join(allocation_parts) + " (partial data)"
                logger.debug(f"   Mapped partial token_allocation: {consolidated['token_allocation']}")

        # Calculate confidence
        total_fields = len(manual_priority_fields)
        fields_with_data = sum(1 for f in manual_priority_fields if consolidated.get(f))
        confidence = (fields_with_data / total_fields) * 100

        # Add cross-validation metadata
        consolidated["_cross_validation"] = {
            "perplexity_source": perplexity_file.name,
            "dropstab_source": dropstab_file.name if dropstab_data else None,
            "dropstab_confidence": dropstab_data.get("_data_confidence") if dropstab_data else None,
            "agreements": agreements,
            "conflicts": conflicts,
            "manual_fields": [f for f in manual_priority_fields if manual_data.get(f)],
            "automated_fields": [f for f in manual_priority_fields if automated_data.get(f)]
        }
        consolidated["_data_confidence"] = confidence
        consolidated["_source"] = "consolidated"

        print_success(f"   ✅ Data merged - confidence: {confidence:.0f}%")
        print_info(f"      Agreements: {len(agreements)}, Conflicts: {len(conflicts)}")

        if conflicts:
            print_warning(f"      ⚠️  Field conflicts detected (using manual values):")
            for conflict in conflicts[:3]:  # Show first 3
                print_warning(f"         • {conflict['field']}: manual={conflict['manual']}, auto={conflict['automated']}")

        return consolidated

    except json.JSONDecodeError as e:
        print_error(f"   ⚠️  Failed to load Perplexity data: {str(e)}")
        return automated_data
    except Exception as e:
        print_error(f"   ⚠️  Error during consolidation: {str(e)}")
        return automated_data


# =============================================================================
# SESSION 318: COINGECKO FETCHER (CryptoRank Replacement)
# =============================================================================

def fetch_token_data_from_unified(token: str) -> Optional[Dict[str, Any]]:
    """
    Fetch token data from unified multi-source fetcher for LISTED tokens.

    Session 346: Replaced CoinGecko with UnifiedTokenomicsFetcher (multi-source waterfall).

    Provides complete tokenomics for listed tokens:
    - FDV (Fully Diluted Valuation)
    - Market Cap
    - Circulating/Total/Max Supply
    - Float % (calculated)
    - Price, Volume
    - Listing exchanges

    Args:
        token: Token symbol (e.g., "BTC", "ETH", "MONAD")

    Returns:
        Dict in automated_data format compatible with data_consolidator.py

    Note:
        Only works for LISTED tokens (post-TGE). For pre-TGE tokens,
        use manual research or wait for listing.
    """
    # Import unified fetcher
    try:
        from src.data.fetchers.unified_tokenomics_fetcher import UnifiedTokenomicsFetcher
    except ImportError:
        print_error("Unified tokenomics fetcher not available - check imports")
        return None

    # Session 290: Redis caching (24h TTL - listed tokens change slowly)
    if REDIS_AVAILABLE:
        redis_cache = get_redis_cache()
        cache_key = f"unified:{token.upper()}"

        # Try to get from cache
        cached = redis_cache.get(cache_key, namespace="tge_data")
        if cached is not None:
            logger.debug(f"✅ Cache HIT: fetch_token_data_from_unified({token})")
            print_info(f"✅ Using cached unified data for {token}")
            return cached

        logger.debug(f"❌ Cache MISS: fetch_token_data_from_unified({token}) - fetching from APIs")

    print_info(f"🔍 Fetching token data from multiple sources for: {token}")

    try:
        fetcher = UnifiedTokenomicsFetcher()
        result = fetcher.fetch(token)

        # Extract data and add quality metadata
        data = result["data"]
        data["_data_quality"] = {
            "score": result["quality_score"],
            "primary_source": result["primary_source"],
            "sources_used": result["sources_used"],
        }

        if not data:
            print_warning(f"Token {token} not found in any source")
            print_info("Possible reasons:")
            print_info("  1. Token not yet listed (wait for listing)")
            print_info("  2. Incorrect token symbol (check spelling)")
            print_info("  3. Token delisted")
            return None

        # Session 290: Cache result for 24h
        if REDIS_AVAILABLE:
            redis_cache = get_redis_cache()
            cache_key = f"unified:{token.upper()}"
            redis_cache.set(cache_key, data, ttl_seconds=86400, namespace="tge_data")
            logger.debug(f"💾 Cached unified({token}) for 24h")

        print_success(f"✅ Found {data.get('symbol', token)} from {result['primary_source']} (quality: {result['quality_score']}%)")
        print_info(f"  Sources used: {', '.join(result['sources_used'])}")
        if data.get('price'):
            print_info(f"  Price: ${data.get('price', 'N/A')}")
        if data.get('market_cap'):
            print_info(f"  Market Cap: ${data.get('market_cap', 0):,.0f}")
        if data.get('fdv'):
            print_info(f"  FDV: ${data.get('fdv', 0):,.0f}")
        if data.get('float_pct'):
            print_info(f"  Float: {data['float_pct']:.1f}%")

        return data

    except Exception as e:
        print_error(f"Error fetching unified data: {str(e)}")
        logger.exception("Unified fetch failed")
        return None


def fetch_automated_token_data(token: str, prefer_source: str = "unified") -> Optional[Dict[str, Any]]:
    """
    Smart fetch function that tries multiple sources.

    Session 346: Replaced CoinGecko with UnifiedTokenomicsFetcher.
    Session 318: Intelligent fallback between sources.

    Strategy:
    1. Try Unified fetcher first (multi-source: CryptoRank → Binance → DexScreener)
    2. If not found, try CryptoRank (DEPRECATED, may fail)

    Args:
        token: Token symbol
        prefer_source: "unified" or "cryptorank" (default: unified)

    Returns:
        Token data dict or None

    Example:
        # Auto-select best source (unified multi-source)
        data = fetch_automated_token_data("BTC")

        # Force CryptoRank (for pre-TGE tokens)
        data = fetch_automated_token_data("UPCOMING_TOKEN", prefer_source="cryptorank")
    """
    sources = ["unified", "cryptorank"] if prefer_source == "unified" else ["cryptorank", "unified"]

    for source in sources:
        try:
            if source == "unified":
                print_info(f"📊 Trying unified multi-source fetcher for {token}...")
                data = fetch_token_data_from_unified(token)
                if data:
                    return data
            elif source == "cryptorank":
                print_info(f"📊 Trying CryptoRank for {token}...")
                data = fetch_tge_data_from_cryptorank(token)
                if data:
                    return data
        except Exception as e:
            print_warning(f"⚠️  {source} fetch failed: {e}")
            continue

    print_error(f"❌ Could not fetch {token} from any source")
    return None
