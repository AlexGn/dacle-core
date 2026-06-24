"""
CoinCodex Data Fetcher - Gainers/Losers Discovery

Session 336: Added based on David's Notion data source list.

CoinCodex provides:
- Top gainers and losers sorted by 24h change
- Public API (no auth required)
- Good coverage of altcoins

URL: https://coincodex.com/gainers-losers/

Rate Limit: Conservative (1 req/5sec recommended)
Cost: $0/month
"""

import logging
import requests
from typing import Dict, List, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class CoinCodexFetcher:
    """
    Fetches top gainers and losers from CoinCodex.

    Uses the cached coins JSON endpoint (no auth required).
    """

    # Use the cached JSON endpoint that works
    CACHE_URL = "https://coincodex.com/apps/coincodex/cache/all_coins.json"
    TIMEOUT = 30

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        })
        self._coins_cache = None
        self._cache_time = None

    def _fetch_all_coins(self) -> List[Dict]:
        """Fetch all coins from the cached endpoint (with 5-min local cache)."""
        # Check if we have a recent cache
        if self._coins_cache and self._cache_time:
            age = (datetime.now(timezone.utc) - self._cache_time).total_seconds()
            if age < 300:  # 5-minute cache
                return self._coins_cache

        try:
            response = self.session.get(self.CACHE_URL, timeout=self.TIMEOUT)
            response.raise_for_status()
            self._coins_cache = response.json()
            self._cache_time = datetime.now(timezone.utc)
            return self._coins_cache
        except Exception as e:
            logger.error(f"CoinCodex fetch all coins failed: {e}")
            return self._coins_cache or []

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
            coins = self._fetch_all_coins()
            if not coins:
                return []

            results = []
            for coin in coins:
                mc = coin.get('market_cap_usd') or 0
                pct_24h = coin.get('price_change_1D_percent') or 0

                # Filter by market cap and positive gain
                if mc >= min_market_cap and pct_24h > 0:
                    results.append({
                        "symbol": coin.get('symbol', '').upper(),
                        "name": coin.get('name'),
                        "coincodex_id": coin.get('shortname'),
                        "price_usd": coin.get('last_price_usd'),
                        "market_cap": mc,
                        "volume_24h": coin.get('volume_24_usd'),
                        "percent_change_1h": coin.get('price_change_1H_percent'),
                        "percent_change_24h": pct_24h,
                        "percent_change_7d": coin.get('price_change_7D_percent'),
                        "percent_change_30d": coin.get('price_change_30D_percent'),
                        "data_source": "coincodex_gainers",
                        "fetched_at": datetime.now(timezone.utc).isoformat()
                    })

            # Sort by 24h gain descending
            results.sort(key=lambda x: x.get('percent_change_24h', 0), reverse=True)

            logger.info(f"CoinCodex: Found {len(results[:limit])} gainers with MC>=${min_market_cap/1e6:.1f}M")
            return results[:limit]

        except Exception as e:
            logger.error(f"CoinCodex get_top_gainers failed: {e}")
            return []

    def get_top_losers(self, limit: int = 50, min_market_cap: float = 1_000_000) -> List[Dict]:
        """
        Get top losing tokens by 24h price change (for LONG opportunities).

        Args:
            limit: Number of top losers to return
            min_market_cap: Minimum market cap filter

        Returns:
            List of token dicts sorted by 24h loss (most negative first)
        """
        try:
            coins = self._fetch_all_coins()
            if not coins:
                return []

            results = []
            for coin in coins:
                mc = coin.get('market_cap_usd') or 0
                pct_24h = coin.get('price_change_1D_percent') or 0

                # Filter by market cap and negative change
                if mc >= min_market_cap and pct_24h < 0:
                    results.append({
                        "symbol": coin.get('symbol', '').upper(),
                        "name": coin.get('name'),
                        "coincodex_id": coin.get('shortname'),
                        "price_usd": coin.get('last_price_usd'),
                        "market_cap": mc,
                        "volume_24h": coin.get('volume_24_usd'),
                        "percent_change_1h": coin.get('price_change_1H_percent'),
                        "percent_change_24h": pct_24h,
                        "percent_change_7d": coin.get('price_change_7D_percent'),
                        "percent_change_30d": coin.get('price_change_30D_percent'),
                        "data_source": "coincodex_losers",
                        "fetched_at": datetime.now(timezone.utc).isoformat()
                    })

            # Sort by 24h loss (most negative first)
            results.sort(key=lambda x: x.get('percent_change_24h', 0))

            logger.info(f"CoinCodex: Found {len(results[:limit])} losers with MC>=${min_market_cap/1e6:.1f}M")
            return results[:limit]

        except Exception as e:
            logger.error(f"CoinCodex get_top_losers failed: {e}")
            return []

    def get_trending(self, limit: int = 30) -> List[Dict]:
        """
        Get trending tokens based on volume (proxy for trending).

        Args:
            limit: Number of trending tokens to return

        Returns:
            List of trending token dicts sorted by 24h volume
        """
        try:
            coins = self._fetch_all_coins()
            if not coins:
                return []

            # Filter coins with valid volume data and sort by volume
            valid_coins = [c for c in coins if c.get('volume_24_usd') and c.get('volume_24_usd') > 0]
            valid_coins.sort(key=lambda x: x.get('volume_24_usd', 0), reverse=True)

            results = []
            for i, coin in enumerate(valid_coins[:limit], 1):
                results.append({
                    "symbol": coin.get('symbol', '').upper(),
                    "name": coin.get('name'),
                    "coincodex_id": coin.get('shortname'),
                    "price_usd": coin.get('last_price_usd'),
                    "market_cap": coin.get('market_cap_usd'),
                    "volume_24h": coin.get('volume_24_usd'),
                    "percent_change_24h": coin.get('price_change_1D_percent'),
                    "trending_rank": i,
                    "data_source": "coincodex_trending",
                    "fetched_at": datetime.now(timezone.utc).isoformat()
                })

            logger.info(f"CoinCodex: Found {len(results)} trending tokens")
            return results

        except Exception as e:
            logger.error(f"CoinCodex get_trending failed: {e}")
            return []


def main():
    """Test the CoinCodex fetcher."""
    import json

    fetcher = CoinCodexFetcher()

    print("=" * 60)
    print("CoinCodex API Test")
    print("=" * 60)

    # Test top gainers
    print("\n📈 Top 10 Gainers (24h, MC>$5M):")
    gainers = fetcher.get_top_gainers(limit=10, min_market_cap=5_000_000)
    for g in gainers:
        print(f"  {g['symbol']:8s} +{g['percent_change_24h']:6.1f}%  MC: ${g['market_cap']/1e6:,.1f}M")

    # Test top losers
    print("\n📉 Top 5 Losers (24h, MC>$5M):")
    losers = fetcher.get_top_losers(limit=5, min_market_cap=5_000_000)
    for l in losers:
        print(f"  {l['symbol']:8s} {l['percent_change_24h']:6.1f}%  MC: ${l['market_cap']/1e6:,.1f}M")

    # Test trending
    print("\n🔥 Top 5 Trending (by volume):")
    trending = fetcher.get_trending(limit=5)
    for t in trending:
        print(f"  #{t['trending_rank']:2d} {t['symbol']:8s}  Vol: ${t['volume_24h']/1e9:,.1f}B")


if __name__ == "__main__":
    main()
