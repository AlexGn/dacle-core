"""
Funding Rates Fetcher - Binance Futures API (FREE)

Session 336: Added based on David's recommendation for finding opportunities.
Initially tried CoinGlass but it requires API auth.
Binance Futures API is FREE and requires no authentication.

API Documentation: https://binance-docs.github.io/apidocs/futures/en/
Rate Limit: 1200/minute (very generous)
Cost: $0/month

Key Endpoints:
- /fapi/v1/fundingRate - Historical funding rates
- /fapi/v1/premiumIndex - Current funding rates + next funding time

Use Cases:
- Detect extremely negative funding (short squeeze risk) - L051
- Find tokens with high positive funding (crowded longs, potential shorts)
- Monitor market sentiment through funding rate divergences
"""

import logging
import requests
from typing import Dict, List, Optional, Any
from datetime import datetime
from collections import defaultdict

logger = logging.getLogger(__name__)


class FundingRateFetcher:
    """
    Fetches funding rate data from Binance Futures API.

    Funding rates indicate market positioning:
    - Positive funding = longs pay shorts (crowded longs, bearish signal)
    - Negative funding = shorts pay longs (crowded shorts, bullish signal)

    Per L051: Extremely negative funding (-0.10%+) = short squeeze risk
    """

    BASE_URL = "https://fapi.binance.com"
    TIMEOUT = 30

    def __init__(self):
        """Initialize Binance Futures fetcher."""
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "DACLE-TGE-Scanner/1.0"
        })

    def get_funding_rates(self, limit: int = 50) -> List[Dict]:
        """
        Get current funding rates for all perpetual contracts.

        Uses Binance premiumIndex endpoint for current rates.

        Returns:
            List of tokens with funding rate data, sorted by absolute rate
        """
        try:
            # Get current funding rates from premiumIndex
            url = f"{self.BASE_URL}/fapi/v1/premiumIndex"
            response = self.session.get(url, timeout=self.TIMEOUT)
            response.raise_for_status()

            data = response.json()

            results = []
            for item in data:
                symbol = item.get("symbol", "")

                # Skip non-USDT pairs
                if not symbol.endswith("USDT"):
                    continue

                # Remove USDT suffix for cleaner display
                base_symbol = symbol.replace("USDT", "")
                rate = float(item.get("lastFundingRate") or 0)

                results.append({
                    "symbol": base_symbol,
                    "pair": symbol,
                    "funding_rate": rate,
                    "funding_rate_pct": rate * 100,  # Convert to percentage
                    "mark_price": float(item.get("markPrice") or 0),
                    "index_price": float(item.get("indexPrice") or 0),
                    "next_funding_time": item.get("nextFundingTime"),
                    "interest_rate": float(item.get("interestRate") or 0),
                    "data_source": "binance_funding",
                    "fetched_at": datetime.utcnow().isoformat()
                })

            # Sort by absolute funding rate (most extreme first)
            results.sort(key=lambda x: abs(x["funding_rate"]), reverse=True)

            logger.info(f"Binance: Found {len(results)} USDT perpetuals with funding data")
            return results[:limit]

        except Exception as e:
            logger.error(f"Binance get_funding_rates failed: {e}")
            return []

    def get_historical_funding(self, symbol: str, limit: int = 100) -> List[Dict]:
        """
        Get historical funding rates for a specific symbol.

        Args:
            symbol: Token symbol (e.g., "BTC" - will append USDT)
            limit: Number of historical records (max 1000)

        Returns:
            List of historical funding rate records
        """
        try:
            pair = f"{symbol.upper()}USDT"
            url = f"{self.BASE_URL}/fapi/v1/fundingRate"
            params = {"symbol": pair, "limit": min(limit, 1000)}

            response = self.session.get(url, params=params, timeout=self.TIMEOUT)
            response.raise_for_status()

            data = response.json()

            results = []
            for item in data:
                rate = float(item.get("fundingRate") or 0)
                results.append({
                    "symbol": symbol.upper(),
                    "pair": pair,
                    "funding_rate": rate,
                    "funding_rate_pct": rate * 100,
                    "funding_time": item.get("fundingTime"),
                    "mark_price": float(item.get("markPrice") or 0),
                    "data_source": "binance_funding_history",
                    "fetched_at": datetime.utcnow().isoformat()
                })

            logger.info(f"Binance: Found {len(results)} historical funding records for {symbol}")
            return results

        except Exception as e:
            logger.error(f"Binance get_historical_funding({symbol}) failed: {e}")
            return []

    def get_extreme_negative_funding(self, threshold: float = -0.05) -> List[Dict]:
        """
        Get tokens with extremely negative funding rates.

        Per L051: Negative funding indicates crowded shorts, squeeze risk.
        - < -0.10%: SKIP shorts (very high squeeze risk)
        - < -0.05%: 25% position size
        - < -0.01%: 50% position size

        Args:
            threshold: Funding rate threshold (default -0.05% = -0.0005)

        Returns:
            List of tokens with negative funding below threshold
        """
        all_funding = self.get_funding_rates(limit=200)

        # Filter for negative funding below threshold
        extreme_negative = [
            f for f in all_funding
            if f["funding_rate"] < threshold / 100  # Convert threshold to decimal
        ]

        # Sort by most negative first
        extreme_negative.sort(key=lambda x: x["funding_rate"])

        logger.info(f"CoinGlass: Found {len(extreme_negative)} tokens with funding < {threshold}%")
        return extreme_negative

    def get_extreme_positive_funding(self, threshold: float = 0.05) -> List[Dict]:
        """
        Get tokens with extremely positive funding rates.

        Positive funding indicates crowded longs - potential SHORT opportunity.

        Args:
            threshold: Funding rate threshold (default 0.05% = 0.0005)

        Returns:
            List of tokens with positive funding above threshold
        """
        all_funding = self.get_funding_rates(limit=200)

        # Filter for positive funding above threshold
        extreme_positive = [
            f for f in all_funding
            if f["funding_rate"] > threshold / 100
        ]

        # Sort by most positive first
        extreme_positive.sort(key=lambda x: x["funding_rate"], reverse=True)

        logger.info(f"CoinGlass: Found {len(extreme_positive)} tokens with funding > {threshold}%")
        return extreme_positive

    def get_open_interest(self, limit: int = 50) -> List[Dict]:
        """
        Get open interest data for all perpetual contracts.

        Returns:
            List of tokens with open interest data, sorted by OI descending
        """
        try:
            url = f"{self.BASE_URL}/fapi/v1/openInterest"
            # Need to get OI for all symbols - use ticker endpoint instead
            url = f"{self.BASE_URL}/fapi/v1/ticker/24hr"

            response = self.session.get(url, timeout=self.TIMEOUT)
            response.raise_for_status()

            data = response.json()

            results = []
            for item in data:
                symbol = item.get("symbol", "")

                if not symbol.endswith("USDT"):
                    continue

                base_symbol = symbol.replace("USDT", "")
                volume = float(item.get("quoteVolume") or 0)
                price_change = float(item.get("priceChangePercent") or 0)

                results.append({
                    "symbol": base_symbol,
                    "pair": symbol,
                    "price": float(item.get("lastPrice") or 0),
                    "price_change_pct": price_change,
                    "volume_24h": volume,
                    "high_24h": float(item.get("highPrice") or 0),
                    "low_24h": float(item.get("lowPrice") or 0),
                    "trades_24h": int(item.get("count") or 0),
                    "data_source": "binance_futures_ticker",
                    "fetched_at": datetime.utcnow().isoformat()
                })

            # Sort by volume descending
            results.sort(key=lambda x: x["volume_24h"], reverse=True)

            logger.info(f"Binance: Found {len(results)} futures tickers")
            return results[:limit]

        except Exception as e:
            logger.error(f"Binance get_open_interest failed: {e}")
            return []

    def classify_funding_risk(self, funding_rate: float) -> Dict:
        """
        Classify funding rate risk per L051.

        Args:
            funding_rate: Funding rate as decimal (e.g., -0.001 = -0.1%)

        Returns:
            Dict with risk classification and position modifier
        """
        rate_pct = funding_rate * 100

        if rate_pct < -0.10:
            return {
                "risk_level": "EXTREME_SQUEEZE_RISK",
                "position_modifier": 0.0,  # SKIP shorts entirely
                "action": "SKIP",
                "reason": f"Funding {rate_pct:.3f}% = extreme short squeeze risk"
            }
        elif rate_pct < -0.05:
            return {
                "risk_level": "HIGH_SQUEEZE_RISK",
                "position_modifier": 0.25,
                "action": "REDUCE",
                "reason": f"Funding {rate_pct:.3f}% = high short squeeze risk, 25% size"
            }
        elif rate_pct < -0.01:
            return {
                "risk_level": "MODERATE_SQUEEZE_RISK",
                "position_modifier": 0.50,
                "action": "REDUCE",
                "reason": f"Funding {rate_pct:.3f}% = moderate squeeze risk, 50% size"
            }
        elif rate_pct > 0.10:
            return {
                "risk_level": "EXTREME_LONG_CROWDING",
                "position_modifier": 1.25,  # Bonus for shorts
                "action": "INCREASE",
                "reason": f"Funding {rate_pct:.3f}% = crowded longs, good for shorts"
            }
        elif rate_pct > 0.05:
            return {
                "risk_level": "HIGH_LONG_CROWDING",
                "position_modifier": 1.15,
                "action": "INCREASE",
                "reason": f"Funding {rate_pct:.3f}% = high long crowding"
            }
        else:
            return {
                "risk_level": "NEUTRAL",
                "position_modifier": 1.0,
                "action": "NORMAL",
                "reason": f"Funding {rate_pct:.3f}% = neutral positioning"
            }


def main():
    """Test the Funding Rate fetcher (Binance Futures API)."""
    import json

    fetcher = FundingRateFetcher()

    print("=" * 60)
    print("Funding Rate API Test (Binance Futures)")
    print("=" * 60)

    # Test funding rates
    print("\n💰 Top 15 Funding Rates (by absolute value):")
    funding = fetcher.get_funding_rates(limit=15)
    if funding:
        for f in funding:
            rate = f['funding_rate_pct']
            sign = '+' if rate >= 0 else ''
            risk = fetcher.classify_funding_risk(f['funding_rate'])
            print(f"  {f['symbol']:10s} {sign}{rate:7.4f}%  {risk['risk_level']}")
    else:
        print("  (No data available)")

    # Test extreme negative funding
    print("\n⚠️  Extreme Negative Funding (<-0.05%):")
    negative = fetcher.get_extreme_negative_funding(threshold=-0.05)
    if negative:
        for n in negative[:5]:
            print(f"  {n['symbol']:10s} {n['funding_rate_pct']:7.4f}%  SQUEEZE RISK")
    else:
        print("  (None found)")

    # Test extreme positive funding
    print("\n🎯 Extreme Positive Funding (>0.05%):")
    positive = fetcher.get_extreme_positive_funding(threshold=0.05)
    if positive:
        for p in positive[:5]:
            print(f"  {p['symbol']:10s} +{p['funding_rate_pct']:7.4f}%  CROWDED LONGS")
    else:
        print("  (None found)")

    # Test top volume futures
    print("\n📊 Top 10 Futures by Volume:")
    tickers = fetcher.get_open_interest(limit=10)
    if tickers:
        for t in tickers:
            pct = t['price_change_pct']
            sign = '+' if pct >= 0 else ''
            print(f"  {t['symbol']:10s} {sign}{pct:6.2f}%  Vol: ${t['volume_24h']/1e9:.2f}B")
    else:
        print("  (No data available)")


if __name__ == "__main__":
    main()
