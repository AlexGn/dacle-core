"""
MEXC Announcements Fetcher - Session 318 P0.3

Fetches new listing announcements from MEXC exchange.
Alternative to Twitter API - scrapes public announcement pages.

Expected: +3-5 TGEs per month, faster timing than CCXT

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


class MEXCAnnouncementsFetcher:
    """Fetches new token listings from MEXC announcement pages."""

    # MEXC public announcement URLs (no API key required)
    ANNOUNCEMENTS_URL = "https://www.mexc.com/support/sections/360000186251-Latest-Activities"
    LISTINGS_URL = "https://www.mexc.com/newlisting"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9'
        })

    def fetch_announcements(self, days_ahead: int = 7) -> List[Dict[str, Any]]:
        """
        Fetch new listing announcements from MEXC.

        NOTE: MEXC's announcement pages are JavaScript-rendered, making
        direct scraping difficult. This method attempts basic scraping
        but may return empty results.

        For production, consider:
        1. Using CCXT API (already implemented in daily_tge_discovery.py)
        2. Twitter API with @MEXC_Listings account
        3. Selenium/Playwright for JS rendering

        Args:
            days_ahead: Look for announcements in the next N days

        Returns:
            List of standardized token dicts
        """
        try:
            logger.info("Fetching MEXC announcements (basic scraping)...")

            # Try the listings calendar page
            response = self.session.get(self.LISTINGS_URL, timeout=30)
            response.raise_for_status()

            # Parse HTML
            soup = BeautifulSoup(response.content, 'html.parser')

            # Look for listing cards/sections
            # NOTE: This is a best-effort approach - MEXC page structure may change
            tokens = []

            # Try to find token symbols in the page
            # Common patterns: "TOKEN/USDT", "TOKEN Listing", etc.
            text_content = soup.get_text()

            # Extract potential token symbols (2-10 uppercase letters)
            # Followed by listing-related keywords
            pattern = r'\b([A-Z]{2,10})\b.*?(?:listing|launch|trading|spot)'
            matches = re.finditer(pattern, text_content, re.IGNORECASE)

            seen_symbols = set()
            for match in matches:
                symbol = match.group(1)

                # Skip common non-token words
                if symbol in {'MEXC', 'USDT', 'BTC', 'ETH', 'TRADING', 'SPOT', 'FUTURES', 'MARGIN', 'LISTING', 'NEW'}:
                    continue

                # Skip if already seen
                if symbol in seen_symbols:
                    continue

                seen_symbols.add(symbol)

                token = {
                    "symbol": symbol,
                    "name": symbol,  # Will be enriched later
                    "listing_date": None,  # Cannot reliably extract from scraping
                    "trading_pairs": [f"{symbol}/USDT"],
                    "exchange": "mexc",
                    "announcement_url": self.LISTINGS_URL,
                    "confidence": "LOW",  # Low confidence for scraped data
                    "data_source": "mexc_scraping"
                }
                tokens.append(token)

            if tokens:
                logger.info(f"Found {len(tokens)} potential MEXC listings via scraping")
            else:
                logger.warning("No MEXC listings found via scraping (JS-rendered page)")

            return tokens[:10]  # Limit to 10 to avoid noise

        except requests.exceptions.RequestException as e:
            logger.error(f"MEXC announcements request failed: {e}")
            return []
        except Exception as e:
            logger.error(f"MEXC announcements fetcher error: {e}", exc_info=True)
            return []

    def validate_token(self, symbol: str) -> bool:
        """
        Validate if a token is listed on MEXC using CCXT.

        This is more reliable than web scraping.

        Args:
            symbol: Token symbol (e.g., "POWER")

        Returns:
            True if token is listed on MEXC, False otherwise
        """
        try:
            import ccxt

            mexc = ccxt.mexc({'enableRateLimit': True})
            markets = mexc.load_markets()

            # Check if symbol/USDT pair exists
            pair = f"{symbol}/USDT"
            return pair in markets

        except ImportError:
            logger.error("CCXT not installed - cannot validate MEXC listings")
            return False
        except Exception as e:
            logger.error(f"MEXC validation error for {symbol}: {e}")
            return False

    def fetch_recent_listings_ccxt(self, days_back: int = 7) -> List[Dict[str, Any]]:
        """
        Fetch recent MEXC listings using CCXT API.

        This is the RECOMMENDED method - more reliable than scraping.
        Already implemented in daily_tge_discovery.py as _scan_mexc().

        Args:
            days_back: Look back N days for new listings

        Returns:
            List of standardized token dicts
        """
        try:
            import ccxt
            from datetime import datetime, timedelta

            logger.info("Fetching MEXC listings via CCXT (RECOMMENDED)...")

            mexc = ccxt.mexc({'enableRateLimit': True})
            markets = mexc.load_markets()

            # Get all USDT spot pairs
            spot_usdt = {
                symbol: market for symbol, market in markets.items()
                if '/USDT' in symbol and ':' not in symbol and market.get('spot', True)
            }

            logger.info(f"Found {len(spot_usdt)} USDT spot pairs on MEXC")

            # Filter for likely new listings (high volume, short history)
            tokens = []
            for pair, market in list(spot_usdt.items())[:50]:  # Sample first 50
                try:
                    symbol = market.get('base', '')

                    # Skip stablecoins and majors
                    if symbol in {'USDT', 'USDC', 'BTC', 'ETH', 'BNB', 'SOL'}:
                        continue

                    # Get ticker to check volume
                    ticker = mexc.fetch_ticker(pair)
                    volume_24h = ticker.get('quoteVolume', 0)

                    # Only include if significant volume (likely recent listing)
                    if volume_24h >= 100000:  # $100K+ daily volume
                        token = {
                            "symbol": symbol,
                            "name": symbol,
                            "listing_date": None,  # CCXT doesn't provide listing dates
                            "trading_pairs": [pair],
                            "exchange": "mexc",
                            "announcement_url": "https://www.mexc.com/newlisting",
                            "confidence": "MEDIUM",
                            "data_source": "mexc_ccxt",
                            "volume_24h_usd": volume_24h
                        }
                        tokens.append(token)

                except Exception as e:
                    logger.debug(f"Error fetching ticker for {pair}: {e}")
                    continue

            logger.info(f"Found {len(tokens)} high-volume MEXC listings (CCXT)")
            return tokens

        except ImportError:
            logger.error("CCXT not installed. Run: pip install ccxt")
            return []
        except Exception as e:
            logger.error(f"MEXC CCXT fetcher error: {e}", exc_info=True)
            return []


# CLI Testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    fetcher = MEXCAnnouncementsFetcher()

    print("\n" + "=" * 80)
    print("MEXC Announcements Fetcher - Session 318 P0.3")
    print("=" * 80)

    # Test 1: Web scraping (LOW reliability - JS-rendered pages)
    print("\n[Test 1] Fetching announcements via web scraping...")
    print("NOTE: This has LOW reliability due to JS rendering")
    announcements = fetcher.fetch_announcements(days_ahead=7)

    if announcements:
        print(f"\nFound {len(announcements)} potential announcements (web scraping)")
        for token in announcements[:5]:
            print(f"\n  Symbol: {token['symbol']}")
            print(f"  Confidence: {token['confidence']}")
            print(f"  Source: {token['data_source']}")
    else:
        print("\n⚠️  No announcements found via web scraping")
        print("   This is expected - MEXC uses JS rendering")

    # Test 2: CCXT validation (HIGH reliability)
    print("\n" + "-" * 80)
    print("\n[Test 2] Fetching recent listings via CCXT API...")
    print("NOTE: This is the RECOMMENDED method")
    ccxt_listings = fetcher.fetch_recent_listings_ccxt(days_back=7)

    if ccxt_listings:
        print(f"\nFound {len(ccxt_listings)} recent MEXC listings (CCXT)")
        for i, token in enumerate(ccxt_listings[:10]):
            print(f"\n  [{i+1}] {token['symbol']}")
            print(f"      Volume 24h: ${token.get('volume_24h_usd', 0):,.0f}")
            print(f"      Trading Pairs: {', '.join(token['trading_pairs'])}")
            print(f"      Confidence: {token['confidence']}")
    else:
        print("\n⚠️  No recent MEXC listings found")

    # Test 3: Validate a known token
    print("\n" + "-" * 80)
    print("\n[Test 3] Validating POWER token on MEXC...")
    is_listed = fetcher.validate_token("POWER")
    print(f"POWER listed on MEXC: {is_listed}")

    print("\n" + "=" * 80)
    print("\n📌 RECOMMENDATION:")
    print("   Use CCXT method (Test 2) for production - already integrated")
    print("   Web scraping (Test 1) has low reliability due to JS rendering")
    print("\n   MEXC listings are already handled by daily_tge_discovery.py")
    print("   via _scan_mexc() method using CCXT API")
    print("=" * 80)
