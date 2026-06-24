"""
CoinPaprika API Fetcher - FREE, No Authentication Required

Session 336: Added based on David's recommendation for discovering gainers.

API Documentation: https://api.coinpaprika.com/
Rate Limit: 10 requests/second (very generous)
Cost: $0/month

Key Endpoints:
- /tickers - All coins with price/volume/change data
- /coins/{coin_id} - Detailed coin info
- /coins/{coin_id}/ohlcv/today - OHLCV data

Use Case: Discover tokens pumping (top gainers) for SHORT opportunities
"""

import logging
import requests
from typing import Dict, List, Optional, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class CoinPaprikaFetcher:
    """
    Fetches token data from CoinPaprika API.

    FREE API with no authentication required.
    Generous rate limits (10 req/sec).
    """

    BASE_URL = "https://api.coinpaprika.com/v1"
    TIMEOUT = 30

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "DACLE-TGE-Scanner/1.0"
        })

    def get_top_gainers(self, limit: int = 50, min_market_cap: float = 1_000_000) -> List[Dict]:
        """
        Get top gaining tokens by 24h price change.

        Args:
            limit: Number of top gainers to return
            min_market_cap: Minimum market cap filter (default $1M)

        Returns:
            List of token dicts sorted by 24h gain descending
        """
        try:
            url = f"{self.BASE_URL}/tickers"
            response = self.session.get(url, timeout=self.TIMEOUT)
            response.raise_for_status()

            tickers = response.json()

            # Filter and sort
            valid_tickers = []
            for t in tickers:
                quotes = t.get("quotes", {}).get("USD", {})
                mc = quotes.get("market_cap") or 0
                pct_24h = quotes.get("percent_change_24h") or 0

                # Filter by market cap and positive gain
                if mc >= min_market_cap and pct_24h > 0:
                    valid_tickers.append({
                        "symbol": t.get("symbol", "").upper(),
                        "name": t.get("name"),
                        "coinpaprika_id": t.get("id"),
                        "rank": t.get("rank"),
                        "price_usd": quotes.get("price"),
                        "volume_24h": quotes.get("volume_24h"),
                        "market_cap": mc,
                        "percent_change_1h": quotes.get("percent_change_1h"),
                        "percent_change_24h": pct_24h,
                        "percent_change_7d": quotes.get("percent_change_7d"),
                        "percent_change_30d": quotes.get("percent_change_30d"),
                        "ath_price": quotes.get("ath_price"),
                        "ath_date": quotes.get("ath_date"),
                        "percent_from_ath": quotes.get("percent_from_price_ath"),
                        "data_source": "coinpaprika_gainers",
                        "fetched_at": datetime.utcnow().isoformat()
                    })

            # Sort by 24h gain descending
            valid_tickers.sort(key=lambda x: x["percent_change_24h"], reverse=True)

            logger.info(f"CoinPaprika: Found {len(valid_tickers)} gainers, returning top {limit}")
            return valid_tickers[:limit]

        except Exception as e:
            logger.error(f"CoinPaprika get_top_gainers failed: {e}")
            return []

    def get_top_losers(self, limit: int = 50, min_market_cap: float = 1_000_000) -> List[Dict]:
        """
        Get top losing tokens by 24h price change (for LONG opportunities).

        Args:
            limit: Number of top losers to return
            min_market_cap: Minimum market cap filter

        Returns:
            List of token dicts sorted by 24h loss descending
        """
        try:
            url = f"{self.BASE_URL}/tickers"
            response = self.session.get(url, timeout=self.TIMEOUT)
            response.raise_for_status()

            tickers = response.json()

            # Filter and sort
            valid_tickers = []
            for t in tickers:
                quotes = t.get("quotes", {}).get("USD", {})
                mc = quotes.get("market_cap") or 0
                pct_24h = quotes.get("percent_change_24h") or 0

                # Filter by market cap and negative change
                if mc >= min_market_cap and pct_24h < 0:
                    valid_tickers.append({
                        "symbol": t.get("symbol", "").upper(),
                        "name": t.get("name"),
                        "coinpaprika_id": t.get("id"),
                        "price_usd": quotes.get("price"),
                        "volume_24h": quotes.get("volume_24h"),
                        "market_cap": mc,
                        "percent_change_24h": pct_24h,
                        "percent_change_7d": quotes.get("percent_change_7d"),
                        "percent_from_ath": quotes.get("percent_from_price_ath"),
                        "data_source": "coinpaprika_losers",
                        "fetched_at": datetime.utcnow().isoformat()
                    })

            # Sort by 24h loss (most negative first)
            valid_tickers.sort(key=lambda x: x["percent_change_24h"])

            logger.info(f"CoinPaprika: Found {len(valid_tickers)} losers, returning top {limit}")
            return valid_tickers[:limit]

        except Exception as e:
            logger.error(f"CoinPaprika get_top_losers failed: {e}")
            return []

    def get_token_details(self, coin_id: str) -> Optional[Dict]:
        """
        Get detailed info for a specific token.

        Args:
            coin_id: CoinPaprika coin ID (e.g., 'btc-bitcoin')

        Returns:
            Token details dict or None
        """
        try:
            url = f"{self.BASE_URL}/coins/{coin_id}"
            response = self.session.get(url, timeout=self.TIMEOUT)
            response.raise_for_status()

            data = response.json()

            return {
                "symbol": data.get("symbol", "").upper(),
                "name": data.get("name"),
                "coinpaprika_id": data.get("id"),
                "type": data.get("type"),  # 'coin' or 'token'
                "is_active": data.get("is_active"),
                "description": data.get("description"),
                "started_at": data.get("started_at"),  # Launch date!
                "first_data_at": data.get("first_data_at"),
                "tags": [t.get("name") for t in data.get("tags", [])],
                "team": [{"name": p.get("name"), "position": p.get("position")}
                        for p in data.get("team", [])[:5]],
                "whitepaper": data.get("whitepaper", {}).get("link"),
                "links": {
                    "website": data.get("links", {}).get("website", [None])[0] if data.get("links", {}).get("website") else None,
                    "twitter": data.get("links", {}).get("twitter", [None])[0] if data.get("links", {}).get("twitter") else None,
                    "telegram": data.get("links", {}).get("telegram", [None])[0] if data.get("links", {}).get("telegram") else None,
                },
                "data_source": "coinpaprika_details",
                "fetched_at": datetime.utcnow().isoformat()
            }

        except Exception as e:
            logger.error(f"CoinPaprika get_token_details({coin_id}) failed: {e}")
            return None

    def search_by_symbol(self, symbol: str) -> Optional[str]:
        """
        Find CoinPaprika coin ID by symbol.

        Args:
            symbol: Token symbol (e.g., 'BTC')

        Returns:
            CoinPaprika coin ID or None
        """
        try:
            url = f"{self.BASE_URL}/search"
            params = {"q": symbol, "c": "currencies", "limit": 10}
            response = self.session.get(url, params=params, timeout=self.TIMEOUT)
            response.raise_for_status()

            data = response.json()
            currencies = data.get("currencies", [])

            # Find exact symbol match
            for c in currencies:
                if c.get("symbol", "").upper() == symbol.upper():
                    return c.get("id")

            # Return first result if no exact match
            if currencies:
                return currencies[0].get("id")

            return None

        except Exception as e:
            logger.error(f"CoinPaprika search({symbol}) failed: {e}")
            return None


def main():
    """Test the CoinPaprika fetcher."""
    import json

    fetcher = CoinPaprikaFetcher()

    print("=" * 60)
    print("CoinPaprika API Test")
    print("=" * 60)

    # Test top gainers
    print("\n📈 Top 10 Gainers (24h):")
    gainers = fetcher.get_top_gainers(limit=10, min_market_cap=5_000_000)
    for g in gainers:
        print(f"  {g['symbol']:8s} +{g['percent_change_24h']:6.1f}%  MC: ${g['market_cap']/1e6:,.1f}M")

    # Test top losers
    print("\n📉 Top 5 Losers (24h):")
    losers = fetcher.get_top_losers(limit=5, min_market_cap=5_000_000)
    for l in losers:
        print(f"  {l['symbol']:8s} {l['percent_change_24h']:6.1f}%  MC: ${l['market_cap']/1e6:,.1f}M")

    # Test token details
    if gainers:
        coin_id = gainers[0].get("coinpaprika_id")
        if coin_id:
            print(f"\n📋 Details for {gainers[0]['symbol']}:")
            details = fetcher.get_token_details(coin_id)
            if details:
                print(f"  Type: {details.get('type')}")
                print(f"  Started: {details.get('started_at')}")
                print(f"  Tags: {', '.join(details.get('tags', [])[:5])}")


if __name__ == "__main__":
    main()
