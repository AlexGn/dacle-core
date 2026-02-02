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
            logger.debug(f"{pair} not available: {e}")
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


def _compute_volume_profile(ohlcv_dicts: list[dict]) -> dict:
    """Calculate volume profile (POC, VAH, VAL)."""
    try:
        from src.analysis.volume_profile import VolumeProfileAnalyzer

        analyzer = VolumeProfileAnalyzer()
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
# Structure classification
# ---------------------------------------------------------------------------

def _classify_market_structure(structure_data: dict) -> str:
    """Convert market structure analysis to LH_LL / HH_HL / UNKNOWN."""
    structure = structure_data.get("current_structure", "unknown")
    if structure == "bearish":
        return "LH_LL"
    elif structure == "bullish":
        return "HH_HL"
    elif structure == "ranging":
        return "RANGING"
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
) -> list[str]:
    """Build human-readable reasoning list for the TA result."""
    reasoning = []

    # Direction and trend
    reasoning.append(
        f"{direction} setup: Entry {entry:.4f}, SL {sl:.4f}, TP {tp:.4f}"
    )
    reasoning.append(f"Trend: {trend}, Market structure: {structure_label}")

    # RSI — Session 354: direction-specific interpretation
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
    else:
        reasoning.append(f"RSI {rsi:.1f} — neutral zone")

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

    # S/R context
    n_supports = len(sr_levels.get("supports", []))
    n_resistances = len(sr_levels.get("resistances", []))
    reasoning.append(f"S/R levels: {n_supports} supports, {n_resistances} resistances")

    return reasoning


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
    structure_label = _classify_market_structure(structure_data)

    # Candlestick patterns
    patterns = _compute_patterns(ohlcv)
    pattern_names = [p.get("pattern_name", "") for p in patterns]

    # Support/Resistance levels
    sr_levels = _compute_sr_levels(ohlcv_dicts, current_price)

    # Volume profile (POC, VAH, VAL)
    volume_profile = _compute_volume_profile(ohlcv_dicts)

    # RSI
    rsi = _compute_rsi(ohlcv)

    # EMAs (12, 24, 200) with alignment classification
    ema_data = _compute_emas(ohlcv)

    # Trend classification
    trend = _classify_trend(ema_data, structure_data)

    # Funding rate
    funding_rate = _compute_funding_rate(token_symbol)

    # Volume analysis
    volume_data = _compute_volume_analysis(ohlcv)

    # Fibonacci levels
    fib_levels = _compute_fibonacci(ohlcv, current_price)

    # VWAP data for confluence counter
    vwap_data = {}
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
    )

    logger.info(
        f"Computed TA complete: {token_symbol} {direction} — "
        f"{n_confluences} confluences, confidence {confidence:.2f}, "
        f"structure {structure_label}, trend {trend}"
    )

    return result
