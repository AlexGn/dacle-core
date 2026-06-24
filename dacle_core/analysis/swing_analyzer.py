import logging
import asyncio
from typing import Dict, List, Optional, Any
from dacle_core.analysis.market_structure import MarketStructureAnalyzer
from dacle_core.data.fetchers.blofin_fetcher import BlofinFetcher

logger = logging.getLogger(__name__)

class SwingAnalyzer:
    """
    Session 478: Swing-specific Technical Analyzer.
    Focuses on 1d and 1w timeframes for macro conviction.
    """

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self.blofin = BlofinFetcher()
        self.msa = MarketStructureAnalyzer()

    async def analyze_symbol(self, symbol: str) -> Dict[str, Any]:
        """
        Perform macro analysis on 1d and 1w timeframes.
        """
        logger.info(f"SwingAnalyzer: Starting macro analysis for {symbol}")
        
        # 1. Fetch 1d candles
        ohlcv_1d = self.blofin.fetch_ohlcv(symbol, timeframe="1d", limit=100)
        
        # 2. Fetch 1w candles
        ohlcv_1w = self.blofin.fetch_ohlcv(symbol, timeframe="1w", limit=52) # 1 year of weekly data
        
        results = {}
        
        if ohlcv_1d:
            # Calculate 1d RSI
            closes_1d = [c[4] for c in ohlcv_1d]
            results["rsi_1d"] = self._calculate_rsi(closes_1d)
            
            # Analyze structure on 1d
            struct_1d = self.msa.analyze_from_ohlcv(ohlcv_1d, timeframe="1d")
            results["trend_1d"] = struct_1d.get("current_structure", "UNKNOWN")
            results["support_1d"] = struct_1d.get("strong_support")
            
        if ohlcv_1w:
            # Calculate 1w RSI
            closes_1w = [c[4] for c in ohlcv_1w]
            results["rsi_1w"] = self._calculate_rsi(closes_1w)
            
            # Analyze structure on 1w
            struct_1w = self.msa.analyze_from_ohlcv(ohlcv_1w, timeframe="1w")
            results["trend_1w"] = struct_1w.get("current_structure", "UNKNOWN")
            
        return results

    def _calculate_rsi(self, prices: List[float], period: int = 14) -> float:
        """Calculate RSI from close prices."""
        if len(prices) <= period:
            return 50.0
            
        deltas = []
        for i in range(1, len(prices)):
            deltas.append(prices[i] - prices[i-1])
            
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]
        
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        
        if avg_loss == 0:
            return 100.0
            
        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            
        if avg_loss == 0:
            return 100.0
            
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))
