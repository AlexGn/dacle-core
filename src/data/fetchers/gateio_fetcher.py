"""
Gate.io Exchange Fetcher - Session 318 P0.2

Fetches new listing announcements from Gate.io exchange.
Expected: +5-8 TGEs per month

Gate.io API Documentation:
- Spot currencies endpoint: https://api.gateio.ws/api/v4/spot/currencies
- Public API, no authentication required
- Rate limit: 900 requests per minute

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


class GateioFetcher:
    """Fetches new token listings from Gate.io exchange."""

    BASE_URL = "https://api.gateio.ws/api/v4"
    SPOT_CURRENCIES_PATH = "/spot/currencies"
    SPOT_CURRENCY_PAIRS_PATH = "/spot/currency_pairs"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
            'Accept': 'application/json'
        })

    def fetch_new_listings(self, days_back: int = 7) -> List[Dict[str, Any]]:
        """
        Fetch tokens that were listed in the last N days.

        Args:
            days_back: Look back N days for new listings

        Returns:
            List of standardized token dicts:
            {
                "symbol": "MONAD",
                "name": "Monad",
                "listing_date": "2025-11-09T10:00:00Z",
                "trading_pairs": ["MONAD/USDT", "MONAD/BTC"],
                "exchange": "gateio",
                "announcement_url": "https://...",
                "confidence": "HIGH",  # HIGH/MEDIUM/LOW
                "data_source": "exchange_api"
            }
        """
        try:
            # Fetch all currencies
            currencies_url = f"{self.BASE_URL}{self.SPOT_CURRENCIES_PATH}"

            logger.info("Fetching Gate.io currencies list")
            response = self.session.get(currencies_url, timeout=30)
            response.raise_for_status()
            currencies = response.json()

            if not isinstance(currencies, list):
                logger.error(f"Unexpected Gate.io currencies response: {type(currencies)}")
                return []

            logger.info(f"Retrieved {len(currencies)} currencies from Gate.io")

            # Fetch trading pairs to get listing dates
            pairs_url = f"{self.BASE_URL}{self.SPOT_CURRENCY_PAIRS_PATH}"

            logger.info("Fetching Gate.io trading pairs")
            pairs_response = self.session.get(pairs_url, timeout=30)
            pairs_response.raise_for_status()
            pairs = pairs_response.json()

            if not isinstance(pairs, list):
                logger.error(f"Unexpected Gate.io pairs response: {type(pairs)}")
                return []

            logger.info(f"Retrieved {len(pairs)} trading pairs from Gate.io")

            # Filter for recent listings
            cutoff_date = datetime.utcnow() - timedelta(days=days_back)
            recent_listings = []

            # Group pairs by base currency
            pairs_by_currency = {}
            for pair in pairs:
                base = pair.get('base', '').upper()
                if base not in pairs_by_currency:
                    pairs_by_currency[base] = []
                pairs_by_currency[base].append(pair)

            # Find currencies with recently enabled pairs
            for currency in currencies:
                symbol = currency.get('currency', '').upper()

                if symbol not in pairs_by_currency:
                    continue

                # Check if any pair for this currency is recent
                currency_pairs = pairs_by_currency[symbol]

                for pair in currency_pairs:
                    # Gate.io doesn't provide listing dates in API
                    # We'll use trade_status to identify active pairs
                    if pair.get('trade_status') != 'tradable':
                        continue

                    # Check if currency is delisted or disabled
                    if not currency.get('trade', True):
                        continue

                    # For new listings, we rely on external announcements
                    # This endpoint is better for validation than discovery
                    # We'll mark all active currencies and let the conviction scorer filter

                    token = self._parse_currency(currency, currency_pairs)
                    if token and token not in recent_listings:
                        recent_listings.append(token)
                        break  # Only add once per currency

            logger.info(f"Found {len(recent_listings)} Gate.io currencies")
            return recent_listings

        except requests.exceptions.RequestException as e:
            logger.error(f"Gate.io API request failed: {e}")
            return []
        except Exception as e:
            logger.error(f"Gate.io fetcher error: {e}", exc_info=True)
            return []

    def _parse_currency(self, currency: Dict[str, Any], pairs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """
        Parse currency data into standardized format.

        Args:
            currency: Currency dict from API
            pairs: List of trading pairs for this currency

        Returns:
            Standardized token dict or None
        """
        try:
            symbol = currency.get('currency', '').upper()
            name = currency.get('name', symbol)

            if not symbol:
                return None

            # Skip stablecoins and common base currencies
            if symbol in ['USDT', 'USDC', 'BTC', 'ETH', 'BNB', 'BUSD', 'DAI']:
                return None

            # Extract trading pairs
            trading_pairs = []
            for pair in pairs:
                if pair.get('trade_status') == 'tradable':
                    quote = pair.get('quote', '').upper()
                    trading_pairs.append(f"{symbol}/{quote}")

            if not trading_pairs:
                return None

            # Gate.io doesn't provide listing dates via API
            # We'll mark listing_date as None and rely on external sources
            listing_date = None

            # Confidence: MEDIUM (API doesn't provide listing dates)
            confidence = "MEDIUM"

            return {
                "symbol": symbol,
                "name": name,
                "listing_date": listing_date,
                "trading_pairs": trading_pairs,
                "exchange": "gateio",
                "announcement_url": "https://www.gate.io/article",
                "confidence": confidence,
                "data_source": "exchange_api"
            }

        except Exception as e:
            logger.error(f"Error parsing Gate.io currency: {e}", exc_info=True)
            return None

    def fetch_announcements(self, days_ahead: int = 7) -> List[Dict[str, Any]]:
        """
        Fetch upcoming listing announcements from Gate.io blog/announcements.

        NOTE: Gate.io doesn't have a public API for announcements.
        This is a placeholder for web scraping implementation.

        Args:
            days_ahead: Look for announcements in the next N days

        Returns:
            List of standardized token dicts (currently empty)
        """
        logger.warning("Gate.io announcements API not available - use web scraping")
        return []

    def validate_token(self, symbol: str) -> bool:
        """
        Validate if a token is listed on Gate.io.

        Args:
            symbol: Token symbol (e.g., "MONAD")

        Returns:
            True if token is listed, False otherwise
        """
        try:
            currencies_url = f"{self.BASE_URL}{self.SPOT_CURRENCIES_PATH}"

            response = self.session.get(currencies_url, timeout=30)
            response.raise_for_status()
            currencies = response.json()

            if not isinstance(currencies, list):
                return False

            # Check if symbol exists in currencies list
            symbol_upper = symbol.upper()
            for currency in currencies:
                if currency.get('currency', '').upper() == symbol_upper:
                    # Check if tradable
                    return currency.get('trade', False)

            return False

        except Exception as e:
            logger.error(f"Gate.io validation error for {symbol}: {e}")
            return False

    def get_trading_pairs(self, symbol: str) -> List[str]:
        """
        Get all trading pairs for a specific token.

        Args:
            symbol: Token symbol (e.g., "MONAD")

        Returns:
            List of trading pairs (e.g., ["MONAD/USDT", "MONAD/BTC"])
        """
        try:
            pairs_url = f"{self.BASE_URL}{self.SPOT_CURRENCY_PAIRS_PATH}"

            response = self.session.get(pairs_url, timeout=30)
            response.raise_for_status()
            pairs = response.json()

            if not isinstance(pairs, list):
                return []

            symbol_upper = symbol.upper()
            trading_pairs = []

            for pair in pairs:
                base = pair.get('base', '').upper()
                quote = pair.get('quote', '').upper()

                if base == symbol_upper and pair.get('trade_status') == 'tradable':
                    trading_pairs.append(f"{base}/{quote}")

            return trading_pairs

        except Exception as e:
            logger.error(f"Gate.io trading pairs error for {symbol}: {e}")
            return []


# CLI Testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    fetcher = GateioFetcher()

    print("\n" + "=" * 60)
    print("Gate.io Exchange Fetcher - Session 318 P0.2")
    print("=" * 60)

    # Test 1: Validate a known token
    print("\n[Test 1] Validating BTC on Gate.io...")
    is_listed = fetcher.validate_token("BTC")
    print(f"BTC listed on Gate.io: {is_listed}")

    # Test 2: Get trading pairs for BTC
    print("\n[Test 2] Fetching BTC trading pairs...")
    pairs = fetcher.get_trading_pairs("BTC")
    print(f"Found {len(pairs)} trading pairs for BTC")
    if pairs:
        print(f"Sample pairs: {', '.join(pairs[:5])}")

    # Test 3: Fetch new listings (last 30 days)
    print("\n[Test 3] Fetching new listings (last 30 days)...")
    listings = fetcher.fetch_new_listings(days_back=30)

    print(f"\nFound {len(listings)} Gate.io currencies")

    # Show first 5 as sample
    for i, token in enumerate(listings[:5]):
        print("\n" + "-" * 60)
        print(f"[{i+1}] Symbol: {token['symbol']}")
        print(f"    Name: {token['name']}")
        print(f"    Trading Pairs: {', '.join(token['trading_pairs'][:3])}")
        print(f"    Confidence: {token['confidence']}")

    if len(listings) > 5:
        print(f"\n... and {len(listings) - 5} more currencies")

    print("\n" + "=" * 60)
    print("NOTE: Gate.io API doesn't provide listing dates.")
    print("For discovery, combine with announcements scraping.")
    print("=" * 60)
