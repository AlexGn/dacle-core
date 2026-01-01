#!/usr/bin/env python3
"""
TA Data Aggregator - Unified Technical Analysis Payload for Agent 4

Canonical location: src/analysis/ta_aggregator.py
Migrated from scripts/helpers/ta_aggregator.py in Session 267.

Aggregates all 21 TA/Macro indicators into a structured JSON payload
for the Execution Reality Check (Agent 4).

Categories:
1. MACRO INDICES (7): BTC/ETH structure, Fear & Greed, Dominance metrics
2. CORE TA (5): RSI 1h/4h, Price vs MA20, Volatility, Current Price
3. ADVANCED TA (9): Order Book, Volume, Funding Rate, OI, Liquidations, NVT, TVL

Session 79K-GEMINI Enhancements:
- Async collection with asyncio.gather() (10-20s → 2-3s)
- TGE-Zero Mode for tokens with no price history
- Fail-safe wrappers for graceful degradation

Author: Claude Code (Session 79K)
Date: 2025-12-01
"""

import asyncio
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, List, Tuple

# Add project root to path for imports when running directly
# Path: src/analysis/ta_aggregator.py -> parent.parent = project root
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Import existing helpers
# Session 267: indices_tracker migrated to src.data.indices_tracker
# price_action_analyzer still at scripts.helpers (deprecation wrapper exists)
try:
    from src.data.indices_tracker import IndicesTracker
    from src.analysis.price_action_analyzer import PriceActionAnalyzer
except ModuleNotFoundError:
    # Fallback for edge cases
    import importlib.util
    spec = importlib.util.spec_from_file_location("indices_tracker", _PROJECT_ROOT / "src/data/indices_tracker.py")
    indices_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(indices_module)
    IndicesTracker = indices_module.IndicesTracker

    spec = importlib.util.spec_from_file_location("price_action_analyzer", _PROJECT_ROOT / "scripts/helpers/price_action_analyzer.py")
    pa_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pa_module)
    PriceActionAnalyzer = pa_module.PriceActionAnalyzer

# Session 264 P2: Redis cache for TA data (4-6x speedup on cache hits)
try:
    from src.utils.redis_cache import get_redis_cache
    REDIS_CACHE_AVAILABLE = True
except ImportError:
    REDIS_CACHE_AVAILABLE = False

logger = logging.getLogger(__name__)


class TADataAggregator:
    """
    Aggregates all 21 TA indicators into structured payload for Agent 4.

    Output format matches Agent 4 expectations in .claude/agents/4-execution-reality-check.md:
    {
        "timestamp": "2025-12-01T12:00:00Z",
        "macro_indices": { ... 7 indicators ... },
        "core_ta": { ... 5 indicators ... },
        "advanced_ta": { ... 7 indicators ... },
        "ta_score_normalized": 7.5,
        "ta_recommendation": "FAVORABLE_FOR_SHORT"
    }

    Session 92: Added macro caching with 15-minute TTL.
    Macro data (BTC/ETH structure, Fear & Greed) changes slowly.
    """

    # Thread pool for parallel sync API calls (async wrapper)
    _executor = ThreadPoolExecutor(max_workers=5)

    def __init__(self, token_symbol: str = None, exchange_id: str = "binance", use_macro_cache: bool = True):
        """
        Initialize aggregator with exchange connection.

        Args:
            token_symbol: Token symbol for token-specific TA (optional, for logging)
            exchange_id: CCXT exchange ID for price data (binance, mexc, gate, bybit)
            use_macro_cache: If True, use cached macro data (15 min TTL) - Session 92
        """
        self.token_symbol = token_symbol
        self.indices_tracker = IndicesTracker(use_cache=use_macro_cache)
        self.price_analyzer = PriceActionAnalyzer(exchange_id=exchange_id)
        self.exchange_id = exchange_id
        self._tge_zero_mode = False  # Flag for tokens with no history
        self.use_macro_cache = use_macro_cache

    def collect_all(self, token_symbol: Optional[str] = None) -> Dict:
        """
        Collect all 21 TA indicators into structured payload.

        Args:
            token_symbol: Optional token symbol for token-specific TA.
                         If None, only macro indicators are collected.

        Returns:
            Dict with all TA data structured for Agent 4
        """
        logger.info(f"Collecting TA data for Agent 4 execution check...")

        # Session 264 P2: Check Redis cache first (4-6x speedup on cache hits)
        if REDIS_CACHE_AVAILABLE:
            try:
                cache = get_redis_cache()
                cache_key = f"{token_symbol or 'macro_only'}"
                cached_ta = cache.get(cache_key, namespace="ta")
                if cached_ta is not None:
                    logger.info(f"✅ Cache HIT for {cache_key} TA data (saved ~2-3s)")
                    return cached_ta
            except Exception as e:
                logger.warning(f"Redis cache check failed for {cache_key}: {e}")

        result = {
            "timestamp": datetime.utcnow().isoformat(),
            "token_symbol": token_symbol,
            "macro_indices": self._collect_macro(),
            "core_ta": self._collect_core_ta(token_symbol),
            "advanced_ta": self._collect_advanced(token_symbol),
        }

        # Calculate aggregate scores
        result["ta_score_normalized"] = self._calculate_score(result)
        result["ta_recommendation"] = self._get_recommendation(result)

        logger.info(f"TA Data collected: score={result['ta_score_normalized']:.1f}/10, "
                   f"recommendation={result['ta_recommendation']}")

        # Session 264 P2: Cache the result in Redis (15-min TTL, TA data changes slowly)
        if REDIS_CACHE_AVAILABLE:
            try:
                cache = get_redis_cache()
                cache_key = f"{token_symbol or 'macro_only'}"
                cache.set(cache_key, result, ttl_seconds=900, namespace="ta")  # 15 min TTL
                logger.debug(f"✅ Cached TA data for {cache_key} (TTL: 15min)")
            except Exception as e:
                logger.warning(f"Failed to cache TA data for {cache_key}: {e}")

        return result

    def get_btc_market_structure(self) -> Optional[str]:
        """
        Session 102 (Gemini P0): Get ONLY BTC market structure for macro context.

        This is a fast method (~0.5s) used by Stage 2 conviction scoring.
        Per Gemini review, conviction should be fundamental-only with just BTC trend
        for macro context (5% weight). Full TA moved to Stage 3 Playbook.

        Returns:
            "uptrend", "downtrend", or "sideways" (None on error)
        """
        try:
            btc_structure = self.price_analyzer._analyze_btc()
            logger.info(f"[BTC-ONLY] Market structure: {btc_structure}")
            return btc_structure
        except Exception as e:
            logger.error(f"[BTC-ONLY] Failed to get BTC structure: {e}")
            return None

    def btc_precheck(self) -> Dict:
        """
        Session 243: Mandatory BTC analysis before any altcoin signal (Learning 031).

        Per Sherlock methodology: Trading altcoins without checking BTC correlation
        is like sailing without checking weather.

        This is the FIRST GATE in the signal pipeline - must pass before any alt trade.

        Returns:
            {
                "btc_trend": "uptrend" | "downtrend" | "sideways",
                "btc_key_levels": {
                    "current_price": float,
                    "nearest_support": float,
                    "nearest_resistance": float,
                    "distance_to_support_pct": float,
                    "distance_to_resistance_pct": float
                },
                "btc_risk_assessment": "LOW" | "MODERATE" | "HIGH" | "CRITICAL",
                "alt_trade_recommendation": "PROCEED" | "CAUTION" | "AVOID" | "STOP",
                "position_size_multiplier": 0.0 | 0.25 | 0.5 | 0.75 | 1.0,
                "leverage_allowed": bool,
                "warning_message": str | None,
                "veto_active": bool  # Session 246: Gemini CRITICAL VETO flag
            }
        """
        result = {
            "btc_trend": "unknown",
            "btc_key_levels": {
                "current_price": None,
                "nearest_support": None,
                "nearest_resistance": None,
                "distance_to_support_pct": None,
                "distance_to_resistance_pct": None
            },
            "btc_risk_assessment": "UNKNOWN",
            "alt_trade_recommendation": "CAUTION",
            "position_size_multiplier": 0.5,
            "leverage_allowed": False,
            "warning_message": None,
            "veto_active": False  # Session 246: Gemini CRITICAL VETO
        }

        try:
            # Get BTC market structure
            btc_structure = self.price_analyzer._analyze_btc()
            result["btc_trend"] = btc_structure or "unknown"

            # Get BTC price data for key levels
            try:
                btc_ohlcv = self.price_analyzer._fetch_ohlcv("BTC", "4h", limit=50)
                if btc_ohlcv:
                    current_price = btc_ohlcv[-1][4]  # Close price
                    result["btc_key_levels"]["current_price"] = current_price

                    # Calculate simple S/R from recent highs/lows
                    highs = [c[2] for c in btc_ohlcv]
                    lows = [c[3] for c in btc_ohlcv]

                    # Find nearest resistance (highest high above current)
                    resistances = [h for h in highs if h > current_price]
                    if resistances:
                        nearest_resistance = min(resistances)
                        result["btc_key_levels"]["nearest_resistance"] = nearest_resistance
                        result["btc_key_levels"]["distance_to_resistance_pct"] = round(
                            ((nearest_resistance - current_price) / current_price) * 100, 2
                        )

                    # Find nearest support (lowest low below current)
                    supports = [l for l in lows if l < current_price]
                    if supports:
                        nearest_support = max(supports)
                        result["btc_key_levels"]["nearest_support"] = nearest_support
                        result["btc_key_levels"]["distance_to_support_pct"] = round(
                            ((current_price - nearest_support) / current_price) * 100, 2
                        )
            except Exception as e:
                logger.debug(f"BTC key levels calculation failed: {e}")

            # Determine risk assessment based on trend and position
            dist_to_support = result["btc_key_levels"].get("distance_to_support_pct")
            dist_to_resistance = result["btc_key_levels"].get("distance_to_resistance_pct")

            if btc_structure == "uptrend":
                # Uptrend: Check if near resistance (potential pullback)
                if dist_to_resistance and dist_to_resistance < 2:
                    result["btc_risk_assessment"] = "MODERATE"
                    result["alt_trade_recommendation"] = "CAUTION"
                    result["position_size_multiplier"] = 0.75
                    result["leverage_allowed"] = True
                    result["warning_message"] = "BTC near resistance - potential pullback"
                else:
                    result["btc_risk_assessment"] = "LOW"
                    result["alt_trade_recommendation"] = "PROCEED"
                    result["position_size_multiplier"] = 1.0
                    result["leverage_allowed"] = True

            elif btc_structure == "downtrend":
                # Downtrend: HIGH risk for alt longs, GOOD for alt shorts
                result["btc_risk_assessment"] = "HIGH"
                result["alt_trade_recommendation"] = "AVOID"  # For longs
                result["position_size_multiplier"] = 0.25
                result["leverage_allowed"] = False
                result["warning_message"] = "BTC downtrend - avoid alt longs, shorts may be favorable"

            elif btc_structure == "sideways":
                # Sideways: Moderate risk, reduced size
                result["btc_risk_assessment"] = "MODERATE"
                result["alt_trade_recommendation"] = "CAUTION"
                result["position_size_multiplier"] = 0.5
                result["leverage_allowed"] = False
                result["warning_message"] = "BTC sideways - use spot only, prepare for volatility"

            else:
                # Unknown structure
                result["btc_risk_assessment"] = "HIGH"
                result["alt_trade_recommendation"] = "CAUTION"
                result["position_size_multiplier"] = 0.5
                result["leverage_allowed"] = False
                result["warning_message"] = "BTC structure unclear - proceed with caution"

            # Session 265: Sherlock Macro Sentiment Integration
            # Adjust position_size_multiplier based on Sherlock's recent performance
            # Sherlock = LONG trader (93.8%), inverse correlation with TGE shorts
            try:
                from src.conviction.sherlock_macro_filter import get_sherlock_macro_sentiment
                sherlock_sentiment = get_sherlock_macro_sentiment()

                # Store Sherlock context for downstream use
                result["sherlock_macro_sentiment"] = sherlock_sentiment['sentiment_score']
                result["sherlock_win_rate"] = sherlock_sentiment['win_rate']
                result["sherlock_active_quad_longs"] = sherlock_sentiment['active_quad_longs']
                result["sherlock_recommendation"] = sherlock_sentiment['recommendation']

                # Adjust position_size_multiplier based on Sherlock recommendation
                # Combine BTC + Sherlock multipliers
                base_multiplier = result["position_size_multiplier"]
                sherlock_multiplier = sherlock_sentiment['position_multiplier']

                # Combined multiplier (multiplicative, not additive)
                # Example: BTC=0.75, Sherlock=0.5 → Combined=0.375 (37.5% size)
                result["position_size_multiplier"] = base_multiplier * sherlock_multiplier
                result["position_size_multiplier_breakdown"] = {
                    "btc_multiplier": base_multiplier,
                    "sherlock_multiplier": sherlock_multiplier,
                    "combined": result["position_size_multiplier"]
                }

                logger.info(f"[SHERLOCK-FILTER] Sentiment: {sherlock_sentiment['sentiment_score']}/100 "
                           f"(WIN: {sherlock_sentiment['win_rate']:.1%}, QUAD: {sherlock_sentiment['active_quad_longs']}) "
                           f"→ {sherlock_sentiment['recommendation']} (×{sherlock_multiplier})")

            except Exception as e:
                logger.warning(f"[SHERLOCK-FILTER] Failed to load Sherlock sentiment: {e}")
                result["sherlock_macro_sentiment"] = None
                result["sherlock_recommendation"] = "UNAVAILABLE"

            # Session 246: CRITICAL state for flash crash conditions (Gemini P2 VETO)
            # CRITICAL = force SKIP all alt trades, VETO any signals
            # Conditions for CRITICAL:
            # 1. BTC downtrend + near support (< 3%) = potential breakdown
            # 2. BTC dropped > 5% in last 4H = active flash crash
            # 3. Distance to support is very small (< 1%) = imminent breakdown

            is_critical = False
            critical_reasons = []

            # Check for downtrend + near support breakdown risk
            if btc_structure == "downtrend" and dist_to_support and dist_to_support < 3:
                is_critical = True
                critical_reasons.append(f"BTC downtrend near support ({dist_to_support:.1f}% away)")

            # Check for imminent breakdown (very close to support)
            if dist_to_support and dist_to_support < 1:
                is_critical = True
                critical_reasons.append(f"BTC at critical support ({dist_to_support:.1f}% away)")

            # Check for active flash crash (large recent move)
            try:
                if btc_ohlcv and len(btc_ohlcv) >= 2:
                    current_close = btc_ohlcv[-1][4]
                    prev_close = btc_ohlcv[-2][4]
                    pct_change = ((current_close - prev_close) / prev_close) * 100
                    if pct_change < -5:  # > 5% drop in 4H = flash crash
                        is_critical = True
                        critical_reasons.append(f"BTC crashed {pct_change:.1f}% in last 4H")
            except Exception:
                pass  # Silently fail - don't break on this check

            if is_critical:
                result["btc_risk_assessment"] = "CRITICAL"
                result["alt_trade_recommendation"] = "STOP"
                result["position_size_multiplier"] = 0.0
                result["leverage_allowed"] = False
                result["veto_active"] = True
                result["warning_message"] = f"🚨 BTC CRITICAL - VETO ALL ALT TRADES: {'; '.join(critical_reasons)}"

            veto_str = " | 🚨 VETO ACTIVE" if result.get('veto_active') else ""
            logger.info(f"[BTC-PRECHECK] Trend: {result['btc_trend']} | "
                       f"Risk: {result['btc_risk_assessment']} | "
                       f"Alt Rec: {result['alt_trade_recommendation']} | "
                       f"Size: {result['position_size_multiplier']*100:.0f}%{veto_str}")

            return result

        except Exception as e:
            logger.error(f"[BTC-PRECHECK] Failed: {e}")
            result["warning_message"] = f"BTC pre-check failed: {e}"
            return result

    def get_market_range_context(self) -> Dict:
        """
        Session 255: L039 - Calculate BTC/ETH weekly range for BE SL buffer decisions.

        In sideways markets (BTC <5% weekly range), BE SL gets hunted by manipulation
        wicks. This method provides the data needed to decide on BE SL buffer size.

        Returns:
            {
                "btc_weekly_range_pct": float,  # BTC high-low range as % of low
                "eth_weekly_range_pct": float,  # ETH high-low range as % of low
                "market_condition": "trending" | "mild_trend" | "sideways" | "choppy",
                "be_sl_strategy": str,  # Recommended BE SL approach
                "be_sl_buffer_pct": float,  # Recommended buffer (0 = exact BE)
                "reasoning": str
            }
        """
        result = {
            "btc_weekly_range_pct": None,
            "eth_weekly_range_pct": None,
            "market_condition": "unknown",
            "be_sl_strategy": "standard",
            "be_sl_buffer_pct": 0.0,
            "reasoning": "Unable to calculate market range"
        }

        try:
            # Get BTC weekly data (7 days * 6 = 42 4H candles)
            btc_ohlcv = self.price_analyzer._fetch_ohlcv("BTC", "4h", limit=42)
            if btc_ohlcv and len(btc_ohlcv) >= 42:
                btc_highs = [c[2] for c in btc_ohlcv]
                btc_lows = [c[3] for c in btc_ohlcv]
                btc_high = max(btc_highs)
                btc_low = min(btc_lows)
                btc_range = ((btc_high - btc_low) / btc_low) * 100
                result["btc_weekly_range_pct"] = round(btc_range, 2)

            # Get ETH weekly data
            eth_ohlcv = self.price_analyzer._fetch_ohlcv("ETH", "4h", limit=42)
            if eth_ohlcv and len(eth_ohlcv) >= 42:
                eth_highs = [c[2] for c in eth_ohlcv]
                eth_lows = [c[3] for c in eth_ohlcv]
                eth_high = max(eth_highs)
                eth_low = min(eth_lows)
                eth_range = ((eth_high - eth_low) / eth_low) * 100
                result["eth_weekly_range_pct"] = round(eth_range, 2)

            # Determine market condition based on BTC range (primary indicator)
            btc_range = result["btc_weekly_range_pct"]
            if btc_range is not None:
                if btc_range > 10:
                    result["market_condition"] = "trending"
                    result["be_sl_strategy"] = "Move to BE after TP1 (standard L034)"
                    result["be_sl_buffer_pct"] = 0.0
                    result["reasoning"] = f"BTC range {btc_range:.1f}% > 10% = trending market, standard BE SL is safe"
                elif btc_range > 5:
                    result["market_condition"] = "mild_trend"
                    result["be_sl_strategy"] = "BE + 1-2% buffer recommended"
                    result["be_sl_buffer_pct"] = 1.5
                    result["reasoning"] = f"BTC range {btc_range:.1f}% (5-10%) = mild trend, add small buffer to avoid wicks"
                else:
                    # < 5% = sideways - L039 applies
                    result["market_condition"] = "sideways"
                    result["be_sl_strategy"] = "BE + 3-5% buffer OR wait for TP2 before moving SL"
                    result["be_sl_buffer_pct"] = 4.0
                    result["reasoning"] = f"⚠️ L039 ACTIVE: BTC range {btc_range:.1f}% < 5% = SIDEWAYS. Manipulation wicks will hunt tight stops. Add 3-5% buffer to BE SL or wait for TP2."

            logger.info(f"[L039-RANGE] BTC: {result['btc_weekly_range_pct']}% | "
                       f"ETH: {result['eth_weekly_range_pct']}% | "
                       f"Condition: {result['market_condition']} | "
                       f"Buffer: {result['be_sl_buffer_pct']}%")

            return result

        except Exception as e:
            logger.error(f"[L039-RANGE] Failed to calculate market range: {e}")
            result["reasoning"] = f"Error calculating range: {e}"
            return result

    def collect_all_fast(self, token_symbol: Optional[str] = None) -> Dict:
        """
        FAST version: Collect all 21 TA indicators in PARALLEL.

        Session 79K-GEMINI: Uses ThreadPoolExecutor to run all API calls
        concurrently, reducing total time from 10-20s to 2-3s.

        Args:
            token_symbol: Optional token symbol for token-specific TA.

        Returns:
            Dict with all TA data structured for Agent 4
        """
        start_time = time.time()
        logger.info(f"[FAST] Collecting TA data in parallel for {token_symbol or 'macro only'}...")

        # Check for TGE-Zero mode (new token with no history)
        if token_symbol and self._check_tge_zero_mode(token_symbol):
            logger.warning(f"[TGE-ZERO] {token_symbol} has no price history, using TGE-Zero mode")
            return self._collect_tge_zero_mode(token_symbol)

        # Run all collection methods in parallel using ThreadPoolExecutor
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            macro_future = loop.run_in_executor(self._executor, self._collect_macro)
            core_future = loop.run_in_executor(self._executor, self._collect_core_ta, token_symbol)
            advanced_future = loop.run_in_executor(self._executor, self._collect_advanced, token_symbol)

            # Wait for all to complete
            macro, core, advanced = loop.run_until_complete(
                asyncio.gather(macro_future, core_future, advanced_future)
            )
        finally:
            loop.close()

        elapsed = time.time() - start_time
        logger.info(f"[FAST] Parallel collection completed in {elapsed:.2f}s")

        # Session 92: Track if macro came from cache
        macro_from_cache = macro.get('_from_cache', False)

        result = {
            "timestamp": datetime.utcnow().isoformat(),
            "token_symbol": token_symbol,
            "collection_mode": "parallel",
            "collection_time_sec": round(elapsed, 2),
            "macro_from_cache": macro_from_cache,
            "macro_cache_age_minutes": macro.get('_cache_age_minutes'),
            "macro_indices": macro,
            "core_ta": core,
            "advanced_ta": advanced,
        }

        result["ta_score_normalized"] = self._calculate_score(result)
        result["ta_recommendation"] = self._get_recommendation(result)

        if macro_from_cache:
            logger.info(f"[FAST] Macro from cache ({macro.get('_cache_age_minutes', '?')}m old)")

        return result

    def _check_tge_zero_mode(self, token_symbol: str) -> bool:
        """
        Check if token has price history (for TGE-Zero detection).

        Returns True if token is new (< 5 minutes of data).
        """
        try:
            ohlcv = self.price_analyzer._fetch_ohlcv(token_symbol, '1m', limit=5)
            if not ohlcv or len(ohlcv) < 3:
                return True
            return False
        except Exception:
            # If we can't fetch data, assume TGE-Zero mode
            return True

    def _collect_tge_zero_mode(self, token_symbol: str) -> Dict:
        """
        TGE-Zero Mode: Collect TA for tokens with NO price history.

        At TGE T+0, RSI/MA/Order Book are empty. Instead we use:
        1. BTC/ETH macro as proxy
        2. Fear & Greed (market-wide)
        3. Order Book Imbalance (if available)
        4. Volume Velocity (if available)

        Returns conservative defaults where data is unavailable.
        """
        logger.info(f"[TGE-ZERO] Collecting zero-history TA for {token_symbol}...")

        # Macro is still valid (BTC/ETH proxy)
        macro = self._collect_macro_safe()

        # Core TA defaults for new token
        core = {
            "rsi_1h": None,
            "rsi_4h": None,
            "rsi_signal": "N/A_TGE_ZERO",
            "price_vs_ma20": "N/A_TGE_ZERO",
            "volatility_24h": None,
            "current_price": None,
            "trend": "N/A_TGE_ZERO",
            "structure": "N/A_TGE_ZERO",
            "tge_zero_mode": True
        }

        # Try to get order book even for new tokens
        advanced = self._collect_advanced_tge_zero(token_symbol)

        result = {
            "timestamp": datetime.utcnow().isoformat(),
            "token_symbol": token_symbol,
            "collection_mode": "tge_zero",
            "tge_zero_mode": True,
            "macro_indices": macro,
            "core_ta": core,
            "advanced_ta": advanced,
        }

        # Conservative scoring for TGE-Zero (rely heavily on macro)
        result["ta_score_normalized"] = self._calculate_tge_zero_score(macro)
        result["ta_recommendation"] = self._get_recommendation(result)

        return result

    def _collect_macro_safe(self) -> Dict:
        """Fail-safe macro collection with defaults on error."""
        try:
            return self._collect_macro()
        except Exception as e:
            logger.error(f"[FAIL-SAFE] Macro collection failed: {e}")
            return self._empty_macro()

    def _collect_advanced_tge_zero(self, token_symbol: str) -> Dict:
        """
        Collect what advanced data we can for a TGE-Zero token.

        Focus on:
        - Order Book (if trading has started)
        - BTC/ETH funding/OI as proxy
        """
        result = self._empty_advanced()
        result["tge_zero_mode"] = True

        try:
            # Try order book for the new token
            from scripts.analysis.analyze_oi_orderbook import OIOrderBookAnalyzer
            oi_analyzer = OIOrderBookAnalyzer()
            orderbook = oi_analyzer.get_order_book_depth(token_symbol, "binance", limit=100)

            if orderbook and orderbook.get('bid_ask_ratio'):
                ratio = orderbook['bid_ask_ratio']
                imbalance = (ratio - 1.0) / (ratio + 1.0)
                result["order_book_imbalance"] = round(imbalance, 3)
                if ratio > 1.5:
                    result["order_book_signal"] = "BUY_PRESSURE"
                elif ratio < 0.67:
                    result["order_book_signal"] = "SELL_PRESSURE"
                else:
                    result["order_book_signal"] = "NEUTRAL"
        except Exception:
            pass  # Keep defaults

        # Use BTC funding/OI as proxy for market conditions
        try:
            from src.risk.liquidation_tracker import LiquidationTracker
            liq_tracker = LiquidationTracker()
            btc_funding = liq_tracker.get_funding_rate("BTC")
            result["proxy_btc_funding"] = btc_funding.get('funding_rate')
            result["proxy_btc_funding_signal"] = btc_funding.get('funding_signal', 'UNKNOWN')
        except Exception:
            pass

        return result

    def _calculate_tge_zero_score(self, macro: Dict) -> float:
        """
        Calculate TA score in TGE-Zero mode (macro only).

        Conservative scoring - max 6.0/10 since we're missing token-specific data.
        """
        score = 0.0

        # MACRO scoring (capped at 6.0 for TGE-Zero)
        if macro.get('btc_market_structure') == 'downtrend':
            score += 2.0
        elif macro.get('btc_market_structure') == 'sideways':
            score += 0.5

        if macro.get('eth_market_structure') == 'downtrend':
            score += 1.0

        fear_greed = macro.get('fear_greed_index')
        if fear_greed and fear_greed >= 70:
            score += 2.0
        elif fear_greed and fear_greed >= 50:
            score += 1.0
        elif fear_greed and fear_greed <= 25:
            score += 1.5  # Extreme fear can also favor shorts

        return round(min(score, 6.0), 1)

    def _collect_macro(self) -> Dict:
        """
        Collect MACRO INDICES (7 indicators).

        Uses IndicesTracker + PriceActionAnalyzer for:
        1. BTC Market Structure (uptrend/downtrend/sideways)
        2. ETH Market Structure
        3. Fear & Greed Index (0-100)
        4. USDT Dominance (%)
        5. BTC Dominance (%)
        6. Total Market Cap ($)
        7. Altcoin Market Cap ($)
        """
        logger.info("   Collecting macro indices (7 indicators)...")

        try:
            # Fetch all indices from IndicesTracker
            indices_data = self.indices_tracker.fetch_all_indices()
            indices = indices_data.get('indices', {})

            # Analyze BTC/ETH market structure
            btc_structure = self.price_analyzer._analyze_btc()
            eth_structure = self.price_analyzer._analyze_eth()

            # Extract individual indices
            fear_greed = indices.get('fear_greed_index', {})
            usdt_d = indices.get('usdt_d', {})
            btc_d = indices.get('btc_d', {})
            total = indices.get('total', {})
            total3 = indices.get('total3', {})  # Altcoin MC (excl BTC+ETH)

            # Get real-time sentiment data (Learning 012)
            realtime_sentiment = indices.get('realtime_sentiment', {})
            btc_24h_change = indices.get('btc_24h_change', 0)
            eth_24h_change = indices.get('eth_24h_change', 0)
            total3_24h_change = indices.get('total3_24h_change', 0)

            # Learning 021: BTCDOM trend for alt timing (Sherlock's insight)
            btc_d_trend = self.indices_tracker.fetch_btc_d_trend()
            btcdom_total3_confluence = self.indices_tracker.fetch_btcdom_total3_confluence()

            return {
                # Market structure (from price analyzer)
                "btc_market_structure": btc_structure,
                "eth_market_structure": eth_structure,

                # Fear & Greed
                "fear_greed_index": fear_greed.get('value'),
                "fear_greed_label": fear_greed.get('signal', 'UNKNOWN'),

                # Dominance metrics
                "usdt_dominance": usdt_d.get('value'),
                "usdt_signal": usdt_d.get('signal', 'NEUTRAL'),
                "btc_dominance": btc_d.get('value'),
                "btc_dominance_signal": btc_d.get('signal', 'NEUTRAL'),

                # Market caps
                "total_market_cap": total.get('value'),
                "altcoin_market_cap": total3.get('value'),  # TOTAL3 = excl BTC+ETH

                # Macro signal (aggregated)
                "macro_signal": indices_data.get('macro_signal', 'UNKNOWN'),

                # Real-time data (Learning 012)
                "realtime_sentiment": realtime_sentiment,
                "btc_24h_change": btc_24h_change,
                "eth_24h_change": eth_24h_change,
                "total3_24h_change": total3_24h_change,
                "data_freshness": indices_data.get('data_freshness', {}),

                # Learning 021: BTCDOM trend for alt timing (Sherlock's insight)
                "btc_d_trend": btc_d_trend,
                "btcdom_total3_confluence": btcdom_total3_confluence,
            }

        except Exception as e:
            logger.error(f"   Failed to collect macro indices: {e}")
            return self._empty_macro()

    def _collect_core_ta(self, token_symbol: Optional[str]) -> Dict:
        """
        Collect CORE TA indicators (5 indicators).

        Token-specific TA using PriceActionAnalyzer:
        1. RSI 1h
        2. RSI 4h
        3. Price vs MA20
        4. Volatility (24h)
        5. Current Price
        """
        logger.info(f"   Collecting core TA (5 indicators) for {token_symbol or 'macro only'}...")

        if not token_symbol:
            return self._empty_core_ta()

        try:
            # Use price analyzer for token-specific TA
            analysis = self.price_analyzer.analyze(token_symbol, analysis_type="SHORT")

            # Calculate price vs MA20 position
            price_vs_ma = "unknown"
            if 'trend' in analysis:
                # If in uptrend, typically above MA20
                if analysis['trend'] == 'uptrend':
                    price_vs_ma = 'above'
                elif analysis['trend'] == 'downtrend':
                    price_vs_ma = 'below'
                else:
                    price_vs_ma = 'at'

            # Calculate volatility (simplified - based on RSI spread)
            rsi_1h = analysis.get('rsi_1h', 50)
            rsi_4h = analysis.get('rsi_4h', 50) or 50
            volatility = abs(rsi_1h - rsi_4h)  # Simplified volatility proxy

            # Determine RSI signal
            rsi_signal = "NEUTRAL"
            if rsi_1h >= 70:
                rsi_signal = "OVERBOUGHT"
            elif rsi_1h <= 30:
                rsi_signal = "OVERSOLD"

            return {
                "rsi_1h": rsi_1h,
                "rsi_4h": rsi_4h,
                "rsi_signal": rsi_signal,
                "price_vs_ma20": price_vs_ma,
                "volatility_24h": round(volatility, 2),
                "current_price": analysis.get('current_price'),
                "trend": analysis.get('trend', 'unknown'),
                "structure": analysis.get('structure', 'unknown')
            }

        except Exception as e:
            logger.error(f"   Failed to collect core TA: {e}")
            return self._empty_core_ta()

    def _collect_advanced(self, token_symbol: Optional[str]) -> Dict:
        """
        Collect ADVANCED TA indicators (7+ indicators).
        Session 103 Gemini: Added Crowded Trade Detection & VWAP.
        Session 104: Added CEX/DEX Delta, Funding Velocity, OTC Volume Trend.
        Session 209: Added Candlestick Pattern Detection (Learning 022).
        Session 230: Added Support/Resistance Detection (Learning 023).
        Session 234: Added NVT Ratio and TVL Tracking (Learning 026).

        1. Order Book Imbalance
        2. Volume 24h
        3. Funding Rate + Velocity
        4. Open Interest
        5. Long Liquidations 24h
        6. Short Liquidations 24h
        7. Liquidation Cascade Fuel
        8. VWAP Price & Signal (Session 103)
        9. Crowded Trade Signal (Session 103)
        10. CEX/DEX Price Delta (Session 104)
        11. OTC Volume Trend (Session 104)
        12. Candlestick Patterns (Session 209 - Learning 022)
        13. Support/Resistance Analysis (Session 230 - Learning 023)
        14. NVT Ratio (Session 234 - Learning 026)
        15. TVL Trend (Session 234 - Learning 026)
        """
        logger.info(f"   Collecting advanced TA (7+ indicators) for {token_symbol or 'macro only'}...")

        if not token_symbol:
            return self._empty_advanced()

        try:
            # Import OI/Orderbook analyzer
            from scripts.analysis.analyze_oi_orderbook import OIOrderBookAnalyzer
            oi_analyzer = OIOrderBookAnalyzer()

            # Import LiquidationTracker for funding rate, OI change, and liquidations
            try:
                from src.risk.liquidation_tracker import LiquidationTracker
                liq_tracker = LiquidationTracker()
                liq_tracker_available = True
            except ImportError:
                liq_tracker_available = False

            # Fetch Order Book data
            orderbook = oi_analyzer.get_order_book_depth(token_symbol, "binance", limit=500)

            # Calculate order book imbalance
            imbalance = 0.0
            ob_signal = "NEUTRAL"
            if orderbook and orderbook.get('bid_ask_ratio'):
                ratio = orderbook['bid_ask_ratio']
                imbalance = (ratio - 1.0) / (ratio + 1.0)  # Normalize to -1 to 1
                if ratio > 1.5:
                    ob_signal = "BUY_PRESSURE"
                elif ratio < 0.67:
                    ob_signal = "SELL_PRESSURE"

            # Initialize defaults
            funding_rate = None
            funding_signal = "UNKNOWN"
            funding_trend = "unknown"
            funding_velocity = None  # Session 104: Rate of change
            oi_value = None
            oi_change = None
            oi_trend = "unknown"
            volume_24h = None
            volume_trend = "unknown"
            long_liqs = None
            short_liqs = None
            cascade_fuel = "UNKNOWN"
            cex_dex_delta = None  # Session 104: CEX vs DEX price diff
            cex_dex_signal = "UNKNOWN"
            otc_volume_trend = None  # Session 104: Pre-TGE OTC volume trend
            otc_trend_signal = "UNKNOWN"
            nvt_ratio = None  # Session 234: NVT Ratio (Learning 026)
            nvt_signal = "UNKNOWN"
            nvt_score_adj = 0.0
            tvl_trend_pct = None  # Session 234: TVL Trend (Learning 026)
            tvl_signal = "UNKNOWN"
            tvl_score_adj = 0.0

            # Use LiquidationTracker for enhanced data
            if liq_tracker_available:
                # Funding Rate with trend analysis + velocity
                funding_data = liq_tracker.get_funding_rate(token_symbol)
                if funding_data.get('funding_rate') is not None:
                    funding_rate = funding_data['funding_rate']
                    funding_signal = funding_data.get('funding_signal', 'UNKNOWN')
                    funding_trend = funding_data.get('trend', 'unknown')
                    # Session 104: Funding velocity (rate of change)
                    # Previous funding from 8h ago if available
                    prev_funding = funding_data.get('previous_funding_rate')
                    if prev_funding is not None and funding_rate is not None:
                        funding_velocity = (funding_rate - prev_funding) / max(abs(prev_funding), 0.0001)
                        # Velocity > 50% = accelerating, < -50% = decelerating

                # OI with 24h change
                oi_data = liq_tracker.get_open_interest_change(token_symbol)
                if oi_data.get('open_interest') is not None:
                    oi_value = oi_data['open_interest']
                    oi_change = oi_data.get('oi_change_24h_pct')
                    oi_trend = oi_data.get('trend', 'unknown')

                # Liquidations (may require API key)
                liq_data = liq_tracker.get_liquidations_24h(token_symbol)
                long_liqs = liq_data.get('long_liquidations_24h')
                short_liqs = liq_data.get('short_liquidations_24h')

                # Cascade risk calculation
                cascade_data = liq_tracker.calculate_cascade_risk(funding_data, oi_data, liq_data)
                cascade_fuel = cascade_data.get('risk_level', 'UNKNOWN')
            else:
                # Fallback to basic OI from OIOrderBookAnalyzer
                oi_binance = oi_analyzer.get_binance_open_interest(token_symbol)
                oi_value = oi_binance['open_interest'] if oi_binance else None

            # Volume from OHLCV (basic - last 24h candles)
            # Session 103 Gemini: Also calculate VWAP from OHLCV
            vwap_price = None
            vwap_signal = "UNKNOWN"
            current_close = None

            try:
                ohlcv = self.price_analyzer._fetch_ohlcv(token_symbol, '1h', limit=24)
                if ohlcv:
                    volume_24h = sum([candle[5] for candle in ohlcv])  # Volume is index 5
                    # Compare first half vs second half for trend
                    first_half_vol = sum([candle[5] for candle in ohlcv[:12]])
                    second_half_vol = sum([candle[5] for candle in ohlcv[12:]])
                    if second_half_vol > first_half_vol * 1.2:
                        volume_trend = "increasing"
                    elif second_half_vol < first_half_vol * 0.8:
                        volume_trend = "decreasing"
                    else:
                        volume_trend = "stable"

                    # Session 103 Gemini: Calculate VWAP
                    # VWAP = Sum(Volume * Typical Price) / Sum(Volume)
                    # Typical Price = (High + Low + Close) / 3
                    cum_vol_price = 0
                    cum_vol = 0
                    current_close = ohlcv[-1][4]  # Close is index 4

                    for candle in ohlcv:
                        high = candle[2]
                        low = candle[3]
                        close = candle[4]
                        vol = candle[5]
                        typical_price = (high + low + close) / 3
                        cum_vol_price += (typical_price * vol)
                        cum_vol += vol

                    if cum_vol > 0:
                        vwap_price = cum_vol_price / cum_vol
                        # VWAP signal: Above VWAP = bullish/bounce, Below VWAP = bearish
                        if current_close > vwap_price:
                            vwap_signal = "ABOVE_VWAP"  # Bullish - caution for shorts
                        else:
                            vwap_signal = "BELOW_VWAP"  # Bearish - good for shorts

            except Exception:
                pass  # Keep defaults

            # Session 243: Extended VWAP Calculation (Learning 028 - Sherlock Methodology)
            # Quarterly and Yearly VWAPs for institutional-grade S/R levels
            vwap_weekly = None
            vwap_monthly = None
            vwap_quarterly = None
            vwap_yearly = None
            vwap_confluence_score = 0
            vwap_confluence_signal = "UNKNOWN"

            try:
                extended_vwaps = self._calculate_extended_vwaps(token_symbol)
                if extended_vwaps:
                    vwap_weekly = extended_vwaps.get('vwap_weekly')
                    vwap_monthly = extended_vwaps.get('vwap_monthly')
                    vwap_quarterly = extended_vwaps.get('vwap_quarterly')
                    vwap_yearly = extended_vwaps.get('vwap_yearly')
                    vwap_confluence_score = extended_vwaps.get('confluence_score', 0)
                    vwap_confluence_signal = extended_vwaps.get('confluence_signal', 'UNKNOWN')

                    if vwap_quarterly or vwap_yearly:
                        logger.info(f"   [VWAP-EXT] Q: ${vwap_quarterly:.6f if vwap_quarterly else 'N/A'} | "
                                  f"Y: ${vwap_yearly:.6f if vwap_yearly else 'N/A'} | "
                                  f"Confluence: {vwap_confluence_signal} ({vwap_confluence_score}/5)")
            except Exception as e:
                logger.debug(f"   Extended VWAP calculation failed: {e}")

            # Session 103 Gemini: Crowded Trade Detection
            # High Risk: Negative Funding + Rising OI = Squeeze imminent
            crowded_signal = "NEUTRAL"
            if funding_rate is not None and oi_change is not None:
                if funding_rate < -0.02 and oi_change > 5.0:
                    # Shorts piling in while funding is negative = squeeze risk
                    crowded_signal = "SQUEEZE_RISK_HIGH"
                elif funding_rate < 0 and oi_change < -10.0:
                    # Shorts closing (covering), price likely to bounce
                    crowded_signal = "SHORT_COVERING"
                elif funding_rate > 0.05 and oi_change > 5.0:
                    # Longs piling in = long squeeze potential (good for shorts)
                    crowded_signal = "LONG_CROWDED"

            # Session 104: CEX vs DEX Price Delta Detection
            # DEX price often leads CEX by minutes during dumps
            # Negative delta (DEX < CEX) = dump incoming on CEX
            try:
                # Get CEX price (already have current_close from OHLCV)
                cex_price = current_close
                if cex_price and cex_price > 0:
                    # Try to get DEX price from DexScreener or Uniswap
                    dex_price = self._fetch_dex_price(token_symbol)
                    if dex_price and dex_price > 0:
                        cex_dex_delta = ((dex_price - cex_price) / cex_price) * 100
                        # Signal interpretation:
                        # DEX < CEX by >1% = DUMP_LEAD (DEX dumping first)
                        # DEX > CEX by >1% = PUMP_LEAD (DEX pumping first)
                        if cex_dex_delta < -1.0:
                            cex_dex_signal = "DUMP_LEAD"  # DEX leading dump - BEARISH
                        elif cex_dex_delta > 1.0:
                            cex_dex_signal = "PUMP_LEAD"  # DEX leading pump - BULLISH
                        else:
                            cex_dex_signal = "SYNCED"  # Prices aligned
            except Exception:
                pass  # Keep defaults

            # Session 104: OTC Volume Trend (pre-TGE fading interest detection)
            # MET pattern: -62.5% volume over 7 days → -60% dump at TGE
            try:
                otc_trend_data = self._fetch_otc_volume_trend(token_symbol)
                if otc_trend_data:
                    otc_volume_trend = otc_trend_data.get('trend_pct')
                    otc_trend_signal = otc_trend_data.get('signal', 'UNKNOWN')
            except Exception:
                pass  # Keep defaults

            # Session 209: Candlestick Pattern Detection (Learning 022)
            # Detects 14 patterns for SHORT entry/exit timing
            candlestick_signal = "UNKNOWN"
            candlestick_patterns = []
            candlestick_score_adj = 0.0
            candlestick_recommendation = "WAIT"

            try:
                from src.analysis.candlestick_detector import CandlestickDetector
                detector = CandlestickDetector(timeframe="4h")

                # Use 4H OHLCV for pattern detection (per L014 - 4H candle close authority)
                ohlcv_4h = self.price_analyzer._fetch_ohlcv(token_symbol, '4h', limit=10)
                if ohlcv_4h and len(ohlcv_4h) >= 3:
                    # Determine context based on VWAP position
                    context = "mid_trend"
                    if vwap_signal == "ABOVE_VWAP":
                        context = "at_resistance"  # Above VWAP = potential resistance
                    elif vwap_signal == "BELOW_VWAP":
                        context = "at_support"  # Below VWAP = potential support

                    candle_result = detector.get_short_signal(
                        ohlcv_data=ohlcv_4h,
                        context=context
                    )
                    candlestick_signal = candle_result.get("signal", "UNKNOWN")
                    candlestick_patterns = candle_result.get("patterns_detected", [])
                    candlestick_score_adj = candle_result.get("score_adjustment", 0.0)
                    candlestick_recommendation = candle_result.get("recommendation", "WAIT")

                    if candlestick_patterns:
                        logger.info(f"   [CANDLESTICK] Detected: {candlestick_patterns} -> {candlestick_signal}")
            except ImportError:
                logger.debug("   Candlestick detector not available")
            except Exception as e:
                logger.debug(f"   Candlestick detection failed: {e}")

            # Session 230: Support/Resistance Detection (Learning 023)
            # Identifies S/R levels, trendlines, role reversals for TGE short timing
            sr_signal = "UNKNOWN"
            sr_position = "UNKNOWN"
            sr_favorability = "NEUTRAL"
            sr_score_adj = 0.0
            sr_analysis = {}

            try:
                from src.analysis.support_resistance_detector import SupportResistanceDetector
                sr_detector = SupportResistanceDetector()

                # Use 4H OHLCV for S/R detection (consistent with candlestick detector)
                ohlcv_4h_sr = ohlcv_4h if 'ohlcv_4h' in dir() and ohlcv_4h else self.price_analyzer._fetch_ohlcv(token_symbol, '4h', limit=50)
                if ohlcv_4h_sr and len(ohlcv_4h_sr) >= 10 and current_close:
                    sr_analysis = sr_detector.analyze_for_tge_short(
                        ohlcv_data=ohlcv_4h_sr,
                        current_price=current_close
                    )
                    sr_position = sr_analysis.get('price_position', {}).get('position', 'UNKNOWN')
                    sr_favorability = sr_analysis.get('price_position', {}).get('position_favorability', 'NEUTRAL')
                    sr_score_adj = sr_analysis.get('confidence_adjustment', 0)

                    # Determine overall S/R signal for TGE shorts
                    if sr_score_adj >= 5:
                        sr_signal = "STRONG_SHORT_ZONE"  # AT_RESISTANCE + strong level or role reversal
                    elif sr_score_adj >= 2:
                        sr_signal = "FAVORABLE_SHORT"  # AT_RESISTANCE or downtrend active
                    elif sr_score_adj <= -2:
                        sr_signal = "UNFAVORABLE_SHORT"  # AT_SUPPORT
                    else:
                        sr_signal = "NEUTRAL"

                    sr_signals = sr_analysis.get('signals', [])
                    if sr_signals:
                        logger.info(f"   [S/R] Position: {sr_position} | Signal: {sr_signal} | Adj: {sr_score_adj:+.1f}")

            except ImportError:
                logger.debug("   Support/Resistance detector not available")
            except Exception as e:
                logger.debug(f"   S/R detection failed: {e}")

            # Session 234: NVT Ratio Detection (Learning 026)
            # NVT = Market Cap / Daily Transaction Volume
            # High NVT = overvalued (price exceeds utility) = SHORT signal
            try:
                nvt_data = self._fetch_nvt_ratio(token_symbol)
                if nvt_data:
                    nvt_ratio = nvt_data.get('nvt_ratio')
                    nvt_signal = nvt_data.get('signal', 'UNKNOWN')
                    nvt_score_adj = nvt_data.get('short_bonus', 0)
                    if nvt_ratio:
                        logger.info(f"   [NVT] Ratio: {nvt_ratio:.1f} | Signal: {nvt_signal} | Adj: {nvt_score_adj:+.2f}")
            except Exception as e:
                logger.debug(f"   NVT ratio fetch failed: {e}")

            # Session 234: TVL Trend Detection (Learning 026)
            # Declining TVL = loss of confidence = SHORT signal (DeFi tokens only)
            try:
                tvl_data = self._fetch_tvl_trend(token_symbol)
                if tvl_data and tvl_data.get('tvl_applicable'):
                    tvl_trend_pct = tvl_data.get('trend_pct')
                    tvl_signal = tvl_data.get('tvl_trend', 'UNKNOWN')
                    tvl_score_adj = tvl_data.get('short_bonus', 0)
                    if tvl_trend_pct is not None:
                        logger.info(f"   [TVL] Trend: {tvl_trend_pct:+.1f}% (7d) | Signal: {tvl_signal} | Adj: {tvl_score_adj:+.2f}")
            except Exception as e:
                logger.debug(f"   TVL trend fetch failed: {e}")

            # Session 265: TVEM Band Detection (Learning 058)
            # TVEM = Trailing VWAP + EMA with Standard Deviation bands
            # Per Sherlock: "Price Retesting the 4H Support + TVEM Band" is STRONG confluence
            tvem_data = {}
            try:
                tvem_data = self.price_analyzer.calculate_tvem_bands(token_symbol, timeframe='4h')
                if tvem_data.get('tvem_mid'):
                    logger.info(f"   [TVEM] Mid: ${tvem_data['tvem_mid']:.6f} | "
                               f"Position: {tvem_data['price_position']} | "
                               f"Signal: {tvem_data['signal']}")
            except Exception as e:
                logger.debug(f"   TVEM Band calculation failed: {e}")

            return {
                # Order Book
                "order_book_imbalance": round(imbalance, 3),
                "order_book_signal": ob_signal,
                "bid_liquidity": orderbook.get('bid_liquidity_top50') if orderbook else None,
                "ask_liquidity": orderbook.get('ask_liquidity_top50') if orderbook else None,

                # Volume (now with trend!)
                "volume_24h": volume_24h,
                "volume_trend": volume_trend,

                # Funding Rate (now with trend + velocity!)
                "funding_rate": funding_rate,
                "funding_signal": funding_signal,
                "funding_trend": funding_trend,
                "funding_velocity": round(funding_velocity, 2) if funding_velocity else None,  # Session 104

                # Open Interest (now with 24h change!)
                "open_interest": oi_value,
                "oi_change_24h": oi_change,
                "oi_trend": oi_trend,

                # Liquidations (requires CoinGlass API key for full data)
                "long_liquidations_24h": long_liqs,
                "short_liquidations_24h": short_liqs,
                "liquidation_cascade_fuel": cascade_fuel,

                # Session 103 Gemini: VWAP and Crowded Trade Detection
                "vwap_price": round(vwap_price, 6) if vwap_price else None,
                "vwap_signal": vwap_signal,
                "crowded_trade_signal": crowded_signal,

                # Session 243: Extended VWAP (Learning 028 - Sherlock Methodology)
                "vwap_weekly": round(vwap_weekly, 6) if vwap_weekly else None,
                "vwap_monthly": round(vwap_monthly, 6) if vwap_monthly else None,
                "vwap_quarterly": round(vwap_quarterly, 6) if vwap_quarterly else None,
                "vwap_yearly": round(vwap_yearly, 6) if vwap_yearly else None,
                "vwap_confluence_score": vwap_confluence_score,
                "vwap_confluence_signal": vwap_confluence_signal,

                # Session 104: CEX vs DEX Delta (early dump detection)
                "cex_dex_delta_pct": round(cex_dex_delta, 2) if cex_dex_delta else None,
                "cex_dex_signal": cex_dex_signal,

                # Session 104: OTC Volume Trend (pre-TGE fading interest)
                "otc_volume_trend_pct": round(otc_volume_trend, 2) if otc_volume_trend else None,
                "otc_trend_signal": otc_trend_signal,

                # Session 209: Candlestick Pattern Detection (Learning 022)
                "candlestick_signal": candlestick_signal,
                "candlestick_patterns": candlestick_patterns,
                "candlestick_score_adjustment": candlestick_score_adj,
                "candlestick_recommendation": candlestick_recommendation,

                # Session 230: Support/Resistance Detection (Learning 023)
                "sr_signal": sr_signal,
                "sr_position": sr_position,
                "sr_favorability": sr_favorability,
                "sr_score_adjustment": sr_score_adj,
                "sr_nearest_resistance": sr_analysis.get('price_position', {}).get('nearest_resistance'),
                "sr_nearest_support": sr_analysis.get('price_position', {}).get('nearest_support'),
                "sr_trendlines": sr_analysis.get('trendlines', {}),
                "sr_role_reversal": sr_analysis.get('role_reversal', {}),
                "sr_signals": sr_analysis.get('signals', []),

                # Session 234: NVT Ratio (Learning 026)
                "nvt_ratio": round(nvt_ratio, 1) if nvt_ratio else None,
                "nvt_signal": nvt_signal,
                "nvt_score_adjustment": nvt_score_adj,

                # Session 234: TVL Trend (Learning 026)
                "tvl_trend_pct": round(tvl_trend_pct, 1) if tvl_trend_pct else None,
                "tvl_signal": tvl_signal,
                "tvl_score_adjustment": tvl_score_adj,

                # Session 265: TVEM Band (Learning 058)
                "tvem_data": tvem_data if tvem_data.get('tvem_mid') else None
            }

        except Exception as e:
            logger.error(f"   Failed to collect advanced TA: {e}")
            return self._empty_advanced()

    def _calculate_score(self, data: Dict) -> float:
        """
        Calculate normalized TA score (0-10) for SHORT thesis.

        Scoring weights (for SHORT positions):
        - BTC downtrend: +1.5
        - ETH downtrend: +1.0
        - Fear & Greed > 70: +1.5 (greed = good for shorts)
        - USDT.D rising (≥6%): +1.0 (risk-off = good for shorts)
        - RSI overbought (>70): +2.0
        - Order book sell pressure: +1.5
        - OI increasing: +1.5 (potential liquidations)

        Total possible: 10 points
        """
        score = 0.0
        macro = data.get('macro_indices', {})
        core = data.get('core_ta', {})
        advanced = data.get('advanced_ta', {})

        # MACRO scoring (4 points max)
        if macro.get('btc_market_structure') == 'downtrend':
            score += 1.5
        if macro.get('eth_market_structure') == 'downtrend':
            score += 1.0

        fear_greed = macro.get('fear_greed_index')
        if fear_greed and fear_greed >= 70:
            score += 1.5
        elif fear_greed and fear_greed >= 50:
            score += 0.5

        if macro.get('usdt_signal') == 'BEARISH_FOR_ALTS':
            score += 1.0

        # CORE TA scoring (3 points max)
        rsi_1h = core.get('rsi_1h')
        if rsi_1h:
            if rsi_1h >= 70:
                score += 2.0
            elif rsi_1h >= 60:
                score += 1.0

        if core.get('trend') == 'downtrend':
            score += 1.0

        # ADVANCED TA scoring (3 points max)
        if advanced.get('order_book_signal') == 'SELL_PRESSURE':
            score += 1.5

        oi = advanced.get('open_interest')
        if oi and oi > 0:
            score += 0.5  # Base score for having OI data
            # Would add more if we had OI change data

        # Liquidation cascade fuel (when implemented)
        if advanced.get('liquidation_cascade_fuel') == 'HIGH_LONG_RISK':
            score += 1.0

        # Session 103 Gemini: Penalty for Crowded Shorts (Squeeze Risk)
        # Negative funding + Rising OI = shorts piling in, squeeze imminent
        # SESSION 234: L011 vs L019 Conflict Resolution (Gemini Review)
        # RULE: Trendline break overrides crowded short warning
        # In high-volatility TGEs, negative funding is often a LAGGING indicator
        crowded_signal = advanced.get('crowded_trade_signal')
        trendlines = advanced.get('sr_trendlines', {})
        downtrend_active = trendlines.get('downtrend', {}).get('active', False)
        downtrend_touches = trendlines.get('downtrend', {}).get('touch_count', 0)

        # Trendline break = downtrend confirmed with 3+ touches (Kaizen rule)
        trendline_break_confirmed = downtrend_active and downtrend_touches >= 3

        if crowded_signal == 'SQUEEZE_RISK_HIGH':
            if trendline_break_confirmed:
                # L019 (trendline) overrides L011 (crowded short) - waive penalty
                logger.info(f"   [L011vsL019] Crowded short penalty WAIVED: trendline break confirmed ({downtrend_touches} touches)")
                # No penalty applied - trendline break takes precedence
            else:
                score -= 2.0  # Apply standard squeeze risk penalty
        elif crowded_signal == 'LONG_CROWDED':
            score += 0.5  # Bonus - longs crowded, good for shorts

        # Session 103 Gemini: VWAP filter - slight penalty when above VWAP
        if advanced.get('vwap_signal') == 'ABOVE_VWAP':
            score -= 0.5  # Price above VWAP = bullish, caution for shorts

        # Session 104: CEX vs DEX Delta bonus/penalty
        # DEX leading dump = early warning, bonus for shorts
        if advanced.get('cex_dex_signal') == 'DUMP_LEAD':
            score += 1.0  # DEX dumping first = bearish signal
        elif advanced.get('cex_dex_signal') == 'PUMP_LEAD':
            score -= 0.5  # DEX pumping first = bullish signal

        # Session 104: Funding velocity (acceleration)
        # Rapidly increasing negative funding = squeeze building
        funding_vel = advanced.get('funding_velocity')
        if funding_vel is not None:
            if funding_vel < -0.5 and advanced.get('funding_rate', 0) < 0:
                # Funding becoming more negative rapidly = shorts paying more
                score -= 0.5  # Squeeze risk increasing
            elif funding_vel > 0.5 and advanced.get('funding_rate', 0) > 0:
                # Funding becoming more positive = longs paying more
                score += 0.5  # Good for shorts

        # Session 104: OTC Volume Trend bonus (MET pattern detection)
        # -62.5% OTC volume → -60% dump pattern
        otc_signal = advanced.get('otc_trend_signal')
        if otc_signal == 'STRONG_FADING':
            score += 1.5  # High conviction SHORT - pre-TGE interest dying
        elif otc_signal == 'FADING_INTEREST':
            score += 0.75  # Moderate SHORT signal
        elif otc_signal == 'STRONG_INTEREST':
            score -= 0.5  # Caution - interest still high

        # Session 209: Candlestick Pattern Score (Learning 022)
        # Bearish reversal patterns at TGE highs = strong SHORT signal
        candlestick_adj = advanced.get('candlestick_score_adjustment', 0)
        if candlestick_adj != 0:
            score += candlestick_adj
            patterns = advanced.get('candlestick_patterns', [])
            if patterns:
                logger.info(f"   [CANDLESTICK] Score adjustment: {candlestick_adj:+.2f} for {patterns}")

        # Session 230: Support/Resistance Score (Learning 023)
        # Price at resistance = favorable, at support = unfavorable
        sr_adj = advanced.get('sr_score_adjustment', 0)
        if sr_adj != 0:
            score += sr_adj
            sr_signals = advanced.get('sr_signals', [])
            if sr_signals:
                logger.info(f"   [S/R] Score adjustment: {sr_adj:+.2f} for {sr_signals}")

        # Session 234: NVT Ratio Score (Learning 026)
        # High NVT = overvalued = strong SHORT signal
        nvt_adj = advanced.get('nvt_score_adjustment', 0)
        if nvt_adj != 0:
            score += nvt_adj
            nvt_ratio = advanced.get('nvt_ratio')
            if nvt_ratio:
                logger.info(f"   [NVT] Score adjustment: {nvt_adj:+.2f} for NVT={nvt_ratio:.1f}")

        # Session 234: TVL Trend Score (Learning 026)
        # Declining TVL = loss of confidence = SHORT signal (DeFi only)
        tvl_adj = advanced.get('tvl_score_adjustment', 0)
        if tvl_adj != 0:
            score += tvl_adj
            tvl_trend = advanced.get('tvl_trend_pct')
            if tvl_trend is not None:
                logger.info(f"   [TVL] Score adjustment: {tvl_adj:+.2f} for TVL trend={tvl_trend:+.1f}%")

        return round(min(max(score, 0.0), 10.0), 1)

    def _get_recommendation(self, data: Dict) -> str:
        """
        Generate TA recommendation based on score.

        Thresholds:
        - 8.0+: MAXIMUM_TA_CONVICTION (all aligned for short)
        - 6.0-7.9: FAVORABLE_FOR_SHORT
        - 4.0-5.9: NEUTRAL_TA
        - 2.0-3.9: UNFAVORABLE_FOR_SHORT
        - 0-1.9: AGAINST_SHORT
        """
        score = data.get('ta_score_normalized', 0)

        if score >= 8.0:
            return "MAXIMUM_TA_CONVICTION"
        elif score >= 6.0:
            return "FAVORABLE_FOR_SHORT"
        elif score >= 4.0:
            return "NEUTRAL_TA"
        elif score >= 2.0:
            return "UNFAVORABLE_FOR_SHORT"
        else:
            return "AGAINST_SHORT"

    def _empty_macro(self) -> Dict:
        """Return empty macro indices structure."""
        return {
            "btc_market_structure": "unknown",
            "eth_market_structure": "unknown",
            "fear_greed_index": None,
            "fear_greed_label": "UNKNOWN",
            "usdt_dominance": None,
            "usdt_signal": "UNKNOWN",
            "btc_dominance": None,
            "btc_dominance_signal": "UNKNOWN",
            "total_market_cap": None,
            "altcoin_market_cap": None,
            "macro_signal": "UNKNOWN"
        }

    def _empty_core_ta(self) -> Dict:
        """Return empty core TA structure."""
        return {
            "rsi_1h": None,
            "rsi_4h": None,
            "rsi_signal": "UNKNOWN",
            "price_vs_ma20": "unknown",
            "volatility_24h": None,
            "current_price": None,
            "trend": "unknown",
            "structure": "unknown"
        }

    # Session 243: Extended VWAP cache (Gemini optimization)
    # Q/Y VWAPs move slowly - calculate once every 4 hours to save API weight
    _extended_vwap_cache: Dict = {}
    _VWAP_CACHE_TTL_SECONDS = 4 * 60 * 60  # 4 hours

    def _calculate_extended_vwaps(self, token_symbol: str) -> Optional[Dict]:
        """
        Session 243: Calculate Extended VWAPs (Learning 028 - Sherlock Methodology)

        Sherlock uses VWAP at multiple timeframes for institutional-grade S/R:
        - 24h VWAP: Intraday mean reversion (already implemented)
        - Weekly VWAP: Short-term institutional level
        - Monthly VWAP: Medium-term institutional level
        - Quarterly VWAP: Major support/resistance (HIGH importance)
        - Yearly VWAP: Critical institutional level (HIGHEST importance)

        VWAP = Sum(Volume * Typical Price) / Sum(Volume)
        Typical Price = (High + Low + Close) / 3

        Gemini Optimization: Uses 4-hour cache since Q/Y VWAPs move slowly.

        Returns:
            Dict with vwap_weekly, vwap_monthly, vwap_quarterly, vwap_yearly,
            confluence_score (0-5), and confluence_signal
        """
        # Check cache first (Gemini optimization)
        cache_key = f"{token_symbol}_extended_vwaps"
        cached = TADataAggregator._extended_vwap_cache.get(cache_key)
        now = datetime.now(tz=None)  # Use local time for cache comparison
        if cached:
            cache_time, cache_data = cached
            age_seconds = (now - cache_time).total_seconds()
            if age_seconds < self._VWAP_CACHE_TTL_SECONDS:
                logger.debug(f"   [VWAP-CACHE] Using cached extended VWAPs (age: {age_seconds/60:.1f}m)")
                return cache_data

        try:
            result = {
                'vwap_weekly': None,
                'vwap_monthly': None,
                'vwap_quarterly': None,
                'vwap_yearly': None,
                'confluence_score': 0,
                'confluence_signal': 'UNKNOWN'
            }

            current_price = None

            # Helper function to calculate VWAP from OHLCV data
            def calc_vwap(ohlcv_data: List) -> Optional[float]:
                if not ohlcv_data:
                    return None
                cum_vol_price = 0
                cum_vol = 0
                for candle in ohlcv_data:
                    high = candle[2]
                    low = candle[3]
                    close = candle[4]
                    vol = candle[5]
                    typical_price = (high + low + close) / 3
                    cum_vol_price += (typical_price * vol)
                    cum_vol += vol
                if cum_vol > 0:
                    return cum_vol_price / cum_vol
                return None

            # Weekly VWAP: 4H candles, 42 bars (7 days * 6 4H periods)
            try:
                ohlcv_weekly = self.price_analyzer._fetch_ohlcv(token_symbol, '4h', limit=42)
                if ohlcv_weekly:
                    result['vwap_weekly'] = calc_vwap(ohlcv_weekly)
                    current_price = ohlcv_weekly[-1][4] if ohlcv_weekly else None
            except Exception:
                pass

            # Monthly VWAP: 4H candles, 180 bars (30 days * 6 4H periods)
            try:
                ohlcv_monthly = self.price_analyzer._fetch_ohlcv(token_symbol, '4h', limit=180)
                if ohlcv_monthly:
                    result['vwap_monthly'] = calc_vwap(ohlcv_monthly)
                    if not current_price:
                        current_price = ohlcv_monthly[-1][4]
            except Exception:
                pass

            # Quarterly VWAP: Daily candles, 90 bars
            try:
                ohlcv_quarterly = self.price_analyzer._fetch_ohlcv(token_symbol, '1d', limit=90)
                if ohlcv_quarterly:
                    result['vwap_quarterly'] = calc_vwap(ohlcv_quarterly)
                    if not current_price:
                        current_price = ohlcv_quarterly[-1][4]
            except Exception:
                pass

            # Yearly VWAP: Daily candles, 365 bars
            try:
                ohlcv_yearly = self.price_analyzer._fetch_ohlcv(token_symbol, '1d', limit=365)
                if ohlcv_yearly:
                    result['vwap_yearly'] = calc_vwap(ohlcv_yearly)
                    if not current_price:
                        current_price = ohlcv_yearly[-1][4]
            except Exception:
                pass

            # Calculate confluence score (0-5 scale)
            # Score how many VWAPs current price is near (within 2%)
            if current_price and current_price > 0:
                confluence_score = 0
                near_vwaps = []
                tolerance = 0.02  # 2% tolerance

                for vwap_name, vwap_value in [
                    ('weekly', result['vwap_weekly']),
                    ('monthly', result['vwap_monthly']),
                    ('quarterly', result['vwap_quarterly']),
                    ('yearly', result['vwap_yearly'])
                ]:
                    if vwap_value:
                        distance_pct = abs(current_price - vwap_value) / vwap_value
                        if distance_pct <= tolerance:
                            confluence_score += 1
                            near_vwaps.append(vwap_name)
                            # Extra point for Q/Y VWAP confluence (most significant)
                            if vwap_name in ['quarterly', 'yearly']:
                                confluence_score += 0.5

                result['confluence_score'] = min(5, confluence_score)

                # Determine confluence signal for TGE shorts
                # Per Sherlock: Price at Q/Y VWAP after breakout = high probability entry
                if confluence_score >= 3:
                    result['confluence_signal'] = 'MAJOR_CONFLUENCE'  # Multiple VWAPs aligning
                elif confluence_score >= 2:
                    result['confluence_signal'] = 'MODERATE_CONFLUENCE'
                elif confluence_score >= 1:
                    result['confluence_signal'] = 'MINOR_CONFLUENCE'
                else:
                    # Check if price is above/below all VWAPs
                    all_vwaps = [v for v in [result['vwap_weekly'], result['vwap_monthly'],
                                             result['vwap_quarterly'], result['vwap_yearly']] if v]
                    if all_vwaps:
                        if all(current_price < v for v in all_vwaps):
                            result['confluence_signal'] = 'BELOW_ALL_VWAPS'  # Bearish structure
                        elif all(current_price > v for v in all_vwaps):
                            result['confluence_signal'] = 'ABOVE_ALL_VWAPS'  # Bullish structure
                        else:
                            result['confluence_signal'] = 'NO_CONFLUENCE'

            # Store in cache (Gemini optimization)
            TADataAggregator._extended_vwap_cache[cache_key] = (now, result)
            logger.debug(f"   [VWAP-CACHE] Cached extended VWAPs for {token_symbol}")

            return result

        except Exception as e:
            logger.debug(f"Extended VWAP calculation failed: {e}")
            return None

    # Session 254: Daily 12 EMA cache (Gemini optimization)
    # EMA moves slowly - calculate once every 4 hours
    _daily_ema_cache: Dict = {}
    _EMA_CACHE_TTL_SECONDS = 4 * 60 * 60  # 4 hours

    def _calculate_daily_12_ema(self, token_symbol: str) -> Optional[Dict]:
        """
        Session 254: Calculate 1D 12 EMA (Learning 038 - Sherlock Confirmation)

        Sherlock uses 1D 12 EMA as his primary trend filter for confirmations.
        Combined with Quarterly VWAP, it provides a simple but effective system.

        EMA Formula: EMA = Price(t) * k + EMA(y) * (1 - k)
        where k = 2 / (N + 1), N = 12

        Returns:
            Dict with:
                - ema_12_value: The EMA value
                - current_price: Current daily close
                - price_vs_ema: "ABOVE" | "BELOW" | "AT"
                - distance_pct: Distance from EMA as percentage
                - ema_trend: "RISING" | "FALLING" | "FLAT"
        """
        # Check cache first
        cache_key = f"{token_symbol}_daily_12_ema"
        cached = TADataAggregator._daily_ema_cache.get(cache_key)
        now = datetime.now(tz=None)
        if cached:
            cache_time, cache_data = cached
            age_seconds = (now - cache_time).total_seconds()
            if age_seconds < self._EMA_CACHE_TTL_SECONDS:
                logger.debug(f"   [EMA-CACHE] Using cached 12 EMA (age: {age_seconds/60:.1f}m)")
                return cache_data

        try:
            result = {
                'ema_12_value': None,
                'current_price': None,
                'price_vs_ema': 'UNKNOWN',
                'distance_pct': None,
                'ema_trend': 'UNKNOWN'
            }

            # Fetch daily candles (need 20+ for EMA stabilization)
            ohlcv = self.price_analyzer._fetch_ohlcv(token_symbol, '1d', limit=30)
            if not ohlcv or len(ohlcv) < 15:
                logger.debug(f"   [EMA] Insufficient data for {token_symbol} 1D 12 EMA")
                return None

            # Extract close prices
            closes = [candle[4] for candle in ohlcv]
            current_price = closes[-1]
            result['current_price'] = current_price

            # Calculate 12-period EMA
            period = 12
            k = 2 / (period + 1)  # Smoothing factor

            # Initialize EMA with SMA of first 'period' values
            ema = sum(closes[:period]) / period

            # Calculate EMA for remaining values
            ema_values = [ema]
            for close in closes[period:]:
                ema = close * k + ema * (1 - k)
                ema_values.append(ema)

            # Current EMA is the last value
            current_ema = ema_values[-1]
            result['ema_12_value'] = round(current_ema, 6)

            # Determine position relative to EMA
            tolerance = 0.01  # 1% tolerance for "AT"
            distance_pct = ((current_price - current_ema) / current_ema) * 100
            result['distance_pct'] = round(distance_pct, 2)

            if abs(distance_pct) <= tolerance * 100:
                result['price_vs_ema'] = 'AT'
            elif distance_pct > 0:
                result['price_vs_ema'] = 'ABOVE'
            else:
                result['price_vs_ema'] = 'BELOW'

            # Determine EMA trend (compare last 3 EMA values)
            if len(ema_values) >= 3:
                ema_change_1 = ema_values[-1] - ema_values[-2]
                ema_change_2 = ema_values[-2] - ema_values[-3]
                avg_change = (ema_change_1 + ema_change_2) / 2
                change_pct = (avg_change / ema_values[-3]) * 100

                if change_pct > 0.5:
                    result['ema_trend'] = 'RISING'
                elif change_pct < -0.5:
                    result['ema_trend'] = 'FALLING'
                else:
                    result['ema_trend'] = 'FLAT'

            # Cache the result
            TADataAggregator._daily_ema_cache[cache_key] = (now, result)
            logger.debug(f"   [EMA] {token_symbol} 1D 12 EMA: ${current_ema:.6f} | Price: {result['price_vs_ema']} ({distance_pct:+.2f}%)")

            return result

        except Exception as e:
            logger.debug(f"   [EMA] Daily 12 EMA calculation failed: {e}")
            return None

    def _calculate_daily_24_ema(self, token_symbol: str) -> Optional[Dict]:
        """
        Session 263: Calculate 1D 24 EMA (L046 - Sherlock Secondary Trend Filter)

        Sherlock uses BOTH 1D 12 EMA AND 1D 24 EMA together for trend confirmation.
        24 EMA is slower (~1 month of data) providing more stable trend confirmation.

        EMA Sandwich Detection (ema_sandwich):
        - Both below price = STRONG TREND UP
        - Both above price = STRONG TREND DOWN
        - Price between EMAs = TRANSITIONAL/CHOPPY (caution)

        Returns:
            Dict with:
                - ema_24_value: The 24 EMA value
                - current_price: Current daily close
                - price_vs_ema: "ABOVE" | "BELOW" | "AT"
                - distance_pct: Distance from EMA as percentage
                - ema_trend: "RISING" | "FALLING" | "FLAT"
        """
        # Check cache first
        cache_key = f"{token_symbol}_daily_24_ema"
        cached = TADataAggregator._daily_ema_cache.get(cache_key)
        now = datetime.now(tz=None)
        if cached:
            cache_time, cache_data = cached
            age_seconds = (now - cache_time).total_seconds()
            if age_seconds < self._EMA_CACHE_TTL_SECONDS:
                logger.debug(f"   [EMA-CACHE] Using cached 24 EMA (age: {age_seconds/60:.1f}m)")
                return cache_data

        try:
            result = {
                'ema_24_value': None,
                'current_price': None,
                'price_vs_ema': 'UNKNOWN',
                'distance_pct': None,
                'ema_trend': 'UNKNOWN'
            }

            # Fetch daily candles (need 30+ for EMA stabilization)
            ohlcv = self.price_analyzer._fetch_ohlcv(token_symbol, '1d', limit=40)
            if not ohlcv or len(ohlcv) < 26:
                logger.debug(f"   [EMA] Insufficient data for {token_symbol} 1D 24 EMA")
                return None

            # Extract close prices
            closes = [candle[4] for candle in ohlcv]
            current_price = closes[-1]
            result['current_price'] = current_price

            # Calculate 24-period EMA
            period = 24
            k = 2 / (period + 1)  # Smoothing factor

            # Initialize EMA with SMA of first 'period' values
            ema = sum(closes[:period]) / period

            # Calculate EMA for remaining values
            ema_values = [ema]
            for close in closes[period:]:
                ema = close * k + ema * (1 - k)
                ema_values.append(ema)

            # Current EMA is the last value
            current_ema = ema_values[-1]
            result['ema_24_value'] = round(current_ema, 6)

            # Determine position relative to EMA
            tolerance = 0.01  # 1% tolerance for "AT"
            distance_pct = ((current_price - current_ema) / current_ema) * 100
            result['distance_pct'] = round(distance_pct, 2)

            if abs(distance_pct) <= tolerance * 100:
                result['price_vs_ema'] = 'AT'
            elif distance_pct > 0:
                result['price_vs_ema'] = 'ABOVE'
            else:
                result['price_vs_ema'] = 'BELOW'

            # Determine EMA trend (compare last 3 EMA values)
            if len(ema_values) >= 3:
                ema_change_1 = ema_values[-1] - ema_values[-2]
                ema_change_2 = ema_values[-2] - ema_values[-3]
                avg_change = (ema_change_1 + ema_change_2) / 2
                change_pct = (avg_change / ema_values[-3]) * 100

                if change_pct > 0.5:
                    result['ema_trend'] = 'RISING'
                elif change_pct < -0.5:
                    result['ema_trend'] = 'FALLING'
                else:
                    result['ema_trend'] = 'FLAT'

            # Cache the result
            TADataAggregator._daily_ema_cache[cache_key] = (now, result)
            logger.debug(f"   [EMA] {token_symbol} 1D 24 EMA: ${current_ema:.6f} | Price: {result['price_vs_ema']} ({distance_pct:+.2f}%)")

            return result

        except Exception as e:
            logger.debug(f"   [EMA] Daily 24 EMA calculation failed: {e}")
            return None

    def _calculate_mtf_ema_200(self, token_symbol: str) -> Optional[Dict]:
        """
        Session 263: Calculate MTF (Multi-Timeframe) EMA 200 (L047)

        Sherlock uses MTF EMA 200 as major trend filter:
        - Price above 200 EMA: Long-term uptrend, long bias
        - Price below 200 EMA: Long-term downtrend, short bias
        - First touch of 200 EMA: High probability bounce/rejection

        Calculates on both 4H and 1D timeframes.

        Returns:
            Dict with:
                - ema_200_4h: 4H 200 EMA data (~33 days)
                - ema_200_1d: 1D 200 EMA data (~10 months)
                - trend_bias: "BULLISH" | "BEARISH" | "MIXED"
                - short_favorable: Boolean indicating if favorable for shorts
        """
        # Check cache first
        cache_key = f"{token_symbol}_mtf_ema_200"
        cached = TADataAggregator._daily_ema_cache.get(cache_key)
        now = datetime.now(tz=None)
        if cached:
            cache_time, cache_data = cached
            age_seconds = (now - cache_time).total_seconds()
            if age_seconds < self._EMA_CACHE_TTL_SECONDS:
                logger.debug(f"   [EMA-CACHE] Using cached MTF 200 EMA (age: {age_seconds/60:.1f}m)")
                return cache_data

        try:
            result = {
                'ema_200_4h': {
                    'value': None,
                    'price_position': 'UNKNOWN',
                    'distance_pct': None,
                    'first_touch': False
                },
                'ema_200_1d': {
                    'value': None,
                    'price_position': 'UNKNOWN',
                    'distance_pct': None,
                    'first_touch': False
                },
                'trend_bias': 'UNKNOWN',
                'short_favorable': False
            }

            # Calculate 4H 200 EMA (need 210 candles for warmup)
            ohlcv_4h = self.price_analyzer._fetch_ohlcv(token_symbol, '4h', limit=220)
            if ohlcv_4h and len(ohlcv_4h) >= 205:
                closes_4h = [candle[4] for candle in ohlcv_4h]
                current_price = closes_4h[-1]

                period = 200
                k = 2 / (period + 1)
                ema = sum(closes_4h[:period]) / period
                for close in closes_4h[period:]:
                    ema = close * k + ema * (1 - k)

                result['ema_200_4h']['value'] = round(ema, 6)
                dist_4h = ((current_price - ema) / ema) * 100
                result['ema_200_4h']['distance_pct'] = round(dist_4h, 2)
                result['ema_200_4h']['price_position'] = 'ABOVE' if dist_4h > 1 else ('BELOW' if dist_4h < -1 else 'AT')
                result['ema_200_4h']['first_touch'] = abs(dist_4h) < 1.5  # Within 1.5% = potential first touch

            # Calculate 1D 200 EMA (need 210 candles for warmup)
            ohlcv_1d = self.price_analyzer._fetch_ohlcv(token_symbol, '1d', limit=220)
            if ohlcv_1d and len(ohlcv_1d) >= 205:
                closes_1d = [candle[4] for candle in ohlcv_1d]
                current_price = closes_1d[-1]

                period = 200
                k = 2 / (period + 1)
                ema = sum(closes_1d[:period]) / period
                for close in closes_1d[period:]:
                    ema = close * k + ema * (1 - k)

                result['ema_200_1d']['value'] = round(ema, 6)
                dist_1d = ((current_price - ema) / ema) * 100
                result['ema_200_1d']['distance_pct'] = round(dist_1d, 2)
                result['ema_200_1d']['price_position'] = 'ABOVE' if dist_1d > 1 else ('BELOW' if dist_1d < -1 else 'AT')
                result['ema_200_1d']['first_touch'] = abs(dist_1d) < 1.5

            # Determine overall trend bias
            pos_4h = result['ema_200_4h']['price_position']
            pos_1d = result['ema_200_1d']['price_position']

            if pos_4h == 'ABOVE' and pos_1d == 'ABOVE':
                result['trend_bias'] = 'BULLISH'
                result['short_favorable'] = False
            elif pos_4h == 'BELOW' and pos_1d == 'BELOW':
                result['trend_bias'] = 'BEARISH'
                result['short_favorable'] = True
            elif pos_4h != 'UNKNOWN' and pos_1d != 'UNKNOWN':
                result['trend_bias'] = 'MIXED'
                result['short_favorable'] = pos_4h == 'BELOW'  # Favor 4H for short-term

            # Cache the result
            TADataAggregator._daily_ema_cache[cache_key] = (now, result)
            logger.debug(f"   [EMA] {token_symbol} MTF 200 EMA: Trend={result['trend_bias']}, Short favorable={result['short_favorable']}")

            return result

        except Exception as e:
            logger.debug(f"   [EMA] MTF 200 EMA calculation failed: {e}")
            return None

    def get_dual_ema_analysis(self, token_symbol: str) -> Dict:
        """
        Session 263: Get dual EMA analysis with 12 and 24 EMA (L046)

        Combines 12 EMA and 24 EMA for trend confirmation with ema_sandwich detection.

        Returns:
            Dict with:
                - ema_12: 12 EMA data
                - ema_24: 24 EMA data
                - dual_alignment: "ALIGNED_BULLISH" | "ALIGNED_BEARISH" | "SANDWICH" | "UNKNOWN"
                - ema_cross: "BULLISH_CROSS" | "BEARISH_CROSS" | "NO_CROSS" | "UNKNOWN"
                - trend_strength: "STRONG" | "MODERATE" | "WEAK"
                - conviction_modifier: Float adjustment for conviction score
        """
        result = {
            'ema_12': None,
            'ema_24': None,
            'dual_alignment': 'UNKNOWN',
            'ema_cross': 'UNKNOWN',
            'trend_strength': 'WEAK',
            'conviction_modifier': 0.0
        }

        try:
            # Get both EMAs
            ema_12 = self._calculate_daily_12_ema(token_symbol)
            ema_24 = self._calculate_daily_24_ema(token_symbol)

            if not ema_12 or not ema_24:
                return result

            result['ema_12'] = ema_12
            result['ema_24'] = ema_24

            # Determine dual alignment
            price_vs_12 = ema_12.get('price_vs_ema', 'UNKNOWN')
            price_vs_24 = ema_24.get('price_vs_ema', 'UNKNOWN')

            if price_vs_12 == 'ABOVE' and price_vs_24 == 'ABOVE':
                result['dual_alignment'] = 'ALIGNED_BULLISH'
                result['trend_strength'] = 'STRONG'
                result['conviction_modifier'] = -0.5  # Unfavorable for shorts
            elif price_vs_12 == 'BELOW' and price_vs_24 == 'BELOW':
                result['dual_alignment'] = 'ALIGNED_BEARISH'
                result['trend_strength'] = 'STRONG'
                result['conviction_modifier'] = +0.5  # Favorable for shorts
            elif (price_vs_12 == 'ABOVE' and price_vs_24 == 'BELOW') or \
                 (price_vs_12 == 'BELOW' and price_vs_24 == 'ABOVE'):
                result['dual_alignment'] = 'SANDWICH'
                result['trend_strength'] = 'WEAK'
                result['conviction_modifier'] = -0.25  # Choppy market penalty

            # Detect EMA cross (compare EMA values directly)
            ema_12_val = ema_12.get('ema_12_value', 0)
            ema_24_val = ema_24.get('ema_24_value', 0)

            if ema_12_val and ema_24_val:
                if ema_12_val > ema_24_val * 1.005:  # 12 EMA above 24 by >0.5%
                    result['ema_cross'] = 'BULLISH_CROSS'
                elif ema_12_val < ema_24_val * 0.995:  # 12 EMA below 24 by >0.5%
                    result['ema_cross'] = 'BEARISH_CROSS'
                else:
                    result['ema_cross'] = 'NO_CROSS'  # EMAs converging

            return result

        except Exception as e:
            logger.debug(f"   [EMA] Dual EMA analysis failed: {e}")
            return result

    def get_all_dynamic_levels(self, token_symbol: str) -> Dict:
        """
        Session 264: Get all "dynamic levels" (L061 - Sherlock's unified S/R terminology)

        Sherlock uses "dynamic levels" as umbrella term for all moving S/R:
        - 12 EMA (1D): Trend
        - 24 EMA (1D): Trend
        - 200 EMA (4H, 1D): Major trend
        - QVWAP (4H): Volume-weighted
        - YVWAP (1D): Volume-weighted

        Returns:
            Dict with:
                - levels: List of all dynamic level values with names
                - current_price: Current token price
                - position: "ABOVE_ALL" | "BELOW_ALL" | "MIXED"
                - above_count: Number of levels price is above
                - below_count: Number of levels price is below
                - recommendation: "STRONG_BULLISH" | "STRONG_BEARISH" | "NEUTRAL"
        """
        result = {
            'levels': [],
            'current_price': None,
            'position': 'UNKNOWN',
            'above_count': 0,
            'below_count': 0,
            'recommendation': 'Insufficient data'
        }

        try:
            # Get all EMAs
            ema_12 = self._calculate_daily_12_ema(token_symbol)
            ema_24 = self._calculate_daily_24_ema(token_symbol)
            ema_200 = self._calculate_mtf_ema_200(token_symbol)

            # Get extended VWAPs
            vwaps = self._calculate_extended_vwaps(token_symbol)

            # Get current price
            current_price = ema_12.get('current_price') if ema_12 else None
            if not current_price:
                return result

            result['current_price'] = current_price

            # Collect all dynamic levels
            levels = []

            if ema_12 and ema_12.get('ema_12_value'):
                levels.append({
                    'name': '1D 12 EMA',
                    'value': ema_12['ema_12_value'],
                    'type': 'EMA',
                    'timeframe': '1D'
                })

            if ema_24 and ema_24.get('ema_24_value'):
                levels.append({
                    'name': '1D 24 EMA',
                    'value': ema_24['ema_24_value'],
                    'type': 'EMA',
                    'timeframe': '1D'
                })

            if ema_200:
                if ema_200.get('ema_200_4h', {}).get('value'):
                    levels.append({
                        'name': '4H 200 EMA',
                        'value': ema_200['ema_200_4h']['value'],
                        'type': 'EMA',
                        'timeframe': '4H'
                    })

                if ema_200.get('ema_200_1d', {}).get('value'):
                    levels.append({
                        'name': '1D 200 EMA',
                        'value': ema_200['ema_200_1d']['value'],
                        'type': 'EMA',
                        'timeframe': '1D'
                    })

            if vwaps:
                if vwaps.get('vwap_quarterly'):
                    levels.append({
                        'name': 'Quarterly VWAP',
                        'value': vwaps['vwap_quarterly'],
                        'type': 'VWAP',
                        'timeframe': 'Q'
                    })

                if vwaps.get('vwap_yearly'):
                    levels.append({
                        'name': 'Yearly VWAP',
                        'value': vwaps['vwap_yearly'],
                        'type': 'VWAP',
                        'timeframe': 'Y'
                    })

            result['levels'] = levels

            # Determine price position vs each level
            above_count = 0
            below_count = 0

            for level in levels:
                level_value = level['value']
                distance_pct = ((current_price - level_value) / level_value) * 100
                level['distance_pct'] = round(distance_pct, 2)

                if distance_pct > 1:  # >1% above
                    level['position'] = 'ABOVE'
                    above_count += 1
                elif distance_pct < -1:  # >1% below
                    level['position'] = 'BELOW'
                    below_count += 1
                else:
                    level['position'] = 'AT'

            result['above_count'] = above_count
            result['below_count'] = below_count

            # Determine overall position
            total_levels = len(levels)
            if total_levels == 0:
                result['position'] = 'UNKNOWN'
            elif above_count == total_levels:
                result['position'] = 'ABOVE_ALL'
                result['recommendation'] = 'STRONG_BULLISH: Price above all dynamic levels'
            elif below_count == total_levels:
                result['position'] = 'BELOW_ALL'
                result['recommendation'] = 'STRONG_BEARISH: Price below all dynamic levels (ideal for shorts)'
            else:
                result['position'] = 'MIXED'
                if above_count > below_count:
                    result['recommendation'] = f'BULLISH: Price above {above_count}/{total_levels} dynamic levels'
                elif below_count > above_count:
                    result['recommendation'] = f'BEARISH: Price below {below_count}/{total_levels} dynamic levels'
                else:
                    result['recommendation'] = f'NEUTRAL: Price at transition ({above_count}/{total_levels} above)'

            logger.debug(f"   [DYNAMIC LEVELS] {token_symbol}: {result['position']} - {above_count} above, {below_count} below")
            return result

        except Exception as e:
            logger.debug(f"   [DYNAMIC LEVELS] Failed to collect dynamic levels: {e}")
            return result

    def get_sherlock_confirmation(self, token_symbol: str, position_type: str = "SHORT") -> Dict:
        """
        Session 254: Sherlock-style confirmation using QVWAP + 1D 12 EMA (Learning 038)

        Combines Quarterly VWAP and Daily 12 EMA for trade confirmation.
        This is Sherlock's primary confirmation method.

        Args:
            token_symbol: Token to analyze
            position_type: "SHORT" or "LONG"

        Returns:
            Dict with:
                - qvwap_position: Price position vs Quarterly VWAP
                - ema_12d_position: Price position vs 1D 12 EMA
                - alignment: "BULLISH" | "BEARISH" | "NEUTRAL"
                - confirmation_strength: "STRONG" | "MODERATE" | "WEAK"
                - first_retest_bonus: Whether this is first retest of either level
                - recommendation: Human-readable recommendation
                - position_size_multiplier: 0.5-1.0 based on confirmation strength
        """
        result = {
            'qvwap_position': 'UNKNOWN',
            'qvwap_value': None,
            'ema_12d_position': 'UNKNOWN',
            'ema_12d_value': None,
            'current_price': None,
            'alignment': 'NEUTRAL',
            'confirmation_strength': 'WEAK',
            'first_retest_bonus': False,
            'recommendation': 'Insufficient data for Sherlock confirmation',
            'position_size_multiplier': 0.5
        }

        try:
            # Get Quarterly VWAP
            extended_vwaps = self._calculate_extended_vwaps(token_symbol)
            qvwap = extended_vwaps.get('vwap_quarterly') if extended_vwaps else None

            # Get 1D 12 EMA
            ema_data = self._calculate_daily_12_ema(token_symbol)
            ema_12 = ema_data.get('ema_12_value') if ema_data else None
            current_price = ema_data.get('current_price') if ema_data else None

            if not qvwap or not ema_12 or not current_price:
                return result

            result['qvwap_value'] = qvwap
            result['ema_12d_value'] = ema_12
            result['current_price'] = current_price

            # Determine position vs QVWAP
            qvwap_distance_pct = ((current_price - qvwap) / qvwap) * 100
            if abs(qvwap_distance_pct) <= 2:
                result['qvwap_position'] = 'AT'
            elif qvwap_distance_pct > 0:
                result['qvwap_position'] = 'ABOVE'
            else:
                result['qvwap_position'] = 'BELOW'

            # Use EMA position from ema_data
            result['ema_12d_position'] = ema_data.get('price_vs_ema', 'UNKNOWN')

            # Determine alignment
            qvwap_bearish = result['qvwap_position'] in ['BELOW', 'AT']
            ema_bearish = result['ema_12d_position'] in ['BELOW', 'AT']
            qvwap_bullish = result['qvwap_position'] in ['ABOVE', 'AT']
            ema_bullish = result['ema_12d_position'] in ['ABOVE', 'AT']

            if qvwap_bearish and ema_bearish:
                result['alignment'] = 'BEARISH'
            elif qvwap_bullish and ema_bullish:
                result['alignment'] = 'BULLISH'
            else:
                result['alignment'] = 'NEUTRAL'

            # Determine confirmation strength for position type
            if position_type == "SHORT":
                if result['alignment'] == 'BEARISH':
                    result['confirmation_strength'] = 'STRONG'
                    result['position_size_multiplier'] = 1.0
                    result['recommendation'] = 'STRONG SHORT: Price below QVWAP and 1D 12 EMA - Sherlock confirmation aligned'
                elif result['alignment'] == 'NEUTRAL':
                    result['confirmation_strength'] = 'MODERATE'
                    result['position_size_multiplier'] = 0.75
                    result['recommendation'] = 'MODERATE SHORT: Mixed QVWAP/EMA signals - reduce size'
                else:
                    result['confirmation_strength'] = 'WEAK'
                    result['position_size_multiplier'] = 0.5
                    result['recommendation'] = 'WEAK SHORT: Price above QVWAP and EMA - Sherlock confirmation AGAINST short'
            else:  # LONG
                if result['alignment'] == 'BULLISH':
                    result['confirmation_strength'] = 'STRONG'
                    result['position_size_multiplier'] = 1.0
                    result['recommendation'] = 'STRONG LONG: Price above QVWAP and 1D 12 EMA - Sherlock confirmation aligned'
                elif result['alignment'] == 'NEUTRAL':
                    result['confirmation_strength'] = 'MODERATE'
                    result['position_size_multiplier'] = 0.75
                    result['recommendation'] = 'MODERATE LONG: Mixed QVWAP/EMA signals - reduce size'
                else:
                    result['confirmation_strength'] = 'WEAK'
                    result['position_size_multiplier'] = 0.5
                    result['recommendation'] = 'WEAK LONG: Price below QVWAP and EMA - Sherlock confirmation AGAINST long'

            # Check for first retest bonus (L029 synergy)
            # If price is AT either level, it might be a retest
            if result['qvwap_position'] == 'AT' or result['ema_12d_position'] == 'AT':
                result['first_retest_bonus'] = True
                result['position_size_multiplier'] = min(1.0, result['position_size_multiplier'] + 0.1)
                result['recommendation'] += ' | First retest bonus active'

            logger.info(f"[SHERLOCK] {token_symbol} {position_type}: {result['alignment']} "
                       f"(QVWAP: {result['qvwap_position']}, EMA: {result['ema_12d_position']}) "
                       f"-> {result['confirmation_strength']}")

            return result

        except Exception as e:
            logger.error(f"[SHERLOCK] Confirmation calculation failed: {e}")
            return result

    def _fetch_otc_volume_trend(self, token_symbol: str) -> Optional[Dict]:
        """
        Session 104: Fetch OTC volume trend from local data files.
        Integrates with otc_volume_scanner.py data.

        MET baseline: -62.5% over 7 days → -60% dump at TGE

        Returns:
            Dict with trend_pct and signal, or None if unavailable
        """
        import json
        from pathlib import Path

        try:
            # Check for OTC data in token's consolidated.json
            token_dir = Path(f"data/tokens/{token_symbol}")
            consolidated_path = token_dir / "consolidated.json"

            if not consolidated_path.exists():
                return None

            with open(consolidated_path) as f:
                data = json.load(f)

            # Check if we have OTC data
            if not data.get('otc_data_available'):
                return None

            # Look for volume trend data
            otc_volume_7d_ago = data.get('otc_volume_7d_ago')
            otc_volume_current = data.get('otc_volume_current')

            if not otc_volume_7d_ago or not otc_volume_current:
                return None

            if otc_volume_7d_ago == 0:
                return None

            # Calculate trend
            trend_pct = ((otc_volume_current - otc_volume_7d_ago) / otc_volume_7d_ago) * 100

            # MET baseline calibration
            if trend_pct <= -50.0:
                signal = "STRONG_FADING"  # Very bearish - high conviction SHORT
            elif trend_pct <= -30.0:
                signal = "FADING_INTEREST"  # Bearish - moderate SHORT signal
            elif trend_pct >= 50.0:
                signal = "STRONG_INTEREST"  # Bullish - caution for shorts
            else:
                signal = "NEUTRAL"

            return {
                "trend_pct": trend_pct,
                "signal": signal
            }

        except Exception as e:
            logger.debug(f"   OTC trend fetch failed: {e}")
            return None

    def _fetch_dex_price(self, token_symbol: str) -> Optional[float]:
        """
        Session 104: Fetch DEX price from DexScreener API.
        DEX price often leads CEX by minutes during TGE dumps.

        Returns price in USD or None if unavailable.
        """
        import requests

        try:
            # DexScreener API - search by token symbol
            url = f"https://api.dexscreener.com/latest/dex/search?q={token_symbol}"
            response = requests.get(url, timeout=5)

            if response.status_code == 200:
                data = response.json()
                pairs = data.get('pairs', [])

                if pairs:
                    # Get the highest liquidity pair
                    best_pair = max(pairs, key=lambda p: float(p.get('liquidity', {}).get('usd', 0) or 0))
                    price = float(best_pair.get('priceUsd', 0))
                    if price > 0:
                        return price

            return None

        except Exception as e:
            logger.debug(f"   DEX price fetch failed: {e}")
            return None

    def _fetch_nvt_ratio(self, token_symbol: str) -> Optional[Dict]:
        """
        Session 234: Fetch NVT Ratio (Network Value to Transactions).
        Learning 026: NVT = Market Cap / Daily Transaction Volume

        For TGE shorts:
        - NVT > 100: STRONG short signal (extremely overvalued)
        - NVT 50-100: MODERATE short signal
        - NVT < 50: WEAK short signal (fairly valued)

        Data sources:
        - Market cap: CoinGecko API (free)
        - Transaction volume: On-chain data via token data files

        Returns:
            Dict with nvt_ratio, signal, and short_bonus, or None if unavailable
        """
        import requests

        try:
            # Step 1: Get market cap from CoinGecko
            # First, try to get CoinGecko ID from our token data
            token_dir = Path(f"data/tokens/{token_symbol}")
            consolidated_path = token_dir / "consolidated.json"

            coingecko_id = None
            market_cap = None
            daily_volume = None

            if consolidated_path.exists():
                with open(consolidated_path) as f:
                    data = json.load(f)
                coingecko_id = data.get('coingecko_id')
                # Try to get cached market data
                market_cap = data.get('market_cap_usd')
                daily_volume = data.get('daily_volume_usd') or data.get('volume_24h_usd')

            # If no cached data, fetch from CoinGecko
            if not market_cap and coingecko_id:
                url = f"https://api.coingecko.com/api/v3/coins/{coingecko_id}"
                response = requests.get(url, timeout=10, params={
                    'localization': 'false',
                    'tickers': 'false',
                    'market_data': 'true',
                    'community_data': 'false',
                    'developer_data': 'false'
                })

                if response.status_code == 200:
                    cg_data = response.json()
                    market_data = cg_data.get('market_data', {})
                    market_cap = market_data.get('market_cap', {}).get('usd')
                    daily_volume = market_data.get('total_volume', {}).get('usd')

            # If we still don't have data, try simple search
            if not market_cap:
                url = f"https://api.coingecko.com/api/v3/search?query={token_symbol}"
                response = requests.get(url, timeout=5)
                if response.status_code == 200:
                    search_data = response.json()
                    coins = search_data.get('coins', [])
                    if coins:
                        # Get first matching coin
                        coin = coins[0]
                        coingecko_id = coin.get('id')
                        # Fetch full data
                        if coingecko_id:
                            url = f"https://api.coingecko.com/api/v3/coins/{coingecko_id}"
                            response = requests.get(url, timeout=10, params={
                                'localization': 'false',
                                'tickers': 'false',
                                'market_data': 'true',
                                'community_data': 'false',
                                'developer_data': 'false'
                            })
                            if response.status_code == 200:
                                cg_data = response.json()
                                market_data = cg_data.get('market_data', {})
                                market_cap = market_data.get('market_cap', {}).get('usd')
                                daily_volume = market_data.get('total_volume', {}).get('usd')

            # Calculate NVT if we have both values
            if market_cap and daily_volume and daily_volume > 0:
                nvt = market_cap / daily_volume

                # Determine signal and bonus
                if nvt > 100:
                    return {
                        "nvt_ratio": nvt,
                        "signal": "OVERVALUED",
                        "short_bonus": 0.5,
                        "description": "Extremely overvalued - price exceeds utility"
                    }
                elif nvt > 50:
                    return {
                        "nvt_ratio": nvt,
                        "signal": "MODERATE_OVERVALUED",
                        "short_bonus": 0.25,
                        "description": "Moderately overvalued"
                    }
                else:
                    return {
                        "nvt_ratio": nvt,
                        "signal": "FAIR",
                        "short_bonus": 0,
                        "description": "Fairly valued"
                    }

            return None

        except Exception as e:
            logger.debug(f"   NVT ratio fetch failed: {e}")
            return None

    def _fetch_tvl_trend(self, token_symbol: str, days: int = 7) -> Optional[Dict]:
        """
        Session 234: Fetch TVL (Total Value Locked) trend from DeFiLlama.
        Learning 026: Declining TVL = loss of confidence = SHORT signal

        Only applies to tokens with DeFi utility (DEX, lending, yield, etc.)

        For TGE shorts:
        - TVL declining >20% (7d): STRONG short signal
        - TVL declining 10-20% (7d): MODERATE short signal
        - TVL stable or growing: No signal

        Data source: DeFiLlama API (free, no key required)

        Returns:
            Dict with tvl_applicable, trend_pct, tvl_trend, and short_bonus
        """
        import requests

        try:
            # Step 1: Check if token is a DeFi protocol on DeFiLlama
            # Try to find protocol by symbol/name
            url = "https://api.llama.fi/protocols"
            response = requests.get(url, timeout=10)

            if response.status_code != 200:
                return {"tvl_applicable": False, "reason": "DeFiLlama API unavailable"}

            protocols = response.json()

            # Search for matching protocol
            protocol = None
            symbol_lower = token_symbol.lower()

            for p in protocols:
                # Match by symbol or name
                if p.get('symbol', '').lower() == symbol_lower:
                    protocol = p
                    break
                if symbol_lower in p.get('name', '').lower():
                    protocol = p
                    break

            if not protocol:
                return {"tvl_applicable": False, "reason": "Not a DeFi protocol"}

            # Step 2: Get TVL history for the protocol
            protocol_slug = protocol.get('slug')
            if not protocol_slug:
                return {"tvl_applicable": False, "reason": "Protocol slug not found"}

            url = f"https://api.llama.fi/protocol/{protocol_slug}"
            response = requests.get(url, timeout=10)

            if response.status_code != 200:
                return {"tvl_applicable": False, "reason": "Protocol data unavailable"}

            protocol_data = response.json()
            tvl_history = protocol_data.get('tvl', [])

            if not tvl_history or len(tvl_history) < 2:
                return {"tvl_applicable": False, "reason": "Insufficient TVL history"}

            # Step 3: Calculate TVL trend over specified days
            current_tvl = tvl_history[-1].get('totalLiquidityUSD', 0)

            # Find TVL from N days ago
            target_timestamp = time.time() - (days * 24 * 3600)
            past_tvl = None

            for entry in reversed(tvl_history):
                if entry.get('date', 0) <= target_timestamp:
                    past_tvl = entry.get('totalLiquidityUSD', 0)
                    break

            if not past_tvl or past_tvl == 0:
                # Use oldest available if not enough history
                past_tvl = tvl_history[0].get('totalLiquidityUSD', 0)

            if past_tvl == 0:
                return {"tvl_applicable": False, "reason": "No historical TVL data"}

            # Calculate percentage change
            trend_pct = ((current_tvl - past_tvl) / past_tvl) * 100

            # Determine signal and bonus
            if trend_pct < -20:
                return {
                    "tvl_applicable": True,
                    "trend_pct": trend_pct,
                    "tvl_trend": "DECLINING_FAST",
                    "short_bonus": 0.5,
                    "current_tvl": current_tvl,
                    "past_tvl": past_tvl,
                    "days": days,
                    "description": f"TVL down {abs(trend_pct):.1f}% in {days}d - loss of confidence"
                }
            elif trend_pct < -10:
                return {
                    "tvl_applicable": True,
                    "trend_pct": trend_pct,
                    "tvl_trend": "DECLINING",
                    "short_bonus": 0.25,
                    "current_tvl": current_tvl,
                    "past_tvl": past_tvl,
                    "days": days,
                    "description": f"TVL down {abs(trend_pct):.1f}% in {days}d - moderate decline"
                }
            else:
                return {
                    "tvl_applicable": True,
                    "trend_pct": trend_pct,
                    "tvl_trend": "STABLE",
                    "short_bonus": 0,
                    "current_tvl": current_tvl,
                    "past_tvl": past_tvl,
                    "days": days,
                    "description": f"TVL stable/growing ({trend_pct:+.1f}% in {days}d)"
                }

        except Exception as e:
            logger.debug(f"   TVL trend fetch failed: {e}")
            return {"tvl_applicable": False, "reason": f"API error: {e}"}

    def _empty_advanced(self) -> Dict:
        """Return empty advanced TA structure."""
        return {
            "order_book_imbalance": None,
            "order_book_signal": "UNKNOWN",
            "bid_liquidity": None,
            "ask_liquidity": None,
            "volume_24h": None,
            "volume_trend": "unknown",
            "funding_rate": None,
            "funding_signal": "UNKNOWN",
            "funding_trend": "unknown",
            "funding_velocity": None,  # Session 104
            "open_interest": None,
            "oi_change_24h": None,
            "oi_trend": "unknown",
            "long_liquidations_24h": None,
            "short_liquidations_24h": None,
            "liquidation_cascade_fuel": "UNKNOWN",
            # Session 103 Gemini: VWAP and Crowded Trade Detection
            "vwap_price": None,
            "vwap_signal": "UNKNOWN",
            "crowded_trade_signal": "NEUTRAL",
            # Session 104: CEX vs DEX Delta
            "cex_dex_delta_pct": None,
            "cex_dex_signal": "UNKNOWN",
            # Session 104: OTC Volume Trend
            "otc_volume_trend_pct": None,
            "otc_trend_signal": "UNKNOWN",
            # Session 209: Candlestick Pattern Detection (Learning 022)
            "candlestick_signal": "UNKNOWN",
            "candlestick_patterns": [],
            "candlestick_score_adjustment": 0.0,
            "candlestick_recommendation": "WAIT",
            # Session 230: Support/Resistance Detection (Learning 023)
            "sr_signal": "UNKNOWN",
            "sr_position": "UNKNOWN",
            "sr_favorability": "NEUTRAL",
            "sr_score_adjustment": 0.0,
            "sr_nearest_resistance": None,
            "sr_nearest_support": None,
            "sr_trendlines": {},
            "sr_role_reversal": {},
            "sr_signals": [],
            # Session 234: NVT Ratio (Learning 026)
            "nvt_ratio": None,
            "nvt_signal": "UNKNOWN",
            "nvt_score_adjustment": 0.0,
            # Session 234: TVL Trend (Learning 026)
            "tvl_trend_pct": None,
            "tvl_signal": "UNKNOWN",
            "tvl_score_adjustment": 0.0
        }


def _detect_exchange_for_token(token_symbol: str) -> str:
    """
    Detect which exchange has the token listed.

    Session 105: Added to fix POWER TA collection - token was on MEXC but
    aggregator defaulted to Binance.

    Checks in order: binance, mexc, bybit, gate (most liquid to less liquid)

    Returns: exchange_id string (e.g., 'mexc')
    """
    import ccxt

    exchanges_to_try = ['binance', 'mexc', 'bybit', 'gate']
    # Session 121: Try perpetual first (for shorting), then spot
    pairs = [f"{token_symbol}/USDT:USDT", f"{token_symbol}/USDT"]

    for exchange_id in exchanges_to_try:
        try:
            exchange_class = getattr(ccxt, exchange_id if exchange_id != 'gate' else 'gateio')
            exchange = exchange_class({'enableRateLimit': True})

            for pair in pairs:
                try:
                    # Try to fetch ticker (faster than OHLCV)
                    ticker = exchange.fetch_ticker(pair)
                    if ticker and ticker.get('last'):
                        market_type = "PERP" if ":USDT" in pair else "SPOT"
                        logger.info(f"[DETECT] Found {token_symbol} on {exchange_id} @ ${ticker['last']:.6f} [{market_type}]")
                        return exchange_id
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f"[DETECT] {token_symbol} not found on {exchange_id}: {e}")
            continue

    # Default to binance if not found anywhere (for BTC/ETH macro)
    logger.warning(f"[DETECT] {token_symbol} not found on any exchange, defaulting to binance")
    return 'binance'


def main():
    """CLI test for TA aggregator."""
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format='%(message)s'
    )

    # Get token symbol from args
    token_symbol = sys.argv[1].upper() if len(sys.argv) > 1 else None

    # Optional: Get exchange from args (e.g., python ta_aggregator.py POWER mexc)
    exchange_id = sys.argv[2].lower() if len(sys.argv) > 2 else None

    print(f"\n{'='*80}")
    print(f"TA DATA AGGREGATOR - Agent 4 Execution Check")
    print(f"{'='*80}")
    print(f"Token: {token_symbol or 'MACRO ONLY'}\n")

    # Auto-detect exchange if not specified
    if token_symbol and not exchange_id:
        print(f"[AUTO-DETECT] Searching for {token_symbol} across exchanges...")
        exchange_id = _detect_exchange_for_token(token_symbol)
        print(f"[AUTO-DETECT] Using exchange: {exchange_id}\n")

    # Collect TA data with correct exchange
    aggregator = TADataAggregator(exchange_id=exchange_id or 'binance')
    ta_data = aggregator.collect_all(token_symbol)

    # Display results
    print(f"\n{'='*80}")
    print("MACRO INDICES (7)")
    print(f"{'='*80}")
    macro = ta_data['macro_indices']
    print(f"  BTC Structure:     {macro.get('btc_market_structure', 'N/A')}")
    print(f"  ETH Structure:     {macro.get('eth_market_structure', 'N/A')}")
    print(f"  Fear & Greed:      {macro.get('fear_greed_index', 'N/A')} ({macro.get('fear_greed_label', 'N/A')})")
    print(f"  USDT Dominance:    {macro.get('usdt_dominance', 'N/A')}% ({macro.get('usdt_signal', 'N/A')})")
    print(f"  BTC Dominance:     {macro.get('btc_dominance', 'N/A')}%")
    total_mc = macro.get('total_market_cap')
    print(f"  Total MC:          ${total_mc/1e12:.2f}T" if total_mc else "  Total MC:          N/A")
    alt_mc = macro.get('altcoin_market_cap')
    print(f"  Altcoin MC:        ${alt_mc/1e9:.0f}B" if alt_mc else "  Altcoin MC:        N/A")

    print(f"\n{'='*80}")
    print("CORE TA (5)")
    print(f"{'='*80}")
    core = ta_data['core_ta']
    print(f"  RSI 1h:            {core.get('rsi_1h', 'N/A')}")
    print(f"  RSI 4h:            {core.get('rsi_4h', 'N/A')}")
    print(f"  RSI Signal:        {core.get('rsi_signal', 'N/A')}")
    print(f"  Price vs MA20:     {core.get('price_vs_ma20', 'N/A')}")
    print(f"  Volatility:        {core.get('volatility_24h', 'N/A')}")
    print(f"  Current Price:     ${core.get('current_price', 'N/A')}")

    print(f"\n{'='*80}")
    print("ADVANCED TA (9)")
    print(f"{'='*80}")
    adv = ta_data['advanced_ta']
    print(f"  Order Book:        {adv.get('order_book_imbalance', 'N/A')} ({adv.get('order_book_signal', 'N/A')})")
    vol = adv.get('volume_24h')
    print(f"  Volume 24h:        {vol:,.0f} ({adv.get('volume_trend', 'N/A')})" if vol else f"  Volume 24h:        N/A ({adv.get('volume_trend', 'N/A')})")
    fr = adv.get('funding_rate')
    print(f"  Funding Rate:      {fr*100:.4f}% ({adv.get('funding_signal', 'N/A')}, {adv.get('funding_trend', 'N/A')})" if fr else f"  Funding Rate:      N/A ({adv.get('funding_signal', 'N/A')})")
    oi = adv.get('open_interest')
    print(f"  Open Interest:     {oi:,.0f} ({adv.get('oi_trend', 'N/A')})" if oi else f"  Open Interest:     N/A")
    oi_change = adv.get('oi_change_24h')
    print(f"  OI Change 24h:     {oi_change:+.2f}%" if oi_change else f"  OI Change 24h:     N/A")
    long_liqs = adv.get('long_liquidations_24h')
    print(f"  Long Liqs 24h:     ${long_liqs:,.0f}" if long_liqs else f"  Long Liqs 24h:     N/A (needs CoinGlass API)")
    short_liqs = adv.get('short_liquidations_24h')
    print(f"  Short Liqs 24h:    ${short_liqs:,.0f}" if short_liqs else f"  Short Liqs 24h:    N/A (needs CoinGlass API)")
    print(f"  Cascade Fuel:      {adv.get('liquidation_cascade_fuel', 'N/A')}")
    # Session 103 Gemini: VWAP and Crowded Trade Detection
    vwap = adv.get('vwap_price')
    print(f"  VWAP (24h):        ${vwap:.6f} ({adv.get('vwap_signal', 'N/A')})" if vwap else f"  VWAP (24h):        N/A ({adv.get('vwap_signal', 'N/A')})")
    # Session 243: Extended VWAP (Learning 028 - Sherlock Methodology)
    vwap_w = adv.get('vwap_weekly')
    vwap_m = adv.get('vwap_monthly')
    vwap_q = adv.get('vwap_quarterly')
    vwap_y = adv.get('vwap_yearly')
    print(f"  VWAP (Weekly):     ${vwap_w:.6f}" if vwap_w else f"  VWAP (Weekly):     N/A")
    print(f"  VWAP (Monthly):    ${vwap_m:.6f}" if vwap_m else f"  VWAP (Monthly):    N/A")
    print(f"  VWAP (Quarterly):  ${vwap_q:.6f}" if vwap_q else f"  VWAP (Quarterly):  N/A")
    print(f"  VWAP (Yearly):     ${vwap_y:.6f}" if vwap_y else f"  VWAP (Yearly):     N/A")
    print(f"  VWAP Confluence:   {adv.get('vwap_confluence_score', 0)}/5 ({adv.get('vwap_confluence_signal', 'N/A')})")
    print(f"  Crowded Trade:     {adv.get('crowded_trade_signal', 'N/A')}")
    # Session 104: Funding velocity, CEX/DEX delta, and OTC trend
    fv = adv.get('funding_velocity')
    print(f"  Funding Velocity:  {fv:+.2f}x" if fv else f"  Funding Velocity:  N/A")
    cex_dex = adv.get('cex_dex_delta_pct')
    print(f"  CEX/DEX Delta:     {cex_dex:+.2f}% ({adv.get('cex_dex_signal', 'N/A')})" if cex_dex else f"  CEX/DEX Delta:     N/A ({adv.get('cex_dex_signal', 'N/A')})")
    otc_trend = adv.get('otc_volume_trend_pct')
    print(f"  OTC Volume Trend:  {otc_trend:+.2f}% ({adv.get('otc_trend_signal', 'N/A')})" if otc_trend else f"  OTC Volume Trend:  N/A ({adv.get('otc_trend_signal', 'N/A')})")
    # Session 234: NVT Ratio and TVL Trend (Learning 026)
    nvt = adv.get('nvt_ratio')
    print(f"  NVT Ratio:         {nvt:.1f} ({adv.get('nvt_signal', 'N/A')})" if nvt else f"  NVT Ratio:         N/A ({adv.get('nvt_signal', 'N/A')})")
    tvl_trend = adv.get('tvl_trend_pct')
    print(f"  TVL Trend (7d):    {tvl_trend:+.1f}% ({adv.get('tvl_signal', 'N/A')})" if tvl_trend is not None else f"  TVL Trend (7d):    N/A ({adv.get('tvl_signal', 'N/A')})")

    print(f"\n{'='*80}")
    print("AGGREGATE SCORE")
    print(f"{'='*80}")
    print(f"  TA Score:          {ta_data['ta_score_normalized']}/10")
    print(f"  Recommendation:    {ta_data['ta_recommendation']}")
    print(f"{'='*80}\n")

    # Save to JSON
    output_file = f"ta_snapshot_{token_symbol or 'MACRO'}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, 'w') as f:
        json.dump(ta_data, f, indent=2, default=str)
    print(f"Saved to: {output_file}\n")


if __name__ == "__main__":
    main()
