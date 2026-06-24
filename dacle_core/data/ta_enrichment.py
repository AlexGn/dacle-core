#!/usr/bin/env python3
"""
TA Data Enrichment for consolidated.json

Session 291: Adds missing LONG scoring data to consolidated.json
- RSI fields (12% weight)
- Support confluence (18% weight)
- Volume capitulation (10% weight)
- Price drawdown (10% weight)
- Time to bottom (3% weight)
- Bottom signals (partial 10% weight)

This enriches fundamental data with technical analysis to enable full LONG scoring.

Author: Claude Code (Session 291)
Created: 2026-01-06
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Tuple, List

import ccxt

logger = logging.getLogger(__name__)


class TAEnrichment:
    """
    Enriches consolidated.json with TA data for LONG scoring.

    Adds the following fields:
    - rsi_14: Current RSI value (14-period)
    - rsi_oversold: Boolean flag (RSI < 30)
    - at_ema_200_support: Boolean flag (price within 5% of 200 EMA)
    - at_qvwap_support: Boolean flag (price near quarterly VWAP)
    - at_yvwap_support: Boolean flag (price near yearly VWAP)
    - dump_volume_ratio: Ratio of dump volume to average (peak/avg)
    - drawdown_from_ath: % decline from ATH
    - ath_price: All-time high price
    - days_since_ath: Days elapsed since ATH
    - bottom_signals_count: Number of bottom signals (0-5)
    """

    def __init__(self, exchange_id: str = "binance"):
        """
        Initialize TA enrichment with exchange.

        Args:
            exchange_id: CCXT exchange ID (binance, gate, mexc, bybit, etc.)
        """
        exchanges = {
            'binance': ccxt.binance,
            'mexc': ccxt.mexc,
            'gate': ccxt.gateio,
            'bybit': ccxt.bybit,
            'coinbase': ccxt.coinbase,
        }

        exchange_class = exchanges.get(exchange_id, ccxt.binance)
        self.exchange = exchange_class({'enableRateLimit': True})
        logger.info(f"Initialized TAEnrichment with {exchange_id}")

    def enrich_token(self, consolidated_data: Dict, token_symbol: str) -> Dict:
        """
        Enrich a single token's consolidated.json with TA data.

        Args:
            consolidated_data: Existing consolidated.json dict
            token_symbol: Token symbol (e.g., "ZEC", "IRYS")

        Returns:
            Updated consolidated_data with TA fields added
        """
        logger.info(f"🔍 Enriching {token_symbol} with TA data...")

        # Get current price from consolidated data
        current_price = consolidated_data.get('current_price')
        if not current_price:
            logger.warning(f"⚠️ No current_price in consolidated.json for {token_symbol}")
            return self._add_null_ta_data(consolidated_data)

        # Try to fetch OHLCV data
        try:
            ohlcv_data = self._fetch_ohlcv(token_symbol)
            if not ohlcv_data:
                logger.warning(f"⚠️ No OHLCV data available for {token_symbol}")
                return self._add_null_ta_data(consolidated_data)

            # Calculate all TA fields
            ta_data = self._calculate_ta_fields(ohlcv_data, current_price, token_symbol)

            # Merge TA data into consolidated_data
            consolidated_data.update(ta_data)

            logger.info(f"✅ TA enrichment complete for {token_symbol}")
            logger.info(f"   RSI: {ta_data.get('rsi_14', 'N/A'):.1f}, Oversold: {ta_data.get('rsi_oversold', False)}")
            logger.info(f"   Drawdown: {ta_data.get('drawdown_from_ath', 'N/A'):.1f}%, Days since ATH: {ta_data.get('days_since_ath', 'N/A')}")
            logger.info(f"   Bottom signals: {ta_data.get('bottom_signals_count', 0)}/5")

            return consolidated_data

        except Exception as e:
            logger.error(f"❌ Error enriching {token_symbol}: {e}")
            return self._add_null_ta_data(consolidated_data)

    def _fetch_ohlcv(self, symbol: str, timeframe: str = "1d", limit: int = 365) -> Optional[List]:
        """
        Fetch OHLCV data from exchange.

        Args:
            symbol: Token symbol (e.g., "ZEC")
            timeframe: Candle timeframe (1h, 4h, 1d)
            limit: Number of candles to fetch

        Returns:
            List of OHLCV candles or None if failed
        """
        try:
            # Try USDT pair first (most common)
            pair = f"{symbol}/USDT"
            logger.debug(f"Fetching OHLCV for {pair}...")
            ohlcv = self.exchange.fetch_ohlcv(pair, timeframe=timeframe, limit=limit)

            if ohlcv and len(ohlcv) > 0:
                logger.debug(f"✅ Fetched {len(ohlcv)} candles for {pair}")
                return ohlcv

        except ccxt.BadSymbol:
            # Try USD pair
            try:
                pair = f"{symbol}/USD"
                logger.debug(f"USDT pair failed, trying {pair}...")
                ohlcv = self.exchange.fetch_ohlcv(pair, timeframe=timeframe, limit=limit)

                if ohlcv and len(ohlcv) > 0:
                    logger.debug(f"✅ Fetched {len(ohlcv)} candles for {pair}")
                    return ohlcv

            except Exception as e:
                logger.debug(f"USD pair also failed: {e}")

        except Exception as e:
            logger.debug(f"Error fetching OHLCV for {symbol}: {e}")

        return None

    def _calculate_ta_fields(self, ohlcv: List, current_price: float, symbol: str) -> Dict:
        """
        Calculate all TA fields from OHLCV data.

        Args:
            ohlcv: List of OHLCV candles [[timestamp, open, high, low, close, volume], ...]
            current_price: Current token price
            symbol: Token symbol for logging

        Returns:
            Dict with TA fields
        """
        ta_data = {}

        # Extract close prices
        closes = [candle[4] for candle in ohlcv]
        volumes = [candle[5] for candle in ohlcv]
        highs = [candle[2] for candle in ohlcv]
        lows = [candle[3] for candle in ohlcv]

        # 1. RSI (12% weight)
        rsi = self._calculate_rsi(closes)
        ta_data['rsi_14'] = rsi
        ta_data['rsi_oversold'] = rsi < 30 if rsi is not None else False

        # 2. Support Confluence (18% weight)
        ema_200 = self._calculate_ema(closes, 200)
        qvwap = self._calculate_vwap(ohlcv, period_days=90)  # Quarterly VWAP
        yvwap = self._calculate_vwap(ohlcv, period_days=365)  # Yearly VWAP

        ta_data['at_ema_200_support'] = self._is_at_support(current_price, ema_200, tolerance=0.05)
        ta_data['at_qvwap_support'] = self._is_at_support(current_price, qvwap, tolerance=0.05)
        ta_data['at_yvwap_support'] = self._is_at_support(current_price, yvwap, tolerance=0.05)

        # 3. Volume Capitulation (10% weight)
        avg_volume = sum(volumes[-30:]) / min(30, len(volumes))  # 30-day avg
        peak_volume = max(volumes[-90:]) if len(volumes) >= 90 else max(volumes)
        ta_data['dump_volume_ratio'] = peak_volume / avg_volume if avg_volume > 0 else 1.0

        # 4. Price Drawdown (10% weight)
        ath = max(highs)
        drawdown = ((ath - current_price) / ath) * 100 if ath > 0 else 0.0

        ta_data['ath_price'] = ath
        ta_data['drawdown_from_ath'] = drawdown

        # 5. Time to Bottom (3% weight)
        ath_index = highs.index(ath)
        days_since_ath = len(highs) - ath_index  # Days elapsed since ATH
        ta_data['days_since_ath'] = days_since_ath

        # 6. Bottom Signals (partial 10% weight)
        bottom_signals = self._count_bottom_signals(
            closes, volumes, rsi, current_price, lows
        )
        ta_data['bottom_signals_count'] = bottom_signals

        logger.debug(f"Calculated TA for {symbol}:")
        logger.debug(f"  RSI: {rsi:.1f}, Oversold: {ta_data['rsi_oversold']}")
        logger.debug(f"  EMA200 support: {ta_data['at_ema_200_support']}")
        logger.debug(f"  Drawdown: {drawdown:.1f}%, Days: {days_since_ath}")
        logger.debug(f"  Bottom signals: {bottom_signals}/5")

        return ta_data

    def _calculate_rsi(self, prices: List[float], period: int = 14) -> Optional[float]:
        """
        Calculate RSI (Relative Strength Index).

        Args:
            prices: List of close prices
            period: RSI period (default 14)

        Returns:
            RSI value (0-100) or None if insufficient data
        """
        if len(prices) < period + 1:
            return None

        gains = []
        losses = []

        for i in range(1, len(prices)):
            change = prices[i] - prices[i-1]
            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))

        # Average gains and losses
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0  # No losses = max RSI

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        return rsi

    def _calculate_ema(self, prices: List[float], period: int) -> Optional[float]:
        """
        Calculate Exponential Moving Average.

        Args:
            prices: List of close prices
            period: EMA period

        Returns:
            EMA value or None if insufficient data
        """
        if len(prices) < period:
            return None

        multiplier = 2 / (period + 1)
        ema = sum(prices[:period]) / period  # Start with SMA

        for price in prices[period:]:
            ema = (price - ema) * multiplier + ema

        return ema

    def _calculate_vwap(self, ohlcv: List, period_days: int = 90) -> Optional[float]:
        """
        Calculate Volume-Weighted Average Price.

        Args:
            ohlcv: List of OHLCV candles
            period_days: Number of days to calculate VWAP over

        Returns:
            VWAP value or None if insufficient data
        """
        if len(ohlcv) < period_days:
            period_days = len(ohlcv)

        recent = ohlcv[-period_days:]

        cumulative_pv = 0
        cumulative_volume = 0

        for candle in recent:
            high = candle[2]
            low = candle[3]
            close = candle[4]
            volume = candle[5]

            typical_price = (high + low + close) / 3
            cumulative_pv += typical_price * volume
            cumulative_volume += volume

        if cumulative_volume == 0:
            return None

        return cumulative_pv / cumulative_volume

    def _is_at_support(self, current_price: float, support_level: Optional[float], tolerance: float = 0.05) -> bool:
        """
        Check if current price is at support level.

        Args:
            current_price: Current token price
            support_level: Support level (EMA, VWAP, etc.)
            tolerance: % tolerance (0.05 = 5%)

        Returns:
            True if price is within tolerance of support
        """
        if support_level is None:
            return False

        lower_bound = support_level * (1 - tolerance)
        upper_bound = support_level * (1 + tolerance)

        return lower_bound <= current_price <= upper_bound

    def _count_bottom_signals(self, closes: List[float], volumes: List[float],
                             rsi: Optional[float], current_price: float,
                             lows: List[float]) -> int:
        """
        Count bottom signals (0-5).

        Bottom signals:
        1. RSI < 25 (extreme oversold)
        2. Volume spike >3x avg (capitulation)
        3. Higher lows forming (trend reversal)
        4. Price within 10% of ATL (absolute low)
        5. RSI divergence (price lower, RSI higher)

        Args:
            closes: List of close prices
            volumes: List of volumes
            rsi: Current RSI value
            current_price: Current price
            lows: List of low prices

        Returns:
            Number of bottom signals (0-5)
        """
        signals = 0

        # Signal 1: Extreme oversold RSI
        if rsi is not None and rsi < 25:
            signals += 1

        # Signal 2: Volume capitulation (>3x avg)
        if len(volumes) >= 30:
            avg_volume = sum(volumes[-30:]) / 30
            recent_volume = volumes[-1]
            if recent_volume > avg_volume * 3:
                signals += 1

        # Signal 3: Higher lows forming (last 3 lows)
        if len(lows) >= 10:
            recent_lows = lows[-10:]
            # Find local minima
            local_mins = []
            for i in range(1, len(recent_lows) - 1):
                if recent_lows[i] < recent_lows[i-1] and recent_lows[i] < recent_lows[i+1]:
                    local_mins.append(recent_lows[i])

            # Check if last 2 lows are higher
            if len(local_mins) >= 2:
                if local_mins[-1] > local_mins[-2]:
                    signals += 1

        # Signal 4: Near ATL (within 10%)
        if len(lows) > 0:
            atl = min(lows)
            if current_price <= atl * 1.10:
                signals += 1

        # Signal 5: RSI divergence (simplified - check last 30 days)
        if rsi is not None and len(closes) >= 30:
            price_30d_ago = closes[-30]
            # If price is lower but RSI is same/higher = bullish divergence
            if current_price < price_30d_ago and rsi >= 30:
                signals += 1

        return signals

    def _add_null_ta_data(self, consolidated_data: Dict) -> Dict:
        """
        Add null TA data fields when OHLCV is unavailable.

        Args:
            consolidated_data: Existing consolidated.json dict

        Returns:
            Updated dict with null TA fields
        """
        consolidated_data.update({
            'rsi_14': None,
            'rsi_oversold': False,
            'at_ema_200_support': False,
            'at_qvwap_support': False,
            'at_yvwap_support': False,
            'dump_volume_ratio': 1.0,  # Neutral default
            'ath_price': None,
            'drawdown_from_ath': None,
            'days_since_ath': None,
            'bottom_signals_count': 0,
            '_ta_enrichment_status': 'unavailable',
            '_ta_enrichment_timestamp': datetime.utcnow().isoformat(),
        })

        return consolidated_data


def enrich_all_tokens(tokens_dir: str = "data/tokens", force: bool = False) -> Dict:
    """
    Batch enrich all tokens with TA data.

    Args:
        tokens_dir: Path to tokens directory
        force: Force re-enrichment even if TA data exists

    Returns:
        Dict with enrichment stats
    """
    enricher = TAEnrichment()
    tokens_path = Path(tokens_dir)

    stats = {
        'total': 0,
        'enriched': 0,
        'skipped': 0,
        'failed': 0,
    }

    for token_dir in sorted(tokens_path.iterdir()):
        if not token_dir.is_dir():
            continue

        consolidated_file = token_dir / "consolidated.json"
        if not consolidated_file.exists():
            continue

        stats['total'] += 1
        token_symbol = token_dir.name

        # Load consolidated data
        with open(consolidated_file, 'r') as f:
            data = json.load(f)

        # Skip if already enriched (unless force)
        if not force and 'rsi_14' in data:
            logger.info(f"⏭️ Skipping {token_symbol} (already enriched)")
            stats['skipped'] += 1
            continue

        # Enrich
        try:
            enriched_data = enricher.enrich_token(data, token_symbol)

            # Save back to consolidated.json
            with open(consolidated_file, 'w') as f:
                json.dump(enriched_data, f, indent=2)

            stats['enriched'] += 1
            logger.info(f"✅ Enriched {token_symbol} ({stats['enriched']}/{stats['total']})")

        except Exception as e:
            logger.error(f"❌ Failed to enrich {token_symbol}: {e}")
            stats['failed'] += 1

    logger.info(f"\n{'='*60}")
    logger.info(f"TA Enrichment Complete")
    logger.info(f"{'='*60}")
    logger.info(f"Total tokens:    {stats['total']}")
    logger.info(f"Enriched:        {stats['enriched']}")
    logger.info(f"Skipped:         {stats['skipped']}")
    logger.info(f"Failed:          {stats['failed']}")
    logger.info(f"{'='*60}\n")

    return stats


if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    # Check for --force flag
    force = '--force' in sys.argv

    if force:
        logger.info("🔄 Force mode: Re-enriching ALL tokens")

    # Batch enrich all tokens
    stats = enrich_all_tokens(force=force)

    sys.exit(0 if stats['failed'] == 0 else 1)
