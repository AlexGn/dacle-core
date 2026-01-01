#!/usr/bin/env python3
"""
Dropstab Data Fetcher

DEPRECATED: Use src.data.fetchers.token_data.fetch_dropstab_data instead.
    from src.data.fetchers import fetch_dropstab_data

---

Extracts funding, vesting, and tokenomics data from Dropstab.com for cross-validation.

Dropstab provides:
- Total funding raised
- Investor list with tiers
- Vesting schedules
- Token allocation breakdown
- Circulating supply metrics

Usage (NEW):
    from src.data.fetchers import fetch_dropstab_data

Usage (DEPRECATED):
    from src.data.fetchers.dropstab_fetcher import fetch_dropstab_data

Created: 2025-11-25 (Session 51.5 Continuation - Data Quality Improvements)
Deprecated: 2025-12-26 (Session 256+ Refactoring)
"""
import warnings
warnings.warn(
    "scripts.helpers.dropstab_fetcher is deprecated. "
    "Use src.data.fetchers.fetch_dropstab_data instead.",
    DeprecationWarning,
    stacklevel=2
)

import json
import logging
import re
from typing import Any, Dict, List, Optional
from bs4 import BeautifulSoup
import requests

logger = logging.getLogger(__name__)


def fetch_dropstab_data(token: str) -> Optional[Dict[str, Any]]:
    """
    Fetch token data from Dropstab.com

    Args:
        token: Token symbol (e.g., "IRYS", "MONAD")

    Returns:
        Dict with funding, vesting, and tokenomics data, or None if not found

    Example Output:
        {
            "symbol": "MONAD",
            "name": "Monad",
            "total_funding": 431500000,  # $431.5M
            "funding_rounds": [
                {"round": "Seed", "amount": 19000000, "date": "2023-02"},
                {"round": "Series A", "amount": 225000000, "date": "2024-04"}
            ],
            "investors": [
                {"name": "Paradigm", "tier": "tier_1_lead"},
                {"name": "Dragonfly", "tier": "tier_1"}
            ],
            "total_supply": 100000000000,
            "circulating_supply_percent": 10.83,
            "vesting_schedule": [
                {"date": "2025-11", "amount": 49330000000, "percent": 49.30},
                {"date": "2026-11", "amount": 16620000000, "percent": 16.62}
            ],
            "_source": "dropstab",
            "_data_confidence": 85
        }
    """
    try:
        # Try common URL patterns
        token_lower = token.lower()
        url_patterns = [
            f"https://dropstab.com/coins/{token_lower}",
            f"https://dropstab.com/coins/{token_lower}-token",
            f"https://dropstab.com/coins/{token_lower}-network"
        ]

        for url in url_patterns:
            logger.debug(f"Trying Dropstab URL: {url}")

            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
                }
                response = requests.get(url, headers=headers, timeout=15)

                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, "html.parser")

                    # Extract data from __NEXT_DATA__ script tag
                    next_data_script = soup.find("script", {"id": "__NEXT_DATA__", "type": "application/json"})

                    if next_data_script:
                        logger.info(f"✓ Found Dropstab data for {token}")
                        return _parse_dropstab_data(next_data_script.string, token, url, soup)
                    else:
                        logger.debug(f"No __NEXT_DATA__ found at {url}")

            except requests.RequestException as e:
                logger.debug(f"Request failed for {url}: {e}")
                continue

        logger.warning(f"Could not find Dropstab data for {token}")
        return None

    except Exception as e:
        logger.error(f"Dropstab fetch failed for {token}: {e}")
        return None


def _parse_dropstab_data(json_str: str, token: str, url: str, soup: BeautifulSoup) -> Optional[Dict[str, Any]]:
    """
    Parse Dropstab __NEXT_DATA__ JSON and HTML

    Args:
        json_str: Raw JSON string from __NEXT_DATA__ script tag
        token: Token symbol
        url: Source URL
        soup: BeautifulSoup object for HTML parsing

    Returns:
        Parsed data dict
    """
    try:
        next_data = json.loads(json_str)
        page_props = next_data.get("props", {}).get("pageProps", {})
        coin_data = page_props.get("coin", {})

        if not coin_data:
            logger.warning(f"No coin data in Dropstab JSON for {token}")
            return None

        # Initialize result
        result = {
            "symbol": token.upper(),
            "name": None,
            "total_funding": None,
            "funding_rounds": [],
            "investors": [],
            "total_supply": None,
            "circulating_supply_percent": None,
            "vesting_schedule": [],
            "whitepaper_url": None,  # Session 85: Added automated extraction
            "farming_sources": [],   # Session 85: Added automated extraction
            "_source": "dropstab",
            "_source_url": url,
            "_data_confidence": 0
        }

        # Extract token name
        result["name"] = coin_data.get("name") or coin_data.get("title")

        # Extract funding data (try JSON first, then HTML)
        funds_raised_str = coin_data.get("funds_raised") or coin_data.get("fundsRaised")
        if funds_raised_str:
            logger.debug(f"   Found funding in JSON: {funds_raised_str}")
            result["total_funding"] = _parse_dollar_amount(funds_raised_str)
        else:
            # Try to extract from HTML (often displayed in fundraising section)
            logger.debug("   No funding in JSON, trying HTML extraction...")
            funding_text = _extract_funding_from_html(soup)
            if funding_text:
                logger.info(f"   ✓ Found funding in HTML: {funding_text}")
                result["total_funding"] = _parse_dollar_amount(funding_text)
            else:
                logger.debug("   No funding found in HTML either")

        # Extract token metrics
        result["total_supply"] = _safe_int(coin_data.get("totalSupply"))
        result["circulating_supply_percent"] = _safe_float(coin_data.get("circulatingSupplyPercent"))

        # Extract investor data (if available in page)
        # Note: Investors are often in a separate section, may need HTML parsing
        investors_data = coin_data.get("investors", [])
        if investors_data:
            for inv in investors_data:
                result["investors"].append({
                    "name": inv.get("name"),
                    "tier": inv.get("tier", "unknown")
                })

        # Extract vesting schedule (try JSON first, then HTML)
        vesting_data = coin_data.get("vestingSchedule", [])
        if vesting_data:
            for event in vesting_data:
                result["vesting_schedule"].append({
                    "date": event.get("date"),
                    "amount": _safe_int(event.get("amount")),
                    "percent": _safe_float(event.get("percent"))
                })
        else:
            # Try to extract from HTML (often in vesting section table)
            vesting_events = _extract_vesting_from_html(soup)
            if vesting_events:
                result["vesting_schedule"] = vesting_events

        # Session 85: Extract whitepaper URL (try JSON first, then HTML)
        whitepaper_link = coin_data.get("whitepaper") or coin_data.get("whitepaperLink")
        if whitepaper_link:
            result["whitepaper_url"] = whitepaper_link
        else:
            # Fallback: Extract from HTML
            whitepaper_html = _extract_whitepaper_from_html(soup)
            if whitepaper_html:
                result["whitepaper_url"] = whitepaper_html

        # Session 85: Extract farming sources (try JSON first, then HTML)
        farming_data = coin_data.get("farmingPlatforms", []) or coin_data.get("launchpads", [])
        if farming_data:
            for platform in farming_data:
                platform_name = platform.get("name") if isinstance(platform, dict) else str(platform)
                if platform_name:
                    result["farming_sources"].append(platform_name)
        else:
            # Fallback: Extract from HTML
            farming_html = _extract_farming_from_html(soup)
            if farming_html:
                result["farming_sources"] = farming_html

        # Calculate confidence (Session 85: Added 2 new fields)
        fields_found = sum([
            1 if result["name"] else 0,
            1 if result["total_funding"] else 0,
            1 if result["total_supply"] else 0,
            1 if result["circulating_supply_percent"] is not None else 0,
            1 if len(result["investors"]) > 0 else 0,
            1 if len(result["vesting_schedule"]) > 0 else 0,
            1 if result["whitepaper_url"] else 0,
            1 if len(result["farming_sources"]) > 0 else 0
        ])
        result["_data_confidence"] = int((fields_found / 8) * 100)

        logger.info(f"✓ Dropstab data extracted for {token}: {result['_data_confidence']}% confidence")
        if result["total_funding"]:
            logger.info(f"   Funding: ${result['total_funding']:,}")
        if result["total_supply"]:
            logger.info(f"   Supply: {result['total_supply']:,}")

        return result

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Dropstab JSON: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to parse Dropstab data: {e}")
        return None


def _extract_funding_from_html(soup: BeautifulSoup) -> Optional[str]:
    """
    Extract funding amount from HTML (fallback when not in JSON)

    Looks for patterns like "Funds raised: $431.50 M" or "$18.90 M" in fundraising sections

    Args:
        soup: BeautifulSoup object

    Returns:
        Funding amount string like "$431.50 M", or None
    """
    try:
        # Look for common patterns in text
        text = soup.get_text()

        # Pattern 1: "Funds Raised: $X.XX M" (exact Dropstab format)
        # Note: \s includes \xa0 (non-breaking space) which Dropstab uses
        match = re.search(r'Funds [Rr]aised[:\s]*\$\s*([\d.]+)\s*([KMBT])', text, re.IGNORECASE)
        if match:
            logger.debug(f"   Found funding via Pattern 1: ${match.group(1)} {match.group(2)}")
            return f"${match.group(1)} {match.group(2)}"

        # Pattern 2: "Total raised: $X.XX M"
        match = re.search(r'Total [Rr]aised[:\s]*\$\s*([\d.]+)\s*([KMBT])', text, re.IGNORECASE)
        if match:
            logger.debug(f"   Found funding via Pattern 2: ${match.group(1)} {match.group(2)}")
            return f"${match.group(1)} {match.group(2)}"

        # Pattern 3: "$X.XX M across" or "$X.XX M" in fundraising context
        match = re.search(r'\$\s*([\d.]+)\s*([KMBT])\s*(?:across|in total)', text, re.IGNORECASE)
        if match:
            logger.debug(f"   Found funding via Pattern 3: ${match.group(1)} {match.group(2)}")
            return f"${match.group(1)} {match.group(2)}"

        # Pattern 4: Look for dollar amounts near "fundraising" or "raised"
        funding_section = soup.find(string=re.compile(r'fundraising|raised|pulled in', re.IGNORECASE))
        if funding_section:
            parent = funding_section.find_parent()
            if parent:
                parent_text = parent.get_text()
                match = re.search(r'\$\s*([\d.]+)\s*([KMBT])', parent_text)
                if match:
                    logger.debug(f"   Found funding via Pattern 4: ${match.group(1)} {match.group(2)}")
                    return f"${match.group(1)} {match.group(2)}"

        return None

    except Exception as e:
        logger.debug(f"Failed to extract funding from HTML: {e}")
        return None


def _extract_vesting_from_html(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    """
    Extract vesting schedule from HTML tables

    Looks for vesting unlock events with dates, amounts, and percentages

    Args:
        soup: BeautifulSoup object

    Returns:
        List of vesting events like [{"date": "2026-11-24", "amount": 16620000000, "percent": 16.62}, ...]
    """
    vesting_events = []

    try:
        # Look for text patterns that indicate vesting events
        # Pattern: "Nov 24, 2026: 16.62B MON (16.62%)"
        text = soup.get_text()

        # Find all lines that look like vesting events
        # Pattern examples from Dropstab:
        # - "Nov242026Unlock of 16.62 B MON - 16.62% of Total Supply"
        # - "Dec242026Unlock of 946.75M MON - 0.95%"
        # Flexible pattern to match both formats (with and without spaces/commas)
        vesting_pattern = re.compile(
            r'([A-Z][a-z]{2})\s*(\d{1,2}),?\s*(\d{4})'  # Date: "Nov242026" or "Nov 24, 2026"
            r'.*?'                                       # Any text (e.g., "Unlock of")
            r'([\d.]+)\s*([KMBT])\s*'                   # Amount: "16.62 B" or "16.62B"
            r'.*?'                                       # Any text in between
            r'-?\s*([\d.]+)%',                          # Percent: "- 16.62%" or "16.62%"
            re.IGNORECASE
        )

        for match in vesting_pattern.finditer(text):
            month_str = match.group(1)  # "Nov"
            day_str = match.group(2)    # "24"
            year_str = match.group(3)   # "2026"
            amount_num = float(match.group(4))  # "16.62"
            amount_mult = match.group(5)  # "B"
            percent = float(match.group(6))  # "16.62"

            # Parse date to ISO format
            try:
                from datetime import datetime
                date_str_formatted = f"{month_str} {day_str}, {year_str}"
                parsed_date = datetime.strptime(date_str_formatted, "%b %d, %Y")
                iso_date = parsed_date.strftime("%Y-%m-%d")
            except Exception as e:
                logger.debug(f"Failed to parse vesting date '{month_str} {day_str}, {year_str}': {e}")
                iso_date = f"{year_str}-??-{day_str}"  # Fallback

            # Convert amount to integer
            multipliers = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000, "T": 1_000_000_000_000}
            amount_int = int(amount_num * multipliers.get(amount_mult.upper(), 1))

            vesting_events.append({
                "date": iso_date,
                "amount": amount_int,
                "percent": percent
            })

        if vesting_events:
            logger.debug(f"   Found {len(vesting_events)} vesting events via HTML parsing")

        return vesting_events

    except Exception as e:
        logger.debug(f"Failed to extract vesting from HTML: {e}")
        return []


def _parse_dollar_amount(amount_str: str) -> Optional[int]:
    """
    Parse dollar amount strings like "$18.90 M", "$431.50 M"

    Args:
        amount_str: String like "$18.90 M" or "$431.50M"

    Returns:
        Amount in USD as integer, or None
    """
    try:
        # Remove $ and spaces
        clean = amount_str.replace("$", "").replace(" ", "").upper()

        # Extract number and multiplier
        match = re.match(r"([\d.]+)([KMBT])?", clean)
        if not match:
            return None

        number = float(match.group(1))
        multiplier = match.group(2)

        # Apply multiplier
        if multiplier == "K":
            number *= 1_000
        elif multiplier == "M":
            number *= 1_000_000
        elif multiplier == "B":
            number *= 1_000_000_000
        elif multiplier == "T":
            number *= 1_000_000_000_000

        return int(number)

    except Exception as e:
        logger.debug(f"Failed to parse dollar amount '{amount_str}': {e}")
        return None


def _extract_whitepaper_from_html(soup: BeautifulSoup) -> Optional[str]:
    """
    Extract whitepaper URL from HTML links (Session 85)

    Looks for links with text like "whitepaper", "white paper", "documentation", "litepaper"
    """
    try:
        for link in soup.find_all('a', href=True):
            link_text = link.get_text().lower()
            if any(term in link_text for term in ['whitepaper', 'white paper', 'documentation', 'litepaper', 'docs']):
                href = link['href']
                if href.startswith('http'):
                    return href
        return None
    except Exception as e:
        logger.debug(f"Failed to extract whitepaper from HTML: {e}")
        return None


def _extract_farming_from_html(soup: BeautifulSoup) -> List[str]:
    """
    Extract farming/launchpad platforms from HTML (Session 85)

    Uses conservative regex with known platform names to prevent false positives
    """
    try:
        # Conservative approach: exact match against known platforms
        KNOWN_FARMING_PLATFORMS = [
            # Major CEX Launchpads
            "Binance Launchpool", "Binance Launchpad", "Binance Megadrop",
            "Bybit Launchpad", "Bybit Launchpool",
            "OKX Jumpstart", "OKX Earn",
            "Gate.io Startup", "Gate Startup",
            "KuCoin Spotlight",
            "MEXC Kickstarter",
            # DeFi Launchpads
            "Coinlist", "CoinList",
            "DAO Maker", "DAOMaker",
            "Polkastarter",
            "Seedify",
            "TrustSwap",
            "GameFi",
            "ChainGPT Pad",
            "Eesee",
            # Specific platforms
            "Talisman Quests",
        ]

        text = soup.get_text()
        farming_platforms = []

        # Build regex pattern from known platforms
        import re
        platforms_pattern = '|'.join(re.escape(platform) for platform in KNOWN_FARMING_PLATFORMS)
        platforms_regex = re.compile(f'({platforms_pattern})', re.IGNORECASE)

        matches = platforms_regex.findall(text)
        if matches:
            # Deduplicate and title-case
            farming_platforms = list(set([m.title() for m in matches]))

        return farming_platforms

    except Exception as e:
        logger.debug(f"Failed to extract farming sources from HTML: {e}")
        return []


def _safe_int(value: Any) -> Optional[int]:
    """Safely convert value to int"""
    try:
        if value is None:
            return None
        return int(float(value))
    except (ValueError, TypeError):
        return None


def _safe_float(value: Any) -> Optional[float]:
    """Safely convert value to float"""
    try:
        if value is None:
            return None
        return float(value)
    except (ValueError, TypeError):
        return None


# CLI for testing
if __name__ == "__main__":
    import sys

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s"
    )

    if len(sys.argv) < 2:
        print("Usage: python dropstab_fetcher.py <TOKEN>")
        print("\nExamples:")
        print("  python dropstab_fetcher.py IRYS")
        print("  python dropstab_fetcher.py MONAD")
        sys.exit(1)

    token = sys.argv[1].upper()

    print(f"\n{'='*70}")
    print(f"DROPSTAB DATA FETCH: {token}")
    print(f"{'='*70}\n")

    result = fetch_dropstab_data(token)

    if result:
        print(f"\n{'='*70}")
        print("RESULT")
        print(f"{'='*70}")
        print(json.dumps(result, indent=2))
    else:
        print(f"\n❌ Failed to fetch Dropstab data for {token}")
        sys.exit(1)
