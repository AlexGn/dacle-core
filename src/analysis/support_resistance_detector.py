#!/usr/bin/env python3
"""
Support, Resistance & Trendline Detector

Learning 023: Implements S/R detection based on Kaizen Trading Guide.

Core Concepts:
- Support: Price floor where buyers concentrate (bounces)
- Resistance: Price ceiling where sellers concentrate (rejections)
- Trendlines: Dynamic diagonal S/R (uptrend = higher lows, downtrend = lower highs)
- Role Reversal: Broken support becomes resistance and vice versa

For TGE Shorts:
- Price at resistance = FAVORABLE (ideal entry zone)
- Price at support = UNFAVORABLE (may bounce)
- Broken support retest = OPTIMAL (role reversal entry)

Migration History:
- Session 267: Migrated from scripts/helpers/support_resistance_detector.py

Usage:
    from src.analysis.support_resistance_detector import SupportResistanceDetector
"""

import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import statistics

logger = logging.getLogger(__name__)


class SupportResistanceDetector:
    """
    Detects horizontal S/R levels and trendlines from OHLCV data.

    Kaizen Rules Applied:
    - More touches = stronger level (touch_count weighting)
    - Higher timeframe = more reliable (timeframe weighting)
    - Volume confirmation (volume_ratio weighting)
    - Psychological levels (round number detection)
    """

    # Configuration
    TOUCH_TOLERANCE_PCT = 1.5  # % tolerance for considering a "touch"
    MIN_TOUCHES_FOR_LEVEL = 2  # Minimum touches to identify S/R
    PSYCHOLOGICAL_LEVELS = [0.01, 0.05, 0.10, 0.25, 0.50, 1.0, 5.0, 10.0, 25.0, 50.0, 100.0]
    NEAR_LEVEL_PCT = 3.0  # % distance to be considered "at" a level

    def __init__(self, touch_tolerance_pct: float = 1.5):
        """
        Initialize the detector.

        Args:
            touch_tolerance_pct: Percentage tolerance for level touches
        """
        self.touch_tolerance_pct = touch_tolerance_pct

    def detect_support_levels(
        self,
        ohlcv_data: List[Dict],
        current_price: float,
        lookback_periods: int = 100,
        min_touches: int = 2
    ) -> List[Dict]:
        """
        Detect horizontal support levels from OHLCV data.

        Support is identified by finding price lows that have been
        touched/bounced from multiple times.

        Args:
            ohlcv_data: List of OHLCV candles with keys: open, high, low, close, volume, timestamp
            current_price: Current price of the asset
            lookback_periods: Number of periods to analyze
            min_touches: Minimum touches required to identify a level

        Returns:
            List of support level dictionaries sorted by distance from current price
        """
        if not ohlcv_data or len(ohlcv_data) < 10:
            logger.warning("Insufficient OHLCV data for support detection")
            return []

        # Use most recent data
        data = ohlcv_data[-lookback_periods:] if len(ohlcv_data) > lookback_periods else ohlcv_data

        # Extract lows
        lows = [(d.get('low', d.get('close', 0)), d.get('volume', 0), d.get('timestamp', ''))
                for d in data if d.get('low') or d.get('close')]

        if not lows:
            return []

        # Find support levels by clustering lows
        support_levels = self._cluster_price_levels(
            [l[0] for l in lows],
            [l[1] for l in lows],
            [l[2] for l in lows],
            level_type='support',
            min_touches=min_touches
        )

        # Add psychological levels that are below current price
        psychological_supports = self._get_psychological_levels(current_price, 'support')
        for psych_level in psychological_supports:
            # Check if already captured
            if not any(abs(s['price'] - psych_level) / psych_level < 0.02 for s in support_levels):
                support_levels.append({
                    'price': psych_level,
                    'strength': 'MODERATE',
                    'touch_count': 0,
                    'volume_confirmed': False,
                    'is_psychological': True,
                    'distance_pct': ((psych_level - current_price) / current_price) * 100,
                    'last_touch': None
                })

        # Calculate distance from current price and filter to levels below current price
        result = []
        for level in support_levels:
            level['distance_pct'] = ((level['price'] - current_price) / current_price) * 100
            if level['price'] < current_price:  # Support must be below current price
                result.append(level)

        # Sort by distance (closest first)
        result.sort(key=lambda x: abs(x['distance_pct']))

        return result[:5]  # Return top 5 support levels

    def detect_resistance_levels(
        self,
        ohlcv_data: List[Dict],
        current_price: float,
        lookback_periods: int = 100,
        min_touches: int = 2
    ) -> List[Dict]:
        """
        Detect horizontal resistance levels from OHLCV data.

        Resistance is identified by finding price highs that have been
        touched/rejected from multiple times.

        Args:
            ohlcv_data: List of OHLCV candles
            current_price: Current price of the asset
            lookback_periods: Number of periods to analyze
            min_touches: Minimum touches required to identify a level

        Returns:
            List of resistance level dictionaries sorted by distance from current price
        """
        if not ohlcv_data or len(ohlcv_data) < 10:
            logger.warning("Insufficient OHLCV data for resistance detection")
            return []

        # Use most recent data
        data = ohlcv_data[-lookback_periods:] if len(ohlcv_data) > lookback_periods else ohlcv_data

        # Extract highs
        highs = [(d.get('high', d.get('close', 0)), d.get('volume', 0), d.get('timestamp', ''))
                 for d in data if d.get('high') or d.get('close')]

        if not highs:
            return []

        # Find resistance levels by clustering highs
        resistance_levels = self._cluster_price_levels(
            [h[0] for h in highs],
            [h[1] for h in highs],
            [h[2] for h in highs],
            level_type='resistance',
            min_touches=min_touches
        )

        # Add psychological levels that are above current price
        psychological_resistances = self._get_psychological_levels(current_price, 'resistance')
        for psych_level in psychological_resistances:
            # Check if already captured
            if not any(abs(r['price'] - psych_level) / psych_level < 0.02 for r in resistance_levels):
                resistance_levels.append({
                    'price': psych_level,
                    'strength': 'MODERATE',
                    'touch_count': 0,
                    'volume_confirmed': False,
                    'is_psychological': True,
                    'distance_pct': ((psych_level - current_price) / current_price) * 100,
                    'last_touch': None
                })

        # Calculate distance from current price and filter to levels above current price
        result = []
        for level in resistance_levels:
            level['distance_pct'] = ((level['price'] - current_price) / current_price) * 100
            if level['price'] > current_price:  # Resistance must be above current price
                result.append(level)

        # Sort by distance (closest first)
        result.sort(key=lambda x: abs(x['distance_pct']))

        return result[:5]  # Return top 5 resistance levels

    def _cluster_price_levels(
        self,
        prices: List[float],
        volumes: List[float],
        timestamps: List[str],
        level_type: str,
        min_touches: int = 2
    ) -> List[Dict]:
        """
        Cluster similar price levels together to identify S/R zones.

        Uses a simple clustering approach where prices within tolerance
        are grouped together.
        """
        if not prices:
            return []

        # Sort prices
        sorted_data = sorted(zip(prices, volumes, timestamps), key=lambda x: x[0])
        prices_sorted = [x[0] for x in sorted_data]
        volumes_sorted = [x[1] for x in sorted_data]
        timestamps_sorted = [x[2] for x in sorted_data]

        clusters = []
        current_cluster = {
            'prices': [prices_sorted[0]],
            'volumes': [volumes_sorted[0]],
            'timestamps': [timestamps_sorted[0]]
        }

        for i in range(1, len(prices_sorted)):
            price = prices_sorted[i]
            cluster_avg = statistics.mean(current_cluster['prices'])

            # Check if within tolerance
            if abs(price - cluster_avg) / cluster_avg <= (self.touch_tolerance_pct / 100):
                current_cluster['prices'].append(price)
                current_cluster['volumes'].append(volumes_sorted[i])
                current_cluster['timestamps'].append(timestamps_sorted[i])
            else:
                # Save current cluster if it has enough touches
                if len(current_cluster['prices']) >= min_touches:
                    clusters.append(current_cluster)
                # Start new cluster
                current_cluster = {
                    'prices': [price],
                    'volumes': [volumes_sorted[i]],
                    'timestamps': [timestamps_sorted[i]]
                }

        # Don't forget the last cluster
        if len(current_cluster['prices']) >= min_touches:
            clusters.append(current_cluster)

        # Convert clusters to level dictionaries
        levels = []
        for cluster in clusters:
            avg_price = statistics.mean(cluster['prices'])
            touch_count = len(cluster['prices'])
            avg_volume = statistics.mean(cluster['volumes']) if cluster['volumes'] else 0

            # Calculate overall average volume for comparison
            overall_avg_volume = statistics.mean(volumes) if volumes else 1
            volume_ratio = avg_volume / overall_avg_volume if overall_avg_volume > 0 else 1

            # Determine strength based on touches and volume
            strength = self._calculate_level_strength(touch_count, volume_ratio)

            # Get most recent touch
            last_touch = max(cluster['timestamps']) if cluster['timestamps'] else None

            levels.append({
                'price': round(avg_price, 6),
                'strength': strength,
                'touch_count': touch_count,
                'volume_confirmed': volume_ratio > 1.2,  # Above average volume
                'is_psychological': self._is_psychological_level(avg_price),
                'distance_pct': 0,  # Will be calculated later
                'last_touch': last_touch
            })

        return levels

    def _calculate_level_strength(self, touch_count: int, volume_ratio: float) -> str:
        """
        Calculate the strength of an S/R level based on touches and volume.

        Kaizen Rule: More touches = stronger level
        """
        score = 0

        # Touch count scoring
        if touch_count >= 5:
            score += 3
        elif touch_count >= 3:
            score += 2
        elif touch_count >= 2:
            score += 1

        # Volume confirmation scoring
        if volume_ratio > 1.5:
            score += 2
        elif volume_ratio > 1.2:
            score += 1

        # Convert score to strength
        if score >= 4:
            return 'STRONG'
        elif score >= 2:
            return 'MODERATE'
        else:
            return 'WEAK'

    def _is_psychological_level(self, price: float) -> bool:
        """Check if price is near a psychological round number."""
        for level in self.PSYCHOLOGICAL_LEVELS:
            if abs(price - level) / level < 0.05:  # Within 5% of round number
                return True
        return False

    def _get_psychological_levels(self, current_price: float, level_type: str) -> List[float]:
        """Get relevant psychological levels based on current price."""
        relevant_levels = []

        for level in self.PSYCHOLOGICAL_LEVELS:
            if level_type == 'support' and level < current_price:
                if level > current_price * 0.5:  # Within 50% below
                    relevant_levels.append(level)
            elif level_type == 'resistance' and level > current_price:
                if level < current_price * 2.0:  # Within 100% above
                    relevant_levels.append(level)

        return relevant_levels[:3]  # Return top 3

    def detect_trendlines(
        self,
        ohlcv_data: List[Dict],
        min_touches: int = 2,
        lookback_periods: int = 50
    ) -> Dict:
        """
        Detect uptrend and downtrend lines from OHLCV data.

        Uptrend: Series of higher lows (connect the lows)
        Downtrend: Series of lower highs (connect the highs)

        Args:
            ohlcv_data: List of OHLCV candles
            min_touches: Minimum touches to confirm trendline
            lookback_periods: Number of periods to analyze

        Returns:
            Dictionary with uptrend and downtrend information
        """
        result = {
            'uptrend': {
                'active': False,
                'slope': 0,
                'touch_count': 0,
                'current_support_price': None
            },
            'downtrend': {
                'active': False,
                'slope': 0,
                'touch_count': 0,
                'current_resistance_price': None
            }
        }

        if not ohlcv_data or len(ohlcv_data) < 10:
            return result

        # Use most recent data
        data = ohlcv_data[-lookback_periods:] if len(ohlcv_data) > lookback_periods else ohlcv_data

        # Detect uptrend (higher lows)
        lows = [d.get('low', d.get('close', 0)) for d in data]
        uptrend_info = self._detect_trend_line(lows, 'uptrend', min_touches)
        result['uptrend'] = uptrend_info

        # Detect downtrend (lower highs)
        highs = [d.get('high', d.get('close', 0)) for d in data]
        downtrend_info = self._detect_trend_line(highs, 'downtrend', min_touches)
        result['downtrend'] = downtrend_info

        return result

    def _detect_trend_line(
        self,
        prices: List[float],
        trend_type: str,
        min_touches: int
    ) -> Dict:
        """
        Detect a trendline from a series of prices.

        For uptrend: Look for higher lows
        For downtrend: Look for lower highs
        """
        result = {
            'active': False,
            'slope': 0,
            'touch_count': 0,
            'current_support_price': None if trend_type == 'uptrend' else None,
            'current_resistance_price': None if trend_type == 'downtrend' else None
        }

        if len(prices) < 5:
            return result

        # Find pivot points (local extremes)
        pivots = []
        for i in range(2, len(prices) - 2):
            if trend_type == 'uptrend':
                # Local minimum
                if prices[i] < prices[i-1] and prices[i] < prices[i-2] and \
                   prices[i] < prices[i+1] and prices[i] < prices[i+2]:
                    pivots.append((i, prices[i]))
            else:
                # Local maximum
                if prices[i] > prices[i-1] and prices[i] > prices[i-2] and \
                   prices[i] > prices[i+1] and prices[i] > prices[i+2]:
                    pivots.append((i, prices[i]))

        if len(pivots) < 2:
            return result

        # Check for trend pattern
        if trend_type == 'uptrend':
            # Higher lows pattern
            higher_lows = True
            for i in range(1, len(pivots)):
                if pivots[i][1] <= pivots[i-1][1]:
                    higher_lows = False
                    break

            if higher_lows and len(pivots) >= min_touches:
                # Calculate slope
                first_pivot = pivots[0]
                last_pivot = pivots[-1]
                slope = (last_pivot[1] - first_pivot[1]) / (last_pivot[0] - first_pivot[0]) if last_pivot[0] != first_pivot[0] else 0

                # Project current support
                periods_since_last = len(prices) - 1 - last_pivot[0]
                current_support = last_pivot[1] + (slope * periods_since_last)

                result = {
                    'active': True,
                    'slope': round(slope, 8),
                    'touch_count': len(pivots),
                    'current_support_price': round(current_support, 6)
                }
        else:
            # Lower highs pattern
            lower_highs = True
            for i in range(1, len(pivots)):
                if pivots[i][1] >= pivots[i-1][1]:
                    lower_highs = False
                    break

            if lower_highs and len(pivots) >= min_touches:
                # Calculate slope
                first_pivot = pivots[0]
                last_pivot = pivots[-1]
                slope = (last_pivot[1] - first_pivot[1]) / (last_pivot[0] - first_pivot[0]) if last_pivot[0] != first_pivot[0] else 0

                # Project current resistance
                periods_since_last = len(prices) - 1 - last_pivot[0]
                current_resistance = last_pivot[1] + (slope * periods_since_last)

                result = {
                    'active': True,
                    'slope': round(slope, 8),
                    'touch_count': len(pivots),
                    'current_resistance_price': round(current_resistance, 6)
                }

        return result

    def count_level_retests(
        self,
        ohlcv_data: List[Dict],
        level: float,
        level_type: str = "RESISTANCE",
        tolerance_pct: float = 2.0,
        lookback_periods: int = 50
    ) -> Dict:
        """
        Count the number of times price has retested a specific level (Learning 029).

        Per Sherlock methodology:
        - First retest = highest probability (1.5x multiplier)
        - Second retest = reduced probability (0.5x multiplier)
        - Third+ retest = likely breakout (penalty)

        Args:
            ohlcv_data: List of OHLCV candles
            level: Price level to check
            level_type: "RESISTANCE" or "SUPPORT"
            tolerance_pct: Percentage tolerance for level touches
            lookback_periods: Number of periods to analyze

        Returns:
            {
                "retest_count": int,
                "first_retest": bool,
                "retest_advantage": "HIGH" | "MEDIUM" | "LOW",
                "confidence_multiplier": float (0.5-1.5),
                "retest_timestamps": List[str],
                "warning": str | None
            }
        """
        result = {
            "retest_count": 0,
            "first_retest": True,
            "retest_advantage": "HIGH",
            "confidence_multiplier": 1.5,  # First retest bonus
            "retest_timestamps": [],
            "warning": None
        }

        if not ohlcv_data or len(ohlcv_data) < 5:
            return result

        # Use most recent data
        data = ohlcv_data[-lookback_periods:] if len(ohlcv_data) > lookback_periods else ohlcv_data
        tolerance = level * (tolerance_pct / 100)

        retest_count = 0
        retest_timestamps = []
        last_retest_idx = -10  # Track to avoid counting same retest multiple times

        for i, candle in enumerate(data):
            close = candle.get('close', candle.get(4, 0))
            high = candle.get('high', candle.get(2, 0))
            low = candle.get('low', candle.get(3, 0))
            timestamp = candle.get('timestamp', str(i))

            # Check if this candle touched the level
            touched = False
            if level_type == "RESISTANCE":
                # Resistance touched from below
                if abs(high - level) <= tolerance and close < level:
                    touched = True
            else:  # SUPPORT
                # Support touched from above
                if abs(low - level) <= tolerance and close > level:
                    touched = True

            # Count as retest if enough candles since last retest
            if touched and (i - last_retest_idx) >= 3:
                retest_count += 1
                retest_timestamps.append(timestamp)
                last_retest_idx = i

        # Determine retest advantage per Learning 029
        if retest_count == 0:
            result["first_retest"] = True
            result["retest_advantage"] = "HIGH"
            result["confidence_multiplier"] = 1.5
        elif retest_count == 1:
            result["first_retest"] = False
            result["retest_advantage"] = "MEDIUM"
            result["confidence_multiplier"] = 0.75
        elif retest_count == 2:
            result["first_retest"] = False
            result["retest_advantage"] = "LOW"
            result["confidence_multiplier"] = 0.5
        else:
            result["first_retest"] = False
            result["retest_advantage"] = "VERY_LOW"
            result["confidence_multiplier"] = 0.25
            result["warning"] = f"Level retested {retest_count}+ times - likely to break"

        result["retest_count"] = retest_count
        result["retest_timestamps"] = retest_timestamps

        return result

    def check_role_reversal(
        self,
        ohlcv_data: List[Dict],
        level: float,
        tolerance_pct: float = 2.0,
        lookback_periods: int = 50
    ) -> Dict:
        """
        Check if a price level has undergone role reversal.

        Role Reversal (from Kaizen):
        - Broken support becomes new resistance
        - Broken resistance becomes new support

        This is critical for TGE shorts: entering on retest of broken support
        is an ideal entry strategy.

        Args:
            ohlcv_data: List of OHLCV candles
            level: Price level to check
            tolerance_pct: Percentage tolerance for level touches
            lookback_periods: Number of periods to analyze

        Returns:
            Dictionary with role reversal information
        """
        result = {
            'reversal_detected': False,
            'original_role': None,
            'current_role': None,
            'break_date': None,
            'retest_count': 0,
            'signal_strength': 'NONE'
        }

        if not ohlcv_data or len(ohlcv_data) < 20:
            return result

        # Use most recent data
        data = ohlcv_data[-lookback_periods:] if len(ohlcv_data) > lookback_periods else ohlcv_data

        tolerance = level * (tolerance_pct / 100)

        # Split data into two halves to compare behavior
        mid_point = len(data) // 2
        first_half = data[:mid_point]
        second_half = data[mid_point:]

        # Check first half - was it support or resistance?
        first_half_support_touches = 0
        first_half_resistance_touches = 0

        for candle in first_half:
            low = candle.get('low', candle.get('close', 0))
            high = candle.get('high', candle.get('close', 0))

            if abs(low - level) <= tolerance:
                first_half_support_touches += 1
            if abs(high - level) <= tolerance:
                first_half_resistance_touches += 1

        # Check second half - has role changed?
        second_half_support_touches = 0
        second_half_resistance_touches = 0
        break_detected = False
        break_date = None

        for candle in second_half:
            low = candle.get('low', candle.get('close', 0))
            high = candle.get('high', candle.get('close', 0))
            close = candle.get('close', 0)
            timestamp = candle.get('timestamp', '')

            if abs(low - level) <= tolerance:
                second_half_support_touches += 1
            if abs(high - level) <= tolerance:
                second_half_resistance_touches += 1

            # Check for break
            if not break_detected:
                if first_half_support_touches > first_half_resistance_touches:
                    # Was support, check if broken below
                    if close < level - tolerance:
                        break_detected = True
                        break_date = timestamp
                else:
                    # Was resistance, check if broken above
                    if close > level + tolerance:
                        break_detected = True
                        break_date = timestamp

        # Determine if role reversal occurred
        original_role = 'SUPPORT' if first_half_support_touches > first_half_resistance_touches else 'RESISTANCE'

        if break_detected:
            if original_role == 'SUPPORT':
                # Was support, now check if it's acting as resistance
                if second_half_resistance_touches >= 1:
                    result = {
                        'reversal_detected': True,
                        'original_role': 'SUPPORT',
                        'current_role': 'RESISTANCE',
                        'break_date': break_date,
                        'retest_count': second_half_resistance_touches,
                        'signal_strength': 'STRONG' if second_half_resistance_touches >= 2 else 'MODERATE'
                    }
            else:
                # Was resistance, now check if it's acting as support
                if second_half_support_touches >= 1:
                    result = {
                        'reversal_detected': True,
                        'original_role': 'RESISTANCE',
                        'current_role': 'SUPPORT',
                        'break_date': break_date,
                        'retest_count': second_half_support_touches,
                        'signal_strength': 'STRONG' if second_half_support_touches >= 2 else 'MODERATE'
                    }

        return result

    def get_price_position(
        self,
        current_price: float,
        supports: List[Dict],
        resistances: List[Dict]
    ) -> Dict:
        """
        Determine where current price sits relative to S/R levels.

        This is key for TGE short entries:
        - AT_RESISTANCE = FAVORABLE for shorts (ideal entry)
        - AT_SUPPORT = UNFAVORABLE for shorts (may bounce)
        - MID_RANGE = NEUTRAL

        Args:
            current_price: Current asset price
            supports: List of support levels from detect_support_levels()
            resistances: List of resistance levels from detect_resistance_levels()

        Returns:
            Dictionary with price position analysis
        """
        result = {
            'nearest_support': None,
            'nearest_resistance': None,
            'position': 'MID_RANGE',
            'position_favorability': 'NEUTRAL',  # For TGE shorts
            'distance_to_support_pct': None,
            'distance_to_resistance_pct': None
        }

        # Find nearest support
        if supports:
            nearest_support = min(supports, key=lambda x: abs(x['distance_pct']))
            result['nearest_support'] = {
                'price': nearest_support['price'],
                'distance_pct': nearest_support['distance_pct'],
                'strength': nearest_support['strength']
            }
            result['distance_to_support_pct'] = abs(nearest_support['distance_pct'])

        # Find nearest resistance
        if resistances:
            nearest_resistance = min(resistances, key=lambda x: abs(x['distance_pct']))
            result['nearest_resistance'] = {
                'price': nearest_resistance['price'],
                'distance_pct': nearest_resistance['distance_pct'],
                'strength': nearest_resistance['strength']
            }
            result['distance_to_resistance_pct'] = abs(nearest_resistance['distance_pct'])

        # Determine position
        if result['distance_to_resistance_pct'] is not None and result['distance_to_resistance_pct'] <= self.NEAR_LEVEL_PCT:
            result['position'] = 'AT_RESISTANCE'
            result['position_favorability'] = 'FAVORABLE'  # Good for shorts
        elif result['distance_to_support_pct'] is not None and result['distance_to_support_pct'] <= self.NEAR_LEVEL_PCT:
            result['position'] = 'AT_SUPPORT'
            result['position_favorability'] = 'UNFAVORABLE'  # Bad for shorts
        else:
            result['position'] = 'MID_RANGE'
            result['position_favorability'] = 'NEUTRAL'

        return result

    def analyze_for_tge_short(
        self,
        ohlcv_data: List[Dict],
        current_price: float
    ) -> Dict:
        """
        Complete S/R analysis optimized for TGE short execution.

        This is the main entry point for integration with the playbook generator.

        Args:
            ohlcv_data: List of OHLCV candles
            current_price: Current price of the TGE token

        Returns:
            Comprehensive S/R analysis dictionary
        """
        # Detect all levels
        supports = self.detect_support_levels(ohlcv_data, current_price)
        resistances = self.detect_resistance_levels(ohlcv_data, current_price)
        trendlines = self.detect_trendlines(ohlcv_data)

        # Get price position
        price_position = self.get_price_position(current_price, supports, resistances)

        # Check for role reversal on nearest levels
        role_reversal = {'reversal_detected': False}
        if supports:
            # Check if nearest support has undergone reversal
            nearest_support_price = supports[0]['price']
            role_reversal = self.check_role_reversal(ohlcv_data, nearest_support_price)

        # Session 243: Track retest count for nearest resistance (Learning 029)
        retest_info = {"retest_count": 0, "first_retest": True, "retest_advantage": "HIGH", "confidence_multiplier": 1.5}
        if resistances:
            nearest_resistance_price = resistances[0]['price']
            retest_info = self.count_level_retests(
                ohlcv_data,
                nearest_resistance_price,
                level_type="RESISTANCE"
            )

        # Calculate confidence adjustment for TGE shorts
        confidence_adjustment = 0
        signals = []

        # Position-based adjustments
        if price_position['position'] == 'AT_RESISTANCE':
            confidence_adjustment += 3
            signals.append("Price at resistance (favorable)")

            # Bonus for strong resistance
            if price_position.get('nearest_resistance', {}).get('strength') == 'STRONG':
                confidence_adjustment += 2
                signals.append("Strong resistance level")

        elif price_position['position'] == 'AT_SUPPORT':
            confidence_adjustment -= 2
            signals.append("Price at support (may bounce)")

        # Role reversal bonus (most powerful signal)
        if role_reversal.get('reversal_detected'):
            if role_reversal.get('current_role') == 'RESISTANCE':
                confidence_adjustment += 4
                signals.append("Broken support now resistance (ideal short zone)")

        # Downtrend line active
        if trendlines.get('downtrend', {}).get('active'):
            confidence_adjustment += 2
            signals.append("Active downtrend line")

        # Uptrend line (headwind for shorts)
        if trendlines.get('uptrend', {}).get('active'):
            confidence_adjustment -= 1
            signals.append("Active uptrend line (headwind)")

        # Session 243: Retest advantage adjustment (Learning 029)
        # First retest = bonus, multiple retests = penalty
        retest_multiplier = retest_info.get("confidence_multiplier", 1.0)
        if retest_info.get("first_retest"):
            signals.append("First retest (highest probability)")
        elif retest_info.get("retest_count", 0) >= 3:
            signals.append(f"Level retested {retest_info['retest_count']}x (likely to break)")
        elif retest_info.get("retest_count", 0) > 0:
            signals.append(f"Retest #{retest_info['retest_count']+1} (reduced probability)")

        return {
            'supports': supports,
            'resistances': resistances,
            'trendlines': trendlines,
            'price_position': price_position,
            'role_reversal': role_reversal,
            'retest_info': retest_info,  # Session 243: Added L029 retest tracking
            'retest_count': retest_info.get('retest_count', 0),  # Convenience field
            'confidence_adjustment': confidence_adjustment,
            'retest_multiplier': retest_multiplier,  # Apply to final sizing
            'signals': signals,
            'analysis_timestamp': datetime.utcnow().isoformat()
        }


def format_sr_summary(sr_data: Dict) -> str:
    """
    Format S/R analysis data for display in playbook.

    Args:
        sr_data: Output from analyze_for_tge_short()

    Returns:
        Formatted string summary
    """
    lines = []
    lines.append("=== Support/Resistance Analysis (L023) ===")

    # Price position
    position = sr_data.get('price_position', {})
    lines.append(f"Position: {position.get('position', 'UNKNOWN')}")
    lines.append(f"Favorability: {position.get('position_favorability', 'UNKNOWN')}")

    # Nearest levels
    if position.get('nearest_resistance'):
        r = position['nearest_resistance']
        lines.append(f"Nearest Resistance: ${r['price']:.6f} ({r['distance_pct']:+.1f}%) [{r['strength']}]")

    if position.get('nearest_support'):
        s = position['nearest_support']
        lines.append(f"Nearest Support: ${s['price']:.6f} ({s['distance_pct']:+.1f}%) [{s['strength']}]")

    # Trendlines
    trendlines = sr_data.get('trendlines', {})
    if trendlines.get('downtrend', {}).get('active'):
        dt = trendlines['downtrend']
        lines.append(f"Downtrend Active: {dt['touch_count']} touches, R at ${dt.get('current_resistance_price', 0):.6f}")
    if trendlines.get('uptrend', {}).get('active'):
        ut = trendlines['uptrend']
        lines.append(f"Uptrend Active: {ut['touch_count']} touches, S at ${ut.get('current_support_price', 0):.6f}")

    # Role reversal
    role_rev = sr_data.get('role_reversal', {})
    if role_rev.get('reversal_detected'):
        lines.append(f"Role Reversal: {role_rev['original_role']} -> {role_rev['current_role']} [{role_rev['signal_strength']}]")

    # Confidence adjustment
    adj = sr_data.get('confidence_adjustment', 0)
    sign = '+' if adj >= 0 else ''
    lines.append(f"Confidence Adjustment: {sign}{adj} points")

    # Signals
    if sr_data.get('signals'):
        lines.append("Signals: " + ", ".join(sr_data['signals']))

    return "\n".join(lines)


# Example usage and testing
if __name__ == "__main__":
    # Create sample OHLCV data for testing
    sample_data = [
        {'timestamp': '2025-12-01', 'open': 0.100, 'high': 0.105, 'low': 0.095, 'close': 0.102, 'volume': 1000000},
        {'timestamp': '2025-12-02', 'open': 0.102, 'high': 0.108, 'low': 0.098, 'close': 0.106, 'volume': 1200000},
        {'timestamp': '2025-12-03', 'open': 0.106, 'high': 0.112, 'low': 0.104, 'close': 0.110, 'volume': 1100000},
        {'timestamp': '2025-12-04', 'open': 0.110, 'high': 0.115, 'low': 0.107, 'close': 0.108, 'volume': 900000},
        {'timestamp': '2025-12-05', 'open': 0.108, 'high': 0.111, 'low': 0.095, 'close': 0.096, 'volume': 1500000},
        {'timestamp': '2025-12-06', 'open': 0.096, 'high': 0.098, 'low': 0.090, 'close': 0.092, 'volume': 1800000},
        {'timestamp': '2025-12-07', 'open': 0.092, 'high': 0.096, 'low': 0.088, 'close': 0.094, 'volume': 1300000},
        {'timestamp': '2025-12-08', 'open': 0.094, 'high': 0.097, 'low': 0.091, 'close': 0.095, 'volume': 1100000},
        {'timestamp': '2025-12-09', 'open': 0.095, 'high': 0.100, 'low': 0.093, 'close': 0.098, 'volume': 1000000},
        {'timestamp': '2025-12-10', 'open': 0.098, 'high': 0.102, 'low': 0.095, 'close': 0.096, 'volume': 900000},
    ]

    detector = SupportResistanceDetector()
    current_price = 0.096

    print("Testing Support/Resistance Detector")
    print("=" * 50)

    # Test support detection
    supports = detector.detect_support_levels(sample_data, current_price)
    print(f"\nSupport Levels Found: {len(supports)}")
    for s in supports:
        print(f"  ${s['price']:.4f} - {s['strength']} ({s['touch_count']} touches)")

    # Test resistance detection
    resistances = detector.detect_resistance_levels(sample_data, current_price)
    print(f"\nResistance Levels Found: {len(resistances)}")
    for r in resistances:
        print(f"  ${r['price']:.4f} - {r['strength']} ({r['touch_count']} touches)")

    # Test trendlines
    trendlines = detector.detect_trendlines(sample_data)
    print(f"\nTrendlines:")
    print(f"  Uptrend Active: {trendlines['uptrend']['active']}")
    print(f"  Downtrend Active: {trendlines['downtrend']['active']}")

    # Test complete analysis
    analysis = detector.analyze_for_tge_short(sample_data, current_price)
    print(f"\n{format_sr_summary(analysis)}")
