#!/usr/bin/env python3
"""
Price Action Analyzer - Automated TA Entry Timing

DEPRECATED: Use src.analysis module instead.
Session 256: Marked for migration to src/analysis/

Extracts real-time price data and technical indicators to score entry timing.
Complements fundamental analysis with technical analysis.

Author: Claude Code
Date: 2025-11-25
Purpose: Solve the "IRYS gap" - fundamental conviction vs entry timing disconnect

Data Sources:
1. CoinGecko - Price, OHLCV, market data (free, no API key)
2. Binance CCXT - Real-time price, OHLCV, orderbook (free, no API key)
3. TradingView (via WebFetch) - RSI, MACD, indicators (scraping)
4. USDT.D - Macro indicator for alt season timing

Outputs:
- Entry Readiness Score (0-10)
- Current price, RSI, trend, market structure
- Fibonacci levels
- USDT.D status
- Recommendation: READY or WAIT
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import ccxt

# Initialize logger first (before any usage)
logger = logging.getLogger(__name__)

# Week 1: David's 7-Index Framework
# Use try/except to handle both standalone and module import paths
try:
    from src.data.indices_tracker import IndicesTracker
except ModuleNotFoundError:
    from indices_tracker import IndicesTracker

# Session 265: Redis LMS integration for <1s price lookups
try:
    from src.utils.redis_lms import get_current_price, is_redis_lms_available
    REDIS_LMS_AVAILABLE = True
except ImportError:
    REDIS_LMS_AVAILABLE = False
    logger.debug("⚠️ Redis LMS not available - using REST API only")


class PriceActionAnalyzer:
    """
    Analyzes price action and technical indicators for entry timing.

    Scoring Components (0-10 scale for SHORT entries):
    1. RSI (3 points) - Overbought = good for short
    2. Trend (3 points) - Downtrend = good for short
    3. Market Structure (2 points) - Lower lows = good for short
    4. Fib Position (2 points) - Below key fib = good for short
    5. USDT.D (bonus) - Macro timing confirmation
    """

    def __init__(self, exchange_id: str = "binance"):
        """
        Initialize price action analyzer with exchange.

        Args:
            exchange_id: CCXT exchange ID (binance, gate, mexc, bybit, etc.)
        """
        # Map exchange IDs to CCXT classes
        exchanges = {
            'binance': ccxt.binance,
            'mexc': ccxt.mexc,
            'gate': ccxt.gateio,
            'bybit': ccxt.bybit,
            'coinbase': ccxt.coinbase,
            'hyperliquid': ccxt.hyperliquid,
            'blofin': ccxt.blofin
        }

        exchange_class = exchanges.get(exchange_id, ccxt.binance)
        self.exchange = exchange_class({'enableRateLimit': True})
        self.coingecko_cache = {}

    def analyze(self, token_symbol: str, analysis_type: str = "SHORT") -> Dict:
        """
        Full price action analysis for entry timing.

        Args:
            token_symbol: Token symbol (e.g., "IRYS")
            analysis_type: "SHORT" or "LONG"

        Returns:
            Dict with:
                - entry_readiness_score: 0-10 (how ready for entry)
                - current_price: Current token price
                - rsi_1h: RSI on 1h timeframe
                - rsi_4h: RSI on 4h timeframe
                - trend: "uptrend", "downtrend", "ranging"
                - structure: "higher_highs", "lower_lows", "ranging"
                - fib_levels: Fibonacci retracement levels
                - price_vs_fib: Position relative to key fib (0.618)
                - usdt_d: USDT Dominance data
                - btc_market_structure: BTC trend ("uptrend", "downtrend", "sideways")
                - eth_market_structure: ETH trend ("uptrend", "downtrend", "sideways")
                - macro_market_conditions: Overall market ("bullish", "neutral", "bearish")
                - recommendation: "READY" or "WAIT"
                - reasoning: List of factors
        """
        logger.info(f"🔍 Analyzing price action for {token_symbol} ({analysis_type})")

        results = {
            'token_symbol': token_symbol,
            'analysis_type': analysis_type,
            'timestamp': datetime.utcnow().isoformat(),
        }

        try:
            # 1. ALWAYS analyze BTC/ETH/macro first (works for all tokens, even pre-launch)
            logger.info(f"   🌐 Analyzing macro market context (7 indices)...")

            # Fetch ALL 7 indices (Week 1 implementation)
            tracker = IndicesTracker()
            indices_data = tracker.fetch_all_indices()

            # Extract individual indices
            btc_d = indices_data['indices'].get('btc_d', {})
            usdt_d = indices_data['indices'].get('usdt_d', {})
            stables_d = indices_data['indices'].get('stables_c_d', {})
            others_d = indices_data['indices'].get('others_d', {})
            total = indices_data['indices'].get('total', {})
            total2 = indices_data['indices'].get('total2', {})
            total3 = indices_data['indices'].get('total3', {})
            fear_greed = indices_data['indices'].get('fear_greed_index', {})

            # Analyze BTC/ETH structure (existing code)
            btc_structure = self._analyze_btc()
            eth_structure = self._analyze_eth()
            macro_conditions = self._calculate_macro_conditions(btc_structure, eth_structure, usdt_d)

            # Add ALL indices to results (including Fear & Greed)
            results['indices'] = {
                'btc_d': btc_d,
                'usdt_d': usdt_d,
                'stables_c_d': stables_d,
                'others_d': others_d,
                'total': total,
                'total2': total2,
                'total3': total3,
                'fear_greed_index': fear_greed,
                'macro_signal': indices_data.get('macro_signal', 'UNKNOWN')
            }
            results['usdt_d'] = usdt_d  # Keep for backward compatibility
            results['btc_market_structure'] = btc_structure
            results['eth_market_structure'] = eth_structure
            results['macro_market_conditions'] = macro_conditions

            logger.info(f"   🌐 Macro: BTC={btc_structure}, ETH={eth_structure}, Conditions={macro_conditions}")
            logger.info(f"   🌍 USDT.D: {usdt_d.get('value', 'N/A')}% - {usdt_d.get('signal', 'N/A')}")
            logger.info(f"   📊 Indices Signal: {indices_data.get('macro_signal', 'UNKNOWN')}")

            # 2. Fetch token-specific OHLCV data (may not exist for pre-launch tokens)
            logger.info(f"   📊 Fetching OHLCV data for {token_symbol}...")
            ohlcv_1h = self._fetch_ohlcv(token_symbol, '1h', limit=100)
            ohlcv_4h = self._fetch_ohlcv(token_symbol, '4h', limit=100)

            if not ohlcv_1h:
                logger.warning(f"   ⚠️  No OHLCV data for {token_symbol} (pre-launch or not listed)")
                logger.info(f"   ℹ️  Returning macro-only analysis")
                results['entry_readiness_score'] = 5.0  # Neutral
                results['recommendation'] = 'WAIT'
                results['reasoning'] = [f"⚠️ {token_symbol} not yet trading - macro analysis only"]
                return results

            # 2. Current price
            current_price = ohlcv_1h[-1][4]  # Close price of latest candle
            results['current_price'] = current_price
            logger.info(f"   💰 Current price: ${current_price:.4f}")

            # 3. Calculate RSI
            rsi_1h = self._calculate_rsi(ohlcv_1h)
            rsi_4h = self._calculate_rsi(ohlcv_4h) if ohlcv_4h else None
            results['rsi_1h'] = rsi_1h
            results['rsi_4h'] = rsi_4h
            rsi_4h_str = f"{rsi_4h:.1f}" if rsi_4h else "N/A"
            logger.info(f"   📈 RSI: 1h={rsi_1h:.1f}, 4h={rsi_4h_str}")

            # 4. Detect trend
            trend = self._detect_trend(ohlcv_1h)
            results['trend'] = trend
            logger.info(f"   📊 Trend: {trend}")

            # 5. Analyze market structure
            structure = self._analyze_market_structure(ohlcv_1h)
            results['structure'] = structure
            logger.info(f"   🏗️  Market structure: {structure}")

            # 6. Calculate Fibonacci levels
            fib_levels = self._calculate_fibonacci(ohlcv_1h)
            results['fib_levels'] = fib_levels
            price_vs_fib = self._price_vs_fib(current_price, fib_levels)
            results['price_vs_fib'] = price_vs_fib
            logger.info(f"   🎯 Fib position: {price_vs_fib}")

            # 7. Calculate entry readiness score (macro already analyzed above)
            score, reasoning = self._calculate_entry_score(
                rsi_1h, rsi_4h, trend, structure, price_vs_fib, usdt_d, analysis_type
            )
            results['entry_readiness_score'] = score
            results['reasoning'] = reasoning
            results['recommendation'] = 'READY' if score >= 7.0 else 'WAIT'

            logger.info(f"   ✅ Entry readiness: {score:.1f}/10 - {results['recommendation']}")

            return results

        except Exception as e:
            logger.error(f"   ❌ Analysis failed: {e}")
            return self._error_result(token_symbol, str(e))

    def _fetch_ohlcv(self, token_symbol: str, timeframe: str, limit: int = 100) -> Optional[List]:
        """
        Fetch OHLCV data from exchange.

        Session 121: Prioritize perpetual markets (TOKEN/USDT:USDT) for shorting,
        fall back to spot if perp not available.

        Returns: List of [timestamp, open, high, low, close, volume]
        """
        try:
            # Session 121: Try perpetual first (what we actually trade for shorts)
            # then spot as fallback
            pairs = [
                f"{token_symbol}/USDT:USDT",  # Perpetual (priority for shorts)
                f"{token_symbol}/USDT",        # Spot
                f"{token_symbol}/USD",         # Alternative spot
                f"{token_symbol}/BUSD",        # Binance stablecoin
            ]

            for pair in pairs:
                try:
                    ohlcv = self.exchange.fetch_ohlcv(pair, timeframe, limit=limit)
                    if ohlcv:
                        market_type = "PERP" if ":USDT" in pair else "SPOT"
                        logger.info(f"      ✓ Found {len(ohlcv)} candles for {pair} [{market_type}]")
                        return ohlcv
                except Exception as e:
                    logger.debug(f"      ⚠️  {pair} not available: {e}")
                    continue

            logger.warning(f"      ⚠️  No OHLCV data found for {token_symbol}")
            return None

        except Exception as e:
            logger.error(f"      ❌ OHLCV fetch error: {e}")
            return None

    def _calculate_rsi(self, ohlcv: List, period: int = 14) -> float:
        """
        Calculate RSI (Relative Strength Index).

        RSI > 70 = Overbought (good for SHORT)
        RSI < 30 = Oversold (good for LONG)
        """
        if not ohlcv or len(ohlcv) < period + 1:
            return 50.0  # Neutral if not enough data

        closes = [candle[4] for candle in ohlcv]

        # Calculate price changes
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]

        # Separate gains and losses
        gains = [delta if delta > 0 else 0 for delta in deltas]
        losses = [-delta if delta < 0 else 0 for delta in deltas]

        # Calculate average gain/loss
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        # Calculate RS and RSI
        if avg_loss == 0:
            return 100.0  # No losses = max RSI

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        return round(rsi, 2)

    def _detect_trend(self, ohlcv: List, ma_short: int = 20, ma_long: int = 50) -> str:
        """
        Detect trend using moving average crossover.

        Returns: "uptrend", "downtrend", or "sideways"
        """
        if not ohlcv or len(ohlcv) < ma_long:
            return "sideways"

        closes = [candle[4] for candle in ohlcv]

        # Calculate moving averages
        ma_short_val = sum(closes[-ma_short:]) / ma_short
        ma_long_val = sum(closes[-ma_long:]) / ma_long

        current_price = closes[-1]

        # Determine trend
        if ma_short_val > ma_long_val and current_price > ma_short_val:
            return "uptrend"
        elif ma_short_val < ma_long_val and current_price < ma_short_val:
            return "downtrend"
        else:
            return "sideways"

    def _analyze_market_structure(self, ohlcv: List, lookback: int = 20) -> str:
        """
        Analyze market structure (higher highs/lows or lower highs/lows).

        Returns: "higher_highs", "lower_lows", "ranging"
        """
        if not ohlcv or len(ohlcv) < lookback:
            return "ranging"

        recent_candles = ohlcv[-lookback:]
        highs = [candle[2] for candle in recent_candles]
        lows = [candle[3] for candle in recent_candles]

        # Find swing highs and lows (local peaks/troughs)
        swing_highs = []
        swing_lows = []

        for i in range(2, len(recent_candles) - 2):
            # Swing high: higher than 2 candles on each side
            if highs[i] > max(highs[i-2:i]) and highs[i] > max(highs[i+1:i+3]):
                swing_highs.append(highs[i])

            # Swing low: lower than 2 candles on each side
            if lows[i] < min(lows[i-2:i]) and lows[i] < min(lows[i+1:i+3]):
                swing_lows.append(lows[i])

        # Analyze structure
        if len(swing_lows) >= 2:
            # Check if making higher lows (bullish)
            if swing_lows[-1] > swing_lows[-2]:
                return "higher_lows"  # Bullish structure
            elif swing_lows[-1] < swing_lows[-2]:
                return "lower_lows"  # Bearish structure

        if len(swing_highs) >= 2:
            if swing_highs[-1] > swing_highs[-2]:
                return "higher_highs"  # Bullish structure
            elif swing_highs[-1] < swing_highs[-2]:
                return "lower_highs"  # Bearish structure

        return "ranging"

    def _calculate_fibonacci(self, ohlcv: List, lookback: int = 50) -> Dict:
        """
        Calculate Fibonacci retracement levels.

        Based on recent swing high and swing low.
        """
        if not ohlcv or len(ohlcv) < lookback:
            return {}

        recent_candles = ohlcv[-lookback:]
        highs = [candle[2] for candle in recent_candles]
        lows = [candle[3] for candle in recent_candles]

        swing_high = max(highs)
        swing_low = min(lows)
        diff = swing_high - swing_low

        return {
            'swing_high': round(swing_high, 6),
            'swing_low': round(swing_low, 6),
            '0.236': round(swing_high - (diff * 0.236), 6),
            '0.382': round(swing_high - (diff * 0.382), 6),
            '0.5': round(swing_high - (diff * 0.5), 6),
            '0.618': round(swing_high - (diff * 0.618), 6),  # Key level
            '0.65': round(swing_high - (diff * 0.65), 6),    # L054: Sherlock's rejection level
            '0.786': round(swing_high - (diff * 0.786), 6),
        }

    def _price_vs_fib(self, current_price: float, fib_levels: Dict) -> str:
        """
        Determine price position relative to key fib level (0.618).
        """
        if not fib_levels or '0.618' not in fib_levels:
            return "unknown"

        fib_618 = fib_levels['0.618']

        if current_price > fib_618:
            return "above_0.618"  # For SHORT: need to break below
        else:
            return "below_0.618"  # For SHORT: good entry zone

    def _calculate_entry_score(
        self,
        rsi_1h: float,
        rsi_4h: Optional[float],
        trend: str,
        structure: str,
        price_vs_fib: str,
        usdt_d: Dict,
        analysis_type: str
    ) -> Tuple[float, List[str]]:
        """
        Calculate entry readiness score (0-10) for SHORT entries.

        UPDATED 2025-11-28: Removed BTC/ETH from scoring (moved to position sizing)

        Scoring breakdown (Token-specific TA only):
        - RSI overbought (>70): +3 points
        - Downtrend confirmed: +3 points
        - Lower lows structure: +2 points
        - Below 0.618 fib: +2 points
        - USDT.D & BTC/ETH: Informational only (not scored)

        Total: 10 points max
        Thresholds: 6.5-10 READY, 4-6.4 MONITOR, 0-3.9 WAIT
        """
        score = 0.0
        reasoning = []

        if analysis_type == "SHORT":
            # 1. RSI check (3 points max)
            if rsi_1h >= 70:
                score += 3.0
                reasoning.append(f"✅ RSI overbought ({rsi_1h:.1f}) - strong short signal")
            elif rsi_1h >= 60:
                score += 1.5
                reasoning.append(f"⚠️ RSI moderately high ({rsi_1h:.1f}) - weak short signal")
            else:
                reasoning.append(f"❌ RSI not overbought ({rsi_1h:.1f}) - not ideal for short")

            # 2. Trend check (3 points max)
            if trend == "downtrend":
                score += 3.0
                reasoning.append("✅ Downtrend confirmed - aligned with short")
            elif trend == "ranging":
                score += 1.0
                reasoning.append("⚠️ Ranging market - wait for trend confirmation")
            else:
                reasoning.append("❌ Uptrend active - against short thesis")

            # 3. Market structure (2 points max)
            if structure == "lower_lows":
                score += 2.0
                reasoning.append("✅ Lower lows forming - bearish structure")
            elif structure == "lower_highs":
                score += 1.0
                reasoning.append("⚠️ Lower highs but not confirmed - partial bearish")
            else:
                reasoning.append(f"❌ Bullish structure ({structure}) - wait for breakdown")

            # 4. Fib position (2 points max)
            if price_vs_fib == "below_0.618":
                score += 2.0
                reasoning.append("✅ Below 0.618 fib - key level broken")
            else:
                reasoning.append("❌ Above 0.618 fib - need breakdown first")

            # 5. USDT.D check (informational, no points for now)
            if usdt_d.get('signal') == 'BEARISH_FOR_ALTS':
                reasoning.append("✅ USDT.D rising - bearish for alts (supportive)")
            elif usdt_d.get('signal') == 'BULLISH_FOR_ALTS':
                reasoning.append("⚠️ USDT.D falling - bullish for alts (against short)")
            else:
                reasoning.append("ℹ️ USDT.D data not available")

        return round(score, 1), reasoning

    def _analyze_btc(self) -> str:
        """
        Analyze BTC/USDT market structure.

        Uses 20/50 MA crossover to determine trend.

        Returns: "uptrend", "downtrend", or "sideways"
        """
        try:
            logger.info("      📊 Analyzing BTC market structure...")
            ohlcv_btc = self._fetch_ohlcv("BTC", "4h", limit=100)

            if not ohlcv_btc:
                logger.warning("      ⚠️  BTC data unavailable, defaulting to sideways")
                return "sideways"

            trend = self._detect_trend(ohlcv_btc, ma_short=20, ma_long=50)

            logger.info(f"      ✓ BTC trend: {trend}")
            return trend

        except Exception as e:
            logger.warning(f"      ⚠️  BTC analysis failed: {e}, defaulting to sideways")
            return "sideways"

    def _analyze_eth(self) -> str:
        """
        Analyze ETH/USDT market structure.

        Uses 20/50 MA crossover to determine trend.

        Returns: "uptrend", "downtrend", or "sideways"
        """
        try:
            logger.info("      📊 Analyzing ETH market structure...")
            ohlcv_eth = self._fetch_ohlcv("ETH", "4h", limit=100)

            if not ohlcv_eth:
                logger.warning("      ⚠️  ETH data unavailable, defaulting to sideways")
                return "sideways"

            trend = self._detect_trend(ohlcv_eth, ma_short=20, ma_long=50)

            logger.info(f"      ✓ ETH trend: {trend}")
            return trend

        except Exception as e:
            logger.warning(f"      ⚠️  ETH analysis failed: {e}, defaulting to sideways")
            return "sideways"

    def _calculate_macro_conditions(self, btc_trend: str, eth_trend: str, usdt_d: Dict) -> str:
        """
        Calculate macro market conditions from BTC + ETH + USDT.D.

        Logic:
        - BTC down + ETH down + USDT.D rising = BEARISH (best for alt shorts)
        - BTC up + ETH up + USDT.D falling = BULLISH (worst for alt shorts)
        - Mixed signals = NEUTRAL

        Returns: "bullish", "neutral", or "bearish"
        """
        try:
            logger.info("      🌐 Calculating macro market conditions...")

            # Count bearish signals
            bearish_signals = 0
            bullish_signals = 0

            # BTC signal
            if btc_trend == "downtrend":
                bearish_signals += 1
            elif btc_trend == "uptrend":
                bullish_signals += 1

            # ETH signal
            if eth_trend == "downtrend":
                bearish_signals += 1
            elif eth_trend == "uptrend":
                bullish_signals += 1

            # USDT.D signal
            usdt_signal = usdt_d.get('signal', 'NEUTRAL')
            if usdt_signal == 'BEARISH_FOR_ALTS':
                bearish_signals += 1
            elif usdt_signal == 'BULLISH_FOR_ALTS':
                bullish_signals += 1

            # Determine overall conditions
            if bearish_signals >= 2 and bullish_signals == 0:
                result = "bearish"
            elif bullish_signals >= 2 and bearish_signals == 0:
                result = "bullish"
            else:
                result = "neutral"

            logger.info(f"      ✓ Macro conditions: {result} (bearish={bearish_signals}, bullish={bullish_signals})")
            return result

        except Exception as e:
            logger.warning(f"      ⚠️  Macro calculation failed: {e}, defaulting to neutral")
            return "neutral"

    def analyze_btc_pair(self, token_symbol: str) -> Dict:
        """
        L052: Analyze TOKEN/BTC pair for confluence confirmation.

        Sherlock checks TOKEN/BTC chart in addition to TOKEN/USDT for confluence.
        When USDT pair is bearish but BTC pair is bullish, it's WEAK confluence
        (token moving with BTC, not dumping independently).

        Args:
            token_symbol: Token symbol (e.g., "BCH", "LTC", "SOL")

        Returns:
            Dict with:
                - btc_pair_available: bool
                - btc_pair_trend: "uptrend", "downtrend", "sideways"
                - usdt_pair_trend: "uptrend", "downtrend", "sideways"
                - confluence: "STRONG", "WEAK", "CONFLICTING", "UNKNOWN"
                - confluence_multiplier: 1.0 (STRONG), 0.5 (WEAK), 0.25 (CONFLICTING)
                - reasoning: str
        """
        logger.info(f"🔍 L052: Analyzing BTC pair confluence for {token_symbol}")

        result = {
            'token_symbol': token_symbol,
            'btc_pair_available': False,
            'btc_pair_trend': None,
            'usdt_pair_trend': None,
            'confluence': 'UNKNOWN',
            'confluence_multiplier': 1.0,
            'reasoning': '',
            'timestamp': datetime.utcnow().isoformat()
        }

        try:
            # 1. Fetch TOKEN/USDT trend (baseline)
            ohlcv_usdt = self._fetch_ohlcv(token_symbol, '4h', limit=50)
            if ohlcv_usdt:
                usdt_trend = self._detect_trend(ohlcv_usdt, ma_short=20, ma_long=50)
                result['usdt_pair_trend'] = usdt_trend
                logger.info(f"   📊 {token_symbol}/USDT trend: {usdt_trend}")
            else:
                result['reasoning'] = f"{token_symbol}/USDT data not available"
                return result

            # 2. Try to fetch TOKEN/BTC pair
            btc_pairs = [
                f"{token_symbol}/BTC",
                f"{token_symbol}/BTC:BTC",  # Perp on some exchanges
            ]

            ohlcv_btc = None
            for pair in btc_pairs:
                try:
                    ohlcv_btc = self.exchange.fetch_ohlcv(pair, '4h', limit=50)
                    if ohlcv_btc:
                        logger.info(f"   ✓ Found {len(ohlcv_btc)} candles for {pair}")
                        result['btc_pair_available'] = True
                        break
                except Exception:
                    continue

            if not ohlcv_btc:
                result['reasoning'] = f"{token_symbol}/BTC pair not available on {self.exchange.id}"
                result['confluence'] = 'UNKNOWN'
                result['confluence_multiplier'] = 1.0  # No penalty if no BTC pair exists
                logger.info(f"   ⚠️  No BTC pair found for {token_symbol}")
                return result

            # 3. Analyze BTC pair trend
            btc_trend = self._detect_trend(ohlcv_btc, ma_short=20, ma_long=50)
            result['btc_pair_trend'] = btc_trend
            logger.info(f"   📊 {token_symbol}/BTC trend: {btc_trend}")

            # 4. Determine confluence (for SHORT trades)
            # STRONG: Both pairs bearish (token dumping vs USD AND vs BTC)
            # WEAK: USDT bearish but BTC bullish (token moving with BTC)
            # CONFLICTING: USDT bullish but BTC bearish (unusual)
            if usdt_trend == 'downtrend' and btc_trend == 'downtrend':
                result['confluence'] = 'STRONG'
                result['confluence_multiplier'] = 1.0
                result['reasoning'] = f"STRONG confluence: {token_symbol} bearish vs both USD and BTC"
            elif usdt_trend == 'downtrend' and btc_trend == 'uptrend':
                result['confluence'] = 'WEAK'
                result['confluence_multiplier'] = 0.5
                result['reasoning'] = f"WEAK confluence: {token_symbol} moving with BTC, not dumping independently"
            elif usdt_trend == 'uptrend' and btc_trend == 'downtrend':
                result['confluence'] = 'CONFLICTING'
                result['confluence_multiplier'] = 0.25
                result['reasoning'] = f"CONFLICTING: {token_symbol}/USDT up but {token_symbol}/BTC down (unusual)"
            elif usdt_trend == 'downtrend' and btc_trend == 'sideways':
                result['confluence'] = 'MODERATE'
                result['confluence_multiplier'] = 0.75
                result['reasoning'] = f"MODERATE: {token_symbol}/USDT bearish, BTC pair ranging"
            else:
                result['confluence'] = 'NEUTRAL'
                result['confluence_multiplier'] = 0.75
                result['reasoning'] = f"NEUTRAL: No strong confluence ({usdt_trend} vs {btc_trend})"

            logger.info(f"   🎯 Confluence: {result['confluence']} (multiplier: {result['confluence_multiplier']})")

            return result

        except Exception as e:
            logger.error(f"   ❌ BTC pair analysis failed: {e}")
            result['reasoning'] = f"Analysis failed: {str(e)}"
            return result

    def _error_result(self, token_symbol: str, error_msg: str) -> Dict:
        """Return error result with neutral score."""
        return {
            'token_symbol': token_symbol,
            'error': error_msg,
            'entry_readiness_score': 5.0,  # Neutral
            'recommendation': 'WAIT',
            'reasoning': [f"❌ Analysis failed: {error_msg}"],
            'timestamp': datetime.utcnow().isoformat()
        }

    def calculate_tvem_bands(
        self,
        token_symbol: str,
        timeframe: str = '4h',
        ema_period: int = 12,
        std_multiplier: float = 2.0,
        limit: int = 100
    ) -> Dict:
        """
        L058: Calculate TVEM Bands (Trailing VWAP + EMA with Standard Deviation).

        Based on Sherlockwhale's TradingView indicator (TVEM BANDS):
        - TVEM = Average of (Trailing VWAP + EMA)
        - Upper Band = TVEM + (StdDev * multiplier)
        - Lower Band = TVEM - (StdDev * multiplier)

        Sherlock uses this as dynamic support/resistance for confluence.

        Args:
            token_symbol: Token symbol (e.g., "SOL", "ETH")
            timeframe: OHLCV timeframe ('1h', '4h', '1d')
            ema_period: EMA period (default 12, Sherlock's preference)
            std_multiplier: Standard deviation multiplier for bands (default 2.0)
            limit: Number of candles to fetch

        Returns:
            Dict with:
                - tvem_mid: Current TVEM mid line value
                - tvem_upper: Upper band value
                - tvem_lower: Lower band value
                - current_price: Current price
                - price_position: "ABOVE_UPPER", "ABOVE_MID", "BELOW_MID", "BELOW_LOWER"
                - distance_to_mid_pct: Distance from price to mid band (%)
                - at_lower_band: True if price within 2% of lower band
                - at_upper_band: True if price within 2% of upper band
                - signal: "BULLISH_RETEST", "BEARISH_RETEST", "NEUTRAL"
        """
        logger.info(f"🎯 L058: Calculating TVEM Bands for {token_symbol} ({timeframe})")

        result = {
            'token_symbol': token_symbol,
            'timeframe': timeframe,
            'tvem_mid': None,
            'tvem_upper': None,
            'tvem_lower': None,
            'trailing_vwap': None,
            'ema_value': None,
            'current_price': None,
            'price_position': 'UNKNOWN',
            'distance_to_mid_pct': None,
            'at_lower_band': False,
            'at_upper_band': False,
            'signal': 'NEUTRAL',
            'timestamp': datetime.utcnow().isoformat()
        }

        try:
            # 1. Fetch OHLCV data
            ohlcv = self._fetch_ohlcv(token_symbol, timeframe, limit=limit)
            if not ohlcv or len(ohlcv) < ema_period + 10:
                logger.warning(f"   ⚠️  Insufficient data for TVEM calculation")
                return result

            # Extract price data
            closes = [candle[4] for candle in ohlcv]  # Close prices
            highs = [candle[2] for candle in ohlcv]   # High prices
            lows = [candle[3] for candle in ohlcv]    # Low prices
            volumes = [candle[5] for candle in ohlcv] # Volumes

            current_price = closes[-1]
            result['current_price'] = current_price

            # 2. Calculate Trailing VWAP
            # VWAP = Sum(Typical Price * Volume) / Sum(Volume)
            # Typical Price = (High + Low + Close) / 3
            typical_prices = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]

            # Calculate cumulative VWAP (trailing from period start)
            cum_tp_vol = 0
            cum_vol = 0
            vwap_values = []
            for i in range(len(typical_prices)):
                cum_tp_vol += typical_prices[i] * volumes[i]
                cum_vol += volumes[i]
                vwap_values.append(cum_tp_vol / cum_vol if cum_vol > 0 else typical_prices[i])

            trailing_vwap = vwap_values[-1]
            result['trailing_vwap'] = round(trailing_vwap, 6)

            # 3. Calculate EMA
            ema_values = self._calculate_ema(closes, ema_period)
            ema_value = ema_values[-1] if ema_values else closes[-1]
            result['ema_value'] = round(ema_value, 6)

            # 4. Calculate TVEM (average of VWAP and EMA)
            tvem_values = []
            for i in range(len(closes)):
                vwap_i = vwap_values[i] if i < len(vwap_values) else trailing_vwap
                ema_i = ema_values[i] if i < len(ema_values) else ema_value
                tvem_values.append((vwap_i + ema_i) / 2)

            tvem_mid = tvem_values[-1]
            result['tvem_mid'] = round(tvem_mid, 6)

            # 5. Calculate Standard Deviation of TVEM values
            if len(tvem_values) >= 20:
                recent_tvem = tvem_values[-20:]
                mean_tvem = sum(recent_tvem) / len(recent_tvem)
                variance = sum((x - mean_tvem) ** 2 for x in recent_tvem) / len(recent_tvem)
                std_dev = variance ** 0.5
            else:
                # Fallback: use price std dev
                mean_price = sum(closes[-20:]) / min(20, len(closes))
                variance = sum((x - mean_price) ** 2 for x in closes[-20:]) / min(20, len(closes))
                std_dev = variance ** 0.5

            # 6. Calculate bands
            tvem_upper = tvem_mid + (std_dev * std_multiplier)
            tvem_lower = tvem_mid - (std_dev * std_multiplier)

            result['tvem_upper'] = round(tvem_upper, 6)
            result['tvem_lower'] = round(tvem_lower, 6)

            # 7. Determine price position
            tolerance = 0.02  # 2% tolerance for "at band"

            if current_price > tvem_upper:
                result['price_position'] = 'ABOVE_UPPER'
            elif current_price > tvem_mid:
                result['price_position'] = 'ABOVE_MID'
            elif current_price > tvem_lower:
                result['price_position'] = 'BELOW_MID'
            else:
                result['price_position'] = 'BELOW_LOWER'

            # Check if at bands (within tolerance)
            dist_to_lower = abs(current_price - tvem_lower) / tvem_lower if tvem_lower > 0 else 0
            dist_to_upper = abs(current_price - tvem_upper) / tvem_upper if tvem_upper > 0 else 0
            dist_to_mid = abs(current_price - tvem_mid) / tvem_mid if tvem_mid > 0 else 0

            result['at_lower_band'] = dist_to_lower <= tolerance
            result['at_upper_band'] = dist_to_upper <= tolerance
            result['distance_to_mid_pct'] = round(dist_to_mid * 100, 2)

            # 8. Generate signal
            if result['at_lower_band']:
                result['signal'] = 'BULLISH_RETEST'  # Price at lower band = potential long
                logger.info(f"   📈 BULLISH_RETEST: Price at TVEM lower band")
            elif result['at_upper_band']:
                result['signal'] = 'BEARISH_RETEST'  # Price at upper band = potential short
                logger.info(f"   📉 BEARISH_RETEST: Price at TVEM upper band")
            elif result['price_position'] == 'BELOW_LOWER':
                result['signal'] = 'OVERSOLD'  # Below lower band = oversold
            elif result['price_position'] == 'ABOVE_UPPER':
                result['signal'] = 'OVERBOUGHT'  # Above upper band = overbought
            else:
                result['signal'] = 'NEUTRAL'

            logger.info(f"   ✓ TVEM Mid: ${tvem_mid:.6f}, Upper: ${tvem_upper:.6f}, Lower: ${tvem_lower:.6f}")
            logger.info(f"   ✓ Current: ${current_price:.6f}, Position: {result['price_position']}")

            return result

        except Exception as e:
            logger.error(f"   ❌ TVEM Band calculation failed: {e}")
            return result

    def _calculate_ema(self, prices: List[float], period: int) -> List[float]:
        """
        Calculate Exponential Moving Average.

        Args:
            prices: List of prices
            period: EMA period

        Returns:
            List of EMA values (same length as prices)
        """
        if not prices or len(prices) < period:
            return prices

        ema_values = []
        multiplier = 2 / (period + 1)

        # Start with SMA for first EMA value
        sma = sum(prices[:period]) / period
        ema_values = [None] * (period - 1) + [sma]

        # Calculate EMA for remaining prices
        for i in range(period, len(prices)):
            ema = (prices[i] * multiplier) + (ema_values[-1] * (1 - multiplier))
            ema_values.append(ema)

        # Fill None values with first valid EMA
        first_valid = next((v for v in ema_values if v is not None), prices[0])
        ema_values = [first_valid if v is None else v for v in ema_values]

        return ema_values

    def check_tvem_confluence(
        self,
        token_symbol: str,
        support_level: Optional[float] = None,
        resistance_level: Optional[float] = None,
        timeframe: str = '4h'
    ) -> Dict:
        """
        L058: Check TVEM Band confluence with S/R levels.

        Per Sherlock: "Price Retesting the 4H Support + TVEM Band" is a STRONG confluence.

        Args:
            token_symbol: Token symbol
            support_level: Horizontal support level (optional)
            resistance_level: Horizontal resistance level (optional)
            timeframe: Timeframe for TVEM calculation

        Returns:
            Dict with:
                - confluence: True if TVEM aligns with S/R
                - strength: "STRONG", "MODERATE", "WEAK", "NONE"
                - components: List of aligned components
                - sherlock_note: Human-readable confluence description
                - trade_bias: "LONG", "SHORT", "NEUTRAL"
        """
        logger.info(f"🔍 L058: Checking TVEM confluence for {token_symbol}")

        result = {
            'token_symbol': token_symbol,
            'confluence': False,
            'strength': 'NONE',
            'components': [],
            'sherlock_note': '',
            'trade_bias': 'NEUTRAL',
            'tvem_data': None,
            'timestamp': datetime.utcnow().isoformat()
        }

        # Get TVEM bands
        tvem_data = self.calculate_tvem_bands(token_symbol, timeframe)
        result['tvem_data'] = tvem_data

        if not tvem_data.get('tvem_mid'):
            result['sherlock_note'] = 'TVEM data unavailable'
            return result

        current_price = tvem_data.get('current_price', 0)
        tvem_lower = tvem_data.get('tvem_lower', 0)
        tvem_upper = tvem_data.get('tvem_upper', 0)
        tolerance = 0.02  # 2% tolerance

        components = []

        # Check TVEM lower band (for LONG trades)
        if tvem_data.get('at_lower_band'):
            components.append('TVEM_LOWER_BAND')

        # Check TVEM upper band (for SHORT trades)
        if tvem_data.get('at_upper_band'):
            components.append('TVEM_UPPER_BAND')

        # Check support confluence (for LONG trades)
        if support_level and support_level > 0:
            dist_to_support = abs(current_price - support_level) / support_level
            if dist_to_support <= tolerance:
                components.append('SUPPORT_LEVEL')

        # Check resistance confluence (for SHORT trades)
        if resistance_level and resistance_level > 0:
            dist_to_resistance = abs(current_price - resistance_level) / resistance_level
            if dist_to_resistance <= tolerance:
                components.append('RESISTANCE_LEVEL')

        result['components'] = components

        # Determine confluence strength
        if 'TVEM_LOWER_BAND' in components and 'SUPPORT_LEVEL' in components:
            result['confluence'] = True
            result['strength'] = 'STRONG'
            result['trade_bias'] = 'LONG'
            result['sherlock_note'] = f"Price Retesting Support (${support_level:.4f}) + TVEM Lower Band"
            logger.info(f"   ✅ STRONG LONG confluence: Support + TVEM Lower Band")

        elif 'TVEM_UPPER_BAND' in components and 'RESISTANCE_LEVEL' in components:
            result['confluence'] = True
            result['strength'] = 'STRONG'
            result['trade_bias'] = 'SHORT'
            result['sherlock_note'] = f"Price Retesting Resistance (${resistance_level:.4f}) + TVEM Upper Band"
            logger.info(f"   ✅ STRONG SHORT confluence: Resistance + TVEM Upper Band")

        elif len(components) == 1:
            result['confluence'] = True
            result['strength'] = 'WEAK'
            if 'TVEM_LOWER_BAND' in components or 'SUPPORT_LEVEL' in components:
                result['trade_bias'] = 'LONG'
                result['sherlock_note'] = f"Single confluence: {components[0]}"
            elif 'TVEM_UPPER_BAND' in components or 'RESISTANCE_LEVEL' in components:
                result['trade_bias'] = 'SHORT'
                result['sherlock_note'] = f"Single confluence: {components[0]}"
            logger.info(f"   ⚠️  WEAK confluence: {components}")

        else:
            result['sherlock_note'] = 'No TVEM confluence detected'
            logger.info(f"   ❌ No confluence")

        return result


class PatternRecognitionEngine:
    """
    Automated chart pattern detection for Sherlock integration.

    Session 268 Phase 1: Pattern Recognition (P3.1)

    Detects 3 primary reversal patterns mentioned in Sherlock's analysis:
    1. Inverse Head & Shoulders (most common)
    2. Cup & Handle (accumulation pattern)
    3. Double Bottom (support confirmation)

    Algorithm uses swing point detection + volume confirmation.
    """

    def __init__(self):
        """Initialize pattern recognition engine."""
        self.swing_window = 5  # Periods for swing point detection
        self.shoulder_tolerance = 0.05  # ±5% price tolerance for shoulders
        self.volume_threshold = 1.3  # 1.3x volume for confirmation

    def _find_swing_points(
        self,
        prices: List[float],
        point_type: str,  # "high" or "low"
        window: int = 5
    ) -> List[Dict]:
        """
        Find swing highs or swing lows in price data.

        Swing high: Local maximum where price[i] > all neighbors in window
        Swing low: Local minimum where price[i] < all neighbors in window

        Args:
            prices: List of price values
            point_type: "high" or "low"
            window: Lookback/lookahead window for comparison

        Returns:
            List of swing points with {'idx': index, 'price': value}
        """
        swing_points = []

        for i in range(window, len(prices) - window):
            is_swing = False

            if point_type == "high":
                # Check if prices[i] is highest in window
                left_max = max(prices[i-window:i])
                right_max = max(prices[i+1:i+window+1])
                is_swing = (prices[i] > left_max and prices[i] > right_max)

            elif point_type == "low":
                # Check if prices[i] is lowest in window
                left_min = min(prices[i-window:i])
                right_min = min(prices[i+1:i+window+1])
                is_swing = (prices[i] < left_min and prices[i] < right_min)

            if is_swing:
                swing_points.append({
                    'idx': i,
                    'price': prices[i]
                })

        return swing_points

    def detect_inverse_head_and_shoulders(
        self,
        ohlcv: List,
        lookback: int = 50
    ) -> Optional[Dict]:
        """
        Detect inverse head & shoulders pattern (bullish reversal).

        Pattern Structure:
        - Left shoulder: local low
        - Head: lower low (middle)
        - Right shoulder: higher low (similar height to left shoulder)
        - Neckline: resistance connecting shoulder highs
        - Breakout: Price above neckline confirms bullish reversal

        Validation Criteria:
        1. Three consecutive swing lows
        2. Middle low (head) is lowest
        3. Left and right shoulders within ±5% price
        4. Volume declining on head (exhaustion)
        5. Volume increasing on right shoulder (buyers returning)

        Args:
            ohlcv: OHLCV data [[timestamp, open, high, low, close, volume], ...]
            lookback: Number of candles to analyze (default: 50)

        Returns:
            Pattern dict if detected, None otherwise
            {
                "pattern": "inverse_h_and_s",
                "left_shoulder": price,
                "head": price,
                "right_shoulder": price,
                "neckline": price,
                "target": price,  # Neckline + (Neckline - Head)
                "confidence": 0.0-1.0,
                "detected_at": timestamp
            }
        """
        if not ohlcv or len(ohlcv) < lookback:
            return None

        recent_candles = ohlcv[-lookback:]
        lows = [candle[3] for candle in recent_candles]
        highs = [candle[2] for candle in recent_candles]
        volumes = [candle[5] for candle in recent_candles]
        closes = [candle[4] for candle in recent_candles]

        # Find swing lows (local minima)
        swing_lows = self._find_swing_points(lows, "low", window=self.swing_window)

        if len(swing_lows) < 3:
            return None

        # Check last 3 consecutive swing lows for pattern
        for i in range(len(swing_lows) - 2):
            left = swing_lows[i]
            head = swing_lows[i+1]
            right = swing_lows[i+2]

            # Validate: Head is lowest point
            if not (head['price'] < left['price'] and head['price'] < right['price']):
                continue

            # Validate: Shoulders are roughly equal (±5% tolerance)
            shoulder_diff = abs(left['price'] - right['price']) / left['price']
            if shoulder_diff > self.shoulder_tolerance:
                continue

            # Calculate neckline (resistance connecting shoulder highs)
            # Find highs between shoulders
            left_high_idx = left['idx']
            right_high_idx = right['idx']
            between_highs = highs[left_high_idx:right_high_idx+1]
            neckline = max(between_highs) if between_highs else (left['price'] + right['price']) / 2

            # Calculate target: Neckline + (Neckline - Head)
            distance = neckline - head['price']
            target = neckline + distance

            # Volume confirmation (optional but increases confidence)
            vol_declining = volumes[head['idx']] < volumes[left['idx']]
            vol_increasing = volumes[right['idx']] > volumes[head['idx']]

            # Calculate confidence score
            confidence = 0.6  # Base confidence
            if vol_declining and vol_increasing:
                confidence = 0.8  # Strong volume confirmation
            elif vol_declining or vol_increasing:
                confidence = 0.7  # Partial volume confirmation

            # Check if current price is near neckline (potential breakout)
            current_price = closes[-1]
            near_neckline = abs(current_price - neckline) / neckline < 0.02  # Within 2%
            if near_neckline:
                confidence += 0.1

            confidence = min(confidence, 1.0)

            return {
                "pattern": "inverse_h_and_s",
                "left_shoulder": round(left['price'], 6),
                "head": round(head['price'], 6),
                "right_shoulder": round(right['price'], 6),
                "neckline": round(neckline, 6),
                "target": round(target, 6),
                "confidence": round(confidence, 2),
                "detected_at": recent_candles[-1][0],  # Latest timestamp
                "volume_confirmed": vol_declining and vol_increasing
            }

        return None

    def detect_cup_and_handle(
        self,
        ohlcv: List,
        lookback: int = 100
    ) -> Optional[Dict]:
        """
        Detect cup & handle pattern (bullish continuation/reversal).

        Pattern Structure:
        - Cup: U-shaped bottom (gradual decline followed by gradual recovery)
        - Handle: Small pullback after cup completes (consolidation)
        - Breakout: Price above cup rim confirms bullish move

        Validation Criteria:
        1. Cup depth: 12-33% decline from left rim to bottom
        2. Cup duration: At least 7-20 candles
        3. Symmetry: Right side recovers to similar level as left rim
        4. Handle: 10-15% pullback from right rim
        5. Volume: Declining on cup bottom, increasing on breakout

        Args:
            ohlcv: OHLCV data [[timestamp, open, high, low, close, volume], ...]
            lookback: Number of candles to analyze (default: 100)

        Returns:
            Pattern dict if detected, None otherwise
        """
        if not ohlcv or len(ohlcv) < lookback:
            return None

        recent_candles = ohlcv[-lookback:]
        highs = [candle[2] for candle in recent_candles]
        lows = [candle[3] for candle in recent_candles]
        closes = [candle[4] for candle in recent_candles]
        volumes = [candle[5] for candle in recent_candles]

        # Find potential cup: Look for U-shape in first 60-80% of data
        cup_section_end = int(len(recent_candles) * 0.8)
        cup_highs = highs[:cup_section_end]
        cup_lows = lows[:cup_section_end]

        if len(cup_highs) < 20:  # Need minimum candles for cup
            return None

        # Left rim: Peak in first 20% of cup section
        left_rim_section = cup_highs[:int(len(cup_highs) * 0.2)]
        left_rim_price = max(left_rim_section) if left_rim_section else cup_highs[0]
        left_rim_idx = cup_highs.index(left_rim_price)

        # Cup bottom: Lowest point in middle 40-60% of cup section
        middle_start = int(len(cup_lows) * 0.3)
        middle_end = int(len(cup_lows) * 0.7)
        cup_bottom_section = cup_lows[middle_start:middle_end]
        cup_bottom_price = min(cup_bottom_section) if cup_bottom_section else min(cup_lows)
        cup_bottom_idx = cup_lows.index(cup_bottom_price)

        # Right rim: Recovery in last 20% of cup section
        right_rim_section = cup_highs[-int(len(cup_highs) * 0.2):]
        right_rim_price = max(right_rim_section) if right_rim_section else cup_highs[-1]
        right_rim_idx = len(cup_highs) - len(right_rim_section) + right_rim_section.index(right_rim_price)

        # Validate cup depth (12-33% decline)
        cup_depth = (left_rim_price - cup_bottom_price) / left_rim_price
        if not (0.12 <= cup_depth <= 0.33):
            return None

        # Validate rim symmetry (right rim within ±8% of left rim)
        rim_diff = abs(right_rim_price - left_rim_price) / left_rim_price
        if rim_diff > 0.08:
            return None

        # Handle: Look for pullback in last 20% of data (after cup)
        handle_section = closes[cup_section_end:]
        if len(handle_section) < 3:  # Need at least 3 candles for handle
            return None

        handle_high = max(handle_section)
        handle_low = min(handle_section)
        handle_pullback = (handle_high - handle_low) / handle_high

        # Validate handle (10-15% pullback)
        if not (0.08 <= handle_pullback <= 0.20):
            return None

        # Calculate target: Cup depth projected upward from breakout
        breakout_level = (left_rim_price + right_rim_price) / 2
        target = breakout_level + (left_rim_price - cup_bottom_price)

        # Volume confirmation
        cup_bottom_volume = volumes[cup_bottom_idx]
        avg_volume = sum(volumes) / len(volumes)
        volume_declining = cup_bottom_volume < avg_volume

        # Calculate confidence
        confidence = 0.6  # Base confidence
        if volume_declining:
            confidence += 0.1
        if handle_pullback < 0.15:  # Tight handle = stronger
            confidence += 0.1
        if rim_diff < 0.05:  # Very symmetric = stronger
            confidence += 0.1

        confidence = min(confidence, 1.0)

        return {
            "pattern": "cup_and_handle",
            "left_rim": round(left_rim_price, 6),
            "cup_bottom": round(cup_bottom_price, 6),
            "right_rim": round(right_rim_price, 6),
            "handle_low": round(handle_low, 6),
            "breakout_level": round(breakout_level, 6),
            "target": round(target, 6),
            "cup_depth_pct": round(cup_depth * 100, 2),
            "handle_depth_pct": round(handle_pullback * 100, 2),
            "confidence": round(confidence, 2),
            "detected_at": recent_candles[-1][0]
        }

    def detect_double_bottom(
        self,
        ohlcv: List,
        lookback: int = 50
    ) -> Optional[Dict]:
        """
        Detect double bottom pattern (bullish reversal).

        Pattern Structure:
        - First bottom: Initial selloff creating first low
        - Peak: Temporary recovery (forms resistance)
        - Second bottom: Second selloff to similar price as first low
        - Breakout: Price breaks above peak resistance

        Validation Criteria:
        1. Two swing lows within ±2% of each other
        2. Peak between lows is at least 3% above lows
        3. Second bottom forms at least 5 candles after first bottom
        4. Volume declining on second bottom (buyers stepping in)
        5. Volume increasing on breakout

        Args:
            ohlcv: OHLCV data [[timestamp, open, high, low, close, volume], ...]
            lookback: Number of candles to analyze (default: 50)

        Returns:
            Pattern dict if detected, None otherwise
        """
        if not ohlcv or len(ohlcv) < lookback:
            return None

        recent_candles = ohlcv[-lookback:]
        lows = [candle[3] for candle in recent_candles]
        highs = [candle[2] for candle in recent_candles]
        closes = [candle[4] for candle in recent_candles]
        volumes = [candle[5] for candle in recent_candles]

        # Find swing lows
        swing_lows = self._find_swing_points(lows, "low", window=self.swing_window)

        if len(swing_lows) < 2:
            return None

        # Check pairs of swing lows for double bottom pattern
        for i in range(len(swing_lows) - 1):
            first_low = swing_lows[i]
            second_low = swing_lows[i+1]

            # Validate: Minimum separation (at least 5 candles)
            separation = second_low['idx'] - first_low['idx']
            if separation < 5:
                continue

            # Validate: Bottoms are at similar price (±2% tolerance)
            price_diff = abs(first_low['price'] - second_low['price']) / first_low['price']
            if price_diff > 0.02:
                continue

            # Find peak between the two lows
            between_start = first_low['idx']
            between_end = second_low['idx']
            between_highs = highs[between_start:between_end+1]
            peak_price = max(between_highs) if between_highs else (first_low['price'] + second_low['price']) / 2
            peak_idx = between_start + between_highs.index(peak_price)

            # Validate: Peak is at least 3% above bottoms
            peak_height = (peak_price - first_low['price']) / first_low['price']
            if peak_height < 0.03:
                continue

            # Calculate target: Peak + (Peak - Bottom)
            bottom_avg = (first_low['price'] + second_low['price']) / 2
            distance = peak_price - bottom_avg
            target = peak_price + distance

            # Volume confirmation
            vol_declining = volumes[second_low['idx']] < volumes[first_low['idx']]
            vol_at_peak = volumes[peak_idx]
            avg_volume = sum(volumes) / len(volumes)
            vol_increasing_at_peak = vol_at_peak > avg_volume

            # Calculate confidence
            confidence = 0.6  # Base confidence
            if vol_declining:
                confidence += 0.1  # Second bottom shows less selling pressure
            if vol_increasing_at_peak:
                confidence += 0.1  # Buyers pushing through resistance
            if price_diff < 0.01:  # Very tight bottoms (within 1%)
                confidence += 0.1

            confidence = min(confidence, 1.0)

            # Check if current price is near peak (potential breakout)
            current_price = closes[-1]
            near_peak = abs(current_price - peak_price) / peak_price < 0.02
            if near_peak:
                confidence += 0.05
                confidence = min(confidence, 1.0)

            return {
                "pattern": "double_bottom",
                "first_bottom": round(first_low['price'], 6),
                "second_bottom": round(second_low['price'], 6),
                "peak": round(peak_price, 6),
                "target": round(target, 6),
                "separation_candles": separation,
                "confidence": round(confidence, 2),
                "detected_at": recent_candles[-1][0],
                "volume_confirmed": vol_declining
            }

        return None

    def detect_all_patterns(
        self,
        ohlcv: List,
        lookback: int = 100
    ) -> List[str]:
        """
        Detect all chart patterns and return list of pattern names.

        Args:
            ohlcv: OHLCV data
            lookback: Number of candles to analyze

        Returns:
            List of detected pattern names (e.g., ["inverse_h_and_s", "double_bottom"])
        """
        detected = []

        # Detect inverse H&S (most common in Sherlock data)
        inv_hs = self.detect_inverse_head_and_shoulders(ohlcv, lookback=min(lookback, 50))
        if inv_hs:
            detected.append("inverse_h_and_s")

        # Detect cup & handle
        cup = self.detect_cup_and_handle(ohlcv, lookback=lookback)
        if cup:
            detected.append("cup_and_handle")

        # Detect double bottom
        double_bot = self.detect_double_bottom(ohlcv, lookback=min(lookback, 50))
        if double_bot:
            detected.append("double_bottom")

        return detected


def main():
    """Test the price action analyzer."""
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format='%(message)s'
    )

    if len(sys.argv) < 2:
        print("Usage: python price_action_analyzer.py <TOKEN_SYMBOL>")
        print("Example: python price_action_analyzer.py IRYS")
        sys.exit(1)

    token_symbol = sys.argv[1].upper()

    print(f"\n{'='*80}")
    print(f"PRICE ACTION ANALYSIS: {token_symbol}")
    print(f"{'='*80}\n")

    analyzer = PriceActionAnalyzer()
    result = analyzer.analyze(token_symbol, analysis_type="SHORT")

    print(f"\n{'='*80}")
    print(f"RESULTS")
    print(f"{'='*80}")
    print(f"\n💰 Current Price: ${result.get('current_price', 'N/A')}")
    print(f"📊 RSI (1h): {result.get('rsi_1h', 'N/A')}")
    print(f"📊 RSI (4h): {result.get('rsi_4h', 'N/A')}")
    print(f"📈 Trend: {result.get('trend', 'N/A')}")
    print(f"🏗️  Structure: {result.get('structure', 'N/A')}")
    print(f"🎯 Fib Position: {result.get('price_vs_fib', 'N/A')}")

    print(f"\n{'='*80}")
    print(f"ENTRY READINESS SCORE: {result.get('entry_readiness_score', 0)}/10")
    print(f"RECOMMENDATION: {result.get('recommendation', 'WAIT')}")
    print(f"{'='*80}\n")

    print("Reasoning:")
    for reason in result.get('reasoning', []):
        print(f"  {reason}")

    print(f"\n{'='*80}\n")

    # Save to JSON
    output_file = f"price_action_{token_symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, 'w') as f:
        json.dump(result, f, indent=2)
    print(f"✅ Results saved to: {output_file}\n")


if __name__ == "__main__":
    main()
