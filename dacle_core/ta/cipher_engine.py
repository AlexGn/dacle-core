"""
Cipher Engine — Market Cipher B Replication for DACLE

Computes the full cipher indicator suite (WaveTrend, MFI, CVD, MACD, Stochastic,
Choppiness) on any OHLCV series and produces a composite CipherSnapshot with a
single actionable signal label.

Designed to run on macro indices (BTC.D, TOTAL, MEME.C, etc.) via the rolling
OHLCV cache built by `src/data/indices_ohlcv_fetcher.py`.

Composite signal rules:
  REVERSAL_UP      — WT long_signal (cross up from oversold <-60) + MFI bullish + VWAP cross above zero
  REVERSAL_DOWN    — WT short_signal (cross down from overbought >60) + MFI bearish + VWAP cross below zero
  BULLISH_MOMENTUM — WT1 > WT2, MACD histogram positive, VWAP above zero, no chop
  BEARISH_MOMENTUM — WT1 < WT2, MACD histogram negative, VWAP below zero, no chop
  CHOPPY           — Choppiness > 61.8 (unreliable / avoid trading)
  NEUTRAL          — No clear confluence
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from dacle_core.ta.indicators.wavetrend import calculate_wavetrend
from dacle_core.ta.indicators.mfi import calculate_dacle_mfi
from dacle_core.ta.indicators.mfi_vw import calculate_mfi_vw
from dacle_core.ta.indicators.heikin_ashi import to_heikin_ashi
from dacle_core.ta.indicators.rsi import calculate_rsi
from dacle_core.analysis.momentum_indicators import calculate_macd, calculate_stochastic, detect_rsi_divergence
from dacle_core.ta.indicators.vwap_oscillator import calculate_vwap_oscillator
from dacle_core.ta.indicators.cvd import calculate_cvd
from dacle_core.ta.indicators.choppiness import calculate_choppiness
from dacle_core.analysis.momentum_indicators import calculate_macd, calculate_stochastic

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

    # Gold Signal (WT1 < -80 extreme oversold)
    gold_signal: bool = False
    gold_signal_price: Optional[float] = None

    # Volume-weighted MFI
    mfi_vw: Optional[float] = None
    mfi_vw_is_bullish: bool = False

    # WaveTrend fractal divergence
    wt_divergence: Optional[str] = None       # "bullish" | "bearish" | None
    wt_divergence_strength: float = 0.0

    # RSI divergence
    rsi_divergence: Optional[str] = None       # "bullish" | "bearish" | None
    rsi_divergence_strength: str = "none"

    # Heikin Ashi streak
    ha_bullish_streak: int = 0
    ha_bearish_streak: int = 0

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

    # --- Heikin Ashi ---
    try:
        ha = to_heikin_ashi(opens, highs, lows, closes)
        snap.ha_bullish_streak = ha.get("bullish_streak", 0)
        snap.ha_bearish_streak = ha.get("bearish_streak", 0)
    except Exception as e:
        logger.warning(f"[cipher_engine] Heikin Ashi failed for {index_key}/{resolution}: {e}")

    # --- RSI divergence ---
    try:
        if n >= 35:
            # Compute RSI as a series for divergence detection
            rsi_series: List[float] = []
            # Calculate RSI for each window to build a series
            for i in range(14, n):
                window = closes[i - 14:i + 1]
                gains = []
                losses = []
                for j in range(1, len(window)):
                    chg = window[j] - window[j - 1]
                    if chg > 0:
                        gains.append(chg)
                        losses.append(0)
                    else:
                        gains.append(0)
                        losses.append(abs(chg))
                if len(gains) >= 14:
                    avg_g = sum(gains[-14:]) / 14
                    avg_l = sum(losses[-14:]) / 14
                    rsi_val = 100 - (100 / (1 + avg_g / avg_l)) if avg_l > 0 else 100.0
                    rsi_series.append(rsi_val)
            if len(rsi_series) >= 20:
                rsi_div = detect_rsi_divergence(closes[-len(rsi_series):], rsi_series, direction="BOTH", lookback=20)
                snap.rsi_divergence = rsi_div.get("type")
                snap.rsi_divergence_strength = rsi_div.get("strength", "none")
    except Exception as e:
        logger.warning(f"[cipher_engine] RSI divergence failed for {index_key}/{resolution}: {e}")

    # --- WaveTrend fractal divergence ---
    if wt is not None and wt.get("wt1_series"):
        try:
            wt1_series = wt["wt1_series"]
            div = detect_wt_fractal_divergence(closes, wt1_series, lookback=20)
            snap.wt_divergence = div.get("type")
            snap.wt_divergence_strength = div.get("strength", 0.0)
        except Exception as e:
            logger.warning(f"[cipher_engine] WT fractal divergence failed for {index_key}/{resolution}: {e}")

    # --- Volume-weighted MFI ---
    try:
        mfi_vw_result = calculate_mfi_vw(highs, lows, closes, opens, volumes, length=14)
        snap.mfi_vw = mfi_vw_result.get("latest_value")
        snap.mfi_vw_is_bullish = mfi_vw_result.get("is_bullish", False)
    except Exception as e:
        logger.warning(f"[cipher_engine] VW-MFI failed for {index_key}/{resolution}: {e}")

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
    '''
    Derive composite signal from indicator results.

    Uses multiplicative confirmation scoring:
      base 0.25 * 1.25^aligned * 0.75^conflicting
    clamped to [0.0, 1.0].

    Gold Signal (WT1 < -80 with WT2 rising) bypasses the chop gate
    because extreme oversold is actionable even in ranging markets.
    '''
    reasons = []
    signal = CompositeSignal.NEUTRAL
    confidence = 0.0

    wt = snap.wavetrend
    mfi = snap.mfi
    vwap = snap.vwap
    cvd = snap.cvd
    macd = snap.macd
    chop = snap.choppiness

    # Step 0: Gold Signal -- pre-chop check, extreme oversold is actionable
    if wt is not None and wt.wt1 is not None and wt.wt1 < -80:
        if snap.momentum is not None and snap.momentum.wt_delta > 0:
            snap.gold_signal = True
            reasons.append(
                f"GOLD SIGNAL: WT1={wt.wt1:.1f} < -80 extreme oversold, WT2 rising"
            )
            return CompositeSignal.REVERSAL_UP, 0.80, reasons

    # Step 1: Chop gate
    if chop is not None and chop > CHOPPINESS_THRESHOLD:
        reasons.append(f"choppiness={chop:.1f}>{CHOPPINESS_THRESHOLD} (ranging)")
        return CompositeSignal.CHOPPY, 0.3, reasons

    # Step 2: Reversal signals -- require WT crossover
    if wt is not None:
        if wt.long_signal:
            reasons.append(f"wt_long_signal (wt1={wt.wt1:.1f} crossed up from oversold)")
            signal = CompositeSignal.REVERSAL_UP
        elif wt.short_signal:
            reasons.append(f"wt_short_signal (wt1={wt.wt1:.1f} crossed down from overbought)")
            signal = CompositeSignal.REVERSAL_DOWN

    # Step 3: Multiplicative confidence scoring
    wt_bullish = wt is not None and wt.wt1 is not None and wt.wt2 is not None and wt.wt1 > wt.wt2
    wt_bearish = wt is not None and wt.wt1 is not None and wt.wt2 is not None and wt.wt1 < wt.wt2
    macd_bullish = macd is not None and macd.direction == "bullish"
    macd_bearish = macd is not None and macd.direction == "bearish"
    mfi_bullish = mfi is not None and mfi.is_bullish
    mfi_bearish = mfi is not None and not mfi.is_bullish
    vwap_bullish = vwap is not None and vwap.above_zero
    vwap_bearish = vwap is not None and not vwap.above_zero
    rsi_bullish = snap.rsi_divergence == "bullish"
    rsi_bearish = snap.rsi_divergence == "bearish"
    cvd_bullish = cvd is not None and cvd.available and cvd.divergence_type == "positive"
    cvd_bearish = cvd is not None and cvd.available and cvd.divergence_type == "negative"
    wt_div_bullish = snap.wt_divergence == "bullish"
    wt_div_bearish = snap.wt_divergence == "bearish"
    ha_bullish = snap.ha_bullish_streak >= 2
    ha_bearish = snap.ha_bearish_streak >= 2
    mfi_vw_bullish = snap.mfi_vw_is_bullish
    mfi_vw_bearish = snap.mfi_vw is not None and not snap.mfi_vw_is_bullish

    # Collect votes: +1 bullish, -1 bearish, 0 neutral
    votes = [
        1 if wt_bullish else (-1 if wt_bearish else 0),
        1 if macd_bullish else (-1 if macd_bearish else 0),
        1 if mfi_bullish else (-1 if mfi_bearish else 0),
        1 if vwap_bullish else (-1 if vwap_bearish else 0),
        1 if rsi_bullish else (-1 if rsi_bearish else 0),
        1 if cvd_bullish else (-1 if cvd_bearish else 0),
        1 if wt_div_bullish else (-1 if wt_div_bearish else 0),
        1 if ha_bullish else (-1 if ha_bearish else 0),
        1 if mfi_vw_bullish else (-1 if mfi_vw_bearish else 0),
    ]

    net = sum(votes)
    active = sum(1 for v in votes if v != 0)

    if signal == CompositeSignal.NEUTRAL and active >= 3 and abs(net) >= 2:
        signal = CompositeSignal.BULLISH_MOMENTUM if net > 0 else CompositeSignal.BEARISH_MOMENTUM

    if signal != CompositeSignal.NEUTRAL:
        aligned = active
        conflicting = 0
        for v in votes:
            if v != 0 and ((net > 0 and v < 0) or (net < 0 and v > 0)):
                conflicting += 1
                aligned -= 1
        confidence = 0.25 * (1.25 ** max(0, aligned)) * (0.75 ** conflicting)
        confidence = min(max(confidence, 0.0), 1.0)

        dir_label = "bullish" if net > 0 else "bearish"
        reasons.append(f"{dir_label}_votes ({aligned} aligned, {conflicting} conflicting, {active} active)")
    else:
        reasons.append("no_confluence")
        confidence = 0.0

    return signal, round(confidence, 4), reasons



def detect_wt_fractal_divergence(
    closes: List[float],
    wt1_series: List[float],
    lookback: int = 20,
) -> dict:
    '''Detect 4-bar fractal turning points on WT1 vs price.

    Bearish: price makes higher high (HH) while WT1 makes lower high (LH).
    Bullish: price makes lower low (LL) while WT1 makes higher low (HL).

    Returns {"type": "bullish" | "bearish" | None, "strength": float}.
    '''
    if len(closes) < lookback or len(wt1_series) < lookback:
        return {"type": None, "strength": 0.0}

    price_slice = closes[-lookback:]
    wt_slice = wt1_series[-lookback:]

    def _find_peaks(arr):
        peaks = []
        for i in range(2, len(arr) - 2):
            if arr[i] > arr[i - 1] and arr[i] > arr[i - 2] and arr[i] > arr[i + 1] and arr[i] > arr[i + 2]:
                peaks.append((i, arr[i]))
        return peaks

    def _find_troughs(arr):
        troughs = []
        for i in range(2, len(arr) - 2):
            if arr[i] < arr[i - 1] and arr[i] < arr[i - 2] and arr[i] < arr[i + 1] and arr[i] < arr[i + 2]:
                troughs.append((i, arr[i]))
        return troughs

    price_peaks = _find_peaks(price_slice)
    wt_peaks = _find_peaks(wt_slice)
    price_troughs = _find_troughs(price_slice)
    wt_troughs = _find_troughs(wt_slice)

    # Bearish divergence: most recent price peak > previous, but WT peak < previous
    if len(price_peaks) >= 2 and len(wt_peaks) >= 2:
        pp1, pp2 = price_peaks[-2][1], price_peaks[-1][1]
        wp1, wp2 = wt_peaks[-2][1], wt_peaks[-1][1]
        if pp2 > pp1 and wp2 < wp1:
            return {"type": "bearish", "strength": min(1.0, abs(wp2 - wp1) / max(abs(wp1), 0.01))}

    # Bullish divergence: most recent price trough < previous, but WT trough > previous
    if len(price_troughs) >= 2 and len(wt_troughs) >= 2:
        pt1, pt2 = price_troughs[-2][1], price_troughs[-1][1]
        wt1_val, wt2_val = wt_troughs[-2][1], wt_troughs[-1][1]
        if pt2 < pt1 and wt2_val > wt1_val:
            return {"type": "bullish", "strength": min(1.0, abs(wt2_val - wt1_val) / max(abs(wt1_val), 0.01))}

    return {"type": None, "strength": 0.0}




def run_cipher_on_series(
    index_key: str,
    resolution: str,
    series: Dict[str, List],
) -> CipherSnapshot:
    """
    Convenience wrapper: accepts the dict returned by `load_ohlcv_series()`.

    series keys: opens, highs, lows, closes, volumes, timestamps
    """
    snap = compute_cipher_snapshot(
        index_key=index_key,
        resolution=resolution,
        opens=series.get("opens", []),
        highs=series.get("highs", []),
        lows=series.get("lows", []),
        closes=series.get("closes", []),
        volumes=series.get("volumes", []),
        timestamps=series.get("timestamps", []),
    )

    # Freshness penalty: reduce confidence when cache data is stale
    try:
        from dacle_core.data.cipher_cache_service import get_cache_age_seconds
        age_seconds = get_cache_age_seconds(resolution)
        if age_seconds is not None:
            age_hours = age_seconds / 3600
            if age_hours > 48:
                snap.confidence = 0.0
                snap.reasons.append(f"data {age_hours:.0f}h stale -- confidence zeroed")
            elif age_hours > 24:
                snap.confidence *= 0.5
                snap.reasons.append(f"data {age_hours:.0f}h stale -- 50% penalty applied")
            elif age_hours > 12:
                snap.confidence *= 0.75
                snap.reasons.append(f"data {age_hours:.0f}h stale -- 25% penalty applied")
    except Exception:
        pass

    return snap
