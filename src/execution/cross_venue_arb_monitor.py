"""
Cross-Venue Arbitrage Monitor
Implements the lifecycle loop for detecting and executing cross-venue arbitrage.
"""

import logging
import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from src.execution.venue_adapter import VenueAdapter, VenueType
from src.execution.cross_venue_arb_engine import CrossVenueArbEngine, ArbVerdict
from src.execution.state_manager import ExecutionStateManager

logger = logging.getLogger(__name__)

class CrossVenueArbMonitor:
    """
    Monitor that orchestrates the arb loop:
    Mapping -> Prices -> Engine Verdict -> Execution -> State Management.
    """
    def __init__(self, config: dict):
        self.config = config
        self.adapter = VenueAdapter(config)
        self.engine = CrossVenueArbEngine(config)
        self.state_mgr = ExecutionStateManager()

        self.mappings_path = config.get("arb", {}).get("mappings_path", "config/arb_mappings.json")
        self.active_mappings: Dict[str, Dict[VenueType, str]] = {}
        self._load_mappings()

    def _load_mappings(self):
        """Load unified market mappings from JSON config."""
        try:
            if os.path.exists(self.mappings_path):
                with open(self.mappings_path, "r") as f:
                    data = json.load(f)
                    for uid, mapping in data.items():
                        # Convert string keys to VenueType enum
                        enum_mapping = {VenueType(k): v for k, v in mapping.items()}
                        self.adapter.register_market_mapping(uid, enum_mapping)
                        self.active_mappings[uid] = enum_mapping
                logger.info(f"Loaded {len(self.active_mappings)} arb market mappings")
            else:
                logger.warning(f"Arb mappings file not found at {self.mappings_path}")
        except Exception as e:
            logger.error(f"Failed to load arb mappings: {e}")

    async def initialize(self):
        """Connect all venues and prepare for monitoring."""
        results = await self.adapter.connect_all()
        logger.info(f"Venue connection status: {results}")

    async def monitor_step(self):
        """Perform one pass over all mapped markets to detect arbitrage."""
        if not self.active_mappings:
            return

        for unified_id in list(self.active_mappings.keys()):
            try:
                # 1. Fetch Unified Prices
                prices = self.adapter.get_unified_prices(unified_id)

                # 2. Fetch Order Books for liquidity analysis
                books = {}
                for v_type, v_market_id in self.active_mappings[unified_id].items():
                    venue = self.adapter.venues.get(v_type)
                    if venue:
                        book = await venue.get_orderbook(v_market_id)
                        if book:
                            books[v_type] = book

                # 3. Analyze via Engine
                verdict = self.engine.analyze(
                    unified_id=unified_id,
                    prices=prices,
                    books=books,
                    venues_map=self.adapter.venues
                )

                if verdict.is_profitable:
                    logger.info(f"ARB SIGNAL: {unified_id} | Edge: {verdict.net_edge_bps:.1f}bps | Size: {verdict.max_size_usdc}")
                    await self._execute_arb_opportunity(unified_id, verdict)

            except Exception as e:
                logger.error(f"Error monitoring arb for {unified_id}: {e}")

    async def _execute_arb_opportunity(self, unified_id: str, verdict: ArbVerdict):
        """
        Handle the execution and state tracking of an arb opportunity.
        """
        # Determine trade size (capped by engine max and config max)
        max_allowed = float(self.config.get("cross_venue_arb", {}).get("max_trade_size", 50.0))
        trade_size = min(verdict.max_size_usdc, max_allowed)

        # Log Intent to State Manager
        intent_id = f"arb_{unified_id}_{int(datetime.now().timestamp())}"
        intent = {
            "intent_id": intent_id,
            "unified_id": unified_id,
            "buy_venue": verdict.buy_venue.value,
            "sell_venue": verdict.sell_venue.value,
            "buy_price": verdict.buy_price,
            "sell_price": verdict.sell_price,
            "size": trade_size,
            "net_edge_bps": verdict.net_edge_bps,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        # Save to state manager (async thread)
        await asyncio.to_thread(self.state_mgr.create_intent, intent_id, intent)

        try:
            # Execution via Adapter (Sell First, Buy Second)
            buy_res, sell_res = await self.adapter.execute_arb(
                verdict,
                size=trade_size
            )

            # Update State
            result_summary = {
                "buy_status": sell_res.status.value if sell_res else "FAILED",
                "sell_status": buy_res.status.value if buy_res else "FAILED",
                "buy_filled": buy_res.filled_size if buy_res else 0,
                "sell_filled": sell_res.filled_size if sell_res else 0,
                "profit_usd": (sell_res.filled_price - buy_res.filled_price) * buy_res.filled_size if (buy_res and sell_res) else 0,
            }

            await asyncio.to_thread(
                self.state_mgr.update_intent_metadata,
                intent_id,
                {"result": result_summary}
            )

            logger.info(f"Arb execution complete for {unified_id}: {result_summary}")

        except Exception as e:
            logger.error(f"Arb execution failed for {unified_id}: {e}")
            await asyncio.to_thread(
                self.state_mgr.update_intent_metadata,
                intent_id,
                {"error": str(e), "status": "FAILED"}
            )
