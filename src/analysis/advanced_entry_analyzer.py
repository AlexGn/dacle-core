"""
Advanced Entry Timing Analyzer for TGE Short Trades

DEPRECATED: Use src.analysis module instead.
Session 256: Marked for migration to src/analysis/

This module extends the basic price_action_analyzer.py with advanced indicators:
- Order book depth (support/resistance levels)
- Volume spike detection (panic vs accumulation)
- Funding rate tracking (perpetual futures sentiment)
- Open interest monitoring (position size changes)
- Liquidation level calculation (cascade risk zones)

Used by telegram_entry_monitor.py to determine "ENTER NOW" vs "WAIT" signals.

Author: DACLE System
Created: 2025-11-27
"""

import warnings
warnings.warn(
    "scripts.helpers.advanced_entry_analyzer is deprecated. "
    "Use src.analysis module instead.",
    DeprecationWarning,
    stacklevel=2
)

import ccxt
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import logging

# Import existing analyzers
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from helpers.price_action_analyzer import PriceActionAnalyzer
from helpers.indices_tracker import IndicesTracker
from src.data.multi_source_fetcher import MultiSourceFetcher

logger = logging.getLogger(__name__)


class AdvancedEntryAnalyzer:
    """
    Combines core TA (RSI, MA, BTC/ETH) with advanced indicators
    (order book, volume, funding, OI, liquidations) for entry timing.
    """

    def __init__(self, exchange_id: str = "binance"):
        """
        Initialize analyzer with exchange connection.

        Args:
            exchange_id: CCXT exchange ID (binance, mexc, hyperliquid)
        """
        self.exchange_id = exchange_id
        self.exchange = self._init_exchange(exchange_id)
        self.core_analyzer = PriceActionAnalyzer(exchange_id=exchange_id)
        self.indices_tracker = IndicesTracker()
        self.multi_source_fetcher = MultiSourceFetcher()  # NEW: Multi-source data fetcher

        # Weights for entry readiness scoring (total = 10.0)
        # UPDATED 2025-11-28: Removed BTC/ETH from scoring (moved to position sizing only)
        # Redistributed 2.0 points across core TA indicators
        self.weights = {
            'core_ta': 7.0,  # RSI, MA, USDT.D, Volatility (BTC/ETH removed)
            'order_book': 1.5,  # Support/resistance strength
            'volume': 1.0,  # Panic selling vs accumulation
            'funding_rate': 1.0,  # Perpetual futures sentiment
            'open_interest': 1.0,  # Position size changes
            'liquidations': 0.5  # Cascade risk zones
        }

    def _init_exchange(self, exchange_id: str) -> ccxt.Exchange:
        """Initialize CCXT exchange with rate limiting."""
        exchanges = {
            'binance': ccxt.binance,
            'mexc': ccxt.mexc,
            'gate': ccxt.gateio,
            'bybit': ccxt.bybit,
            'coinbase': ccxt.coinbase,
            'hyperliquid': ccxt.hyperliquid,
            'blofin': ccxt.blofin
        }

        if exchange_id not in exchanges:
            logger.warning(f"Exchange {exchange_id} not supported, using binance")
            exchange_id = 'binance'

        exchange_class = exchanges[exchange_id]
        return exchange_class({'enableRateLimit': True})

    def analyze(
        self,
        symbol: str,
        analysis_type: str = "SHORT",
        timestamp: Optional[str] = None
    ) -> Dict:
        """
        Analyze entry timing for a token using UNIFIED TA system (TADataAggregator).

        Session 80-INTEGRATION: This method now uses TADataAggregator as single
        source of truth for TA calculations, ensuring consistency with Agent 4.

        Args:
            symbol: Token symbol (e.g., "MONAD", "BTC")
            analysis_type: "SHORT" or "LONG"
            timestamp: ISO timestamp for historical analysis (optional)

        Returns:
            {
                'entry_readiness_score': 8.5,  # 0-10 (maps to ta_score_normalized)
                'recommendation': 'ENTER NOW',  # or 'WAIT'
                'core_ta': {...},  # Mapped from TA Aggregator
                'advanced_ta': {...},  # Mapped from TA Aggregator
                'macro_indices': {...},  # NEW: From TA Aggregator
                'reasoning': [...]  # Human-readable signals
                'raw_ta_data': {...}  # Full TA Aggregator output
            }
        """
        try:
            # Import unified TA aggregator (Session 79K)
            from src.analysis.ta_aggregator import TADataAggregator

            logger.info(f"[UNIFIED] Using TADataAggregator for {symbol} (consistency with Agent 4)")

            # Use fast parallel collection (2-3s vs 10-20s)
            aggregator = TADataAggregator(exchange_id=self.exchange_id)
            ta_data = aggregator.collect_all_fast(token_symbol=symbol)

            # Map TADataAggregator output to Entry Monitor format
            entry_score = ta_data['ta_score_normalized']
            recommendation = self._map_recommendation(ta_data, analysis_type)

            # Fetch TGE fundamentals (for pre-launch tokens)
            tge_fundamentals = self._get_tge_fundamentals(symbol)

            return {
                'entry_readiness_score': entry_score,
                'recommendation': recommendation,
                'timestamp': timestamp or datetime.utcnow().isoformat(),

                # Mapped formats (preserve Entry Monitor structure)
                'core_ta': self._map_core_ta(ta_data['core_ta'], ta_data['macro_indices']),
                'advanced_ta': self._map_advanced_ta(ta_data['advanced_ta']),
                'macro_indices': ta_data['macro_indices'],  # NEW: Full macro data

                # Additional context
                'tge_fundamentals': tge_fundamentals,
                'reasoning': self._generate_reasoning_unified(ta_data, analysis_type),

                # Full TA Aggregator output (for logging/debugging)
                'raw_ta_data': ta_data,
                'collection_mode': ta_data.get('collection_mode', 'unified'),
                'collection_time_sec': ta_data.get('collection_time_sec')
            }

        except Exception as e:
            logger.error(f"[UNIFIED] Error analyzing {symbol}: {e}", exc_info=True)
            logger.warning(f"[FALLBACK] Attempting legacy analysis method...")

            # Fallback to legacy method if unified fails
            try:
                return self.analyze_legacy(symbol, analysis_type, timestamp)
            except Exception as e2:
                logger.error(f"[FALLBACK] Legacy method also failed: {e2}")
                return {
                    'entry_readiness_score': 0,
                    'recommendation': 'WAIT',
                    'error': str(e),
                    'reasoning': [f"❌ Analysis failed: {e}"]
                }

    def analyze_legacy(
        self,
        symbol: str,
        analysis_type: str = "SHORT",
        timestamp: Optional[str] = None
    ) -> Dict:
        """
        LEGACY: Original analyze() method kept as backup.

        DO NOT USE THIS DIRECTLY - only called as fallback if unified method fails.
        This will be removed after unified system is validated.
        """
        try:
            # 1. Get core TA from existing analyzer
            core_result = self.core_analyzer.analyze(
                token_symbol=symbol,
                analysis_type=analysis_type
            )

            # Add symbol to core_result for volatility calculation
            core_result['symbol'] = symbol

            # 2. Get advanced TA
            trading_pair = self._get_trading_pair(symbol)

            order_book_analysis = self._analyze_order_book(trading_pair)
            volume_analysis = self._analyze_volume(trading_pair)
            funding_analysis = self._analyze_funding_rate(trading_pair)
            oi_analysis = self._analyze_open_interest(trading_pair)
            liquidation_analysis = self._calculate_liquidation_levels(
                trading_pair,
                core_result.get('current_price', 0)
            )

            # 3. Calculate entry readiness score
            core_score = core_result.get('entry_readiness_score', 0)
            advanced_score = self._calculate_advanced_score(
                order_book_analysis,
                volume_analysis,
                funding_analysis,
                oi_analysis,
                liquidation_analysis,
                analysis_type
            )

            total_score = (
                (core_score / 10.0) * self.weights['core_ta'] +
                advanced_score
            )

            # 4. Generate reasoning
            reasoning = self._generate_reasoning(
                core_result,
                order_book_analysis,
                volume_analysis,
                funding_analysis,
                oi_analysis,
                liquidation_analysis,
                analysis_type
            )

            # 5. Make recommendation
            # UPDATED 2025-11-28: Lowered from 7.0 to 6.5 (with macro filter, can be more aggressive)
            recommendation = "ENTER NOW" if total_score >= 6.5 else "WAIT"

            # 6. Fetch TGE fundamentals (for pre-launch tokens)
            tge_fundamentals = self._get_tge_fundamentals(symbol)

            return {
                'entry_readiness_score': round(total_score, 1),
                'recommendation': recommendation,
                'timestamp': timestamp or datetime.utcnow().isoformat(),
                'core_ta': {
                    'rsi_1h': core_result.get('rsi_1h', 0),
                    'rsi_4h': core_result.get('rsi_4h', 0),
                    'price_vs_ma20': 'below' if core_result.get('current_price', 0) < core_result.get('trend_data', {}).get('ma_short', 999999) else 'above',
                    'btc_structure': core_result.get('btc_market_structure', 'unknown'),
                    'eth_structure': core_result.get('eth_market_structure', 'unknown'),
                    'usdt_d': (core_result.get('indices', {}).get('usdt_d', {}).get('value') or
                              (core_result.get('usdt_d', {}).get('value') if isinstance(core_result.get('usdt_d'), dict) else 0)),
                    'volatility_pattern': self._classify_volatility(core_result)
                },
                'advanced_ta': {
                    'order_book': order_book_analysis,
                    'volume': volume_analysis,
                    'funding_rate': funding_analysis,
                    'open_interest': oi_analysis,
                    'liquidations': liquidation_analysis
                },
                'tge_fundamentals': tge_fundamentals,  # NEW: TGE fundamental data
                'reasoning': reasoning,
                'raw_core_result': core_result  # For debugging
            }

        except Exception as e:
            logger.error(f"Error analyzing {symbol}: {e}", exc_info=True)
            return {
                'entry_readiness_score': 0,
                'recommendation': 'WAIT',
                'error': str(e),
                'reasoning': [f"❌ Analysis failed: {e}"]
            }

    def _get_trading_pair(self, symbol: str) -> str:
        """Convert token symbol to exchange trading pair."""
        # Common patterns
        if '/' in symbol:
            return symbol  # Already formatted (BTC/USDT)

        # Try common pairs
        common_pairs = [
            f"{symbol}/USDT",
            f"{symbol}/USD",
            f"{symbol}/USDC"
        ]

        # Check which pair exists on exchange
        try:
            markets = self.exchange.load_markets()
            for pair in common_pairs:
                if pair in markets:
                    return pair
        except Exception as e:
            logger.warning(f"Could not load markets: {e}")

        # Default to USDT
        return f"{symbol}/USDT"

    def _analyze_order_book(self, trading_pair: str) -> Dict:
        """
        Analyze order book depth to find support/resistance levels.

        Returns:
            {
                'resistance_levels': [0.028, 0.031],
                'support_levels': [0.024, 0.020],
                'buy_wall_strength': 'weak',  # weak/medium/strong
                'sell_wall_strength': 'strong',
                'imbalance': 'sell_pressure'  # sell_pressure/buy_pressure/balanced
            }
        """
        try:
            orderbook = self.exchange.fetch_order_book(trading_pair, limit=100)

            bids = np.array([[price, amount] for price, amount in orderbook['bids']])
            asks = np.array([[price, amount] for price, amount in orderbook['asks']])

            if len(bids) == 0 or len(asks) == 0:
                return self._default_order_book_analysis()

            # Calculate buy/sell wall strength
            total_bid_volume = np.sum(bids[:, 1])
            total_ask_volume = np.sum(asks[:, 1])

            # Find significant walls (>10% of total volume)
            bid_threshold = total_bid_volume * 0.10
            ask_threshold = total_ask_volume * 0.10

            support_levels = []
            for price, amount in bids:
                if amount >= bid_threshold:
                    support_levels.append(float(price))

            resistance_levels = []
            for price, amount in asks:
                if amount >= ask_threshold:
                    resistance_levels.append(float(price))

            # Classify wall strength
            buy_wall_strength = self._classify_wall_strength(total_bid_volume, bids)
            sell_wall_strength = self._classify_wall_strength(total_ask_volume, asks)

            # Calculate imbalance
            ratio = total_bid_volume / total_ask_volume if total_ask_volume > 0 else 1.0
            if ratio > 1.2:
                imbalance = 'buy_pressure'
            elif ratio < 0.8:
                imbalance = 'sell_pressure'
            else:
                imbalance = 'balanced'

            return {
                'resistance_levels': resistance_levels[:3],  # Top 3
                'support_levels': support_levels[:3],
                'buy_wall_strength': buy_wall_strength,
                'sell_wall_strength': sell_wall_strength,
                'imbalance': imbalance,
                'bid_ask_ratio': round(ratio, 2)
            }

        except Exception as e:
            logger.warning(f"Order book analysis failed: {e}")
            return self._default_order_book_analysis()

    def _classify_wall_strength(self, total_volume: float, orders: np.ndarray) -> str:
        """Classify buy/sell wall strength based on concentration."""
        if len(orders) == 0:
            return 'weak'

        # Check if top 5 orders contain >50% of volume
        top_5_volume = np.sum(orders[:5, 1]) if len(orders) >= 5 else np.sum(orders[:, 1])
        concentration = top_5_volume / total_volume if total_volume > 0 else 0

        if concentration > 0.5:
            return 'strong'
        elif concentration > 0.3:
            return 'medium'
        else:
            return 'weak'

    def _default_order_book_analysis(self) -> Dict:
        """Return default order book analysis when data unavailable."""
        return {
            'resistance_levels': [],
            'support_levels': [],
            'buy_wall_strength': 'unknown',
            'sell_wall_strength': 'unknown',
            'imbalance': 'unknown',
            'bid_ask_ratio': 1.0
        }

    def _analyze_volume(self, trading_pair: str) -> Dict:
        """
        Analyze volume patterns to detect panic selling or accumulation.

        Returns:
            {
                '24h_change': '+340%',
                'pattern': 'panic_selling',  # panic_selling/accumulation/normal
                'spike_detected': True,
                'vs_7d_avg': 3.4  # 24h volume / 7d average
            }
        """
        try:
            # Fetch 7 days of OHLCV data (1h candles)
            since = self.exchange.milliseconds() - (7 * 24 * 60 * 60 * 1000)
            ohlcv = self.exchange.fetch_ohlcv(
                trading_pair,
                timeframe='1h',
                since=since,
                limit=168  # 7 days * 24 hours
            )

            if len(ohlcv) < 24:
                return self._default_volume_analysis()

            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

            # Calculate 24h and 7d average volumes
            volume_24h = df.tail(24)['volume'].sum()
            volume_7d_avg = df['volume'].mean() * 24

            # Detect spike
            ratio = volume_24h / volume_7d_avg if volume_7d_avg > 0 else 1.0
            spike_detected = ratio > 2.0  # 2x above average = spike

            # Classify pattern
            if ratio > 3.0:
                pattern = 'panic_selling'
            elif ratio > 2.0:
                pattern = 'high_activity'
            elif ratio < 0.5:
                pattern = 'accumulation'  # Low volume = quiet accumulation
            else:
                pattern = 'normal'

            # Calculate 24h change
            volume_24h_ago = df.iloc[-48:-24]['volume'].sum() if len(df) >= 48 else volume_24h
            change_pct = ((volume_24h - volume_24h_ago) / volume_24h_ago * 100) if volume_24h_ago > 0 else 0

            return {
                '24h_change': f"+{int(change_pct)}%" if change_pct > 0 else f"{int(change_pct)}%",
                'pattern': pattern,
                'spike_detected': spike_detected,
                'vs_7d_avg': round(ratio, 1)
            }

        except Exception as e:
            logger.warning(f"Volume analysis failed: {e}")
            return self._default_volume_analysis()

    def _default_volume_analysis(self) -> Dict:
        """Return default volume analysis when data unavailable."""
        return {
            '24h_change': 'N/A',
            'pattern': 'unknown',
            'spike_detected': False,
            'vs_7d_avg': 1.0
        }

    def _analyze_funding_rate(self, trading_pair: str) -> Dict:
        """
        Analyze funding rate for perpetual futures using multi-source fetcher.

        Returns:
            {
                'current_rate': -0.01,  # Negative = shorts paying longs
                'sentiment': 'bearish',  # bearish/bullish/neutral
                '24h_avg': -0.005,
                'source': 'binance',  # NEW: Which exchange provided the data
                'confidence': 95  # NEW: Data confidence (0-100)
            }
        """
        # Extract symbol from trading pair (MON/USDT → MON)
        symbol = trading_pair.split('/')[0] if '/' in trading_pair else trading_pair

        # Use multi-source fetcher (tries Binance → Bybit → Gate.io → MEXC)
        funding_data = self.multi_source_fetcher.get_funding_rate(symbol)

        # Check if data was found
        if funding_data['value'] == 'N/A':
            return {
                'current_rate': 0,
                'sentiment': 'unknown',
                '24h_avg': 0,
                'source': funding_data.get('source', 'none'),
                'confidence': funding_data.get('confidence', 0),
                'reason': funding_data.get('reason', 'No data available')
            }

        # Data found - classify sentiment
        current_rate = funding_data['value'] / 100  # Convert from percentage back to decimal

        if current_rate < -0.005:
            sentiment = 'bearish'  # Shorts dominant
        elif current_rate > 0.005:
            sentiment = 'bullish'  # Longs dominant
        else:
            sentiment = 'neutral'

        return {
            'current_rate': round(current_rate, 4),
            'sentiment': sentiment,
            '24h_avg': round(current_rate, 4),  # Simplified - using current as 24h avg
            'source': funding_data.get('source', 'unknown'),
            'confidence': funding_data.get('confidence', 0)
        }

    def _analyze_open_interest(self, trading_pair: str) -> Dict:
        """
        Analyze open interest changes (perpetual futures) using multi-source fetcher.

        Returns:
            {
                'current_oi': 1000000,  # USD value
                '24h_change': '+120%',
                'direction': 'increasing_shorts',  # increasing_shorts/increasing_longs/decreasing
                'oi_vs_volume_ratio': 2.5,
                'source': 'binance',  # NEW: Which exchange provided the data
                'confidence': 95  # NEW: Data confidence (0-100)
            }
        """
        # Extract symbol from trading pair (MON/USDT → MON)
        symbol = trading_pair.split('/')[0] if '/' in trading_pair else trading_pair

        # Use multi-source fetcher (tries Binance → Bybit → Gate.io)
        oi_data = self.multi_source_fetcher.get_open_interest(symbol)

        # Check if data was found
        if oi_data['value'] == 'N/A':
            return {
                'current_oi': 0,
                '24h_change': 'N/A',
                'direction': 'unknown',
                'oi_vs_volume_ratio': 0,
                'source': oi_data.get('source', 'none'),
                'confidence': oi_data.get('confidence', 0),
                'reason': oi_data.get('reason', 'No data available')
            }

        # Data found
        current_oi = oi_data['value']

        # Simplified: Assume stable direction (would need historical data for real analysis)
        direction = 'stable'

        return {
            'current_oi': int(current_oi),
            '24h_change': 'N/A',  # Would need historical OI data
            'direction': direction,
            'oi_vs_volume_ratio': 0,  # Would need volume data
            'source': oi_data.get('source', 'unknown'),
            'confidence': oi_data.get('confidence', 0)
        }

    def _calculate_liquidation_levels(self, trading_pair: str, current_price: float) -> Dict:
        """
        Estimate liquidation levels based on open interest and leverage.

        Returns:
            {
                'long_cascades': [0.024, 0.020],  # Prices where longs get liquidated
                'short_cascades': [0.031, 0.035],  # Prices where shorts get liquidated
                'nearest_long_liq': 0.024,
                'nearest_short_liq': 0.031
            }
        """
        try:
            # Simplified calculation (real would use exchange liquidation data)
            # Assume 10x leverage average, liquidation at ±9% move

            long_liq_pct = [0.91, 0.82, 0.73]  # -9%, -18%, -27% (10x, 5x, 3x leverage)
            short_liq_pct = [1.09, 1.18, 1.27]  # +9%, +18%, +27%

            long_cascades = [round(current_price * pct, 6) for pct in long_liq_pct]
            short_cascades = [round(current_price * pct, 6) for pct in short_liq_pct]

            return {
                'long_cascades': long_cascades,
                'short_cascades': short_cascades,
                'nearest_long_liq': long_cascades[0],
                'nearest_short_liq': short_cascades[0]
            }

        except Exception as e:
            logger.warning(f"Liquidation level calculation failed: {e}")
            return {
                'long_cascades': [],
                'short_cascades': [],
                'nearest_long_liq': 0,
                'nearest_short_liq': 0
            }

    def _calculate_advanced_score(
        self,
        order_book: Dict,
        volume: Dict,
        funding: Dict,
        oi: Dict,
        liquidations: Dict,
        analysis_type: str
    ) -> float:
        """
        Calculate advanced TA score (0-5.0 points).

        Scoring (for SHORTS):
        - Order book: +1.5 if strong sell walls (resistance)
        - Volume: +1.0 if panic selling detected
        - Funding: +1.0 if negative (shorts paying longs)
        - OI: +1.0 if increasing shorts
        - Liquidations: +0.5 if long cascades near price
        """
        score = 0.0

        if analysis_type == "SHORT":
            # Order book (1.5 points max)
            if order_book.get('sell_wall_strength') == 'strong':
                score += 1.5
            elif order_book.get('sell_wall_strength') == 'medium':
                score += 0.75
            elif order_book.get('imbalance') == 'sell_pressure':
                score += 0.5

            # Volume (1.0 point max)
            if volume.get('pattern') == 'panic_selling':
                score += 1.0
            elif volume.get('spike_detected'):
                score += 0.5

            # Funding rate (1.0 point max)
            if funding.get('sentiment') == 'bearish':
                score += 1.0
            elif funding.get('current_rate', 0) < 0:
                score += 0.5

            # Open interest (1.0 point max)
            if oi.get('direction') == 'increasing_shorts':
                score += 1.0
            elif '24h_change' in oi and '+' in str(oi['24h_change']):
                score += 0.5

            # Liquidations (0.5 point max)
            if liquidations.get('long_cascades'):
                # Check if nearest long liquidation is within 5% of current price
                nearest_liq = liquidations['nearest_long_liq']
                if nearest_liq > 0:  # Avoid division by zero
                    score += 0.5

        return score

    def _generate_reasoning(
        self,
        core_result: Dict,
        order_book: Dict,
        volume: Dict,
        funding: Dict,
        oi: Dict,
        liquidations: Dict,
        analysis_type: str
    ) -> List[str]:
        """Generate human-readable reasoning for entry decision."""
        reasoning = []

        # Core TA signals
        rsi = core_result.get('rsi_1h', 0)
        if 40 <= rsi <= 70:
            reasoning.append(f"✅ RSI {rsi} (neutral, ready to dump)")
        elif rsi > 70:
            reasoning.append(f"✅ RSI {rsi} (overbought, strong short signal)")
        else:
            reasoning.append(f"⚠️ RSI {rsi} (oversold, wait for bounce)")

        # Trend
        btc = core_result.get('btc_market_structure', 'unknown')
        eth = core_result.get('eth_market_structure', 'unknown')
        if btc == 'downtrend' and eth == 'downtrend':
            reasoning.append("✅ BTC/ETH both downtrend (favorable macro)")
        elif btc == 'uptrend' or eth == 'uptrend':
            reasoning.append("⚠️ BTC/ETH uptrend (macro headwind)")

        # Order book
        if order_book.get('sell_wall_strength') == 'strong':
            resistance = order_book.get('resistance_levels', [])
            if resistance:
                reasoning.append(f"✅ Strong sell walls at {resistance[:2]} (resistance)")
        elif order_book.get('imbalance') == 'sell_pressure':
            reasoning.append("✅ Order book imbalance: sell pressure detected")

        # Volume
        if volume.get('pattern') == 'panic_selling':
            change = volume.get('24h_change', 'N/A')
            reasoning.append(f"✅ Panic selling detected ({change} volume spike)")
        elif volume.get('spike_detected'):
            reasoning.append(f"✅ Volume spike detected ({volume.get('vs_7d_avg', 0)}x above average)")

        # Funding
        if funding.get('sentiment') == 'bearish':
            rate = funding.get('current_rate', 0)
            reasoning.append(f"✅ Negative funding rate ({rate}%, shorts dominating)")
        elif funding.get('sentiment') == 'unknown':
            reasoning.append("⚠️ Funding rate unavailable (spot trading only?)")

        # Open interest
        if oi.get('direction') == 'increasing_shorts':
            change = oi.get('24h_change', 'N/A')
            reasoning.append(f"✅ Open interest up {change} (new shorts entering)")
        elif oi.get('direction') == 'unknown':
            reasoning.append("⚠️ Open interest unavailable (spot trading only?)")

        # Liquidations
        if liquidations.get('long_cascades'):
            nearest = liquidations['nearest_long_liq']
            reasoning.append(f"⚠️ Long liquidations at {nearest} (cascade fuel)")

        return reasoning

    def _classify_volatility(self, core_result: Dict) -> str:
        """
        Classify volatility pattern based on ATR calculation.

        Uses multi-source fetcher to get OHLCV and calculate real ATR.
        """
        try:
            # Get symbol from core result
            symbol = core_result.get('symbol', '')
            if not symbol:
                return "unknown"

            # Fetch OHLCV data from multi-source fetcher
            ohlcv_data = self.multi_source_fetcher.get_ohlcv_adaptive(
                symbol=symbol,
                timeframe='4h',
                exchange_id=self.exchange_id
            )

            if not ohlcv_data.get('ohlcv'):
                # Fallback to 1h if 4h not available
                ohlcv_data = self.multi_source_fetcher.get_ohlcv_adaptive(
                    symbol=symbol,
                    timeframe='1h',
                    exchange_id=self.exchange_id
                )

            # Calculate volatility classification
            if ohlcv_data.get('ohlcv'):
                volatility = self.multi_source_fetcher.calculate_volatility(
                    ohlcv_data['ohlcv']
                )
                return volatility.get('classification', 'unknown')

            return "unknown"

        except Exception as e:
            logger.warning(f"Volatility classification failed: {e}")
            return "unknown"

    def _get_tge_fundamentals(self, symbol: str) -> Dict:
        """
        Fetch TGE fundamentals from Supabase tge_short_setups table.

        Args:
            symbol: Token symbol (e.g., "MONAD")

        Returns:
            Dict with TGE fundamental data or empty dict if not found
        """
        try:
            from src.knowledge.supabase_client import SupabaseKnowledgeBase

            kb = SupabaseKnowledgeBase()

            # Query tge_short_setups table for this token
            result = kb.client.table('tge_short_setups')\
                .select('*')\
                .eq('project_symbol', symbol)\
                .order('created_at', desc=True)\
                .limit(1)\
                .execute()

            if result.data and len(result.data) > 0:
                data = result.data[0]
                return {
                    'fdv': data.get('fdv'),
                    'market_cap': data.get('market_cap'),
                    'fdv_mc_ratio': data.get('fdv_mc_ratio'),
                    'float_percentage': data.get('float_percentage'),
                    'retail_sale_amount': data.get('retail_sale_amount'),
                    'participants': data.get('participants'),
                    'vc_investors': data.get('vc_investors', []),
                    'exchanges': data.get('exchanges', []),
                    'pattern_match': data.get('pattern_match'),
                    'conviction_score': data.get('conviction_score')
                }

            logger.debug(f"No TGE fundamentals found for {symbol}")
            return {}

        except Exception as e:
            logger.warning(f"Could not fetch TGE fundamentals for {symbol}: {e}")
            return {}

    # ============================================================================
    # FORMAT MAPPING LAYER - Session 80-INTEGRATION
    # ============================================================================
    # These methods translate TA Aggregator format → Entry Monitor format
    # to preserve backward compatibility with existing alert formatting.

    def _map_recommendation(self, ta_data: Dict, analysis_type: str) -> str:
        """
        Map TA Aggregator recommendation to Entry Monitor format.

        Args:
            ta_data: Full TA Aggregator output
            analysis_type: "SHORT" or "LONG"

        Returns:
            "ENTER NOW" or "WAIT"
        """
        ta_score = ta_data.get('ta_score_normalized', 0)
        ta_recommendation = ta_data.get('ta_recommendation', 'NEUTRAL_TA')

        # Entry threshold: 6.5/10 (UPDATED 2025-11-28)
        # With macro filter enabled, we can be more aggressive
        entry_threshold = 6.5

        if analysis_type == "SHORT":
            # For shorts: need favorable TA conditions
            if ta_score >= entry_threshold:
                return "ENTER NOW"
            else:
                return "WAIT"
        else:
            # For longs: inverse logic (not primary use case)
            if ta_score <= (10 - entry_threshold):
                return "ENTER NOW"
            else:
                return "WAIT"

    def _map_core_ta(self, core_ta: Dict, macro_indices: Dict) -> Dict:
        """
        Map TA Aggregator core_ta to Entry Monitor format.

        Args:
            core_ta: TA Aggregator core_ta dict
            macro_indices: TA Aggregator macro_indices dict (for BTC/ETH/USDT.D)

        Returns:
            Entry Monitor core_ta format
        """
        return {
            'rsi_1h': core_ta.get('rsi_1h'),
            'rsi_4h': core_ta.get('rsi_4h'),
            'price_vs_ma20': core_ta.get('price_vs_ma20', 'unknown'),
            'btc_structure': macro_indices.get('btc_market_structure', 'unknown'),
            'eth_structure': macro_indices.get('eth_market_structure', 'unknown'),
            'usdt_d': macro_indices.get('usdt_dominance'),
            'volatility_pattern': self._classify_volatility_from_ta(core_ta)
        }

    def _map_advanced_ta(self, advanced_ta: Dict) -> Dict:
        """
        Map TA Aggregator advanced_ta to Entry Monitor format.

        Args:
            advanced_ta: TA Aggregator advanced_ta dict

        Returns:
            Entry Monitor advanced_ta format
        """
        return {
            'order_book': {
                'imbalance': advanced_ta.get('order_book_imbalance'),
                'sell_wall_strength': self._map_ob_signal(advanced_ta.get('order_book_signal')),
                'bid_liquidity': advanced_ta.get('bid_liquidity'),
                'ask_liquidity': advanced_ta.get('ask_liquidity')
            },
            'volume': {
                'pattern': self._map_volume_pattern(advanced_ta.get('volume_trend')),
                '24h_volume': advanced_ta.get('volume_24h'),
                'spike_detected': advanced_ta.get('volume_trend') == 'increasing'
            },
            'funding_rate': {
                'current_rate': advanced_ta.get('funding_rate'),
                'sentiment': advanced_ta.get('funding_signal'),
                'trend': advanced_ta.get('funding_trend'),
                'source': 'ta_aggregator'
            },
            'open_interest': {
                'current_oi': advanced_ta.get('open_interest'),
                'direction': advanced_ta.get('oi_trend'),
                '24h_change': f"{advanced_ta.get('oi_change_24h')}%" if advanced_ta.get('oi_change_24h') else 'N/A'
            },
            'liquidations': {
                'cascade_fuel': advanced_ta.get('liquidation_cascade_fuel', 'UNKNOWN')
            }
        }

    def _map_ob_signal(self, ob_signal: str) -> str:
        """Map order book signal to sell wall strength."""
        if ob_signal == 'SELL_PRESSURE':
            return 'strong'
        elif ob_signal == 'BUY_PRESSURE':
            return 'weak'
        else:
            return 'medium'

    def _map_volume_pattern(self, volume_trend: str) -> str:
        """Map volume trend to pattern description."""
        if volume_trend == 'increasing':
            return 'panic_selling'
        elif volume_trend == 'decreasing':
            return 'accumulation'
        else:
            return 'steady'

    def _classify_volatility_from_ta(self, core_ta: Dict) -> str:
        """
        Classify volatility pattern from TA Aggregator core_ta.

        Args:
            core_ta: TA Aggregator core_ta dict

        Returns:
            'high_volatile' | 'low_volatile' | 'stable'
        """
        volatility = core_ta.get('volatility_24h')

        if volatility is None or core_ta.get('tge_zero_mode'):
            return 'N/A_TGE_ZERO'

        if volatility >= 10.0:  # >10% daily volatility
            return 'high_volatile'
        elif volatility >= 3.0:  # 3-10% daily volatility
            return 'moderate_volatile'
        else:
            return 'low_volatile'

    def _generate_reasoning_unified(self, ta_data: Dict, analysis_type: str) -> List[str]:
        """
        Generate human-readable reasoning from TA Aggregator data.

        Args:
            ta_data: Full TA Aggregator output
            analysis_type: "SHORT" or "LONG"

        Returns:
            List of reasoning strings
        """
        reasoning = []
        macro = ta_data.get('macro_indices', {})
        core = ta_data.get('core_ta', {})
        advanced = ta_data.get('advanced_ta', {})
        score = ta_data.get('ta_score_normalized', 0)
        recommendation = ta_data.get('ta_recommendation', 'NEUTRAL_TA')

        # Overall score
        reasoning.append(f"📊 TA Score: {score:.1f}/10 ({recommendation})")

        # Macro conditions
        if macro.get('btc_market_structure') == 'downtrend':
            reasoning.append("✅ BTC in downtrend (bearish for alts)")
        if macro.get('eth_market_structure') == 'downtrend':
            reasoning.append("✅ ETH in downtrend (bearish for alts)")

        fear_greed = macro.get('fear_greed_index')
        if fear_greed and fear_greed >= 70:
            reasoning.append(f"✅ Fear & Greed: {fear_greed} (extreme greed, good for shorts)")
        elif fear_greed and fear_greed <= 30:
            reasoning.append(f"⚠️ Fear & Greed: {fear_greed} (extreme fear, caution on shorts)")

        usdt_signal = macro.get('usdt_signal')
        if usdt_signal == 'BEARISH_FOR_ALTS':
            reasoning.append("✅ USDT.D rising (risk-off, bearish for alts)")
        elif usdt_signal == 'BULLISH_FOR_ALTS':
            reasoning.append("❌ USDT.D falling (risk-on, bullish for alts)")

        # Core TA
        rsi_1h = core.get('rsi_1h')
        if rsi_1h and rsi_1h >= 70:
            reasoning.append(f"✅ RSI 1h: {rsi_1h:.0f} (overbought, good for short entry)")
        elif rsi_1h and rsi_1h <= 30:
            reasoning.append(f"❌ RSI 1h: {rsi_1h:.0f} (oversold, poor short entry)")

        # Advanced TA
        ob_signal = advanced.get('order_book_signal')
        if ob_signal == 'SELL_PRESSURE':
            reasoning.append("✅ Order book showing sell pressure")
        elif ob_signal == 'BUY_PRESSURE':
            reasoning.append("❌ Order book showing buy pressure")

        funding = advanced.get('funding_rate')
        if funding and funding > 0.0005:  # 0.05% per funding interval
            reasoning.append(f"✅ Funding rate: {funding*100:.3f}% (longs paying shorts)")
        elif funding and funding < -0.0003:
            reasoning.append(f"❌ Funding rate: {funding*100:.3f}% (shorts paying longs, crowded trade)")

        oi_trend = advanced.get('oi_trend')
        if oi_trend == 'increasing':
            reasoning.append("⚠️ Open interest increasing (building positions)")

        return reasoning


if __name__ == "__main__":
    # Test analyzer
    logging.basicConfig(level=logging.INFO)

    analyzer = AdvancedEntryAnalyzer(exchange_id='binance')

    # Test with BTC (easier to get data than new TGE tokens)
    result = analyzer.analyze("BTC", analysis_type="SHORT")

    print("\n" + "="*60)
    print("ADVANCED ENTRY ANALYSIS TEST")
    print("="*60)
    print(f"\nEntry Readiness: {result['entry_readiness_score']}/10")
    print(f"Recommendation: {result['recommendation']}")
    print(f"\nCore TA:")
    for key, value in result['core_ta'].items():
        print(f"  {key}: {value}")
    print(f"\nAdvanced TA:")
    print(f"  Order Book: {result['advanced_ta']['order_book']}")
    print(f"  Volume: {result['advanced_ta']['volume']}")
    print(f"  Funding: {result['advanced_ta']['funding_rate']}")
    print(f"  OI: {result['advanced_ta']['open_interest']}")
    print(f"\nReasoning:")
    for reason in result['reasoning']:
        print(f"  {reason}")
