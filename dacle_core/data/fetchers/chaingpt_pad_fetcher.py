#!/usr/bin/env python3
"""
ChainGPT Pad Data Fetcher (Session 85 - Phase 2B)

DEPRECATED: Use src.data.fetchers.token_data module instead.
Session 256: Marked for migration to src/data/fetchers/token_data.py

Extracts high-quality pre-TGE data from ChainGPT Pad launchpad.

ChainGPT Pad provides excellent structured data for:
- Exact IDO prices (real market data, not predictions)
- Official TGE dates (from launchpad schedule)
- Detailed vesting schedules (weekly unlock phases)
- FDV calculations (from actual IDO valuations)
- Total raise amounts

Why ChainGPT Pad is valuable:
- Data quality: HIGH (actual launchpad data)
- Confidence: HIGH (verified IDO information)
- Coverage: Active/recent IDOs (last 6-12 months)
- Format: Structured JSON (easy to parse)

Usage:
    from dacle_core.data.fetchers.chaingpt_pad_fetcher import fetch_chaingpt_data

    data = fetch_chaingpt_data("SEEK", "Talisman")
    print(f"IDO Price: ${data['listing_price_low']}")

Created: 2025-12-04 (Session 85 - Automated Field Extraction)
Priority: P1 - Add high-quality launchpad data source
GitHub Actions: ✅ Compatible (simple HTTP requests, no browser)
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional
from datetime import datetime
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def fetch_chaingpt_data(symbol: str, name: str = None) -> Optional[Dict[str, Any]]:
    """
    Fetch token data from ChainGPT Pad launchpad

    Args:
        symbol: Token symbol (e.g., "SEEK", "MONAD")
        name: Token name (e.g., "Talisman") - helps with URL matching

    Returns:
        Dict with IDO price, TGE date, FDV, vesting, and funding data, or None if not found

    Example Output:
        {
            "symbol": "SEEK",
            "name": "Talisman",
            "listing_price_low": 0.30,
            "listing_price_high": 0.30,
            "tge_date": "2025-12-30T00:00:00Z",
            "fdv": 60000000,
            "vesting_schedule": "25% unlock, 6 months linear vesting",
            "total_raised": 2000000,
            "_source": "chaingpt_pad",
            "_source_url": "https://pad.chaingpt.org/pools/talisman",
            "_data_confidence": 90
        }
    """
    try:
        # Try common URL patterns
        symbol_lower = symbol.lower()
        name_lower = name.lower() if name else None

        url_patterns = [
            f"https://pad.chaingpt.org/pools/{name_lower}" if name_lower else None,
            f"https://pad.chaingpt.org/pools/{symbol_lower}",
            f"https://pad.chaingpt.org/pools/{name_lower}-{symbol_lower}" if name_lower else None,
        ]

        # Filter out None values
        url_patterns = [url for url in url_patterns if url]

        for url in url_patterns:
            logger.debug(f"Trying ChainGPT Pad URL: {url}")

            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
                }
                response = requests.get(url, headers=headers, timeout=15)

                if response.status_code == 200:
                    # ChainGPT Pad is a React app - look for __NEXT_DATA__ or data in HTML
                    soup = BeautifulSoup(response.text, "html.parser")

                    # Check if page exists (ChainGPT returns 200 even for 404s sometimes)
                    if _is_valid_chaingpt_page(soup):
                        logger.info(f"✓ Found ChainGPT Pad data for {symbol}")
                        return _parse_chaingpt_data(soup, symbol, url)
                    else:
                        logger.debug(f"Invalid ChainGPT Pad page at {url}")

            except requests.RequestException as e:
                logger.debug(f"Request failed for {url}: {e}")
                continue

        logger.info(f"ChainGPT Pad: No data found for {symbol}")
        return None

    except Exception as e:
        logger.error(f"ChainGPT Pad fetch error for {symbol}: {e}")
        return None


def _is_valid_chaingpt_page(soup: BeautifulSoup) -> bool:
    """Check if this is a valid ChainGPT Pad pool page"""
    # Look for key elements that indicate a pool page
    # ChainGPT Pad uses Next.js with __NEXT_DATA__ script
    next_data = soup.find("script", id="__NEXT_DATA__")
    if next_data:
        return True

    # Fallback: look for pool-specific elements
    has_pool_info = soup.find(text=re.compile("Token Sale|IDO|Pool Details", re.IGNORECASE))
    return has_pool_info is not None


def _parse_chaingpt_data(soup: BeautifulSoup, symbol: str, url: str) -> Dict[str, Any]:
    """
    Parse token data from ChainGPT Pad HTML/JSON

    ChainGPT Pad structure:
    - Next.js app with __NEXT_DATA__ JSON in script tag
    - Contains pool info, token info, sale info, vesting details
    """
    try:
        # Initialize result
        result = {
            "symbol": symbol.upper(),
            "name": None,
            "listing_price_low": None,
            "listing_price_high": None,
            "tge_date": None,
            "fdv": None,
            "fdv_low": None,
            "fdv_high": None,
            "vesting_schedule": None,
            "unlock_schedule": {},
            "total_raised": None,
            "total_supply": None,
            "farming_sources": ["ChainGPT Pad"],
            "_source": "chaingpt_pad",
            "_source_url": url,
            "_data_confidence": 0
        }

        # Try to extract __NEXT_DATA__ JSON
        next_data_script = soup.find("script", id="__NEXT_DATA__")
        if next_data_script:
            try:
                next_data = json.loads(next_data_script.string)

                # Extract data from Next.js props
                # Structure: next_data['props']['pageProps']['pool']
                page_props = next_data.get("props", {}).get("pageProps", {})
                pool_data = page_props.get("pool", {})

                if pool_data:
                    result = _extract_from_pool_json(pool_data, result, symbol)

            except json.JSONDecodeError as e:
                logger.debug(f"Failed to parse __NEXT_DATA__: {e}")

        # Fallback: Parse HTML elements if JSON extraction failed
        if result["_data_confidence"] == 0:
            result = _extract_from_html(soup, result)

        # Calculate confidence score
        fields_found = sum([
            1 if result["name"] else 0,
            1 if result["listing_price_low"] else 0,
            1 if result["tge_date"] else 0,
            1 if result["fdv"] else 0,
            1 if result["vesting_schedule"] else 0,
            1 if result["total_raised"] else 0,
        ])
        result["_data_confidence"] = int((fields_found / 6) * 100)

        logger.info(f"✓ ChainGPT Pad data extracted for {symbol}: {result['_data_confidence']}% confidence")
        if result["listing_price_low"]:
            logger.info(f"   IDO Price: ${result['listing_price_low']}")
        if result["tge_date"]:
            logger.info(f"   TGE Date: {result['tge_date']}")
        if result["fdv"]:
            logger.info(f"   FDV: ${result['fdv']:,.0f}")

        return result

    except Exception as e:
        logger.error(f"Failed to parse ChainGPT Pad data: {e}")
        return None


def _extract_from_pool_json(pool_data: Dict, result: Dict, symbol: str) -> Dict:
    """Extract data from ChainGPT Pad pool JSON structure"""
    try:
        # Token name
        result["name"] = pool_data.get("name") or pool_data.get("token_name")

        # IDO price (token_conversion_rate)
        ido_price = pool_data.get("token_conversion_rate")
        if ido_price:
            result["listing_price_low"] = float(ido_price)
            result["listing_price_high"] = float(ido_price)

        # TGE date (release_time - Unix timestamp)
        release_time = pool_data.get("release_time")
        if release_time:
            try:
                tge_datetime = datetime.fromtimestamp(release_time)
                result["tge_date"] = tge_datetime.strftime("%Y-%m-%dT%H:%M:%SZ")
            except (ValueError, TypeError):
                pass

        # FDV
        fdv = pool_data.get("fdv")
        if fdv:
            result["fdv"] = float(fdv)
            # Use same value for both low/high since it's from official IDO
            result["fdv_low"] = float(fdv)
            result["fdv_high"] = float(fdv)

        # Total supply
        total_supply = pool_data.get("total_supply") or pool_data.get("max_supply")
        if total_supply:
            result["total_supply"] = int(total_supply)

        # Total raised
        total_raised = pool_data.get("total_raised") or pool_data.get("hard_cap")
        if total_raised:
            result["total_raised"] = float(total_raised)

        # Vesting schedule (claim_config)
        claim_config = pool_data.get("claim_config", {})
        if claim_config:
            vesting_info = _parse_vesting_from_claim_config(claim_config)
            if vesting_info:
                result["vesting_schedule"] = vesting_info["human_readable"]
                result["unlock_schedule"] = vesting_info["structured"]

        logger.debug(f"Extracted from pool JSON: {len([k for k, v in result.items() if v])} fields")

    except Exception as e:
        logger.debug(f"Failed to extract from pool JSON: {e}")

    return result


def _parse_vesting_from_claim_config(claim_config: Dict) -> Optional[Dict]:
    """
    Parse vesting schedule from ChainGPT claim_config

    ChainGPT provides detailed vesting with:
    - phases: Array of {unlock_percentage, duration_days}
    - Example: 181 phases over 175 days
    """
    try:
        phases = claim_config.get("phases", [])
        if not phases:
            return None

        # Get first and last phases
        first_phase = phases[0]
        last_phase = phases[-1]

        tge_unlock_pct = first_phase.get("unlock_percentage", 0)
        total_days = sum(p.get("duration_days", 0) for p in phases)
        total_months = round(total_days / 30)

        # Determine vesting type (linear if phases are evenly distributed)
        is_linear = len(set(p.get("unlock_percentage") for p in phases[1:])) > 5
        vesting_type = "linear_weekly" if is_linear else "stepped"

        return {
            "human_readable": f"{tge_unlock_pct}% unlock, {total_months} months linear vesting",
            "structured": {
                "tge_unlock_pct": tge_unlock_pct,
                "cliff_months": 0,
                "vesting_months": total_months,
                "vesting_type": vesting_type,
                "total_phases": len(phases),
                "raw_schedule": f"{tge_unlock_pct}% unlock, {len(phases)} phases over {total_days} days"
            }
        }

    except Exception as e:
        logger.debug(f"Failed to parse vesting from claim_config: {e}")
        return None


def _extract_from_html(soup: BeautifulSoup, result: Dict) -> Dict:
    """Fallback: Extract data from HTML elements if JSON parsing failed"""
    try:
        # This is a fallback for when __NEXT_DATA__ is not available
        # ChainGPT Pad is primarily a React app, so HTML parsing is less reliable

        # Look for token name in title or h1
        title = soup.find("title")
        if title:
            result["name"] = title.get_text().split("|")[0].strip()

        # Look for price patterns in text
        price_pattern = re.compile(r'\$?(\d+\.?\d*)\s*(?:USDT|USD|per\s+token)', re.IGNORECASE)
        price_matches = soup.find_all(text=price_pattern)
        if price_matches:
            for match in price_matches:
                price = price_pattern.search(match)
                if price:
                    result["listing_price_low"] = float(price.group(1))
                    result["listing_price_high"] = float(price.group(1))
                    break

        logger.debug("Fallback HTML extraction completed (limited data)")

    except Exception as e:
        logger.debug(f"HTML extraction failed: {e}")

    return result
