#!/usr/bin/env python3
"""
Market Regime Detector

Detects market regime (BULL/BEAR/CHOP) at specific date using:
- BTC/ETH price trends (30-day MA vs 90-day MA)
- Fear & Greed Index (sentiment validation)

Session 139: Critical data backfill to enable regime multipliers.
"""

import requests
from datetime import datetime, timedelta
from typing import Dict, Optional, List
import logging

logger = logging.getLogger(__name__)


class MarketRegimeDetector:
    """
    Detect market regime (BULL/BEAR/CHOP) at specific date.

    Uses BTC/ETH price trends + Fear & Greed Index.

    Regime Classification Rules:
    - BULL: Both BTC/ETH MA30 > MA90 by >5%, F&G > 50
    - BEAR: Both BTC/ETH MA30 < MA90 by >5%, F&G < 40
    - CHOP: Mixed signals or FLAT trends
    """

    def __init__(self):
        self.coingecko_base = "https://api.coingecko.com/api/v3"
        self.fear_greed_base = "https://api.alternative.me/fng"

        # Cache for price data to avoid redundant API calls
        self._price_cache: Dict[str, Dict] = {}

    def detect_regime(self, date: str) -> Dict:
        """
        Detect market regime at given date.

        Args:
            date: ISO date string (YYYY-MM-DD)

        Returns:
            {
                "regime": "BULL" | "BEAR" | "CHOP",
                "confidence": 0.0-1.0,
                "btc_trend": "UP" | "DOWN" | "FLAT",
                "eth_trend": "UP" | "DOWN" | "FLAT",
                "fear_greed": 0-100 or None,
                "reasoning": "..."
            }
        """
        target_date = datetime.fromisoformat(date)

        logger.info(f"Detecting market regime for {date}")

        # Get BTC/ETH trends (30-day MA vs 90-day MA)
        btc_trend = self._get_price_trend("bitcoin", target_date)
        eth_trend = self._get_price_trend("ethereum", target_date)

        # Get Fear & Greed Index
        fear_greed = self._get_fear_greed(target_date)

        # Classify regime
        regime, confidence = self._classify_regime(
            btc_trend, eth_trend, fear_greed
        )

        reasoning = self._generate_reasoning(
            regime, btc_trend, eth_trend, fear_greed
        )

        result = {
            "regime": regime,
            "confidence": confidence,
            "btc_trend": btc_trend["direction"],
            "eth_trend": eth_trend["direction"],
            "fear_greed": fear_greed,
            "reasoning": reasoning
        }

        logger.info(f"Regime detected: {regime} (confidence={confidence:.2f})")

        return result

    def _get_price_trend(self, coin_id: str, date: datetime) -> Dict:
        """
        Get price trend (30-day MA vs 90-day MA).

        Args:
            coin_id: "bitcoin" or "ethereum"
            date: Target date

        Returns:
            {
                "direction": "UP" | "DOWN" | "FLAT",
                "ma_30": float,
                "ma_90": float,
                "divergence": float  # % difference
            }
        """
        # Check cache
        cache_key = f"{coin_id}_{date.isoformat()}"
        if cache_key in self._price_cache:
            return self._price_cache[cache_key]

        # Get 120 days of prices (90-day MA needs 90 days + 30 days lookback)
        start_date = date - timedelta(days=120)
        end_date = date

        url = f"{self.coingecko_base}/coins/{coin_id}/market_chart/range"
        params = {
            "vs_currency": "usd",
            "from": int(start_date.timestamp()),
            "to": int(end_date.timestamp())
        }

        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            # Extract prices (data["prices"] is list of [timestamp, price])
            prices = [p[1] for p in data["prices"]]

            if len(prices) < 90:
                logger.warning(f"{coin_id}: Insufficient price data ({len(prices)} days)")
                # Return FLAT with low confidence
                return {
                    "direction": "FLAT",
                    "ma_30": 0.0,
                    "ma_90": 0.0,
                    "divergence": 0.0
                }

            # Calculate MAs
            ma_30 = sum(prices[-30:]) / len(prices[-30:])
            ma_90 = sum(prices[-90:]) / len(prices[-90:])

            divergence = ((ma_30 - ma_90) / ma_90) * 100

            # Classify direction
            if divergence > 5.0:
                direction = "UP"
            elif divergence < -5.0:
                direction = "DOWN"
            else:
                direction = "FLAT"

            result = {
                "direction": direction,
                "ma_30": ma_30,
                "ma_90": ma_90,
                "divergence": divergence
            }

            # Cache result
            self._price_cache[cache_key] = result

            return result

        except Exception as e:
            logger.error(f"Failed to fetch {coin_id} price data: {e}")
            # Return FLAT as fallback
            return {
                "direction": "FLAT",
                "ma_30": 0.0,
                "ma_90": 0.0,
                "divergence": 0.0
            }

    def _get_fear_greed(self, date: datetime) -> Optional[int]:
        """
        Get Fear & Greed Index at date (0-100).

        Returns:
            0-100: Fear & Greed score
            None: Data not available
        """
        # Fear & Greed API requires limit parameter
        # We'll get last 2000 days and find closest match
        url = f"{self.fear_greed_base}"
        params = {
            "limit": 2000,  # Max limit
            "format": "json"
        }

        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            if "data" not in data:
                return None

            # Find closest date match
            target_ts = int(date.timestamp())
            best_match = None
            min_diff = float('inf')

            for entry in data["data"]:
                entry_ts = int(entry["timestamp"])
                diff = abs(entry_ts - target_ts)

                if diff < min_diff:
                    min_diff = diff
                    best_match = entry

            # Only accept match within 7 days
            if best_match and min_diff < (7 * 86400):
                return int(best_match["value"])

            return None

        except Exception as e:
            logger.warning(f"Failed to fetch Fear & Greed data: {e}")
            return None

    def _classify_regime(
        self,
        btc_trend: Dict,
        eth_trend: Dict,
        fear_greed: Optional[int]
    ) -> tuple[str, float]:
        """
        Classify market regime based on signals.

        Rules:
        - BULL: Both BTC/ETH UP, F&G > 50 (high confidence)
        - BEAR: Both BTC/ETH DOWN, F&G < 40 (high confidence)
        - CHOP: Mixed signals or FLAT trends

        Returns:
            (regime, confidence)
        """
        btc_dir = btc_trend["direction"]
        eth_dir = eth_trend["direction"]

        # BULL market (strong uptrend)
        if btc_dir == "UP" and eth_dir == "UP":
            if fear_greed and fear_greed > 50:
                return "BULL", 0.95  # High confidence
            elif fear_greed and fear_greed >= 40:
                return "BULL", 0.80  # Medium-high confidence
            else:
                return "BULL", 0.70  # Medium confidence (price up but sentiment neutral/fear)

        # BEAR market (strong downtrend)
        elif btc_dir == "DOWN" and eth_dir == "DOWN":
            if fear_greed and fear_greed < 40:
                return "BEAR", 0.95  # High confidence
            elif fear_greed and fear_greed <= 50:
                return "BEAR", 0.80  # Medium-high confidence
            else:
                return "BEAR", 0.70  # Medium confidence (price down but sentiment greedy)

        # CHOP market (mixed/flat)
        else:
            if btc_dir == "FLAT" or eth_dir == "FLAT":
                confidence = 0.85  # High confidence in CHOP (clear sideways action)
            else:
                confidence = 0.75  # Medium confidence (BTC up ETH down or vice versa)

            return "CHOP", confidence

    def _generate_reasoning(
        self,
        regime: str,
        btc_trend: Dict,
        eth_trend: Dict,
        fear_greed: Optional[int]
    ) -> str:
        """Generate human-readable reasoning."""
        btc_dir = btc_trend["direction"]
        eth_dir = eth_trend["direction"]
        btc_div = btc_trend["divergence"]
        eth_div = eth_trend["divergence"]

        parts = [
            f"BTC: {btc_dir} ({btc_div:+.1f}% MA divergence)",
            f"ETH: {eth_dir} ({eth_div:+.1f}% MA divergence)"
        ]

        if fear_greed is not None:
            if fear_greed >= 75:
                sentiment = "Extreme Greed"
            elif fear_greed >= 55:
                sentiment = "Greed"
            elif fear_greed >= 45:
                sentiment = "Neutral"
            elif fear_greed >= 25:
                sentiment = "Fear"
            else:
                sentiment = "Extreme Fear"

            parts.append(f"F&G: {fear_greed}/100 ({sentiment})")
        else:
            parts.append("F&G: N/A")

        parts.append(f"→ {regime} market")

        return ", ".join(parts)


def detect_regime_for_date(date: str) -> Dict:
    """
    Convenience function to detect regime for a specific date.

    Args:
        date: ISO date string (YYYY-MM-DD)

    Returns:
        Regime detection result dict
    """
    detector = MarketRegimeDetector()
    return detector.detect_regime(date)


if __name__ == "__main__":
    # Test with known dates
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    if len(sys.argv) > 1:
        test_date = sys.argv[1]
    else:
        test_date = "2023-05-01"  # Default test date

    print(f"\n{'='*80}")
    print(f"TESTING MARKET REGIME DETECTOR")
    print(f"{'='*80}\n")

    result = detect_regime_for_date(test_date)

    print(f"Date: {test_date}")
    print(f"Regime: {result['regime']}")
    print(f"Confidence: {result['confidence']:.2f}")
    print(f"BTC Trend: {result['btc_trend']}")
    print(f"ETH Trend: {result['eth_trend']}")
    print(f"Fear & Greed: {result['fear_greed']}")
    print(f"Reasoning: {result['reasoning']}")
    print()
