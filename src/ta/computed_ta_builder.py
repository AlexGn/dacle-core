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
    Fetch OHLCV candles from Binance via CCXT.

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

    logger.warning(f"No OHLCV data found for {symbol}")
    return []


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
) -> list[str]:
    """Build human-readable reasoning list for the TA result."""
    reasoning = []

    # Direction and trend
    reasoning.append(
        f"{direction} setup: Entry {entry:.4f}, SL {sl:.4f}, TP {tp:.4f}"
    )
    reasoning.append(f"Trend: {trend}, Market structure: {structure_label}")

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
    dir_pattern_type = "bearish_reversal" if direction == "SHORT" else "bullish_reversal"
    shown_patterns = 0
    for p in patterns:
        ptype = p.get("pattern_type", "")
        if ptype == dir_pattern_type or ptype == "neutral":
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

    # Filter by distance: within 3% of current price
    for c in filtered:
        c["distance_pct"] = round(abs(c["level"] - current_price) / current_price * 100, 2)

    nearby = [c for c in filtered if c["distance_pct"] <= 3.0]

    # Sort by distance, return top 3
    nearby.sort(key=lambda x: x["distance_pct"])
    return nearby[:3]


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

def build_computed_ta(
    token_symbol: str,
    direction: str,
    entry: float,
    sl: float,
    tp: float,
    dca: Optional[float] = None,
    timeframe: str = "4h",
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
    ohlcv = _fetch_ohlcv_binance(token_symbol, timeframe, limit=200)
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
    pattern_names = [p.get("pattern_name", "") for p in patterns]

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
        # Append DCA confluences to main list
        confluences.extend(dca_confluences)

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
    # Step 7: Assemble TAExtractionResult
    # ------------------------------------------------------------------
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
        dca_level=dca,
        patterns_detected=aligned_patterns,
        trend_direction=trend,
        timeframe=timeframe,
        market_structure=structure_label,
        extraction_confidence=confidence,
        reasoning=reasoning,
        confluences=confluences,
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
