#!/usr/bin/env python3
"""
OTC Premium Tracker

Tracks OTC premium (Listing Price vs OTC Price) for tokens.

Session 139: Phase 2 - OTC Premium Tracking System

OTC Premium = (Listing Price - OTC Price) / OTC Price × 100%

Higher premium = early holders already exited = less sell pressure at listing

Data Sources:
1. Whales Market (manual research for now)
2. Aevo (pre-launch futures)
3. Twitter OTC announcements

Expected Correlation: ρ ≈ +0.40 (higher premium = better outcomes due to reduced sell pressure)
"""

import logging
from typing import Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class OTCPremiumTracker:
    """
    Track OTC premium from various sources.

    OTC Premium = (Listing Price - OTC Price) / OTC Price × 100%

    Interpretation:
    - Positive premium (listing > OTC): Early holders exited OTC, less sell pressure
    - Negative premium (listing < OTC): Early holders waited for listing, MORE sell pressure
    - Zero premium: Fair pricing, neutral signal
    """

    def __init__(self):
        """Initialize OTC premium tracker."""
        # Manual OTC price database (to be populated via research)
        self._manual_otc_data: Dict[str, Dict] = {}

    def get_otc_premium(
        self,
        token_symbol: str,
        listing_price: float
    ) -> Optional[Dict]:
        """
        Get OTC premium for token.

        Args:
            token_symbol: Token symbol (e.g., "APT", "SUI")
            listing_price: Actual listing price at TGE (USD)

        Returns:
            {
                "otc_price": float,
                "listing_price": float,
                "premium_pct": float,  # Percentage premium
                "data_source": str,    # "whales_market", "aevo", "manual"
                "confidence": float,   # 0.0-1.0
                "notes": str
            }
            Returns None if no OTC data available
        """
        # Check manual database
        otc_data = self._manual_otc_data.get(token_symbol.upper())

        if not otc_data:
            logger.debug(f"No OTC data available for {token_symbol}")
            return None

        otc_price = otc_data.get("otc_price")
        if not otc_price:
            return None

        # Calculate premium
        premium_pct = ((listing_price - otc_price) / otc_price) * 100

        result = {
            "otc_price": otc_price,
            "listing_price": listing_price,
            "premium_pct": premium_pct,
            "data_source": otc_data.get("source", "manual"),
            "confidence": otc_data.get("confidence", 0.5),
            "notes": otc_data.get("notes", "")
        }

        logger.info(
            f"{token_symbol}: OTC ${otc_price:.4f} → Listing ${listing_price:.4f} "
            f"({premium_pct:+.1f}% premium)"
        )

        return result

    def add_manual_otc_data(
        self,
        token_symbol: str,
        otc_price: float,
        source: str = "manual",
        confidence: float = 0.5,
        notes: str = ""
    ):
        """
        Add manual OTC price data.

        Args:
            token_symbol: Token symbol (e.g., "APT")
            otc_price: OTC price in USD
            source: Data source ("whales_market", "aevo", "twitter", "manual")
            confidence: Confidence in data (0.0-1.0)
            notes: Additional notes/context
        """
        self._manual_otc_data[token_symbol.upper()] = {
            "otc_price": otc_price,
            "source": source,
            "confidence": confidence,
            "notes": notes,
            "added_at": datetime.now().isoformat()
        }

        logger.info(f"Added OTC data for {token_symbol}: ${otc_price:.4f} ({source})")

    def load_known_otc_prices(self):
        """
        Load known OTC prices from research.

        This is a placeholder for manual data entry.
        In production, this would query a database or API.
        """
        # Known OTC prices from research (to be populated)
        known_prices = {
            # Example format:
            # "APT": {
            #     "otc_price": 7.50,
            #     "source": "whales_market",
            #     "confidence": 0.8,
            #     "notes": "Pre-TGE OTC trading on Whales Market"
            # },
        }

        for symbol, data in known_prices.items():
            self.add_manual_otc_data(
                symbol,
                data["otc_price"],
                data.get("source", "manual"),
                data.get("confidence", 0.5),
                data.get("notes", "")
            )

        logger.info(f"Loaded {len(known_prices)} known OTC prices")

    def get_available_tokens(self) -> list:
        """Get list of tokens with OTC data."""
        return list(self._manual_otc_data.keys())


# Global instance (singleton pattern)
_otc_tracker: Optional[OTCPremiumTracker] = None


def get_otc_tracker() -> OTCPremiumTracker:
    """Get global OTCPremiumTracker instance."""
    global _otc_tracker
    if _otc_tracker is None:
        _otc_tracker = OTCPremiumTracker()
        _otc_tracker.load_known_otc_prices()
    return _otc_tracker


def get_otc_premium(token_symbol: str, listing_price: float) -> Optional[Dict]:
    """
    Convenience function to get OTC premium.

    Args:
        token_symbol: Token symbol (e.g., "APT")
        listing_price: Listing price at TGE (USD)

    Returns:
        OTC premium data dict or None

    Example:
        >>> premium = get_otc_premium("APT", 8.50)
        >>> if premium:
        >>>     print(f"OTC Premium: {premium['premium_pct']:.1f}%")
    """
    tracker = get_otc_tracker()
    return tracker.get_otc_premium(token_symbol, listing_price)


if __name__ == "__main__":
    # Test the tracker
    logging.basicConfig(level=logging.INFO)

    print("\n" + "=" * 80)
    print("OTC PREMIUM TRACKER - SESSION 139")
    print("=" * 80)

    tracker = OTCPremiumTracker()

    # Add some test data
    print("\n📝 Adding test OTC data...")
    tracker.add_manual_otc_data("APT", 7.50, "whales_market", 0.8, "Pre-TGE OTC on Whales Market")
    tracker.add_manual_otc_data("SUI", 0.45, "aevo", 0.9, "Aevo pre-launch futures")

    # Test premium calculation
    print("\n💰 Calculating OTC premiums...")

    apt_premium = tracker.get_otc_premium("APT", 8.50)
    if apt_premium:
        print(f"\nAPT:")
        print(f"  OTC Price: ${apt_premium['otc_price']:.2f}")
        print(f"  Listing Price: ${apt_premium['listing_price']:.2f}")
        print(f"  Premium: {apt_premium['premium_pct']:+.1f}%")
        print(f"  Source: {apt_premium['data_source']}")

    sui_premium = tracker.get_otc_premium("SUI", 0.38)
    if sui_premium:
        print(f"\nSUI:")
        print(f"  OTC Price: ${sui_premium['otc_price']:.2f}")
        print(f"  Listing Price: ${sui_premium['listing_price']:.2f}")
        print(f"  Premium: {sui_premium['premium_pct']:+.1f}%")
        print(f"  Source: {sui_premium['data_source']}")

    # Test missing data
    print("\n❓ Testing missing OTC data...")
    missing = tracker.get_otc_premium("UNKNOWN", 1.00)
    if not missing:
        print("  ✅ Correctly returned None for unknown token")

    print("\n" + "=" * 80)
