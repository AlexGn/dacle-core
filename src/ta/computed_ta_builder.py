"""
Computed TA Builder - Core orchestrator for real-time technical analysis.

Replaces screenshot-based GPT Vision extraction with computed analysis from
real Binance OHLCV data. Produces a TAExtractionResult compatible with the
existing TATransformer -> scorer -> persistence pipeline.

David's workflow:
    1. Draw Entry/SL/TP on TradingView
    2. Set alert (webhook fires)
    3. This module fetches real data, runs all analysis, produces TAExtractionResult
    4. Existing pipeline scores and saves it

Uses existing analysis modules:
    - market_structure.py    -> CHoCH, BOS, swing points, FVGs
    - technical_patterns.py  -> 14 candlestick patterns
    - confluence_counter.py  -> 13 confluence types
    - support_resistance_detector.py -> S/R levels
    - exhaustion_calculator.py -> RSI, CVD, volume
    - volume_profile.py      -> POC, VAH, VAL
    - price_action_analyzer.py -> Fibonacci, EMA, TVEM

Session 353: Initial implementation (feature/computed-ta branch)
"""
import logging
import os
import hashlib
import time
from datetime import datetime, timezone
from typing import Optional

import ccxt

from api.routers.ta.models import TAExtractionResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OHLCV data fetching
# ---------------------------------------------------------------------------

def _fetch_ohlcv_binance(
    symbol: str,
    timeframe: str = "4h",
    limit: int = 200,
) -> list[list]:
    """
    Fetch OHLCV candles via CCXT.

    Tries perpetual first (what David trades), then spot.
    Returns list of [timestamp, open, high, low, close, volume].
    """
    exchange = ccxt.binance({"enableRateLimit": True, "timeout": 15000})

    # Binance requires lowercase intervals (e.g. "4h" not "4H")
    timeframe = timeframe.lower()

    pairs = [
        f"{symbol}/USDT:USDT",  # Perpetual (priority for shorts)
        f"{symbol}/USDT",       # Spot
    ]

    for pair in pairs:
        try:
            ohlcv = exchange.fetch_ohlcv(pair, timeframe, limit=limit)
            if ohlcv and len(ohlcv) > 0:
                logger.info(
                    f"Fetched {len(ohlcv)} {timeframe} candles for {pair}"
                )
                return ohlcv
        except Exception as e:
            logger.warning(f"{pair} not available: {e}")
            continue

    logger.warning(f"No Binance OHLCV data found for {symbol}")
    return []


def _fetch_ohlcv_blofin(
    symbol: str,
    timeframe: str = "4h",
    limit: int = 200,
) -> list[list]:
    """Fetch OHLCV from Blofin public data."""
    try:
        from src.data.fetchers.blofin_fetcher import BlofinFetcher
    except ImportError:
        logger.debug("BlofinFetcher not available — skipping Blofin OHLCV fallback")
        return []

    try:
        blofin = BlofinFetcher()
        ohlcv = blofin.fetch_ohlcv(symbol, timeframe, limit)
        if ohlcv and len(ohlcv) > 0:
            return ohlcv
    except Exception as e:
        logger.warning(f"Blofin OHLCV fetch failed for {symbol}: {e}")
    return []


# Backward compatibility alias
_fetch_ohlcv = _fetch_ohlcv_binance


def _timeframe_to_ms(timeframe: str) -> int:
    """Convert CCXT timeframe to milliseconds."""
    tf = (timeframe or "4h").strip().lower()
    mapping = {
        "1m": 60_000,
        "3m": 180_000,
        "5m": 300_000,
        "15m": 900_000,
        "30m": 1_800_000,
        "1h": 3_600_000,
        "2h": 7_200_000,
        "4h": 14_400_000,
        "6h": 21_600_000,
        "8h": 28_800_000,
        "12h": 43_200_000,
        "1d": 86_400_000,
    }
    return mapping.get(tf, 14_400_000)


def _get_quality_staleness_multiplier() -> float:
    """Read staleness multiplier from env with safe default."""
    raw = os.getenv("QUICK_TA_MAX_CANDLE_STALENESS_MULTIPLIER", "2")
    try:
        value = float(raw)
        return value if value > 0 else 2.0
    except (TypeError, ValueError):
        return 2.0


def _collect_ohlcv_quality_flags(ohlcv: list[list], timeframe: str) -> list[str]:
    """
    Return quality flags for OHLCV shape/timestamps/sanity.
    Empty list means quality checks passed.
    """
    flags: list[str] = []
    if not ohlcv:
        return ["empty_ohlcv"]

    if len(ohlcv) < 20:
        flags.append("insufficient_candles")

    prev_ts = None
    expected_interval = _timeframe_to_ms(timeframe)
    for row in ohlcv:
        if not isinstance(row, list) or len(row) < 6:
            flags.append("invalid_candle_shape")
            break
        ts, o, h, l, c = row[:5]
        if prev_ts is not None and ts <= prev_ts:
            flags.append("non_monotonic_timestamps")
            break
        prev_ts = ts
        try:
            if not (float(l) <= float(o) <= float(h) and float(l) <= float(c) <= float(h)):
                flags.append("ohlc_sanity_failed")
                break
        except (TypeError, ValueError):
            flags.append("ohlc_parse_failed")
            break

    if len(ohlcv) >= 2:
        diffs = [ohlcv[i][0] - ohlcv[i - 1][0] for i in range(1, len(ohlcv))]
        # Allow modest jitter from exchange APIs
        if any(abs(diff - expected_interval) > (expected_interval * 0.25) for diff in diffs[-10:]):
            flags.append("interval_mismatch")

    last_ts = ohlcv[-1][0]
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    stale_threshold = int(expected_interval * _get_quality_staleness_multiplier())
    if now_ms - last_ts > stale_threshold:
        flags.append("stale_last_candle")

    return flags


def _should_use_blofin_first(symbol: str) -> bool:
    """
    Determine if this request should use Blofin as primary.
    Uses policy + deterministic symbol-level canary percentage.
    """
    policy = os.getenv("QUICK_TA_SOURCE_POLICY", "binance_first").strip().lower()
    if policy != "blofin_first":
        return False

    canary_raw = os.getenv("QUICK_TA_BLOFIN_CANARY_PCT", "0")
    try:
        canary_pct = max(0, min(100, int(canary_raw)))
    except (TypeError, ValueError):
        canary_pct = 0

    if canary_pct >= 100:
        return True
    if canary_pct <= 0:
        return False

    bucket = int(hashlib.sha1(symbol.upper().encode("utf-8")).hexdigest()[:8], 16) % 100
    return bucket < canary_pct


def select_ohlcv_source(
    symbol: str,
    timeframe: str = "4h",
    limit: int = 200,
) -> tuple[list[list], str, Optional[str], list[str]]:
    """
    Select OHLCV source with quality-gated fallback.

    Returns:
        (ohlcv, source, fallback_reason, quality_flags)
    """
    use_blofin_first = _should_use_blofin_first(symbol)
    if not use_blofin_first:
        # Backward-compatible behavior: Binance primary, Blofin fallback.
        # Keep this permissive to avoid changing legacy computed TA behavior.
        started = time.perf_counter()
        binance_candles = _fetch_ohlcv_binance(symbol, timeframe, limit)
        primary_ms = round((time.perf_counter() - started) * 1000)
        if binance_candles:
            logger.info(
                f"quick_ta_ohlcv_source symbol={symbol.upper()} timeframe={timeframe} "
                f"source=binance fallback=false candles={len(binance_candles)} latency_ms={primary_ms}"
            )
            return binance_candles, "binance", None, []

        started = time.perf_counter()
        blofin_candles = _fetch_ohlcv_blofin(symbol, timeframe, limit)
        secondary_ms = round((time.perf_counter() - started) * 1000)
        if blofin_candles:
            reason = "binance_fetch_failed"
            logger.warning(
                f"quick_ta_ohlcv_source symbol={symbol.upper()} timeframe={timeframe} "
                f"source=blofin fallback=true fallback_reason={reason} "
                f"candles={len(blofin_candles)} latency_ms={secondary_ms}"
            )
            return blofin_candles, "blofin", reason, []

        logger.warning(
            f"quick_ta_ohlcv_source symbol={symbol.upper()} timeframe={timeframe} "
            f"source=none fallback=true fallback_reason=binance_fetch_failed quality_flags=empty_ohlcv"
        )
        return [], "none", "binance_fetch_failed", ["empty_ohlcv"]

    primary_name = "blofin" if use_blofin_first else "binance"
    secondary_name = "binance" if use_blofin_first else "blofin"

    fetchers = {
        "binance": _fetch_ohlcv_binance,
        "blofin": _fetch_ohlcv_blofin,
    }

    def _try_source(name: str) -> tuple[list[list], list[str], int]:
        started = time.perf_counter()
        candles = fetchers[name](symbol, timeframe, limit)
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        return candles, _collect_ohlcv_quality_flags(candles, timeframe), elapsed_ms

    primary_candles, primary_flags, primary_ms = _try_source(primary_name)
    if primary_candles and not primary_flags:
        logger.info(
            f"quick_ta_ohlcv_source symbol={symbol.upper()} timeframe={timeframe} "
            f"source={primary_name} fallback=false candles={len(primary_candles)} latency_ms={primary_ms}"
        )
        return primary_candles, primary_name, None, []

    fallback_reason = (
        f"{primary_name}_quality_failed:{','.join(primary_flags)}"
        if primary_flags else
        f"{primary_name}_fetch_failed"
    )

    secondary_candles, secondary_flags, secondary_ms = _try_source(secondary_name)
    if secondary_candles and not secondary_flags:
        logger.warning(
            f"quick_ta_ohlcv_source symbol={symbol.upper()} timeframe={timeframe} "
            f"source={secondary_name} fallback=true fallback_reason={fallback_reason} "
            f"candles={len(secondary_candles)} latency_ms={secondary_ms}"
        )
        return secondary_candles, secondary_name, fallback_reason, secondary_flags

    combined_flags = primary_flags + secondary_flags
    logger.warning(
        f"quick_ta_ohlcv_source symbol={symbol.upper()} timeframe={timeframe} "
        f"source=none fallback=true fallback_reason={fallback_reason} "
        f"quality_flags={','.join(combined_flags) if combined_flags else 'none'}"
    )
    return [], "none", fallback_reason, combined_flags


def _ohlcv_to_dicts(ohlcv: list[list]) -> list[dict]:
    """Convert CCXT list format to dict format used by some modules."""
    return [
        {
            "timestamp": row[0],
            "open": row[1],
            "high": row[2],
            "low": row[3],
            "close": row[4],
            "volume": row[5],
        }
        for row in ohlcv
    ]


# ---------------------------------------------------------------------------
# Individual analysis steps
# ---------------------------------------------------------------------------

def _compute_market_structure(symbol: str, timeframe: str) -> dict:
    """Run MarketStructureAnalyzer for CHoCH/BOS/swing points."""
    try:
        from src.analysis.market_structure import MarketStructureAnalyzer

        analyzer = MarketStructureAnalyzer()
        result = analyzer.analyze(symbol, timeframe=timeframe)
        return result
    except Exception as e:
        logger.warning(f"Market structure analysis failed: {e}")
        return {}


def _compute_patterns(ohlcv: list[list]) -> list[dict]:
    """Detect candlestick patterns from OHLCV data."""
    try:
        from src.analysis.technical_patterns import CandlestickDetector

        detector = CandlestickDetector()
        patterns = detector.detect_patterns(ohlcv, lookback=20)
        return [p.to_dict() for p in patterns]
    except Exception as e:
        logger.warning(f"Pattern detection failed: {e}")
        return []


def _compute_sr_levels(
    ohlcv_dicts: list[dict],
    current_price: float,
) -> dict:
    """Detect support/resistance levels."""
    try:
        from src.analysis.support_resistance_detector import (
            SupportResistanceDetector,
        )

        detector = SupportResistanceDetector()
        supports = detector.detect_support_levels(ohlcv_dicts, current_price)
        resistances = detector.detect_resistance_levels(
            ohlcv_dicts, current_price
        )
        return {
            "supports": supports,
            "resistances": resistances,
            "near_support": any(
                abs(s["price"] - current_price) / current_price < 0.02
                for s in supports
            )
            if supports
            else False,
            "near_resistance": any(
                abs(r["price"] - current_price) / current_price < 0.02
                for r in resistances
            )
            if resistances
            else False,
        }
    except Exception as e:
        logger.warning(f"S/R detection failed: {e}")
        return {"supports": [], "resistances": [], "near_support": False, "near_resistance": False}


def _compute_volume_profile(ohlcv_dicts: list[dict], current_price: float = 0) -> dict:
    """Calculate volume profile with full MP-VWAP quarterly analysis.

    Calls calculate_mp_vwap() for POC/VAH/VAL + quarterly VWAP, zone
    classification, and signal.  Falls back to basic volume profile
    if MP-VWAP fails.
    """
    try:
        from src.analysis.volume_profile import VolumeProfileAnalyzer

        analyzer = VolumeProfileAnalyzer()

        # Try full MP-VWAP first (includes POC/VAH/VAL + QVWAP + zone + signal)
        if current_price > 0:
            try:
                result = analyzer.calculate_mp_vwap(ohlcv_dicts, current_price, "quarterly")
                return result
            except Exception as e:
                logger.debug(f"MP-VWAP failed, falling back to basic volume profile: {e}")

        # Fallback to basic volume profile
        return analyzer.calculate_volume_profile(ohlcv_dicts)
    except Exception as e:
        logger.warning(f"Volume profile failed: {e}")
        return {}


def _compute_rsi(ohlcv: list[list], period: int = 14) -> float:
    """Calculate RSI from OHLCV data."""
    try:
        from src.analysis.exhaustion_calculator import calculate_rsi

        closes = [candle[4] for candle in ohlcv]
        return calculate_rsi(closes, period)
    except Exception as e:
        logger.warning(f"RSI calculation failed: {e}")
        return 50.0


def _compute_emas(ohlcv: list[list]) -> dict:
    """Calculate key EMAs (12, 24, 200) and classify alignment."""
    try:
        from src.analysis.price_action_analyzer import PriceActionAnalyzer

        analyzer = PriceActionAnalyzer(exchange_id="binance")
        closes = [candle[4] for candle in ohlcv]

        ema_12 = analyzer._calculate_ema(closes, 12)
        ema_24 = analyzer._calculate_ema(closes, 24)
        ema_200 = analyzer._calculate_ema(closes, 200)

        current_price = closes[-1]
        ema_12_val = ema_12[-1] if ema_12 else current_price
        ema_24_val = ema_24[-1] if ema_24 else current_price
        ema_200_val = ema_200[-1] if ema_200 else current_price

        # Classify EMA alignment
        if ema_12_val < ema_24_val < ema_200_val:
            alignment = "bearish"
        elif ema_12_val > ema_24_val > ema_200_val:
            alignment = "bullish"
        else:
            alignment = "choppy"

        # Position vs 200 EMA
        if current_price < ema_200_val:
            position = "below"
        elif current_price > ema_200_val:
            position = "above"
        else:
            position = "at"

        return {
            "ema_12": ema_12_val,
            "ema_24": ema_24_val,
            "ema_200": ema_200_val,
            "current_price": current_price,
            "dual_ema": {"alignment": alignment},
            "mtf_ema_200": {"position_vs_ema": position},
            "ema_200_distance_pct": (
                (current_price - ema_200_val) / ema_200_val * 100
                if ema_200_val
                else 0
            ),
        }
    except Exception as e:
        logger.warning(f"EMA calculation failed: {e}")
        return {
            "dual_ema": {"alignment": "unknown"},
            "mtf_ema_200": {"position_vs_ema": "unknown"},
        }


def _compute_funding_rate(symbol: str) -> Optional[float]:
    """Fetch current funding rate from Binance."""
    try:
        from src.analysis.exhaustion_calculator import fetch_funding_rate

        return fetch_funding_rate(symbol)
    except Exception as e:
        logger.debug(f"Funding rate fetch failed: {e}")
        return None


def _compute_chart_patterns(ohlcv: list[list], sr_levels: dict) -> list[dict]:
    """Detect chart patterns (H&S, Double Top/Bottom, Deviation).

    Uses PatternRecognitionEngine to find structural reversal patterns
    and wraps them in the pipeline-compatible format expected by
    _build_confluences_for_pipeline (pattern_type + strength).

    Session 358: Close Quick TA pattern gaps.
    """
    try:
        from src.analysis.price_action_analyzer import PatternRecognitionEngine

        engine = PatternRecognitionEngine()
        results: list[dict] = []

        # Normal H&S (bearish)
        hs = engine.detect_normal_head_and_shoulders(ohlcv)
        if hs:
            results.append({
                "pattern_name": f"Head & Shoulders (neckline {hs['neckline']:.4f}, target {hs['target']:.4f})",
                "pattern_type": "bearish_reversal",
                "strength": "STRONG" if hs["confidence"] >= 0.7 else "MODERATE",
                "confidence": hs["confidence"],
                "raw": hs,
            })

        # Inverse H&S (bullish)
        inv_hs = engine.detect_inverse_head_and_shoulders(ohlcv)
        if inv_hs:
            results.append({
                "pattern_name": f"Inverse H&S (neckline {inv_hs['neckline']:.4f}, target {inv_hs['target']:.4f})",
                "pattern_type": "bullish_reversal",
                "strength": "STRONG" if inv_hs["confidence"] >= 0.7 else "MODERATE",
                "confidence": inv_hs["confidence"],
                "raw": inv_hs,
            })

        # Double Top (bearish)
        dt = engine.detect_double_top(ohlcv)
        if dt:
            results.append({
                "pattern_name": f"Double Top (valley {dt['valley']:.4f}, target {dt['target']:.4f})",
                "pattern_type": "bearish_reversal",
                "strength": "STRONG" if dt["confidence"] >= 0.7 else "MODERATE",
                "confidence": dt["confidence"],
                "raw": dt,
            })

        # Double Bottom (bullish)
        db = engine.detect_double_bottom(ohlcv)
        if db:
            results.append({
                "pattern_name": f"Double Bottom (peak {db['peak']:.4f}, target {db['target']:.4f})",
                "pattern_type": "bullish_reversal",
                "strength": "STRONG" if db["confidence"] >= 0.7 else "MODERATE",
                "confidence": db["confidence"],
                "raw": db,
            })

        # Deviation/Fakeout
        dev = engine.detect_deviation_fakeout(ohlcv, sr_levels)
        if dev:
            ptype = "bearish_reversal" if dev["direction"] == "bearish" else "bullish_reversal"
            results.append({
                "pattern_name": dev["description"],
                "pattern_type": ptype,
                "strength": "STRONG" if dev["confidence"] >= 0.7 else "MODERATE",
                "confidence": dev["confidence"],
                "raw": dev,
            })

        return results
    except Exception as e:
        logger.warning(f"Chart pattern detection failed: {e}")
        return []


def _compute_confluence(
    ema_data: dict,
    vwap_data: dict,
    sr_levels: dict,
    pattern_names: list[str],
    volume_data: dict,
    funding_rate: Optional[float],
    ohlcv: list[list],
    timeframe: str,
    direction: str,
) -> dict:
    """Run ConfluenceCounter with all computed data."""
    try:
        from src.analysis.confluence_counter import ConfluenceCounter

        counter = ConfluenceCounter()
        result = counter.count_confluence(
            ema_data=ema_data,
            vwap_data=vwap_data,
            sr_levels=sr_levels,
            patterns=pattern_names,
            volume_data=volume_data,
            funding_rate=funding_rate,
            ohlcv_data=ohlcv,
            timeframe=timeframe,
            direction=direction,
        )
        return {
            "score": result.score,
            "rating": result.rating,
            "factors": [f.value for f in result.factors],
            "factor_descriptions": result.factor_descriptions,
            "conviction_modifier": result.conviction_modifier,
        }
    except Exception as e:
        logger.warning(f"Confluence counting failed: {e}")
        return {"score": 0, "rating": "NONE", "factors": [], "factor_descriptions": []}


def _compute_fibonacci(ohlcv: list[list], current_price: float) -> dict:
    """Calculate Fibonacci levels."""
    try:
        from src.analysis.price_action_analyzer import PriceActionAnalyzer

        analyzer = PriceActionAnalyzer(exchange_id="binance")
        return analyzer._calculate_fibonacci(ohlcv, lookback=50, current_price=current_price)
    except Exception as e:
        logger.warning(f"Fibonacci calculation failed: {e}")
        return {}


def _compute_tvem(ohlcv: list[list], ema_period: int = 12, std_multiplier: float = 2.0) -> dict:
    """Calculate TVEM Bands (L058) from OHLCV data.

    TVEM = Average of (Trailing VWAP + EMA).
    Upper/Lower bands = TVEM ± StdDev × multiplier.

    Returns dict with tvem_mid, tvem_upper, tvem_lower, current_price,
    price_position, at_lower_band, at_upper_band, signal.
    """
    result: dict = {
        "tvem_mid": None,
        "tvem_upper": None,
        "tvem_lower": None,
        "current_price": None,
        "price_position": "UNKNOWN",
        "at_lower_band": False,
        "at_upper_band": False,
        "signal": "NEUTRAL",
    }

    if not ohlcv or len(ohlcv) < ema_period + 10:
        return result

    try:
        closes = [c[4] for c in ohlcv]
        highs = [c[2] for c in ohlcv]
        lows = [c[3] for c in ohlcv]
        volumes = [c[5] for c in ohlcv]
        current_price = closes[-1]
        result["current_price"] = current_price

        # Trailing VWAP: cumulative TP×Vol / cumulative Vol
        typical_prices = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]
        cum_tp_vol = 0.0
        cum_vol = 0.0
        vwap_values: list[float] = []
        for i in range(len(typical_prices)):
            cum_tp_vol += typical_prices[i] * volumes[i]
            cum_vol += volumes[i]
            vwap_values.append(cum_tp_vol / cum_vol if cum_vol > 0 else typical_prices[i])

        # EMA
        from src.analysis.price_action_analyzer import PriceActionAnalyzer
        analyzer = PriceActionAnalyzer(exchange_id="binance")
        ema_values = analyzer._calculate_ema(closes, ema_period)
        ema_last = ema_values[-1] if ema_values else closes[-1]

        # TVEM = (VWAP + EMA) / 2
        tvem_values: list[float] = []
        for i in range(len(closes)):
            vwap_i = vwap_values[i] if i < len(vwap_values) else vwap_values[-1]
            ema_i = ema_values[i] if i < len(ema_values) else ema_last
            tvem_values.append((vwap_i + ema_i) / 2)

        tvem_mid = tvem_values[-1]
        result["tvem_mid"] = tvem_mid

        # Std dev of recent TVEM values
        window = tvem_values[-20:] if len(tvem_values) >= 20 else tvem_values
        mean_tvem = sum(window) / len(window)
        variance = sum((x - mean_tvem) ** 2 for x in window) / len(window)
        std_dev = variance ** 0.5

        tvem_upper = tvem_mid + std_dev * std_multiplier
        tvem_lower = tvem_mid - std_dev * std_multiplier
        result["tvem_upper"] = tvem_upper
        result["tvem_lower"] = tvem_lower

        # Price position
        if current_price > tvem_upper:
            result["price_position"] = "ABOVE_UPPER"
        elif current_price > tvem_mid:
            result["price_position"] = "ABOVE_MID"
        elif current_price > tvem_lower:
            result["price_position"] = "BELOW_MID"
        else:
            result["price_position"] = "BELOW_LOWER"

        # At-band detection (2% tolerance)
        dist_lower = abs(current_price - tvem_lower) / tvem_lower if tvem_lower > 0 else 0
        dist_upper = abs(current_price - tvem_upper) / tvem_upper if tvem_upper > 0 else 0
        result["at_lower_band"] = dist_lower <= 0.02
        result["at_upper_band"] = dist_upper <= 0.02

        # Signal
        if result["at_lower_band"]:
            result["signal"] = "BULLISH_RETEST"
        elif result["at_upper_band"]:
            result["signal"] = "BEARISH_RETEST"
        elif result["price_position"] == "BELOW_LOWER":
            result["signal"] = "OVERSOLD"
        elif result["price_position"] == "ABOVE_UPPER":
            result["signal"] = "OVERBOUGHT"

        return result
    except Exception as e:
        logger.warning(f"TVEM calculation failed: {e}")
        return result


# ---------------------------------------------------------------------------
# Harmonic pattern detection (XABCD)
# ---------------------------------------------------------------------------

# Fibonacci ratio tolerances for harmonic patterns
_HARMONIC_PATTERNS: dict[str, dict] = {
    "Gartley": {
        "ab_xа": (0.55, 0.68),   # AB = 61.8% of XA
        "bc_ab": (0.38, 0.89),   # BC = 38.2-88.6% of AB
        "cd_bc": (1.13, 1.62),   # CD = 1.13-1.618x of BC
        "cd_xa": (0.72, 0.85),   # D = 78.6% of XA
    },
    "Bat": {
        "ab_xа": (0.33, 0.55),   # AB = 38.2-50% of XA
        "bc_ab": (0.38, 0.89),   # BC = 38.2-88.6% of AB
        "cd_bc": (1.62, 2.62),   # CD = 1.618-2.618x of BC
        "cd_xa": (0.82, 0.93),   # D = 88.6% of XA
    },
    "Butterfly": {
        "ab_xа": (0.72, 0.85),   # AB = 78.6% of XA
        "bc_ab": (0.38, 0.89),   # BC = 38.2-88.6% of AB
        "cd_bc": (1.62, 2.62),   # CD = 1.618-2.618x of BC
        "cd_xa": (1.20, 1.68),   # D = 127-161.8% of XA
    },
    "Crab": {
        "ab_xа": (0.33, 0.68),   # AB = 38.2-61.8% of XA
        "bc_ab": (0.38, 0.89),   # BC = 38.2-88.6% of AB
        "cd_bc": (2.24, 3.62),   # CD = 2.24-3.618x of BC
        "cd_xa": (1.55, 1.68),   # D = 161.8% of XA
    },
}


def _find_swing_points(ohlcv: list[list], lookback: int = 5) -> list[dict]:
    """Find swing highs and lows from OHLCV data.

    A swing high has highest high in ±lookback window.
    A swing low has lowest low in ±lookback window.
    """
    if len(ohlcv) < lookback * 2 + 1:
        return []

    swings: list[dict] = []
    for i in range(lookback, len(ohlcv) - lookback):
        high = ohlcv[i][2]
        low = ohlcv[i][3]

        # Check swing high
        is_high = all(
            ohlcv[j][2] <= high
            for j in range(i - lookback, i + lookback + 1)
            if j != i
        )
        if is_high:
            swings.append({"index": i, "type": "high", "price": high})

        # Check swing low
        is_low = all(
            ohlcv[j][3] >= low
            for j in range(i - lookback, i + lookback + 1)
            if j != i
        )
        if is_low:
            swings.append({"index": i, "type": "low", "price": low})

    return swings


def _check_harmonic_ratios(
    xa: float, ab: float, bc: float, cd: float, pattern_def: dict
) -> bool:
    """Check if XABCD legs match a harmonic pattern's Fibonacci ratios."""
    if xa == 0 or ab == 0 or bc == 0:
        return False

    ab_xa = ab / xa
    bc_ab = bc / ab
    cd_bc = cd / bc if bc > 0 else 0
    cd_xa = cd / xa

    lo, hi = pattern_def["ab_xа"]
    if not (lo <= ab_xa <= hi):
        return False

    lo, hi = pattern_def["bc_ab"]
    if not (lo <= bc_ab <= hi):
        return False

    lo, hi = pattern_def["cd_bc"]
    if not (lo <= cd_bc <= hi):
        return False

    lo, hi = pattern_def["cd_xa"]
    if not (lo <= cd_xa <= hi):
        return False

    return True


def _detect_harmonics(ohlcv: list[list], current_price: float) -> list[dict]:
    """Detect XABCD harmonic patterns from OHLCV data.

    Returns list of detected patterns with:
      name, direction (bullish/bearish), d_price, strength, completion_pct.
    """
    if not ohlcv or len(ohlcv) < 30:
        return []

    swings = _find_swing_points(ohlcv, lookback=3)
    if len(swings) < 5:
        return []

    detected: list[dict] = []

    # Try combinations of last 12 swing points (limit search space)
    recent_swings = swings[-12:]

    for i in range(len(recent_swings) - 4):
        x = recent_swings[i]
        a = recent_swings[i + 1]
        b = recent_swings[i + 2]
        c = recent_swings[i + 3]
        d = recent_swings[i + 4]

        # Validate alternating swing types (H-L-H-L-H or L-H-L-H-L)
        types = [s["type"] for s in [x, a, b, c, d]]
        expected_bull = ["low", "high", "low", "high", "low"]
        expected_bear = ["high", "low", "high", "low", "high"]

        if types == expected_bull:
            direction = "bullish"
        elif types == expected_bear:
            direction = "bearish"
        else:
            continue

        # Calculate leg lengths
        xa = abs(a["price"] - x["price"])
        ab = abs(b["price"] - a["price"])
        bc = abs(c["price"] - b["price"])
        cd = abs(d["price"] - c["price"])

        # Check against each pattern
        for name, ratios in _HARMONIC_PATTERNS.items():
            if _check_harmonic_ratios(xa, ab, bc, cd, ratios):
                # Calculate completion (how close current price is to D)
                d_price = d["price"]
                dist_to_d = abs(current_price - d_price) / d_price if d_price > 0 else 1.0
                completion = max(0.0, 1.0 - dist_to_d) * 100

                detected.append({
                    "name": name,
                    "direction": direction,
                    "d_price": d_price,
                    "strength": "STRONG" if completion > 80 else "MODERATE",
                    "completion_pct": round(completion, 1),
                })
                break  # One pattern per XABCD combination

    # Deduplicate: keep highest completion per pattern name
    best: dict[str, dict] = {}
    for p in detected:
        key = f"{p['name']}_{p['direction']}"
        if key not in best or p["completion_pct"] > best[key]["completion_pct"]:
            best[key] = p

    return list(best.values())


# ---------------------------------------------------------------------------
# Confluence tier classification helpers
# ---------------------------------------------------------------------------

def _classify_confluence_tier(name: str) -> str:
    """Classify a confluence factor into its tier for BQS scoring."""
    structural = {
        "ema_alignment", "ema_200_position", "trendline_break",
        "market_structure",
    }
    zones = {
        "qvwap_retest", "yvwap_support", "support_retest",
        "resistance_retest", "mp_vwap_zone", "fibonacci_level",
    }
    # Everything else is confirmation
    if name in structural:
        return "structural"
    if name in zones:
        return "zones"
    return "confirmation"


def _build_confluences_for_pipeline(
    ema_data: dict,
    sr_levels: dict,
    volume_profile: dict,
    fib_levels: dict,
    patterns: list[dict],
    confluence_result: dict,
    direction: str,
    entry: float,
    current_price: float,
    structure_data: Optional[dict] = None,
) -> list[dict]:
    """
    Build the confluences list for TAExtractionResult.

    Each confluence is a dict with: type (tier), name, level, indicator.
    This is what score_confluences() in scorer.py expects.
    """
    confluences = []

    # EMA alignment
    alignment = ema_data.get("dual_ema", {}).get("alignment", "unknown")
    if (direction == "SHORT" and alignment == "bearish") or (
        direction == "LONG" and alignment == "bullish"
    ):
        confluences.append({
            "tier": "structural",
            "type": "structural",
            "name": f"EMA 12/24 {alignment} alignment",
            "indicator": "EMA_ALIGNMENT",
        })

    # EMA 200 position
    pos = ema_data.get("mtf_ema_200", {}).get("position_vs_ema", "unknown")
    ema_200 = ema_data.get("ema_200")
    if (direction == "SHORT" and pos == "below") or (
        direction == "LONG" and pos == "above"
    ):
        confluences.append({
            "tier": "structural",
            "type": "structural",
            "name": f"Price {pos} 200 EMA ({ema_200:.4f})" if ema_200 else f"Price {pos} 200 EMA",
            "indicator": "EMA_200_POSITION",
            "level": ema_200,
        })

    # S/R levels near entry
    for s in sr_levels.get("supports", [])[:3]:
        if abs(s["price"] - entry) / entry < 0.03:
            confluences.append({
                "tier": "zones",
                "type": "zones",
                "name": f"Support at {s['price']:.4f} ({s.get('strength', 'MODERATE')})",
                "indicator": "SUPPORT_RETEST",
                "level": s["price"],
            })

    for r in sr_levels.get("resistances", [])[:3]:
        if abs(r["price"] - entry) / entry < 0.03:
            confluences.append({
                "tier": "zones",
                "type": "zones",
                "name": f"Resistance at {r['price']:.4f} ({r.get('strength', 'MODERATE')})",
                "indicator": "RESISTANCE_RETEST",
                "level": r["price"],
            })

    # Volume profile zones
    poc = volume_profile.get("poc")
    if poc and abs(poc - entry) / entry < 0.02:
        confluences.append({
            "tier": "zones",
            "type": "zones",
            "name": f"POC at {poc:.4f}",
            "indicator": "MP_VWAP_ZONE",
            "level": poc,
        })

    vah = volume_profile.get("vah")
    if vah and abs(vah - entry) / entry < 0.02:
        confluences.append({
            "tier": "zones",
            "type": "zones",
            "name": f"VAH at {vah:.4f}",
            "indicator": "MP_VWAP_ZONE",
            "level": vah,
        })

    val = volume_profile.get("val")
    if val and abs(val - entry) / entry < 0.02:
        confluences.append({
            "tier": "zones",
            "type": "zones",
            "name": f"VAL at {val:.4f}",
            "indicator": "MP_VWAP_ZONE",
            "level": val,
        })

    # Quarterly VWAP (Session 355 P1b)
    qvwap = volume_profile.get("vwap")
    if qvwap and abs(qvwap - entry) / entry < 0.02:
        confluences.append({
            "tier": "zones",
            "type": "zones",
            "name": f"QVWAP at {qvwap:.4f}",
            "indicator": "QVWAP_LEVEL",
            "level": qvwap,
        })

    # TVEM Band levels (Session 355 P1a — L058)
    tvem_mid = volume_profile.get("_tvem_mid")
    tvem_upper = volume_profile.get("_tvem_upper")
    tvem_lower = volume_profile.get("_tvem_lower")
    for tvem_val, tvem_label in [
        (tvem_mid, "TVEM Mid"),
        (tvem_upper, "TVEM Upper"),
        (tvem_lower, "TVEM Lower"),
    ]:
        if tvem_val and isinstance(tvem_val, (int, float)) and tvem_val > 0:
            if abs(tvem_val - entry) / entry < 0.02:
                confluences.append({
                    "tier": "zones",
                    "type": "zones",
                    "name": f"{tvem_label} at {tvem_val:.4f}",
                    "indicator": "TVEM_BAND",
                    "level": tvem_val,
                })
                break  # Only one TVEM confluence per entry

    # Fibonacci levels near entry
    for fib_name, fib_price in fib_levels.items():
        if isinstance(fib_price, (int, float)) and fib_price > 0:
            if abs(fib_price - entry) / entry < 0.02:
                confluences.append({
                    "tier": "zones",
                    "type": "zones",
                    "name": f"Fib {fib_name} at {fib_price:.4f}",
                    "indicator": "FIBONACCI_LEVEL",
                    "level": fib_price,
                })

    # Detected patterns
    for p in patterns[:3]:
        ptype = p.get("pattern_type", "neutral")
        if (direction == "SHORT" and ptype == "bearish_reversal") or (
            direction == "LONG" and ptype == "bullish_reversal"
        ):
            strength = p.get("strength", "MODERATE")
            tier = "structural" if strength == "STRONG" else "confirmation"
            confluences.append({
                "tier": tier,
                "type": tier,
                "name": p.get("pattern_name", "Unknown Pattern"),
                "indicator": f"CHART_PATTERN_{strength}",
            })

    # ------------------------------------------------------------------
    # Session 357: Market structure confluences (trendlines, FVGs, OBs, equilibrium)
    # ------------------------------------------------------------------
    sd = structure_data or {}

    # Trendline confluence
    trendline = sd.get("trendline")
    if trendline and trendline.get("detected"):
        tl_direction = trendline.get("direction", "")
        tl_broken = trendline.get("broken", False)
        tl_touches = trendline.get("touch_count", 0)
        tl_strength = trendline.get("strength", "weak")
        # Trendline break aligned with trade direction = structural confluence
        if tl_broken:
            ascending_break_short = direction == "SHORT" and "up" in tl_direction.lower()
            descending_break_long = direction == "LONG" and "down" in tl_direction.lower()
            if ascending_break_short or descending_break_long:
                tier = "structural" if tl_touches >= 3 else "confirmation"
                confluences.append({
                    "tier": tier, "type": tier,
                    "name": f"Trendline break ({tl_touches} touches, {tl_strength})",
                    "indicator": "TRENDLINE_BREAK",
                })
        # Approaching trendline (breakout imminent)
        elif trendline.get("breakout_imminent"):
            dist = trendline.get("distance_pct", 0)
            confluences.append({
                "tier": "confirmation", "type": "confirmation",
                "name": f"Approaching trendline ({dist:.1f}% away, {tl_touches} touches)",
                "indicator": "TRENDLINE_APPROACH",
            })

    # FVG confluence — direction-aligned FVG near entry
    if direction == "SHORT":
        fvg = sd.get("nearest_bearish_fvg")
    else:
        fvg = sd.get("nearest_bullish_fvg")
    if fvg:
        fvg_mid = fvg.get("midpoint", 0)
        if fvg_mid and entry > 0 and abs(fvg_mid - entry) / entry < 0.03:
            confluences.append({
                "tier": "zones", "type": "zones",
                "name": f"FVG zone {fvg.get('bottom', 0):.4f}-{fvg.get('top', 0):.4f} ({fvg.get('strength', 'moderate')})",
                "indicator": "FVG_ZONE",
                "level": fvg_mid,
            })

    # Order Block confluence — unmitigated OB near entry
    if direction == "SHORT":
        ob = sd.get("unmitigated_bearish_ob")
    else:
        ob = sd.get("unmitigated_bullish_ob")
    if ob:
        ob_mid = ob.get("midpoint", 0)
        if ob_mid and entry > 0 and abs(ob_mid - entry) / entry < 0.03:
            tier = "structural" if ob.get("preceded_by_sweep") else "zones"
            confluences.append({
                "tier": tier, "type": tier,
                "name": f"Order Block {ob.get('bottom', 0):.4f}-{ob.get('top', 0):.4f} ({ob.get('strength', 'moderate')})",
                "indicator": "ORDER_BLOCK",
                "level": ob_mid,
            })

    # Equilibrium zone alignment
    eq = sd.get("equilibrium")
    if eq:
        zone = eq.get("zone", "")
        if (direction == "SHORT" and zone == "premium") or \
           (direction == "LONG" and zone == "discount"):
            eq_price = eq.get("equilibrium_price", 0)
            confluences.append({
                "tier": "confirmation", "type": "confirmation",
                "name": f"Price in {zone} zone (eq at {eq_price:.4f})" if eq_price else f"Price in {zone} zone",
                "indicator": "EQUILIBRIUM_ZONE",
            })

    # Harmonic patterns (Session 355 P2)
    _harmonics = volume_profile.get("_harmonics", [])
    for h in _harmonics:
        h_dir = h.get("direction", "")
        h_name = h.get("name", "Unknown")
        # Only add if direction aligns
        if (direction == "SHORT" and h_dir == "bearish") or (
            direction == "LONG" and h_dir == "bullish"
        ):
            strength = h.get("strength", "MODERATE")
            tier = "structural" if strength == "STRONG" else "confirmation"
            d_price = h.get("d_price")
            label = f"Harmonic {h_name} ({h_dir})"
            if d_price:
                label += f" D={d_price:.4f}"
            confluences.append({
                "tier": tier,
                "type": tier,
                "name": label,
                "indicator": "HARMONIC_PATTERN",
                "level": d_price,
            })

    # Additional factors from confluence counter
    for desc in confluence_result.get("factor_descriptions", []):
        # Only add if not already represented (check both directions)
        desc_lower = desc.lower()
        already_present = False
        for c in confluences:
            c_lower = c["name"].lower()
            # Check if either string contains the other, or if they share
            # key terms (e.g. "ema 12/24" vs "12+24 ema")
            if desc_lower in c_lower or c_lower in desc_lower:
                already_present = True
                break
            # Catch EMA alignment duplicates with different formatting
            if "ema" in desc_lower and "ema" in c_lower:
                if ("12" in desc_lower and "24" in desc_lower and
                        "12" in c_lower and "24" in c_lower):
                    already_present = True
                    break
        if not already_present:
            confluences.append({
                "tier": "confirmation",
                "type": "confirmation",
                "name": desc,
                "indicator": "CONFLUENCE_FACTOR",
            })

    # Session 354: Deduplicate by indicator type — keep first occurrence
    seen_indicators: set[str] = set()
    deduped: list[dict] = []
    for c in confluences:
        indicator = c.get("indicator", "")
        if indicator and indicator in seen_indicators:
            continue
        if indicator:
            seen_indicators.add(indicator)
        deduped.append(c)

    return deduped


# ---------------------------------------------------------------------------
# DCA confluence check
# ---------------------------------------------------------------------------

def _check_dca_confluence(
    dca: Optional[float],
    sr_levels: dict,
    fib_levels: dict,
    ema_data: dict,
    volume_profile: dict,
) -> list[dict]:
    """
    Check if DCA aligns (within 2%) with key technical levels.

    Returns list of confluence dicts matching the pipeline format.
    Each match is classified as a "zones" tier confluence.
    """
    if dca is None or dca <= 0:
        return []

    matches: list[dict] = []

    # Check S/R levels
    for s in sr_levels.get("supports", []):
        if abs(s["price"] - dca) / dca < 0.02:
            matches.append({
                "tier": "zones",
                "type": "zones",
                "name": f"DCA at support {s['price']:.4f}",
                "indicator": "DCA_SUPPORT_CONFLUENCE",
                "level": s["price"],
            })
            break

    for r in sr_levels.get("resistances", []):
        if abs(r["price"] - dca) / dca < 0.02:
            matches.append({
                "tier": "zones",
                "type": "zones",
                "name": f"DCA at resistance {r['price']:.4f}",
                "indicator": "DCA_RESISTANCE_CONFLUENCE",
                "level": r["price"],
            })
            break

    # Check Fibonacci levels
    for fib_name, fib_price in fib_levels.items():
        if isinstance(fib_price, (int, float)) and fib_price > 0:
            if abs(fib_price - dca) / dca < 0.02:
                matches.append({
                    "tier": "zones",
                    "type": "zones",
                    "name": f"DCA at Fib {fib_name} ({fib_price:.4f})",
                    "indicator": "DCA_FIB_CONFLUENCE",
                    "level": fib_price,
                })
                break

    # Check EMA levels
    for ema_key in ("ema_12", "ema_24", "ema_200"):
        ema_val = ema_data.get(ema_key)
        if ema_val and isinstance(ema_val, (int, float)) and ema_val > 0:
            if abs(ema_val - dca) / dca < 0.02:
                matches.append({
                    "tier": "zones",
                    "type": "zones",
                    "name": f"DCA at {ema_key.upper().replace('_', ' ')} ({ema_val:.4f})",
                    "indicator": "DCA_EMA_CONFLUENCE",
                    "level": ema_val,
                })
                break

    # Check volume profile (POC, VAH, VAL)
    for vp_key in ("poc", "vah", "val"):
        vp_val = volume_profile.get(vp_key)
        if vp_val and isinstance(vp_val, (int, float)) and vp_val > 0:
            if abs(vp_val - dca) / dca < 0.02:
                matches.append({
                    "tier": "zones",
                    "type": "zones",
                    "name": f"DCA at {vp_key.upper()} ({vp_val:.4f})",
                    "indicator": "DCA_VP_CONFLUENCE",
                    "level": vp_val,
                })
                break

    # Check QVWAP (Session 355 P1b)
    qvwap = volume_profile.get("vwap")
    if qvwap and isinstance(qvwap, (int, float)) and qvwap > 0:
        if abs(qvwap - dca) / dca < 0.02:
            matches.append({
                "tier": "zones",
                "type": "zones",
                "name": f"DCA at QVWAP ({qvwap:.4f})",
                "indicator": "DCA_QVWAP_CONFLUENCE",
                "level": qvwap,
            })

    # Check TVEM band levels (Session 355 P1a)
    for tvem_key, tvem_label in [
        ("_tvem_mid", "TVEM Mid"),
        ("_tvem_lower", "TVEM Lower"),
        ("_tvem_upper", "TVEM Upper"),
    ]:
        tvem_val = volume_profile.get(tvem_key)
        if tvem_val and isinstance(tvem_val, (int, float)) and tvem_val > 0:
            if abs(tvem_val - dca) / dca < 0.02:
                matches.append({
                    "tier": "zones",
                    "type": "zones",
                    "name": f"DCA at {tvem_label} ({tvem_val:.4f})",
                    "indicator": "DCA_TVEM_CONFLUENCE",
                    "level": tvem_val,
                })
                break  # Only one TVEM DCA confluence

    return matches


# ---------------------------------------------------------------------------
# Structure classification
# ---------------------------------------------------------------------------

def _classify_market_structure(
    structure_data: dict,
    ema_data: Optional[dict] = None,
) -> str:
    """Convert market structure analysis to LH_LL / HH_HL / UNKNOWN.

    Falls back to EMA alignment when the swing-point analyzer returns
    "unknown" (e.g. insufficient swing data for CHoCH/BOS detection).
    """
    structure = structure_data.get("current_structure", "unknown")
    if structure == "bearish":
        return "LH_LL"
    elif structure == "bullish":
        return "HH_HL"
    elif structure == "ranging":
        return "RANGING"

    # Fallback: infer from EMA alignment when structure analyzer has no data
    if ema_data:
        alignment = ema_data.get("dual_ema", {}).get("alignment", "unknown")
        if alignment == "bearish":
            return "LH_LL"
        elif alignment == "bullish":
            return "HH_HL"

    return "UNKNOWN"


def _classify_trend(ema_data: dict, structure_data: dict) -> str:
    """Determine trend direction from EMAs and market structure."""
    alignment = ema_data.get("dual_ema", {}).get("alignment", "unknown")
    structure = structure_data.get("current_structure", "unknown")

    if alignment == "bearish" and structure in ("bearish", "ranging"):
        return "BEARISH"
    elif alignment == "bullish" and structure in ("bullish", "ranging"):
        return "BULLISH"
    elif alignment == "bearish" or structure == "bearish":
        return "BEARISH"
    elif alignment == "bullish" or structure == "bullish":
        return "BULLISH"
    return "NEUTRAL"


# ---------------------------------------------------------------------------
# Volume analysis
# ---------------------------------------------------------------------------

def _compute_volume_analysis(ohlcv: list[list]) -> dict:
    """Analyze volume for spikes and divergences."""
    if not ohlcv or len(ohlcv) < 10:
        return {"volume_spike": False}

    volumes = [c[5] for c in ohlcv]
    avg_volume = sum(volumes[-20:]) / min(len(volumes), 20)
    recent_volume = volumes[-1]

    return {
        "volume_spike": recent_volume > avg_volume * 1.5,
        "avg_volume": avg_volume,
        "recent_volume": recent_volume,
        "volume_ratio": recent_volume / avg_volume if avg_volume > 0 else 1.0,
    }


def _detect_sideways_range(ohlcv: list[list], lookback_candles: int = 42) -> dict:
    """Detect sideways/ranging market from TOKEN OHLCV data.

    Session 373b: Data-driven sideways detection at source level.
    Uses the token's own 7-day range (42 x 4h candles) to detect ranging markets.
    Evidence: 11 of 38 feedback trades cite L039 whipsaw from sideways markets.

    Args:
        ohlcv: OHLCV candle data [[ts, o, h, l, c, v], ...]
        lookback_candles: Number of candles to check (default 42 = ~7 days of 4h)

    Returns:
        dict with keys:
            range_pct: float - 7-day range as percentage of avg price
            is_sideways: bool - True if range < 5%
            is_deep_sideways: bool - True if range < 3%
            label: str - "SIDEWAYS" / "DEEP_SIDEWAYS" / "TRENDING"
    """
    if not ohlcv or len(ohlcv) < 10:
        return {"range_pct": None, "is_sideways": False, "is_deep_sideways": False, "label": "UNKNOWN"}

    recent = ohlcv[-lookback_candles:]
    highs = [c[2] for c in recent]
    lows = [c[3] for c in recent]
    max_high = max(highs)
    min_low = min(lows)
    avg_price = (max_high + min_low) / 2

    if avg_price <= 0:
        return {"range_pct": None, "is_sideways": False, "is_deep_sideways": False, "label": "UNKNOWN"}

    range_pct = (max_high - min_low) / avg_price * 100

    is_deep = range_pct < 3.0
    is_sideways = range_pct < 5.0

    if is_deep:
        label = "DEEP_SIDEWAYS"
    elif is_sideways:
        label = "SIDEWAYS"
    else:
        label = "TRENDING"

    return {
        "range_pct": round(range_pct, 2),
        "is_sideways": is_sideways,
        "is_deep_sideways": is_deep,
        "label": label,
    }


# ---------------------------------------------------------------------------
# Session 357: Candle behavior at key levels
# ---------------------------------------------------------------------------

def _analyze_candle_behavior_at_levels(
    ohlcv: list[list],
    sr_levels: dict,
    structure_data: dict,
    entry: float,
    direction: str,
    lookback: int = 5,
) -> list[dict]:
    """Analyze how recent candles behave at key levels.

    Checks for rejection wicks, absorption, and engulfing patterns
    at support/resistance, order block zones, and entry price.

    Returns list of observations sorted by relevance:
    [{"type": "rejection_wick", "level": 1.234, "level_name": "resistance",
      "description": "Strong rejection wick at resistance (wick 3.2x body)"}]
    """
    if not ohlcv or len(ohlcv) < lookback + 1:
        return []

    observations: list[dict] = []

    # Collect key levels to check
    key_levels: list[tuple[float, str]] = []

    # Entry price
    if entry > 0:
        key_levels.append((entry, "entry"))

    # S/R levels
    for s in sr_levels.get("supports", [])[:3]:
        price = s.get("price", 0)
        if price > 0:
            key_levels.append((price, f"support ({s.get('strength', 'MODERATE')})"))
    for r in sr_levels.get("resistances", [])[:3]:
        price = r.get("price", 0)
        if price > 0:
            key_levels.append((price, f"resistance ({r.get('strength', 'MODERATE')})"))

    # Order Block zones
    sd = structure_data or {}
    for ob_key, ob_label in [
        ("unmitigated_bearish_ob", "bearish OB"),
        ("unmitigated_bullish_ob", "bullish OB"),
    ]:
        ob = sd.get(ob_key)
        if ob and ob.get("midpoint"):
            key_levels.append((ob["midpoint"], ob_label))

    if not key_levels:
        return []

    # Analyze recent candles at each key level
    recent_candles = ohlcv[-lookback:]
    for level, level_name in key_levels:
        if level <= 0:
            continue

        for candle in recent_candles:
            _, o, h, l, c, vol = candle
            body = abs(c - o)
            upper_wick = h - max(o, c)
            lower_wick = min(o, c) - l
            full_range = h - l
            if full_range <= 0:
                continue

            # Did this candle touch the level? (within 0.5%)
            touches_level = (l <= level * 1.005) and (h >= level * 0.995)
            if not touches_level:
                continue

            body_safe = max(body, full_range * 0.01)  # avoid division by zero

            # Rejection wick: long wick > 2x body at level
            if level_name.startswith("resistance") or (direction == "SHORT" and level_name == "entry"):
                # Upper wick rejection at resistance/entry (bearish rejection)
                if upper_wick > body_safe * 2:
                    ratio = upper_wick / body_safe
                    observations.append({
                        "type": "rejection_wick",
                        "level": level,
                        "level_name": level_name,
                        "description": f"Rejection wick at {level_name} (wick {ratio:.1f}x body)",
                        "strength": "strong" if ratio > 3 else "moderate",
                    })
            elif level_name.startswith("support") or (direction == "LONG" and level_name == "entry"):
                # Lower wick rejection at support/entry (bullish rejection)
                if lower_wick > body_safe * 2:
                    ratio = lower_wick / body_safe
                    observations.append({
                        "type": "rejection_wick",
                        "level": level,
                        "level_name": level_name,
                        "description": f"Rejection wick at {level_name} (wick {ratio:.1f}x body)",
                        "strength": "strong" if ratio > 3 else "moderate",
                    })

            # Absorption: high volume + small body at level (institutional activity)
            if body < full_range * 0.3 and vol > 0:
                # Check if this candle had above-average volume
                recent_vols = [c_[5] for c_ in ohlcv[-20:] if c_[5] > 0]
                if recent_vols:
                    avg_vol = sum(recent_vols) / len(recent_vols)
                    if vol > avg_vol * 1.5:
                        observations.append({
                            "type": "absorption",
                            "level": level,
                            "level_name": level_name,
                            "description": f"Absorption at {level_name} (small body, {vol/avg_vol:.1f}x avg volume)",
                            "strength": "strong" if vol > avg_vol * 2 else "moderate",
                        })

    # Deduplicate by level_name, keep strongest
    seen: dict[str, dict] = {}
    strength_order = {"strong": 2, "moderate": 1}
    for obs in observations:
        key = f"{obs['type']}_{obs['level_name']}"
        existing = seen.get(key)
        if not existing or strength_order.get(obs["strength"], 0) > strength_order.get(existing["strength"], 0):
            seen[key] = obs

    result = list(seen.values())

    # --- Proximity-based dedup: same type at levels within 1% → keep strongest ---
    proximity_deduped: list[dict] = []
    for obs in result:
        merged = False
        for existing in proximity_deduped:
            if existing["type"] != obs["type"]:
                continue
            # Check if levels are within 1% of each other
            avg_level = (existing["level"] + obs["level"]) / 2
            if avg_level > 0 and abs(existing["level"] - obs["level"]) / avg_level <= 0.01:
                # Keep the one with the strongest level or highest strength
                if strength_order.get(obs["strength"], 0) > strength_order.get(existing["strength"], 0):
                    proximity_deduped.remove(existing)
                    proximity_deduped.append(obs)
                merged = True
                break
        if not merged:
            proximity_deduped.append(obs)
    result = proximity_deduped

    # Sort: strong first, then by type (rejection > absorption)
    result.sort(key=lambda x: (-strength_order.get(x["strength"], 0), x["type"]))
    return result


# ---------------------------------------------------------------------------
# Session 357: Multi-candle momentum narrative
# ---------------------------------------------------------------------------

def _analyze_momentum_narrative(
    ohlcv: list[list],
    direction: str,
    lookback: int = 10,
) -> str:
    """Produce a one-line momentum narrative from recent candle character.

    Analyzes body size trend, wick ratios, candle color distribution,
    and range trend to classify momentum.

    Examples:
    - "Momentum weakening: decreasing body sizes, increasing upper wicks"
    - "Strong selling pressure: 7/10 bearish candles with expanding bodies"
    - "Indecision: alternating candles with shrinking range"
    """
    if not ohlcv or len(ohlcv) < lookback:
        return ""

    recent = ohlcv[-lookback:]

    # Body sizes
    bodies: list[float] = []
    upper_wicks: list[float] = []
    lower_wicks: list[float] = []
    ranges: list[float] = []
    bullish_count = 0
    bearish_count = 0

    for candle in recent:
        _, o, h, l, c, _ = candle
        body = abs(c - o)
        full_range = h - l
        bodies.append(body)
        ranges.append(full_range)
        upper_wicks.append(h - max(o, c))
        lower_wicks.append(min(o, c) - l)
        if c > o:
            bullish_count += 1
        elif c < o:
            bearish_count += 1

    if not bodies or not ranges:
        return ""

    # Body size trend: compare first half vs second half
    half = lookback // 2
    first_half_body = sum(bodies[:half]) / half if half > 0 else 0
    second_half_body = sum(bodies[half:]) / (lookback - half) if (lookback - half) > 0 else 0

    body_expanding = second_half_body > first_half_body * 1.2
    body_contracting = second_half_body < first_half_body * 0.8

    # Wick trend
    first_half_wick = sum(upper_wicks[:half]) + sum(lower_wicks[:half])
    second_half_wick = sum(upper_wicks[half:]) + sum(lower_wicks[half:])
    wick_increasing = second_half_wick > first_half_wick * 1.3 if first_half_wick > 0 else False

    # Range trend
    first_half_range = sum(ranges[:half]) / half if half > 0 else 0
    second_half_range = sum(ranges[half:]) / (lookback - half) if (lookback - half) > 0 else 0
    range_expanding = second_half_range > first_half_range * 1.2
    range_contracting = second_half_range < first_half_range * 0.8

    # Classification
    total = bullish_count + bearish_count
    if total == 0:
        return "Indecision: all doji candles"

    bearish_pct = bearish_count / total
    bullish_pct = bullish_count / total

    # Strong directional momentum
    if bearish_pct >= 0.7 and body_expanding:
        return f"Strong selling pressure: {bearish_count}/{lookback} bearish candles with expanding bodies"
    if bullish_pct >= 0.7 and body_expanding:
        return f"Strong buying pressure: {bullish_count}/{lookback} bullish candles with expanding bodies"

    # Weakening momentum
    if body_contracting and wick_increasing:
        return "Momentum weakening: decreasing body sizes, increasing wicks"
    if body_contracting:
        return "Momentum fading: decreasing body sizes over recent candles"

    # Compression
    if range_contracting and not body_expanding:
        return "Range compression: contracting candles suggest imminent breakout"

    # Expanding volatility
    if range_expanding and body_expanding:
        dominant = "bearish" if bearish_pct > 0.55 else "bullish" if bullish_pct > 0.55 else "mixed"
        return f"Expanding volatility: widening ranges with {dominant} bias"

    # Indecision
    if abs(bearish_pct - bullish_pct) < 0.2 and wick_increasing:
        return "Indecision: balanced candle colors with increasing wicks"

    # Default: only show if there's a clear lean (>60%), suppress noise
    if bearish_pct > 0.6:
        return f"Bearish leaning: {bearish_count}/{lookback} bearish candles"
    elif bullish_pct > 0.6:
        return f"Bullish leaning: {bullish_count}/{lookback} bullish candles"

    # 40-60% split is noise — no actionable signal
    return ""


# ---------------------------------------------------------------------------
# Reasoning builder
# ---------------------------------------------------------------------------

def _build_reasoning(
    direction: str,
    entry: float,
    sl: float,
    tp: float,
    rsi: float,
    ema_data: dict,
    structure_label: str,
    trend: str,
    confluence_result: dict,
    patterns: list[dict],
    sr_levels: dict,
    volume_data: dict,
    num_confluences: int = 0,
    dca: Optional[float] = None,
    dca_confluences: Optional[list[dict]] = None,
    volume_profile: Optional[dict] = None,
    tvem_data: Optional[dict] = None,
    harmonics: Optional[list[dict]] = None,
    structure_data: Optional[dict] = None,
    ohlcv: Optional[list[list]] = None,
    sideways_data: Optional[dict] = None,
) -> list[str]:
    """Build human-readable reasoning list for the TA result."""
    reasoning = []

    # Session 373b: Sideways market warning at source level (L039)
    sw = sideways_data or {}
    if sw.get("is_deep_sideways"):
        reasoning.append(
            f"\u26a0\ufe0f DEEP SIDEWAYS: Token 7d range {sw['range_pct']:.1f}% "
            f"— high whipsaw risk, widen SL buffer +5% (L039)"
        )
    elif sw.get("is_sideways"):
        reasoning.append(
            f"\u26a0\ufe0f SIDEWAYS MARKET: Token 7d range {sw['range_pct']:.1f}% "
            f"— whipsaw risk, widen SL buffer +2-5% (L039)"
        )

    sd = structure_data or {}

    # Direction and trend (enriched with CHoCH/BOS details — Session 357)
    reasoning.append(
        f"{direction} setup: Entry {entry:.4f}, SL {sl:.4f}, TP {tp:.4f}"
    )
    structure_parts = [f"Market structure: {structure_label}"]
    choch = sd.get("choch_details")
    if choch and choch.get("direction"):
        choch_price = choch.get("price", 0)
        if choch_price:
            structure_parts.append(f"CHoCH {choch['direction']} at {choch_price:.4f}")
        else:
            structure_parts.append(f"CHoCH {choch['direction']}")
    bos = sd.get("bos_details")
    if bos and bos.get("direction"):
        structure_parts.append(f"BOS {bos['direction']} confirmed")
    reasoning.append(f"Trend: {trend}, {' | '.join(structure_parts)}")

    # RSI — Session 354 + 356: direction-specific sub-range interpretation
    if rsi < 30:
        if direction == "SHORT":
            reasoning.append(f"RSI {rsi:.1f} — oversold (bounce risk, penalizes SHORT)")
        else:
            reasoning.append(f"RSI {rsi:.1f} — oversold (favorable dip-buy for LONG)")
    elif rsi > 70:
        if direction == "LONG":
            reasoning.append(f"RSI {rsi:.1f} — overbought (reversal risk, penalizes LONG)")
        else:
            reasoning.append(f"RSI {rsi:.1f} — overbought (favorable for SHORT)")
    elif rsi <= 40:
        if direction == "SHORT":
            reasoning.append(f"RSI {rsi:.1f} — approaching oversold (caution for SHORT)")
        else:
            reasoning.append(f"RSI {rsi:.1f} — leaning oversold (mildly favorable for LONG)")
    elif rsi >= 60:
        if direction == "SHORT":
            reasoning.append(f"RSI {rsi:.1f} — leaning overbought (mildly favorable for SHORT)")
        else:
            reasoning.append(f"RSI {rsi:.1f} — approaching overbought (caution for LONG)")
    else:
        reasoning.append(f"RSI {rsi:.1f} — neutral (40-60, no directional edge)")

    # EMAs
    ema_200 = ema_data.get("ema_200")
    if ema_200:
        pos = ema_data.get("mtf_ema_200", {}).get("position_vs_ema", "?")
        reasoning.append(f"Price {pos} 200 EMA ({ema_200:.4f})")

    alignment = ema_data.get("dual_ema", {}).get("alignment", "?")
    reasoning.append(f"EMA 12/24 alignment: {alignment}")

    # Patterns — Session 354: only show direction-aligned patterns
    # Session 358: chart patterns get richer reasoning with targets/levels
    # Session 359: flag when chart pattern target already exceeded
    dir_pattern_type = "bearish_reversal" if direction == "SHORT" else "bullish_reversal"
    current_price = ohlcv[-1][4] if ohlcv else None
    shown_patterns = 0
    for p in patterns:
        ptype = p.get("pattern_type", "")
        if ptype == dir_pattern_type or ptype == "neutral":
            raw = p.get("raw")
            if raw:
                # Chart pattern (H&S, Double Top/Bottom, Deviation) — show target
                pattern_key = raw.get("pattern", "")
                target = raw.get("target")
                conf = raw.get("confidence", 0)
                vol_ok = raw.get("volume_confirmed", False)
                parts = [f"Pattern: {p.get('pattern_name', '?')} ({p.get('strength', '?')})"]
                if vol_ok:
                    parts.append("volume confirmed")
                parts.append(f"confidence {conf:.0%}")
                # Session 359: check if target already reached or show progress
                if target is not None and current_price is not None and target > 0:
                    if direction == "SHORT" and current_price <= target:
                        parts.append("\u26a0\ufe0f TARGET ALREADY REACHED")
                    elif direction == "LONG" and current_price >= target:
                        parts.append("\u26a0\ufe0f TARGET ALREADY REACHED")
                    elif direction == "SHORT" and entry is not None and entry > target:
                        total_move = entry - target
                        completed = entry - current_price
                        pct_done = (completed / total_move) * 100 if total_move > 0 else 0
                        parts.append(f"{pct_done:.0f}% of move completed to target")
                    elif direction == "LONG" and entry is not None and entry < target:
                        total_move = target - entry
                        completed = current_price - entry
                        pct_done = (completed / total_move) * 100 if total_move > 0 else 0
                        parts.append(f"{pct_done:.0f}% of move completed to target")
                reasoning.append(" — ".join(parts))
            else:
                # Candlestick pattern (CandlestickDetector)
                reasoning.append(
                    f"Pattern: {p.get('pattern_name', '?')} "
                    f"({p.get('strength', '?')}, {ptype})"
                )
            shown_patterns += 1
            if shown_patterns >= 3:
                break

    # Confluence — use the actual number of confluences in the final list,
    # not the counter's "score" (which is a 1-4 category count)
    rating = confluence_result.get("rating", "NONE")
    count = num_confluences or confluence_result.get("score", 0)
    reasoning.append(f"Confluence: {rating} ({count} factors)")

    # Volume
    if volume_data.get("volume_spike"):
        reasoning.append(
            f"Volume spike detected ({volume_data.get('volume_ratio', 0):.1f}x average)"
        )

    # MP-VWAP context (Session 355 P1b)
    volume_profile = volume_profile or {}
    if volume_profile.get("zone"):
        zone = volume_profile.get("zone", "")
        qvwap = volume_profile.get("vwap")
        signal = volume_profile.get("signal", "")
        zone_label = zone.replace("_", " ").title()
        parts = [f"MP-VWAP: Price in {zone_label} zone"]
        if qvwap:
            parts.append(f"QVWAP at {qvwap:.4f}")
        if signal and signal != "NEUTRAL":
            signal_labels = {
                "LONG_ZONE": "Near value area low",
                "SHORT_ZONE": "Near value area high",
            }
            label = signal_labels.get(signal, signal.replace("_", " ").title())
            # Flag when MP-VWAP signal conflicts with trade direction
            conflicts = (
                (direction == "SHORT" and signal == "LONG_ZONE")
                or (direction == "LONG" and signal == "SHORT_ZONE")
            )
            if conflicts:
                label = f"\u26a0\ufe0f {label} (caution for {direction})"
            parts.append(label)
        reasoning.append(" \u2014 ".join(parts))

    # TVEM Band context (Session 355 P1a — L058)
    tvem_data = tvem_data or {}
    tvem_mid = tvem_data.get("tvem_mid")
    if tvem_mid:
        tvem_upper = tvem_data.get("tvem_upper")
        tvem_lower = tvem_data.get("tvem_lower")
        pos = tvem_data.get("price_position", "UNKNOWN")
        tvem_signal = tvem_data.get("signal", "NEUTRAL")
        parts = [f"TVEM Band: Price {pos.replace('_', ' ').lower()}"]
        if tvem_mid:
            parts.append(f"Mid {tvem_mid:.4f}")
        if tvem_signal and tvem_signal != "NEUTRAL":
            signal_label = tvem_signal.replace("_", " ").title()
            parts.append(signal_label)
        reasoning.append(" \u2014 ".join(parts))

        # Direction-aware oversold/overbought warning
        oversold_short = (
            direction == "SHORT"
            and tvem_signal in ("OVERSOLD", "BULLISH_RETEST")
        )
        overbought_long = (
            direction == "LONG"
            and tvem_signal in ("OVERBOUGHT", "BEARISH_RETEST")
        )
        if oversold_short or overbought_long:
            rsi_confirms = (
                (direction == "SHORT" and rsi < 40)
                or (direction == "LONG" and rsi > 60)
            )
            severity = "high" if rsi_confirms else "moderate"
            if severity == "high":
                reasoning.append(
                    f"\u26a0\ufe0f Bounce risk: TVEM {tvem_signal.replace('_',' ').lower()}"
                    f" + RSI {rsi:.1f} \u2014 consider tighter stops or reduced size"
                )
            else:
                reasoning.append(
                    f"\u26a0\ufe0f TVEM {tvem_signal.replace('_',' ').lower()}"
                    f" \u2014 watch for reversal signals"
                )

    # Harmonic patterns (Session 355 P2)
    harmonics = harmonics or []
    for h in harmonics:
        h_dir = h.get("direction", "")
        h_name = h.get("name", "Unknown")
        comp = h.get("completion_pct", 0)
        d_price = h.get("d_price")
        parts = [f"Harmonic: {h_name} ({h_dir})"]
        if d_price:
            parts.append(f"D at {d_price:.4f}")
        parts.append(f"{comp:.0f}% complete")
        reasoning.append(" — ".join(parts))

    # ------------------------------------------------------------------
    # Session 357: Market structure reasoning (trendlines, FVGs, OBs, sweeps, EQH/EQL, equilibrium)
    # ------------------------------------------------------------------

    # Trendline context
    trendline = sd.get("trendline")
    if trendline and trendline.get("detected"):
        touches = trendline.get("touch_count", 0)
        strength = trendline.get("strength", "weak")
        dist = trendline.get("distance_pct", 0)
        if trendline.get("broken"):
            reasoning.append(f"Trendline BROKEN ({touches} touches, {strength}) — trend shift signal")
        elif trendline.get("breakout_imminent"):
            reasoning.append(f"Trendline breakout imminent ({dist:.1f}% away, {touches} touches)")
        else:
            reasoning.append(f"Trendline: {trendline.get('direction', '?')}, {touches} touches, {dist:.1f}% away")

    # FVG context — focus on nearest relevant FVG, not raw counts
    fvg_count = sd.get("fvg_count", 0)
    if fvg_count > 0:
        in_fvg = sd.get("in_fvg_zone", False) if direction == "SHORT" \
            else sd.get("in_bullish_fvg_zone", False)
        # Show nearest direction-aligned FVG if available
        fvg_key = "nearest_bearish_fvg" if direction == "SHORT" else "nearest_bullish_fvg"
        nearest = sd.get(fvg_key)
        if nearest and nearest.get("top") and nearest.get("bottom"):
            fvg_str = (
                f"Nearest {'bearish' if direction == 'SHORT' else 'bullish'} FVG: "
                f"{nearest['bottom']:.4f}-{nearest['top']:.4f}"
            )
            if nearest.get("strength"):
                fvg_str += f" ({nearest['strength']})"
        elif fvg_count <= 5:
            bear_fvgs = sd.get("bearish_fvg_count", 0)
            bull_fvgs = sd.get("bullish_fvg_count", 0)
            fvg_str = f"FVGs: {fvg_count} ({bear_fvgs} bearish, {bull_fvgs} bullish)"
        else:
            fvg_str = f"FVGs: {fvg_count} detected"
        if in_fvg:
            fvg_str += " — price IN FVG zone (imbalance fill expected)"
        reasoning.append(fvg_str)

    # Order Blocks
    bear_obs = sd.get("bearish_ob_count", 0)
    bull_obs = sd.get("bullish_ob_count", 0)
    if bear_obs or bull_obs:
        ob_str = f"Order Blocks: {bear_obs} bearish, {bull_obs} bullish"
        ob = sd.get("unmitigated_bearish_ob" if direction == "SHORT" else "unmitigated_bullish_ob")
        if ob:
            ob_mid = ob.get("midpoint", 0)
            dir_label = "bearish" if direction == "SHORT" else "bullish"
            if ob_mid:
                ob_str += f" — unmitigated {dir_label} OB at {ob_mid:.4f}"
            if ob.get("preceded_by_sweep"):
                ob_str += " (preceded by liquidity sweep)"
        reasoning.append(ob_str)

    # Liquidity Sweeps — filter noise (depth < 0.5% AND weak = not actionable)
    sweeps = sd.get("liquidity_sweeps", [])
    if sweeps:
        recent = sweeps[0]
        depth = recent.get("sweep_depth_pct", 0)
        strength = recent.get("strength", "weak")
        if not (depth < 0.5 and strength == "weak"):
            reasoning.append(
                f"Liquidity sweep: {recent.get('direction', '?')} "
                f"(depth {depth:.1f}%, {strength})"
            )

    # EQH/EQL (equal highs/lows = liquidity targets)
    eqh_count = sd.get("eqh_count", 0)
    eql_count = sd.get("eql_count", 0)
    if eqh_count or eql_count:
        parts = []
        if eqh_count:
            eqh_part = f"{eqh_count} EQH"
            if sd.get("eqh_above_price"):
                eqh_part += " above price (liquidity target)"
            parts.append(eqh_part)
        if eql_count:
            eql_part = f"{eql_count} EQL"
            if sd.get("eql_below_price"):
                eql_part += " below price (liquidity target)"
            parts.append(eql_part)
        reasoning.append(f"Equal levels: {', '.join(parts)}")

    # Equilibrium zone
    eq = sd.get("equilibrium")
    if eq:
        zone = eq.get("zone", "unknown")
        dist_eq = eq.get("distance_to_eq_pct", 0)
        if direction == "SHORT" and zone == "premium":
            reasoning.append(f"Price in premium zone ({dist_eq:.1f}% from equilibrium) — favorable for SHORT")
        elif direction == "LONG" and zone == "discount":
            reasoning.append(f"Price in discount zone ({dist_eq:.1f}% from equilibrium) — favorable for LONG")
        elif zone in ("premium", "discount"):
            # Misaligned: SHORT in discount or LONG in premium
            if (direction == "SHORT" and zone == "discount") or \
               (direction == "LONG" and zone == "premium"):
                reasoning.append(
                    f"\u26a0\ufe0f Price in {zone} zone ({dist_eq:.1f}% from equilibrium)"
                    f" — unfavorable for {direction}"
                )
            else:
                reasoning.append(f"Price in {zone} zone ({dist_eq:.1f}% from equilibrium)")

    # Candle behavior at key levels (Session 357 Phase 2A)
    if ohlcv:
        behaviors = _analyze_candle_behavior_at_levels(
            ohlcv, sr_levels, sd, entry, direction,
        )
        for b in behaviors[:3]:  # top 3 most relevant
            desc = b["description"]
            level_name = b.get("level_name", "")
            # Direction-aware warnings: rejection at support = bad for SHORT,
            # rejection at resistance = bad for LONG
            is_counter = (
                (direction == "SHORT" and "support" in level_name)
                or (direction == "LONG" and "resistance" in level_name)
            )
            if is_counter and b.get("type") == "rejection_wick":
                defender = "buyers" if "support" in level_name else "sellers"
                desc = f"\u26a0\ufe0f {desc} — {defender} defending (caution for {direction})"
            reasoning.append(desc)

    # Multi-candle momentum narrative (Session 357 Phase 2B)
    if ohlcv:
        momentum = _analyze_momentum_narrative(ohlcv, direction)
        if momentum:
            reasoning.append(f"Candle character: {momentum}")

    # S/R context
    n_supports = len(sr_levels.get("supports", []))
    n_resistances = len(sr_levels.get("resistances", []))
    reasoning.append(f"S/R levels: {n_supports} supports, {n_resistances} resistances")

    # DCA context
    if dca is not None and dca > 0:
        avg_entry = (entry + dca) / 2.0
        risk = abs(sl - avg_entry)
        reward = abs(tp - avg_entry)
        eff_rr = reward / risk if risk > 0 else 0.0
        reasoning.append(
            f"DCA {dca:.4f}: blended entry {avg_entry:.4f}, effective R:R {eff_rr:.1f}:1"
        )
        if dca_confluences:
            conf_names = [c["name"] for c in dca_confluences]
            reasoning.append(f"DCA confluences: {', '.join(conf_names)}")
        else:
            reasoning.append("DCA: no confluence alignment with key levels")

    # ------------------------------------------------------------------
    # Session 370+: Macro-SL coherence check + scale-in for LONGs (L112)
    # ------------------------------------------------------------------
    if direction == "LONG" and entry and sl:
        sl_dist_pct = abs(entry - sl) / entry * 100 if entry > 0 else 0

        # 1. SL width check vs nearest support/EMA levels
        nearest_support_below = None
        for s in sr_levels.get("supports", []):
            if s["price"] < entry:
                if nearest_support_below is None or s["price"] > nearest_support_below:
                    nearest_support_below = s["price"]

        ema_200 = ema_data.get("ema_200")
        ema_24_val = ema_data.get("ema_24")

        # Check if SL is above key support (too tight)
        key_level_below = None
        key_level_name = None
        for level, name in [
            (nearest_support_below, "nearest support"),
            (ema_200, "200 EMA"),
            (ema_24_val, "24 EMA"),
        ]:
            if level and level < entry and level < sl:
                # SL is above this key level — it could get swept
                key_level_below = level
                key_level_name = name
                break

        if key_level_below and key_level_name:
            buffer_needed = abs(entry - key_level_below) / entry * 100
            reasoning.append(
                f"\u26a0\ufe0f SL ({sl:.4f}) is above {key_level_name} ({key_level_below:.4f})"
                f" — consider widening to {buffer_needed:.1f}% below entry"
                f" (current SL: {sl_dist_pct:.1f}%) per L055/L112"
            )

        # 2. RSI neutral + LONG = macro uncertain warning
        if 35 < rsi < 65:
            # RSI neutral zone — no clear directional edge for LONG
            alignment = ema_data.get("dual_ema", {}).get("alignment", "")
            ema_misaligned = alignment in ("bearish_aligned", "sandwich")
            if ema_misaligned:
                reasoning.append(
                    f"\u26a0\ufe0f RSI neutral ({rsi:.1f}) + EMA {alignment.replace('_', ' ')}"
                    f" — macro uncertain for LONG, consider scale-in entry"
                    f" (30-50% initial size, add on confirmation) per L112"
                )
            elif sl_dist_pct < 3.0:
                reasoning.append(
                    f"\u26a0\ufe0f RSI neutral ({rsi:.1f}) with tight SL ({sl_dist_pct:.1f}%)"
                    f" — for swing LONG in uncertain macro, consider"
                    f" wider SL (5-8%) or scale-in entry per L112/L039"
                )

    # ------------------------------------------------------------------
    # Priority reorder: pin setup+trend at top, then warnings, then rest
    # ------------------------------------------------------------------
    if len(reasoning) > 2:
        pinned = reasoning[:2]  # setup line + trend/structure line
        rest = reasoning[2:]
        warnings = [r for r in rest if "\u26a0\ufe0f" in r]
        non_warnings = [r for r in rest if "\u26a0\ufe0f" not in r]
        reasoning = pinned + warnings + non_warnings

    return reasoning


# ---------------------------------------------------------------------------
# Smart entry suggestions (P0: stale entry fix)
# ---------------------------------------------------------------------------

def _suggest_entries_near_price(
    current_price: float,
    direction: str,
    sr_levels: dict,
    fib_levels: dict,
    ema_data: dict,
    volume_profile: dict,
) -> list[dict]:
    """
    Suggest up to 3 alternative entry levels near the current price.

    When the user's entry is far from the current price (stale setup),
    this finds nearby technical levels that could serve as better entries.

    For SHORT: suggests levels ABOVE current price (sell into resistance).
    For LONG:  suggests levels BELOW current price (buy into support).

    Returns list of {level, source, distance_pct} sorted by distance.
    """
    if current_price <= 0:
        return []

    candidates: list[dict] = []

    # S/R levels
    for s in sr_levels.get("supports", []):
        price = s.get("price", 0)
        if price > 0:
            candidates.append({"level": price, "source": f"Support ({s.get('strength', 'MODERATE')})"})
    for r in sr_levels.get("resistances", []):
        price = r.get("price", 0)
        if price > 0:
            candidates.append({"level": price, "source": f"Resistance ({r.get('strength', 'MODERATE')})"})

    # Fibonacci levels
    for fib_name, fib_price in fib_levels.items():
        if isinstance(fib_price, (int, float)) and fib_price > 0:
            candidates.append({"level": fib_price, "source": f"Fib {fib_name}"})

    # EMA levels
    for ema_key, label in [("ema_12", "EMA 12"), ("ema_24", "EMA 24"), ("ema_200", "EMA 200")]:
        val = ema_data.get(ema_key)
        if val and isinstance(val, (int, float)) and val > 0:
            candidates.append({"level": val, "source": label})

    # Volume profile levels
    for vp_key, label in [("poc", "POC"), ("vah", "VAH"), ("val", "VAL")]:
        val = volume_profile.get(vp_key)
        if val and isinstance(val, (int, float)) and val > 0:
            candidates.append({"level": val, "source": label})

    # QVWAP if present
    qvwap = volume_profile.get("vwap")
    if qvwap and isinstance(qvwap, (int, float)) and qvwap > 0:
        candidates.append({"level": qvwap, "source": "QVWAP"})

    # Filter by direction: SHORT = above price, LONG = below price
    if direction == "SHORT":
        filtered = [c for c in candidates if c["level"] > current_price]
    else:
        filtered = [c for c in candidates if c["level"] < current_price]

    # Filter by distance: within 5% of current price
    for c in filtered:
        c["distance_pct"] = round(abs(c["level"] - current_price) / current_price * 100, 2)

    nearby = [c for c in filtered if c["distance_pct"] <= 5.0]

    # Sort by distance, select up to 3 with minimum 1.5% spacing
    nearby.sort(key=lambda x: x["distance_pct"])
    selected: list[dict] = []
    for c in nearby:
        if len(selected) >= 3:
            break
        # Check minimum spacing from all already-selected entries
        too_close = False
        for s in selected:
            spacing = abs(c["level"] - s["level"]) / current_price * 100
            if spacing < 1.5:
                too_close = True
                break
        if not too_close:
            selected.append(c)
    return selected


# ---------------------------------------------------------------------------
# Level validation
# ---------------------------------------------------------------------------

class InvalidLevelsError(ValueError):
    """Raised when entry/SL/TP levels are invalid for the given direction."""


def validate_levels(
    direction: str,
    entry: float,
    sl: float,
    tp: float,
    dca: Optional[float] = None,
) -> None:
    """
    Validate that trading levels are consistent with the direction.

    SHORT: SL > Entry > TP
    LONG:  SL < Entry < TP

    Raises InvalidLevelsError if levels are inconsistent.
    """
    if entry <= 0 or sl <= 0 or tp <= 0:
        raise InvalidLevelsError("All levels must be positive")

    if direction == "SHORT":
        if not (sl > entry):
            raise InvalidLevelsError(
                f"SHORT: SL ({sl}) must be above Entry ({entry})"
            )
        if not (entry > tp):
            raise InvalidLevelsError(
                f"SHORT: Entry ({entry}) must be above TP ({tp})"
            )
        if dca is not None and dca > 0:
            if not (sl > dca > tp):
                raise InvalidLevelsError(
                    f"SHORT: DCA ({dca}) must be between SL ({sl}) and TP ({tp})"
                )
    elif direction == "LONG":
        if not (sl < entry):
            raise InvalidLevelsError(
                f"LONG: SL ({sl}) must be below Entry ({entry})"
            )
        if not (entry < tp):
            raise InvalidLevelsError(
                f"LONG: Entry ({entry}) must be below TP ({tp})"
            )
        if dca is not None and dca > 0:
            if not (sl < dca < tp):
                raise InvalidLevelsError(
                    f"LONG: DCA ({dca}) must be between SL ({sl}) and TP ({tp})"
                )
    else:
        raise InvalidLevelsError(f"Direction must be SHORT or LONG, got: {direction}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _compute_mtf_context(
    token_symbol: str,
    token_age_hours: float,
    direction: str,
) -> dict:
    """
    Session 374c: Compute multi-timeframe context using L043 lifecycle-aware TFs.

    Returns {mode, entry_tf, confirm_tf, aligned: bool, reasoning: str}
    or empty dict on failure.
    """
    try:
        from src.analysis.market_structure import MarketStructureAnalyzer

        analyzer = MarketStructureAnalyzer()
        ctx = analyzer._get_entry_timeframe_context(token_age_hours)

        mode = ctx.get("mode", "STANDARD")
        entry_tf = ctx.get("entry_tf", "4h")
        confirm_tf = ctx.get("confirm_tf", "4h")

        # If confirm TF is different from main 4h, fetch and analyze
        aligned = True
        reasoning = f"MTF {mode}: entry={entry_tf}, confirm={confirm_tf}"

        if confirm_tf != "4h":
            confirm_struct = _compute_market_structure(token_symbol, confirm_tf)
            if confirm_struct:
                confirm_label = _classify_market_structure(confirm_struct)

                # Check alignment: confirm TF should agree with direction
                if direction == "SHORT" and confirm_label in ("HH_HL",):
                    aligned = False
                    reasoning += f" | {confirm_tf} structure={confirm_label} DISAGREES with SHORT"
                elif direction == "LONG" and confirm_label in ("LH_LL",):
                    aligned = False
                    reasoning += f" | {confirm_tf} structure={confirm_label} DISAGREES with LONG"
                else:
                    reasoning += f" | {confirm_tf} structure={confirm_label} confirms {direction}"

        return {
            "mode": mode,
            "entry_tf": entry_tf,
            "confirm_tf": confirm_tf,
            "aligned": aligned,
            "reasoning": reasoning,
            "position_multiplier": ctx.get("position_multiplier", 1.0),
        }
    except Exception as e:
        logger.warning(f"MTF context failed (non-fatal): {e}")
        return {}


def build_computed_ta(
    token_symbol: str,
    direction: str,
    entry: float,
    sl: float,
    tp: float,
    dca: Optional[float] = None,
    timeframe: str = "4h",
    token_age_hours: Optional[float] = None,
    preloaded_ohlcv: Optional[list[list]] = None,
) -> TAExtractionResult:
    """
    Build a TAExtractionResult from real market data.

    This is the core orchestrator that replaces screenshot-based GPT extraction.
    It fetches OHLCV data from Binance and runs all existing analysis modules
    to produce a result compatible with the TATransformer scoring pipeline.

    Args:
        token_symbol: Token to analyze (e.g., "SUSHI", "BTC", "ETH")
        direction: "SHORT" or "LONG"
        entry: Entry price level
        sl: Stop loss price level
        tp: Take profit price level (TP1)
        dca: Optional DCA level
        timeframe: Chart timeframe (default "4h")
        token_age_hours: Optional token age for MTF timeframe selection (L043)

    Returns:
        TAExtractionResult ready for TATransformer.transform()

    Raises:
        InvalidLevelsError: If levels are inconsistent with direction
    """
    direction = direction.upper()
    validate_levels(direction, entry, sl, tp, dca)

    logger.info(
        f"Building computed TA for {token_symbol} {direction} "
        f"E={entry} SL={sl} TP={tp} [{timeframe}]"
    )

    # ------------------------------------------------------------------
    # Step 1: Fetch OHLCV data
    # ------------------------------------------------------------------
    ohlcv = preloaded_ohlcv if preloaded_ohlcv is not None else select_ohlcv_source(
        token_symbol,
        timeframe=timeframe,
        limit=200,
    )[0]
    if not ohlcv or len(ohlcv) < 20:
        logger.warning(f"Insufficient OHLCV data for {token_symbol}, returning minimal result")
        return TAExtractionResult(
            entry_levels=[entry],
            stop_loss=sl,
            take_profit_1=tp,
            dca_level=dca,
            trend_direction=direction,
            timeframe=timeframe,
            extraction_confidence=0.3,
            reasoning=[f"Insufficient OHLCV data for {token_symbol}"],
        )

    ohlcv_dicts = _ohlcv_to_dicts(ohlcv)
    current_price = ohlcv[-1][4]

    # ------------------------------------------------------------------
    # Step 2: Run all analysis modules
    # ------------------------------------------------------------------

    # Market structure (CHoCH, BOS, swing points)
    structure_data = _compute_market_structure(token_symbol, timeframe)
    # NOTE: structure_label resolved after EMAs so we can fall back to EMA alignment

    # Candlestick patterns
    patterns = _compute_patterns(ohlcv)

    # Support/Resistance levels
    sr_levels = _compute_sr_levels(ohlcv_dicts, current_price)

    # Volume profile (POC, VAH, VAL + QVWAP, zone, signal via MP-VWAP)
    volume_profile = _compute_volume_profile(ohlcv_dicts, current_price)

    # RSI
    rsi = _compute_rsi(ohlcv)

    # EMAs (12, 24, 200) with alignment classification
    ema_data = _compute_emas(ohlcv)

    # Market structure label (with EMA fallback when swing-point data unavailable)
    structure_label = _classify_market_structure(structure_data, ema_data)

    # Trend classification
    trend = _classify_trend(ema_data, structure_data)

    # Funding rate
    funding_rate = _compute_funding_rate(token_symbol)

    # Volume analysis
    volume_data = _compute_volume_analysis(ohlcv)

    # Fibonacci levels
    fib_levels = _compute_fibonacci(ohlcv, current_price)

    # TVEM Bands (Session 355 P1a — L058)
    tvem_data = _compute_tvem(ohlcv)
    # Stash TVEM levels in volume_profile so confluence builder can use them
    if tvem_data.get("tvem_mid"):
        volume_profile["_tvem_mid"] = tvem_data["tvem_mid"]
        volume_profile["_tvem_upper"] = tvem_data.get("tvem_upper")
        volume_profile["_tvem_lower"] = tvem_data.get("tvem_lower")

    # Harmonic patterns (Session 355 P2 — XABCD)
    harmonics = _detect_harmonics(ohlcv, current_price)
    if harmonics:
        volume_profile["_harmonics"] = harmonics

    # Chart patterns: H&S, Double Top/Bottom, Deviation (Session 358)
    chart_patterns = _compute_chart_patterns(ohlcv, sr_levels)
    patterns.extend(chart_patterns)

    # Session 373b: Sideways market detection at source level (L039)
    sideways_data = _detect_sideways_range(ohlcv)

    # Build pattern_names after merging candlestick + chart patterns
    pattern_names = [p.get("pattern_name", "") for p in patterns]

    # VWAP data for confluence counter (use actual QVWAP when available)
    vwap_data = {}
    qvwap = volume_profile.get("vwap")
    if qvwap and current_price > 0:
        vwap_data["qvwap_distance_pct"] = abs(qvwap - current_price) / current_price * 100
    else:
        poc = volume_profile.get("poc")
        if poc and current_price > 0:
            vwap_data["qvwap_distance_pct"] = abs(poc - current_price) / current_price * 100

    # ------------------------------------------------------------------
    # Step 3: Confluence counting
    # ------------------------------------------------------------------
    confluence_result = _compute_confluence(
        ema_data=ema_data,
        vwap_data=vwap_data,
        sr_levels=sr_levels,
        pattern_names=pattern_names,
        volume_data=volume_data,
        funding_rate=funding_rate,
        ohlcv=ohlcv,
        timeframe=timeframe,
        direction=direction,
    )

    # ------------------------------------------------------------------
    # Step 4: Build confluences list for the pipeline
    # ------------------------------------------------------------------
    confluences = _build_confluences_for_pipeline(
        ema_data=ema_data,
        sr_levels=sr_levels,
        volume_profile=volume_profile,
        fib_levels=fib_levels,
        patterns=patterns,
        confluence_result=confluence_result,
        direction=direction,
        entry=entry,
        current_price=current_price,
        structure_data=structure_data,
    )

    # ------------------------------------------------------------------
    # Step 4b: DCA confluence check
    # ------------------------------------------------------------------
    dca_confluences: list[dict] = []
    if dca is not None and dca > 0:
        dca_confluences = _check_dca_confluence(
            dca=dca,
            sr_levels=sr_levels,
            fib_levels=fib_levels,
            ema_data=ema_data,
            volume_profile=volume_profile,
        )
        # DCA confluences kept separate — they are NOT main confluences
        # and should not inflate the confluence count (Fix 6, Session 360)

    # ------------------------------------------------------------------
    # Step 5: Build reasoning
    # ------------------------------------------------------------------
    reasoning = _build_reasoning(
        direction=direction,
        entry=entry,
        sl=sl,
        tp=tp,
        rsi=rsi,
        ema_data=ema_data,
        structure_label=structure_label,
        trend=trend,
        confluence_result=confluence_result,
        patterns=patterns,
        sr_levels=sr_levels,
        volume_data=volume_data,
        num_confluences=len(confluences),
        dca=dca,
        dca_confluences=dca_confluences,
        volume_profile=volume_profile,
        tvem_data=tvem_data,
        harmonics=harmonics,
        structure_data=structure_data,
        ohlcv=ohlcv,
        sideways_data=sideways_data,
    )

    # ------------------------------------------------------------------
    # Step 6: Calculate extraction confidence
    # ------------------------------------------------------------------
    # High confidence because we use real data (not GPT vision)
    confidence = 0.90

    # Boost if trend aligns with direction
    if (direction == "SHORT" and trend == "BEARISH") or (
        direction == "LONG" and trend == "BULLISH"
    ):
        confidence = min(confidence + 0.05, 1.0)

    # Boost for more confluences
    n_confluences = len(confluences)
    if n_confluences >= 4:
        confidence = min(confidence + 0.05, 1.0)
    elif n_confluences >= 2:
        confidence = min(confidence + 0.02, 1.0)

    # Session 373b: Sideways market confidence penalty (L039)
    if sideways_data.get("is_deep_sideways"):
        confidence = max(confidence - 0.10, 0.50)
    elif sideways_data.get("is_sideways"):
        confidence = max(confidence - 0.05, 0.50)

    # Session 371: SL-above-support penalty (technical setup flaw, not macro)
    if direction == "LONG" and entry and sl:
        for level, _name in [
            (max((s["price"] for s in sr_levels.get("supports", []) if s["price"] < entry), default=None), "support"),
            (ema_data.get("ema_200"), "200 EMA"),
            (ema_data.get("ema_24"), "24 EMA"),
        ]:
            if level and level < entry and level < sl:
                confidence = max(confidence - 0.07, 0.50)
                break

    # Session 354: Entry vs current price distance warning
    # Session 355: Smart entry suggestions when entry is stale
    suggested_entries: list[dict] | None = None
    if current_price and entry > 0:
        entry_distance_pct = abs(entry - current_price) / entry * 100
        if entry_distance_pct > 10:
            reasoning.insert(0,
                f"⚠️ Entry {entry:.4f} is {entry_distance_pct:.1f}% from "
                f"current price {current_price:.4f} — setup may be stale"
            )
            confidence = max(confidence - 0.10, 0.50)
        elif entry_distance_pct > 5:
            reasoning.insert(0,
                f"Entry {entry:.4f} is {entry_distance_pct:.1f}% from "
                f"current price {current_price:.4f}"
            )

        # Suggest alternative entries when entry is >5% away
        if entry_distance_pct > 5:
            suggested_entries = _suggest_entries_near_price(
                current_price=current_price,
                direction=direction,
                sr_levels=sr_levels,
                fib_levels=fib_levels,
                ema_data=ema_data,
                volume_profile=volume_profile,
            )
            if suggested_entries:
                reasoning.append(
                    f"Suggested entries near price: "
                    + ", ".join(
                        f"{s['source']} at {s['level']:.4f} ({s['distance_pct']:.1f}%)"
                        for s in suggested_entries
                    )
                )

    # ------------------------------------------------------------------
    # Step 6b: MTF context (Session 374c — GAP #5)
    # ------------------------------------------------------------------
    if token_age_hours is not None:
        mtf_ctx = _compute_mtf_context(token_symbol, token_age_hours, direction)
        if mtf_ctx:
            reasoning.append(mtf_ctx.get("reasoning", ""))
            if not mtf_ctx.get("aligned", True):
                confidence = max(confidence - 0.05, 0.50)

    # ------------------------------------------------------------------
    # Step 7: Assemble TAExtractionResult
    # ------------------------------------------------------------------
    # Session 374c: Compute TP2/TP3 from R:R multiples (2x, 3x risk)
    risk = abs(entry - sl)
    if risk > 0:
        if direction == "SHORT":
            tp2 = entry - 2 * risk if entry - 2 * risk > 0 else None
            tp3 = entry - 3 * risk if entry - 3 * risk > 0 else None
        else:
            tp2 = entry + 2 * risk
            tp3 = entry + 3 * risk
    else:
        tp2 = None
        tp3 = None

    # Session 354: Filter patterns to direction-aligned only
    aligned_type = "bearish_reversal" if direction == "SHORT" else "bullish_reversal"
    aligned_patterns = [
        p.get("pattern_name", "")
        for p in patterns
        if p.get("pattern_type") == aligned_type or p.get("pattern_type") == "neutral"
    ][:5]

    result = TAExtractionResult(
        entry_levels=[entry],
        stop_loss=sl,
        take_profit_1=tp,
        take_profit_2=tp2,
        take_profit_3=tp3,
        dca_level=dca,
        patterns_detected=aligned_patterns,
        trend_direction=trend,
        timeframe=timeframe,
        market_structure=structure_label,
        extraction_confidence=confidence,
        reasoning=reasoning,
        confluences=confluences,
        dca_confluences=dca_confluences,
        rsi_value=rsi,
        current_price=current_price,
        suggested_entries=suggested_entries,
    )

    logger.info(
        f"Computed TA complete: {token_symbol} {direction} — "
        f"{n_confluences} confluences, confidence {confidence:.2f}, "
        f"structure {structure_label}, trend {trend}"
    )

    return result
