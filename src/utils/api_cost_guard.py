#!/usr/bin/env python3
"""
API Cost Guard - Session 79F
Protects against runaway API costs with hard spending limits.

Purpose:
- Enforce daily/monthly spending caps per API
- Track cumulative spending across sessions
- Block requests when limits exceeded
- Provide clear warnings before limits hit

CRITICAL SAFETY FEATURE:
This module prevents accidental API cost explosions from bugs or infinite loops.
All API clients MUST use this guard before making requests.

Usage:
    from src.utils.api_cost_guard import APICostGuard

    guard = APICostGuard()

    # Before API call
    if not guard.can_spend("perplexity", estimated_cost=0.05):
        raise APILimitExceeded("Daily limit reached for Perplexity API")

    # After API call
    guard.record_spend("perplexity", actual_cost=0.048, tokens_used=1200)
"""

import json
import logging
from datetime import datetime, date
from pathlib import Path
from typing import Dict, Optional
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)


@dataclass
class APILimits:
    """Spending limits for an API"""
    daily_usd: float
    monthly_usd: float
    daily_calls: int
    warning_threshold_pct: float = 80.0  # Warn at 80% of limit


# ============================================================================
# SPENDING LIMITS - ADJUST THESE TO PROTECT YOUR WALLET
# ============================================================================

DEFAULT_LIMITS = {
    "perplexity": APILimits(
        daily_usd=1.00,       # $1/day max (~20 TGE analyses)
        monthly_usd=10.00,    # $10/month max
        daily_calls=50,       # Max 50 API calls/day
    ),
    "openai": APILimits(
        # Session 263 Optimization: Switched to GPT-4o-mini + response caching
        # Cost reduced 16.6x: $0.03 → $0.002 per call
        # Cache reduces calls by ~40%
        # New estimated cost: $0.12/day (1000 calls/month = ~$2/month)
        daily_usd=0.50,       # $0.50/day (was $2.00, reduced 4x for safety margin)
        monthly_usd=5.00,     # $5/month (was $20, reduced 4x)
        daily_calls=200,      # Increased from 100 (calls are much cheaper now)
    ),
    "anthropic": APILimits(
        daily_usd=5.00,       # $5/day max (if using Claude API)
        monthly_usd=50.00,    # $50/month max
        daily_calls=200,      # Max 200 API calls/day
    ),
    "groq": APILimits(
        # Session 436: Groq free tier — text + simple vision via Scout 17B
        # 30K TPM, 500K TPD. Leave 200 RPD buffer for ClawdBot.
        daily_usd=999.0,      # Free tier — USD limit effectively unlimited
        monthly_usd=9999.0,   # Free tier — USD limit effectively unlimited
        daily_calls=800,      # Conservative limit (leave headroom for ClawdBot)
    ),
}

# ============================================================================


class APILimitExceeded(Exception):
    """Raised when API spending limit is exceeded"""
    pass


class APICostGuard:
    """
    Enforces API spending limits to prevent cost explosions.

    Features:
    - Daily and monthly spending caps per API
    - Call count limits
    - Persistent tracking across sessions
    - Warning alerts at 80% threshold
    - Automatic reset at midnight/month start
    """

    def __init__(self, data_dir: Optional[Path] = None):
        """
        Initialize cost guard with spending tracker.

        Args:
            data_dir: Directory to store spending data (default: data/api_costs/)
        """
        if data_dir is None:
            project_root = Path(__file__).parent.parent.parent
            data_dir = project_root / "data" / "api_costs"

        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.limits = DEFAULT_LIMITS.copy()
        self.spending_file = self.data_dir / "spending_tracker.json"
        self.spending = self._load_spending()

    def _load_spending(self) -> Dict:
        """Load spending data from disk."""
        if self.spending_file.exists():
            try:
                with open(self.spending_file, 'r') as f:
                    data = json.load(f)
                    # Reset if new day/month
                    data = self._reset_if_needed(data)
                    return data
            except (json.JSONDecodeError, KeyError):
                logger.warning("Corrupted spending file - resetting")

        return self._create_fresh_spending()

    def _create_fresh_spending(self) -> Dict:
        """Create fresh spending tracker."""
        today = date.today().isoformat()
        month = date.today().strftime("%Y-%m")

        return {
            "last_reset_date": today,
            "last_reset_month": month,
            "apis": {
                api: {
                    "daily_usd": 0.0,
                    "monthly_usd": 0.0,
                    "daily_calls": 0,
                    "monthly_calls": 0,
                    "total_usd": 0.0,
                    "total_calls": 0
                }
                for api in DEFAULT_LIMITS.keys()
            }
        }

    def _reset_if_needed(self, data: Dict) -> Dict:
        """Reset daily/monthly counters if date changed."""
        today = date.today().isoformat()
        month = date.today().strftime("%Y-%m")

        # Reset daily counters
        if data.get("last_reset_date") != today:
            logger.info(f"📅 New day - resetting daily API counters")
            for api in data.get("apis", {}).values():
                api["daily_usd"] = 0.0
                api["daily_calls"] = 0
            data["last_reset_date"] = today

        # Reset monthly counters
        if data.get("last_reset_month") != month:
            logger.info(f"📅 New month - resetting monthly API counters")
            for api in data.get("apis", {}).values():
                api["monthly_usd"] = 0.0
                api["monthly_calls"] = 0
            data["last_reset_month"] = month

        return data

    def _save_spending(self) -> None:
        """Save spending data to disk."""
        with open(self.spending_file, 'w') as f:
            json.dump(self.spending, f, indent=2)

    def can_spend(
        self,
        api_name: str,
        estimated_cost: float = 0.0,
        raise_on_limit: bool = True
    ) -> bool:
        """
        Check if API request is allowed within limits.

        Args:
            api_name: API identifier (e.g., "perplexity", "openai")
            estimated_cost: Estimated cost of this request in USD
            raise_on_limit: If True, raise exception when limit exceeded

        Returns:
            True if request allowed, False if blocked

        Raises:
            APILimitExceeded: If raise_on_limit=True and limit exceeded
        """
        api_name = api_name.lower()

        if api_name not in self.limits:
            logger.warning(f"Unknown API '{api_name}' - allowing with no limits")
            return True

        limits = self.limits[api_name]
        current = self.spending["apis"].get(api_name, {})

        # Check daily USD limit
        daily_after = current.get("daily_usd", 0) + estimated_cost
        if daily_after > limits.daily_usd:
            msg = (
                f"🚫 DAILY LIMIT EXCEEDED for {api_name.upper()}!\n"
                f"   Current: ${current.get('daily_usd', 0):.4f}\n"
                f"   Limit: ${limits.daily_usd:.2f}\n"
                f"   This request would cost: ${estimated_cost:.4f}\n"
                f"   Resets at midnight"
            )
            logger.error(msg)
            if raise_on_limit:
                raise APILimitExceeded(msg)
            return False

        # Check monthly USD limit
        monthly_after = current.get("monthly_usd", 0) + estimated_cost
        if monthly_after > limits.monthly_usd:
            msg = (
                f"🚫 MONTHLY LIMIT EXCEEDED for {api_name.upper()}!\n"
                f"   Current: ${current.get('monthly_usd', 0):.4f}\n"
                f"   Limit: ${limits.monthly_usd:.2f}\n"
                f"   Resets on the 1st of next month"
            )
            logger.error(msg)
            if raise_on_limit:
                raise APILimitExceeded(msg)
            return False

        # Check daily call count
        daily_calls = current.get("daily_calls", 0)
        if daily_calls >= limits.daily_calls:
            msg = (
                f"🚫 DAILY CALL LIMIT EXCEEDED for {api_name.upper()}!\n"
                f"   Calls today: {daily_calls}\n"
                f"   Limit: {limits.daily_calls}\n"
                f"   Resets at midnight"
            )
            logger.error(msg)
            if raise_on_limit:
                raise APILimitExceeded(msg)
            return False

        # Warning at 80% threshold
        daily_pct = (daily_after / limits.daily_usd) * 100
        if daily_pct >= limits.warning_threshold_pct:
            logger.warning(
                f"⚠️ {api_name.upper()} at {daily_pct:.0f}% of daily limit "
                f"(${daily_after:.4f}/${limits.daily_usd:.2f})"
            )

        return True

    def record_spend(
        self,
        api_name: str,
        actual_cost: float,
        tokens_used: int = 0
    ) -> None:
        """
        Record actual spending after API call.

        Args:
            api_name: API identifier
            actual_cost: Actual cost in USD
            tokens_used: Number of tokens used (for logging)
        """
        api_name = api_name.lower()

        if api_name not in self.spending["apis"]:
            self.spending["apis"][api_name] = {
                "daily_usd": 0.0,
                "monthly_usd": 0.0,
                "daily_calls": 0,
                "monthly_calls": 0,
                "total_usd": 0.0,
                "total_calls": 0
            }

        api = self.spending["apis"][api_name]
        api["daily_usd"] += actual_cost
        api["monthly_usd"] += actual_cost
        api["daily_calls"] += 1
        api["monthly_calls"] += 1
        api["total_usd"] += actual_cost
        api["total_calls"] += 1

        self._save_spending()

        logger.debug(
            f"📊 {api_name}: ${actual_cost:.4f} ({tokens_used} tokens) | "
            f"Daily: ${api['daily_usd']:.4f} | "
            f"Monthly: ${api['monthly_usd']:.4f}"
        )

    def get_status(self, api_name: Optional[str] = None) -> Dict:
        """
        Get current spending status for one or all APIs.

        Args:
            api_name: Specific API to check (None for all)

        Returns:
            Dict with spending status and remaining limits
        """
        if api_name:
            api_name = api_name.lower()
            current = self.spending["apis"].get(api_name, {})
            limits = self.limits.get(api_name)

            if not limits:
                return {"error": f"Unknown API: {api_name}"}

            return {
                "api": api_name,
                "daily": {
                    "spent_usd": current.get("daily_usd", 0),
                    "limit_usd": limits.daily_usd,
                    "remaining_usd": limits.daily_usd - current.get("daily_usd", 0),
                    "calls": current.get("daily_calls", 0),
                    "max_calls": limits.daily_calls,
                    "pct_used": (current.get("daily_usd", 0) / limits.daily_usd) * 100
                },
                "monthly": {
                    "spent_usd": current.get("monthly_usd", 0),
                    "limit_usd": limits.monthly_usd,
                    "remaining_usd": limits.monthly_usd - current.get("monthly_usd", 0),
                    "calls": current.get("monthly_calls", 0),
                    "pct_used": (current.get("monthly_usd", 0) / limits.monthly_usd) * 100
                },
                "total": {
                    "spent_usd": current.get("total_usd", 0),
                    "calls": current.get("total_calls", 0)
                }
            }

        # Return status for all APIs
        return {
            api: self.get_status(api) for api in self.limits.keys()
        }

    def set_limits(
        self,
        api_name: str,
        daily_usd: Optional[float] = None,
        monthly_usd: Optional[float] = None,
        daily_calls: Optional[int] = None
    ) -> None:
        """
        Update spending limits for an API.

        Args:
            api_name: API identifier
            daily_usd: New daily USD limit
            monthly_usd: New monthly USD limit
            daily_calls: New daily call limit
        """
        api_name = api_name.lower()

        if api_name not in self.limits:
            self.limits[api_name] = APILimits(
                daily_usd=daily_usd or 1.0,
                monthly_usd=monthly_usd or 10.0,
                daily_calls=daily_calls or 50
            )
        else:
            current = self.limits[api_name]
            self.limits[api_name] = APILimits(
                daily_usd=daily_usd or current.daily_usd,
                monthly_usd=monthly_usd or current.monthly_usd,
                daily_calls=daily_calls or current.daily_calls
            )

        logger.info(
            f"✅ Updated {api_name} limits: "
            f"${self.limits[api_name].daily_usd}/day, "
            f"${self.limits[api_name].monthly_usd}/month, "
            f"{self.limits[api_name].daily_calls} calls/day"
        )


# Singleton instance for global access
_guard_instance: Optional[APICostGuard] = None


def get_cost_guard() -> APICostGuard:
    """Get or create the global cost guard instance."""
    global _guard_instance
    if _guard_instance is None:
        _guard_instance = APICostGuard()
    return _guard_instance


if __name__ == "__main__":
    # Test cost guard
    logging.basicConfig(level=logging.INFO)

    guard = APICostGuard()

    print("=== API Cost Guard Status ===\n")

    for api in ["perplexity", "openai"]:
        status = guard.get_status(api)
        print(f"{api.upper()}:")
        print(f"  Daily: ${status['daily']['spent_usd']:.4f} / ${status['daily']['limit_usd']:.2f}")
        print(f"  Monthly: ${status['monthly']['spent_usd']:.4f} / ${status['monthly']['limit_usd']:.2f}")
        print(f"  Total: ${status['total']['spent_usd']:.4f} ({status['total']['calls']} calls)")
        print()

    # Test recording
    print("Testing spend recording...")
    guard.record_spend("perplexity", 0.05, 1000)
    guard.record_spend("openai", 0.02, 500)

    print("\nAfter recording:")
    for api in ["perplexity", "openai"]:
        status = guard.get_status(api)
        print(f"  {api}: ${status['daily']['spent_usd']:.4f} daily")
