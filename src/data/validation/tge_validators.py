#!/usr/bin/env python3
"""
TGE Validation Helpers
======================

Helper functions for validating TGE data:
- Unlock schedule extraction
- Listing venue validation
- Execution blocker detection

Session: claude/fix-tge-analysis-bugs-01GvEJVajVgrUd3WETGf72HS
"""

from typing import Dict, List, Optional, Tuple


def extract_unlock_schedule(tge_data: Dict) -> Dict[str, float]:
    """
    Extract unlock schedule from TGE tokenomics data

    Args:
        tge_data: TGE data from CryptoRank or validation result

    Returns:
        Dict with unlock percentages:
        {
            "unlock_at_tge_pct": 15.0,  # Total % unlocked at TGE
            "investors_unlock_pct": 0.0,  # % of investor allocation unlocked
            "team_unlock_pct": 0.0,  # % of team allocation unlocked
            "circulating_supply_pct": 15.0,  # Same as unlock_at_tge_pct
            "locked_supply_pct": 85.0,  # 100 - unlock_at_tge_pct
            "immediate_sell_pressure": "LOW"  # LOW/MEDIUM/HIGH
        }

    Example tokenomics from CryptoRank:
        {
            "allocation_breakdown": {
                "Investors": {"pct": 13.62, "tge_unlock": 0.0},
                "Team": {"pct": 10.0, "tge_unlock": 0.0},
                "Liquidity": {"pct": 7.0, "tge_unlock": 100.0},
                "Community": {"pct": 50.0, "tge_unlock": 10.0}
            }
        }
    """
    result = {
        "unlock_at_tge_pct": 0.0,
        "investors_unlock_pct": 0.0,
        "team_unlock_pct": 0.0,
        "circulating_supply_pct": 0.0,
        "locked_supply_pct": 100.0,
        "immediate_sell_pressure": "UNKNOWN"
    }

    # Try to extract from allocation_breakdown
    allocation = tge_data.get("allocation_breakdown") or tge_data.get("tokenomics", {}).get("allocation_breakdown")

    if allocation:
        total_unlock = 0.0
        investors_unlock = 0.0
        team_unlock = 0.0

        for category, data in allocation.items():
            category_pct = data.get("pct", 0.0)
            tge_unlock_pct = data.get("tge_unlock", 0.0)

            # Calculate actual unlock amount (category_pct * tge_unlock_pct / 100)
            unlock_amount = category_pct * (tge_unlock_pct / 100.0)
            total_unlock += unlock_amount

            # Track investor and team unlocks specifically
            category_lower = category.lower()
            if "invest" in category_lower or "vc" in category_lower or "private" in category_lower:
                investors_unlock = tge_unlock_pct
            elif "team" in category_lower or "advisor" in category_lower:
                team_unlock = tge_unlock_pct

        result["unlock_at_tge_pct"] = round(total_unlock, 2)
        result["investors_unlock_pct"] = investors_unlock
        result["team_unlock_pct"] = team_unlock
        result["circulating_supply_pct"] = result["unlock_at_tge_pct"]
        result["locked_supply_pct"] = round(100.0 - total_unlock, 2)

    # Try alternative format (direct float_percent field)
    elif "float_percent" in tge_data or "circulating_supply_pct" in tge_data:
        float_pct = tge_data.get("float_percent") or tge_data.get("circulating_supply_pct", 0.0)
        result["unlock_at_tge_pct"] = float_pct
        result["circulating_supply_pct"] = float_pct
        result["locked_supply_pct"] = 100.0 - float_pct

    # Classify sell pressure based on unlock amounts
    unlock = result["unlock_at_tge_pct"]
    investors = result["investors_unlock_pct"]

    if investors >= 10.0:  # VCs unlocking 10%+ = HIGH pressure
        result["immediate_sell_pressure"] = "HIGH"
    elif unlock >= 30.0:  # Total unlock >30% = MEDIUM pressure
        result["immediate_sell_pressure"] = "MEDIUM"
    elif unlock >= 10.0:  # Total unlock 10-30% = LOW pressure
        result["immediate_sell_pressure"] = "LOW"
    else:  # <10% unlock = VERY LOW pressure
        result["immediate_sell_pressure"] = "VERY LOW"

    return result


def validate_listing_venues(
    tge_data: Dict,
    confirmed_venues: Optional[List[str]] = None
) -> Tuple[List[str], bool, Optional[str]]:
    """
    Validate listing venues and check if shorting is possible

    Args:
        tge_data: TGE data with listing information
        confirmed_venues: Optional list of confirmed venues (overrides tge_data)

    Returns:
        Tuple of (listing_venues, can_short, execution_blocker)

    Examples:
        >>> validate_listing_venues({"listing_venues": ["Hyperliquid", "Binance"]})
        (["Hyperliquid", "Binance"], True, None)

        >>> validate_listing_venues({"listing_venues": ["Aerodrome DEX"]})
        (["Aerodrome DEX"], False, "DEX-only listing (Aerodrome DEX) - no shorting available")
    """
    # Perpetual exchanges where shorting is available
    PERP_EXCHANGES = {
        "hyperliquid",
        "binance",
        "mexc",
        "aster",
        "bybit",
        "okx",
        "gate.io",
        "kucoin",
        "bitget",
        "dydx",
        "gmx",
    }

    # DEX-only platforms (no shorting)
    DEX_ONLY = {
        "uniswap",
        "aerodrome",
        "pancakeswap",
        "sushiswap",
        "curve",
        "balancer",
        "raydium",
        "jupiter",
    }

    # Get venues
    if confirmed_venues:
        venues = confirmed_venues
    else:
        venues = (
            tge_data.get("listing_venues") or
            tge_data.get("exchanges") or
            tge_data.get("confirmed_listings") or
            []
        )

    # Convert to list if string
    if isinstance(venues, str):
        venues = [v.strip() for v in venues.split(",")]

    # Normalize venue names
    venues_normalized = [v.lower().strip() for v in venues]

    # Check if any perpetual exchange is present
    can_short = any(
        any(perp in venue for perp in PERP_EXCHANGES)
        for venue in venues_normalized
    )

    # Check if only DEX (execution blocker)
    is_dex_only = (
        len(venues) > 0 and
        all(any(dex in venue for dex in DEX_ONLY) for venue in venues_normalized)
    )

    execution_blocker = None
    if is_dex_only:
        execution_blocker = f"DEX-only listing ({', '.join(venues)}) - no shorting available"
    elif not can_short and len(venues) > 0:
        execution_blocker = f"No perpetual exchanges confirmed ({', '.join(venues)}) - shorting unavailable"

    return (venues, can_short, execution_blocker)


def detect_execution_blockers(
    tge_data: Dict,
    validation_result=None,
    days_until_tge: Optional[int] = None
) -> List[str]:
    """
    Detect all execution blockers for TGE short

    Args:
        tge_data: Full TGE data
        validation_result: Agent 0 validation result
        days_until_tge: Days until TGE (if known)

    Returns:
        List of execution blocker messages

    Example blockers:
        - "TGE in <24 hours - insufficient time for analysis"
        - "DEX-only listing - no shorting available"
        - "FDV unknown - cannot calculate FDV/MC ratio (30% of score)"
        - "Float % unknown - cannot assess overvaluation"
        - "No VC funding data - cannot calculate markup"
    """
    blockers = []

    # Time blocker
    if days_until_tge is not None and days_until_tge < 1:
        blockers.append("TGE in <24 hours - insufficient time for analysis")

    # Listing venue blocker
    venues, can_short, venue_blocker = validate_listing_venues(tge_data)
    if venue_blocker:
        blockers.append(venue_blocker)

    # Critical data blockers
    if validation_result:
        validation_blockers = getattr(validation_result, 'blockers', [])
        blockers.extend(validation_blockers)
    else:
        # Manual check if no validation result
        if not tge_data.get("fdv"):
            blockers.append("FDV unknown - cannot calculate FDV/MC ratio (30% of score)")

        if not tge_data.get("float_percent") and not tge_data.get("circulating_supply_pct"):
            blockers.append("Float % unknown - cannot assess overvaluation")

        if not tge_data.get("vc_funding") and not tge_data.get("total_raised"):
            blockers.append("No VC funding data - cannot calculate markup (20% of score)")

    return blockers


# Quick test
if __name__ == "__main__":
    print("TGE Validators Tests\n")

    # Test 1: Unlock schedule extraction
    print("Test 1: Extract unlock schedule (ANICHESS example)")
    test_data = {
        "allocation_breakdown": {
            "Investors": {"pct": 13.62, "tge_unlock": 0.0},
            "Team": {"pct": 10.0, "tge_unlock": 0.0},
            "Liquidity": {"pct": 7.0, "tge_unlock": 100.0},
            "Community": {"pct": 50.0, "tge_unlock": 10.0}
        }
    }
    unlock = extract_unlock_schedule(test_data)
    print(f"  Total unlock at TGE: {unlock['unlock_at_tge_pct']}%")
    print(f"  Investors unlock: {unlock['investors_unlock_pct']}%")
    print(f"  Team unlock: {unlock['team_unlock_pct']}%")
    print(f"  Sell pressure: {unlock['immediate_sell_pressure']}\n")

    # Test 2: Listing venue validation - Can short
    print("Test 2: Listing venues - Hyperliquid + Binance (CAN SHORT)")
    venues, can_short, blocker = validate_listing_venues(
        {"listing_venues": ["Hyperliquid", "Binance"]}
    )
    print(f"  Venues: {venues}")
    print(f"  Can short: {can_short}")
    print(f"  Blocker: {blocker}\n")

    # Test 3: Listing venue validation - DEX only (CANNOT SHORT)
    print("Test 3: Listing venues - Aerodrome DEX only (CANNOT SHORT)")
    venues, can_short, blocker = validate_listing_venues(
        {"listing_venues": ["Aerodrome DEX"]}
    )
    print(f"  Venues: {venues}")
    print(f"  Can short: {can_short}")
    print(f"  Blocker: {blocker}\n")

    # Test 4: Execution blockers
    print("Test 4: Detect execution blockers")
    blockers = detect_execution_blockers(
        {"listing_venues": ["Aerodrome DEX"]},
        days_until_tge=0
    )
    print(f"  Blockers found: {len(blockers)}")
    for i, blocker in enumerate(blockers, 1):
        print(f"    {i}. {blocker}")
