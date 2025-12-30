"""
WebFetch Helper Functions for TGE Data Extraction

This module provides reusable patterns for extracting TGE data using Claude Code's
WebFetch capability. These functions serve as templates - they document the expected
input/output format for manual WebFetch requests.

Session 267: Migrated from scripts/helpers/webfetch_helpers.py to src/data/webfetch_helpers.py

Usage:
    1. During manual research: Use these patterns as prompts for Claude Code
    2. In Agent workflow: Document what data to request via WebFetch
    3. For automation: Reference these patterns when building WebFetch integrations

Note: These are NOT automated scrapers. They document the WebFetch pattern for
      manual data extraction by Claude Code during the research phase.
"""

from datetime import datetime
from typing import Dict, List, Optional
import json
from pathlib import Path


def save_webfetch_result(token: str, data_type: str, data: dict) -> str:
    """
    Save WebFetch extraction result to token's sources directory.

    Args:
        token: Token symbol (e.g., "BIT", "MONAD")
        data_type: Type of data (e.g., "otc", "social", "fdv_verification")
        data: Extracted data dictionary

    Returns:
        Path to saved file
    """
    project_root = Path(__file__).parent.parent
    sources_dir = project_root / "data" / "tokens" / token.upper() / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)

    # Filename format: {priority}_{data_type}_webfetch_{date}.json
    # Priority: 4 (manual research data, between automated and David's feedback)
    filename = f"4_{data_type}_webfetch_{datetime.now().strftime('%Y-%m-%d')}.json"
    filepath = sources_dir / filename

    # Add metadata
    output = {
        "data_source": "webfetch",
        "extraction_method": "claude_code_webfetch",
        "extracted_at": datetime.now().isoformat(),
        "token": token.upper(),
        "data_type": data_type,
        **data
    }

    with open(filepath, 'w') as f:
        json.dump(output, f, indent=2)

    return str(filepath)


# ============================================================================
# WHALES MARKET OTC DATA
# ============================================================================

def fetch_otc_data_pattern() -> dict:
    """
    Pattern for extracting OTC data from Whales Market using WebFetch.

    WebFetch Request:
    -----------------
    URL: https://whales.market/en/premarket/{TOKEN_SYMBOL}
    Prompt: "Extract: OTC price, 24h volume, 7d volume, total volume for {TOKEN} token"

    Expected Response:
    ------------------
    {
        "otc_price_usd": 0.0448,
        "volume_24h": 0,
        "volume_7d": 12500,
        "volume_total": 45000,
        "network": "Solana",
        "total_supply": 1000000000,
        "available": true
    }

    Usage in Agent 1:
    -----------------
    When token has pre-market OTC:
    1. User/script requests OTC data
    2. Claude Code: WebFetch(whales.market/en/premarket/TOKEN)
    3. Parse response → Calculate volume trend
    4. Return signal (FADING_INTEREST / DECLINING / STABLE)

    Returns:
        Template showing expected data structure
    """
    return {
        "pattern_name": "whales_market_otc",
        "url_template": "https://whales.market/en/premarket/{TOKEN_SYMBOL}",
        "webfetch_prompt": "Extract: OTC price, 24h volume, 7d volume, total volume for {TOKEN} token",
        "expected_output": {
            "otc_price_usd": float,
            "volume_24h": float,
            "volume_7d": float,
            "volume_total": float,
            "network": str,
            "total_supply": int,
            "available": bool
        },
        "when_to_use": "T-7 to T-1 days before TGE, when token has OTC market",
        "agent": "Agent 1 (OTC Volume Analysis)",
        "trust_level": 75,  # % - WebFetch is reliable for extracting visible data
        "notes": [
            "Replace Playwright scraper with this WebFetch pattern",
            "Faster than browser automation (no Playwright needed)",
            "More flexible (adapts to HTML changes automatically)"
        ]
    }


def calculate_otc_signal(otc_data: dict, listing_price: float) -> dict:
    """
    Calculate OTC trading signal from WebFetch data.

    Args:
        otc_data: OTC data from WebFetch
        listing_price: Expected TGE listing price

    Returns:
        Signal analysis dictionary
    """
    otc_price = otc_data.get("otc_price_usd", 0)
    volume_24h = otc_data.get("volume_24h", 0)
    volume_7d = otc_data.get("volume_7d", 0)
    volume_total = otc_data.get("volume_total", 0)

    # Calculate premium/discount
    if listing_price and otc_price:
        premium_pct = ((otc_price - listing_price) / listing_price) * 100
    else:
        premium_pct = 0

    # Calculate volume trend
    if volume_total > 0:
        volume_ratio_24h = volume_24h / volume_total
    else:
        volume_ratio_24h = 0

    # Signal determination (from MET pattern)
    if volume_ratio_24h < 0.15:  # <15% of total volume in last 24h
        signal = "FADING_INTEREST"
        strength = 8.0
        reasoning = "OTC volume declining sharply - strong short signal (MET pattern)"
    elif volume_ratio_24h < 0.30:
        signal = "DECLINING"
        strength = 6.0
        reasoning = "OTC volume moderately declining - moderate short signal"
    else:
        signal = "STABLE"
        strength = 4.0
        reasoning = "OTC volume stable or growing - neutral signal"

    return {
        "signal": signal,
        "strength": strength,
        "otc_premium_pct": round(premium_pct, 2),
        "volume_ratio_24h": round(volume_ratio_24h, 4),
        "reasoning": reasoning,
        "data_quality": 90 if otc_data.get("available") else 0
    }


# ============================================================================
# COINGECKO SOCIAL METRICS
# ============================================================================

def fetch_social_metrics_pattern() -> dict:
    """
    Pattern for extracting social validation metrics from CoinGecko using WebFetch.

    WebFetch Request:
    -----------------
    URL: https://www.coingecko.com/en/coins/{token_slug}
    Prompt: "Extract: watchlist count, Twitter followers, Telegram members, community score"

    Expected Response:
    ------------------
    {
        "watchlist_count": 1200,
        "twitter_followers": 5000,
        "telegram_members": 2500,
        "community_score": 45.2,
        "social_links": {
            "twitter": "https://twitter.com/...",
            "telegram": "https://t.me/...",
            "website": "https://..."
        }
    }

    Social Validation Tiers (v3.3):
    --------------------------------
    - VERY_LOW: <1K watchers (RED FLAG - KO had 1,200)
    - LOW: 1-10K watchers
    - MEDIUM: 10-30K watchers
    - HIGH: >30K watchers (STRK/GAIB range)

    Usage:
    ------
    1. API first (automated Agent 0 enrichment)
    2. WebFetch fallback when API rate limited or token not found
    3. Prevents false positives (tokens with low social validation)

    Returns:
        Template showing expected data structure
    """
    return {
        "pattern_name": "coingecko_social_metrics",
        "url_template": "https://www.coingecko.com/en/coins/{token_slug}",
        "webfetch_prompt": "Extract: watchlist count, Twitter followers, Telegram members, community score",
        "expected_output": {
            "watchlist_count": int,
            "twitter_followers": int,
            "telegram_members": int,
            "community_score": float,
            "social_links": dict
        },
        "when_to_use": "When CoinGecko API rate limited or token not indexed yet",
        "agent": "Agent 0 (Data Validation) - Social validation tier",
        "trust_level": 80,  # % - WebFetch + API cross-validation
        "social_tiers": {
            "VERY_LOW": {"range": "<1K", "risk": "RED FLAG"},
            "LOW": {"range": "1-10K", "risk": "Moderate"},
            "MEDIUM": {"range": "10-30K", "risk": "Low"},
            "HIGH": {"range": ">30K", "risk": "Very Low"}
        }
    }


def calculate_social_tier(watchlist_count: int) -> str:
    """
    Calculate social validation tier from watchlist count.

    Args:
        watchlist_count: Number of users watching token on CoinGecko

    Returns:
        Social tier (VERY_LOW / LOW / MEDIUM / HIGH)
    """
    if watchlist_count < 1000:
        return "VERY_LOW"  # RED FLAG
    elif watchlist_count < 10000:
        return "LOW"
    elif watchlist_count < 30000:
        return "MEDIUM"
    else:
        return "HIGH"


# ============================================================================
# FDV VERIFICATION
# ============================================================================

def fetch_fdv_verification_pattern() -> dict:
    """
    Pattern for verifying FDV from multiple sources using WebFetch.

    Use Case:
    ---------
    When David or Perplexity flags FDV as "estimated" and needs verification.

    WebFetch Requests (Multi-Source):
    ----------------------------------
    1. Official Announcement:
       URL: {official_medium/twitter/website}
       Prompt: "Find FDV or fully diluted valuation mentioned in official announcement"

    2. CryptoRank:
       URL: https://cryptorank.io/price/{token}
       Prompt: "Extract FDV or fully diluted valuation"

    3. CoinGecko:
       URL: https://www.coingecko.com/en/coins/{token}
       Prompt: "Extract fully diluted valuation (FDV)"

    Expected Aggregated Response:
    -----------------------------
    {
        "fdv_confirmed": true,
        "fdv_value": 1000000000,
        "sources": [
            {"source": "official", "fdv": 1000000000, "confidence": "confirmed"},
            {"source": "cryptorank", "fdv": 1050000000, "confidence": "confirmed"},
            {"source": "coingecko", "fdv": null, "confidence": "not_found"}
        ],
        "consensus": "2/3 sources confirm ~$1B FDV",
        "recommendation": "Set fdv_source = 'confirmed' (2+ sources agree within 10%)"
    }

    Returns:
        Template showing expected verification pattern
    """
    return {
        "pattern_name": "fdv_verification",
        "sources_to_check": [
            "Official announcement (Medium/Twitter)",
            "CryptoRank",
            "CoinGecko",
            "Official tokenomics page"
        ],
        "webfetch_prompts": {
            "official": "Find FDV or fully diluted valuation in official announcement",
            "cryptorank": "Extract FDV or fully diluted valuation",
            "coingecko": "Extract fully diluted valuation (FDV)",
            "website": "Find total supply and token price to calculate FDV"
        },
        "confidence_rules": {
            "confirmed": "2+ sources agree within 10%",
            "estimated": "1 source only, or sources differ by >20%",
            "null": "0 sources found, or conflict >50%"
        },
        "when_to_use": "When David flags FDV concern in open_questions[]",
        "agent": "Phase 0.5 (Data Consolidation) - FDV Confirmation Gate",
        "trust_level": 90,  # % - Multi-source verification = high trust
        "session_52_rule": "If fdv_source != 'confirmed', cap conviction at 6.5/10"
    }


# ============================================================================
# TOKEN ALLOCATION
# ============================================================================

def fetch_token_allocation_pattern() -> dict:
    """
    Pattern for extracting token allocation from official sources using WebFetch.

    Use Case:
    ---------
    When CryptoRank doesn't have allocation data (common for new tokens).

    WebFetch Request:
    -----------------
    URL: {official_website}/tokenomics or {medium_article}
    Prompt: "Extract token allocation breakdown: team %, investors %, community %, ecosystem %, treasury %, liquidity %"

    Expected Response:
    ------------------
    {
        "team_pct": 15.0,
        "investors_pct": 20.0,
        "community_pct": 25.0,
        "ecosystem_pct": 30.0,
        "treasury_pct": 5.0,
        "liquidity_pct": 5.0,
        "source": "https://...",
        "total": 100.0
    }

    Returns:
        Template showing expected allocation structure
    """
    return {
        "pattern_name": "token_allocation",
        "url_sources": [
            "{official_website}/tokenomics",
            "Medium article (tokenomics)",
            "Whitepaper PDF",
            "CryptoRank (if available)"
        ],
        "webfetch_prompt": "Extract token allocation: team %, investors %, community %, ecosystem %, treasury %, liquidity %",
        "expected_output": {
            "team_pct": float,
            "investors_pct": float,
            "community_pct": float,
            "ecosystem_pct": float,
            "treasury_pct": float,
            "liquidity_pct": float,
            "locked_at_tge_pct": float,
            "source": str
        },
        "when_to_use": "When CryptoRank allocation[] is empty",
        "agent": "Agent 2 (Conviction Scoring) - locked_tokens component",
        "trust_level": 85,  # % - Official source usually accurate
        "notes": [
            "Use for calculating locked_tokens conviction component",
            "Higher locked % at TGE = better for shorts (less supply pressure)"
        ]
    }


# ============================================================================
# NEW TOKEN PRICE COLLECTION
# ============================================================================

def fetch_new_token_price_pattern() -> dict:
    """
    Pattern for collecting price data for newly launched tokens using WebSearch + WebFetch.

    Use Case:
    ---------
    T+1, T+3, T+7 outcome tracking when APIs don't have token yet.

    WebSearch + WebFetch Workflow:
    -------------------------------
    1. WebSearch: "{TOKEN} token price live market cap FDV cryptocurrency"
    2. Parse search results → Find CryptoRank, CMC, DEXScreener, CoinGecko
    3. WebFetch each source → Extract price, market cap, FDV, volume
    4. Aggregate → Use most recent/reliable data
    5. Save to: data/tokens/{TOKEN}/price_data_{DATE}.json

    Expected Aggregated Response:
    -----------------------------
    {
        "token_symbol": "MONAD",
        "price": 0.03194,
        "market_cap": 345908200,
        "fdv": 3194000000,
        "circulating_supply": 10830000000,
        "total_supply": 100000000000,
        "volume_24h": 669670000,
        "source": "cryptorank_coinmarketcap_aggregated",
        "fetched_at": "2025-11-25T18:45:00Z",
        "collection_method": "claude_code_web_search",
        "data_quality_score": 90
    }

    Sync Script Integration:
    ------------------------
    scripts/sync/sync_tge_outcomes.py checks for manual data FIRST:
    1. Check: data/tokens/{TOKEN}/price_data_{TODAY}.json exists?
    2. If yes: Use manual WebFetch data
    3. If no: Fallback to CMC/CoinGecko API

    Returns:
        Template showing price collection pattern
    """
    return {
        "pattern_name": "new_token_price_collection",
        "workflow": [
            "WebSearch: '{TOKEN} token price live'",
            "Find sources: CryptoRank, CMC, DEXScreener, CoinGecko",
            "WebFetch each source",
            "Aggregate prices (use most recent)",
            "Save to price_data_{DATE}.json"
        ],
        "expected_output": {
            "price": float,
            "market_cap": float,
            "fdv": float,
            "circulating_supply": int,
            "total_supply": int,
            "volume_24h": float,
            "source": str,
            "data_quality_score": int
        },
        "when_to_use": "T+1, T+3, T+7 days after TGE launch",
        "saves_to": "data/tokens/{TOKEN}/price_data_{DATE}.json",
        "sync_script": "scripts/sync/sync_tge_outcomes.py",
        "trust_level": 85,  # % - Multi-source aggregation
        "advantages": [
            "No API rate limits",
            "Works for newly launched tokens (not indexed yet)",
            "Multi-source aggregation (more reliable)",
            "Faster than waiting for API indexing"
        ],
        "status": "✅ PRODUCTION-READY (MONAD test successful)"
    }


# ============================================================================
# USAGE EXAMPLES
# ============================================================================

def example_usage_otc():
    """
    Example: How to request OTC data via WebFetch during manual research.

    Scenario:
    ---------
    Agent 1 needs OTC data for BitDealer token 7 days before TGE.

    Manual WebFetch Request (via Claude Code chat):
    ------------------------------------------------
    User: "Get OTC data for BIT token from Whales Market"

    Claude Code (internally):
        WebFetch(
            url="https://whales.market/en/premarket/BIT",
            prompt="Extract: OTC price, 24h volume, 7d volume for BIT token"
        )

    Claude Code Response:
    ---------------------
    "OTC Data for BIT:
    - Current Price: $0.0448 USD
    - 24h Volume: $0
    - Total Volume: $45,000
    - Premium vs Listing ($0.035): 28%
    - Signal: FADING_INTEREST (volume declined from $12.5K to $0 in 24h)"

    Save Result:
    ------------
    save_webfetch_result("BIT", "otc", {
        "otc_price_usd": 0.0448,
        "volume_24h": 0,
        "volume_7d": 12500,
        "volume_total": 45000,
        "available": True
    })

    File Created:
    -------------
    data/tokens/BIT/sources/4_otc_webfetch_2025-11-25.json
    """
    pass


def example_usage_fdv_verification():
    """
    Example: How to verify FDV when David flags uncertainty.

    Scenario:
    ---------
    David's feedback (3_david_*.json):
    {
        "open_questions": ["Can you triple check the FDV estimate?"]
    }

    Manual WebFetch Request (via Claude Code chat):
    ------------------------------------------------
    User: "Verify FDV for IRYS from official sources"

    Claude Code (internally):
        1. WebSearch("IRYS token FDV fully diluted valuation official announcement")
        2. WebFetch(Medium article) → Extract FDV mention
        3. WebFetch(CryptoRank) → Extract FDV
        4. WebFetch(Official website) → Calculate from supply × price
        5. Compare all 3 sources

    Claude Code Response:
    ---------------------
    "FDV Verification for IRYS:
    1. Official Medium (Nov 20): $1B FDV mentioned
    2. CryptoRank: $1.05B FDV (5% higher)
    3. Website calculation: 1B supply × $1.00 = $1B

    Consensus: 2/3 sources confirm $1B FDV (±5%)
    Recommendation: Set fdv_source = 'confirmed'
    Confidence: 90%"

    Update Consolidated Data:
    -------------------------
    # In Phase 0.5, update:
    consolidated["fdv"] = 1000000000
    consolidated["fdv_source"] = "confirmed"
    consolidated["fdv_confidence"] = 90
    # Remove FDV blocker → Conviction no longer capped at 6.5/10
    """
    pass


if __name__ == "__main__":
    """
    Print available WebFetch patterns for reference.
    """
    print("=" * 80)
    print("WEBFETCH HELPER PATTERNS - Quick Reference")
    print("=" * 80)
    print()

    patterns = [
        fetch_otc_data_pattern(),
        fetch_social_metrics_pattern(),
        fetch_fdv_verification_pattern(),
        fetch_token_allocation_pattern(),
        fetch_new_token_price_pattern()
    ]

    for pattern in patterns:
        print(f"Pattern: {pattern['pattern_name']}")
        print(f"When to use: {pattern['when_to_use']}")
        print(f"Trust level: {pattern['trust_level']}%")
        print(f"URL: {pattern.get('url_template', 'Multiple sources')}")
        print()

    print("=" * 80)
    print("Usage: Import functions from this module for WebFetch templates")
    print("=" * 80)
