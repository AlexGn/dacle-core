"""
Cipher Engine — Market Cipher B Replication for DACLE

Computes the full cipher indicator suite (WaveTrend, MFI, CVD, MACD, Stochastic,
Choppiness) on any OHLCV series and produces a composite CipherSnapshot with a
single actionable signal label.

Designed to run on macro indices (BTC.D, TOTAL, MEME.C, etc.) via the rolling
OHLCV cache built by `src/data/indices_ohlcv_fetcher.py`.

Composite signal rules:
  REVERSAL_UP    — WT long_signal (cross up from oversold <-60) + MFI bullish
  REVERSAL_DOWN  — WT short_signal (cross down from overbought >60) + MFI bearish
  BULLISH_MOMENTUM — WT1 > WT2, MACD histogram positive, no chop
  BEARISH_MOMENTUM — WT1 < WT2, MACD histogram negative, no chop
  CHOPPY         — Choppiness > 61.8 (unreliable / avoid trading)
  NEUTRAL        — No clear confluence
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from src.ta.indicators.wavetrend import calculate_wavetrend
from src.ta.indicators.mfi import calculate_dacle_mfi
from src.ta.indicators.vwap_oscillator import calculate_vwap_oscillator
from src.ta.indicators.cvd import calculate_cvd
from src.ta.indicators.choppiness import calculate_choppiness
from src.analysis.momentum_indicators import calculate_macd, calculate_stochastic

logger = logging.getLogger(__name__)

# Minimum bars required to produce a meaningful snapshot
MIN_BARS_WAVETREND = 32   # n1 + n2 + buffer
MIN_BARS_MFI = 65         # length=60 + buffer
MIN_BARS_CHOP = 20        # period=14 + buffer
MIN_BARS_CVD = 15         # needs enough to detect divergence

CHOPPINESS_THRESHOLD = 61.8
WT_OVERBOUGHT = 60.0
WT_OVERSOLD = -60.0


class CompositeSignal:
    REVERSAL_UP = "REVERSAL_UP"
    REVERSAL_DOWN = "REVERSAL_DOWN"
    BULLISH_MOMENTUM = "BULLISH_MOMENTUM"
    BEARISH_MOMENTUM = "BEARISH_MOMENTUM"
    CHOPPY = "CHOPPY"
    NEUTRAL = "NEUTRAL"


@dataclass
class WaveTrendSnapshot:
    wt1: float
    wt2: float
    long_signal: bool
    short_signal: bool
    zone: str  # "overbought" | "oversold" | "neutral"


@dataclass
class MFISnapshot:
    value: float
    is_bullish: bool
    previous_value: Optional[float] = None
    crossed_above_zero: bool = False
    crossed_below_zero: bool = False


@dataclass
class VWAPSnapshot:
    value: float
    above_zero: bool
    previous_value: Optional[float] = None
    crossed_above_zero: bool = False
    crossed_below_zero: bool = False


@dataclass
class CVDSnapshot:
    divergence_detected: bool
    divergence_type: Optional[str]   # "positive" | "negative" | None
    strength: float
    available: bool  # False when volume data is absent


@dataclass
class MACDSnapshot:
    histogram: float
    direction: str  # "bullish" | "bearish" | "neutral"


@dataclass
class StochasticSnapshot:
    k: float
    d: float
    crossover: Optional[str]  # "bullish" | "bearish" | None
    zone: str  # "overbought" | "oversold" | "neutral"


@dataclass
class MomentumSnapshot:
    wt_delta: float
    previous_wt_delta: Optional[float]
    curved_up: bool
    curved_down: bool
    green_dot: bool
    red_dot: bool
    oversold_reversal: bool
    overbought_reversal: bool


@dataclass
class CipherSnapshot:
    index_key: str
    resolution: str
    timestamp: str           # ISO string of last bar

    # Individual indicators (None if insufficient data)
    wavetrend: Optional[WaveTrendSnapshot] = None
    mfi: Optional[MFISnapshot] = None
    vwap: Optional[VWAPSnapshot] = None
    cvd: Optional[CVDSnapshot] = None
    macd: Optional[MACDSnapshot] = None
    stochastic: Optional[StochasticSnapshot] = None
    momentum: Optional[MomentumSnapshot] = None
    choppiness: Optional[float] = None

    # Composite output
    signal: str = CompositeSignal.NEUTRAL
    confidence: float = 0.0   # 0.0 – 1.0
    reasons: List[str] = field(default_factory=list)

    # Meta
    bars_used: int = 0
    error: Optional[str] = None


def compute_cipher_snapshot(
    index_key: str,
    resolution: str,
    opens: List[float],
    highs: List[float],
    lows: List[float],
    closes: List[float],
    volumes: List[float],
    timestamps: List[str],
) -> CipherSnapshot:
    """
    Run the full cipher suite on a raw OHLCV series and return a CipherSnapshot.

    All list arguments must be the same length and ordered oldest-first.
    volumes may be all-zero for dominance indices — CVD will be skipped.
    """
    n = len(closes)
    ts = timestamps[-1] if timestamps else ""
    snap = CipherSnapshot(index_key=index_key, resolution=resolution, timestamp=ts, bars_used=n)

    if n < MIN_BARS_WAVETREND:
        snap.error = f"insufficient_data: need {MIN_BARS_WAVETREND} bars, have {n}"
        return snap

    # --- WaveTrend ---
    wt = None
    try:
        wt = calculate_wavetrend(highs, lows, closes)
        snap.wavetrend = WaveTrendSnapshot(
            wt1=wt["wt1"],
            wt2=wt["wt2"],
            long_signal=wt["long_signal"],
            short_signal=wt["short_signal"],
            zone=wt["zone"],
        )
    except Exception as e:
        logger.warning(f"[cipher_engine] WaveTrend failed for {index_key}/{resolution}: {e}")

    # --- MFI ---
    if n >= MIN_BARS_MFI:
        try:
            mfi = calculate_dacle_mfi(highs, lows, closes)
            snap.mfi = MFISnapshot(
                value=mfi["latest_mfi"],
                is_bullish=mfi["is_bullish"],
                previous_value=mfi.get("previous_mfi"),
                crossed_above_zero=mfi.get("crossed_above_zero", False),
                crossed_below_zero=mfi.get("crossed_below_zero", False),
            )
        except Exception as e:
            logger.warning(f"[cipher_engine] MFI failed for {index_key}/{resolution}: {e}")

    # --- VWAP oscillator ---
    try:
        vwap = calculate_vwap_oscillator(highs, lows, closes, volumes)
        if vwap["latest_value"] is not None:
            snap.vwap = VWAPSnapshot(
                value=vwap["latest_value"],
                above_zero=vwap["above_zero"],
                previous_value=vwap.get("previous_value"),
                crossed_above_zero=vwap.get("crossed_above_zero", False),
                crossed_below_zero=vwap.get("crossed_below_zero", False),
            )
    except Exception as e:
        logger.warning(f"[cipher_engine] VWAP oscillator failed for {index_key}/{resolution}: {e}")

    # --- CVD ---
    has_volume = any(v != 0.0 for v in volumes)
    if has_volume and n >= MIN_BARS_CVD:
        try:
            ohlcv_dicts = [
                {"open": o, "high": h, "low": l, "close": c, "volume": v}
                for o, h, l, c, v in zip(opens, highs, lows, closes, volumes)
            ]
            cvd_result = calculate_cvd(ohlcv_dicts)
            snap.cvd = CVDSnapshot(
                divergence_detected=cvd_result["divergence_detected"],
                divergence_type=cvd_result.get("divergence_type"),
                strength=cvd_result.get("strength", 0.0),
                available=True,
            )
        except Exception as e:
            logger.warning(f"[cipher_engine] CVD failed for {index_key}/{resolution}: {e}")
            snap.cvd = CVDSnapshot(
                divergence_detected=False, divergence_type=None, strength=0.0, available=False
            )
    else:
        snap.cvd = CVDSnapshot(
            divergence_detected=False, divergence_type=None, strength=0.0, available=False
        )

    # --- MACD ---
    try:
        macd = calculate_macd(closes)
        snap.macd = MACDSnapshot(
            histogram=macd["histogram"],
            direction=macd["direction"],
        )
    except Exception as e:
        logger.warning(f"[cipher_engine] MACD failed for {index_key}/{resolution}: {e}")

    # --- Stochastic ---
    try:
        stoch = calculate_stochastic(highs, lows, closes)
        snap.stochastic = StochasticSnapshot(
            k=stoch["k_value"],
            d=stoch["d_value"],
            crossover=stoch.get("crossover"),
            zone=stoch.get("zone", "neutral"),
        )
    except Exception as e:
        logger.warning(f"[cipher_engine] Stochastic failed for {index_key}/{resolution}: {e}")

    # --- Choppiness ---
    if n >= MIN_BARS_CHOP:
        try:
            ohlcv_ccxt = [
                [ts_i, o, h, l, c, v]
                for ts_i, o, h, l, c, v in zip(timestamps, opens, highs, lows, closes, volumes)
            ]
            snap.choppiness = calculate_choppiness(ohlcv_ccxt)
        except Exception as e:
            logger.warning(f"[cipher_engine] Choppiness failed for {index_key}/{resolution}: {e}")

    # --- Composite Signal ---
    if wt is not None:
        snap.momentum = _build_momentum_snapshot(wt)
    snap.signal, snap.confidence, snap.reasons = _compute_composite(snap)
    return snap


def _build_momentum_snapshot(wt: dict) -> Optional[MomentumSnapshot]:
    """Build explicit momentum-turn state from WaveTrend output."""
    wt1_series = wt.get("wt1_series") or []
    wt2_series = wt.get("wt2_series") or []
    if len(wt1_series) < 3 or len(wt2_series) < 2:
        return None

    latest = wt1_series[-1]
    prev = wt1_series[-2]
    prev2 = wt1_series[-3]
    wt_delta = latest - prev
    prev_delta = prev - prev2
    curved_up = wt_delta > 0 and wt_delta >= prev_delta
    curved_down = wt_delta < 0 and wt_delta <= prev_delta
    green_dot = bool(wt.get("long_signal"))
    red_dot = bool(wt.get("short_signal"))

    return MomentumSnapshot(
        wt_delta=round(wt_delta, 4),
        previous_wt_delta=round(prev_delta, 4),
        curved_up=curved_up,
        curved_down=curved_down,
        green_dot=green_dot,
        red_dot=red_dot,
        oversold_reversal=bool((prev <= WT_OVERSOLD or prev2 <= WT_OVERSOLD) and curved_up),
        overbought_reversal=bool((prev >= WT_OVERBOUGHT or prev2 >= WT_OVERBOUGHT) and curved_down),
    )


def _compute_composite(snap: CipherSnapshot):
    """
    Derive composite signal from indicator results.
    Returns (signal, confidence, reasons).
    """
    reasons = []
    signal = CompositeSignal.NEUTRAL
    confidence = 0.0

    wt = snap.wavetrend
    mfi = snap.mfi
    cvd = snap.cvd
    macd = snap.macd
    chop = snap.choppiness

    # Chop gate — if choppy, stop here regardless of other signals
    if chop is not None and chop > CHOPPINESS_THRESHOLD:
        reasons.append(f"choppiness={chop:.1f}>{CHOPPINESS_THRESHOLD} (ranging)")
        return CompositeSignal.CHOPPY, 0.3, reasons

    # Reversal signals — highest priority, require WT signal + MFI confirmation
    if wt is not None:
        if wt.long_signal:
            reasons.append(f"wt_long_signal (wt1={wt.wt1:.1f} crossed up from oversold)")
            confidence += 0.4
            if mfi is not None and mfi.is_bullish:
                reasons.append(f"mfi_bullish ({mfi.value:.2f})")
                confidence += 0.25
            if cvd is not None and cvd.available and cvd.divergence_type == "positive":
                reasons.append(f"cvd_positive_divergence (strength={cvd.strength:.2f})")
                confidence += 0.2
            if macd is not None and macd.direction == "bullish":
                reasons.append(f"macd_bullish (hist={macd.histogram:.4f})")
                confidence += 0.15
            signal = CompositeSignal.REVERSAL_UP

        elif wt.short_signal:
            reasons.append(f"wt_short_signal (wt1={wt.wt1:.1f} crossed down from overbought)")
            confidence += 0.4
            if mfi is not None and not mfi.is_bullish:
                reasons.append(f"mfi_bearish ({mfi.value:.2f})")
                confidence += 0.25
            if cvd is not None and cvd.available and cvd.divergence_type == "negative":
                reasons.append(f"cvd_negative_divergence (strength={cvd.strength:.2f})")
                confidence += 0.2
            if macd is not None and macd.direction == "bearish":
                reasons.append(f"macd_bearish (hist={macd.histogram:.4f})")
                confidence += 0.15
            signal = CompositeSignal.REVERSAL_DOWN

        else:
            # Momentum — WT trend direction without a fresh crossover signal
            wt_bullish = wt.wt1 > wt.wt2
            macd_bullish = macd is not None and macd.direction == "bullish"
            macd_bearish = macd is not None and macd.direction == "bearish"
            mfi_bullish = mfi is not None and mfi.is_bullish
            mfi_bearish = mfi is not None and not mfi.is_bullish

            bull_votes = sum([wt_bullish, macd_bullish, mfi_bullish])
            bear_votes = sum([not wt_bullish, macd_bearish, mfi_bearish])

            if bull_votes >= 2:
                reasons.append(
                    f"wt_trend_up (wt1={wt.wt1:.1f}>{wt.wt2:.1f}), "
                    f"macd={'bullish' if macd_bullish else 'neutral'}, "
                    f"mfi={'bullish' if mfi_bullish else 'neutral'}"
                )
                signal = CompositeSignal.BULLISH_MOMENTUM
                confidence = 0.25 + (bull_votes - 2) * 0.15
            elif bear_votes >= 2:
                reasons.append(
                    f"wt_trend_down (wt1={wt.wt1:.1f}<{wt.wt2:.1f}), "
                    f"macd={'bearish' if macd_bearish else 'neutral'}, "
                    f"mfi={'bearish' if mfi_bearish else 'neutral'}"
                )
                signal = CompositeSignal.BEARISH_MOMENTUM
                confidence = 0.25 + (bear_votes - 2) * 0.15
            else:
                reasons.append("no_confluence")
                signal = CompositeSignal.NEUTRAL
                confidence = 0.0

    confidence = min(1.0, confidence)
    return signal, confidence, reasons


def run_cipher_on_series(
    index_key: str,
    resolution: str,
    series: Dict[str, List],
) -> CipherSnapshot:
    """
    Convenience wrapper: accepts the dict returned by `load_ohlcv_series()`.

    series keys: opens, highs, lows, closes, volumes, timestamps
    """
    return compute_cipher_snapshot(
        index_key=index_key,
        resolution=resolution,
        opens=series.get("opens", []),
        highs=series.get("highs", []),
        lows=series.get("lows", []),
        closes=series.get("closes", []),
        volumes=series.get("volumes", []),
        timestamps=series.get("timestamps", []),
    )
