"""
L085: MP-VWAP (Market Profile VWAP) Indicator

Combines Market Profile volume analysis with VWAP levels to identify
high-probability support/resistance zones.

Components:
- POC (Point of Control): Price level with highest volume
- VAH (Value Area High): Upper 70% of volume distribution
- VAL (Value Area Low): Lower 70% of volume distribution
- VWAP: Volume-Weighted Average Price (Quarterly/Yearly anchors)

Session 302 - January 8, 2026
Source: David's TradingView indicator screenshots
"""

import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import numpy as np

logger = logging.getLogger(__name__)


class VolumeProfileAnalyzer:
    """
    L085: Market Profile + VWAP analysis for institutional S/R levels.

    Usage:
        analyzer = VolumeProfileAnalyzer()
        result = analyzer.calculate_mp_vwap(ohlcv_data)
    """

    def __init__(self, num_bins: int = 24, value_area_pct: float = 0.70):
        """
        Initialize Volume Profile analyzer.

        Args:
            num_bins: Number of price bins for volume distribution (default 24)
            value_area_pct: Percentage of volume for value area (default 70%)
        """
        self.num_bins = num_bins
        self.value_area_pct = value_area_pct

    def calculate_volume_profile(
        self,
        ohlcv_data: List[Dict],
        custom_bins: Optional[int] = None
    ) -> Dict:
        """
        Calculate Market Profile from OHLCV data.

        Args:
            ohlcv_data: List of candles with keys: open, high, low, close, volume
            custom_bins: Override default number of bins

        Returns:
            {
                "poc": float,           # Point of Control price
                "vah": float,           # Value Area High
                "val": float,           # Value Area Low
                "volume_by_price": Dict[float, float],  # Volume at each price level
                "total_volume": float,
                "price_range": {"high": float, "low": float}
            }
        """
        if not ohlcv_data or len(ohlcv_data) < 2:
            logger.warning("L085: Insufficient OHLCV data for volume profile")
            return self._empty_profile()

        num_bins = custom_bins or self.num_bins

        # Find price range
        all_highs = [c.get('high', c.get('h', 0)) for c in ohlcv_data]
        all_lows = [c.get('low', c.get('l', 0)) for c in ohlcv_data]

        price_high = max(all_highs)
        price_low = min(all_lows)

        if price_high == price_low:
            logger.warning("L085: No price range in data")
            return self._empty_profile()

        # Create price bins
        bin_size = (price_high - price_low) / num_bins
        bins = {}
        for i in range(num_bins):
            bin_price = price_low + (i + 0.5) * bin_size  # Bin center
            bins[round(bin_price, 6)] = 0.0

        # Distribute volume to bins
        total_volume = 0.0
        for candle in ohlcv_data:
            high = candle.get('high', candle.get('h', 0))
            low = candle.get('low', candle.get('l', 0))
            volume = candle.get('volume', candle.get('v', 0))

            if volume <= 0:
                continue

            total_volume += volume

            # Distribute volume across price range of candle
            candle_bins = self._get_bins_for_candle(low, high, price_low, bin_size, num_bins)
            volume_per_bin = volume / len(candle_bins) if candle_bins else 0

            for bin_idx in candle_bins:
                bin_price = price_low + (bin_idx + 0.5) * bin_size
                bin_price = round(bin_price, 6)
                if bin_price in bins:
                    bins[bin_price] += volume_per_bin

        if total_volume == 0:
            logger.warning("L085: No volume in data")
            return self._empty_profile()

        # Find POC (bin with maximum volume)
        poc_price = max(bins, key=bins.get)

        # Calculate Value Area (70% of volume centered on POC)
        vah, val = self._calculate_value_area(bins, poc_price, total_volume)

        return {
            "poc": poc_price,
            "vah": vah,
            "val": val,
            "volume_by_price": bins,
            "total_volume": total_volume,
            "price_range": {"high": price_high, "low": price_low}
        }

    def _get_bins_for_candle(
        self,
        low: float,
        high: float,
        price_low: float,
        bin_size: float,
        num_bins: int
    ) -> List[int]:
        """Get list of bin indices that a candle spans."""
        if bin_size <= 0:
            return []

        start_bin = max(0, int((low - price_low) / bin_size))
        end_bin = min(num_bins - 1, int((high - price_low) / bin_size))

        return list(range(start_bin, end_bin + 1))

    def _calculate_value_area(
        self,
        bins: Dict[float, float],
        poc_price: float,
        total_volume: float
    ) -> Tuple[float, float]:
        """
        Calculate Value Area High and Low (70% of volume centered on POC).

        Uses the standard Market Profile algorithm:
        1. Start at POC
        2. Alternately add bins above and below
        3. Stop when 70% of volume is captured
        """
        target_volume = total_volume * self.value_area_pct

        # Sort bins by price
        sorted_prices = sorted(bins.keys())
        poc_idx = sorted_prices.index(poc_price) if poc_price in sorted_prices else len(sorted_prices) // 2

        # Start with POC
        included_volume = bins.get(poc_price, 0)
        low_idx = poc_idx
        high_idx = poc_idx

        # Expand outward until we capture 70% of volume
        while included_volume < target_volume:
            # Check volume above and below
            vol_above = bins.get(sorted_prices[high_idx + 1], 0) if high_idx + 1 < len(sorted_prices) else 0
            vol_below = bins.get(sorted_prices[low_idx - 1], 0) if low_idx - 1 >= 0 else 0

            if vol_above == 0 and vol_below == 0:
                break

            # Add the side with more volume
            if vol_above >= vol_below and high_idx + 1 < len(sorted_prices):
                high_idx += 1
                included_volume += vol_above
            elif low_idx - 1 >= 0:
                low_idx -= 1
                included_volume += vol_below
            elif high_idx + 1 < len(sorted_prices):
                high_idx += 1
                included_volume += vol_above
            else:
                break

        vah = sorted_prices[high_idx] if high_idx < len(sorted_prices) else sorted_prices[-1]
        val = sorted_prices[low_idx] if low_idx >= 0 else sorted_prices[0]

        return vah, val

    def _empty_profile(self) -> Dict:
        """Return empty profile structure."""
        return {
            "poc": None,
            "vah": None,
            "val": None,
            "volume_by_price": {},
            "total_volume": 0,
            "price_range": {"high": None, "low": None}
        }

    def calculate_vwap(
        self,
        ohlcv_data: List[Dict],
        anchor: str = 'all'  # 'all', 'quarterly', 'yearly', or ISO date string
    ) -> Optional[float]:
        """
        Calculate Volume-Weighted Average Price.

        Args:
            ohlcv_data: List of candles
            anchor: Anchor point for VWAP calculation

        Returns:
            VWAP value or None if insufficient data
        """
        if not ohlcv_data:
            return None

        # Filter data based on anchor
        filtered_data = self._filter_by_anchor(ohlcv_data, anchor)

        if not filtered_data:
            return None

        cumulative_tp_vol = 0.0
        cumulative_vol = 0.0

        for candle in filtered_data:
            high = candle.get('high', candle.get('h', 0))
            low = candle.get('low', candle.get('l', 0))
            close = candle.get('close', candle.get('c', 0))
            volume = candle.get('volume', candle.get('v', 0))

            typical_price = (high + low + close) / 3
            cumulative_tp_vol += typical_price * volume
            cumulative_vol += volume

        if cumulative_vol == 0:
            return None

        return cumulative_tp_vol / cumulative_vol

    def _filter_by_anchor(
        self,
        ohlcv_data: List[Dict],
        anchor: str
    ) -> List[Dict]:
        """Filter OHLCV data based on anchor type."""
        if anchor == 'all':
            return ohlcv_data

        now = datetime.utcnow()

        if anchor == 'quarterly':
            # Start of current quarter
            quarter_month = ((now.month - 1) // 3) * 3 + 1
            anchor_date = datetime(now.year, quarter_month, 1)
        elif anchor == 'yearly':
            # Start of current year
            anchor_date = datetime(now.year, 1, 1)
        else:
            # Try to parse as ISO date
            try:
                anchor_date = datetime.fromisoformat(anchor)
            except ValueError:
                return ohlcv_data

        # Filter candles after anchor date
        filtered = []
        for candle in ohlcv_data:
            timestamp = candle.get('timestamp', candle.get('time', candle.get('t')))
            if timestamp:
                if isinstance(timestamp, (int, float)):
                    candle_date = datetime.utcfromtimestamp(timestamp / 1000 if timestamp > 1e10 else timestamp)
                elif isinstance(timestamp, str):
                    candle_date = datetime.fromisoformat(timestamp.replace('Z', '+00:00').replace('+00:00', ''))
                else:
                    continue

                if candle_date >= anchor_date:
                    filtered.append(candle)

        return filtered if filtered else ohlcv_data

    def calculate_mp_vwap(
        self,
        ohlcv_data: List[Dict],
        current_price: float,
        vwap_anchor: str = 'quarterly'
    ) -> Dict:
        """
        L085: Combined Market Profile + VWAP analysis.

        Args:
            ohlcv_data: List of OHLCV candles
            current_price: Current token price
            vwap_anchor: 'quarterly', 'yearly', or custom date

        Returns:
            {
                "vwap": float,
                "poc": float,
                "vah": float,
                "val": float,
                "current_price": float,
                "zone": "STRONG_BULLISH" | "WEAK_BULLISH" | "WEAK_BEARISH" | "STRONG_BEARISH",
                "confluence": bool,
                "confluence_strength": "STRONG" | "MODERATE" | "NONE",
                "signal": "LONG_ZONE" | "SHORT_ZONE" | "NEUTRAL",
                "distance_to_poc_pct": float,
                "distance_to_vwap_pct": float
            }
        """
        # Calculate volume profile
        profile = self.calculate_volume_profile(ohlcv_data)

        # Calculate VWAP
        vwap = self.calculate_vwap(ohlcv_data, vwap_anchor)

        if profile['poc'] is None or vwap is None:
            return self._empty_mp_vwap(current_price)

        poc = profile['poc']
        vah = profile['vah']
        val = profile['val']

        # Determine zone
        zone = self._classify_zone(current_price, vwap, poc)

        # Check confluence (price near both VWAP and POC)
        confluence, confluence_strength = self._check_confluence(current_price, vwap, poc)

        # Determine signal
        signal = self._determine_signal(current_price, vwap, poc, vah, val)

        # Calculate distances
        dist_to_poc = ((current_price - poc) / poc * 100) if poc else 0
        dist_to_vwap = ((current_price - vwap) / vwap * 100) if vwap else 0

        return {
            "vwap": round(vwap, 6) if vwap else None,
            "poc": round(poc, 6) if poc else None,
            "vah": round(vah, 6) if vah else None,
            "val": round(val, 6) if val else None,
            "current_price": current_price,
            "zone": zone,
            "confluence": confluence,
            "confluence_strength": confluence_strength,
            "signal": signal,
            "distance_to_poc_pct": round(dist_to_poc, 2),
            "distance_to_vwap_pct": round(dist_to_vwap, 2),
            "volume_profile": profile
        }

    def _classify_zone(
        self,
        price: float,
        vwap: float,
        poc: float
    ) -> str:
        """
        Classify price zone based on position relative to VWAP and POC.

        STRONG_BULLISH: Above both VWAP and POC
        WEAK_BULLISH: Above VWAP, below POC
        WEAK_BEARISH: Below VWAP, above POC
        STRONG_BEARISH: Below both VWAP and POC
        """
        above_vwap = price > vwap
        above_poc = price > poc

        if above_vwap and above_poc:
            return "STRONG_BULLISH"
        elif above_vwap and not above_poc:
            return "WEAK_BULLISH"
        elif not above_vwap and above_poc:
            return "WEAK_BEARISH"
        else:
            return "STRONG_BEARISH"

    def _check_confluence(
        self,
        price: float,
        vwap: float,
        poc: float,
        tolerance_pct: float = 1.5
    ) -> Tuple[bool, str]:
        """
        Check if price is at MP-VWAP confluence zone.

        STRONG: Price within tolerance of BOTH VWAP and POC
        MODERATE: Price within tolerance of either VWAP or POC
        NONE: Price not near either level
        """
        near_vwap = abs(price - vwap) / vwap * 100 <= tolerance_pct if vwap else False
        near_poc = abs(price - poc) / poc * 100 <= tolerance_pct if poc else False

        if near_vwap and near_poc:
            return True, "STRONG"
        elif near_vwap or near_poc:
            return True, "MODERATE"
        return False, "NONE"

    def _determine_signal(
        self,
        price: float,
        vwap: float,
        poc: float,
        vah: float,
        val: float
    ) -> str:
        """
        Determine trading signal based on MP-VWAP analysis.

        LONG_ZONE: Price at/near VAL or below POC with bullish structure
        SHORT_ZONE: Price at/near VAH or above POC with bearish structure
        NEUTRAL: Price in middle of value area
        """
        if val and vah:
            value_area_range = vah - val

            # Near VAL (lower value area) = potential long zone
            if price <= val * 1.01:  # Within 1% of VAL
                return "LONG_ZONE"

            # Near VAH (upper value area) = potential short zone
            if price >= vah * 0.99:  # Within 1% of VAH
                return "SHORT_ZONE"

        # Check vs VWAP and POC
        if vwap and poc:
            avg_level = (vwap + poc) / 2
            if price < avg_level * 0.98:
                return "LONG_ZONE"
            elif price > avg_level * 1.02:
                return "SHORT_ZONE"

        return "NEUTRAL"

    def _empty_mp_vwap(self, current_price: float) -> Dict:
        """Return empty MP-VWAP result."""
        return {
            "vwap": None,
            "poc": None,
            "vah": None,
            "val": None,
            "current_price": current_price,
            "zone": "UNKNOWN",
            "confluence": False,
            "confluence_strength": "NONE",
            "signal": "NEUTRAL",
            "distance_to_poc_pct": 0,
            "distance_to_vwap_pct": 0,
            "volume_profile": self._empty_profile()
        }


# Convenience function for external use
def calculate_mp_vwap(
    ohlcv_data: List[Dict],
    current_price: float,
    vwap_anchor: str = 'quarterly',
    num_bins: int = 24
) -> Dict:
    """
    L085: Calculate MP-VWAP (Market Profile + VWAP) analysis.

    Args:
        ohlcv_data: List of OHLCV candles
        current_price: Current token price
        vwap_anchor: 'quarterly', 'yearly', or custom date
        num_bins: Number of price bins for volume profile

    Returns:
        MP-VWAP analysis dict with zone, confluence, and signal
    """
    analyzer = VolumeProfileAnalyzer(num_bins=num_bins)
    return analyzer.calculate_mp_vwap(ohlcv_data, current_price, vwap_anchor)
