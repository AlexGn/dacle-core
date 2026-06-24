"""
Discovery TA - Lightweight technical analysis for the discovery pipeline.

Session 382: Part of Unified High-Conviction Token Discovery Pipeline.
Provides market context enrichment for discovered tokens WITHOUT requiring
entry/SL/TP levels (David draws those manually).

Reuses existing _compute_* modules from computed_ta_builder.py.

Usage:
    from dacle_core.ta.discovery_ta import run_discovery_ta
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

    # SMC Details (Phase 11 Integration)
    choch_direction: Optional[str] = None  # bullish / bearish
    bos_direction: Optional[str] = None    # bullish / bearish
    smc_fakeout_risk: bool = False
    smc_structural_confirmation: bool = False
    obi_confirmed_sweep: bool = False      # Session 460: OBI alignment on recent sweep
    fvg_proximity_pct: Optional[float] = None
    rsi_divergence: Optional[str] = None  # bullish / bearish / none

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
    volume_profile_zone: str = "unknown"

    # Volume
    volume_ratio: float = 1.0  # recent volume / avg volume (RVOL)
    cvd_z_score: float = 0.0   # CVD spike magnitude (Vector Candle quality)
    body_ratio: float = 0.0    # Candle body / candle range (Session 460)
    atr_bps: Optional[float] = None # v1.5.1 volatility metric

    # Macro Context (Session 460)
    usdt_d_value: Optional[float] = None
    usdt_d_signal: str = "NEUTRAL" # BULLISH_FOR_ALTS, BEARISH_FOR_ALTS, NEUTRAL

    # Choppiness
    choppiness_index: float = 50.0 # 0-100, < 35 trending, > 61 choppy

    # TVEM signal
    tvem_signal: str = "NEUTRAL"  # BULLISH, BEARISH, NEUTRAL

    # Cipher context
    cipher_signal: str = "UNAVAILABLE"
    cipher_confidence: float = 0.0
    cipher_timeframe: str = "4h"

    # Derived fields
    ta_bias: str = "NEUTRAL"  # SHORT_ALIGNED / LONG_ALIGNED / NEUTRAL
    ta_confidence: float = 0.0  # 0.0-1.0 based on # of aligned factors

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        return {
            "status": self.status,
            "error": self.error,
            "market_structure": self.market_structure,
            "choch_direction": self.choch_direction,
            "bos_direction": self.bos_direction,
            "rsi_divergence": self.rsi_divergence,
            "smc_fakeout_risk": self.smc_fakeout_risk,
            "smc_structural_confirmation": self.smc_structural_confirmation,
            "obi_confirmed_sweep": self.obi_confirmed_sweep,
            "fvg_proximity_pct": self.fvg_proximity_pct,
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
            "cvd_z_score": round(self.cvd_z_score, 2),
            "body_ratio": round(self.body_ratio, 2),
            "choppiness_index": round(self.choppiness_index, 1),
            "usdt_d_value": round(self.usdt_d_value, 2) if self.usdt_d_value is not None else None,
            "usdt_d_signal": self.usdt_d_signal,
            "atr_bps": round(self.atr_bps, 2) if self.atr_bps is not None else None,
            "volume_profile_zone": self.volume_profile_zone,
            "tvem_signal": self.tvem_signal,
            "cipher_signal": self.cipher_signal,
            "cipher_confidence": round(self.cipher_confidence, 2),
            "cipher_timeframe": self.cipher_timeframe,
            "ta_bias": self.ta_bias,
            "ta_confidence": round(self.ta_confidence, 2),
        }


def run_discovery_ta(token_symbol: str, timeframe: str = "4h", obi: float = None) -> DiscoveryTAResult:
    """
    Run lightweight TA for a discovered token.

    Fetches OHLCV data from Binance and runs all available analysis modules.
    Does NOT require entry/SL/TP levels -- pure market context.

    Args:
        token_symbol: Token symbol (e.g., "BTC", "MONAD")
        timeframe: OHLCV timeframe (default "4h")
        obi: Optional Order Book Imbalance (-1.0 to +1.0) (Session 460)

    Returns:
        DiscoveryTAResult with market context data
    """
    result = DiscoveryTAResult()

    # Import compute functions from computed_ta_builder
    try:
        from dacle_core.ta.computed_ta_builder import (
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
    # 2a. Macro Context (Session 460)
    try:
        from dacle_core.data.indices_tracker import IndicesTracker
        tracker = IndicesTracker(use_cache=True)
        macro_data = tracker.fetch_all_indices()
        usdt_d = macro_data.get("indices", {}).get("usdt_d", {})
        result.usdt_d_value = usdt_d.get("value")
        result.usdt_d_signal = usdt_d.get("signal", "NEUTRAL")
    except Exception as e:
        logger.warning(f"Macro indices fetch failed: {e}")

    # 2b. Market structure (Refined SMC)
    ms_data = _compute_market_structure(symbol, timeframe, ohlcv=ohlcv, obi=obi)
    if ms_data:
        # Fix contract bug: Analyzer returns 'current_structure' (Step 1 of checklist)
        result.market_structure = ms_data.get("current_structure", "ranging")
        
        # Extract SMC details (Step 2 of checklist)
        choch = ms_data.get("choch_details")
        if choch:
            result.choch_direction = choch.get("direction")
            
        bos = ms_data.get("bos_details")
        if bos:
            result.bos_direction = bos.get("direction")
            
        # Session 460: OBI confirmed sweep
        sweeps = ms_data.get("liquidity_sweeps", [])
        if sweeps:
            # Check if ANY recent sweep is OBI confirmed
            result.obi_confirmed_sweep = any(s.get("obi_confirmed") for s in sweeps[:3])

        # FVG magnet strength
        fvg = ms_data.get("nearest_bearish_fvg") or ms_data.get("nearest_bullish_fvg")
        if fvg:
            fvg_price = fvg.get("midpoint")
            if fvg_price and current_price > 0:
                result.fvg_proximity_pct = abs(current_price - fvg_price) / current_price * 100

    # EMAs
    ema_data = _compute_emas(ohlcv)
    result.ema_alignment = ema_data.get("dual_ema", {}).get("alignment", "unknown")
    result.ema_200_distance_pct = ema_data.get("ema_200_distance_pct", 0.0)

    # RSI
    result.rsi_14 = _compute_rsi(ohlcv, 14)
    result.rsi_divergence = _detect_rsi_divergence(ohlcv)

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
    
    # Session 460: CVD Z-Score (Vector Candle / CVD Spike)
    try:
        from dacle_core.ta.indicators.cvd import calculate_cvd
        cvd_res = calculate_cvd(ohlcv_dicts)
        result.cvd_z_score = cvd_res.get("cvd_z_score", 0.0)
        result.body_ratio = cvd_res.get("body_ratio", 0.0)
    except Exception as e:
        logger.warning(f"CVD calculation failed: {e}")

    # Session 460: Choppiness Index
    try:
        from dacle_core.ta.indicators.choppiness import calculate_choppiness
        result.choppiness_index = calculate_choppiness(ohlcv)
    except Exception as e:
        logger.warning(f"Choppiness calculation failed: {e}")
    
    # Session 454 (v1.5.1): ATR calculation for volatility telemetry
    try:
        result.atr_bps = _calculate_atr_bps(ohlcv)
    except Exception as e:
        logger.warning(f"ATR calculation failed: {e}")

    # TVEM
    tvem_data = _compute_tvem(ohlcv)
    result.tvem_signal = tvem_data.get("signal", "NEUTRAL")

    # Cipher snapshot
    try:
        from dacle_core.ta.cipher_engine import compute_cipher_snapshot

        cipher = compute_cipher_snapshot(
            symbol,
            timeframe.upper(),
            opens=[float(row[1]) for row in ohlcv],
            highs=[float(row[2]) for row in ohlcv],
            lows=[float(row[3]) for row in ohlcv],
            closes=[float(row[4]) for row in ohlcv],
            volumes=[float(row[5]) for row in ohlcv],
            timestamps=[str(row[0]) for row in ohlcv],
        )
        result.cipher_signal = cipher.signal
        result.cipher_confidence = cipher.confidence
        result.cipher_timeframe = timeframe
    except Exception as e:
        logger.warning(f"Cipher snapshot failed for {symbol}: {e}")

    # Step 3: Derive ta_bias and ta_confidence (SMC-Hardened)
    _compute_bias_and_confidence(result)

    # Step 4: Final SMC Status Flags (Step 2 of checklist)
    # Determine if structure confirms or contradicts the technical bias
    if result.ta_bias == "LONG_ALIGNED":
        result.smc_structural_confirmation = (result.bos_direction == "bullish")
        result.smc_fakeout_risk = (result.choch_direction == "bearish")
    elif result.ta_bias == "SHORT_ALIGNED":
        result.smc_structural_confirmation = (result.bos_direction == "bearish")
        result.smc_fakeout_risk = (result.choch_direction == "bullish")

    logger.info(
        f"Discovery TA for {symbol}: bias={result.ta_bias}, "
        f"conf={result.ta_confidence:.2f}, struct={result.market_structure}, "
        f"BOS={result.bos_direction}, CHoCH={result.choch_direction}, "
        f"fakeout={result.smc_fakeout_risk}"
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

    # 1. Market structure (High Weight)
    if result.market_structure in ("bearish", "bullish"):
        total_factors += 1
        if result.market_structure == "bearish":
            bearish_signals += 1
        else:
            bullish_signals += 1

    # 2. SMC - BOS (Highest Weight Confirmation)
    if result.bos_direction in ("bullish", "bearish"):
        total_factors += 1
        if result.bos_direction == "bearish":
            bearish_signals += 1
        else:
            bullish_signals += 1

    # 3. EMA alignment
    if result.ema_alignment in ("bearish", "bullish"):
        total_factors += 1
        if result.ema_alignment == "bearish":
            bearish_signals += 1
        else:
            bullish_signals += 1

    # 4. RSI
    if result.rsi_14 < 30 or result.rsi_14 > 70:
        total_factors += 1
        if result.rsi_14 > 70:
            bearish_signals += 1  # Overbought = bearish
        else:
            bullish_signals += 1  # Oversold = bullish

    # 5. Patterns
    if result.bearish_patterns > 0 or result.bullish_patterns > 0:
        total_factors += 1
        if result.bearish_patterns > result.bullish_patterns:
            bearish_signals += 1
        elif result.bullish_patterns > result.bearish_patterns:
            bullish_signals += 1

    # 6. TVEM signal
    if result.tvem_signal in ("BULLISH", "BEARISH"):
        total_factors += 1
        if result.tvem_signal == "BEARISH":
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

    # Institutional Fakeout Logic: Force low confidence if CHoCH opposes the bias
    if result.ta_bias == "LONG_ALIGNED" and result.choch_direction == "bearish":
        result.ta_confidence = min(result.ta_confidence, 0.35)
    elif result.ta_bias == "SHORT_ALIGNED" and result.choch_direction == "bullish":
        result.ta_confidence = min(result.ta_confidence, 0.35)


def _detect_rsi_divergence(ohlcv: list[list]) -> Optional[str]:
    """
    Detect RSI divergence between the last 2 peaks/troughs.
    Returns: 'bullish', 'bearish', or None
    """
    try:
        if not ohlcv or len(ohlcv) < 20:
            return None

        # 1. Calculate RSI for the series
        import numpy as np
        closes = np.array([c[4] for c in ohlcv])
        highs = np.array([c[2] for c in ohlcv])
        lows = np.array([c[3] for c in ohlcv])

        # Simple RSI calc
        def get_rsi_series(data, window=14):
            diff = np.diff(data)
            gain = np.where(diff > 0, diff, 0)
            loss = np.where(diff < 0, -diff, 0)
            avg_gain = np.convolve(gain, np.ones(window) / window, mode="valid")
            avg_loss = np.convolve(loss, np.ones(window) / window, mode="valid")
            # Avoid div zero
            rs = np.divide(
                avg_gain, avg_loss, out=np.zeros_like(avg_gain), where=avg_loss != 0
            )
            return 100 - (100 / (1 + rs))

        rsi_series = get_rsi_series(closes)
        if len(rsi_series) < 5:
            return None

        # 2. Find local peaks in Price (within last 30 candles)
        # We look for the last 2 local highs
        price_peaks = []
        for i in range(len(highs) - 2, 5, -1):
            if highs[i] > highs[i - 1] and highs[i] > highs[i + 1]:
                price_peaks.append(i)
                if len(price_peaks) >= 2:
                    break

        if len(price_peaks) >= 2:
            p2, p1 = price_peaks[0], price_peaks[1]  # p2 is most recent
            # Shift RSI index (offset by window-1)
            offset = 13
            r2_idx, r1_idx = p2 - offset, p1 - offset

            if r2_idx < len(rsi_series) and r1_idx < len(rsi_series):
                # BEARISH DIVERGENCE: Price Higher High, RSI Lower High
                if highs[p2] > highs[p1] and rsi_series[r2_idx] < rsi_series[r1_idx]:
                    return "bearish"

        # 3. Find local troughs in Price
        price_troughs = []
        for i in range(len(lows) - 2, 5, -1):
            if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
                price_troughs.append(i)
                if len(price_troughs) >= 2:
                    break

        if len(price_troughs) >= 2:
            t2, t1 = price_troughs[0], price_troughs[1]
            offset = 13
            r2_idx, r1_idx = t2 - offset, t1 - offset

            if r2_idx < len(rsi_series) and r1_idx < len(rsi_series):
                # BULLISH DIVERGENCE: Price Lower Low, RSI Higher Low
                if lows[t2] < lows[t1] and rsi_series[r2_idx] > rsi_series[r1_idx]:
                    return "bullish"

    except Exception as e:
        logger.warning(f"Divergence detection failed: {e}")

    return None

def _calculate_atr_bps(ohlcv: list[list], period: int = 14) -> Optional[float]:
    """
    Calculate Average True Range (ATR) normalized to basis points (bps).
    1 bps = 0.01%
    """
    if not ohlcv or len(ohlcv) < period + 1:
        return None
        
    try:
        true_ranges = []
        for i in range(1, len(ohlcv)):
            high = ohlcv[i][2]
            low = ohlcv[i][3]
            prev_close = ohlcv[i-1][4]
            
            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close)
            )
            true_ranges.append(tr)
            
        if len(true_ranges) < period:
            return None
            
        atr_abs = sum(true_ranges[-period:]) / period
        current_price = ohlcv[-1][4]
        
        if current_price <= 0:
            return None
            
        # Convert to bps: (ATR / Price) * 10000
        atr_bps = (atr_abs / current_price) * 10000
        return float(atr_bps)
    except Exception:
        return None
