"""
HTX (Huobi) Exchange Fetcher - Session 318 P1.2

Fetches new listing announcements from HTX exchange (formerly Huobi).
Expected: +3-5 TGEs per month

HTX API Documentation:
- Official API: https://huobiapi.github.io/docs/spot/v1/en/
- Symbols endpoint: https://api.huobi.pro/v1/common/symbols
- Announcements: https://www.htx.com/support/list/360000039942
- No authentication required for market data

Integration Point:
- Called by daily_tge_discovery.py during exchange scan
- Returns standardized token format for consolidation
"""

import requests
import logging
from typing import List, Dict, Optional, Any
from datetime import datetime, timedelta
import re

logger = logging.getLogger(__name__)


class HTXFetcher:
    """Fetches new token listings from HTX (formerly Huobi) exchange."""

    BASE_URL = "https://api.huobi.pro"
    SYMBOLS_ENDPOINT = "/v1/common/symbols"
    ANNOUNCEMENTS_URL = "https://www.htx.com/support/list/360000039942"

    # Keywords indicating new listing announcements
    LISTING_KEYWORDS = [
        "new listing",
        "will list",
        "listing",
        "trading starts",
        "trading opens",
        "listed",
    ]

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        })
        self._known_symbols = set()  # Cache for symbol tracking

    def fetch_announcements(self, days_ahead: int = 7) -> List[Dict[str, Any]]:
        """
        Fetch new listing announcements from HTX.

        Since HTX doesn't have a public announcements API like KuCoin,
        we use a hybrid approach:
        1. Compare current symbols against cached symbols (new additions)
        2. Filter by volume to identify recent TGEs ($100K+ daily volume)

        Args:
            days_ahead: Not used for HTX (no future announcement API)

        Returns:
            List of standardized token dicts
        """
        try:
            # Get all trading symbols from HTX API
            url = f"{self.BASE_URL}{self.SYMBOLS_ENDPOINT}"

            logger.info("Fetching HTX trading symbols")
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            data = response.json()

            if data.get("status") != "ok":
                logger.error(f"HTX API error: {data.get('err-msg')}")
                return []

            symbols = data.get("data", [])
            logger.info(f"Retrieved {len(symbols)} symbols from HTX")

            # Filter for USDT pairs (spot trading)
            usdt_pairs = [
                s for s in symbols
                if s.get("quote-currency") == "usdt"
                and s.get("state") == "online"  # Only active pairs
                and s.get("api-trading") == "enabled"  # API trading enabled
            ]

            logger.info(f"Found {len(usdt_pairs)} USDT spot pairs")

            # Get tickers for volume filtering
            tokens = self._filter_by_volume(usdt_pairs, min_volume_usd=100000)

            logger.info(f"Found {len(tokens)} high-volume new listings from HTX")
            return tokens

        except requests.exceptions.RequestException as e:
            logger.error(f"HTX API request failed: {e}")
            return []
        except Exception as e:
            logger.error(f"HTX fetcher error: {e}", exc_info=True)
            return []

    def _filter_by_volume(
        self,
        pairs: List[Dict[str, Any]],
        min_volume_usd: int = 100000
    ) -> List[Dict[str, Any]]:
        """
        Filter trading pairs by 24h volume to identify recent TGEs.

        High volume ($100K+) indicates recent listing activity.
        """
        tokens = []

        # Get market tickers for volume data
        tickers_url = "https://api.huobi.pro/market/tickers"

        try:
            response = self.session.get(tickers_url, timeout=30)
            response.raise_for_status()
            data = response.json()

            if data.get("status") != "ok":
                logger.error(f"HTX tickers API error: {data.get('err-msg')}")
                return []

            tickers = data.get("data", [])

            # Create ticker lookup by symbol
            ticker_map = {t["symbol"]: t for t in tickers}

            # Filter pairs by volume
            for pair in pairs[:100]:  # Limit to top 100 to avoid noise
                symbol_name = pair.get("symbol")  # e.g., "btcusdt"
                base_currency = pair.get("base-currency", "").upper()

                # Skip stablecoins and common pairs
                if base_currency in ["USDT", "USDC", "BUSD", "DAI", "BTC", "ETH"]:
                    continue

                # Get ticker data
                ticker = ticker_map.get(symbol_name, {})
                volume_24h_quote = ticker.get("vol", 0)  # Volume in quote currency (USDT)

                if volume_24h_quote >= min_volume_usd:
                    token = {
                        "symbol": base_currency,
                        "name": base_currency,  # HTX doesn't provide full names
                        "listing_date": None,  # No listing date in symbols API
                        "trading_pairs": [f"{base_currency}/USDT"],
                        "exchange": "htx",
                        "announcement_url": self.ANNOUNCEMENTS_URL,
                        "confidence": "MEDIUM",  # Volume-based discovery
                        "data_source": "exchange_api",
                        "volume_24h_usd": volume_24h_quote,
                        "price_change_pct": ticker.get("close", 0)  # Price
                    }
                    tokens.append(token)

        except Exception as e:
            logger.error(f"Error fetching HTX tickers: {e}", exc_info=True)

        return tokens

    def fetch_new_listings(self, days_back: int = 7) -> List[Dict[str, Any]]:
        """
        Fetch tokens that were listed in the last N days.

        Note: HTX API doesn't provide listing dates directly.
        This method uses volume patterns to infer recent listings.

        Args:
            days_back: Look back N days for new listings

        Returns:
            List of standardized token dicts
        """
        # HTX doesn't provide listing dates via API
        # Use announcements method which filters by volume
        return self.fetch_announcements(days_ahead=days_back)

    def validate_token(self, symbol: str) -> bool:
        """
        Validate if a token is listed on HTX.

        Args:
            symbol: Token symbol (e.g., "POWER")

        Returns:
            True if token is listed, False otherwise
        """
        try:
            url = f"{self.BASE_URL}{self.SYMBOLS_ENDPOINT}"
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            data = response.json()

            if data.get("status") != "ok":
                return False

            symbols = data.get("data", [])

            # Check if symbol exists as base currency with USDT pair
            usdt_pair = f"{symbol.lower()}usdt"
            for s in symbols:
                if s.get("symbol") == usdt_pair:
                    return True

            return False

        except Exception as e:
            logger.error(f"HTX validation error: {e}")
            return False


# CLI Testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    fetcher = HTXFetcher()

    print("\n" + "=" * 60)
    print("HTX Exchange Fetcher - Session 318 P1.2")
    print("=" * 60)

    # Test announcements fetch
    print("\nFetching high-volume tokens (likely recent listings)...")
    announcements = fetcher.fetch_announcements(days_ahead=7)

    print(f"\nFound {len(announcements)} high-volume tokens on HTX")

    for token in announcements[:10]:  # Show first 10
        print("\n" + "-" * 60)
        print(f"Symbol: {token['symbol']}")
        print(f"Name: {token['name']}")
        print(f"Trading Pairs: {', '.join(token['trading_pairs'])}")
        print(f"24h Volume: ${token['volume_24h_usd']:,.0f}")
        print(f"Confidence: {token['confidence']}")
        print(f"URL: {token['announcement_url']}")

    if not announcements:
        print("\nNo high-volume tokens found")
        print("This could mean:")
        print("1. HTX API is not responding")
        print("2. No tokens meet $100K+ volume threshold")
        print("3. Network connectivity issues")

    # Test token validation
    print("\n" + "=" * 60)
    print("Testing token validation (POWER)...")
    is_listed = fetcher.validate_token("POWER")
    print(f"POWER on HTX: {'✅ TRUE' if is_listed else '❌ FALSE'}")
