#!/usr/bin/env python3
"""
David's 7-Index Tracker - Crypto Market Indices Data Collection

Session 267: Migrated from scripts/helpers/indices_tracker.py to src/data/indices_tracker.py

Fetches real-time data for:
1. BTC.D - Bitcoin Dominance
2. USDT.D - Tether Dominance (PRIMARY INDICATOR)
3. STABLES.C.D - All Stablecoins Dominance
4. OTHERS.D - Altcoin Dominance (excl BTC/ETH)
5. TOTAL - Total Crypto Market Cap
6. TOTAL2 - Total Crypto Market Cap (excl BTC)
7. TOTAL3 - Total Crypto Market Cap (excl BTC+ETH)

Data Source: CoinGecko API (free tier)
Author: Claude Code (Session 68+)
Date: 2025-11-26
Purpose: Week 1 - David's Indices Framework Implementation
"""

import logging
import json
import os
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional
from pycoingecko import CoinGeckoAPI

logger = logging.getLogger(__name__)

# Session 92: File-based cache for macro indicators
# Macro data changes slowly (BTC/ETH structure, Fear & Greed)
# 15-minute TTL reduces API calls without losing actionability
MACRO_CACHE_DIR = Path(__file__).parent.parent / "data" / "cache"
MACRO_CACHE_FILE = MACRO_CACHE_DIR / "macro_indices_cache.json"
MACRO_CACHE_TTL_MINUTES = 30  # Session 238: Extended from 15→30min (BTC/ETH changes slowly)


class IndicesTracker:
    """
    Fetches and analyzes all 7 crypto market indices.

    Design Philosophy:
    - Single API call efficiency (CoinGecko /global + 2 coin lookups)
    - Graceful degradation (missing data returns None, not error)
    - Signal interpretation (BEARISH_FOR_ALTS vs BULLISH_FOR_ALTS)

    Usage:
        tracker = IndicesTracker()
        results = tracker.fetch_all_indices()
        print(results['indices']['usdt_d'])  # USDT Dominance data
    """

    def __init__(self, use_cache: bool = True, cache_ttl_minutes: int = None):
        """
        Initialize CoinGecko API client.

        Args:
            use_cache: If True, use file-based cache for macro data (Session 92)
            cache_ttl_minutes: Override default cache TTL (30 min)
        """
        self.cg = CoinGeckoAPI()
        self.use_cache = use_cache
        self.cache_ttl = cache_ttl_minutes or MACRO_CACHE_TTL_MINUTES

        # Session 238: Reusable HTTP session for connection pooling
        self._session = requests.Session()

        # Ensure cache directory exists
        MACRO_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def _get_cached_indices(self) -> Optional[Dict]:
        """
        Session 92: Retrieve cached macro indices if still valid.

        Returns:
            Cached data dict if valid, None if expired or missing
        """
        if not self.use_cache:
            return None

        try:
            if not MACRO_CACHE_FILE.exists():
                return None

            with open(MACRO_CACHE_FILE, 'r') as f:
                cached = json.load(f)

            # Check if cache is still valid
            cached_time = datetime.fromisoformat(cached.get('timestamp', ''))
            if cached_time.tzinfo is None:
                cached_time = cached_time.replace(tzinfo=timezone.utc)

            now = datetime.now(timezone.utc)
            age_minutes = (now - cached_time).total_seconds() / 60

            if age_minutes <= self.cache_ttl:
                logger.info(f"📦 Using cached macro data ({age_minutes:.1f}m old, TTL={self.cache_ttl}m)")
                cached['_from_cache'] = True
                cached['_cache_age_minutes'] = round(age_minutes, 1)
                return cached

            logger.info(f"🔄 Cache expired ({age_minutes:.1f}m > {self.cache_ttl}m TTL)")
            return None

        except Exception as e:
            logger.debug(f"Cache read failed: {e}")
            return None

    def _save_to_cache(self, data: Dict) -> None:
        """
        Session 92: Save macro indices to file cache.

        Args:
            data: Indices data to cache
        """
        if not self.use_cache:
            return

        try:
            # Ensure timestamp is set
            if 'timestamp' not in data:
                data['timestamp'] = datetime.now(timezone.utc).isoformat()

            with open(MACRO_CACHE_FILE, 'w') as f:
                json.dump(data, f, indent=2, default=str)

            logger.debug(f"💾 Macro indices cached to {MACRO_CACHE_FILE}")

        except Exception as e:
            logger.debug(f"Cache save failed: {e}")

    def invalidate_cache(self) -> bool:
        """
        Session 92: Force invalidate the macro cache.

        Returns:
            True if cache was deleted, False otherwise
        """
        try:
            if MACRO_CACHE_FILE.exists():
                MACRO_CACHE_FILE.unlink()
                logger.info("🗑️ Macro cache invalidated")
                return True
            return False
        except Exception as e:
            logger.debug(f"Cache invalidation failed: {e}")
            return False

    def get_usdt_dominance(self) -> Dict:
        """
        Session 286: Get current USDT dominance data (convenience wrapper).

        Used by Agent 6 playbook generator for macro context.
        Returns cached data if available, fetches fresh if not.

        Returns:
            Dict with:
                - value: float (e.g., 5.76)
                - signal: str (RISK_ON/NEUTRAL/RISK_OFF)
                - note: str (interpretation)
        """
        try:
            indices = self.fetch_all_indices()
            if indices and 'indices' in indices:
                usdt_d = indices['indices'].get('usdt_d', {})
                return {
                    'value': usdt_d.get('value', 0),
                    'signal': usdt_d.get('signal', 'UNKNOWN'),
                    'note': usdt_d.get('note', '')
                }
        except Exception as e:
            logger.debug(f"get_usdt_dominance failed: {e}")

        # Fallback
        return {'value': 0, 'signal': 'UNKNOWN', 'note': 'Data unavailable'}

    def fetch_all_indices(self, force_refresh: bool = False) -> Dict:
        """
        Fetch all 7 indices in one optimized call sequence.

        Session 92: Added file-based caching with 15-minute TTL.
        Macro data changes slowly, so caching reduces API calls significantly.

        API Calls (if not cached):
        1. /global → BTC.D, USDT.D, TOTAL, stablecoins
        2. /coins/bitcoin → BTC market cap
        3. /coins/ethereum → ETH market cap

        Args:
            force_refresh: If True, bypass cache and fetch fresh data

        Returns:
            Dict with structure:
            {
                'timestamp': '2025-11-26T...',
                'indices': {
                    'btc_d': {'value': 56.52, 'signal': 'NEUTRAL', 'note': '...'},
                    'usdt_d': {...},
                    'stables_c_d': {...},
                    'others_d': {...},
                    'total': {...},
                    'total2': {...},
                    'total3': {...}
                },
                'macro_signal': 'BEARISH_FOR_ALTS'  # Aggregated signal
            }
        """
        # Session 92: Check cache first (unless force_refresh)
        if not force_refresh:
            cached = self._get_cached_indices()
            if cached:
                return cached

        logger.info("📊 Fetching David's 7 Indices (fresh)...")

        try:
            # 1. Fetch global data (BTC.D, USDT.D, TOTAL)
            logger.info("   → Fetching global market data...")
            global_data = self.cg.get_global()

            # 2. Fetch BTC and ETH market caps
            logger.info("   → Fetching BTC market data...")
            btc_data = self.cg.get_coin_by_id('bitcoin')
            logger.info("   → Fetching ETH market data...")
            eth_data = self.cg.get_coin_by_id('ethereum')

            # 3. Fetch Fear & Greed Index (from Alternative.me API)
            logger.info("   → Fetching Fear & Greed Index...")
            fear_greed_value = self._fetch_fear_greed_index()

            # 4. Fetch REAL-TIME sentiment from TradingView (Learning 012)
            logger.info("   → Fetching real-time sentiment (TradingView)...")
            realtime_sentiment = self._fetch_realtime_sentiment()

            # Extract base data
            btc_market_cap = btc_data['market_data']['market_cap']['usd']
            eth_market_cap = eth_data['market_data']['market_cap']['usd']
            total_market_cap = global_data['total_market_cap']['usd']

            # Get 24h changes for trend direction
            btc_24h_change = btc_data['market_data'].get('price_change_percentage_24h', 0)
            eth_24h_change = eth_data['market_data'].get('price_change_percentage_24h', 0)
            total_mc_change = global_data.get('market_cap_change_percentage_24h_usd', 0)

            btc_d = global_data['market_cap_percentage']['btc']
            eth_d = global_data['market_cap_percentage'].get('eth', 0)
            usdt_d = global_data['market_cap_percentage']['usdt']
            usdc_d = global_data['market_cap_percentage'].get('usdc', 0)
            dai_d = global_data['market_cap_percentage'].get('dai', 0)
            busd_d = global_data['market_cap_percentage'].get('busd', 0)

            # Calculate derived indices
            stables_c_d = usdt_d + usdc_d + dai_d + busd_d
            others_d = 100 - btc_d - eth_d
            total2 = total_market_cap - btc_market_cap
            total3 = total_market_cap - btc_market_cap - eth_market_cap

            # Calculate TOTAL3 24h change (approximate from overall market change)
            total3_24h_change = total_mc_change  # Approximate, could be refined

            # Build results with signals
            results = {
                'timestamp': datetime.utcnow().isoformat(),
                'indices': {
                    'btc_d': {
                        'value': round(btc_d, 2),
                        'signal': self._interpret_btc_d(btc_d),
                        'note': f'Bitcoin Dominance at {btc_d:.2f}%'
                    },
                    'usdt_d': {
                        'value': round(usdt_d, 2),
                        'signal': self._interpret_usdt_d(usdt_d),
                        'note': f'USDT Dominance at {usdt_d:.2f}%'
                    },
                    'stables_c_d': {
                        'value': round(stables_c_d, 2),
                        'signal': self._interpret_stables_d(stables_c_d),
                        'note': f'All Stablecoins Dominance at {stables_c_d:.2f}%'
                    },
                    'others_d': {
                        'value': round(others_d, 2),
                        'signal': self._interpret_others_d(others_d),
                        'note': f'Altcoin Dominance (excl BTC/ETH) at {others_d:.2f}%'
                    },
                    'total': {
                        'value': int(total_market_cap),
                        'signal': 'INFO',
                        'note': f'Total Market Cap: ${total_market_cap/1e12:.2f}T'
                    },
                    'total2': {
                        'value': int(total2),
                        'signal': 'INFO',
                        'note': f'Total2 (excl BTC): ${total2/1e12:.2f}T'
                    },
                    'total3': {
                        'value': int(total3),
                        'signal': 'INFO',
                        'note': f'Total3 (excl BTC+ETH): ${total3/1e9:.0f}B'
                    },
                    'fear_greed_index': {
                        'value': fear_greed_value,
                        'signal': self._interpret_fear_greed(fear_greed_value) if fear_greed_value else 'UNKNOWN',
                        'note': f'Fear & Greed Index: {fear_greed_value}' if fear_greed_value else 'Fear & Greed Index unavailable'
                    },
                    # Real-time data (Learning 012)
                    'realtime_sentiment': realtime_sentiment,
                    'btc_24h_change': btc_24h_change,
                    'eth_24h_change': eth_24h_change,
                    'total3_24h_change': total3_24h_change,
                    'usdt_d_value': usdt_d,  # Raw value for trend calculation
                },
                'macro_signal': self._calculate_macro_signal(btc_d, usdt_d, others_d),
                'data_freshness': {
                    'timestamp': datetime.utcnow().isoformat(),
                    'fear_greed_daily': True,  # F&G updates once per day
                    'sentiment_realtime': realtime_sentiment.get('source') == 'TradingView (live)',
                    'indices_delay_minutes': 5  # CoinGecko has ~5 min delay
                }
            }

            logger.info("✅ All 7 indices fetched successfully")
            logger.info(f"   BTC.D: {btc_d:.2f}% | USDT.D: {usdt_d:.2f}% | OTHERS.D: {others_d:.2f}%")
            logger.info(f"   Macro Signal: {results['macro_signal']}")

            # Session 92: Save to cache for future requests
            self._save_to_cache(results)

            return results

        except Exception as e:
            logger.error(f"❌ Failed to fetch indices: {e}")
            return self._fallback_result()

    def _interpret_btc_d(self, value: float) -> str:
        """
        Interpret BTC Dominance signal.

        Logic:
        - BTC.D rising → Money flowing to BTC → BEARISH FOR ALTS
        - BTC.D falling → Money flowing to alts → BULLISH FOR ALTS

        Thresholds (from David's framework):
        - ≥60%: Strong BTC dominance (bearish for alts)
        - ≤50%: Weak BTC dominance (bullish for alts)
        - 50-60%: Neutral
        """
        if value >= 60:
            return "BEARISH_FOR_ALTS"
        elif value <= 50:
            return "BULLISH_FOR_ALTS"
        return "NEUTRAL"

    def _interpret_usdt_d(self, value: float) -> str:
        """
        Interpret USDT Dominance signal (PRIMARY INDICATOR).

        Logic:
        - USDT.D ≥6.0% → Risk-off mode → BEARISH FOR ALTS
        - USDT.D ≤5.0% → Risk-on mode → BULLISH FOR ALTS
        - 5.0-6.0%: Neutral

        This is David's primary macro indicator for TGE timing.
        """
        if value >= 6.0:
            return "BEARISH_FOR_ALTS"
        elif value <= 5.0:
            return "BULLISH_FOR_ALTS"
        return "NEUTRAL"

    def _interpret_stables_d(self, value: float) -> str:
        """
        Interpret Total Stablecoins Dominance signal.

        Logic:
        - Stables rising → Money to safety → BEARISH FOR ALTS
        - Stables falling → Money to risk assets → BULLISH FOR ALTS

        Thresholds:
        - ≥8.0%: High stablecoin parking (bearish)
        - ≤6.0%: Low stablecoin parking (bullish)
        """
        if value >= 8.0:
            return "BEARISH_FOR_ALTS"
        elif value <= 6.0:
            return "BULLISH_FOR_ALTS"
        return "NEUTRAL"

    def _interpret_others_d(self, value: float) -> str:
        """
        Interpret OTHERS Dominance signal.

        Logic:
        - OTHERS.D rising → Altcoin season → BULLISH FOR ALTS
        - OTHERS.D falling → Altcoin weakness → BEARISH FOR ALTS

        Thresholds:
        - ≥10%: Strong altcoin season (bullish)
        - ≤7%: Weak altcoin season (bearish)
        """
        if value >= 10:
            return "BULLISH_FOR_ALTS"
        elif value <= 7:
            return "BEARISH_FOR_ALTS"
        return "NEUTRAL"

    def _interpret_fear_greed(self, value: int) -> str:
        """
        Interpret Fear & Greed Index signal.

        Scale: 0-100
        - 0-24: Extreme Fear (potential bounce/BULLISH for contrarians)
        - 25-49: Fear (NEUTRAL to BULLISH)
        - 50-74: Greed (NEUTRAL to BEARISH)
        - 75-100: Extreme Greed (market top/BEARISH)

        For SHORT strategies:
        - Extreme Greed (>75) = BULLISH FOR SHORTS
        - Extreme Fear (<25) = BEARISH FOR SHORTS
        """
        if value >= 75:
            return "EXTREME_GREED"  # Bullish for shorts
        elif value >= 50:
            return "GREED"
        elif value >= 25:
            return "FEAR"
        else:
            return "EXTREME_FEAR"  # Bearish for shorts

    def _fetch_realtime_sentiment(self) -> Dict:
        """
        Fetch real-time sentiment using TradingView scanner API.

        This provides LIVE data vs Fear & Greed which updates only once per day.
        Enhanced with comprehensive 4H indicators for David's execution timing.

        Returns:
            Dict with BTC RSI, ETH RSI, 4H indicators, and live sentiment score
        """
        result = {
            'btc_rsi': None,
            'eth_rsi': None,
            'btc_24h_change': None,
            'eth_24h_change': None,
            'live_sentiment': None,
            'data_age_seconds': 0,
            'source': None,
            # NEW: 4H Execution Indicators (Learning 013)
            'btc_4h': None,
            'eth_4h': None,
        }

        try:
            # Fetch comprehensive indicators including 4H timeframe for execution timing
            response = requests.post(
                'https://scanner.tradingview.com/crypto/scan',
                headers={'Content-Type': 'application/json'},
                json={
                    'symbols': {'tickers': ['BINANCE:BTCUSDT', 'BINANCE:ETHUSDT']},
                    'columns': [
                        # Daily indicators
                        'close', 'change', 'RSI', 'Recommend.All',
                        # 4H indicators (David's primary timeframe)
                        'RSI|240', 'MACD.macd|240', 'MACD.signal|240',
                        'Stoch.K|240', 'Stoch.D|240', 'ADX|240',
                        'Recommend.All|240', 'change|240',
                        # 1H confirmation
                        'RSI|60', 'Recommend.All|60',
                        # EMAs for trend
                        'EMA20|240', 'EMA50|240', 'close|240'
                    ]
                },
                timeout=5
            )
            response.raise_for_status()
            data = response.json()

            if 'data' in data and len(data['data']) >= 2:
                btc_raw = data['data'][0].get('d', [])
                eth_raw = data['data'][1].get('d', [])

                # Parse BTC data
                if len(btc_raw) >= 17:
                    result['btc_rsi'] = round(btc_raw[2], 1) if btc_raw[2] else None
                    result['btc_24h_change'] = round(btc_raw[1], 2) if btc_raw[1] else None

                    # 4H indicators for execution timing
                    result['btc_4h'] = {
                        'rsi': round(btc_raw[4], 1) if btc_raw[4] else None,
                        'macd': round(btc_raw[5], 2) if btc_raw[5] else None,
                        'macd_signal': round(btc_raw[6], 2) if btc_raw[6] else None,
                        'stoch_k': round(btc_raw[7], 1) if btc_raw[7] else None,
                        'stoch_d': round(btc_raw[8], 1) if btc_raw[8] else None,
                        'adx': round(btc_raw[9], 1) if btc_raw[9] else None,
                        'recommendation': self._interpret_recommendation(btc_raw[10]),
                        'change_pct': round(btc_raw[11], 2) if btc_raw[11] else None,
                        'rsi_1h': round(btc_raw[12], 1) if btc_raw[12] else None,
                        'rec_1h': self._interpret_recommendation(btc_raw[13]),
                        'ema20': round(btc_raw[14], 2) if btc_raw[14] else None,
                        'ema50': round(btc_raw[15], 2) if btc_raw[15] else None,
                        'close': round(btc_raw[16], 2) if btc_raw[16] else None,
                    }

                    # Determine trend from EMAs
                    if result['btc_4h']['close'] and result['btc_4h']['ema20'] and result['btc_4h']['ema50']:
                        close = result['btc_4h']['close']
                        ema20 = result['btc_4h']['ema20']
                        ema50 = result['btc_4h']['ema50']
                        if close > ema20 > ema50:
                            result['btc_4h']['trend'] = 'BULLISH'
                        elif close < ema20 < ema50:
                            result['btc_4h']['trend'] = 'BEARISH'
                        else:
                            result['btc_4h']['trend'] = 'MIXED'

                # Parse ETH data
                if len(eth_raw) >= 17:
                    result['eth_rsi'] = round(eth_raw[2], 1) if eth_raw[2] else None
                    result['eth_24h_change'] = round(eth_raw[1], 2) if eth_raw[1] else None

                    result['eth_4h'] = {
                        'rsi': round(eth_raw[4], 1) if eth_raw[4] else None,
                        'macd': round(eth_raw[5], 2) if eth_raw[5] else None,
                        'macd_signal': round(eth_raw[6], 2) if eth_raw[6] else None,
                        'stoch_k': round(eth_raw[7], 1) if eth_raw[7] else None,
                        'stoch_d': round(eth_raw[8], 1) if eth_raw[8] else None,
                        'adx': round(eth_raw[9], 1) if eth_raw[9] else None,
                        'recommendation': self._interpret_recommendation(eth_raw[10]),
                        'change_pct': round(eth_raw[11], 2) if eth_raw[11] else None,
                        'rsi_1h': round(eth_raw[12], 1) if eth_raw[12] else None,
                        'rec_1h': self._interpret_recommendation(eth_raw[13]),
                        'ema20': round(eth_raw[14], 2) if eth_raw[14] else None,
                        'ema50': round(eth_raw[15], 2) if eth_raw[15] else None,
                        'close': round(eth_raw[16], 2) if eth_raw[16] else None,
                    }

                    # Determine trend from EMAs
                    if result['eth_4h']['close'] and result['eth_4h']['ema20'] and result['eth_4h']['ema50']:
                        close = result['eth_4h']['close']
                        ema20 = result['eth_4h']['ema20']
                        ema50 = result['eth_4h']['ema50']
                        if close > ema20 > ema50:
                            result['eth_4h']['trend'] = 'BULLISH'
                        elif close < ema20 < ema50:
                            result['eth_4h']['trend'] = 'BEARISH'
                        else:
                            result['eth_4h']['trend'] = 'MIXED'

                # Calculate live sentiment from RSI (real-time proxy for Fear & Greed)
                # RSI < 30 = Extreme Fear, RSI > 70 = Extreme Greed
                if result['btc_rsi']:
                    rsi = result['btc_rsi']
                    if rsi <= 30:
                        result['live_sentiment'] = int(rsi)  # 0-30 = Extreme Fear
                    elif rsi >= 70:
                        result['live_sentiment'] = int(70 + (rsi - 70))  # 70-100 = Extreme Greed
                    else:
                        # Map 30-70 RSI to 30-70 sentiment
                        result['live_sentiment'] = int(rsi)

                result['source'] = 'TradingView (live)'
                result['data_age_seconds'] = 0

                btc_4h_rsi = result['btc_4h']['rsi'] if result['btc_4h'] else 'N/A'
                btc_4h_rec = result['btc_4h']['recommendation'] if result['btc_4h'] else 'N/A'
                logger.info(f"   ✅ Real-time: BTC RSI={result['btc_rsi']}, 4H RSI={btc_4h_rsi}, 4H Rec={btc_4h_rec}")

        except Exception as e:
            logger.debug(f"   ⚠️ TradingView sentiment failed: {str(e)[:60]}")

        return result

    def _interpret_recommendation(self, value: float) -> str:
        """
        Interpret TradingView recommendation value.

        TradingView returns -1 to 1:
        - Strong Sell: < -0.5
        - Sell: -0.5 to -0.1
        - Neutral: -0.1 to 0.1
        - Buy: 0.1 to 0.5
        - Strong Buy: > 0.5
        """
        if value is None:
            return 'N/A'
        if value < -0.5:
            return 'STRONG_SELL'
        elif value < -0.1:
            return 'SELL'
        elif value < 0.1:
            return 'NEUTRAL'
        elif value < 0.5:
            return 'BUY'
        else:
            return 'STRONG_BUY'

    def fetch_realtime_macro_indices(self) -> Dict:
        """
        Fetch USDT.D and TOTAL3 real-time data from TradingView global scanner.

        These are critical macro indices for TGE short timing:
        - USDT.D rising = risk-off = GOOD for shorts
        - TOTAL3 falling = alt selling pressure = GOOD for shorts

        Uses TradingView global scanner (not crypto scanner) for CRYPTOCAP tickers.

        Returns:
            Dict with usdt_d and total3 real-time data including 4H indicators
        """
        result = {
            'usdt_d': None,
            'total3': None,
            'source': None,
            'error': None
        }

        try:
            response = requests.post(
                'https://scanner.tradingview.com/global/scan',
                headers={'Content-Type': 'application/json'},
                json={
                    'symbols': {'tickers': ['CRYPTOCAP:USDT.D', 'CRYPTOCAP:TOTAL3']},
                    'columns': [
                        'close', 'change',  # Daily
                        'change|240', 'RSI|240', 'Recommend.All|240',  # 4H
                        'change|60', 'RSI|60'  # 1H
                    ]
                },
                timeout=5
            )
            response.raise_for_status()
            data = response.json()

            if 'data' in data and len(data['data']) >= 2:
                usdt_raw = data['data'][0].get('d', [])
                total3_raw = data['data'][1].get('d', [])

                # Parse USDT.D data
                if len(usdt_raw) >= 7:
                    usdt_value = usdt_raw[0]
                    usdt_change = usdt_raw[1]
                    usdt_4h_change = usdt_raw[2]
                    usdt_4h_rsi = usdt_raw[3]
                    usdt_4h_rec = usdt_raw[4]

                    # For shorts: USDT.D rising is GOOD (risk-off)
                    if usdt_4h_change and usdt_4h_change > 0.5:
                        signal = 'FAVORABLE'  # Rising = risk-off = good for shorts
                    elif usdt_4h_change and usdt_4h_change < -0.5:
                        signal = 'HEADWIND'  # Falling = risk-on = bad for shorts
                    else:
                        signal = 'NEUTRAL'

                    result['usdt_d'] = {
                        'value': round(usdt_value, 2) if usdt_value else None,
                        'change_24h': round(usdt_change, 2) if usdt_change else None,
                        'change_4h': round(usdt_4h_change, 2) if usdt_4h_change else None,
                        'rsi_4h': round(usdt_4h_rsi, 1) if usdt_4h_rsi else None,
                        'recommendation_4h': self._interpret_recommendation(usdt_4h_rec),
                        'signal_for_short': signal
                    }

                # Parse TOTAL3 data
                if len(total3_raw) >= 7:
                    total3_value = total3_raw[0]
                    total3_change = total3_raw[1]
                    total3_4h_change = total3_raw[2]
                    total3_4h_rsi = total3_raw[3]
                    total3_4h_rec = total3_raw[4]

                    # For shorts: TOTAL3 falling is GOOD (alt selling pressure)
                    if total3_4h_change and total3_4h_change < -0.5:
                        signal = 'FAVORABLE'  # Falling = alt sell pressure = good for shorts
                    elif total3_4h_change and total3_4h_change > 0.5:
                        signal = 'HEADWIND'  # Rising = alt buying = bad for shorts
                    else:
                        signal = 'NEUTRAL'

                    result['total3'] = {
                        'value': total3_value,  # Raw value in billions
                        'value_formatted': f"${total3_value/1e9:.0f}B" if total3_value else None,
                        'change_24h': round(total3_change, 2) if total3_change else None,
                        'change_4h': round(total3_4h_change, 2) if total3_4h_change else None,
                        'rsi_4h': round(total3_4h_rsi, 1) if total3_4h_rsi else None,
                        'recommendation_4h': self._interpret_recommendation(total3_4h_rec),
                        'signal_for_short': signal
                    }

                result['source'] = 'TradingView (live)'

                usdt_val = result['usdt_d']['value'] if result['usdt_d'] else 'N/A'
                total3_val = result['total3']['value_formatted'] if result['total3'] else 'N/A'
                logger.info(f"   ✅ Macro indices: USDT.D={usdt_val}%, TOTAL3={total3_val}")

        except Exception as e:
            result['error'] = str(e)
            logger.debug(f"   ⚠️ TradingView macro indices failed: {str(e)[:60]}")

        return result

    def fetch_usdt_d_total3_key_levels(self) -> Dict:
        """
        Fetch USDT.D, TOTAL1, TOTAL2, and TOTAL3 key S/R levels from TradingView.

        SESSION 93+: David's Feedback (POWER 8 Dec 2025)
        "Il faudrait plutôt se baser sur des key levels. Comment se préparer pour les prochains coins?"

        SESSION 302: Added TOTAL1 and TOTAL2 per Sherlock methodology
        - Sherlock focuses on top 1-100 (TOTAL1) and top 200 (TOTAL2) coins
        - TOTAL1 = Total Crypto Market Cap (all coins)
        - TOTAL2 = Total Crypto Market Cap (excl BTC)
        - TOTAL3 = Total Crypto Market Cap (excl BTC+ETH)

        Key insight: David needs S/R levels for USDT.D and TOTAL indices to time entries,
        not just current values. These indices dictate altcoin direction.

        For TGE Shorts:
        - USDT.D near support = likely to bounce up = FAVORABLE for shorts (risk-off)
        - USDT.D near resistance = likely to reject down = HEADWIND for shorts (risk-on)
        - TOTAL indices near resistance = likely to reject = FAVORABLE for shorts
        - TOTAL indices near support = likely to bounce = HEADWIND for shorts

        Returns:
            Dict with USDT.D, TOTAL1, TOTAL2, TOTAL3 S/R levels for David's manual checks
        """
        result = {
            'usdt_d': None,
            'total1': None,  # Session 302: Added TOTAL1 (all coins)
            'total2': None,  # Session 302: Added TOTAL2 (excl BTC)
            'total3': None,
            'timing_guidance': None,
            'source': None,
            'error': None
        }

        try:
            # Session 302: Fetch USDT.D, TOTAL1, TOTAL2, TOTAL3 (4 indices)
            response = requests.post(
                'https://scanner.tradingview.com/global/scan',
                headers={'Content-Type': 'application/json'},
                json={
                    'symbols': {'tickers': [
                        'CRYPTOCAP:USDT.D',
                        'CRYPTOCAP:TOTAL',    # TOTAL1 - all coins
                        'CRYPTOCAP:TOTAL2',   # TOTAL2 - excl BTC
                        'CRYPTOCAP:TOTAL3'    # TOTAL3 - excl BTC+ETH
                    ]},
                    'columns': [
                        'close',
                        # Daily pivots for all indices
                        'Pivot.M.Classic.S3', 'Pivot.M.Classic.S2', 'Pivot.M.Classic.S1',
                        'Pivot.M.Classic.Middle',
                        'Pivot.M.Classic.R1', 'Pivot.M.Classic.R2', 'Pivot.M.Classic.R3',
                        # Daily EMAs as dynamic S/R
                        'EMA20', 'EMA50', 'EMA100',
                        # Daily change and RSI
                        'change', 'RSI',
                        # Daily timeframe (David prefers daily for accuracy)
                        'high', 'low'
                    ]
                },
                timeout=5
            )
            response.raise_for_status()
            data = response.json()

            if 'data' in data and len(data['data']) >= 4:
                usdt_raw = data['data'][0].get('d', [])
                total1_raw = data['data'][1].get('d', [])  # Session 302: TOTAL1
                total2_raw = data['data'][2].get('d', [])  # Session 302: TOTAL2
                total3_raw = data['data'][3].get('d', [])

                # Parse USDT.D S/R levels
                if len(usdt_raw) >= 15:
                    close = usdt_raw[0]
                    s3, s2, s1 = usdt_raw[1], usdt_raw[2], usdt_raw[3]
                    pivot = usdt_raw[4]
                    r1, r2, r3 = usdt_raw[5], usdt_raw[6], usdt_raw[7]
                    ema20, ema50, ema100 = usdt_raw[8], usdt_raw[9], usdt_raw[10]
                    change = usdt_raw[11]
                    rsi = usdt_raw[12]
                    high, low = usdt_raw[13], usdt_raw[14]

                    # Find nearest level for USDT.D
                    levels = {'R3': r3, 'R2': r2, 'R1': r1, 'Pivot': pivot, 'S1': s1, 'S2': s2, 'S3': s3}
                    nearest_level = None
                    nearest_dist_pct = float('inf')
                    for level_name, level_value in levels.items():
                        if level_value and close:
                            dist_pct = abs((close - level_value) / close) * 100
                            if dist_pct < nearest_dist_pct:
                                nearest_dist_pct = dist_pct
                                nearest_level = level_name

                    # USDT.D interpretation for shorts
                    # USDT.D at support = will bounce UP = FAVORABLE (risk-off coming)
                    # USDT.D at resistance = will reject DOWN = HEADWIND (risk-on coming)
                    near_support = any(
                        abs((close - s) / close) * 100 < 1.5
                        for s in [s1, s2, s3] if s and close
                    )
                    near_resistance = any(
                        abs((close - r) / close) * 100 < 1.5
                        for r in [r1, r2, r3] if r and close
                    )

                    if near_support:
                        signal = 'FAVORABLE'
                        guidance = 'USDT.D at support - likely to bounce UP (risk-off) - GOOD for shorts'
                    elif near_resistance:
                        signal = 'HEADWIND'
                        guidance = 'USDT.D at resistance - likely to reject DOWN (risk-on) - BAD for shorts'
                    elif change and change > 0:
                        signal = 'FAVORABLE'
                        guidance = 'USDT.D trending UP (risk-off) - shorts have tailwind'
                    elif change and change < 0:
                        signal = 'HEADWIND'
                        guidance = 'USDT.D trending DOWN (risk-on) - shorts face headwind'
                    else:
                        signal = 'NEUTRAL'
                        guidance = 'USDT.D at pivot - could go either way'

                    result['usdt_d'] = {
                        'current': round(close, 3) if close else None,
                        'daily_high': round(high, 3) if high else None,
                        'daily_low': round(low, 3) if low else None,
                        'daily_change_pct': round(change, 2) if change else None,
                        'rsi': round(rsi, 1) if rsi else None,
                        'pivots': {
                            'S3': round(s3, 3) if s3 else None,
                            'S2': round(s2, 3) if s2 else None,
                            'S1': round(s1, 3) if s1 else None,
                            'Pivot': round(pivot, 3) if pivot else None,
                            'R1': round(r1, 3) if r1 else None,
                            'R2': round(r2, 3) if r2 else None,
                            'R3': round(r3, 3) if r3 else None,
                        },
                        'emas': {
                            'EMA20': round(ema20, 3) if ema20 else None,
                            'EMA50': round(ema50, 3) if ema50 else None,
                            'EMA100': round(ema100, 3) if ema100 else None,
                        },
                        'nearest_level': nearest_level,
                        'nearest_dist_pct': round(nearest_dist_pct, 2) if nearest_dist_pct != float('inf') else None,
                        'signal_for_short': signal,
                        'guidance': guidance
                    }

                # Parse TOTAL3 S/R levels
                if len(total3_raw) >= 15:
                    close = total3_raw[0]
                    s3, s2, s1 = total3_raw[1], total3_raw[2], total3_raw[3]
                    pivot = total3_raw[4]
                    r1, r2, r3 = total3_raw[5], total3_raw[6], total3_raw[7]
                    ema20, ema50, ema100 = total3_raw[8], total3_raw[9], total3_raw[10]
                    change = total3_raw[11]
                    rsi = total3_raw[12]
                    high, low = total3_raw[13], total3_raw[14]

                    # Find nearest level for TOTAL3
                    levels = {'R3': r3, 'R2': r2, 'R1': r1, 'Pivot': pivot, 'S1': s1, 'S2': s2, 'S3': s3}
                    nearest_level = None
                    nearest_dist_pct = float('inf')
                    for level_name, level_value in levels.items():
                        if level_value and close:
                            dist_pct = abs((close - level_value) / close) * 100
                            if dist_pct < nearest_dist_pct:
                                nearest_dist_pct = dist_pct
                                nearest_level = level_name

                    # TOTAL3 interpretation for shorts
                    # TOTAL3 at resistance = will reject DOWN = FAVORABLE for shorts
                    # TOTAL3 at support = will bounce UP = HEADWIND for shorts
                    near_support = any(
                        abs((close - s) / close) * 100 < 2.0
                        for s in [s1, s2, s3] if s and close
                    )
                    near_resistance = any(
                        abs((close - r) / close) * 100 < 2.0
                        for r in [r1, r2, r3] if r and close
                    )

                    if near_resistance:
                        signal = 'FAVORABLE'
                        guidance = 'TOTAL3 at resistance - likely to reject DOWN - GOOD for shorts'
                    elif near_support:
                        signal = 'HEADWIND'
                        guidance = 'TOTAL3 at support - likely to bounce UP - BAD for shorts'
                    elif change and change < 0:
                        signal = 'FAVORABLE'
                        guidance = 'TOTAL3 trending DOWN (alt selling) - shorts have tailwind'
                    elif change and change > 0:
                        signal = 'HEADWIND'
                        guidance = 'TOTAL3 trending UP (alt buying) - shorts face headwind'
                    else:
                        signal = 'NEUTRAL'
                        guidance = 'TOTAL3 at pivot - could go either way'

                    result['total3'] = {
                        'current': close,  # Raw value in billions
                        'current_formatted': f"${close/1e9:.1f}B" if close else None,
                        'daily_high': high,
                        'daily_low': low,
                        'daily_high_formatted': f"${high/1e9:.1f}B" if high else None,
                        'daily_low_formatted': f"${low/1e9:.1f}B" if low else None,
                        'daily_change_pct': round(change, 2) if change else None,
                        'rsi': round(rsi, 1) if rsi else None,
                        'pivots': {
                            'S3': round(s3/1e9, 1) if s3 else None,
                            'S2': round(s2/1e9, 1) if s2 else None,
                            'S1': round(s1/1e9, 1) if s1 else None,
                            'Pivot': round(pivot/1e9, 1) if pivot else None,
                            'R1': round(r1/1e9, 1) if r1 else None,
                            'R2': round(r2/1e9, 1) if r2 else None,
                            'R3': round(r3/1e9, 1) if r3 else None,
                        },
                        'emas': {
                            'EMA20': round(ema20/1e9, 1) if ema20 else None,
                            'EMA50': round(ema50/1e9, 1) if ema50 else None,
                            'EMA100': round(ema100/1e9, 1) if ema100 else None,
                        },
                        'nearest_level': nearest_level,
                        'nearest_dist_pct': round(nearest_dist_pct, 2) if nearest_dist_pct != float('inf') else None,
                        'signal_for_short': signal,
                        'guidance': guidance
                    }

                # Session 302: Parse TOTAL1 S/R levels (all coins)
                if len(total1_raw) >= 15:
                    close = total1_raw[0]
                    s3, s2, s1 = total1_raw[1], total1_raw[2], total1_raw[3]
                    pivot = total1_raw[4]
                    r1, r2, r3 = total1_raw[5], total1_raw[6], total1_raw[7]
                    ema20, ema50, ema100 = total1_raw[8], total1_raw[9], total1_raw[10]
                    change = total1_raw[11]
                    rsi = total1_raw[12]
                    high, low = total1_raw[13], total1_raw[14]

                    # Find nearest level for TOTAL1
                    levels = {'R3': r3, 'R2': r2, 'R1': r1, 'Pivot': pivot, 'S1': s1, 'S2': s2, 'S3': s3}
                    nearest_level = None
                    nearest_dist_pct = float('inf')
                    for level_name, level_value in levels.items():
                        if level_value and close:
                            dist_pct = abs((close - level_value) / close) * 100
                            if dist_pct < nearest_dist_pct:
                                nearest_dist_pct = dist_pct
                                nearest_level = level_name

                    # TOTAL1 interpretation for shorts (same logic as TOTAL3)
                    near_support = any(
                        abs((close - s) / close) * 100 < 2.0
                        for s in [s1, s2, s3] if s and close
                    )
                    near_resistance = any(
                        abs((close - r) / close) * 100 < 2.0
                        for r in [r1, r2, r3] if r and close
                    )

                    if near_resistance:
                        signal = 'FAVORABLE'
                        guidance = 'TOTAL1 at resistance - likely to reject DOWN - GOOD for shorts'
                    elif near_support:
                        signal = 'HEADWIND'
                        guidance = 'TOTAL1 at support - likely to bounce UP - BAD for shorts'
                    elif change and change < 0:
                        signal = 'FAVORABLE'
                        guidance = 'TOTAL1 trending DOWN (market selling) - shorts have tailwind'
                    elif change and change > 0:
                        signal = 'HEADWIND'
                        guidance = 'TOTAL1 trending UP (market buying) - shorts face headwind'
                    else:
                        signal = 'NEUTRAL'
                        guidance = 'TOTAL1 at pivot - could go either way'

                    result['total1'] = {
                        'current': close,
                        'current_formatted': f"${close/1e12:.2f}T" if close else None,
                        'daily_high': high,
                        'daily_low': low,
                        'daily_high_formatted': f"${high/1e12:.2f}T" if high else None,
                        'daily_low_formatted': f"${low/1e12:.2f}T" if low else None,
                        'daily_change_pct': round(change, 2) if change else None,
                        'rsi': round(rsi, 1) if rsi else None,
                        'pivots': {
                            'S3': round(s3/1e12, 2) if s3 else None,
                            'S2': round(s2/1e12, 2) if s2 else None,
                            'S1': round(s1/1e12, 2) if s1 else None,
                            'Pivot': round(pivot/1e12, 2) if pivot else None,
                            'R1': round(r1/1e12, 2) if r1 else None,
                            'R2': round(r2/1e12, 2) if r2 else None,
                            'R3': round(r3/1e12, 2) if r3 else None,
                        },
                        'emas': {
                            'EMA20': round(ema20/1e12, 2) if ema20 else None,
                            'EMA50': round(ema50/1e12, 2) if ema50 else None,
                            'EMA100': round(ema100/1e12, 2) if ema100 else None,
                        },
                        'nearest_level': nearest_level,
                        'nearest_dist_pct': round(nearest_dist_pct, 2) if nearest_dist_pct != float('inf') else None,
                        'signal_for_short': signal,
                        'guidance': guidance
                    }

                # Session 302: Parse TOTAL2 S/R levels (excl BTC)
                if len(total2_raw) >= 15:
                    close = total2_raw[0]
                    s3, s2, s1 = total2_raw[1], total2_raw[2], total2_raw[3]
                    pivot = total2_raw[4]
                    r1, r2, r3 = total2_raw[5], total2_raw[6], total2_raw[7]
                    ema20, ema50, ema100 = total2_raw[8], total2_raw[9], total2_raw[10]
                    change = total2_raw[11]
                    rsi = total2_raw[12]
                    high, low = total2_raw[13], total2_raw[14]

                    # Find nearest level for TOTAL2
                    levels = {'R3': r3, 'R2': r2, 'R1': r1, 'Pivot': pivot, 'S1': s1, 'S2': s2, 'S3': s3}
                    nearest_level = None
                    nearest_dist_pct = float('inf')
                    for level_name, level_value in levels.items():
                        if level_value and close:
                            dist_pct = abs((close - level_value) / close) * 100
                            if dist_pct < nearest_dist_pct:
                                nearest_dist_pct = dist_pct
                                nearest_level = level_name

                    # TOTAL2 interpretation for shorts (same logic as TOTAL3)
                    near_support = any(
                        abs((close - s) / close) * 100 < 2.0
                        for s in [s1, s2, s3] if s and close
                    )
                    near_resistance = any(
                        abs((close - r) / close) * 100 < 2.0
                        for r in [r1, r2, r3] if r and close
                    )

                    if near_resistance:
                        signal = 'FAVORABLE'
                        guidance = 'TOTAL2 at resistance - likely to reject DOWN - GOOD for shorts'
                    elif near_support:
                        signal = 'HEADWIND'
                        guidance = 'TOTAL2 at support - likely to bounce UP - BAD for shorts'
                    elif change and change < 0:
                        signal = 'FAVORABLE'
                        guidance = 'TOTAL2 trending DOWN (alt selling) - shorts have tailwind'
                    elif change and change > 0:
                        signal = 'HEADWIND'
                        guidance = 'TOTAL2 trending UP (alt buying) - shorts face headwind'
                    else:
                        signal = 'NEUTRAL'
                        guidance = 'TOTAL2 at pivot - could go either way'

                    result['total2'] = {
                        'current': close,
                        'current_formatted': f"${close/1e12:.2f}T" if close else None,
                        'daily_high': high,
                        'daily_low': low,
                        'daily_high_formatted': f"${high/1e12:.2f}T" if high else None,
                        'daily_low_formatted': f"${low/1e12:.2f}T" if low else None,
                        'daily_change_pct': round(change, 2) if change else None,
                        'rsi': round(rsi, 1) if rsi else None,
                        'pivots': {
                            'S3': round(s3/1e12, 2) if s3 else None,
                            'S2': round(s2/1e12, 2) if s2 else None,
                            'S1': round(s1/1e12, 2) if s1 else None,
                            'Pivot': round(pivot/1e12, 2) if pivot else None,
                            'R1': round(r1/1e12, 2) if r1 else None,
                            'R2': round(r2/1e12, 2) if r2 else None,
                            'R3': round(r3/1e12, 2) if r3 else None,
                        },
                        'emas': {
                            'EMA20': round(ema20/1e12, 2) if ema20 else None,
                            'EMA50': round(ema50/1e12, 2) if ema50 else None,
                            'EMA100': round(ema100/1e12, 2) if ema100 else None,
                        },
                        'nearest_level': nearest_level,
                        'nearest_dist_pct': round(nearest_dist_pct, 2) if nearest_dist_pct != float('inf') else None,
                        'signal_for_short': signal,
                        'guidance': guidance
                    }

                # Session 302: Overall timing guidance with all TOTAL indices
                usdt_signal = result['usdt_d']['signal_for_short'] if result['usdt_d'] else 'UNKNOWN'
                total1_signal = result['total1']['signal_for_short'] if result['total1'] else 'UNKNOWN'
                total2_signal = result['total2']['signal_for_short'] if result['total2'] else 'UNKNOWN'
                total3_signal = result['total3']['signal_for_short'] if result['total3'] else 'UNKNOWN'

                # Count favorable signals
                favorable_count = sum(1 for s in [usdt_signal, total1_signal, total2_signal, total3_signal] if s == 'FAVORABLE')
                headwind_count = sum(1 for s in [usdt_signal, total1_signal, total2_signal, total3_signal] if s == 'HEADWIND')

                if favorable_count >= 3:
                    result['timing_guidance'] = f'OPTIMAL - {favorable_count}/4 indices favor shorts'
                elif headwind_count >= 3:
                    result['timing_guidance'] = f'WAIT - {headwind_count}/4 indices unfavorable for shorts'
                elif favorable_count >= 2:
                    result['timing_guidance'] = f'FAVORABLE - {favorable_count}/4 indices favor shorts'
                elif headwind_count >= 2:
                    result['timing_guidance'] = f'CAUTION - {headwind_count}/4 indices unfavorable'
                else:
                    result['timing_guidance'] = 'MIXED - No clear directional consensus'

                result['source'] = 'TradingView (live)'

                logger.info(f"   ✅ USDT.D/TOTAL1/TOTAL2/TOTAL3 S/R: {result['timing_guidance']}")

        except Exception as e:
            result['error'] = str(e)
            logger.debug(f"   ⚠️ TradingView USDT.D/TOTAL3 S/R failed: {str(e)[:60]}")

        return result

    def fetch_btc_eth_sr_levels(self) -> Dict:
        """
        Fetch BTC and ETH Support/Resistance levels from TradingView.

        This provides key S/R levels for TGE short timing:
        - Monthly Pivot Points (Classic): S1, S2, S3, Pivot, R1, R2, R3
        - Weekly Pivot Points: More granular S/R for shorter timeframes
        - Key EMAs as dynamic S/R: 20, 50, 100, 200 (daily)

        For TGE Shorts:
        - Price near R1/R2/R3 = Resistance rejection likely = FAVORABLE for shorts
        - Price near S1/S2/S3 = Support bounce likely = HEADWIND for shorts
        - Price near Pivot = Could go either way = NEUTRAL

        Returns:
            Dict with BTC and ETH S/R levels and proximity analysis
        """
        result = {
            'btc': None,
            'eth': None,
            'source': None,
            'error': None
        }

        try:
            response = requests.post(
                'https://scanner.tradingview.com/crypto/scan',
                headers={'Content-Type': 'application/json'},
                json={
                    'symbols': {'tickers': ['BINANCE:BTCUSDT', 'BINANCE:ETHUSDT']},
                    'columns': [
                        'close',
                        # Monthly Classic Pivots
                        'Pivot.M.Classic.S3', 'Pivot.M.Classic.S2', 'Pivot.M.Classic.S1',
                        'Pivot.M.Classic.Middle',
                        'Pivot.M.Classic.R1', 'Pivot.M.Classic.R2', 'Pivot.M.Classic.R3',
                        # Key EMAs (daily) as dynamic S/R
                        'EMA20', 'EMA50', 'EMA100', 'EMA200'
                    ]
                },
                timeout=5
            )
            response.raise_for_status()
            data = response.json()

            if 'data' in data and len(data['data']) >= 2:
                for i, symbol in enumerate(['btc', 'eth']):
                    raw = data['data'][i].get('d', [])

                    if len(raw) >= 12:
                        close = raw[0]
                        s3, s2, s1 = raw[1], raw[2], raw[3]
                        pivot = raw[4]
                        r1, r2, r3 = raw[5], raw[6], raw[7]
                        ema20, ema50, ema100, ema200 = raw[8], raw[9], raw[10], raw[11]

                        # Calculate proximity to key levels
                        levels = {
                            'R3': r3, 'R2': r2, 'R1': r1,
                            'Pivot': pivot,
                            'S1': s1, 'S2': s2, 'S3': s3,
                            'EMA20': ema20, 'EMA50': ema50,
                            'EMA100': ema100, 'EMA200': ema200
                        }

                        # Find nearest level
                        nearest_level = None
                        nearest_dist_pct = float('inf')
                        for level_name, level_value in levels.items():
                            if level_value:
                                dist_pct = abs((close - level_value) / close) * 100
                                if dist_pct < nearest_dist_pct:
                                    nearest_dist_pct = dist_pct
                                    nearest_level = level_name

                        # Determine signal for shorts based on position
                        if close and r1 and s1:
                            above_pivot = close > pivot if pivot else False

                            # Near resistance (within 2% of R1/R2/R3) = FAVORABLE for shorts
                            near_resistance = any(
                                abs((close - r) / close) * 100 < 2.0
                                for r in [r1, r2, r3] if r
                            )

                            # Near support (within 2% of S1/S2/S3) = HEADWIND for shorts
                            near_support = any(
                                abs((close - s) / close) * 100 < 2.0
                                for s in [s1, s2, s3] if s
                            )

                            if near_resistance:
                                signal = 'FAVORABLE'
                                signal_note = f'Near {nearest_level} resistance'
                            elif near_support:
                                signal = 'HEADWIND'
                                signal_note = f'Near {nearest_level} support'
                            elif above_pivot:
                                signal = 'NEUTRAL_HIGH'
                                signal_note = 'Above pivot, room to fall'
                            else:
                                signal = 'NEUTRAL_LOW'
                                signal_note = 'Below pivot, may bounce'
                        else:
                            signal = 'UNKNOWN'
                            signal_note = 'Insufficient data'

                        result[symbol] = {
                            'close': round(close, 2) if close else None,
                            'pivots': {
                                'S3': round(s3, 2) if s3 else None,
                                'S2': round(s2, 2) if s2 else None,
                                'S1': round(s1, 2) if s1 else None,
                                'Pivot': round(pivot, 2) if pivot else None,
                                'R1': round(r1, 2) if r1 else None,
                                'R2': round(r2, 2) if r2 else None,
                                'R3': round(r3, 2) if r3 else None,
                            },
                            'emas': {
                                'EMA20': round(ema20, 2) if ema20 else None,
                                'EMA50': round(ema50, 2) if ema50 else None,
                                'EMA100': round(ema100, 2) if ema100 else None,
                                'EMA200': round(ema200, 2) if ema200 else None,
                            },
                            'nearest_level': nearest_level,
                            'nearest_dist_pct': round(nearest_dist_pct, 2),
                            'signal_for_short': signal,
                            'signal_note': signal_note
                        }

                result['source'] = 'TradingView (live)'

                btc_signal = result['btc']['signal_for_short'] if result['btc'] else 'N/A'
                eth_signal = result['eth']['signal_for_short'] if result['eth'] else 'N/A'
                logger.info(f"   ✅ S/R Levels: BTC={btc_signal}, ETH={eth_signal}")

        except Exception as e:
            result['error'] = str(e)
            logger.debug(f"   ⚠️ TradingView S/R levels failed: {str(e)[:60]}")

        return result

    def _fetch_fear_greed_index(self) -> Optional[int]:
        """
        Fetch Fear & Greed Index from multiple sources (waterfall strategy).

        Priority (David's preferred sources):
        1. Alternative.me (most reliable, free API)
        2. Coinglass (professional data provider)
        3. CryptoRank (backup source)

        NOTE: This updates only once per day! For real-time sentiment,
        use _fetch_realtime_sentiment() which uses TradingView RSI.

        Returns:
            int: Fear & Greed value (0-100) or None if all sources fail
        """
        # Source 1: Alternative.me (fastest, most reliable)
        # Session 238: Use self._session for connection pooling
        try:
            response = self._session.get(
                'https://api.alternative.me/fng/',
                timeout=5
            )
            response.raise_for_status()
            data = response.json()

            if 'data' in data and len(data['data']) > 0:
                value = int(data['data'][0]['value'])
                # Check data age
                timestamp = int(data['data'][0].get('timestamp', 0))
                if timestamp:
                    age_hours = (datetime.utcnow().timestamp() - timestamp) / 3600
                    logger.info(f"   ✅ Fear & Greed Index: {value} (source: Alternative.me, {age_hours:.1f}h old)")
                else:
                    logger.info(f"   ✅ Fear & Greed Index: {value} (source: Alternative.me)")
                return value

        except Exception as e:
            logger.debug(f"   ⚠️ Alternative.me failed: {str(e)[:60]}")

        # Source 2: Coinglass
        try:
            response = self._session.get(
                'https://open-api.coinglass.com/public/v2/indicator/fear_greed',
                timeout=5
            )
            response.raise_for_status()
            data = response.json()

            # Parse Coinglass response format
            if data.get('success') and 'data' in data:
                value = int(data['data'].get('value', 0))
                if 0 <= value <= 100:
                    logger.info(f"   ✅ Fear & Greed Index: {value} (source: Coinglass)")
                    return value

        except Exception as e:
            logger.debug(f"   ⚠️ Coinglass failed: {str(e)[:60]}")

        # Source 3: CryptoRank
        try:
            response = self._session.get(
                'https://api.cryptorank.io/v1/fear-greed',
                timeout=5,
                headers={'User-Agent': 'DACLE/1.0'}
            )
            response.raise_for_status()
            data = response.json()

            # Parse CryptoRank response format
            if 'data' in data:
                value = int(data['data'].get('value', 0))
                if 0 <= value <= 100:
                    logger.info(f"   ✅ Fear & Greed Index: {value} (source: CryptoRank)")
                    return value

        except Exception as e:
            logger.debug(f"   ⚠️ CryptoRank failed: {str(e)[:60]}")

        # All sources failed
        logger.warning(f"   ❌ Fear & Greed Index unavailable (all 3 sources failed)")
        return None

    def fetch_btc_d_trend(self) -> Dict:
        """
        Fetch BTCDOM trend direction from TradingView.

        Session 180 (Learning 021): Sherlock's insight - BTCDOM direction matters
        more than absolute value for timing altcoin trades.

        - Rising BTCDOM = money flowing to BTC = alts bleeding = FAVORABLE for shorts
        - Falling BTCDOM = alt season = HEADWIND for shorts
        - At resistance = about to rotate to alts = WAIT for shorts

        Key Level (Sherlock): 4650 region is major resistance on BTCDOM chart

        Returns:
            Dict with trend data and signal for shorts
        """
        result = {
            'current_value': None,
            'change_4h': None,
            'change_24h': None,
            'direction': 'UNKNOWN',  # RISING/FALLING/FLAT/BREAKOUT
            'near_resistance': False,
            'near_support': False,
            'breakout_above_r1': False,  # NEW: Sherlock's strongest signal
            'nearest_level': None,
            'nearest_dist_pct': None,
            'r1_level': None,  # Store R1 for reference
            'rsi': None,
            'signal_for_short': 'NEUTRAL',
            'guidance': None,
            'source': None,
            'error': None
        }

        try:
            response = requests.post(
                'https://scanner.tradingview.com/global/scan',
                headers={'Content-Type': 'application/json'},
                json={
                    'symbols': {'tickers': ['CRYPTOCAP:BTC.D']},
                    'columns': [
                        'close', 'change',  # Daily
                        'change|240', 'RSI|240', 'Recommend.All|240',  # 4H
                        'change|60', 'RSI|60',  # 1H
                        # Monthly pivots for key levels
                        'Pivot.M.Classic.S1', 'Pivot.M.Classic.S2',
                        'Pivot.M.Classic.R1', 'Pivot.M.Classic.R2',
                        'EMA20', 'EMA50'
                    ]
                },
                timeout=5
            )
            response.raise_for_status()
            data = response.json()

            if 'data' in data and len(data['data']) >= 1:
                raw = data['data'][0].get('d', [])

                if len(raw) >= 13:
                    close = raw[0]
                    change_24h = raw[1]
                    change_4h = raw[2]
                    rsi_4h = raw[3]
                    rec_4h = raw[4]
                    change_1h = raw[5]
                    rsi_1h = raw[6]
                    s1 = raw[7]
                    s2 = raw[8]
                    r1 = raw[9]
                    r2 = raw[10]
                    ema20 = raw[11]
                    ema50 = raw[12]

                    result['current_value'] = round(close, 2) if close else None
                    result['change_4h'] = round(change_4h, 2) if change_4h else None
                    result['change_24h'] = round(change_24h, 2) if change_24h else None
                    result['rsi'] = round(rsi_4h, 1) if rsi_4h else None
                    result['r1_level'] = round(r1, 2) if r1 else None

                    # Session 180 (L021 Update): Check for BREAKOUT above R1
                    # Sherlock: "A daily close above $4690 level would further seal the deal for altcoins"
                    # This is the STRONGEST short signal - BTCDOM breaking above resistance
                    if close and r1 and close > r1:
                        result['breakout_above_r1'] = True
                        result['direction'] = 'BREAKOUT'
                    # Determine trend direction based on 4H change
                    # Sherlock uses 4H timeframe for entries
                    elif change_4h is not None:
                        if change_4h > 0.3:
                            result['direction'] = 'RISING'
                        elif change_4h < -0.3:
                            result['direction'] = 'FALLING'
                        else:
                            result['direction'] = 'FLAT'

                    # Check proximity to key levels (within 1.5%)
                    if close and r1:
                        dist_r1 = abs((close - r1) / close) * 100
                        if dist_r1 < 1.5:
                            result['near_resistance'] = True
                            result['nearest_level'] = 'R1'
                            result['nearest_dist_pct'] = round(dist_r1, 2)
                    if close and r2 and not result['near_resistance']:
                        dist_r2 = abs((close - r2) / close) * 100
                        if dist_r2 < 1.5:
                            result['near_resistance'] = True
                            result['nearest_level'] = 'R2'
                            result['nearest_dist_pct'] = round(dist_r2, 2)

                    if close and s1 and not result['near_resistance']:
                        dist_s1 = abs((close - s1) / close) * 100
                        if dist_s1 < 1.5:
                            result['near_support'] = True
                            result['nearest_level'] = 'S1'
                            result['nearest_dist_pct'] = round(dist_s1, 2)
                    if close and s2 and not result['near_resistance'] and not result['near_support']:
                        dist_s2 = abs((close - s2) / close) * 100
                        if dist_s2 < 1.5:
                            result['near_support'] = True
                            result['nearest_level'] = 'S2'
                            result['nearest_dist_pct'] = round(dist_s2, 2)

                    # Generate signal for shorts based on Sherlock's framework
                    # BREAKOUT above R1 = strongest signal (L021 screenshot insight)
                    # Rising BTCDOM = alts bleeding = FAVORABLE
                    # At resistance = about to rotate = HEADWIND (alts pump)
                    # Falling BTCDOM = alt season = HEADWIND
                    if result['breakout_above_r1']:
                        result['signal_for_short'] = 'MAXIMUM'
                        result['guidance'] = f'BTCDOM BREAKOUT above R1 ({result["r1_level"]}) - MAXIMUM short signal! Big caps will dump hard'
                    elif result['near_resistance']:
                        result['signal_for_short'] = 'HEADWIND'
                        result['guidance'] = 'BTCDOM at resistance - likely to reject = alts may pump soon'
                    elif result['near_support'] and result['direction'] == 'RISING':
                        result['signal_for_short'] = 'FAVORABLE'
                        result['guidance'] = 'BTCDOM bouncing from support - money to BTC = alts bleeding'
                    elif result['direction'] == 'RISING':
                        result['signal_for_short'] = 'FAVORABLE'
                        result['guidance'] = 'BTCDOM rising = money flowing to BTC = alts dumping'
                    elif result['direction'] == 'FALLING':
                        result['signal_for_short'] = 'HEADWIND'
                        result['guidance'] = 'BTCDOM falling = alt season in progress = shorts risky'
                    else:
                        result['signal_for_short'] = 'NEUTRAL'
                        result['guidance'] = 'BTCDOM flat - no clear directional signal'

                    result['source'] = 'TradingView (live)'

                    logger.info(f"   ✅ BTCDOM Trend: {result['direction']} ({result['change_4h']:+.2f}% 4H) | Signal: {result['signal_for_short']}")

        except Exception as e:
            result['error'] = str(e)
            logger.debug(f"   ⚠️ BTCDOM trend fetch failed: {str(e)[:60]}")

        return result

    def fetch_btcdom_total3_confluence(self) -> Dict:
        """
        Fetch BTCDOM + TOTAL3 confluence signal for maximum short conviction.

        Session 180 (Learning 021): When BTCDOM rising AND TOTAL3 falling,
        this is the OPTIMAL macro setup for TGE shorts.

        Returns:
            Dict with confluence status and confidence adjustment
        """
        result = {
            'btc_d_trend': None,
            'btc_d_breakout': False,  # NEW: Sherlock's strongest signal
            'total3_trend': None,
            'confluence': 'UNKNOWN',  # MAXIMUM, OPTIMAL, FAVORABLE, NEUTRAL, HEADWIND
            'confidence_adjustment': 0,
            'guidance': None,
            'source': None,
            'error': None
        }

        try:
            # Fetch both in a single API call - include R1 for breakout detection
            response = requests.post(
                'https://scanner.tradingview.com/global/scan',
                headers={'Content-Type': 'application/json'},
                json={
                    'symbols': {'tickers': ['CRYPTOCAP:BTC.D', 'CRYPTOCAP:TOTAL3']},
                    'columns': ['close', 'change', 'change|240', 'Pivot.M.Classic.R1']  # Added R1
                },
                timeout=5
            )
            response.raise_for_status()
            data = response.json()

            if 'data' in data and len(data['data']) >= 2:
                btc_d_raw = data['data'][0].get('d', [])
                total3_raw = data['data'][1].get('d', [])

                # Parse BTCDOM - check for breakout first
                if len(btc_d_raw) >= 4:
                    btc_d_close = btc_d_raw[0]
                    btc_d_change_4h = btc_d_raw[2]
                    btc_d_r1 = btc_d_raw[3]

                    # Check for BREAKOUT above R1 (strongest signal)
                    if btc_d_close and btc_d_r1 and btc_d_close > btc_d_r1:
                        result['btc_d_trend'] = 'BREAKOUT'
                        result['btc_d_breakout'] = True
                    elif btc_d_change_4h is not None:
                        if btc_d_change_4h > 0.3:
                            result['btc_d_trend'] = 'RISING'
                        elif btc_d_change_4h < -0.3:
                            result['btc_d_trend'] = 'FALLING'
                        else:
                            result['btc_d_trend'] = 'FLAT'
                elif len(btc_d_raw) >= 3:
                    btc_d_change_4h = btc_d_raw[2]
                    if btc_d_change_4h is not None:
                        if btc_d_change_4h > 0.3:
                            result['btc_d_trend'] = 'RISING'
                        elif btc_d_change_4h < -0.3:
                            result['btc_d_trend'] = 'FALLING'
                        else:
                            result['btc_d_trend'] = 'FLAT'

                # Parse TOTAL3
                if len(total3_raw) >= 3:
                    total3_change_4h = total3_raw[2]
                    if total3_change_4h is not None:
                        if total3_change_4h > 0.5:
                            result['total3_trend'] = 'RISING'
                        elif total3_change_4h < -0.5:
                            result['total3_trend'] = 'FALLING'
                        else:
                            result['total3_trend'] = 'FLAT'

                # Calculate confluence
                btc_d = result['btc_d_trend']
                total3 = result['total3_trend']

                # Session 180 L021 Update: BREAKOUT is the strongest signal (+7 points)
                # Sherlock: "A daily close above this level would seal the deal for altcoins"
                if btc_d == 'BREAKOUT' and total3 == 'FALLING':
                    result['confluence'] = 'MAXIMUM'
                    result['confidence_adjustment'] = 7
                    result['guidance'] = 'BTCDOM BREAKOUT + TOTAL3↓ = MAXIMUM conviction! Big caps will dump hard (L021)'
                elif btc_d == 'BREAKOUT':
                    result['confluence'] = 'MAXIMUM'
                    result['confidence_adjustment'] = 6
                    result['guidance'] = 'BTCDOM BREAKOUT above R1 = Very strong short signal (L021)'
                elif btc_d == 'RISING' and total3 == 'FALLING':
                    result['confluence'] = 'OPTIMAL'
                    result['confidence_adjustment'] = 5
                    result['guidance'] = 'BTCDOM↑ + TOTAL3↓ = Maximum conviction for shorts (L021)'
                elif btc_d == 'RISING' and total3 == 'FLAT':
                    result['confluence'] = 'FAVORABLE'
                    result['confidence_adjustment'] = 3
                    result['guidance'] = 'BTCDOM rising, TOTAL3 flat = Good for shorts'
                elif btc_d == 'FLAT' and total3 == 'FALLING':
                    result['confluence'] = 'FAVORABLE'
                    result['confidence_adjustment'] = 2
                    result['guidance'] = 'BTCDOM flat, TOTAL3 falling = Moderate short signal'
                elif btc_d == 'FALLING' and total3 == 'RISING':
                    result['confluence'] = 'HEADWIND'
                    result['confidence_adjustment'] = -3
                    result['guidance'] = 'BTCDOM↓ + TOTAL3↑ = Alt season - avoid shorts'
                elif btc_d == 'FALLING':
                    result['confluence'] = 'HEADWIND'
                    result['confidence_adjustment'] = -2
                    result['guidance'] = 'BTCDOM falling = money to alts - shorts risky'
                else:
                    result['confluence'] = 'NEUTRAL'
                    result['confidence_adjustment'] = 0
                    result['guidance'] = 'No clear BTCDOM/TOTAL3 confluence'

                result['source'] = 'TradingView (live)'

                logger.info(f"   ✅ BTCDOM+TOTAL3: {result['confluence']} | Adj: {result['confidence_adjustment']:+d} pts")

        except Exception as e:
            result['error'] = str(e)
            logger.debug(f"   ⚠️ BTCDOM+TOTAL3 confluence fetch failed: {str(e)[:60]}")

        return result

    def analyze_usdt_d_trendline(self) -> Dict:
        """
        Analyze USDT.D trendline for entry timing.

        Session 122 (CYS Learning): David's key insight - USDT.D trendline retest
        bounce signals alts will bleed (TOTAL3 drops). This is a strong short entry signal.

        Strategy:
        - Fetch USDT.D OHLC data from TradingView
        - Identify descending trendline (or ascending for support)
        - Check if price is near/at trendline for bounce
        - Signal when trendline bounce likely = alts will bleed

        Returns:
            Dict with trendline analysis and timing signal
        """
        result = {
            'detected': False,
            'direction': None,  # 'ascending_support' or 'descending_resistance'
            'current_value': None,
            'trendline_value': None,
            'distance_pct': None,
            'touch_count': 0,
            'near_trendline': False,  # Within 1.5% of trendline
            'signal_for_short': 'NEUTRAL',
            'guidance': None,
            'source': None,
            'error': None
        }

        try:
            # Fetch USDT.D historical data for trendline analysis (20 candles 4H)
            response = requests.post(
                'https://scanner.tradingview.com/global/scan',
                headers={'Content-Type': 'application/json'},
                json={
                    'symbols': {'tickers': ['CRYPTOCAP:USDT.D']},
                    'columns': [
                        'close', 'high', 'low', 'open',
                        # Moving averages as trend proxy
                        'EMA20', 'EMA50',
                        # Recent high/low for trendline
                        'High.1M', 'Low.1M',  # Monthly high/low
                        'High.3M', 'Low.3M',  # 3-month high/low
                        # RSI for overbought/oversold
                        'RSI', 'change'
                    ]
                },
                timeout=5
            )
            response.raise_for_status()
            data = response.json()

            if 'data' in data and len(data['data']) >= 1:
                raw = data['data'][0].get('d', [])

                if len(raw) >= 12:
                    close = raw[0]
                    high = raw[1]
                    low = raw[2]
                    open_price = raw[3]
                    ema20 = raw[4]
                    ema50 = raw[5]
                    high_1m = raw[6]
                    low_1m = raw[7]
                    high_3m = raw[8]
                    low_3m = raw[9]
                    rsi = raw[10]
                    change = raw[11]

                    result['current_value'] = round(close, 3) if close else None

                    # Determine trend direction using EMAs
                    if ema20 and ema50 and close:
                        if close > ema20 > ema50:
                            # Uptrend - USDT.D rising = risk-off = GOOD for shorts
                            result['direction'] = 'ascending_support'
                            result['trendline_value'] = round(ema20, 3)  # EMA20 as dynamic support
                            result['detected'] = True

                            # Check if near trendline (support bounce opportunity)
                            dist_pct = ((close - ema20) / close) * 100
                            result['distance_pct'] = round(dist_pct, 2)
                            result['near_trendline'] = abs(dist_pct) < 1.5

                            if result['near_trendline']:
                                result['signal_for_short'] = 'FAVORABLE'
                                result['guidance'] = 'USDT.D near ascending support (EMA20) - bounce likely = alts will bleed'
                            else:
                                result['signal_for_short'] = 'NEUTRAL'
                                result['guidance'] = f'USDT.D uptrend but {dist_pct:.1f}% above support - wait for pullback to EMA20'

                        elif close < ema20 < ema50:
                            # Downtrend - USDT.D falling = risk-on = BAD for shorts
                            result['direction'] = 'descending_resistance'
                            result['trendline_value'] = round(ema20, 3)  # EMA20 as dynamic resistance
                            result['detected'] = True

                            dist_pct = ((ema20 - close) / close) * 100
                            result['distance_pct'] = round(dist_pct, 2)
                            result['near_trendline'] = abs(dist_pct) < 1.5

                            if result['near_trendline']:
                                result['signal_for_short'] = 'HEADWIND'
                                result['guidance'] = 'USDT.D near descending resistance (EMA20) - rejection likely = alts may pump'
                            else:
                                result['signal_for_short'] = 'HEADWIND'
                                result['guidance'] = f'USDT.D downtrend = risk-on = shorts face headwind'

                        else:
                            # Mixed/consolidation
                            result['direction'] = 'consolidation'
                            result['trendline_value'] = round((ema20 + ema50) / 2, 3) if ema20 and ema50 else None
                            result['detected'] = False

                            # Check if RSI shows oversold (potential bounce)
                            if rsi and rsi < 35:
                                result['signal_for_short'] = 'FAVORABLE'
                                result['guidance'] = f'USDT.D oversold (RSI {rsi:.0f}) - bounce likely = alts may bleed soon'
                            elif rsi and rsi > 65:
                                result['signal_for_short'] = 'HEADWIND'
                                result['guidance'] = f'USDT.D overbought (RSI {rsi:.0f}) - pullback likely = alts may pump'
                            else:
                                result['signal_for_short'] = 'NEUTRAL'
                                result['guidance'] = 'USDT.D consolidating - no clear trendline signal'

                    # Add monthly range context for David
                    result['monthly_range'] = {
                        'high_1m': round(high_1m, 3) if high_1m else None,
                        'low_1m': round(low_1m, 3) if low_1m else None,
                        'high_3m': round(high_3m, 3) if high_3m else None,
                        'low_3m': round(low_3m, 3) if low_3m else None,
                    }
                    result['rsi'] = round(rsi, 1) if rsi else None
                    result['daily_change'] = round(change, 2) if change else None
                    result['source'] = 'TradingView (live)'

                    logger.info(f"   ✅ USDT.D Trendline: {result['direction']} | Signal: {result['signal_for_short']}")

        except Exception as e:
            result['error'] = str(e)
            logger.debug(f"   ⚠️ USDT.D trendline analysis failed: {str(e)[:60]}")

        return result

    def _calculate_macro_signal(self, btc_d: float, usdt_d: float, others_d: float) -> str:
        """
        Calculate overall macro signal from key indices.

        Logic: Count bearish vs bullish signals
        - If 2+ bearish signals → BEARISH_FOR_ALTS
        - If 2+ bullish signals → BULLISH_FOR_ALTS
        - Else → NEUTRAL

        Args:
            btc_d: BTC Dominance %
            usdt_d: USDT Dominance %
            others_d: Others Dominance %

        Returns:
            'BEARISH_FOR_ALTS', 'BULLISH_FOR_ALTS', or 'NEUTRAL'
        """
        bearish_count = 0
        bullish_count = 0

        # BTC.D signal
        if btc_d >= 60:
            bearish_count += 1
        elif btc_d <= 50:
            bullish_count += 1

        # USDT.D signal (PRIMARY - weight it more)
        if usdt_d >= 6.0:
            bearish_count += 1
        elif usdt_d <= 5.0:
            bullish_count += 1

        # OTHERS.D signal
        if others_d <= 7:
            bearish_count += 1
        elif others_d >= 10:
            bullish_count += 1

        if bearish_count >= 2:
            return "BEARISH_FOR_ALTS"
        elif bullish_count >= 2:
            return "BULLISH_FOR_ALTS"
        return "NEUTRAL"

    def _fallback_result(self) -> Dict:
        """
        Return empty result on error (graceful degradation).

        Returns:
            Dict with empty indices and UNKNOWN macro signal
        """
        return {
            'timestamp': datetime.utcnow().isoformat(),
            'indices': {},
            'macro_signal': 'UNKNOWN',
            'error': 'Failed to fetch indices data'
        }


def main():
    """
    CLI test for indices tracker.

    Usage:
        python scripts/helpers/indices_tracker.py
    """
    import sys

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(message)s',
        handlers=[logging.StreamHandler(sys.stdout)]
    )

    # Fetch indices
    tracker = IndicesTracker()
    results = tracker.fetch_all_indices()

    # Display results
    print("\n" + "="*80)
    print("DAVID'S 7 INDICES - LIVE DATA")
    print("="*80 + "\n")

    if results.get('error'):
        print(f"❌ Error: {results['error']}\n")
        return

    # Display each index
    for idx_name, idx_data in results['indices'].items():
        if idx_name == 'realtime_sentiment':
            print(f"{idx_name.upper():<15s} {'':<20s} {'':<20s} Real-time Sentiment: {idx_data}")
        elif idx_name in ['btc_24h_change', 'eth_24h_change', 'total3_24h_change', 'usdt_d_value']:
            print(f"{idx_name.upper():<15s} {idx_data:<20f}")
        else:
            value = idx_data['value']
            signal = idx_data['signal']
            note = idx_data['note']

            # Format value based on type
            if isinstance(value, float):
                value_str = f"{value:>6.2f}%"
            elif isinstance(value, int):
                value_str = f"${value:>14,}"
            else:
                value_str = str(value)


            print(f"{idx_name.upper():<15s} {value_str:<20s} {signal:<20s} {note}")

    print(f"\n{'MACRO SIGNAL':<15s} {results['macro_signal']}")
    print("\n" + "="*80 + "\n")

    # Save to JSON
    output_file = "latest_market_indicators.json"
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"✅ Saved to: {output_file}\n")


if __name__ == "__main__":
    main()
