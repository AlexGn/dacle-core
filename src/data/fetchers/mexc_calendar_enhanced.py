#!/usr/bin/env python3
"""
Enhanced MEXC Calendar Fetcher

DEPRECATED: Use src.data.fetchers.exchange.fetch_mexc_calendar instead.
    from src.data.fetchers import fetch_mexc_calendar

---

Fetches and parses MEXC new listing calendar with detailed analysis

Deprecated: 2025-12-26 (Session 256+ Refactoring)
"""
import warnings
warnings.warn(
    "scripts.helpers.mexc_calendar_enhanced is deprecated. "
    "Use src.data.fetchers.fetch_mexc_calendar instead.",
    DeprecationWarning,
    stacklevel=2
)

import os
import sys
import json
from datetime import datetime, timezone
import re

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from src.utils.config import load_config

def fetch_mexc_calendar_detailed():
    """
    Fetch MEXC new listing calendar with enhanced parsing

    Returns:
        List of listings with detailed metadata
    """
    try:
        load_config()
    except RuntimeError:
        pass

    # Try to use WebFetch through Claude Code
    print("🔍 Fetching MEXC new listing calendar...")
    print("   URL: https://www.mexc.com/newlisting")

    try:
        from anthropic import Anthropic

        client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

        # Use Claude to fetch and parse the MEXC calendar
        message = client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": """Please fetch https://www.mexc.com/newlisting and extract all new token listings.

For each listing, extract:
- Token symbol
- Token name
- Listing date/time
- Trading pairs available
- Any additional notes about the listing

Format as JSON array with this structure:
[
  {
    "token_symbol": "...",
    "token_name": "...",
    "listing_date": "YYYY-MM-DD",
    "listing_time": "HH:MM UTC",
    "trading_pairs": ["USDT", "BTC", ...],
    "notes": "..."
  }
]

If you cannot access the page, explain why."""
            }]
        )

        response_text = message.content[0].text

        # Try to parse JSON from response
        json_match = re.search(r'\[[\s\S]*\]', response_text)
        if json_match:
            listings = json.loads(json_match.group())

            print(f"\n✅ Found {len(listings)} new listings on MEXC calendar\n")
            print("=" * 80)

            for listing in listings:
                print(f"🚀 {listing.get('token_symbol', '???')} - {listing.get('token_name', 'Unknown')}")
                print(f"   Listing: {listing.get('listing_date', 'TBD')} at {listing.get('listing_time', 'TBD')}")
                if listing.get('trading_pairs'):
                    print(f"   Pairs: {', '.join(listing['trading_pairs'])}")
                if listing.get('notes'):
                    print(f"   Note: {listing['notes']}")
                print()

            print("=" * 80)

            # Save to file
            output_file = f"data/mexc/calendar_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"
            os.makedirs("data/mexc", exist_ok=True)

            with open(output_file, 'w') as f:
                json.dump(listings, f, indent=2)

            print(f"\n💾 Saved to: {output_file}")

            return listings
        else:
            print(f"\n⚠️  Could not parse JSON from response")
            print(f"Response: {response_text[:500]}")
            return []

    except Exception as e:
        print(f"❌ Error fetching MEXC calendar: {e}")
        import traceback
        traceback.print_exc()
        return []

def compare_with_scanner_data():
    """
    Compare MEXC calendar data with what the scanner found
    """
    print("\n🔄 Comparing with TGE scanner data...")

    try:
        from src.knowledge.supabase_client import SupabaseKnowledgeBase

        kb = SupabaseKnowledgeBase()

        # Query recent TGEs from MEXC source
        result = kb.client.table('tge_calendar').select('*').contains(
            'sources', ['mexc']
        ).order('tge_date').limit(20).execute()

        if result.data:
            print(f"\n📊 Found {len(result.data)} TGEs from MEXC in scanner database:\n")
            for tge in result.data:
                print(f"   • {tge['token_symbol']} - {tge.get('token_name', 'Unknown')} ({tge['tge_date']})")
        else:
            print(f"\n⚠️  No MEXC-sourced TGEs found in scanner database")

    except Exception as e:
        print(f"⚠️  Could not query database: {e}")

if __name__ == "__main__":
    print("=" * 80)
    print("MEXC New Listing Calendar - Enhanced Fetcher")
    print("=" * 80)
    print()

    listings = fetch_mexc_calendar_detailed()

    if listings:
        compare_with_scanner_data()

        print("\n" + "=" * 80)
        print("✅ MEXC calendar fetch complete")
        print("=" * 80)
    else:
        print("\n" + "=" * 80)
        print("❌ No listings found or fetch failed")
        print("\nAlternative: Visit https://www.mexc.com/newlisting directly")
        print("=" * 80)
