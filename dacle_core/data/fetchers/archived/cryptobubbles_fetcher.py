"""
CryptoBubbles Data Fetcher - Visual Bubble Chart Data

Session 336: Added based on David's Notion data source list.

CryptoBubbles provides:
- Top gainers/losers with visual bubble representation
- Multiple timeframes (1h, 24h, 7d, 30d, 1y)
- Good for spotting momentum in altcoins

URL: https://cryptobubbles.net/

Note: CryptoBubbles doesn't have a public API, so we scrape the data
from their backend API endpoint used by the website.

Rate Limit: Conservative (1 req/10sec recommended)
Cost: $0/month
"""

import logging
import requests
from typing import Dict, List, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class CryptoBubblesFetcher:
    """
    Fetches bubble chart data from CryptoBubbles.

    Uses the backend API endpoint that powers the website's bubble visualization.
    """

    # The actual API endpoint used by cryptobubbles.net frontend
    BASE_URL = "https://cryptobubbles.net/backend"
    TIMEOUT = 30

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://cryptobubbles.net/",
            "Origin": "https://cryptobubbles.net",
        })

    def _fetch_bubbles_data(self) -> List[Dict]:
        """
        Fetch raw bubbles data from the backend.

        Returns:
            List of bubble dicts with performance data
        """
        try:
            # Main data endpoint for the bubble chart
            url = f"{self.BASE_URL}/data/bubbles1000.usd.json"

            response = self.session.get(url, timeout=self.TIMEOUT)
            response.raise_for_status()
            return response.json()

        except Exception as e:
            logger.error(f"CryptoBubbles data fetch failed: {e}")
            return []

    def get_top_gainers(self, limit: int = 50, min_market_cap: float = 1_000_000, timeframe: str = "24h") -> List[Dict]:
        """
        Get top gaining tokens by price change.

        Args:
            limit: Number of top gainers to return
            min_market_cap: Minimum market cap filter (default $1M)
            timeframe: "1h", "24h", "7d", "30d", "1y"

        Returns:
            List of token dicts sorted by gain descending
        """
        # Map timeframe to data field
        timeframe_map = {
            "1h": "hour",
            "24h": "day",
            "7d": "week",
            "30d": "month",
            "1y": "year"
        }

        perf_field = timeframe_map.get(timeframe, "day")

        try:
            bubbles = self._fetch_bubbles_data()
            if not bubbles:
                return []

            results = []
            for bubble in bubbles:
                # Get market cap - may be in different formats
                mc = bubble.get('marketcap') or bubble.get('market_cap') or 0
                if isinstance(mc, str):
                    mc = float(mc.replace(',', ''))

                # Get performance for selected timeframe
                performance = bubble.get('performance', {})
                pct_change = performance.get(perf_field) or 0

                if mc >= min_market_cap and pct_change > 0:
                    results.append({
                        "symbol": bubble.get('symbol', '').upper(),
                        "name": bubble.get('name'),
                        "price_usd": bubble.get('price'),
                        "market_cap": mc,
                        "volume_24h": bubble.get('volume'),
                        "percent_change_1h": performance.get('hour'),
                        "percent_change_24h": performance.get('day'),
                        "percent_change_7d": performance.get('week'),
                        "percent_change_30d": performance.get('month'),
                        "rank": bubble.get('rank'),
                        "dominance": bubble.get('dominance'),
                        "data_source": f"cryptobubbles_gainers_{timeframe}",
                        "fetched_at": datetime.now(timezone.utc).isoformat()
                    })

            # Sort by the selected timeframe's change
            results.sort(key=lambda x: x.get(f'percent_change_{timeframe}', 0) or 0, reverse=True)

            logger.info(f"CryptoBubbles: Found {len(results[:limit])} gainers ({timeframe}) with MC>=${min_market_cap/1e6:.1f}M")
            return results[:limit]

        except Exception as e:
            logger.error(f"CryptoBubbles get_top_gainers failed: {e}")
            return []

    def get_top_losers(self, limit: int = 50, min_market_cap: float = 1_000_000, timeframe: str = "24h") -> List[Dict]:
        """
        Get top losing tokens by price change (for LONG opportunities).

        Args:
            limit: Number of top losers to return
            min_market_cap: Minimum market cap filter
            timeframe: "1h", "24h", "7d", "30d", "1y"

        Returns:
            List of token dicts sorted by loss (most negative first)
        """
        timeframe_map = {
            "1h": "hour",
            "24h": "day",
            "7d": "week",
            "30d": "month",
            "1y": "year"
        }

        perf_field = timeframe_map.get(timeframe, "day")

        try:
            bubbles = self._fetch_bubbles_data()
            if not bubbles:
                return []

            results = []
            for bubble in bubbles:
                mc = bubble.get('marketcap') or bubble.get('market_cap') or 0
                if isinstance(mc, str):
                    mc = float(mc.replace(',', ''))

                performance = bubble.get('performance', {})
                pct_change = performance.get(perf_field) or 0

                if mc >= min_market_cap and pct_change < 0:
                    results.append({
                        "symbol": bubble.get('symbol', '').upper(),
                        "name": bubble.get('name'),
                        "price_usd": bubble.get('price'),
                        "market_cap": mc,
                        "volume_24h": bubble.get('volume'),
                        "percent_change_1h": performance.get('hour'),
                        "percent_change_24h": performance.get('day'),
                        "percent_change_7d": performance.get('week'),
                        "percent_change_30d": performance.get('month'),
                        "rank": bubble.get('rank'),
                        "data_source": f"cryptobubbles_losers_{timeframe}",
                        "fetched_at": datetime.now(timezone.utc).isoformat()
                    })

            # Sort by the selected timeframe's change (ascending = most negative first)
            results.sort(key=lambda x: x.get(f'percent_change_{timeframe}', 0) or 0)

            logger.info(f"CryptoBubbles: Found {len(results[:limit])} losers ({timeframe}) with MC>=${min_market_cap/1e6:.1f}M")
            return results[:limit]

        except Exception as e:
            logger.error(f"CryptoBubbles get_top_losers failed: {e}")
            return []

    def get_momentum_extremes(self, limit: int = 20, min_market_cap: float = 5_000_000) -> Dict[str, List[Dict]]:
        """
        Get tokens showing extreme momentum (potential reversal candidates).

        Finds tokens that are:
        - Overextended: +30% in 24h (SHORT candidates)
        - Oversold: -30% in 24h (LONG candidates)

        Args:
            limit: Number of tokens per category
            min_market_cap: Minimum market cap filter

        Returns:
            Dict with 'overextended' and 'oversold' lists
        """
        try:
            bubbles = self._fetch_bubbles_data()
            if not bubbles:
                return {"overextended": [], "oversold": []}

            overextended = []
            oversold = []

            for bubble in bubbles:
                mc = bubble.get('marketcap') or bubble.get('market_cap') or 0
                if isinstance(mc, str):
                    mc = float(mc.replace(',', ''))

                if mc < min_market_cap:
                    continue

                performance = bubble.get('performance', {})
                pct_24h = performance.get('day') or 0

                token_data = {
                    "symbol": bubble.get('symbol', '').upper(),
                    "name": bubble.get('name'),
                    "price_usd": bubble.get('price'),
                    "market_cap": mc,
                    "percent_change_24h": pct_24h,
                    "percent_change_7d": performance.get('week'),
                    "data_source": "cryptobubbles_momentum",
                    "fetched_at": datetime.now(timezone.utc).isoformat()
                }

                if pct_24h >= 30:
                    token_data["momentum_type"] = "OVEREXTENDED"
                    overextended.append(token_data)
                elif pct_24h <= -30:
                    token_data["momentum_type"] = "OVERSOLD"
                    oversold.append(token_data)

            # Sort
            overextended.sort(key=lambda x: x.get('percent_change_24h', 0), reverse=True)
            oversold.sort(key=lambda x: x.get('percent_change_24h', 0))

            logger.info(f"CryptoBubbles: Found {len(overextended[:limit])} overextended, {len(oversold[:limit])} oversold")

            return {
                "overextended": overextended[:limit],
                "oversold": oversold[:limit]
            }

        except Exception as e:
            logger.error(f"CryptoBubbles get_momentum_extremes failed: {e}")
            return {"overextended": [], "oversold": []}


def main():
    """Test the CryptoBubbles fetcher."""

    fetcher = CryptoBubblesFetcher()

    print("=" * 60)
    print("CryptoBubbles API Test")
    print("=" * 60)

    # Test top gainers
    print("\n📈 Top 10 Gainers (24h, MC>$5M):")
    gainers = fetcher.get_top_gainers(limit=10, min_market_cap=5_000_000)
    if gainers:
        for g in gainers:
            pct = g.get('percent_change_24h') or 0
            print(f"  {g['symbol']:8s} +{pct:6.1f}%  MC: ${g['market_cap']/1e6:,.1f}M")
    else:
        print("  (No data - API may require different approach)")

    # Test top losers
    print("\n📉 Top 5 Losers (24h, MC>$5M):")
    losers = fetcher.get_top_losers(limit=5, min_market_cap=5_000_000)
    if losers:
        for l in losers:
            pct = l.get('percent_change_24h') or 0
            print(f"  {l['symbol']:8s} {pct:6.1f}%  MC: ${l['market_cap']/1e6:,.1f}M")
    else:
        print("  (No data - API may require different approach)")

    # Test momentum extremes
    print("\n🎯 Momentum Extremes:")
    extremes = fetcher.get_momentum_extremes(limit=5, min_market_cap=5_000_000)
    if extremes['overextended']:
        print("  Overextended (SHORT candidates):")
        for o in extremes['overextended'][:3]:
            print(f"    {o['symbol']:8s} +{o['percent_change_24h']:.1f}%")
    if extremes['oversold']:
        print("  Oversold (LONG candidates):")
        for o in extremes['oversold'][:3]:
            print(f"    {o['symbol']:8s} {o['percent_change_24h']:.1f}%")


if __name__ == "__main__":
    main()
