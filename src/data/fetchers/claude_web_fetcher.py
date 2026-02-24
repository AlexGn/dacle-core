#!/usr/bin/env python3
"""
Claude Code Web Data Fetcher

DEPRECATED: Use src.data.fetchers.llm_web.ClaudeWebFetcher instead.
Session 256: Migrated to src/data/fetchers/llm_web.py

Purpose: Fetch token price data using Claude Code as a subprocess
         This allows automated scripts to leverage Claude's web capabilities

Strategy:
    1. Call Claude Code with specific prompts to fetch price data
    2. Parse Claude's responses to extract structured data
    3. Cache results to avoid redundant fetches
    4. Log all successful patterns for learning loop

Author: Claude Code
Date: 2025-11-25
"""

import json
import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


# ==============================================================================
# CLAUDE CODE INTERACTION
# ==============================================================================


def call_claude_for_price_data(token_symbol: str, date: datetime) -> Optional[str]:
    """
    Call Claude Code to fetch price data for a token.

    Args:
        token_symbol: Token symbol (e.g., "MONAD")
        date: Target date (for context, but usually fetches current data)

    Returns:
        Claude's response as text, or None if failed
    """
    # Create a prompt file for Claude
    prompt = f"""Please find the current price and market data for the token {token_symbol}.

I need the following information:
- Current price in USD
- Market cap
- Fully diluted valuation (FDV)
- Circulating supply
- Total supply
- 24h volume

Please search for this token on CoinGecko, CoinMarketCap, DexScreener, or any other reliable crypto data source.

Return ONLY a JSON object with this exact structure (no markdown, no explanations):
{{
  "price": <number>,
  "market_cap": <number or null>,
  "fdv": <number or null>,
  "circulating_supply": <number or null>,
  "total_supply": <number or null>,
  "volume_24h": <number or null>,
  "source": "<source name>",
  "fetched_at": "<ISO timestamp>"
}}

If you cannot find the token, return:
{{"error": "Token not found", "token": "{token_symbol}"}}
"""

    try:
        # Write prompt to temp file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write(prompt)
            prompt_file = f.name

        # Note: This is a placeholder for calling Claude Code
        # In practice, this would need to be integrated with Claude Code's API
        # or use a different mechanism to invoke Claude

        print(f"   ℹ️  Claude Code integration: Prompt written to {prompt_file}")
        print(f"   ℹ️  Manual step: Run 'claude code < {prompt_file}' to get data")

        # Clean up temp file
        os.unlink(prompt_file)

        return None  # Placeholder - actual implementation would return Claude's response

    except Exception as e:
        print(f"   ❌ Error calling Claude: {e}")
        return None


def parse_claude_response(response: str, token_symbol: str) -> Optional[Dict[str, Any]]:
    """
    Parse Claude's response to extract price data.

    Args:
        response: Claude's text response
        token_symbol: Token symbol for validation

    Returns:
        Dict with price data or None if parsing failed
    """
    try:
        # Try to extract JSON from response
        # Claude might wrap it in markdown code blocks
        json_match = re.search(r'```json\s*(\{.*?\})\s*```', response, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            # Try to find raw JSON
            json_match = re.search(r'(\{.*?\})', response, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                print(f"   ❌ No JSON found in Claude's response")
                return None

        # Parse JSON
        data = json.loads(json_str)

        # Check for error
        if "error" in data:
            print(f"   ❌ Claude reported: {data['error']}")
            return None

        # Validate required fields
        if "price" not in data or data["price"] is None:
            print(f"   ❌ No price data in response")
            return None

        # Add metadata
        data["token_symbol"] = token_symbol
        data["collection_method"] = "claude_code_web"

        return data

    except json.JSONDecodeError as e:
        print(f"   ❌ JSON parse error: {e}")
        return None
    except Exception as e:
        print(f"   ❌ Parse error: {e}")
        return None


# ==============================================================================
# LEARNING LOOP INTEGRATION
# ==============================================================================


def log_successful_fetch(token_symbol: str, data: Dict[str, Any], source: str):
    """
    Log successful fetch pattern to learning loop.

    This helps track which sources work best for different tokens.

    Args:
        token_symbol: Token symbol
        data: Successfully fetched data
        source: Source that provided the data
    """
    try:
        log_dir = Path("logs/price_fetch_patterns")
        log_dir.mkdir(parents=True, exist_ok=True)

        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "token_symbol": token_symbol,
            "source": source,
            "data_quality": {
                "has_price": data.get("price") is not None,
                "has_market_cap": data.get("market_cap") is not None,
                "has_fdv": data.get("fdv") is not None,
                "has_supply": data.get("circulating_supply") is not None,
            },
            "fetch_method": data.get("collection_method", "unknown")
        }

        # Append to daily log file
        log_file = log_dir / f"fetch_log_{datetime.now().strftime('%Y-%m-%d')}.jsonl"
        with open(log_file, 'a') as f:
            f.write(json.dumps(log_entry) + '\n')

        print(f"   📝 Logged successful fetch pattern: {source}")

    except Exception as e:
        print(f"   ⚠️  Could not log fetch pattern: {e}")


def get_best_source_for_token(token_symbol: str) -> Optional[str]:
    """
    Get the best-performing source for a token based on historical data.

    Args:
        token_symbol: Token symbol

    Returns:
        Source name or None if no history
    """
    try:
        log_dir = Path("logs/price_fetch_patterns")
        if not log_dir.exists():
            return None

        # Read recent logs
        source_scores = {}
        for log_file in sorted(log_dir.glob("fetch_log_*.jsonl"), reverse=True)[:7]:  # Last 7 days
            with open(log_file, 'r') as f:
                for line in f:
                    entry = json.loads(line)
                    if entry["token_symbol"].upper() == token_symbol.upper():
                        source = entry["source"]
                        quality = entry["data_quality"]
                        score = sum(1 for v in quality.values() if v)

                        if source not in source_scores:
                            source_scores[source] = {"total": 0, "count": 0}
                        source_scores[source]["total"] += score
                        source_scores[source]["count"] += 1

        if not source_scores:
            return None

        # Calculate average scores
        best_source = max(source_scores.items(), key=lambda x: x[1]["total"] / x[1]["count"])
        return best_source[0]

    except Exception as e:
        print(f"   ⚠️  Could not determine best source: {e}")
        return None


# ==============================================================================
# MAIN FETCH FUNCTION
# ==============================================================================


def fetch_price_data_with_claude(token_symbol: str, date: datetime) -> Optional[Dict[str, Any]]:
    """
    Fetch price data using Claude Code's web capabilities.

    This function serves as a bridge between automated scripts and Claude Code.

    Args:
        token_symbol: Token symbol (e.g., "MONAD")
        date: Target date for price data

    Returns:
        Dict with price data or None if failed
    """
    print(f"   🤖 Using Claude Code to fetch {token_symbol} data...")

    # Check if we have historical data on best source
    best_source = get_best_source_for_token(token_symbol)
    if best_source:
        print(f"   💡 Historical data suggests trying: {best_source}")

    # Call Claude
    response = call_claude_for_price_data(token_symbol, date)
    if not response:
        print(f"   ⚠️  Claude Code integration not fully implemented yet")
        print(f"   ℹ️  This function requires Claude Code to be running as a service")
        return None

    # Parse response
    data = parse_claude_response(response, token_symbol)
    if not data:
        return None

    # Log successful fetch for learning loop
    log_successful_fetch(token_symbol, data, data.get("source", "unknown"))

    return data


if __name__ == "__main__":
    # Test the module
    test_symbol = "MONAD"
    test_date = datetime.now()

    print(f"Testing Claude Code fetcher for {test_symbol}...")
    result = fetch_price_data_with_claude(test_symbol, test_date)

    if result:
        print(f"\nSuccess!")
        print(json.dumps(result, indent=2))
    else:
        print(f"\nNote: Full implementation requires Claude Code service integration")
