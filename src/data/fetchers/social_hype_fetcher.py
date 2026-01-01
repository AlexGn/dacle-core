#!/usr/bin/env python3
"""
Social Hype Intelligence Fetcher (FREE alternatives to Kaito.ai)

DEPRECATED: Use src.data.fetchers.token_data module instead.
Session 256: Marked for migration to src/data/fetchers/token_data.py

Fetches social hype data from FREE sources:
1. CryptoRank Social Score API (cryptorank.io)
2. Twitter/X Search (via Perplexity or manual count)
3. CoinGecko Community Data API (coingecko.com)

Used by conviction scoring v3.0 to calculate Social Hype component (3% weight).

Author: DACLE System
Version: 1.0 (Session 39)
"""

import warnings
warnings.warn(
    "scripts.helpers.social_hype_fetcher is deprecated. "
    "Use src.data.fetchers.token_data module instead.",
    DeprecationWarning,
    stacklevel=2
)

import logging
import os
from typing import Dict, Optional, Tuple

import requests

logger = logging.getLogger(__name__)


class SocialHypeFetcher:
    """Fetch social hype intelligence from FREE sources."""

    def __init__(self):
        """Initialize social hype fetcher with API clients."""
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        })

        # CryptoRank API (free tier)
        self.cryptorank_api_key = os.getenv("CRYPTORANK_API_KEY", "")  # Optional

        # CoinGecko API (free tier)
        self.coingecko_api_key = os.getenv("COINGECKO_API_KEY", "")  # Optional

    def fetch_cryptorank_social(self, token_symbol: str) -> Dict:
        """
        Fetch CryptoRank social score (0-100).

        Args:
            token_symbol: Token symbol (e.g., "MONAD")

        Returns:
            Dict with social_score, twitter_followers, etc.
        """
        try:
            url = f"https://api.cryptorank.io/v1/currencies/{token_symbol.lower()}"

            params = {}
            if self.cryptorank_api_key:
                params['api_key'] = self.cryptorank_api_key

            response = self.session.get(url, params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()

                # Extract social metrics
                social_data = {
                    "social_score": data.get("data", {}).get("socialScore", 0),
                    "twitter_followers": data.get("data", {}).get("twitterFollowers", 0),
                    "telegram_members": data.get("data", {}).get("telegramMembers", 0),
                    "reddit_subscribers": data.get("data", {}).get("redditSubscribers", 0),
                }

                logger.info(f"CryptoRank social score for {token_symbol}: {social_data['social_score']}/100")
                return social_data
            else:
                logger.warning(f"CryptoRank API returned {response.status_code} for {token_symbol}")
                return {"social_score": 0}

        except Exception as e:
            logger.error(f"Error fetching CryptoRank social for {token_symbol}: {e}")
            return {"social_score": 0}

    def fetch_coingecko_community(self, token_symbol: str) -> Dict:
        """
        Fetch CoinGecko community data (watchlist count, upvotes).

        Args:
            token_symbol: Token symbol (e.g., "MONAD")

        Returns:
            Dict with watchlist_count, upvotes, etc.
        """
        try:
            # First, search for coin ID by symbol
            search_url = "https://api.coingecko.com/api/v3/search"
            params = {"query": token_symbol}

            if self.coingecko_api_key:
                params['x_cg_pro_api_key'] = self.coingecko_api_key

            search_response = self.session.get(search_url, params=params, timeout=10)

            if search_response.status_code != 200:
                logger.warning(f"CoinGecko search failed for {token_symbol}")
                return {"watchlist_count": 0}

            coins = search_response.json().get("coins", [])
            if not coins:
                logger.warning(f"No CoinGecko data found for {token_symbol}")
                return {"watchlist_count": 0}

            # Get first matching coin ID
            coin_id = coins[0].get("id")

            # Fetch coin details
            coin_url = f"https://api.coingecko.com/api/v3/coins/{coin_id}"
            coin_params = {}
            if self.coingecko_api_key:
                coin_params['x_cg_pro_api_key'] = self.coingecko_api_key

            coin_response = self.session.get(coin_url, params=coin_params, timeout=10)

            if coin_response.status_code == 200:
                data = coin_response.json()

                community_data = {
                    "watchlist_count": data.get("watchlist_portfolio_users", 0),
                    "upvotes": data.get("sentiment_votes_up_percentage", 0),
                    "downvotes": data.get("sentiment_votes_down_percentage", 0),
                    "reddit_subscribers": data.get("community_data", {}).get("reddit_subscribers", 0),
                    "twitter_followers": data.get("community_data", {}).get("twitter_followers", 0),
                }

                logger.info(f"CoinGecko watchlist for {token_symbol}: {community_data['watchlist_count']} users")
                return community_data
            else:
                logger.warning(f"CoinGecko API returned {coin_response.status_code} for {coin_id}")
                return {"watchlist_count": 0}

        except Exception as e:
            logger.error(f"Error fetching CoinGecko community for {token_symbol}: {e}")
            return {"watchlist_count": 0}

    def estimate_twitter_mentions(self, token_symbol: str, perplexity_data: Optional[Dict] = None) -> int:
        """
        Estimate Twitter mentions from Perplexity data or return 0.

        For now, this is a placeholder. In practice:
        1. Use Perplexity daily scan JSON (social_hype.twitter_mentions_7d)
        2. OR manually count mentions via Twitter Search
        3. OR use Twitter API (requires paid access)

        Args:
            token_symbol: Token symbol
            perplexity_data: Optional Perplexity discovery JSON

        Returns:
            int: Estimated Twitter mentions (past 7 days)
        """
        if perplexity_data and "social_hype" in perplexity_data:
            return perplexity_data["social_hype"].get("twitter_mentions_7d", 0)

        # Fallback: Return 0 if no data available
        logger.warning(f"No Twitter mention data for {token_symbol} - using 0")
        return 0

    def calculate_social_hype_score(
        self,
        cryptorank_score: int,
        twitter_mentions: int,
        watchlist_count: int
    ) -> Tuple[float, str]:
        """
        Calculate composite social hype score from free sources.

        Scoring (Session 39 v3.0):
        - 5 pts: EXTREME HYPE (>10K mentions OR >80 CryptoRank OR >50K watchlist)
        - 4 pts: HIGH HYPE (5K-10K mentions OR 60-80 CR OR 20K-50K watchlist)
        - 2 pts: MODERATE HYPE (1K-5K mentions OR 40-60 CR OR 5K-20K watchlist)
        - 0 pts: LOW HYPE (<1K mentions OR <40 CR OR <5K watchlist)

        Args:
            cryptorank_score: CryptoRank social score (0-100)
            twitter_mentions: Twitter mentions count (past 7 days)
            watchlist_count: CoinGecko watchlist count

        Returns:
            Tuple[float, str]: (score 0-5, description)
        """
        # Check for EXTREME HYPE (any metric triggers)
        if twitter_mentions > 10000 or cryptorank_score > 80 or watchlist_count > 50000:
            return (
                5.0,
                f"EXTREME HYPE: {twitter_mentions} Twitter mentions, {cryptorank_score}/100 CryptoRank, "
                f"{watchlist_count} watchlist → contrarian short opportunity"
            )

        # Check for HIGH HYPE
        if (5000 <= twitter_mentions <= 10000 or
            60 <= cryptorank_score <= 80 or
            20000 <= watchlist_count <= 50000):
            return (
                4.0,
                f"HIGH HYPE: {twitter_mentions} Twitter mentions, {cryptorank_score}/100 CryptoRank, "
                f"{watchlist_count} watchlist → strong dump setup"
            )

        # Check for MODERATE HYPE
        if (1000 <= twitter_mentions <= 5000 or
            40 <= cryptorank_score <= 60 or
            5000 <= watchlist_count <= 20000):
            return (
                2.0,
                f"MODERATE HYPE: {twitter_mentions} Twitter mentions, {cryptorank_score}/100 CryptoRank, "
                f"{watchlist_count} watchlist → standard setup"
            )

        # LOW HYPE (skip - no retail)
        return (
            0.0,
            f"LOW HYPE: {twitter_mentions} Twitter mentions, {cryptorank_score}/100 CryptoRank, "
            f"{watchlist_count} watchlist → skip (no retail to dump on)"
        )

    def get_social_hype_intelligence(
        self,
        token_symbol: str,
        perplexity_data: Optional[Dict] = None
    ) -> Dict:
        """
        Get complete social hype intelligence for a token.

        Args:
            token_symbol: Token symbol (e.g., "MONAD")
            perplexity_data: Optional Perplexity discovery JSON with social_hype field

        Returns:
            Dict with score, description, and raw metrics
        """
        logger.info(f"Fetching social hype intelligence for {token_symbol}...")

        # Fetch from free sources
        cryptorank_data = self.fetch_cryptorank_social(token_symbol)
        coingecko_data = self.fetch_coingecko_community(token_symbol)
        twitter_mentions = self.estimate_twitter_mentions(token_symbol, perplexity_data)

        # Calculate composite score
        score, description = self.calculate_social_hype_score(
            cryptorank_score=cryptorank_data.get("social_score", 0),
            twitter_mentions=twitter_mentions,
            watchlist_count=coingecko_data.get("watchlist_count", 0)
        )

        return {
            "score": score,
            "description": description,
            "cryptorank_social_score": cryptorank_data.get("social_score", 0),
            "twitter_mentions_7d": twitter_mentions,
            "coingecko_watchlist": coingecko_data.get("watchlist_count", 0),
            "raw_data": {
                "cryptorank": cryptorank_data,
                "coingecko": coingecko_data,
            }
        }


def main():
    """Test the social hype fetcher."""
    import sys

    if len(sys.argv) < 2:
        print("Usage: python social_hype_fetcher.py TOKEN_SYMBOL")
        sys.exit(1)

    token_symbol = sys.argv[1].upper()

    fetcher = SocialHypeFetcher()
    result = fetcher.get_social_hype_intelligence(token_symbol)

    print(f"\n🎯 Social Hype Intelligence: {token_symbol}")
    print("=" * 60)
    print(f"Score: {result['score']}/5")
    print(f"Description: {result['description']}")
    print(f"\nMetrics:")
    print(f"  CryptoRank Social: {result['cryptorank_social_score']}/100")
    print(f"  Twitter Mentions (7d): {result['twitter_mentions_7d']}")
    print(f"  CoinGecko Watchlist: {result['coingecko_watchlist']} users")
    print("=" * 60)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
