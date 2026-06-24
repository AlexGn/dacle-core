#!/usr/bin/env python3
"""
Interactive Token Price Fetcher

Purpose: Fetch token price data through Claude Code interactive session
         This script prompts the user to ask Claude for price data

Usage:
    python3 src/data/fetch_token_price_interactive.py MONAD

This will:
1. Print a prompt for you to give to Claude Code
2. Wait for you to paste Claude's JSON response
3. Save the data to the appropriate location
4. Update the TGE outcomes database

Migration History:
- Session 267: Migrated from scripts/helpers/fetch_token_price_interactive.py

Author: Claude Code
Date: 2025-11-25
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()


def print_claude_prompt(token_symbol: str):
    """Print the prompt to give to Claude Code."""
    prompt = f"""Please find the current price and market data for {token_symbol}.

Search CoinGecko, CoinMarketCap, DexScreener, or DEX websites.

Return ONLY a JSON object (no markdown formatting):
{{
  "price": <number>,
  "market_cap": <number or null>,
  "fdv": <number or null>,
  "circulating_supply": <number or null>,
  "total_supply": <number or null>,
  "volume_24h": <number or null>,
  "source": "<source name>"
}}"""

    print("\n" + "=" * 70)
    print("COPY THIS PROMPT AND SEND TO CLAUDE CODE:")
    print("=" * 70)
    print(prompt)
    print("=" * 70)


def get_claude_response() -> dict:
    """Prompt user to paste Claude's JSON response."""
    print("\nPaste Claude's JSON response below (press Enter twice when done):")
    print("-" * 70)

    lines = []
    while True:
        line = input()
        if not line and lines:
            break
        lines.append(line)

    response_text = '\n'.join(lines)

    # Parse JSON
    try:
        # Try to extract JSON if wrapped in markdown
        if '```json' in response_text:
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1
            response_text = response_text[json_start:json_end]

        data = json.loads(response_text)
        return data
    except json.JSONDecodeError as e:
        print(f"\n❌ Error parsing JSON: {e}")
        print(f"Text received: {response_text}")
        return None


def save_price_data(token_symbol: str, data: dict, tge_date: str, days_post: int):
    """Save price data to database."""
    import os

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY")

    if not supabase_url or not supabase_key:
        print("❌ Supabase credentials not found in .env")
        return False

    client = create_client(supabase_url, supabase_key)

    # Build outcome record
    price_key = f"price_t_plus_{days_post}d"
    record = {
        "token_symbol": token_symbol,
        "tge_date": tge_date,
        price_key: data.get("price"),
        "market_cap": data.get("market_cap"),
        "fdv": data.get("fdv"),
        "circulating_supply": data.get("circulating_supply"),
        "volume_24h": data.get("volume_24h"),
        "data_sources": [data.get("source", "claude_code")],
        "collection_method": "claude_code_interactive",
        "collected_at": datetime.now().isoformat(),
        "last_updated": datetime.now().isoformat()
    }

    try:
        # Check if exists
        existing = (
            client.table("tge_outcomes")
            .select("id")
            .eq("token_symbol", token_symbol)
            .eq("tge_date", tge_date)
            .execute()
        )

        if existing.data:
            # Update
            client.table("tge_outcomes").update(record).eq("token_symbol", token_symbol).eq("tge_date", tge_date).execute()
            print(f"✅ Updated TGE outcome for {token_symbol}")
        else:
            # Insert
            client.table("tge_outcomes").insert(record).execute()
            print(f"✅ Inserted TGE outcome for {token_symbol}")

        return True

    except Exception as e:
        print(f"❌ Database error: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Interactive token price fetcher using Claude Code")
    parser.add_argument("token", help="Token symbol (e.g., MONAD)")
    parser.add_argument("--tge-date", help="TGE date (YYYY-MM-DD)")
    parser.add_argument("--days-post", type=int, choices=[1, 7, 30], default=1, help="Days post-TGE")
    args = parser.parse_args()

    print(f"\n🔍 Fetching price data for {args.token}...")

    # Print prompt for user to copy
    print_claude_prompt(args.token)

    # Get response from user
    data = get_claude_response()
    if not data:
        print("\n❌ Failed to parse response")
        return 1

    # Validate data
    if "price" not in data or data["price"] is None:
        print("\n❌ No price found in response")
        return 1

    # Display parsed data
    print("\n✅ Successfully parsed data:")
    print(json.dumps(data, indent=2))

    # Save to database if TGE date provided
    if args.tge_date:
        print(f"\n💾 Saving to database...")
        if save_price_data(args.token, data, args.tge_date, args.days_post):
            print(f"\n✅ Data saved successfully!")
        else:
            print(f"\n⚠️  Could not save to database")
    else:
        print(f"\n💡 To save to database, run with --tge-date YYYY-MM-DD")

    return 0


if __name__ == "__main__":
    sys.exit(main())
