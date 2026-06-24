#!/usr/bin/env python3
"""
MEXC Listings Twitter Fetcher

DEPRECATED: Use src.data.fetchers.exchange.fetch_mexc_twitter instead.
Session 256: Migrated to src/data/fetchers/exchange.py

Fetches recent tweets from @MEXC_Listings to check for TGE announcements
"""

import warnings
warnings.warn(
    "scripts.helpers.mexc_twitter_fetcher is deprecated. "
    "Use src.data.fetchers.exchange.fetch_mexc_twitter instead.",
    DeprecationWarning,
    stacklevel=2
)

import os
import sys
import json
from datetime import datetime, timezone
import tweepy

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from dacle_core.utils.config import load_config

def fetch_mexc_listings_tweets(count=20):
    """
    Fetch recent tweets from @MEXC_Listings

    Args:
        count: Number of recent tweets to fetch (default 20)

    Returns:
        List of tweet dictionaries with relevant info
    """
    try:
        load_config()
    except RuntimeError:
        pass

    # Get Twitter API credentials from environment
    api_key = os.getenv("TWITTER_API_KEY")
    api_secret = os.getenv("TWITTER_API_SECRET")
    bearer_token = os.getenv("TWITTER_BEARER_TOKEN")

    if not api_key or not api_secret:
        print("❌ Twitter API credentials not found in environment")
        print("   Set TWITTER_API_KEY and TWITTER_API_SECRET in .env")
        return []

    # Initialize Twitter API client
    try:
        # Try bearer token first (Twitter API v2 - simpler)
        if bearer_token:
            client = tweepy.Client(bearer_token=bearer_token)
        else:
            # Fall back to API key/secret (Twitter API v2 with OAuth)
            client = tweepy.Client(
                consumer_key=api_key,
                consumer_secret=api_secret
            )

        # Get user ID for @MEXC_Listings
        print("🔍 Looking up @MEXC_Listings account...")
        user = client.get_user(username="MEXC_Listings")

        if not user or not user.data:
            print("❌ Could not find @MEXC_Listings account")
            return []

        user_id = user.data.id
        print(f"   ✅ Found account (ID: {user_id})")

        # Fetch recent tweets
        print(f"\n📱 Fetching last {count} tweets from @MEXC_Listings...")
        tweets = client.get_users_tweets(
            id=user_id,
            max_results=count,
            tweet_fields=['created_at', 'public_metrics', 'entities']
        )

        if not tweets or not tweets.data:
            print("⚠️  No tweets found")
            return []

        # Parse tweets for TGE announcements
        parsed_tweets = []
        tge_keywords = ['listing', 'launch', 'tge', 'token generation', 'new listing', 'will list']

        print(f"\n✅ Found {len(tweets.data)} tweets\n")
        print("=" * 80)

        for tweet in tweets.data:
            text = tweet.text
            created_at = tweet.created_at

            # Check if tweet mentions TGE/listing keywords
            is_listing_announcement = any(keyword in text.lower() for keyword in tge_keywords)

            tweet_data = {
                'id': tweet.id,
                'text': text,
                'created_at': created_at.isoformat() if created_at else None,
                'is_listing_announcement': is_listing_announcement,
                'likes': tweet.public_metrics.get('like_count', 0) if hasattr(tweet, 'public_metrics') else 0,
                'retweets': tweet.public_metrics.get('retweet_count', 0) if hasattr(tweet, 'public_metrics') else 0,
            }

            parsed_tweets.append(tweet_data)

            # Print tweet summary
            emoji = "🚀" if is_listing_announcement else "📝"
            print(f"{emoji} {created_at.strftime('%Y-%m-%d %H:%M UTC') if created_at else 'Unknown date'}")
            print(f"   {text[:100]}{'...' if len(text) > 100 else ''}")
            print(f"   ❤️ {tweet_data['likes']} | 🔁 {tweet_data['retweets']}")
            if is_listing_announcement:
                print(f"   ⚡ LISTING ANNOUNCEMENT DETECTED")
            print()

        print("=" * 80)

        # Summary
        listing_count = sum(1 for t in parsed_tweets if t['is_listing_announcement'])
        print(f"\n📊 Summary:")
        print(f"   Total tweets: {len(parsed_tweets)}")
        print(f"   Listing announcements: {listing_count}")

        return parsed_tweets

    except tweepy.errors.Unauthorized as e:
        print(f"❌ Twitter API authentication failed: {e}")
        print("   Check your API credentials and ensure they have read permissions")
        return []

    except tweepy.errors.TooManyRequests as e:
        print(f"❌ Twitter API rate limit exceeded: {e}")
        print("   Try again in 15 minutes")
        return []

    except Exception as e:
        print(f"❌ Error fetching tweets: {e}")
        import traceback
        traceback.print_exc()
        return []

if __name__ == "__main__":
    tweets = fetch_mexc_listings_tweets(count=20)

    if tweets:
        # Save to JSON for analysis
        output_file = f"data/twitter/mexc_listings_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"
        os.makedirs("data/twitter", exist_ok=True)

        with open(output_file, 'w') as f:
            json.dump(tweets, f, indent=2, default=str)

        print(f"\n💾 Saved to: {output_file}")
