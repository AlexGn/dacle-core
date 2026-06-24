"""
Liquidity Validator - P0 Critical Safety Feature (Session 291)

Validates token liquidity to prevent honeypot/low-liquidity tokens from receiving
high conviction scores. Uses CoinGecko API to fetch 2% market depth.

PROBLEM: System can give 8/10 conviction to tokens with only $5K liquidity
SOLUTION: Fetch market depth, calculate liquidity/FDV ratio, apply conviction penalty

**Gemini's Rationale** (Session 291):
"Validates consistency, NOT tradeability. System can give 8/10 conviction to a
honeypot token. This prevents scoring tokens with $5K liquidity as 8/10."

Risk Thresholds:
- CRITICAL: liquidity_depth < $5,000 (honeypot risk)
- HIGH: liquidity_depth < $10,000 (slippage >5% for $1K trade)
- MODERATE: liquidity_depth < $50,000 (slippage >1% for $5K trade)
- LOW: liquidity_depth >= $50,000 (acceptable)

Conviction Penalties:
- CRITICAL: -3.0 (block from EXECUTE tier)
- HIGH: -2.0 (reduce from 8/10 to 6/10)
- MODERATE: -1.0 (reduce from 8/10 to 7/10)
- LOW: 0.0 (no penalty)

Integration:
- Called by data_consolidator.py after consolidation
- Stored in _validation.liquidity field
- Used by conviction scoring to apply penalties

Created: Session 291 (2026-01-06)
"""

import logging
from typing import Dict, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import requests

logger = logging.getLogger(__name__)


class LiquidityRisk(Enum):
    """Liquidity risk severity levels"""
    CRITICAL = "CRITICAL"  # < $5K
    HIGH = "HIGH"  # < $10K
    MODERATE = "MODERATE"  # < $50K
    LOW = "LOW"  # >= $50K
    UNKNOWN = "UNKNOWN"  # API failure or missing data


@dataclass
class LiquidityValidationResult:
    """Result of liquidity validation"""
    risk_level: LiquidityRisk
    liquidity_depth_usd: Optional[float]
    liquidity_fdv_ratio: Optional[float]
    conviction_penalty: float
    warning_message: Optional[str]
    data_source: str
    confidence: str  # HIGH, MEDIUM, LOW


class LiquidityValidator:
    """
    Validates token liquidity using market depth data from CoinGecko API

    **Key Metrics**:
    - 2% Market Depth: Total liquidity within ±2% of current price
    - Liquidity/FDV Ratio: Market depth as % of fully diluted valuation

    **API Endpoints**:
    - CoinGecko: /coins/{id}/tickers (order book depth)
    - Binance: /api/v3/depth?symbol={symbol}&limit=100 (fallback)
    """

    CRITICAL_THRESHOLD = 5_000  # $5K
    HIGH_THRESHOLD = 10_000  # $10K
    MODERATE_THRESHOLD = 50_000  # $50K

    CRITICAL_PENALTY = -3.0
    HIGH_PENALTY = -2.0
    MODERATE_PENALTY = -1.0
    LOW_PENALTY = 0.0

    def __init__(self):
        self.coingecko_base_url = "https://api.coingecko.com/api/v3"
        self.binance_base_url = "https://api.binance.com/api/v3"
        self.timeout = 10

    def validate_liquidity(
        self,
        coingecko_id: Optional[str] = None,
        symbol: Optional[str] = None,
        fdv: Optional[float] = None,
        current_price: Optional[float] = None
    ) -> LiquidityValidationResult:
        """
        Validate token liquidity and calculate conviction penalty

        Args:
            coingecko_id: CoinGecko token ID (e.g., "bitcoin")
            symbol: Token symbol (e.g., "BTC")
            fdv: Fully diluted valuation in USD
            current_price: Current token price in USD

        Returns:
            LiquidityValidationResult with risk level and conviction penalty
        """
        # Try CoinGecko first (most reliable)
        if coingecko_id:
            result = self._fetch_coingecko_liquidity(coingecko_id)
            if result:
                return self._classify_liquidity(result, fdv)

        # Fallback to Binance order book depth
        if symbol:
            result = self._fetch_binance_liquidity(symbol, current_price)
            if result:
                return self._classify_liquidity(result, fdv)

        # No data available - conservative approach
        logger.warning(f"Liquidity data unavailable for {symbol or coingecko_id}")
        return LiquidityValidationResult(
            risk_level=LiquidityRisk.UNKNOWN,
            liquidity_depth_usd=None,
            liquidity_fdv_ratio=None,
            conviction_penalty=self.MODERATE_PENALTY,  # Conservative penalty
            warning_message="Liquidity data unavailable - applying MODERATE penalty for safety",
            data_source="NONE",
            confidence="VERY_LOW"
        )

    def _fetch_coingecko_liquidity(self, coingecko_id: str) -> Optional[Dict]:
        """
        Fetch 2% market depth from CoinGecko tickers endpoint

        Returns:
            {
                "liquidity_depth_usd": float,
                "bid_ask_spread_pct": float,
                "data_source": "coingecko"
            }
        """
        try:
            url = f"{self.coingecko_base_url}/coins/{coingecko_id}/tickers"
            params = {"depth": "true"}  # Request order book depth data

            response = requests.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()

            # Extract order book depth from tickers
            # CoinGecko returns bid/ask depth for each ticker
            total_depth = 0
            ticker_count = 0

            for ticker in data.get("tickers", [])[:5]:  # Top 5 exchanges
                # Cost to move price ±2%
                cost_to_move_up = ticker.get("cost_to_move_up_usd")
                cost_to_move_down = ticker.get("cost_to_move_down_usd")

                if cost_to_move_up and cost_to_move_down:
                    # Average of bid and ask side depth
                    depth = (cost_to_move_up + cost_to_move_down) / 2
                    total_depth += depth
                    ticker_count += 1

            if ticker_count == 0:
                logger.warning(f"No order book depth data for {coingecko_id}")
                return None

            # Average depth across top exchanges
            avg_depth = total_depth / ticker_count

            return {
                "liquidity_depth_usd": avg_depth,
                "bid_ask_spread_pct": None,  # CoinGecko doesn't provide this
                "data_source": "coingecko",
                "confidence": "HIGH"
            }

        except requests.exceptions.RequestException as e:
            logger.error(f"CoinGecko API error for {coingecko_id}: {e}")
            return None
        except (KeyError, ValueError) as e:
            logger.error(f"CoinGecko data parsing error: {e}")
            return None

    def _fetch_binance_liquidity(
        self,
        symbol: str,
        current_price: Optional[float]
    ) -> Optional[Dict]:
        """
        Fallback: Calculate 2% market depth from Binance order book

        Args:
            symbol: Trading pair (e.g., "BTCUSDT")
            current_price: Current price to calculate ±2% range

        Returns:
            {
                "liquidity_depth_usd": float,
                "bid_ask_spread_pct": float,
                "data_source": "binance"
            }
        """
        try:
            # Add USDT suffix if not present
            trading_pair = symbol.upper()
            if not trading_pair.endswith("USDT"):
                trading_pair = f"{trading_pair}USDT"

            url = f"{self.binance_base_url}/depth"
            params = {"symbol": trading_pair, "limit": 100}

            response = requests.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()

            bids = data.get("bids", [])
            asks = data.get("asks", [])

            if not bids or not asks or not current_price:
                logger.warning(f"Insufficient order book data for {trading_pair}")
                return None

            # Calculate 2% price range
            price_up_2pct = current_price * 1.02
            price_down_2pct = current_price * 0.98

            # Sum liquidity within ±2% range
            bid_depth = sum(
                float(price) * float(qty)
                for price, qty in bids
                if float(price) >= price_down_2pct
            )

            ask_depth = sum(
                float(price) * float(qty)
                for price, qty in asks
                if float(price) <= price_up_2pct
            )

            total_depth = bid_depth + ask_depth

            # Calculate bid-ask spread
            best_bid = float(bids[0][0])
            best_ask = float(asks[0][0])
            spread_pct = ((best_ask - best_bid) / best_bid) * 100

            return {
                "liquidity_depth_usd": total_depth,
                "bid_ask_spread_pct": spread_pct,
                "data_source": "binance",
                "confidence": "MEDIUM"
            }

        except requests.exceptions.RequestException as e:
            logger.error(f"Binance API error for {symbol}: {e}")
            return None
        except (KeyError, ValueError, IndexError) as e:
            logger.error(f"Binance data parsing error: {e}")
            return None

    def _classify_liquidity(
        self,
        liquidity_data: Dict,
        fdv: Optional[float]
    ) -> LiquidityValidationResult:
        """
        Classify liquidity risk and calculate conviction penalty

        Args:
            liquidity_data: Output from _fetch_coingecko_liquidity or _fetch_binance_liquidity
            fdv: Fully diluted valuation in USD

        Returns:
            LiquidityValidationResult with risk classification
        """
        liquidity_depth = liquidity_data["liquidity_depth_usd"]
        data_source = liquidity_data["data_source"]
        confidence = liquidity_data.get("confidence", "MEDIUM")

        # Calculate liquidity/FDV ratio
        liquidity_fdv_ratio = None
        if fdv and fdv > 0:
            liquidity_fdv_ratio = (liquidity_depth / fdv) * 100

        # Classify risk level
        if liquidity_depth < self.CRITICAL_THRESHOLD:
            risk_level = LiquidityRisk.CRITICAL
            penalty = self.CRITICAL_PENALTY
            warning = f"HONEYPOT RISK: Only ${liquidity_depth:,.0f} liquidity (<${self.CRITICAL_THRESHOLD:,})"

        elif liquidity_depth < self.HIGH_THRESHOLD:
            risk_level = LiquidityRisk.HIGH
            penalty = self.HIGH_PENALTY
            warning = f"LOW LIQUIDITY: ${liquidity_depth:,.0f} (<${self.HIGH_THRESHOLD:,}) - High slippage risk"

        elif liquidity_depth < self.MODERATE_THRESHOLD:
            risk_level = LiquidityRisk.MODERATE
            penalty = self.MODERATE_PENALTY
            warning = f"MODERATE LIQUIDITY: ${liquidity_depth:,.0f} (<${self.MODERATE_THRESHOLD:,}) - Slippage >1%"

        else:
            risk_level = LiquidityRisk.LOW
            penalty = self.LOW_PENALTY
            warning = None

        return LiquidityValidationResult(
            risk_level=risk_level,
            liquidity_depth_usd=liquidity_depth,
            liquidity_fdv_ratio=liquidity_fdv_ratio,
            conviction_penalty=penalty,
            warning_message=warning,
            data_source=data_source,
            confidence=confidence
        )


def validate_token_liquidity(
    coingecko_id: Optional[str] = None,
    symbol: Optional[str] = None,
    fdv: Optional[float] = None,
    current_price: Optional[float] = None
) -> Dict:
    """
    Convenience function for liquidity validation

    Args:
        coingecko_id: CoinGecko token ID
        symbol: Token symbol
        fdv: Fully diluted valuation
        current_price: Current price

    Returns:
        Dict with validation results for storage in consolidated.json
    """
    validator = LiquidityValidator()
    result = validator.validate_liquidity(
        coingecko_id=coingecko_id,
        symbol=symbol,
        fdv=fdv,
        current_price=current_price
    )

    return {
        "risk_level": result.risk_level.value,
        "liquidity_depth_usd": result.liquidity_depth_usd,
        "liquidity_fdv_ratio": result.liquidity_fdv_ratio,
        "conviction_penalty": result.conviction_penalty,
        "warning_message": result.warning_message,
        "data_source": result.data_source,
        "confidence": result.confidence
    }


if __name__ == "__main__":
    # Example usage
    import sys

    if len(sys.argv) < 2:
        print("Usage: python liquidity_validator.py <symbol> [fdv] [price]")
        sys.exit(1)

    symbol = sys.argv[1]
    fdv = float(sys.argv[2]) if len(sys.argv) > 2 else None
    price = float(sys.argv[3]) if len(sys.argv) > 3 else None

    result = validate_token_liquidity(
        symbol=symbol,
        fdv=fdv,
        current_price=price
    )

    print(f"\n{'='*60}")
    print(f"LIQUIDITY VALIDATION: {symbol}")
    print(f"{'='*60}")
    print(f"Risk Level: {result['risk_level']}")
    print(f"Liquidity Depth: ${result['liquidity_depth_usd']:,.0f}" if result['liquidity_depth_usd'] else "N/A")
    print(f"Liquidity/FDV: {result['liquidity_fdv_ratio']:.4f}%" if result['liquidity_fdv_ratio'] else "N/A")
    print(f"Conviction Penalty: {result['conviction_penalty']}")
    print(f"Warning: {result['warning_message']}")
    print(f"Source: {result['data_source']}")
    print(f"Confidence: {result['confidence']}")
    print(f"{'='*60}\n")
