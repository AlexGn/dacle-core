"""
CoinMarketCap Data Fetcher - FREE Public API Endpoints

Session 336: Added based on David's recommendation for discovering gainers/trending.

Public API Endpoints (no auth required):
- /data-api/v3/topsearch/rank - Top trending/searched tokens
- /data-api/v3/cryptocurrency/listing - Gainers/losers sorted by 24h change

Rate Limit: ~30 requests/minute (conservative estimate)
Cost: $0/month

Use Cases:
- Discover trending tokens for SHORT opportunities (pump detection)
- Find top gainers that may be overextended
- Monitor market sentiment through search trends
"""

import logging
import requests
from typing import Dict, List, Optional, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class CoinMarketCapFetcher:
    """
    Fetches token data from CoinMarketCap public API.

    Uses undocumented public endpoints that don't require API key.
    Rate limits are estimated - be conservative with request frequency.
    """

    BASE_URL = "https://api.coinmarketcap.com"
    TIMEOUT = 30

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        })

    def get_trending(self, limit: int = 30) -> List[Dict]:
        """
        Get top trending/searched tokens on CoinMarketCap.

        Trending tokens often indicate market interest and potential volatility.

        Args:
            limit: Number of trending tokens to return

        Returns:
            List of trending token dicts
        """
        try:
            url = f"{self.BASE_URL}/data-api/v3/topsearch/rank"
            response = self.session.get(url, timeout=self.TIMEOUT)
            response.raise_for_status()

            data = response.json()

            if data.get("status", {}).get("error_code") != "0":
                logger.warning(f"CMC trending API returned error: {data.get('status')}")
                return []

            crypto_list = data.get("data", {}).get("cryptoTopSearchRanks", [])

            results = []
            for item in crypto_list[:limit]:
                results.append({
                    "symbol": item.get("symbol", "").upper(),
                    "name": item.get("name"),
                    "cmc_id": item.get("id"),
                    "cmc_slug": item.get("slug"),
                    "rank": item.get("rank"),
                    "price_usd": item.get("priceChange", {}).get("price"),
                    "percent_change_24h": item.get("priceChange", {}).get("priceChange24h"),
                    "market_cap": item.get("priceChange", {}).get("marketCap"),
                    "volume_24h": item.get("priceChange", {}).get("volume24h"),
                    "trending_rank": len(results) + 1,
                    "data_source": "coinmarketcap_trending",
                    "fetched_at": datetime.utcnow().isoformat()
                })

            logger.info(f"CoinMarketCap: Found {len(results)} trending tokens")
            return results

        except Exception as e:
            logger.error(f"CoinMarketCap get_trending failed: {e}")
            return []

    def get_top_market_cap(self, limit: int = 100) -> List[Dict]:
        """
        Get top cryptocurrencies by market capitalization.

        Args:
            limit: Number of top tokens to return (max 200 per request)

        Returns:
            List of token dicts sorted by market cap descending
        """
        try:
            url = f"{self.BASE_URL}/data-api/v3/cryptocurrency/listing"
            params = {
                "start": 1,
                "limit": limit,
                "sortBy": "market_cap",
                "sortType": "desc",
                "convert": "USD",
                "cryptoType": "all",
                "tagType": "all",
                "audited": "false",
            }

            response = self.session.get(url, params=params, timeout=self.TIMEOUT)
            response.raise_for_status()

            data = response.json()

            if data.get("status", {}).get("error_code") != "0":
                logger.warning(f"CMC market cap API returned error: {data.get('status')}")
                return []

            crypto_list = data.get("data", {}).get("cryptoCurrencyList", [])

            results = []
            for item in crypto_list:
                quotes = item.get("quotes", [])
                usd_quote = next((q for q in quotes if q.get("name") == "USD"), {})

                results.append({
                    "symbol": item.get("symbol", "").upper(),
                    "name": item.get("name"),
                    "cmc_id": item.get("id"),
                    "cmc_slug": item.get("slug"),
                    "cmc_rank": item.get("cmcRank"),
                    "price_usd": usd_quote.get("price"),
                    "volume_24h": usd_quote.get("volume24h"),
                    "market_cap": usd_quote.get("marketCap"),
                    "percent_change_1h": usd_quote.get("percentChange1h"),
                    "percent_change_24h": usd_quote.get("percentChange24h"),
                    "percent_change_7d": usd_quote.get("percentChange7d"),
                    "data_source": "coinmarketcap_top_cap",
                    "fetched_at": datetime.utcnow().isoformat()
                })

            logger.info(f"CoinMarketCap: Found top {len(results)} tokens by market cap")
            return results

        except Exception as e:
            logger.error(f"CoinMarketCap get_top_market_cap failed: {e}")
            return []

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
            url = f"{self.BASE_URL}/data-api/v3/cryptocurrency/listing"
            params = {
                "start": 1,
                "limit": 200,  # Fetch more to filter by market cap
                "sortBy": "percent_change_24h",
                "sortType": "desc",
                "convert": "USD",
                "cryptoType": "all",
                "tagType": "all",
                "audited": "false",
            }

            response = self.session.get(url, params=params, timeout=self.TIMEOUT)
            response.raise_for_status()

            data = response.json()

            if data.get("status", {}).get("error_code") != "0":
                logger.warning(f"CMC gainers API returned error: {data.get('status')}")
                return []

            crypto_list = data.get("data", {}).get("cryptoCurrencyList", [])

            results = []
            for item in crypto_list:
                # Get USD quote data
                quotes = item.get("quotes", [])
                usd_quote = next((q for q in quotes if q.get("name") == "USD"), {})

                mc = usd_quote.get("marketCap") or 0
                pct_24h = usd_quote.get("percentChange24h") or 0

                # Filter by market cap and positive gain
                if mc >= min_market_cap and pct_24h > 0:
                    results.append({
                        "symbol": item.get("symbol", "").upper(),
                        "name": item.get("name"),
                        "cmc_id": item.get("id"),
                        "cmc_slug": item.get("slug"),
                        "cmc_rank": item.get("cmcRank"),
                        "price_usd": usd_quote.get("price"),
                        "volume_24h": usd_quote.get("volume24h"),
                        "market_cap": mc,
                        "percent_change_1h": usd_quote.get("percentChange1h"),
                        "percent_change_24h": pct_24h,
                        "percent_change_7d": usd_quote.get("percentChange7d"),
                        "percent_change_30d": usd_quote.get("percentChange30d"),
                        "circulating_supply": item.get("circulatingSupply"),
                        "total_supply": item.get("totalSupply"),
                        "max_supply": item.get("maxSupply"),
                        "date_added": item.get("dateAdded"),
                        "tags": item.get("tags", []),
                        "data_source": "coinmarketcap_gainers",
                        "fetched_at": datetime.utcnow().isoformat()
                    })

                    if len(results) >= limit:
                        break

            logger.info(f"CoinMarketCap: Found {len(results)} gainers with MC>=${min_market_cap/1e6:.1f}M")
            return results

        except Exception as e:
            logger.error(f"CoinMarketCap get_top_gainers failed: {e}")
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
            url = f"{self.BASE_URL}/data-api/v3/cryptocurrency/listing"
            params = {
                "start": 1,
                "limit": 200,
                "sortBy": "percent_change_24h",
                "sortType": "asc",  # Ascending = most negative first
                "convert": "USD",
                "cryptoType": "all",
                "tagType": "all",
                "audited": "false",
            }

            response = self.session.get(url, params=params, timeout=self.TIMEOUT)
            response.raise_for_status()

            data = response.json()

            if data.get("status", {}).get("error_code") != "0":
                logger.warning(f"CMC losers API returned error: {data.get('status')}")
                return []

            crypto_list = data.get("data", {}).get("cryptoCurrencyList", [])

            results = []
            for item in crypto_list:
                quotes = item.get("quotes", [])
                usd_quote = next((q for q in quotes if q.get("name") == "USD"), {})

                mc = usd_quote.get("marketCap") or 0
                pct_24h = usd_quote.get("percentChange24h") or 0

                # Filter by market cap and negative change
                if mc >= min_market_cap and pct_24h < 0:
                    results.append({
                        "symbol": item.get("symbol", "").upper(),
                        "name": item.get("name"),
                        "cmc_id": item.get("id"),
                        "cmc_slug": item.get("slug"),
                        "price_usd": usd_quote.get("price"),
                        "volume_24h": usd_quote.get("volume24h"),
                        "market_cap": mc,
                        "percent_change_24h": pct_24h,
                        "percent_change_7d": usd_quote.get("percentChange7d"),
                        "date_added": item.get("dateAdded"),
                        "data_source": "coinmarketcap_losers",
                        "fetched_at": datetime.utcnow().isoformat()
                    })

                    if len(results) >= limit:
                        break

            logger.info(f"CoinMarketCap: Found {len(results)} losers with MC>=${min_market_cap/1e6:.1f}M")
            return results

        except Exception as e:
            logger.error(f"CoinMarketCap get_top_losers failed: {e}")
            return []

    def get_categories(self) -> List[Dict]:
        """
        Get all available cryptocurrency categories.

        Session 336: Added for narrative-based discovery.

        Returns:
            List of category dicts with id, name, token count
        """
        try:
            url = f"{self.BASE_URL}/data-api/v3/cryptocurrency/categories"
            params = {
                "start": 1,
                "limit": 100,
            }

            response = self.session.get(url, params=params, timeout=self.TIMEOUT)
            response.raise_for_status()

            data = response.json()

            if data.get("status", {}).get("error_code") != "0":
                logger.warning(f"CMC categories API returned error: {data.get('status')}")
                return []

            categories = data.get("data", [])

            results = []
            for cat in categories:
                results.append({
                    "id": cat.get("id"),
                    "name": cat.get("name"),
                    "title": cat.get("title"),
                    "description": cat.get("description"),
                    "num_tokens": cat.get("numTokens") or cat.get("num_tokens", 0),
                    "market_cap": cat.get("marketCap") or cat.get("market_cap", 0),
                    "market_cap_change_24h": cat.get("marketCapChange") or cat.get("market_cap_change", 0),
                    "volume_24h": cat.get("volume") or cat.get("volume_24h", 0),
                    "volume_change_24h": cat.get("volumeChange") or cat.get("volume_change", 0),
                    "avg_price_change_24h": cat.get("avgPriceChange") or 0,
                    "data_source": "coinmarketcap_categories",
                    "fetched_at": datetime.utcnow().isoformat()
                })

            logger.info(f"CoinMarketCap: Found {len(results)} categories")
            return results

        except Exception as e:
            logger.error(f"CoinMarketCap get_categories failed: {e}")
            return []

    def get_category_tokens(
        self,
        category_id: str,
        limit: int = 50,
        min_market_cap: float = 1_000_000
    ) -> List[Dict]:
        """
        Get tokens in a specific category.

        Session 336: Added for narrative-based discovery (AI, Gaming, DeFi, etc.).

        Args:
            category_id: CMC category ID (e.g., "artificial-intelligence", "gaming")
            limit: Number of tokens to return
            min_market_cap: Minimum market cap filter

        Returns:
            List of token dicts in the category
        """
        try:
            url = f"{self.BASE_URL}/data-api/v3/cryptocurrency/category"
            params = {
                "id": category_id,
                "start": 1,
                "limit": 200,
                "convert": "USD",
            }

            response = self.session.get(url, params=params, timeout=self.TIMEOUT)
            response.raise_for_status()

            data = response.json()

            if data.get("status", {}).get("error_code") != "0":
                logger.warning(f"CMC category tokens API returned error: {data.get('status')}")
                return []

            crypto_list = data.get("data", {}).get("coins", [])

            results = []
            for item in crypto_list:
                mc = item.get("marketCap") or item.get("market_cap") or 0

                if mc >= min_market_cap:
                    results.append({
                        "symbol": item.get("symbol", "").upper(),
                        "name": item.get("name"),
                        "cmc_id": item.get("id"),
                        "cmc_slug": item.get("slug"),
                        "cmc_rank": item.get("cmcRank") or item.get("cmc_rank"),
                        "price_usd": item.get("price"),
                        "volume_24h": item.get("volume24h") or item.get("volume_24h"),
                        "market_cap": mc,
                        "percent_change_1h": item.get("percentChange1h") or item.get("percent_change_1h"),
                        "percent_change_24h": item.get("percentChange24h") or item.get("percent_change_24h"),
                        "percent_change_7d": item.get("percentChange7d") or item.get("percent_change_7d"),
                        "category": category_id,
                        "data_source": f"coinmarketcap_category_{category_id}",
                        "fetched_at": datetime.utcnow().isoformat()
                    })

                    if len(results) >= limit:
                        break

            logger.info(f"CoinMarketCap: Found {len(results)} tokens in category '{category_id}'")
            return results

        except Exception as e:
            logger.error(f"CoinMarketCap get_category_tokens failed: {e}")
            return []

    def get_narrative_movers(
        self,
        narratives: Optional[List[str]] = None,
        limit_per_narrative: int = 10,
        min_market_cap: float = 5_000_000
    ) -> Dict[str, List[Dict]]:
        """
        Get top movers (gainers) in key narrative categories.

        Session 336: Implements David's "catch the narrative" requirement.

        Args:
            narratives: List of narrative category IDs to scan (defaults to key narratives)
            limit_per_narrative: Number of tokens per narrative
            min_market_cap: Minimum market cap filter

        Returns:
            Dict mapping narrative to list of top movers
        """
        if narratives is None:
            # Key narratives for TGE shorting per David's methodology
            narratives = [
                "artificial-intelligence",
                "gaming",
                "defi",
                "layer-1",
                "layer-2",
                "meme-token",
                "real-world-assets-rwa",
                "depin",
                "restaking",
                "modular-blockchain",
            ]

        results = {}

        for narrative in narratives:
            try:
                tokens = self.get_category_tokens(
                    category_id=narrative,
                    limit=limit_per_narrative * 2,  # Fetch more to filter
                    min_market_cap=min_market_cap
                )

                # Sort by 24h gain and take top movers
                tokens_sorted = sorted(
                    [t for t in tokens if (t.get("percent_change_24h") or 0) > 0],
                    key=lambda x: x.get("percent_change_24h", 0),
                    reverse=True
                )[:limit_per_narrative]

                if tokens_sorted:
                    results[narrative] = tokens_sorted
                    logger.debug(f"  {narrative}: {len(tokens_sorted)} top movers")

            except Exception as e:
                logger.warning(f"Failed to get narrative {narrative}: {e}")
                continue

        logger.info(f"CoinMarketCap: Found movers in {len(results)}/{len(narratives)} narratives")
        return results

    def get_recently_added(self, limit: int = 30, days: int = 30) -> List[Dict]:
        """
        Get recently added tokens (potential TGE candidates).

        Args:
            limit: Number of tokens to return
            days: Look back period in days

        Returns:
            List of recently added token dicts
        """
        try:
            url = f"{self.BASE_URL}/data-api/v3/cryptocurrency/listing"
            params = {
                "start": 1,
                "limit": 200,
                "sortBy": "date_added",
                "sortType": "desc",
                "convert": "USD",
                "cryptoType": "all",
                "tagType": "all",
                "audited": "false",
            }

            response = self.session.get(url, params=params, timeout=self.TIMEOUT)
            response.raise_for_status()

            data = response.json()

            if data.get("status", {}).get("error_code") != "0":
                logger.warning(f"CMC recently added API returned error: {data.get('status')}")
                return []

            crypto_list = data.get("data", {}).get("cryptoCurrencyList", [])

            results = []
            cutoff_date = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

            for item in crypto_list:
                date_added_str = item.get("dateAdded")
                if not date_added_str:
                    continue

                try:
                    # Parse ISO format date
                    date_added = datetime.fromisoformat(date_added_str.replace("Z", "+00:00"))
                    days_since = (cutoff_date - date_added.replace(tzinfo=None)).days

                    if days_since > days:
                        continue  # Too old

                except (ValueError, TypeError):
                    continue

                quotes = item.get("quotes", [])
                usd_quote = next((q for q in quotes if q.get("name") == "USD"), {})

                results.append({
                    "symbol": item.get("symbol", "").upper(),
                    "name": item.get("name"),
                    "cmc_id": item.get("id"),
                    "cmc_slug": item.get("slug"),
                    "price_usd": usd_quote.get("price"),
                    "volume_24h": usd_quote.get("volume24h"),
                    "market_cap": usd_quote.get("marketCap"),
                    "percent_change_24h": usd_quote.get("percentChange24h"),
                    "date_added": date_added_str,
                    "days_since_added": days_since,
                    "circulating_supply": item.get("circulatingSupply"),
                    "total_supply": item.get("totalSupply"),
                    "tags": item.get("tags", []),
                    "data_source": "coinmarketcap_recent",
                    "fetched_at": datetime.utcnow().isoformat()
                })

                if len(results) >= limit:
                    break

            logger.info(f"CoinMarketCap: Found {len(results)} tokens added in last {days} days")
            return results

        except Exception as e:
            logger.error(f"CoinMarketCap get_recently_added failed: {e}")
            return []


def main():
    """Test the CoinMarketCap fetcher."""
    import json

    fetcher = CoinMarketCapFetcher()

    print("=" * 60)
    print("CoinMarketCap API Test")
    print("=" * 60)

    # Test trending
    print("\n🔥 Top 10 Trending Tokens:")
    trending = fetcher.get_trending(limit=10)
    for t in trending:
        pct = t.get('percent_change_24h') or 0
        sign = '+' if pct >= 0 else ''
        print(f"  #{t['trending_rank']:2d} {t['symbol']:8s} {sign}{pct:6.1f}%  {t['name']}")

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

    # Test recently added
    print("\n🆕 Recently Added (last 7 days):")
    recent = fetcher.get_recently_added(limit=5, days=7)
    for r in recent:
        print(f"  {r['symbol']:8s} Added {r['days_since_added']}d ago  MC: ${(r['market_cap'] or 0)/1e6:,.1f}M")

    # Test categories (Session 336)
    print("\n📂 Top 5 Categories (by market cap):")
    categories = fetcher.get_categories()
    categories_sorted = sorted(categories, key=lambda x: x.get('market_cap', 0) or 0, reverse=True)[:5]
    for c in categories_sorted:
        mc = c.get('market_cap') or 0
        avg_change = c.get('avg_price_change_24h') or 0
        sign = '+' if avg_change >= 0 else ''
        print(f"  {c['name'][:20]:20s} {c['num_tokens']:4d} tokens  MC: ${mc/1e9:,.1f}B  {sign}{avg_change:.1f}%")

    # Test category tokens (AI narrative)
    print("\n🤖 Top 5 AI Tokens (MC>$10M):")
    ai_tokens = fetcher.get_category_tokens("artificial-intelligence", limit=5, min_market_cap=10_000_000)
    for t in ai_tokens:
        pct = t.get('percent_change_24h') or 0
        sign = '+' if pct >= 0 else ''
        print(f"  {t['symbol']:8s} {sign}{pct:6.1f}%  MC: ${t['market_cap']/1e6:,.1f}M")

    # Test narrative movers (Session 336 - David's "catch the narrative")
    print("\n🎯 Narrative Movers (Top gainers per narrative):")
    movers = fetcher.get_narrative_movers(
        narratives=["artificial-intelligence", "gaming", "meme-token"],
        limit_per_narrative=3,
        min_market_cap=5_000_000
    )
    for narrative, tokens in movers.items():
        print(f"\n  {narrative.upper().replace('-', ' ')}:")
        for t in tokens:
            pct = t.get('percent_change_24h') or 0
            print(f"    {t['symbol']:8s} +{pct:5.1f}%  MC: ${t['market_cap']/1e6:,.1f}M")


if __name__ == "__main__":
    main()
