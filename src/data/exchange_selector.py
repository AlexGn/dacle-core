"""
Exchange Selection Helper

Determines the best exchange for analyzing a given token based on:
1. Listing exchanges from tge_calendar
2. Exchange tier preferences (Binance > Bybit > Gate > MEXC)
3. Token-specific overrides for known tokens
4. Intelligent fallbacks

This ensures we always use the most reliable exchange with the best data quality.

Migration History:
- Session 267: Migrated from scripts/helpers/exchange_selector.py

Usage:
    from src.data.exchange_selector import select_exchange, EXCHANGE_TIERS
"""

from typing import Optional, List
from src.knowledge.supabase_client import SupabaseKnowledgeBase


# Exchange tier mapping (lower tier = higher priority)
EXCHANGE_TIERS = {
    # Tier 1: Most reliable, best data quality
    "binance": 1,
    "coinbase": 1,

    # Tier 2: Good data quality
    "bybit": 2,
    "okx": 2,
    "gate.io": 2,
    "gate": 2,
    "gateio": 2,
    "hyperliquid": 2,

    # Tier 3: Fallback options
    "mexc": 3,
    "kucoin": 3,
    "bitget": 3,
}

# Token-specific exchange overrides (we know these work well)
TOKEN_EXCHANGE_MAP = {
    "MONAD": "gate",  # MONAD has best data on Gate.io
    "MON": "gate",
    "IRYS": "binance",  # IRYS commonly on Binance
}


def normalize_exchange_name(exchange: str) -> str:
    """
    Normalize exchange names to lowercase and handle variations.

    Examples:
        "Gate.io" → "gate"
        "BINANCE" → "binance"
        "Bybit" → "bybit"
    """
    exchange_lower = exchange.lower().strip()

    # Handle common variations
    if "gate" in exchange_lower:
        return "gate"

    return exchange_lower


def get_exchange_tier(exchange: str) -> int:
    """
    Get the tier (priority) of an exchange.

    Args:
        exchange: Exchange name (case-insensitive)

    Returns:
        Tier number (1 = highest priority, 3 = lowest)
        Returns 999 if exchange not in tier mapping
    """
    normalized = normalize_exchange_name(exchange)
    return EXCHANGE_TIERS.get(normalized, 999)


def select_best_exchange(exchanges: List[str]) -> Optional[str]:
    """
    Select the best exchange from a list based on tier priority.

    Args:
        exchanges: List of exchange names

    Returns:
        Best exchange name (normalized), or None if list is empty

    Example:
        >>> select_best_exchange(["MEXC", "Binance", "Gate.io"])
        "binance"  # Tier 1 beats tier 2 and 3
    """
    if not exchanges:
        return None

    # Sort by tier (lower tier number = higher priority)
    sorted_exchanges = sorted(
        exchanges,
        key=lambda e: (get_exchange_tier(e), e.lower())
    )

    # Return the best (first in sorted list)
    return normalize_exchange_name(sorted_exchanges[0])


def get_listing_exchanges(token_symbol: str) -> List[str]:
    """
    Get the list of exchanges where a token is listed from tge_calendar.

    Args:
        token_symbol: Token symbol (e.g., "MONAD", "IRYS")

    Returns:
        List of exchange names, or empty list if not found
    """
    try:
        kb = SupabaseKnowledgeBase()

        # Query tge_calendar for listing exchanges
        result = kb.client.table('tge_calendar')\
            .select('listing_exchanges')\
            .eq('token_symbol', token_symbol.upper())\
            .order('tge_date', desc=True)\
            .limit(1)\
            .execute()

        if result.data and len(result.data) > 0:
            exchanges = result.data[0].get('listing_exchanges', [])
            return exchanges if exchanges else []

        return []

    except Exception as e:
        print(f"Warning: Could not fetch listing exchanges for {token_symbol}: {e}")
        return []


def determine_best_exchange(token_symbol: str, fallback: str = "binance") -> str:
    """
    Determine the best exchange to use for analyzing a token.

    Fallback chain:
    1. Token-specific override (if exists in TOKEN_EXCHANGE_MAP)
    2. Listing exchanges from tge_calendar (pick tier-1, then tier-2)
    3. Fallback parameter (default: "binance")

    Args:
        token_symbol: Token symbol (e.g., "MONAD", "IRYS")
        fallback: Fallback exchange if no data found (default: "binance")

    Returns:
        Exchange name (normalized lowercase)

    Examples:
        >>> determine_best_exchange("MONAD")
        "gate"  # Token-specific override

        >>> determine_best_exchange("IRYS")
        "binance"  # From tge_calendar or token-specific

        >>> determine_best_exchange("UNKNOWN_TOKEN")
        "binance"  # Fallback
    """
    # 1. Check token-specific overrides first
    if token_symbol.upper() in TOKEN_EXCHANGE_MAP:
        return TOKEN_EXCHANGE_MAP[token_symbol.upper()]

    # 2. Try to get listing exchanges from tge_calendar
    listing_exchanges = get_listing_exchanges(token_symbol)

    if listing_exchanges:
        best_exchange = select_best_exchange(listing_exchanges)
        if best_exchange:
            return best_exchange

    # 3. Fallback to default
    return normalize_exchange_name(fallback)


if __name__ == "__main__":
    # Test the exchange selector
    print("Testing Exchange Selector")
    print("=" * 50)

    # Test 1: Token with override
    print(f"\nMONAD: {determine_best_exchange('MONAD')}")

    # Test 2: Token from calendar (if exists)
    print(f"IRYS: {determine_best_exchange('IRYS')}")

    # Test 3: Unknown token (fallback)
    print(f"UNKNOWN: {determine_best_exchange('UNKNOWN_TOKEN')}")

    # Test 4: Exchange tier sorting
    print(f"\nBest from [MEXC, Binance, Gate]: {select_best_exchange(['MEXC', 'Binance', 'Gate.io'])}")
    print(f"Best from [Bybit, OKX]: {select_best_exchange(['Bybit', 'OKX'])}")
