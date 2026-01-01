#!/usr/bin/env python3
"""
CryptoRank Web Fetcher - Session 79K

DEPRECATED: Use src.data.fetchers.token_data.fetch_cryptorank_web instead.
    from src.data.fetchers import fetch_cryptorank_web

This module will be removed in a future version.

---

Extracts TGE data directly from CryptoRank website HTML when API doesn't have the token.

Problem Solved:
- CryptoRank API doesn't index pre-TGE tokens
- CryptoRank WEBSITE has the data (accessible via browser)
- This scraper extracts data from HTML, bypassing the API limitation

Data Extracted:
- TGE date
- Total supply, circulating supply at TGE
- Float percentage
- FDV estimates
- Funding rounds and investors
- Token allocation (tokenomics)
- Categories, blockchain

Priority: Tier 1.5 (after API fails, before Perplexity)

Usage (DEPRECATED):
    from src.data.fetchers.cryptorank_web_fetcher import fetch_cryptorank_web

Usage (NEW):
    from src.data.fetchers import fetch_cryptorank_web

Created: 2025-12-01 (Session 79K)
Deprecated: 2025-12-26 (Session 256+ Refactoring)
"""
import warnings
warnings.warn(
    "scripts.helpers.cryptorank_web_fetcher is deprecated. "
    "Use src.data.fetchers.fetch_cryptorank_web instead.",
    DeprecationWarning,
    stacklevel=2
)

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional
from bs4 import BeautifulSoup
import requests

logger = logging.getLogger(__name__)


def fetch_cryptorank_web(
    token: str,
    url: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Fetch token data directly from CryptoRank website HTML.

    This is the fallback when CryptoRank API doesn't have the token indexed
    (common for pre-TGE tokens).

    Args:
        token: Token symbol (e.g., "RAYLS")
        url: Optional explicit URL (if None, tries common patterns)

    Returns:
        Dict with TGE data, or None if not found

    Example Output:
        {
            "token_symbol": "RAYLS",
            "token_name": "Rayls",
            "tge_date": "2025-12-01",
            "total_supply": 10000000000,
            "circulating_supply_at_tge": 1500000000,
            "float_percent": 15.0,
            "funding_raised_usd": 38000000,
            "investors": ["Tether", "Framework Ventures", "ParaFi Capital"],
            "token_allocation": {"community": 50, "investors": 22, ...},
            "category": "Layer 1",
            "_source": "cryptorank_web",
            "_source_url": "https://cryptorank.io/price/rayls"
        }
    """
    try:
        # Try to read token_name from consolidated.json
        # This is critical for tokens where CryptoRank uses name in URL (e.g., /price/talisman not /price/SEEK)
        token_name = None
        try:
            from pathlib import Path
            project_root = Path(__file__).parent.parent.parent
            consolidated_path = project_root / "data" / "tokens" / token.upper() / "consolidated.json"
            if consolidated_path.exists():
                with open(consolidated_path) as f:
                    data = json.load(f)
                    token_name = data.get("token_name")
                    if token_name and token_name != token.upper():
                        logger.info(f"Found token_name '{token_name}' for {token} in consolidated.json")
        except Exception as e:
            logger.debug(f"Could not read token_name from consolidated.json: {e}")

        # Build URL patterns to try
        token_lower = token.lower()
        url_patterns = []

        if url:
            url_patterns.append(url)
        else:
            # Common URL patterns on CryptoRank
            # If we have token_name, try it FIRST (most likely to succeed)
            if token_name:
                name_lower = token_name.lower()
                url_patterns.extend([
                    f"https://cryptorank.io/price/{name_lower}",
                    f"https://cryptorank.io/ico/{name_lower}",
                ])

            # Then try symbol-based patterns
            url_patterns.extend([
                f"https://cryptorank.io/price/{token_lower}",
                f"https://cryptorank.io/price/{token_lower}-network",
                f"https://cryptorank.io/price/{token_lower}-token",
                f"https://cryptorank.io/ico/{token_lower}",
            ])

        for try_url in url_patterns:
            logger.debug(f"Trying CryptoRank URL: {try_url}")

            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.5",
                }
                response = requests.get(try_url, headers=headers, timeout=20)

                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, "html.parser")

                    # Check if page has actual content (not a 404 or redirect)
                    if _is_valid_cryptorank_page(soup, token):
                        logger.info(f"Found CryptoRank page for {token}")
                        return _parse_cryptorank_html(soup, token, try_url)
                    else:
                        logger.debug(f"Page at {try_url} doesn't contain {token} data")

                elif response.status_code == 404:
                    logger.debug(f"404 at {try_url}")
                else:
                    logger.debug(f"Status {response.status_code} at {try_url}")

            except requests.RequestException as e:
                logger.debug(f"Request failed for {try_url}: {e}")
                continue

        logger.warning(f"Could not find CryptoRank web data for {token}")
        return None

    except Exception as e:
        logger.error(f"CryptoRank web fetch failed for {token}: {e}")
        return None


def _is_valid_cryptorank_page(soup: BeautifulSoup, token: str) -> bool:
    """Check if page contains data for the requested token."""
    # Look for token name in title or header
    title = soup.find("title")
    if title and token.upper() in title.get_text().upper():
        return True

    # Look for token symbol in meta tags or headers
    h1 = soup.find("h1")
    if h1 and token.upper() in h1.get_text().upper():
        return True

    # Check for __NEXT_DATA__ with token data
    next_data = soup.find("script", {"id": "__NEXT_DATA__"})
    if next_data:
        try:
            data = json.loads(next_data.string)
            # Check if pageProps contains token data
            page_props = data.get("props", {}).get("pageProps", {})
            if page_props.get("coin") or page_props.get("currency"):
                return True
        except json.JSONDecodeError:
            pass

    return False


def _parse_cryptorank_html(
    soup: BeautifulSoup,
    token: str,
    url: str
) -> Optional[Dict[str, Any]]:
    """
    Parse CryptoRank HTML page to extract TGE data.

    Handles both:
    1. __NEXT_DATA__ JSON (preferred - structured data)
    2. HTML scraping (fallback - less reliable)
    """
    result = {
        "token_symbol": token.upper(),
        "token_name": None,
        "tge_date": None,
        "total_supply": None,
        "circulating_supply_at_tge": None,
        "float_percent": None,
        "fdv_low": None,
        "fdv_high": None,
        "funding_raised_usd": None,
        "investors": [],
        "vc_investors": [],
        "funding_rounds": [],
        "token_allocation": {},
        "category": None,
        "categories": [],
        "blockchain": None,
        "project_description": None,
        "_source": "cryptorank_web",
        "_source_url": url,
        "_fetched_at": datetime.utcnow().isoformat() + "Z"
    }

    # Try to extract from __NEXT_DATA__ first (most reliable)
    next_data_script = soup.find("script", {"id": "__NEXT_DATA__"})
    if next_data_script:
        try:
            next_data = json.loads(next_data_script.string)
            _extract_from_next_data(next_data, result)
        except json.JSONDecodeError as e:
            logger.debug(f"Failed to parse __NEXT_DATA__: {e}")

    # Supplement with HTML scraping
    _extract_from_html(soup, result)

    # Calculate fields if possible
    _calculate_derived_fields(result)

    # Calculate confidence score
    fields_found = sum([
        1 if result["token_name"] else 0,
        1 if result["tge_date"] else 0,
        1 if result["total_supply"] else 0,
        1 if result["float_percent"] is not None else 0,
        1 if result["funding_raised_usd"] else 0,
        1 if len(result["investors"]) > 0 else 0,
        1 if result["token_allocation"] else 0,
    ])
    result["_data_confidence"] = int((fields_found / 7) * 100)

    # Log what we found
    logger.info(f"CryptoRank web data for {token}: {result['_data_confidence']}% confidence")
    if result["tge_date"]:
        logger.info(f"  TGE Date: {result['tge_date']}")
    if result["float_percent"]:
        logger.info(f"  Float: {result['float_percent']}%")
    if result["funding_raised_usd"]:
        logger.info(f"  Funding: ${result['funding_raised_usd']:,}")

    return result


def _extract_from_next_data(next_data: Dict, result: Dict):
    """Extract data from CryptoRank's __NEXT_DATA__ JSON."""
    page_props = next_data.get("props", {}).get("pageProps", {})

    # Try different data structures
    coin_data = page_props.get("coin") or page_props.get("currency") or page_props.get("project") or {}

    # Basic info
    result["token_name"] = coin_data.get("name")
    result["project_description"] = coin_data.get("description")

    # Supply and float
    result["total_supply"] = _safe_int(coin_data.get("totalSupply"))
    result["circulating_supply_at_tge"] = _safe_int(
        coin_data.get("circulatingSupply") or
        coin_data.get("initialCirculatingSupply")
    )

    # Float percentage
    if coin_data.get("circulatingSupplyPercent"):
        result["float_percent"] = _safe_float(coin_data.get("circulatingSupplyPercent"))
    elif coin_data.get("tgeUnlockPercent"):
        result["float_percent"] = _safe_float(coin_data.get("tgeUnlockPercent"))

    # Category
    category = coin_data.get("category") or coin_data.get("type")
    if category:
        if isinstance(category, dict):
            result["category"] = category.get("name")
        else:
            result["category"] = category

    categories = coin_data.get("categories", [])
    if categories:
        result["categories"] = [c.get("name") if isinstance(c, dict) else c for c in categories]

    # Blockchain
    platforms = coin_data.get("platforms", []) or coin_data.get("tokens", [])
    if platforms:
        platform = platforms[0] if isinstance(platforms, list) else platforms
        result["blockchain"] = platform.get("name") or platform.get("platform")

    # Funding data
    fundraising = coin_data.get("fundraising", []) or coin_data.get("fundingRounds", [])
    if fundraising:
        _extract_funding_data(fundraising, result)

    # ICO/TGE data
    ico_data = coin_data.get("ico") or page_props.get("ico") or {}
    if ico_data:
        _extract_ico_data(ico_data, result)

    # Token allocation/tokenomics
    tokenomics = coin_data.get("tokenomics") or coin_data.get("tokenDistribution", {})
    if tokenomics:
        _extract_tokenomics(tokenomics, result)


def _extract_funding_data(fundraising: List, result: Dict):
    """Extract funding rounds and investor data."""
    total_raised = 0.0
    all_investors = set()

    for round_data in fundraising:
        round_type = round_data.get("name") or round_data.get("type") or "Unknown"
        amount = _safe_float(round_data.get("raisedAmount") or round_data.get("amount"))

        round_info = {
            "type": round_type,
            "amount": int(amount) if amount else None,
            "valuation": _safe_int(round_data.get("valuation")),
            "date": round_data.get("date") or round_data.get("endDate"),
            "lead_investors": []
        }

        # Extract investors
        for inv in round_data.get("investors", []):
            inv_name = inv.get("name") if isinstance(inv, dict) else str(inv)
            if inv_name:
                all_investors.add(inv_name)
                if isinstance(inv, dict) and (inv.get("isLead") or inv.get("lead")):
                    round_info["lead_investors"].append(inv_name)

        if amount:
            total_raised += amount

        result["funding_rounds"].append(round_info)

    result["investors"] = list(all_investors)
    result["vc_investors"] = list(all_investors)
    if total_raised > 0:
        result["funding_raised_usd"] = int(total_raised)


def _extract_ico_data(ico_data: Dict, result: Dict):
    """Extract ICO/TGE specific data."""
    # TGE date
    tge_date = (
        ico_data.get("startDate") or
        ico_data.get("listingDate") or
        ico_data.get("releaseDate") or
        ico_data.get("tgeDate")
    )
    if tge_date and not result["tge_date"]:
        result["tge_date"] = tge_date

    # Price
    price = ico_data.get("price") or ico_data.get("salePrice")
    if price:
        result["listing_price_low"] = price
        result["listing_price_high"] = price

    # FDV
    fdv = ico_data.get("fdv") or ico_data.get("fullyDilutedValuation")
    if fdv:
        result["fdv_low"] = fdv
        result["fdv_high"] = fdv


def _extract_tokenomics(tokenomics: Dict, result: Dict):
    """Extract token allocation data."""
    allocation = tokenomics.get("allocation", {}) or tokenomics.get("distribution", [])

    if isinstance(allocation, list):
        for item in allocation:
            if isinstance(item, dict):
                name = item.get("name") or item.get("category") or item.get("type")
                pct = _safe_float(item.get("percent") or item.get("percentage") or item.get("share"))
                if name and pct is not None:
                    # Convert decimal to percentage if needed
                    if pct > 0 and pct < 1:
                        pct = pct * 100
                    result["token_allocation"][name.lower().replace(" ", "_")] = round(pct, 2)

    elif isinstance(allocation, dict):
        for key, value in allocation.items():
            if not key.startswith("_"):
                pct = _safe_float(value)
                if pct is not None:
                    if pct > 0 and pct < 1:
                        pct = pct * 100
                    result["token_allocation"][key.lower().replace(" ", "_")] = round(pct, 2)


def _extract_from_html(soup: BeautifulSoup, result: Dict):
    """Extract data from HTML when __NEXT_DATA__ is incomplete."""
    text = soup.get_text()

    # TGE/Launch date patterns
    if not result["tge_date"]:
        date_patterns = [
            r'(?:TGE|Launch|Listing)\s*(?:Date)?[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{4})',
            r'(?:TGE|Launch|Listing)\s*(?:Date)?[:\s]*(\w+\s+\d{1,2},?\s+\d{4})',
            r'(\d{4}-\d{2}-\d{2}).*?(?:TGE|launch|listing)',
        ]
        for pattern in date_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                result["tge_date"] = match.group(1)
                logger.debug(f"Found TGE date via HTML: {result['tge_date']}")
                break

    # Funding patterns
    if not result["funding_raised_usd"]:
        funding_patterns = [
            r'(?:Funds?\s+[Rr]aised|Total\s+[Rr]aised)[:\s]*\$?\s*([\d.]+)\s*([KMBT])',
            r'\$\s*([\d.]+)\s*([KMBT])\s*(?:raised|funding)',
        ]
        for pattern in funding_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                result["funding_raised_usd"] = _parse_dollar_amount(
                    f"${match.group(1)}{match.group(2)}"
                )
                logger.debug(f"Found funding via HTML: ${result['funding_raised_usd']:,}")
                break

    # Float percentage patterns
    if result["float_percent"] is None:
        float_patterns = [
            r'(?:Float|Circulating|TGE\s+Unlock)[:\s]*([\d.]+)\s*%',
            r'([\d.]+)\s*%\s*(?:float|circulating|unlocked)',
        ]
        for pattern in float_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                result["float_percent"] = _safe_float(match.group(1))
                logger.debug(f"Found float via HTML: {result['float_percent']}%")
                break

    # Total supply patterns
    if not result["total_supply"]:
        supply_patterns = [
            r'(?:Total|Max)\s+Supply[:\s]*([\d,.]+)\s*([KMBT])?',
            r'([\d,.]+)\s*([KMBT])?\s*(?:total|max)\s+supply',
        ]
        for pattern in supply_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                num_str = match.group(1).replace(",", "")
                mult = match.group(2) if len(match.groups()) > 1 else None
                result["total_supply"] = _parse_supply_amount(num_str, mult)
                logger.debug(f"Found total supply via HTML: {result['total_supply']:,}")
                break


def _calculate_derived_fields(result: Dict):
    """Calculate derived fields from extracted data."""
    # Calculate float_percent if we have supply data
    if (result["float_percent"] is None and
        result["total_supply"] and
        result["circulating_supply_at_tge"]):
        result["float_percent"] = round(
            (result["circulating_supply_at_tge"] / result["total_supply"]) * 100,
            2
        )

    # Calculate circulating supply if we have float and total
    if (result["circulating_supply_at_tge"] is None and
        result["total_supply"] and
        result["float_percent"]):
        result["circulating_supply_at_tge"] = int(
            result["total_supply"] * (result["float_percent"] / 100)
        )


def _parse_dollar_amount(amount_str: str) -> Optional[int]:
    """Parse dollar amount strings like "$38M", "$431.50M"."""
    try:
        clean = amount_str.replace("$", "").replace(" ", "").replace(",", "").upper()
        match = re.match(r"([\d.]+)([KMBT])?", clean)
        if not match:
            return None

        number = float(match.group(1))
        multiplier = match.group(2)

        multipliers = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000, "T": 1_000_000_000_000}
        if multiplier:
            number *= multipliers.get(multiplier, 1)

        return int(number)
    except Exception:
        return None


def _parse_supply_amount(num_str: str, multiplier: Optional[str]) -> Optional[int]:
    """Parse supply amount like "10,000,000,000" or "10B"."""
    try:
        number = float(num_str.replace(",", ""))
        if multiplier:
            multipliers = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000, "T": 1_000_000_000_000}
            number *= multipliers.get(multiplier.upper(), 1)
        return int(number)
    except Exception:
        return None


def _safe_int(value: Any) -> Optional[int]:
    """Safely convert to int."""
    try:
        if value is None:
            return None
        return int(float(value))
    except (ValueError, TypeError):
        return None


def _safe_float(value: Any) -> Optional[float]:
    """Safely convert to float."""
    try:
        if value is None:
            return None
        return float(value)
    except (ValueError, TypeError):
        return None


# CLI for testing
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if len(sys.argv) < 2:
        print("Usage: python cryptorank_web_fetcher.py <TOKEN> [URL]")
        print("\nExamples:")
        print("  python cryptorank_web_fetcher.py RAYLS")
        print("  python cryptorank_web_fetcher.py RAYLS https://cryptorank.io/price/rayls")
        sys.exit(1)

    token = sys.argv[1].upper()
    url = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"\n{'='*70}")
    print(f"CRYPTORANK WEB FETCH: {token}")
    print(f"{'='*70}\n")

    result = fetch_cryptorank_web(token, url)

    if result:
        print(f"\n{'='*70}")
        print("RESULT")
        print(f"{'='*70}")
        # Remove internal fields for display
        display_result = {k: v for k, v in result.items() if not k.startswith("_") or k == "_data_confidence"}
        print(json.dumps(display_result, indent=2))
    else:
        print(f"\n Failed to fetch CryptoRank web data for {token}")
        sys.exit(1)
