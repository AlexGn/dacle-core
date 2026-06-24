#!/usr/bin/env python3
"""
ICODrops Data Fetcher (Session 85 - Phase 2)

DEPRECATED: Use src.data.fetchers.token_data.fetch_icodrops_data instead.
    from dacle_core.data.fetchers import fetch_icodrops_data

---

Extracts funding, vesting, whitepaper, and farming data from ICODrops.com.

ICODrops provides high-quality structured data for:
- Vesting schedules (often cleaner than Dropstab)
- Whitepaper URLs
- Farming/Launchpad platforms
- Token allocation breakdown
- Funding rounds and investors

Usage (NEW):
    from dacle_core.data.fetchers import fetch_icodrops_data

Usage (DEPRECATED):
    from dacle_core.data.fetchers.icodrops_fetcher import fetch_icodrops_data

Created: 2025-12-04 (Session 85 - Automated Field Extraction)
Deprecated: 2025-12-26 (Session 256+ Refactoring)
"""
import warnings
warnings.warn(
    "scripts.helpers.icodrops_fetcher is deprecated. "
    "Use src.data.fetchers.fetch_icodrops_data instead.",
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


def fetch_icodrops_data(symbol: str, name: str = None) -> Optional[Dict[str, Any]]:
    """
    Fetch token data from ICODrops.com

    Args:
        symbol: Token symbol (e.g., "SEEK", "MONAD")
        name: Token name (e.g., "Talisman") - helps with URL matching

    Returns:
        Dict with vesting, whitepaper, farming, and tokenomics data, or None if not found

    Example Output:
        {
            "symbol": "SEEK",
            "name": "Talisman",
            "whitepaper_url": "https://docs.talisman.xyz",
            "vesting_schedule": "25% TGE unlock, 6 months linear vesting",
            "farming_sources": ["Binance Launchpool"],
            "total_supply": 100000000,
            "token_allocation": {...},
            "_source": "icodrops",
            "_source_url": "https://icodrops.com/talisman/",
            "_data_confidence": 75
        }
    """
    try:
        # Try common URL patterns
        symbol_lower = symbol.lower()
        name_lower = name.lower() if name else None

        url_patterns = [
            f"https://icodrops.com/{name_lower}/" if name_lower else None,
            f"https://icodrops.com/{symbol_lower}/",
            f"https://icodrops.com/{symbol_lower}-protocol/",
            f"https://icodrops.com/{name_lower}-{symbol_lower}/" if name_lower else None,
        ]

        # Filter out None values
        url_patterns = [url for url in url_patterns if url]

        for url in url_patterns:
            logger.debug(f"Trying ICODrops URL: {url}")

            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
                }
                response = requests.get(url, headers=headers, timeout=15)

                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, "html.parser")

                    # Check if page exists (ICODrops returns 200 even for 404s sometimes)
                    if _is_valid_icodrops_page(soup):
                        logger.info(f"✓ Found ICODrops data for {symbol}")
                        return _parse_icodrops_data(soup, symbol, url)
                    else:
                        logger.debug(f"Invalid ICODrops page at {url}")

            except requests.RequestException as e:
                logger.debug(f"Request failed for {url}: {e}")
                continue

        logger.info(f"ICODrops: No data found for {symbol}")
        return None

    except Exception as e:
        logger.error(f"ICODrops fetch error for {symbol}: {e}")
        return None


def _is_valid_icodrops_page(soup: BeautifulSoup) -> bool:
    """Check if this is a valid ICODrops project page"""
    # Look for key elements that indicate a project page
    # ICODrops uses "Project-Page-Header__name" class for h1
    has_title = soup.find("h1", class_=re.compile("Project-Page-Header__name|ico-main-info|title"))
    has_content = soup.find("div", class_=re.compile("Project-Page|ico-content|project-info"))
    return has_title is not None or has_content is not None


def _parse_icodrops_data(soup: BeautifulSoup, symbol: str, url: str) -> Dict[str, Any]:
    """
    Parse token data from ICODrops HTML

    Session 86A: Enhanced token_allocation extraction with multiple fallback strategies

    ICODrops structure is cleaner than Dropstab:
    - Clear section headers
    - Structured tables
    - Direct whitepaper links
    """
    try:
        # Initialize result
        result = {
            "symbol": symbol.upper(),
            "name": None,
            "whitepaper_url": None,
            "vesting_schedule": None,
            "farming_sources": [],
            "total_supply": None,
            "token_allocation": {},
            "investors": [],
            "funding_rounds": [],
            "_source": "icodrops",
            "_source_url": url,
            "_data_confidence": 0
        }

        # Extract token name
        name_tag = soup.find("h1", class_=re.compile("Project-Page-Header__name|ico-main-info|title"))
        if name_tag:
            result["name"] = name_tag.get_text().strip()

        # Extract whitepaper URL
        whitepaper_link = soup.find("a", href=True, text=re.compile("whitepaper|white paper", re.IGNORECASE))
        if whitepaper_link:
            result["whitepaper_url"] = whitepaper_link["href"]
        else:
            # Try finding in links section
            links_section = soup.find("div", class_=re.compile("links|resources"))
            if links_section:
                for link in links_section.find_all("a", href=True):
                    if "whitepaper" in link.get_text().lower() or "docs" in link.get_text().lower():
                        result["whitepaper_url"] = link["href"]
                        break

        # Extract vesting schedule - Session 86A: Enhanced extraction
        result["vesting_schedule"] = _extract_vesting_schedule(soup)

        # Extract farming sources using conservative regex (same as Dropstab)
        farming_platforms = _extract_farming_from_html(soup)
        if farming_platforms:
            result["farming_sources"] = farming_platforms

        # Extract token allocation - Session 86A: Enhanced with multiple strategies
        result["token_allocation"] = _extract_token_allocation(soup)

        # Extract float_percent (Session 495 clarity fix)
        # Look for "Tokens for sale" or "TGE unlock"
        tokens_for_sale = soup.find(text=re.compile("tokens for sale", re.IGNORECASE))
        if tokens_for_sale:
            parent = tokens_for_sale.parent
            if parent:
                # Look for percentage in same or next element
                text = parent.get_text()
                pct_match = re.search(r'(\d+(?:\.\d+)?)\s*%', text)
                if pct_match:
                    result["float_percent"] = float(pct_match.group(1))
        
        if not result.get("float_percent") and result.get("token_allocation"):
            # Check allocation for 'Public Sale', 'Seed', etc.
            sale_keys = ["Public Sale", "Token Sale", "Ieo", "Ido", "Launchpad"]
            for k, v in result["token_allocation"].items():
                if any(sk in k for sk in sale_keys):
                    result["float_percent"] = v
                    break

        # Extract total supply
        supply_text = soup.find(text=re.compile("total.*supply", re.IGNORECASE))
        if supply_text:
            # Look for number in nearby text
            parent = supply_text.parent
            if parent:
                supply_str = parent.get_text()
                supply_match = re.search(r'([\d,]+(?:\.\d+)?)\s*(?:million|billion|M|B)?', supply_str, re.IGNORECASE)
                if supply_match:
                    result["total_supply"] = _parse_number_with_multiplier(supply_match.group(0))

        # Calculate confidence
        fields_found = sum([
            1 if result["name"] else 0,
            1 if result["whitepaper_url"] else 0,
            1 if result["vesting_schedule"] else 0,
            1 if len(result["farming_sources"]) > 0 else 0,
            1 if result["total_supply"] else 0,
            1 if len(result["token_allocation"]) > 0 else 0,
            1 if result.get("float_percent") else 0,
        ])
        result["_data_confidence"] = int((fields_found / 7) * 100)

        logger.info(f"✓ ICODrops data extracted for {symbol}: {result['_data_confidence']}% confidence")
        if result["whitepaper_url"]:
            logger.info(f"   Whitepaper: {result['whitepaper_url']}")
        if result["farming_sources"]:
            logger.info(f"   Farming: {', '.join(result['farming_sources'])}")
        if result["token_allocation"]:
            logger.info(f"   Token Allocation: {len(result['token_allocation'])} categories")

        return result

    except Exception as e:
        logger.error(f"Failed to parse ICODrops data: {e}")
        return None


def _extract_vesting_schedule(soup: BeautifulSoup) -> Optional[str]:
    """
    Session 86A: Extract vesting schedule with multiple strategies.

    Strategies:
    1. Look for "Distribution:" or "Vesting:" labels
    2. Look for TGE unlock percentage mentions
    3. Look for structured vesting data in tables
    """
    try:
        # Strategy 1: Look for Distribution/Vesting labels
        for label in ["distribution", "vesting", "unlock"]:
            label_elem = soup.find(text=re.compile(f"{label}\\s*:", re.IGNORECASE))
            if label_elem:
                parent = label_elem.parent
                if parent:
                    # Get text after the label
                    full_text = parent.get_text()
                    # Extract the value after the colon
                    match = re.search(f"{label}\\s*:\\s*(.+)", full_text, re.IGNORECASE)
                    if match:
                        vesting_text = match.group(1).strip()
                        # Clean up - remove extra whitespace
                        vesting_text = " ".join(vesting_text.split())
                        if len(vesting_text) > 10:  # Sanity check
                            return vesting_text

        # Strategy 2: Look for TGE percentage patterns
        tge_pattern = re.compile(r'(\d+(?:\.\d+)?%?\s*(?:at\s*)?TGE|TGE\s*(?:unlock\s*)?:?\s*\d+(?:\.\d+)?%)', re.IGNORECASE)
        tge_match = soup.find(text=tge_pattern)
        if tge_match:
            # Get surrounding context
            parent = tge_match.parent
            if parent:
                context = parent.get_text().strip()
                if context:
                    return context[:200]  # Limit length

        # Strategy 3: Look in specific ICODrops sections
        token_sale_section = soup.find("div", class_=re.compile("token-sale|tokenomics"))
        if token_sale_section:
            for row in token_sale_section.find_all(["tr", "div", "p"]):
                text = row.get_text().strip()
                if "vest" in text.lower() or "unlock" in text.lower() or "tge" in text.lower():
                    return text[:200]

    except Exception as e:
        logger.debug(f"Failed to extract vesting schedule: {e}")

    return None


def _extract_token_allocation(soup: BeautifulSoup) -> Dict[str, float]:
    """
    Session 86A: Extract token allocation with multiple fallback strategies.

    Strategies:
    1. Look for .token-allocation or .allocation-table classes
    2. Look for tables near "Token Allocation" or "Distribution" headers
    3. Parse list items with "Category: XX%" format
    4. Look for structured data in any table with percentage columns
    """
    allocation = {}

    try:
        # Strategy 1: Look for specific allocation classes
        allocation_section = soup.select_one(
            '.token-allocation, .allocation-table, '
            '[class*="allocation"], [class*="distribution"], '
            '[class*="tokenomics"]'
        )

        if allocation_section:
            allocation = _parse_allocation_from_element(allocation_section)
            if allocation:
                logger.debug(f"Found allocation via class selector: {len(allocation)} items")
                return allocation

        # Strategy 2: Look for tables near allocation headers
        allocation_headers = soup.find_all(
            text=re.compile(r"token\s*allocation|distribution|tokenomics", re.IGNORECASE)
        )

        for header in allocation_headers:
            parent = header.parent
            if parent:
                # Look for table or list in parent or siblings
                table = parent.find_next("table")
                if table:
                    allocation = _parse_allocation_table(table)
                    if allocation:
                        logger.debug(f"Found allocation via header search: {len(allocation)} items")
                        return allocation

                ul_list = parent.find_next("ul")
                if ul_list:
                    allocation = _parse_allocation_list(ul_list)
                    if allocation:
                        return allocation

        # Strategy 3: Look for percentage patterns in any structured data
        # Pattern: "Category Name: XX%" or "Category Name XX%"
        text = soup.get_text()
        percent_pattern = re.compile(
            r'([A-Za-z\s&]+?)[\s:]+(\d+(?:\.\d+)?)\s*%',
            re.MULTILINE
        )
        matches = percent_pattern.findall(text)

        if matches:
            # Filter for likely allocation categories
            allocation_keywords = [
                "team", "advisor", "investor", "community", "ecosystem",
                "treasury", "reserve", "foundation", "marketing", "development",
                "liquidity", "sale", "private", "public", "seed", "strategic",
                "airdrop", "staking", "reward", "incentive"
            ]

            for category, percent in matches:
                category_clean = category.strip().title()
                # Check if category looks like an allocation category
                if any(kw in category_clean.lower() for kw in allocation_keywords):
                    try:
                        pct = float(percent)
                        if 0 < pct <= 100:  # Valid percentage
                            allocation[category_clean] = pct
                    except ValueError:
                        continue

            if allocation:
                # Validate: allocations should roughly sum to 100%
                total = sum(allocation.values())
                if 90 <= total <= 110:  # Allow some margin for rounding
                    logger.debug(f"Found allocation via pattern matching: {len(allocation)} items")
                    return allocation
                else:
                    logger.debug(f"Allocation sum {total}% out of range, discarding")
                    allocation = {}

    except Exception as e:
        logger.debug(f"Failed to extract token allocation: {e}")

    return allocation


def _parse_allocation_from_element(element) -> Dict[str, float]:
    """Parse allocation from a container element (table, div, or list)."""
    allocation = {}

    try:
        # Try parsing as table
        if element.name == "table" or element.find("table"):
            table = element if element.name == "table" else element.find("table")
            return _parse_allocation_table(table)

        # Try parsing rows (tr, li, div with rows)
        rows = element.select('tr, li, .allocation-row, [class*="row"]')
        for row in rows:
            cells = row.select('td, .allocation-value, span, .category, .percent')

            if len(cells) >= 2:
                category = cells[0].get_text(strip=True)
                percentage = cells[1].get_text(strip=True)

                # Clean up and parse
                if category and percentage:
                    percent_match = re.search(r'(\d+(?:\.\d+)?)', percentage)
                    if percent_match:
                        try:
                            pct = float(percent_match.group(1))
                            if 0 < pct <= 100:
                                allocation[category.title()] = pct
                        except ValueError:
                            continue

            # Fallback: parse text content
            elif row.get_text():
                text = row.get_text(strip=True)
                # Pattern: "Category: XX%" or "Category XX%"
                match = re.search(r'([^:]+?)[\s:]+(\d+(?:\.\d+)?)\s*%?', text)
                if match:
                    category = match.group(1).strip().title()
                    try:
                        pct = float(match.group(2))
                        if 0 < pct <= 100:
                            allocation[category] = pct
                    except ValueError:
                        continue

    except Exception as e:
        logger.debug(f"Failed to parse allocation from element: {e}")

    return allocation


def _parse_allocation_list(ul_element) -> Dict[str, float]:
    """Parse allocation from a <ul> list element."""
    allocation = {}

    try:
        for item in ul_element.find_all("li"):
            text = item.get_text(strip=True)
            # Pattern: "Category: XX%" or "XX% Category"
            match = re.search(r'([A-Za-z\s&]+?)[\s:]+(\d+(?:\.\d+)?)\s*%', text)
            if not match:
                match = re.search(r'(\d+(?:\.\d+)?)\s*%\s*([A-Za-z\s&]+)', text)
                if match:
                    # Swap groups
                    category = match.group(2).strip().title()
                    pct = float(match.group(1))
                else:
                    continue
            else:
                category = match.group(1).strip().title()
                pct = float(match.group(2))

            if 0 < pct <= 100:
                allocation[category] = pct

    except Exception as e:
        logger.debug(f"Failed to parse allocation list: {e}")

    return allocation


def _extract_farming_from_html(soup: BeautifulSoup) -> List[str]:
    """
    Extract farming/launchpad platforms from HTML (Session 85)

    Uses same conservative regex as Dropstab for consistency
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


def _parse_allocation_table(table_element) -> Dict[str, float]:
    """Parse token allocation from HTML table or list"""
    allocation = {}

    try:
        if table_element.name == "table":
            # Parse table rows
            for row in table_element.find_all("tr"):
                cols = row.find_all(["td", "th"])
                if len(cols) >= 2:
                    category = cols[0].get_text().strip()
                    percent_text = cols[1].get_text().strip()
                    percent_match = re.search(r'(\d+(?:\.\d+)?)', percent_text)
                    if percent_match:
                        allocation[category] = float(percent_match.group(1))
        else:
            # Parse list items
            for item in table_element.find_all("li"):
                text = item.get_text()
                # Look for pattern like "Community: 50%"
                match = re.search(r'([^:]+):\s*(\d+(?:\.\d+)?)%?', text)
                if match:
                    category = match.group(1).strip()
                    percent = float(match.group(2))
                    allocation[category] = percent

    except Exception as e:
        logger.debug(f"Failed to parse allocation table: {e}")

    return allocation


def _parse_number_with_multiplier(num_str: str) -> Optional[int]:
    """
    Parse number with multiplier (e.g., "100M", "1.5B")

    Examples:
        "100 million" -> 100000000
        "1.5B" -> 1500000000
    """
    try:
        # Remove commas and spaces
        num_str = num_str.replace(",", "").replace(" ", "")

        # Extract number and multiplier
        match = re.search(r'([\d.]+)\s*([MmBbKk])?(?:illion)?', num_str, re.IGNORECASE)
        if not match:
            return None

        number = float(match.group(1))
        multiplier = match.group(2)

        # Apply multiplier
        if multiplier:
            multiplier_upper = multiplier.upper()
            if multiplier_upper == "K":
                number *= 1_000
            elif multiplier_upper == "M":
                number *= 1_000_000
            elif multiplier_upper == "B":
                number *= 1_000_000_000

        return int(number)

    except Exception as e:
        logger.debug(f"Failed to parse number '{num_str}': {e}")
        return None
