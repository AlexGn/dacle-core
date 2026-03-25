"""
Lighter OBI Bridge - Real-time Microstructure for SMC.
Extracts OBI and spread data from Lighter OrderBook for the Intelligence Engine.
Session 460 Institutional Pivot.
"""
import logging
from typing import Optional, Dict, Any
from src.lighter.order_book import OrderBook

logger = logging.getLogger(__name__)

class LighterOBIBridge:
    def __init__(self, order_book: OrderBook):
        self.book = order_book

    def get_institutional_context(self) -> Dict[str, Any]:
        """
        Extracts current OBI and spread from the order book.
        Returns context for Discovery TA and SMC confirmation.
        """
        obi = self.book.get_imbalance(depth=10)
        
        best_bid = self.book.get_best_bid()
        best_ask = self.book.get_best_ask()
        
        spread_bps = 0.0
        mid_price = 0.0
        
        if best_bid and best_ask:
            mid_price = (best_bid[0] + best_ask[0]) / 2.0
            spread = best_ask[0] - best_bid[0]
            if mid_price > 0:
                spread_bps = (spread / mid_price) * 10000.0
                
        return {
            "obi": round(obi, 3),
            "spread_bps": round(spread_bps, 2),
            "mid_price": mid_price,
            "is_liquid": spread_bps < 100.0 # Standard liquid threshold
        }
