"""
Discovery TA - Lightweight technical analysis for the discovery pipeline.

Session 382: Part of Unified High-Conviction Token Discovery Pipeline.
Provides market context enrichment for discovered tokens WITHOUT requiring
entry/SL/TP levels (David draws those manually).

Reuses existing _compute_* modules from computed_ta_builder.py.

Usage:
    from src.ta.discovery_ta import run_discovery_ta
    result = run_discovery_ta("BTC")
    print(result.ta_bias, result.ta_confidence)
"""
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class DiscoveryTAResult:
    """Result of discovery-phase technical analysis."""
    # Status
    status: str = "OK"  # OK, DEX_ONLY, NO_DATA, ERROR
    error: str = ""

    # Market structure
    market_structure: str = "unknown"  # bearish / bullish / ranging

    # EMA alignment
    ema_alignment: str = "unknown"  # bearish / bullish / choppy
    ema_200_distance_pct: float = 0.0

    # RSI
    rsi_14: float = 50.0

    # Funding rate
    funding_rate: Optional[float] = None

    # Patterns
    pattern_count: int = 0
    bearish_patterns: int = 0
    bullish_patterns: int = 0

    # Support/Resistance
    near_support: bool = False
    near_resistance: bool = False
    sr_levels_count: int = 0

    # Volume
    volume_ratio: float = 1.0  # recent volume / avg volume (RVOL)

    # Volume profile
    volume_profile_zone: str = "unknown"  # STRONG_BULLISH, WEAK_BULLISH, WEAK_BEARISH, STRONG_BEARISH

    # TVEM signal
    tvem_signal: str = "NEUTRAL"  # BULLISH, BEARISH, NEUTRAL

    # Derived fields
    ta_bias: str = "NEUTRAL"  # SHORT_ALIGNED / LONG_ALIGNED / NEUTRAL
    ta_confidence: float = 0.0  # 0.0-1.0 based on # of aligned factors

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        return {
            "status": self.status,
            "error": self.error,
            "market_structure": self.market_structure,
            "ema_alignment": self.ema_alignment,
            "ema_200_distance_pct": round(self.ema_200_distance_pct, 2),
            "rsi_14": round(self.rsi_14, 1),
            "funding_rate": round(self.funding_rate, 6) if self.funding_rate is not None else None,
            "pattern_count": self.pattern_count,
            "bearish_patterns": self.bearish_patterns,
            "bullish_patterns": self.bullish_patterns,
            "near_support": self.near_support,
            "near_resistance": self.near_resistance,
            "sr_levels_count": self.sr_levels_count,
            "volume_ratio": round(self.volume_ratio, 2),
            "volume_profile_zone": self.volume_profile_zone,
            "tvem_signal": self.tvem_signal,
            "ta_bias": self.ta_bias,
            "ta_confidence": round(self.ta_confidence, 2),
        }


def run_discovery_ta(token_symbol: str, timeframe: str = "4h") -> DiscoveryTAResult:
    """
    Run lightweight TA for a discovered token.

    Fetches OHLCV data from Binance and runs all available analysis modules.
    Does NOT require entry/SL/TP levels -- pure market context.

    Args:
        token_symbol: Token symbol (e.g., "BTC", "MONAD")
        timeframe: OHLCV timeframe (default "4h")

    Returns:
        DiscoveryTAResult with market context data
    """
    result = DiscoveryTAResult()

    # Import compute functions from computed_ta_builder
    try:
        from src.ta.computed_ta_builder import (
            _fetch_ohlcv_binance,
            _ohlcv_to_dicts,
            _compute_market_structure,
            _compute_emas,
            _compute_rsi,
            _compute_funding_rate,
            _compute_patterns,
            _compute_sr_levels,
            _compute_volume_profile,
            _compute_volume_analysis,
            _compute_tvem,
        )
    except ImportError as e:
        logger.error(f"Failed to import computed_ta_builder: {e}")
        result.status = "ERROR"
        result.error = f"Import error: {e}"
        return result

    # Step 1: Fetch OHLCV data
    symbol = token_symbol.upper()
    ohlcv = _fetch_ohlcv_binance(symbol, timeframe, limit=200)

    if not ohlcv or len(ohlcv) < 20:
        logger.warning(f"No OHLCV data for {symbol} — likely DEX-only token")
        result.status = "DEX_ONLY"
        result.error = f"No Binance data for {symbol}"
        return result

    ohlcv_dicts = _ohlcv_to_dicts(ohlcv)
    current_price = ohlcv[-1][4]  # Last close

    # Step 2: Run all analysis modules (each gracefully handles errors)
    # Market structure
    ms_data = _compute_market_structure(symbol, timeframe)
    if ms_data:
        bias = ms_data.get("market_structure_bias", "").lower()
        if "bearish" in bias:
            result.market_structure = "bearish"
        elif "bullish" in bias:
            result.market_structure = "bullish"
        else:
            result.market_structure = "ranging"

    # EMAs
    ema_data = _compute_emas(ohlcv)
    result.ema_alignment = ema_data.get("dual_ema", {}).get("alignment", "unknown")
    result.ema_200_distance_pct = ema_data.get("ema_200_distance_pct", 0.0)

    # RSI
    result.rsi_14 = _compute_rsi(ohlcv, 14)

    # Funding rate
    result.funding_rate = _compute_funding_rate(symbol)

    # Patterns
    patterns = _compute_patterns(ohlcv)
    result.pattern_count = len(patterns)
    for p in patterns:
        p_type = p.get("pattern_type", "").lower() if isinstance(p, dict) else ""
        if "bearish" in p_type:
            result.bearish_patterns += 1
        elif "bullish" in p_type:
            result.bullish_patterns += 1

    # Support/Resistance
    sr_data = _compute_sr_levels(ohlcv_dicts, current_price)
    result.near_support = sr_data.get("near_support", False)
    result.near_resistance = sr_data.get("near_resistance", False)
    supports = sr_data.get("supports", [])
    resistances = sr_data.get("resistances", [])
    result.sr_levels_count = len(supports) + len(resistances)

    # Volume profile
    vp_data = _compute_volume_profile(ohlcv_dicts, current_price)
    result.volume_profile_zone = vp_data.get("zone_classification", "unknown")

    # Relative Volume Analysis
    vol_data = _compute_volume_analysis(ohlcv)
    result.volume_ratio = vol_data.get("volume_ratio", 1.0)

    # TVEM
    tvem_data = _compute_tvem(ohlcv)
    result.tvem_signal = tvem_data.get("signal", "NEUTRAL")

    # Step 3: Derive ta_bias and ta_confidence
    _compute_bias_and_confidence(result)

    logger.info(
        f"Discovery TA for {symbol}: bias={result.ta_bias}, "
        f"confidence={result.ta_confidence:.2f}, structure={result.market_structure}, "
        f"ema={result.ema_alignment}, rsi={result.rsi_14:.1f}"
    )

    return result


def _compute_bias_and_confidence(result: DiscoveryTAResult) -> None:
    """
    Derive ta_bias and ta_confidence from individual TA components.

    Counts bearish vs bullish signals across all factors.
    ta_confidence = aligned_count / total_factors (0.0-1.0).
    """
    bearish_signals = 0
    bullish_signals = 0
    total_factors = 0

    # Market structure
    if result.market_structure in ("bearish", "bullish"):
        total_factors += 1
        if result.market_structure == "bearish":
            bearish_signals += 1
        else:
            bullish_signals += 1

    # EMA alignment
    if result.ema_alignment in ("bearish", "bullish"):
        total_factors += 1
        if result.ema_alignment == "bearish":
            bearish_signals += 1
        else:
            bullish_signals += 1

    # RSI
    if result.rsi_14 < 30 or result.rsi_14 > 70:
        total_factors += 1
        if result.rsi_14 > 70:
            bearish_signals += 1  # Overbought = bearish
        else:
            bullish_signals += 1  # Oversold = bullish

    # Patterns
    if result.bearish_patterns > 0 or result.bullish_patterns > 0:
        total_factors += 1
        if result.bearish_patterns > result.bullish_patterns:
            bearish_signals += 1
        elif result.bullish_patterns > result.bearish_patterns:
            bullish_signals += 1

    # TVEM signal
    if result.tvem_signal in ("BULLISH", "BEARISH"):
        total_factors += 1
        if result.tvem_signal == "BEARISH":
            bearish_signals += 1
        else:
            bullish_signals += 1

    # Volume profile zone
    zone = result.volume_profile_zone.upper()
    if "BEARISH" in zone or "BULLISH" in zone:
        total_factors += 1
        if "BEARISH" in zone:
            bearish_signals += 1
        else:
            bullish_signals += 1

    # Volume Ratio (Momentum Confirmation)
    if result.volume_ratio >= 2.0:
        if result.market_structure in ("bearish", "bullish"):
            total_factors += 1
            if result.market_structure == "bearish":
                bearish_signals += 1
            else:
                bullish_signals += 1

    # Derive bias
    if total_factors == 0:
        result.ta_bias = "NEUTRAL"
        result.ta_confidence = 0.0
        return

    if bearish_signals > bullish_signals:
        result.ta_bias = "SHORT_ALIGNED"
        aligned = bearish_signals
    elif bullish_signals > bearish_signals:
        result.ta_bias = "LONG_ALIGNED"
        aligned = bullish_signals
    else:
        result.ta_bias = "NEUTRAL"
        aligned = 0

    result.ta_confidence = aligned / total_factors if total_factors > 0 else 0.0
