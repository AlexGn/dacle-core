"""
Cross-Venue Arbitrage Engine
Handles net-of-fee edge calculations, jitter guards, and liquidity analysis between venues.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timezone

from src.execution.venue_adapter import VenueType, OrderBook

logger = logging.getLogger(__name__)

@dataclass
class ArbVerdict:
    """Consolidated cross-venue arb verdict."""
    is_profitable: bool
    net_edge_bps: float
    max_size_usdc: float
    reason_code: str  # "OK", "LOW_EDGE", "JITTER_GUARD", "LOW_DEPTH"
    buy_venue: Optional[VenueType] = None
    sell_venue: Optional[VenueType] = None
    buy_price: float = 0.0
    sell_price: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

class CrossVenueArbEngine:
    def __init__(self, config: dict):
        arb_cfg = config.get("cross_venue_arb", {})
        self.min_net_edge_bps = arb_cfg.get("min_net_edge_bps", 25)
        self.jitter_guard_ticks = arb_cfg.get("jitter_guard_ticks", 3)
        self.slippage_buffer_bps = arb_cfg.get("slippage_buffer_bps", 5)

        # State for jitter guard: {unified_id: consecutive_ticks}
        self._signal_counts: Dict[str, int] = {}
        self._last_edges: Dict[str, float] = {}

    def calculate_net_edge(
        self,
        buy_venue: VenueType,
        buy_price: float,
        sell_venue: VenueType,
        sell_price: float,
        venues_map: Dict[VenueType, Any]
    ) -> float:
        """
        Calculate net edge after venue-specific taker fees and slippage buffer.
        Net Edge = (Sell - Buy) / Buy - (BuyFee + SellFee) - Buffer
        """
        # 1. Gross Edge
        gross_edge = (sell_price - buy_price) / buy_price

        # 2. Fees
        buy_venue_obj = venues_map.get(buy_venue)
        sell_venue_obj = venues_map.get(sell_venue)

        buy_fee = 0.0
        sell_fee = 0.0

        if buy_venue_obj:
            buy_fee = buy_venue_obj.get_fee_schedule().get("taker_fee", 0.0)
        if sell_venue_obj:
            sell_fee = sell_venue_obj.get_fee_schedule().get("taker_fee", 0.0)

        # 3. Total Net Edge in BPS
        net_edge = (gross_edge - buy_fee - sell_fee) - (self.slippage_buffer_bps / 10000.0)
        return net_edge * 10000

    def estimate_max_size(
        self,
        buy_venue: VenueType,
        buy_price: float,
        sell_venue: VenueType,
        sell_price: float,
        books: Dict[VenueType, OrderBook]
    ) -> float:
        """
        Analyze order book depth to find the maximum executable size at the given prices.
        """
        buy_book = books.get(buy_venue)
        sell_book = books.get(sell_venue)

        if not buy_book or not sell_book:
            return 0.0

        # Cumulative size at or better than target price
        def get_depth(book: OrderBook, target_px: float, side: str) -> float:
            total = 0.0
            if side == "BUY": # We are buying, so look at ASKS
                for level in book.asks:
                    if level.price <= target_px:
                        total += level.size
                    else:
                        break
            else: # We are selling, so look at BIDS
                for level in book.bids:
                    if level.price >= target_px:
                        total += level.size
                    else:
                        break
            return total

        buy_depth = get_depth(buy_book, buy_price, "BUY")
        sell_depth = get_depth(sell_book, sell_price, "SELL")

        return min(buy_depth, sell_depth)

    def analyze(
        self,
        unified_id: str,
        prices: Dict[VenueType, Dict[str, float]],
        books: Dict[VenueType, OrderBook],
        venues_map: Dict[VenueType, Any]
    ) -> ArbVerdict:
        """
        Perform full arb analysis for a market.
        """
        if len(prices) < 2:
            return ArbVerdict(False, 0.0, 0.0, "INSUFFICIENT_VENUES")

        # Find best bid and best ask across venues
        best_bid = 0.0
        best_bid_venue = None
        best_ask = float('inf')
        best_ask_venue = None

        for v_type, data in prices.items():
            bid = data.get("bid")
            ask = data.get("ask")
            if bid and bid > best_bid:
                best_bid = bid
                best_bid_venue = v_type
            if ask and ask < best_ask:
                best_ask = ask
                best_ask_venue = v_type

        if not best_bid_venue or not best_ask_venue or best_bid <= best_ask:
            return ArbVerdict(False, 0.0, 0.0, "NO_SPREAD")

        # Calculate net edge
        net_edge_bps = self.calculate_net_edge(
            best_ask_venue, best_ask,
            best_bid_venue, best_bid,
            venues_map
        )

        if net_edge_bps < self.min_net_edge_bps:
            self._signal_counts[unified_id] = 0
            return ArbVerdict(False, net_edge_bps, 0.0, "LOW_EDGE")

        # Jitter Guard
        self._signal_counts[unified_id] = self._signal_counts.get(unified_id, 0) + 1
        if self._signal_counts[unified_id] < self.jitter_guard_ticks:
            return ArbVerdict(
                False, net_edge_bps, 0.0, "JITTER_GUARD",
                metadata={"streak": self._signal_counts[unified_id], "required": self.jitter_guard_ticks}
            )

        # Size estimation
        max_size = self.estimate_max_size(
            best_ask_venue, best_ask,
            best_bid_venue, best_bid,
            books
        )

        if max_size < 1.0: # Min size threshold
            self._signal_counts[unified_id] = 0
            return ArbVerdict(False, net_edge_bps, max_size, "LOW_DEPTH")

        return ArbVerdict(
            True, net_edge_bps, max_size, "OK",
            buy_venue=best_ask_venue,
            buy_price=best_ask,
            sell_venue=best_bid_venue,
            sell_price=best_bid
        )
