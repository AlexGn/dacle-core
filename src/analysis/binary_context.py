"""
DACLE Binary 1h Context Engine
Bundles Micro-VWAP and Binance Aggression into a single high-confidence
decision vector for 1h binary markets.

Moved from src/polymarket/binary_context.py during Phase 1 pillar decoupling.
Binary1hContext is a leaf class (no polymarket deps) consumed by BOTH the
polymarket daemon and the lighter daemon — genuinely shared, so the canonical
home is the shared analysis layer. src/polymarket/binary_context.py re-exports
it for backward compatibility.
"""

import logging
import time
from typing import Optional, Dict

logger = logging.getLogger(__name__)


class Binary1hContext:
    def __init__(self, config: Optional[Dict] = None):
        if config is None:
            config = {}
        self.vwap_window_sec = config.get("vwap_window_sec", 300)
        self.aggression_threshold = config.get("aggression_threshold", 0.6)
        self.stale_ttl_sec = config.get("stale_ttl_sec", 60)

        # State
        now = time.time()
        self.binance_aggression: float = 0.5  # 0.0 (Bearish) to 1.0 (Bullish)
        self.local_vwap: float = 0.0
        self.last_update_ts: float = now
        self.last_poll_success_ts: float = now

    def update_binance_aggression(self, aggression: float):
        """Update from Binance Aggression Stream."""
        self.binance_aggression = aggression
        self.last_update_ts = time.time()

    def update_poll_success(self, ts: Optional[float] = None):
        """Update upstream poll freshness independently from signal freshness."""
        self.last_poll_success_ts = ts if ts is not None else time.time()

    def update_local_vwap(self, vwap: float):
        """Update from Polymarket Order Book / Fills."""
        self.local_vwap = vwap

    def get_aggression_score(self) -> float:
        """
        Returns normalized binance aggression (0.0 to 1.0).
        0.5 is neutral. >0.6 is bullish, <0.4 is bearish.
        """
        if self.is_stale():
            return 0.5
        return self.binance_aggression

    def get_bias(self, current_price: float) -> str:
        """
        Returns 'BULLISH', 'BEARISH', or 'NEUTRAL' based on
        convergence/divergence between local price and Binance lead.
        """
        # 1. Price vs VWAP (Reversion Bias)
        if self.local_vwap > 0:
            vwap_dist = (current_price - self.local_vwap) / self.local_vwap
            # If price is >0.2% above VWAP, consider it locally overextended
            if vwap_dist > 0.002:
                return "BEARISH"
            elif vwap_dist < -0.002:
                return "BULLISH"

        # 2. Aggression Lead (Momentum Bias)
        if self.binance_aggression >= self.aggression_threshold:
            return "BULLISH"
        elif self.binance_aggression <= (1.0 - self.aggression_threshold):
            return "BEARISH"

        return "NEUTRAL"

    def is_stale(self) -> bool:
        """Checks if the qualifying signal stream has gone stale."""
        return (time.time() - self.last_update_ts) > self.stale_ttl_sec

    def is_feed_dead(self, ttl: Optional[float] = None) -> bool:
        """Checks if upstream polling itself has gone stale."""
        effective_ttl = float(ttl if ttl is not None else self.stale_ttl_sec)
        return (time.time() - self.last_poll_success_ts) > effective_ttl

    def get_context_vector(self, current_price: float) -> Dict[str, any]:
        """Returns the full context for SniperBrain decision making."""
        bias = self.get_bias(current_price)
        return {
            "bias": bias,
            "aggression": self.binance_aggression,
            "vwap_dist_bps": round(((current_price - self.local_vwap) / self.local_vwap * 10000), 1) if self.local_vwap > 0 else 0,
            "is_stale": self.is_stale(),
            "is_feed_dead": self.is_feed_dead(),
            "timestamp": self.last_update_ts,
        }