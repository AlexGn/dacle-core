"""
KuCoin Exchange Fetcher - Session 318 P0.1

Fetches new listing announcements from KuCoin exchange.
Expected: +5-10 TGEs per month

KuCoin API Documentation:
- Announcements endpoint: https://api.kucoin.com/api/v1/announcements
- Public API, no authentication required
- Rate limit: 100 requests per 10 seconds

Integration Point:
- Called by daily_tge_discovery.py during exchange scan
- Returns standardized token format for consolidation
"""

import requests
import logging
from typing import List, Dict, Optional, Any
from datetime import datetime, timedelta
import re
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class KuCoinFetcher:
    """Fetches new token listings from KuCoin exchange."""

    BASE_URL = "https://www.kucoin.com"
    ANNOUNCEMENTS_PATH = "/_api/cms/articles"

    # Keywords indicating new listing announcements
    LISTING_KEYWORDS = [
        "new listing",
        "token listing",
        "trading starts",
        "trading opens",
        "will list",
        "to list",
        "listing on kucoin"
    ]

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
            'Accept': 'application/json'
        })

    def fetch_announcements(self, days_ahead: int = 7, page_size: int = 50) -> List[Dict[str, Any]]:
        """
        Fetch new listing announcements from KuCoin.

        Args:
            days_ahead: Look for announcements in the next N days
            page_size: Number of announcements per page (max 100)

        Returns:
            List of standardized token dicts:
            {
                "symbol": "MONAD",
                "name": "Monad",
                "listing_date": "2025-11-09T10:00:00Z",
                "trading_pairs": ["MONAD/USDT", "MONAD/BTC"],
                "exchange": "kucoin",
                "announcement_url": "https://...",
                "confidence": "HIGH",  # HIGH/MEDIUM/LOW
                "data_source": "exchange_api"
            }
        """
        try:
            # KuCoin announcements API (CMS articles endpoint)
            url = f"{self.BASE_URL}{self.ANNOUNCEMENTS_PATH}"
            params = {
                'category': 'listing',  # Filter for listing announcements
                'page': 1,
                'pageSize': page_size,
                'lang': 'en_US'
            }

            logger.info(f"Fetching KuCoin announcements (page_size={page_size})")
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            # KuCoin CMS API returns data directly (no code/msg wrapper)
            if not isinstance(data, dict) or 'items' not in data:
                logger.error(f"Unexpected KuCoin API response format: {type(data)}")
                return []

            announcements = data.get('items', [])
            logger.info(f"Retrieved {len(announcements)} announcements from KuCoin")

            # Parse announcements for new listings
            tokens = []
            cutoff_date = datetime.utcnow() + timedelta(days=days_ahead)

            for ann in announcements:
                parsed = self._parse_announcement(ann, cutoff_date)
                if parsed:
                    tokens.append(parsed)

            logger.info(f"Found {len(tokens)} new listing announcements from KuCoin")
            return tokens

        except requests.exceptions.RequestException as e:
            logger.error(f"KuCoin API request failed: {e}")
            return []
        except Exception as e:
            logger.error(f"KuCoin fetcher error: {e}", exc_info=True)
            return []

    def _parse_announcement(self, ann: Dict[str, Any], cutoff_date: datetime) -> Optional[Dict[str, Any]]:
        """
        Parse a single announcement for listing information.

        Args:
            ann: Raw announcement dict from API
            cutoff_date: Only include announcements before this date

        Returns:
            Standardized token dict or None if not a listing announcement
        """
        try:
            title = ann.get('title', '').lower()
            content = ann.get('summary', '').lower()

            # Check if this is a listing announcement
            is_listing = any(keyword in title or keyword in content
                           for keyword in self.LISTING_KEYWORDS)

            if not is_listing:
                return None

            # Extract announcement date
            created_at = ann.get('created_at')
            if created_at:
                ann_date = datetime.fromtimestamp(created_at / 1000)  # KuCoin uses milliseconds
                if ann_date > cutoff_date:
                    return None

            # Extract token symbol and name from title
            # Example: "KuCoin Listing: Monad (MONAD)"
            # Example: "MONAD Will Be Listed on KuCoin!"
            symbol_match = re.search(r'\(([A-Z0-9]{2,10})\)', ann.get('title', ''))
            if not symbol_match:
                # Try to find standalone symbol
                symbol_match = re.search(r'\b([A-Z]{3,10})\b', ann.get('title', ''))

            if not symbol_match:
                logger.debug(f"Could not extract symbol from: {ann.get('title')}")
                return None

            symbol = symbol_match.group(1)

            # Extract token name (before the symbol)
            name_match = re.search(r'(?:listing|list|lists)[\s:]*([A-Za-z\s]+)\s*\(', ann.get('title', ''), re.IGNORECASE)
            name = name_match.group(1).strip() if name_match else symbol

            # Extract listing date from content
            listing_date = self._extract_listing_date(content, created_at)

            # Extract trading pairs
            trading_pairs = self._extract_trading_pairs(symbol, content)

            # Build announcement URL
            ann_url = f"https://www.kucoin.com/news/{ann.get('annId', '')}"

            # Determine confidence level
            confidence = self._determine_confidence(title, content, listing_date)

            return {
                "symbol": symbol,
                "name": name,
                "listing_date": listing_date.isoformat() if listing_date else None,
                "trading_pairs": trading_pairs,
                "exchange": "kucoin",
                "announcement_url": ann_url,
                "confidence": confidence,
                "data_source": "exchange_api"
            }

        except Exception as e:
            logger.error(f"Error parsing KuCoin announcement: {e}", exc_info=True)
            return None

    def _extract_listing_date(self, content: str, created_at: Optional[int]) -> Optional[datetime]:
        """
        Extract listing date from announcement content.

        Args:
            content: Announcement text
            created_at: Announcement creation timestamp (milliseconds)

        Returns:
            Listing datetime or None
        """
        # Try to find specific date patterns
        # Example: "November 9, 2025 at 10:00 (UTC)"
        # Example: "2025-11-09 10:00:00 UTC"

        # ISO format
        iso_match = re.search(r'(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})', content)
        if iso_match:
            date_str = f"{iso_match.group(1)} {iso_match.group(2)}"
            try:
                return datetime.strptime(date_str, "%Y-%m-%d %H:%M")
            except ValueError:
                pass

        # Month Day, Year format
        month_match = re.search(r'(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2}),?\s+(\d{4})', content, re.IGNORECASE)
        if month_match:
            month_name = month_match.group(1).capitalize()
            day = month_match.group(2)
            year = month_match.group(3)
            try:
                date_str = f"{month_name} {day}, {year}"
                return datetime.strptime(date_str, "%B %d, %Y")
            except ValueError:
                pass

        # Fallback: Use announcement date + 1 day (typical listing delay)
        if created_at:
            return datetime.fromtimestamp(created_at / 1000) + timedelta(days=1)

        return None

    def _extract_trading_pairs(self, symbol: str, content: str) -> List[str]:
        """
        Extract trading pairs from announcement content.

        Args:
            symbol: Token symbol
            content: Announcement text

        Returns:
            List of trading pairs (e.g., ["MONAD/USDT", "MONAD/BTC"])
        """
        pairs = []

        # Common quote currencies on KuCoin
        quote_currencies = ['USDT', 'BTC', 'ETH', 'USDC']

        # Look for explicit pair mentions
        for quote in quote_currencies:
            if f"{symbol}/{quote}".lower() in content or f"{symbol}-{quote}".lower() in content:
                pairs.append(f"{symbol}/{quote}")

        # Default to USDT if no pairs found
        if not pairs:
            pairs.append(f"{symbol}/USDT")

        return pairs

    def _determine_confidence(self, title: str, content: str, listing_date: Optional[datetime]) -> str:
        """
        Determine confidence level of the listing announcement.

        Args:
            title: Announcement title
            content: Announcement content
            listing_date: Extracted listing date

        Returns:
            Confidence level: HIGH, MEDIUM, or LOW
        """
        # HIGH confidence: Official listing announcement with date
        if "official" in title or "announcement" in title:
            if listing_date:
                return "HIGH"
            return "MEDIUM"

        # MEDIUM confidence: Listing mentioned but unclear timing
        if any(keyword in title for keyword in ["listing", "will list", "to list"]):
            return "MEDIUM"

        # LOW confidence: Vague or speculative
        return "LOW"

    def fetch_new_listings(self, days_back: int = 7) -> List[Dict[str, Any]]:
        """
        Fetch tokens that were listed in the last N days.

        Args:
            days_back: Look back N days for new listings

        Returns:
            List of standardized token dicts
        """
        try:
            # KuCoin symbols endpoint (lists all trading pairs)
            url = f"{self.BASE_URL}/api/v1/symbols"

            logger.info("Fetching KuCoin symbols list")
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            data = response.json()

            if data.get('code') != '200000':
                logger.error(f"KuCoin symbols API error: {data.get('msg')}")
                return []

            symbols = data.get('data', [])

            # Filter for recently enabled pairs
            cutoff_date = datetime.utcnow() - timedelta(days=days_back)
            recent_listings = []

            for pair in symbols:
                if not pair.get('enableTrading'):
                    continue

                # Check if symbol is new (this requires additional metadata that may not be available)
                # For now, we'll rely on announcements for discovery
                pass

            logger.info(f"Found {len(recent_listings)} recent listings from KuCoin symbols")
            return recent_listings

        except requests.exceptions.RequestException as e:
            logger.error(f"KuCoin symbols API request failed: {e}")
            return []
        except Exception as e:
            logger.error(f"KuCoin new listings fetcher error: {e}", exc_info=True)
            return []


# CLI Testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    fetcher = KuCoinFetcher()

    print("\n" + "=" * 60)
    print("KuCoin Exchange Fetcher - Session 318 P0.1")
    print("=" * 60)

    # Test announcements fetch
    print("\nFetching announcements (next 7 days)...")
    announcements = fetcher.fetch_announcements(days_ahead=7)

    print(f"\nFound {len(announcements)} new listing announcements")

    for token in announcements:
        print("\n" + "-" * 60)
        print(f"Symbol: {token['symbol']}")
        print(f"Name: {token['name']}")
        print(f"Listing Date: {token['listing_date']}")
        print(f"Trading Pairs: {', '.join(token['trading_pairs'])}")
        print(f"Confidence: {token['confidence']}")
        print(f"URL: {token['announcement_url']}")

    if not announcements:
        print("\nNo new listings found in the next 7 days")
        print("This is expected if KuCoin has no upcoming announcements")
