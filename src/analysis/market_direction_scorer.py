"""
Market Direction Scorer — Periodic Market Bias Assessment

Calculates a composite direction bias (BULLISH / NEUTRAL / BEARISH) from 8 weighted
signals. Used by the market_direction_monitor cron script and the /api/macro/market-direction
endpoint to give David a clear "which way is the market likely to break?" assessment.

Signals (all scored -1 to +1):
    1. BTC Trend (20%)       — Price vs EMA20 from Binance 4H
    2. BTC RSI 4H (8%)       — Oversold/overbought momentum
    3. BTCDOM direction (12%) — Falling = alt-friendly, rising = alt-bearish
    4. USDT.D direction (12%) — Falling = risk-on, rising = risk-off
    5. TOTAL3 direction (12%) — Alt market cap trend
    6. Fear & Greed (8%)      — Sentiment extremes
    7. BTC Funding Rate (8%)  — Derivatives positioning
    8. BTC Structure (20%)    — Higher-timeframe structural bias from daily/weekly levels

Data Sources (all free, no API keys):
    - Binance REST API (BTC price, klines, funding)
    - CoinPaprika /v1/global + /v1/tickers (BTCDOM, TOTAL3, USDT.D)
    - Alternative.me (Fear & Greed)
    - Local JSON files (key levels from Sherlock, BTC structure bias)
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import List, Optional

import httpx

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
CONFIG_DIR = PROJECT_ROOT / "config"

_WEIGHTS_CACHE: Optional[dict] = None


def load_weights() -> dict:
    """Load signal weights from config/market_direction_weights.json.

    Returns a dict mapping signal key -> weight (0.0-1.0).
    Falls back to hardcoded defaults if config is missing.
    """
    global _WEIGHTS_CACHE
    if _WEIGHTS_CACHE is not None:
        return _WEIGHTS_CACHE

    config_path = CONFIG_DIR / "market_direction_weights.json"
    defaults = {
        "btc_trend": 0.17, "btc_rsi": 0.07, "btcdom": 0.10,
        "usdt_d": 0.10, "total3": 0.10, "fear_greed": 0.07,
        "funding": 0.08, "btc_structure": 0.17,
        "oi_trend": 0.08, "ls_ratio": 0.06, "volume_profile": 0.05,
        "external_macro": 0.15, "liquidity_fuel": 0.10,
    }
    try:
        if config_path.exists():
            data = json.loads(config_path.read_text())
            weights = data.get("weights", defaults)
            _WEIGHTS_CACHE = weights
            return weights
    except Exception as e:
        logger.debug(f"Failed to load weights config: {e}")
    _WEIGHTS_CACHE = defaults
    return defaults


def _reset_weights_cache() -> None:
    """Reset the weights cache (for testing)."""
    global _WEIGHTS_CACHE
    _WEIGHTS_CACHE = None


# =============================================================================
# Data Types
# =============================================================================

class DirectionBias(str, Enum):
    BULLISH = "BULLISH"
    NEUTRAL = "NEUTRAL"
    BEARISH = "BEARISH"


@dataclass
class SignalResult:
    name: str
    weight: float           # 0.0 - 1.0
    score: float            # -1.0 to +1.0
    value: Optional[float]  # raw observed value
    label: str              # human-readable interpretation
    emoji: str              # color indicator


@dataclass
class KeyLevelProximity:
    name: str
    current: float
    level: float
    distance_pct: float
    status: str             # ABOVE / BELOW / AT_LEVEL
    alert: bool             # True when |distance| < 3%


@dataclass
class DirectionUpdate:
    bias: DirectionBias
    score: float                          # -1.0 to +1.0
    confidence_pct: int                   # 0-100
    signals: List[SignalResult]
    key_levels: dict
    position_implications: dict           # sizing hints for SHORT/LONG
    context_signals: List[SignalResult] = field(default_factory=list)
    shift_detected: bool = False
    previous_bias: Optional[str] = None
    timestamp: str = ""
    btc_price: float = 0.0
    signal_quality: dict = field(default_factory=dict)
    data_quality: dict = field(default_factory=dict)
    economic_calendar: Optional[dict] = None

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        signals_active = sum(1 for s in self.signals if s.score != 0)
        d = {
            "bias": self.bias.value,
            "score": round(self.score, 3),
            "confidence_pct": self.confidence_pct,
            "signals_active": signals_active,
            "signals_total": len(self.signals),
            "signals": [
                {
                    "name": s.name,
                    "weight_pct": int(s.weight * 100),
                    "score": round(s.score, 2),
                    "value": s.value,
                    "label": s.label,
                    "emoji": s.emoji,
                }
                for s in self.signals
            ],
            "context_signals": [
                {
                    "name": s.name,
                    "weight_pct": int(s.weight * 100),
                    "score": round(s.score, 2),
                    "value": s.value,
                    "label": s.label,
                    "emoji": s.emoji,
                }
                for s in self.context_signals
            ],
            "key_levels": self.key_levels,
            "position_implications": self.position_implications,
            "shift_detected": self.shift_detected,
            "previous_bias": self.previous_bias,
            "timestamp": self.timestamp,
            "btc_price": self.btc_price,
            "signal_quality": self.signal_quality,
            "data_quality": self.data_quality,
            "narrative": generate_narrative_summary(self),
            "economic_calendar": self.economic_calendar,
            "regime": classify_regime(
                bias=self.bias,
                score=self.score,
                signals_agreeing=sum(
                    1 for s in self.signals
                    if s.score != 0 and (
                        (s.score > 0 and self.score > 0) or (s.score < 0 and self.score < 0)
                    )
                ),
                signals_total=len(self.signals),
            ),
        }
        return d


# =============================================================================
# Signal Scorers
# =============================================================================

def _score_btc_trend(trend: str, price: float, ema20: float) -> SignalResult:
    """BTC Trend — weight from config (single-EMA backward compatibility)."""
    w = load_weights()["btc_trend"]
    trend_upper = (trend or "").upper()
    if trend_upper == "UPTREND" or (price and ema20 and price > ema20):
        return SignalResult("BTC Trend", w, 1.0, price, f"UPTREND (${price:,.0f})", "🟢")
    elif trend_upper == "DOWNTREND" or (price and ema20 and price < ema20):
        return SignalResult("BTC Trend", w, -1.0, price, f"DOWNTREND (${price:,.0f})", "🔴")
    return SignalResult("BTC Trend", w, 0.0, price, f"SIDEWAYS (${price:,.0f})", "🟡")


def _score_btc_trend_mtf(
    price: float,
    ema20_4h: float,
    ema50_4h: float,
    ema20_1d: float,
) -> SignalResult:
    """Multi-TF BTC Trend — scores alignment across 3 EMAs.

    All EMAs aligned bullish (price above all): +1.0
    All EMAs aligned bearish (price below all): -1.0
    Partial alignment: ±0.5 based on majority
    No data: 0.0
    """
    w = load_weights()["btc_trend"]

    if not price or not any([ema20_4h, ema50_4h, ema20_1d]):
        return SignalResult("BTC Trend", w, 0.0, price, "N/A (insufficient data)", "⚪")

    # Count bullish/bearish alignment
    checks = []
    labels = []
    if ema20_4h:
        above = price > ema20_4h
        checks.append(1 if above else -1)
        labels.append(f"EMA20(4H) {'✓' if above else '✗'}")
    if ema50_4h:
        above = price > ema50_4h
        checks.append(1 if above else -1)
        labels.append(f"EMA50(4H) {'✓' if above else '✗'}")
    if ema20_1d:
        above = price > ema20_1d
        checks.append(1 if above else -1)
        labels.append(f"EMA20(1D) {'✓' if above else '✗'}")

    if not checks:
        return SignalResult("BTC Trend", w, 0.0, price, "N/A", "⚪")

    total = sum(checks)
    n = len(checks)

    if total == n:  # All bullish
        score = 1.0
        trend = "UPTREND"
        emoji = "🟢"
    elif total == -n:  # All bearish
        score = -1.0
        trend = "DOWNTREND"
        emoji = "🔴"
    elif total > 0:  # Majority bullish
        score = 0.5
        trend = "MIXED-BULLISH"
        emoji = "🟢"
    elif total < 0:  # Majority bearish
        score = -0.5
        trend = "MIXED-BEARISH"
        emoji = "🔴"
    else:  # Split
        score = 0.0
        trend = "SIDEWAYS"
        emoji = "🟡"

    label = f"{trend} (${price:,.0f}) — {', '.join(labels)}"
    return SignalResult("BTC Trend", w, score, price, label, emoji)


def _score_btc_rsi(rsi: Optional[float]) -> SignalResult:
    """BTC RSI(4H) — continuous proportional scoring.

    Maps RSI to a -1.0 to +1.0 score:
      RSI 50 = 0.0 (neutral midpoint)
      RSI 100 = +1.0, RSI 0 = -1.0
      Dead zone: RSI 40-60 = 0.0 (noise)
    Outside dead zone, linearly interpolate toward extremes.
    """
    w = load_weights()["btc_rsi"]
    if rsi is None:
        return SignalResult("BTC RSI", w, 0.0, None, "N/A", "⚪")

    # Continuous scoring with dead zone at 40-60
    if 40 <= rsi <= 60:
        score = 0.0
        label = f"{rsi:.1f} — Neutral"
        emoji = "🟡"
    elif rsi > 60:
        # Linear from 0.0 at 60 to 1.0 at 100
        score = min((rsi - 60) / 40, 1.0)
        label = f"{rsi:.1f} — {'Extreme Greed' if rsi >= 80 else 'Strong'}"
        emoji = "🟢"
    else:  # rsi < 40
        # Linear from 0.0 at 40 to -1.0 at 0
        score = max(-(40 - rsi) / 40, -1.0)
        label = f"{rsi:.1f} — {'Extreme Fear' if rsi <= 20 else 'Weak'}"
        emoji = "🔴"

    return SignalResult("BTC RSI", w, round(score, 3), round(rsi, 1), label, emoji)


def _score_btcdom(change_24h: Optional[float], value: Optional[float]) -> SignalResult:
    """BTCDOM direction — continuous scoring. Falling BTCDOM = bullish for alts.

    Dead zone: |change| < 0.3% = 0.0
    Outside: linearly scale toward ±1.0, capped at ±2.0% change.
    """
    w = load_weights()["btcdom"]
    if change_24h is None:
        return SignalResult("BTCDOM", w, 0.0, None, "N/A", "⚪")

    if abs(change_24h) < 0.3:
        return SignalResult("BTCDOM", w, 0.0, round(value or 0, 1),
                            f"Flat {change_24h:+.1f}%/24h", "🟡")

    # Falling BTCDOM is bullish for alts (inverted)
    # Scale: -0.3% → +0.0, -2.0% → +1.0 (linear)
    # Scale: +0.3% → -0.0, +2.0% → -1.0
    if change_24h < 0:
        score = min((abs(change_24h) - 0.3) / 1.7, 1.0)
        emoji = "🟢"
        label = f"Falling {change_24h:+.1f}%/24h"
    else:
        score = -min((change_24h - 0.3) / 1.7, 1.0)
        emoji = "🔴"
        label = f"Rising {change_24h:+.1f}%/24h"

    return SignalResult("BTCDOM", w, round(score, 3), round(value or 0, 1), label, emoji)


def _score_usdt_d(change_24h: Optional[float], value: Optional[float]) -> SignalResult:
    """USDT.D direction — continuous scoring. Falling = risk-on (bullish).

    Dead zone: |change| < 0.2% = 0.0
    Outside: linearly scale toward ±1.0, capped at ±1.0% change.
    """
    w = load_weights()["usdt_d"]
    if change_24h is None:
        return SignalResult("USDT.D", w, 0.0, None, "N/A", "⚪")

    if abs(change_24h) < 0.2:
        return SignalResult("USDT.D", w, 0.0, round(value or 0, 2),
                            f"Flat {change_24h:+.2f}%/24h", "🟡")

    # Falling USDT.D = risk-on = bullish (inverted)
    if change_24h < 0:
        score = min((abs(change_24h) - 0.2) / 0.8, 1.0)
        label = f"Falling {change_24h:+.2f}%/24h (risk-on)"
        emoji = "🟢"
    else:
        score = -min((change_24h - 0.2) / 0.8, 1.0)
        label = f"Rising {change_24h:+.2f}%/24h (risk-off)"
        emoji = "🔴"

    return SignalResult("USDT.D", w, round(score, 3), round(value or 0, 2), label, emoji)


def _score_total3(change_24h: Optional[float], value_b: Optional[float]) -> SignalResult:
    """TOTAL3 direction — continuous scoring. Rising = bullish for alts.

    Dead zone: |change| < 1.0% = 0.0
    Outside: linearly scale toward ±1.0, capped at ±5.0% change.
    """
    w = load_weights()["total3"]
    if change_24h is None:
        return SignalResult("TOTAL3", w, 0.0, None, "N/A", "⚪")

    if abs(change_24h) < 1.0:
        return SignalResult("TOTAL3", w, 0.0, round(value_b or 0, 0),
                            f"Flat {change_24h:+.1f}%/24h", "🟡")

    if change_24h > 0:
        score = min((change_24h - 1.0) / 4.0, 1.0)
        label = f"Rising {change_24h:+.1f}%/24h"
        emoji = "🟢"
    else:
        score = -min((abs(change_24h) - 1.0) / 4.0, 1.0)
        label = f"Falling {change_24h:+.1f}%/24h"
        emoji = "🔴"

    return SignalResult("TOTAL3", w, round(score, 3), round(value_b or 0, 0), label, emoji)


def compute_fg_velocity(history: List) -> float:
    """Compute Fear & Greed velocity from 7-day history.

    Args:
        history: List of F&G values (oldest to newest, ideally 7 entries).

    Returns:
        Velocity as normalized float (-1.0 to +1.0).
        Positive = rising (bullish modifier), negative = falling (bearish modifier).
    """
    if not history or len(history) < 2:
        return 0.0
    # Simple: (last - first) / range, normalized by max possible change (100)
    delta = history[-1] - history[0]
    return max(-1.0, min(1.0, delta / 100.0))


def _score_fear_greed(value: Optional[int], velocity: float = 0.0) -> SignalResult:
    """Fear & Greed — continuous proportional scoring with velocity modifier.

    Maps F&G to a -1.0 to +1.0 score:
      50 = 0.0 (neutral midpoint)
      Dead zone: 34-55 = 0.0
      Outside: linearly interpolate toward ±1.0
    Velocity modifier (±0.3 max) shifts score when F&G is changing rapidly.
    """
    w = load_weights()["fear_greed"]
    if value is None:
        return SignalResult("Fear & Greed", w, 0.0, None, "N/A", "⚪")

    # Continuous scoring with dead zone at 34-55
    if 34 < value < 55:
        base_score = 0.0
        label_type = "Neutral"
        emoji = "🟡"
    elif value >= 55:
        # Linear from 0.0 at 55 to 1.0 at 100
        base_score = min((value - 55) / 45, 1.0)
        label_type = "Extreme Greed" if value >= 75 else "Greed"
        emoji = "🟢"
    else:  # value <= 34
        # Linear from 0.0 at 34 to -1.0 at 0
        base_score = max(-(34 - value) / 34, -1.0)
        label_type = "Extreme Fear" if value <= 20 else "Fear"
        emoji = "🔴"

    # Apply velocity modifier (capped at ±0.3)
    vel_modifier = max(-0.3, min(0.3, velocity))
    score = max(-1.0, min(1.0, base_score + vel_modifier))

    vel_label = ""
    if abs(velocity) > 0.05:
        vel_label = f", {'rising' if velocity > 0 else 'falling'} fast"

    return SignalResult(
        "Fear & Greed", w, round(score, 3), value,
        f"{value} — {label_type}{vel_label}", emoji,
    )


def _score_funding(rate_pct: Optional[float]) -> SignalResult:
    """BTC Funding Rate — weight from config."""
    w = load_weights()["funding"]
    if rate_pct is None:
        return SignalResult("Funding", w, 0.0, None, "N/A", "⚪")
    if rate_pct > 0.005:
        return SignalResult("Funding", w, 1.0, round(rate_pct, 4),
                            f"{rate_pct:.4f}% — Positive (longs paying)", "🟢")
    elif rate_pct < -0.01:
        return SignalResult("Funding", w, -1.0, round(rate_pct, 4),
                            f"{rate_pct:.4f}% — Negative (crowded shorts)", "🔴")
    return SignalResult("Funding", w, 0.0, round(rate_pct, 4),
                        f"{rate_pct:.4f}% — Neutral", "🟡")


def _score_btc_structure(btc_price: float, structure_bias: str, structure_shift: float) -> SignalResult:
    """BTC Structure — weight from config. Higher-timeframe structural bias from daily/weekly levels."""
    w = load_weights()["btc_structure"]
    bias_upper = (structure_bias or "").upper()

    # Missing data: no price or no bias
    if not btc_price or not bias_upper:
        return SignalResult("BTC Structure", w, 0.0, None, "N/A", "⚪")

    if bias_upper == "NEUTRAL":
        return SignalResult("BTC Structure", w, 0.0, btc_price,
                            f"NEUTRAL (${btc_price:,.0f})", "🟡")

    # Determine distance from MSS (market structure shift level)
    if structure_shift and structure_shift > 0:
        distance_pct = ((btc_price - structure_shift) / structure_shift) * 100
    else:
        distance_pct = None

    if bias_upper == "BEARISH":
        if distance_pct is not None and distance_pct <= -10.0:
            score = -1.0
            emoji = "🔴"
            label = f"BEARISH ${btc_price:,.0f} ({distance_pct:+.1f}% from MSS ${structure_shift:,.0f})"
        elif distance_pct is not None and distance_pct <= -5.0:
            score = -0.7
            emoji = "🔴"
            label = f"BEARISH ${btc_price:,.0f} ({distance_pct:+.1f}% from MSS ${structure_shift:,.0f})"
        else:
            score = -0.5
            emoji = "🟠"
            if distance_pct is not None:
                label = f"BEARISH ${btc_price:,.0f} ({distance_pct:+.1f}% from MSS ${structure_shift:,.0f})"
            else:
                label = f"BEARISH ${btc_price:,.0f} (no MSS level)"
        return SignalResult("BTC Structure", w, score, btc_price, label, emoji)

    if bias_upper == "BULLISH":
        if distance_pct is not None and distance_pct >= 10.0:
            score = 1.0
            emoji = "🟢"
            label = f"BULLISH ${btc_price:,.0f} ({distance_pct:+.1f}% from MSS ${structure_shift:,.0f})"
        elif distance_pct is not None and distance_pct >= 5.0:
            score = 0.7
            emoji = "🟢"
            label = f"BULLISH ${btc_price:,.0f} ({distance_pct:+.1f}% from MSS ${structure_shift:,.0f})"
        else:
            score = 0.5
            emoji = "🟡"
            if distance_pct is not None:
                label = f"BULLISH ${btc_price:,.0f} ({distance_pct:+.1f}% from MSS ${structure_shift:,.0f})"
            else:
                label = f"BULLISH ${btc_price:,.0f} (no MSS level)"
        return SignalResult("BTC Structure", w, score, btc_price, label, emoji)

    # Unknown bias string
    return SignalResult("BTC Structure", w, 0.0, btc_price,
                        f"{bias_upper} (${btc_price:,.0f})", "🟡")


# =============================================================================
# Derivatives Signals (Phase 2)
# =============================================================================

# OI change threshold: only classify if |OI change| > 1%
_OI_CHANGE_THRESHOLD = 1.0


def _score_oi_trend(
    oi_change_pct: Optional[float], price_change_pct: Optional[float]
) -> SignalResult:
    """BTC OI Trend — 4-quadrant matrix of OI change vs price change.

    OI↑ + Price↑ = Confirmation (strong bullish, new longs entering) = +1.0
    OI↑ + Price↓ = Distribution (strong bearish, new shorts entering) = -1.0
    OI↓ + Price↑ = Short squeeze / weak longs covering = +0.5
    OI↓ + Price↓ = Liquidation / trend exhausting = -0.5
    Small OI change = Neutral = 0.0
    """
    w = load_weights().get("oi_trend", 0.08)

    if oi_change_pct is None or price_change_pct is None:
        return SignalResult("OI Trend", w, 0.0, None, "N/A", "⚪")

    oi_abs = abs(oi_change_pct)

    # If OI change is too small, neutral
    if oi_abs < _OI_CHANGE_THRESHOLD:
        return SignalResult(
            "OI Trend", w, 0.0, round(oi_change_pct, 2),
            f"OI {oi_change_pct:+.1f}% — Flat", "🟡",
        )

    oi_up = oi_change_pct > 0
    price_up = price_change_pct > 0

    if oi_up and price_up:
        return SignalResult(
            "OI Trend", w, 1.0, round(oi_change_pct, 2),
            f"OI {oi_change_pct:+.1f}% + Price {price_change_pct:+.1f}% — Confirmation",
            "🟢",
        )
    elif oi_up and not price_up:
        return SignalResult(
            "OI Trend", w, -1.0, round(oi_change_pct, 2),
            f"OI {oi_change_pct:+.1f}% + Price {price_change_pct:+.1f}% — Distribution",
            "🔴",
        )
    elif not oi_up and price_up:
        return SignalResult(
            "OI Trend", w, 0.5, round(oi_change_pct, 2),
            f"OI {oi_change_pct:+.1f}% + Price {price_change_pct:+.1f}% — Short Squeeze",
            "🟢",
        )
    else:  # OI down, price down
        return SignalResult(
            "OI Trend", w, -0.5, round(oi_change_pct, 2),
            f"OI {oi_change_pct:+.1f}% + Price {price_change_pct:+.1f}% — Liquidation",
            "🔴",
        )


def _score_ls_ratio(ratio: Optional[float]) -> SignalResult:
    """BTC Long/Short Account Ratio — Contrarian positioning signal.

    Ratio >= 2.0 = Crowded longs → contrarian bearish (-1.0)
    Ratio 1.5-2.0 = Moderately long-biased → slight bearish (-0.5)
    Ratio 0.7-1.5 = Balanced → neutral (0.0)
    Ratio 0.5-0.7 = Moderately short-biased → slight bullish (+0.5)
    Ratio <= 0.5 = Crowded shorts → contrarian bullish (+1.0)
    """
    w = load_weights().get("ls_ratio", 0.06)

    if ratio is None:
        return SignalResult("L/S Ratio", w, 0.0, None, "N/A", "⚪")

    if ratio >= 2.0:
        return SignalResult(
            "L/S Ratio", w, -1.0, round(ratio, 2),
            f"{ratio:.2f} — Crowded Longs (contrarian bearish)", "🔴",
        )
    elif ratio >= 1.5:
        return SignalResult(
            "L/S Ratio", w, -0.5, round(ratio, 2),
            f"{ratio:.2f} — Moderately Long", "🟠",
        )
    elif ratio <= 0.5:
        return SignalResult(
            "L/S Ratio", w, 1.0, round(ratio, 2),
            f"{ratio:.2f} — Crowded Shorts (squeeze risk)", "🟢",
        )
    elif ratio <= 0.7:
        return SignalResult(
            "L/S Ratio", w, 0.5, round(ratio, 2),
            f"{ratio:.2f} — Moderately Short", "🟢",
        )
    else:
        return SignalResult(
            "L/S Ratio", w, 0.0, round(ratio, 2),
            f"{ratio:.2f} — Balanced", "🟡",
        )


def _score_funding_enhanced(rates: Optional[list] = None) -> SignalResult:
    """Enhanced BTC Funding Rate — scores on level AND trend direction.

    Takes a list of recent funding rate snapshots (ideally 8 = 24h at 8h intervals).
    Combines:
      - Level: current rate vs thresholds (±0.005%, ±0.01%)
      - Direction: is the rate rising or falling over the period?

    Score matrix:
      High funding + rising  → +1.0 (strong bullish, longs aggressively paying)
      High funding + falling → +0.5 (bullish weakening)
      Low/negative + falling → -1.0 (strong bearish, shorts aggressively paying)
      Low/negative + rising  → -0.5 (bearish weakening)
      Neutral level          →  0.0
    """
    w = load_weights().get("funding", 0.08)

    if not rates:
        return SignalResult("Funding", w, 0.0, None, "N/A", "⚪")

    current_rate = rates[-1]

    # Determine direction from trend
    if len(rates) >= 2:
        first_half = sum(rates[:len(rates) // 2]) / max(len(rates) // 2, 1)
        second_half = sum(rates[len(rates) // 2:]) / max(len(rates) - len(rates) // 2, 1)
        trend_diff = second_half - first_half
        rising = trend_diff > 0.001   # threshold for "rising"
        falling = trend_diff < -0.001  # threshold for "falling"
    else:
        rising = False
        falling = False

    # Level classification
    is_high = current_rate > 0.005
    is_negative = current_rate < -0.01
    is_neutral = not is_high and not is_negative

    if is_neutral:
        trend_label = "Rising" if rising else "Falling" if falling else "Flat"
        return SignalResult(
            "Funding", w, 0.0, round(current_rate, 4),
            f"{current_rate:.4f}% — Neutral ({trend_label})", "🟡",
        )

    if is_high:
        if rising:
            score = 1.0
            label = f"{current_rate:.4f}% — Positive & Rising (longs aggressively paying)"
            emoji = "🟢"
        else:
            score = 0.5
            label = f"{current_rate:.4f}% — Positive but {'Falling' if falling else 'Flat'}"
            emoji = "🟢"
    else:  # is_negative
        if falling:
            score = -1.0
            label = f"{current_rate:.4f}% — Negative & Falling (shorts aggressively paying)"
            emoji = "🔴"
        else:
            score = -0.5
            label = f"{current_rate:.4f}% — Negative but {'Rising' if rising else 'Flat'}"
            emoji = "🔴"

    return SignalResult("Funding", w, score, round(current_rate, 4), label, emoji)


# =============================================================================
# Context Indicators (weight=0, informational only)
# =============================================================================

def _context_total1(total1_t: Optional[float], change_24h: Optional[float]) -> SignalResult:
    """TOTAL1 (total crypto MC) — Context indicator. Rising = bullish."""
    if total1_t is None or change_24h is None:
        return SignalResult("TOTAL1", 0.0, 0.0, None, "N/A", "⚪")
    label = f"${total1_t:.2f}T ({change_24h:+.1f}%/24h)"
    if change_24h > 1.0:
        return SignalResult("TOTAL1", 0.0, 1.0, round(total1_t, 2), label, "🟢")
    elif change_24h < -1.0:
        return SignalResult("TOTAL1", 0.0, -1.0, round(total1_t, 2), label, "🔴")
    return SignalResult("TOTAL1", 0.0, 0.0, round(total1_t, 2), label, "🟡")


def _context_total2(total2_b: Optional[float], change_24h: Optional[float]) -> SignalResult:
    """TOTAL2 (total ex-BTC) — Context indicator. Rising = bullish for alts."""
    if total2_b is None or change_24h is None:
        return SignalResult("TOTAL2", 0.0, 0.0, None, "N/A", "⚪")
    label = f"${total2_b:.0f}B ({change_24h:+.1f}%/24h)"
    if change_24h > 1.0:
        return SignalResult("TOTAL2", 0.0, 1.0, round(total2_b, 0), label, "🟢")
    elif change_24h < -1.0:
        return SignalResult("TOTAL2", 0.0, -1.0, round(total2_b, 0), label, "🔴")
    return SignalResult("TOTAL2", 0.0, 0.0, round(total2_b, 0), label, "🟡")


def _context_others_d(others_d_val: Optional[float], change_pp: Optional[float]) -> SignalResult:
    """Others.D (altcoin dominance ex-BTC/ETH) — Context indicator. Rising = bullish for alts."""
    if others_d_val is None or change_pp is None:
        return SignalResult("Others.D", 0.0, 0.0, None, "N/A", "⚪")
    label = f"{others_d_val:.1f}% ({change_pp:+.2f}pp/24h)"
    if change_pp > 0.3:
        return SignalResult("Others.D", 0.0, 1.0, round(others_d_val, 1), label, "🟢")
    elif change_pp < -0.3:
        return SignalResult("Others.D", 0.0, -1.0, round(others_d_val, 1), label, "🔴")
    return SignalResult("Others.D", 0.0, 0.0, round(others_d_val, 1), label, "🟡")


def _context_stable_d(stable_d_val: Optional[float], change_pp: Optional[float]) -> SignalResult:
    """STABLE.C.D (stablecoin dominance) — Context indicator. Rising = risk-off (bearish)."""
    if stable_d_val is None or change_pp is None:
        return SignalResult("STABLE.C.D", 0.0, 0.0, None, "N/A", "⚪")
    if change_pp < -0.1:
        label = f"{stable_d_val:.1f}% ({change_pp:+.2f}pp/24h, risk-on)"
        return SignalResult("STABLE.C.D", 0.0, 1.0, round(stable_d_val, 1), label, "🟢")
    elif change_pp > 0.1:
        label = f"{stable_d_val:.1f}% ({change_pp:+.2f}pp/24h, risk-off)"
        return SignalResult("STABLE.C.D", 0.0, -1.0, round(stable_d_val, 1), label, "🔴")
    label = f"{stable_d_val:.1f}% ({change_pp:+.2f}pp/24h)"
    return SignalResult("STABLE.C.D", 0.0, 0.0, round(stable_d_val, 1), label, "🟡")


# =============================================================================
# Key Level Proximity
# =============================================================================

def _calc_proximity(name: str, current: float, level: float) -> KeyLevelProximity:
    """Calculate distance between current value and a key level."""
    if level == 0:
        return KeyLevelProximity(name, current, level, 0.0, "UNKNOWN", False)
    distance_pct = ((current - level) / level) * 100
    if abs(distance_pct) < 0.5:
        status = "AT_LEVEL"
    elif distance_pct > 0:
        status = "ABOVE"
    else:
        status = "BELOW"
    alert = abs(distance_pct) < 3.0
    return KeyLevelProximity(name, round(current, 2), round(level, 2),
                             round(distance_pct, 1), status, alert)


def _build_key_levels(
    btc_price: float,
    btc_levels: dict,
    btcdom_value: float,
    sherlock_levels: dict,
    total3_value_b: float,
) -> dict:
    """Build key level proximity data from available levels."""
    result = {"btc": [], "btcdom": [], "total3": []}

    # BTC structure levels
    if btc_price and btc_levels:
        for name, level in btc_levels.items():
            if isinstance(level, (int, float)) and level > 0:
                prox = _calc_proximity(name.replace("_", " ").title(), btc_price, level)
                result["btc"].append(asdict(prox))

    # BTCDOM levels
    sl = sherlock_levels.get("btcdom", {})
    if btcdom_value and sl:
        for name in ["resistance_high", "resistance_low"]:
            if name in sl and sl[name]:
                prox = _calc_proximity(name.replace("_", " ").title(), btcdom_value, sl[name])
                result["btcdom"].append(asdict(prox))

    # TOTAL3 levels
    sl_t3 = sherlock_levels.get("total3", {})
    if total3_value_b and sl_t3:
        for name in ["support", "resistance"]:
            if name in sl_t3 and sl_t3[name]:
                prox = _calc_proximity(name.title(), total3_value_b, sl_t3[name])
                result["total3"].append(asdict(prox))

    return result


# =============================================================================
# Main Calculation
# =============================================================================

def _env_flag(name: str, default: bool = False) -> bool:
    """Parse boolean env var flags with safe defaults."""
    val = os.getenv(name)
    if val is None:
        return default
    return str(val).strip().lower() in {"1", "true", "yes", "on"}


def _parse_iso8601(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _load_external_indices_snapshot() -> dict:
    """Load external macro snapshot and compute staleness metadata."""
    external_indices_path = DATA_DIR / "macro" / "external_indices.json"
    snapshot = {
        "available": False,
        "stale": True,
        "source": "UNAVAILABLE",
        "dxy_change_pct": None,
        "ndx_change_pct": None,
        "dxy_trend": "UNAVAILABLE",
        "ndx_trend": "UNAVAILABLE",
        "fetched_at": None,
        "stale_after_minutes": 300,
    }
    try:
        if not external_indices_path.exists():
            return snapshot

        data = json.loads(external_indices_path.read_text())
        fetched_at = _parse_iso8601(data.get("fetched_at", ""))
        stale_after_minutes = int(data.get("stale_after_minutes", 300) or 300)
        is_stale = True
        if fetched_at:
            age_s = (datetime.now(timezone.utc) - fetched_at).total_seconds()
            is_stale = age_s > (stale_after_minutes * 60)

        snapshot.update({
            "available": True,
            "stale": is_stale,
            "source": data.get("source", "UNKNOWN"),
            "dxy_change_pct": data.get("dxy_change_pct"),
            "ndx_change_pct": data.get("ndx_change_pct"),
            "dxy_trend": data.get("dxy_trend", "NEUTRAL"),
            "ndx_trend": data.get("ndx_trend", "NEUTRAL"),
            "fetched_at": data.get("fetched_at"),
            "stale_after_minutes": stale_after_minutes,
        })
    except Exception:
        return snapshot
    return snapshot


def _append_shadow_compare(baseline: DirectionUpdate, candidate: DirectionUpdate) -> None:
    """Append baseline-vs-candidate comparison for shadow rollout gating."""
    try:
        shadow_compare_path = DATA_DIR / "state" / "market_direction_shadow_compare.jsonl"
        shadow_compare_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "baseline_bias": baseline.bias.value,
            "baseline_score": round(baseline.score, 4),
            "baseline_confidence_pct": baseline.confidence_pct,
            "candidate_bias": candidate.bias.value,
            "candidate_score": round(candidate.score, 4),
            "candidate_confidence_pct": candidate.confidence_pct,
            "score_delta_abs": round(abs(candidate.score - baseline.score), 4),
            "bias_mismatch": baseline.bias.value != candidate.bias.value,
            "baseline_data_quality": baseline.data_quality or {},
            "candidate_data_quality": candidate.data_quality or {},
        }
        with open(shadow_compare_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")
    except Exception as e:
        logger.debug(f"Shadow compare write failed: {e}")


async def calculate_direction_bias() -> DirectionUpdate:
    """Entry point with optional shadow/candidate realism rollout flags."""
    shadow_enabled = _env_flag("MARKET_DIRECTION_REALISM_SHADOW", default=False)
    candidate_enabled = _env_flag("MARKET_DIRECTION_REALISM_CANDIDATE", default=False)

    if shadow_enabled:
        baseline = await _calculate_direction_bias_impl(use_realism=False)
        candidate = await _calculate_direction_bias_impl(use_realism=True)
        _append_shadow_compare(baseline, candidate)
        return candidate if candidate_enabled else baseline

    return await _calculate_direction_bias_impl(use_realism=candidate_enabled)


async def _calculate_direction_bias_impl(use_realism: bool = False) -> DirectionUpdate:
    """
    Fetch all 8 signals and compute composite market direction bias.

    Returns a DirectionUpdate with score, signals, key levels, and sizing hints.
    """
    signals: List[SignalResult] = []

    # ---- Fetch BTC data from Binance ----
    btc_price = btc_ema20 = btc_rsi = btc_trend = btc_change_24h = None
    funding_rate_pct = None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # 4H klines for EMA/RSI
            klines_resp = await client.get(
                "https://api.binance.com/api/v3/klines",
                params={"symbol": "BTCUSDT", "interval": "4h", "limit": 50}
            )
            ticker_resp = await client.get(
                "https://api.binance.com/api/v3/ticker/24hr",
                params={"symbol": "BTCUSDT"}
            )

            if klines_resp.status_code == 200 and ticker_resp.status_code == 200:
                closes = [float(k[4]) for k in klines_resp.json()]
                ticker = ticker_resp.json()
                btc_price = float(ticker["lastPrice"])
                btc_change_24h = float(ticker["priceChangePercent"])

                # EMA20
                if len(closes) >= 20:
                    mult = 2 / 21
                    ema = sum(closes[:20]) / 20
                    for p in closes[20:]:
                        ema = (p - ema) * mult + ema
                    btc_ema20 = ema

                btc_trend = "UPTREND" if (btc_ema20 and btc_price > btc_ema20) else "DOWNTREND"

                # RSI(14)
                if len(closes) >= 15:
                    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
                    gains = [c if c > 0 else 0 for c in changes[-14:]]
                    losses = [-c if c < 0 else 0 for c in changes[-14:]]
                    avg_gain = sum(gains) / 14
                    avg_loss = sum(losses) / 14 or 0.001
                    btc_rsi = 100 - (100 / (1 + avg_gain / avg_loss))

            # Funding rate
            try:
                fr_resp = await client.get(
                    "https://fapi.binance.com/fapi/v1/fundingRate",
                    params={"symbol": "BTCUSDT", "limit": 1}
                )
                if fr_resp.status_code == 200:
                    fr_data = fr_resp.json()
                    if fr_data:
                        funding_rate_pct = float(fr_data[0].get("fundingRate", 0)) * 100
            except Exception as e:
                logger.debug(f"Funding rate fetch failed: {e}")

    except Exception as e:
        logger.warning(f"Binance BTC fetch failed: {e}")

    # ---- Fetch CoinPaprika global + ticker data ----
    btcdom_value = btcdom_change_24h = None
    usdt_d_value = usdt_d_change_24h = None
    total3_value_b = total3_change_24h = None
    cp_global = {"btc_mc": 0, "total_mc": 0, "btcdom": 0, "stable_mc": 0}
    # Context indicators
    total1_t = total1_change = None
    total2_b = total2_change = None
    others_d = others_d_change_pp = None
    stable_d = stable_d_change_pp = None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Parallel fetch: global stats + BTC + ETH + USDT + USDC tickers
            global_resp, btc_ticker, eth_ticker, usdt_ticker, usdc_ticker = await asyncio.gather(
                client.get("https://api.coinpaprika.com/v1/global"),
                client.get("https://api.coinpaprika.com/v1/tickers/btc-bitcoin"),
                client.get("https://api.coinpaprika.com/v1/tickers/eth-ethereum"),
                client.get("https://api.coinpaprika.com/v1/tickers/usdt-tether"),
                client.get("https://api.coinpaprika.com/v1/tickers/usdc-usd-coin"),
                return_exceptions=True,
            )

            if not isinstance(global_resp, Exception) and global_resp.status_code == 200:
                gd = global_resp.json()
                total_mc = gd.get("market_cap_usd", 0)
                btcdom_value = gd.get("bitcoin_dominance_percentage", 0)
                mc_change_24h = gd.get("market_cap_change_24h", 0)

                # Extract BTC ticker data
                btc_pct_change = 0
                btc_mc = 0
                if not isinstance(btc_ticker, Exception) and btc_ticker.status_code == 200:
                    btc_data = btc_ticker.json()
                    btc_pct_change = btc_data.get("quotes", {}).get("USD", {}).get("percent_change_24h", 0) or 0
                    btc_mc = btc_data.get("quotes", {}).get("USD", {}).get("market_cap", 0) or 0
                if not btc_mc and btcdom_value:
                    btc_mc = total_mc * (btcdom_value / 100)  # fallback
                cp_global = {
                    "btc_mc": btc_mc or 0,
                    "total_mc": total_mc or 0,
                    "btcdom": btcdom_value or 0,
                    "stable_mc": 0,
                }

                # BTCDOM change: BTC outperforms market → dominance rises
                btcdom_change_24h = btc_pct_change - mc_change_24h

                # Extract ETH ticker data
                eth_mc = 0
                eth_pct_change = 0
                if not isinstance(eth_ticker, Exception) and eth_ticker.status_code == 200:
                    eth_data = eth_ticker.json()
                    eth_mc = eth_data.get("quotes", {}).get("USD", {}).get("market_cap", 0) or 0
                    eth_pct_change = eth_data.get("quotes", {}).get("USD", {}).get("percent_change_24h", 0) or 0

                # Extract USDT ticker data
                usdt_mc = 0
                usdt_pct_change = 0
                if not isinstance(usdt_ticker, Exception) and usdt_ticker.status_code == 200:
                    usdt_data = usdt_ticker.json()
                    usdt_mc = usdt_data.get("quotes", {}).get("USD", {}).get("market_cap", 0) or 0
                    usdt_pct_change = usdt_data.get("quotes", {}).get("USD", {}).get("percent_change_24h", 0) or 0
                if total_mc:
                    usdt_d_value = (usdt_mc / total_mc * 100)
                    # USDT.D change: when total MC rises, USDT.D falls (inverse)
                    usdt_d_change_24h = -mc_change_24h * 0.1 if mc_change_24h else 0

                # Extract USDC ticker data (graceful degradation if unavailable)
                usdc_mc = 0
                usdc_pct_change = 0
                if not isinstance(usdc_ticker, Exception) and usdc_ticker.status_code == 200:
                    usdc_data = usdc_ticker.json()
                    usdc_mc = usdc_data.get("quotes", {}).get("USD", {}).get("market_cap", 0) or 0
                    usdc_pct_change = usdc_data.get("quotes", {}).get("USD", {}).get("percent_change_24h", 0) or 0

                # TOTAL3: total MC minus BTC minus ETH
                total3_mc = total_mc - btc_mc - eth_mc
                total3_value_b = total3_mc / 1e9 if total3_mc > 0 else 0

                # Derive 24h-ago values for accurate change computation
                total_mc_24h_ago = total_mc / (1 + mc_change_24h / 100) if mc_change_24h else total_mc
                btc_mc_24h_ago = btc_mc / (1 + btc_pct_change / 100) if btc_pct_change else btc_mc
                eth_mc_24h_ago = eth_mc / (1 + eth_pct_change / 100) if eth_pct_change else eth_mc

                # TOTAL3 change (exact computation instead of ETH proxy)
                total3_mc_24h_ago = total_mc_24h_ago - btc_mc_24h_ago - eth_mc_24h_ago
                if total3_mc_24h_ago > 0:
                    total3_change_24h = ((total3_mc - total3_mc_24h_ago) / total3_mc_24h_ago) * 100
                else:
                    total3_change_24h = 0

                # ---- Context indicators ----
                # TOTAL1: total crypto market cap in trillions
                if total_mc:
                    total1_t = total_mc / 1e12
                    total1_change = mc_change_24h

                # TOTAL2: total ex-BTC in billions
                total2_mc = total_mc - btc_mc
                if total2_mc > 0:
                    total2_b = total2_mc / 1e9
                    total2_mc_24h_ago = total_mc_24h_ago - btc_mc_24h_ago
                    if total2_mc_24h_ago > 0:
                        total2_change = ((total2_mc - total2_mc_24h_ago) / total2_mc_24h_ago) * 100
                    else:
                        total2_change = 0

                # Others.D: altcoin dominance ex-BTC/ETH (= TOTAL3 / TOTAL1 * 100)
                if total_mc and total3_mc > 0:
                    others_d = (total3_mc / total_mc) * 100
                    others_d_24h_ago = (total3_mc_24h_ago / total_mc_24h_ago) * 100 if total_mc_24h_ago else 0
                    others_d_change_pp = others_d - others_d_24h_ago

                # STABLE.C.D: stablecoin dominance (USDT + USDC)
                stables_mc = usdt_mc + usdc_mc
                cp_global["stable_mc"] = stables_mc or 0
                if total_mc and stables_mc > 0:
                    stable_d = (stables_mc / total_mc) * 100
                    usdt_mc_24h_ago = usdt_mc / (1 + usdt_pct_change / 100) if usdt_pct_change else usdt_mc
                    usdc_mc_24h_ago = usdc_mc / (1 + usdc_pct_change / 100) if usdc_pct_change else usdc_mc
                    stables_mc_24h_ago = usdt_mc_24h_ago + usdc_mc_24h_ago
                    stable_d_24h_ago = (stables_mc_24h_ago / total_mc_24h_ago) * 100 if total_mc_24h_ago else 0
                    stable_d_change_pp = stable_d - stable_d_24h_ago
    except Exception as e:
        logger.warning(f"CoinPaprika fetch failed: {e}")

    # ---- Fetch Fear & Greed ----
    fg_value = None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            fg_resp = await client.get("https://api.alternative.me/fng/?limit=1")
            if fg_resp.status_code == 200:
                fg_data = fg_resp.json()
                if fg_data.get("data"):
                    fg_value = int(fg_data["data"][0].get("value", 50))
    except Exception as e:
        logger.debug(f"Fear & Greed fetch failed: {e}")

    # ---- Load BTC structure data for 8th signal ----
    structure_data = _load_btc_structure_data()
    structure_bias = structure_data.get("structure_bias", "")
    structure_levels = structure_data.get("levels", {})
    structure_shift = structure_levels.get("structure_shift", 0)

    # ---- Score all 8 signals ----
    signals = [
        _score_btc_trend(btc_trend or "", btc_price or 0, btc_ema20 or 0),
        _score_btc_rsi(btc_rsi),
        _score_btcdom(btcdom_change_24h, btcdom_value),
        _score_usdt_d(usdt_d_change_24h, usdt_d_value),
        _score_total3(total3_change_24h, total3_value_b),
        _score_fear_greed(fg_value),
        _score_funding(funding_rate_pct),
        _score_btc_structure(btc_price or 0, structure_bias, structure_shift),
    ]

    # ---- Elite Macro Layer (Session 441: DXY/NDX Correlation) ----
    external_snapshot = _load_external_indices_snapshot()
    external_macro = await _score_external_macro(use_realism=use_realism)
    if external_macro:
        signals.append(external_macro)

    # ---- Liquidity Fuel Layer (Session 441: Whale Flows & SSR) ----
    liquidity_fuel = await _score_liquidity_fuel(btc_price, cp_global, use_realism=use_realism)
    if liquidity_fuel:
        signals.append(liquidity_fuel)

    # ---- Context indicators (weight=0, informational) ----
    context_signals = [
        _context_total1(total1_t, total1_change),
        _context_total2(total2_b, total2_change),
        _context_others_d(others_d, others_d_change_pp),
        _context_stable_d(stable_d, stable_d_change_pp),
    ]

    # ---- Composite score = weighted sum ----
    # Re-calculate total weight to include external macro if present
    total_weight = sum(s.weight for s in signals)
    composite = sum(s.weight * s.score for s in signals) / max(total_weight, 0.01)
    composite = max(-1.0, min(1.0, composite))

    if composite > 0.30:
        bias = DirectionBias.BULLISH
    elif composite < -0.30:
        bias = DirectionBias.BEARISH
    else:
        bias = DirectionBias.NEUTRAL

    # Confidence: weighted fraction of signals that agree with composite direction
    agreeing_weight = sum(s.weight for s in signals if s.score != 0 and (s.score > 0) == (composite > 0))
    confidence_pct = int((agreeing_weight / max(total_weight, 0.01)) * 100) if composite != 0 else 0

    # ---- Key Level Proximity ----
    btc_levels = _load_btc_structure_levels()
    sherlock_levels = _load_sherlock_macro_levels()
    key_levels = _build_key_levels(
        btc_price or 0, btc_levels, btcdom_value or 0,
        sherlock_levels, total3_value_b or 0
    )

    # ---- Position implications (graduated by score strength) ----
    abs_score = abs(composite)

    if bias == DirectionBias.BEARISH:
        if abs_score >= 0.60:  # Strong bearish
            position_impl = {
                "short_sizing": "1.2x (strong bearish alignment)",
                "long_sizing": "0.25x (strongly counter-trend)",
                "recommendation": "Look for SHORT setups",
            }
        else:  # Moderate bearish (0.30-0.60)
            position_impl = {
                "short_sizing": "1.0x (favorable)",
                "long_sizing": "0.5x (counter-trend)",
                "recommendation": "Look for SHORT setups",
            }
    elif bias == DirectionBias.BULLISH:
        if abs_score >= 0.60:  # Strong bullish
            position_impl = {
                "short_sizing": "0.25x (strongly counter-trend)",
                "long_sizing": "1.2x (strong bullish alignment)",
                "recommendation": "Look for LONG setups",
            }
        else:  # Moderate bullish
            position_impl = {
                "short_sizing": "0.5x (counter-trend)",
                "long_sizing": "1.0x (favorable)",
                "recommendation": "Look for LONG setups",
            }
    else:  # NEUTRAL
        position_impl = {
            "short_sizing": "0.75x (mixed signals)",
            "long_sizing": "0.75x (mixed signals)",
            "recommendation": "Both directions viable, smaller size",
        }

    # ---- Data quality metadata (non-blocking, transparency only) ----
    core_signal_names = {
        "BTC Trend", "BTC RSI", "BTCDOM", "USDT.D", "TOTAL3",
        "Fear & Greed", "Funding", "BTC Structure",
    }
    signal_quality = {}
    for s in signals:
        signal_quality[s.name] = {
            "available": s.label != "N/A",
            "stale": False,
        }

    signal_quality["External Macro"] = {
        "available": external_macro is not None,
        "stale": bool(external_snapshot.get("stale", True)),
        "source": external_snapshot.get("source", "UNAVAILABLE"),
    }
    signal_quality["Liquidity Fuel"] = {
        "available": liquidity_fuel is not None,
        "stale": False,
    }

    core_available = sum(
        1
        for s in signals
        if s.name in core_signal_names and s.label != "N/A"
    )
    optional_live = int(external_macro is not None) + int(liquidity_fuel is not None)
    data_quality = {
        "mode": "realism" if use_realism else "baseline",
        "core_signals_total": len(core_signal_names),
        "core_signals_available": core_available,
        "optional_signals_total": 2,
        "optional_signals_live": optional_live,
        "optional_signals_missing": 2 - optional_live,
    }

    return DirectionUpdate(
        bias=bias,
        score=composite,
        confidence_pct=confidence_pct,
        signals=signals,
        key_levels=key_levels,
        position_implications=position_impl,
        context_signals=context_signals,
        btc_price=btc_price or 0.0,
        signal_quality=signal_quality,
        data_quality=data_quality,
    )


# =============================================================================
# Signal Flip Tracker (Enhancement 5)
# =============================================================================

# Thresholds for each signal to flip between bearish/neutral/bullish
_SIGNAL_THRESHOLDS = {
    "BTC RSI": {"bullish": 60, "bearish": 40},
    "BTCDOM": {"bullish": -0.3, "bearish": 0.3},     # 24h change %
    "USDT.D": {"bullish": -0.2, "bearish": 0.2},     # 24h change %
    "TOTAL3": {"bullish": 1.0, "bearish": -1.0},      # 24h change %
    "Fear & Greed": {"bullish": 55, "bearish": 34},
    "Funding": {"bullish": 0.005, "bearish": -0.01},  # rate %
}

# Map signal names to raw_data keys
_SIGNAL_RAW_KEYS = {
    "BTC RSI": "btc_rsi",
    "BTCDOM": "btcdom_change_24h",
    "USDT.D": "usdt_d_change_24h",
    "TOTAL3": "total3_change_24h",
    "Fear & Greed": "fear_greed",
    "Funding": "funding_rate",
}


def _trend_from_change(change_pct: Optional[float], threshold: float = 0.2) -> str:
    """Normalize percent change into UPTREND/DOWNTREND/NEUTRAL/UNAVAILABLE."""
    if change_pct is None:
        return "UNAVAILABLE"
    if change_pct > threshold:
        return "UPTREND"
    if change_pct < -threshold:
        return "DOWNTREND"
    return "NEUTRAL"


async def _score_external_macro(use_realism: bool = False) -> Optional[SignalResult]:
    """
    External Macro Layer: score DXY and NDX correlations.

    Baseline mode uses trend labels from cache.
    Realism mode requires fresh numeric daily changes.
    """
    try:
        snapshot = _load_external_indices_snapshot()
        if not snapshot.get("available"):
            return None

        score = 0.0
        dxy_change = snapshot.get("dxy_change_pct")
        ndx_change = snapshot.get("ndx_change_pct")
        dxy_trend = snapshot.get("dxy_trend", "NEUTRAL")
        ndx_trend = snapshot.get("ndx_trend", "NEUTRAL")

        if use_realism:
            if snapshot.get("stale"):
                return None
            if dxy_change is None and ndx_change is None:
                return None

            if dxy_change is not None:
                if dxy_change > 0.2:
                    score -= 0.5
                elif dxy_change < -0.2:
                    score += 0.5
                dxy_trend = _trend_from_change(dxy_change)

            if ndx_change is not None:
                if ndx_change > 0.2:
                    score += 0.5
                elif ndx_change < -0.2:
                    score -= 0.5
                ndx_trend = _trend_from_change(ndx_change)

            dxy_txt = "N/A" if dxy_change is None else f"{dxy_change:+.2f}% ({dxy_trend})"
            ndx_txt = "N/A" if ndx_change is None else f"{ndx_change:+.2f}% ({ndx_trend})"
            label = f"DXY {dxy_txt} | NDX {ndx_txt}"
        else:
            if dxy_trend == "UPTREND":
                score -= 0.5
            elif dxy_trend == "DOWNTREND":
                score += 0.5

            if ndx_trend == "UPTREND":
                score += 0.5
            elif ndx_trend == "DOWNTREND":
                score -= 0.5

            label = f"DXY {dxy_trend} | NDX {ndx_trend}"

        emoji = "🟢" if score > 0 else "🔴" if score < 0 else "🟡"
        em_w = load_weights().get("external_macro", 0.15)
        return SignalResult("External Macro", em_w, score, None, label, emoji)
    except Exception:
        return None


async def _score_liquidity_fuel(
    btc_price: float,
    cp_global: dict,
    use_realism: bool = False,
) -> Optional[SignalResult]:
    """
    Liquidity Layer: Score SSR (Buying Power proxy from market caps).

    In realism mode, stablecoin market cap uses USDT+USDC from CoinPaprika.
    Baseline mode preserves legacy estimated stablecoin denominator.
    """
    try:
        btc_mc = cp_global.get("btc_mc", 0)
        total_mc = cp_global.get("total_mc", 0)

        if use_realism:
            stable_mc = cp_global.get("stable_mc", 0)
        else:
            # Legacy heuristic fallback
            stable_mc = total_mc * 0.10

        if btc_mc > 0 and stable_mc > 0:
            ssr = btc_mc / stable_mc

            score = 0.0
            if ssr < 10:
                score += 0.5
            elif ssr > 15:
                score -= 0.5

            fuel_label = "High Fuel" if ssr < 10 else "Low Fuel" if ssr > 15 else "Balanced"
            label = f"SSR {ssr:.1f} ({fuel_label})"
            emoji = "🟢" if score > 0 else "🔴" if score < 0 else "🟡"
            lf_w = load_weights().get("liquidity_fuel", 0.10)
            return SignalResult("Liquidity Fuel", lf_w, score, ssr, label, emoji)
    except Exception:
        return None


def compute_signal_proximity(
    signals: List[SignalResult],
    raw_data: dict,
) -> List[dict]:
    """Compute proximity of each signal to its flip threshold.

    For signals with numeric thresholds, calculates distance to the nearest
    flip point (neutral→bullish or neutral→bearish). For BTC Trend and
    BTC Structure (which depend on qualitative state), provides descriptive
    proximity.

    Args:
        signals: List of current SignalResult objects from calculate_direction_bias.
        raw_data: Dict with raw values (btc_price, btc_ema20, btc_rsi, etc.)

    Returns:
        List of dicts sorted by proximity (closest to flip first), each with:
        - name: Signal name
        - current_score: Current score (-1 to +1)
        - weight: Signal weight
        - to_neutral: Absolute distance to nearest neutral threshold (None if N/A)
        - description: Human-readable proximity text
    """
    proximities = []

    for sig in signals:
        entry = {
            "name": sig.name,
            "current_score": sig.score,
            "weight": sig.weight,
            "to_neutral": None,
            "description": "",
        }

        if sig.name == "BTC Trend":
            btc_price = raw_data.get("btc_price", 0) or 0
            ema20 = raw_data.get("btc_ema20", 0) or 0
            if btc_price and ema20:
                pct_diff = ((btc_price - ema20) / ema20) * 100
                entry["to_neutral"] = abs(pct_diff)
                if pct_diff > 0:
                    entry["description"] = f"{pct_diff:.1f}% above EMA20 (flip below to bearish)"
                elif pct_diff < 0:
                    entry["description"] = f"{abs(pct_diff):.1f}% below EMA20 (flip above to bullish)"
                else:
                    entry["description"] = "At EMA20 (neutral)"
                    entry["to_neutral"] = 0
            else:
                entry["description"] = "Data unavailable"

        elif sig.name == "BTC Structure":
            # Qualitative — just describe current state
            if sig.score == 0:
                entry["to_neutral"] = 0
                entry["description"] = "Already NEUTRAL"
            else:
                entry["to_neutral"] = abs(sig.score) * 10  # Rough proxy
                bias_label = "BEARISH" if sig.score < 0 else "BULLISH"
                entry["description"] = f"{bias_label} structure (qualitative — depends on price action)"

        elif sig.name in _SIGNAL_THRESHOLDS:
            thresholds = _SIGNAL_THRESHOLDS[sig.name]
            raw_key = _SIGNAL_RAW_KEYS.get(sig.name)
            raw_val = raw_data.get(raw_key) if raw_key else None

            if raw_val is not None:
                bull_thresh = thresholds["bullish"]
                bear_thresh = thresholds["bearish"]

                if sig.score > 0:
                    # Currently bullish — distance to neutral (flip down)
                    if sig.name in ("BTCDOM", "USDT.D"):
                        # Inverted: bullish = falling (negative change)
                        dist = abs(raw_val - bear_thresh)
                    elif sig.name == "Funding":
                        dist = abs(raw_val - 0.005)  # distance from bullish threshold
                    else:
                        dist = abs(raw_val - bull_thresh)
                    entry["to_neutral"] = round(dist, 2)
                    entry["description"] = f"{raw_val} → neutral at {bull_thresh} ({dist:.1f} away)"
                elif sig.score < 0:
                    # Currently bearish — distance to neutral (flip up)
                    if sig.name in ("BTCDOM", "USDT.D"):
                        dist = abs(raw_val - bull_thresh)
                    elif sig.name == "Funding":
                        dist = abs(raw_val - (-0.01))
                    else:
                        dist = abs(raw_val - bear_thresh)
                    entry["to_neutral"] = round(dist, 2)
                    entry["description"] = f"{raw_val} → neutral at {bear_thresh} ({dist:.1f} away)"
                else:
                    # Currently neutral — distance to nearest flip
                    dist_bull = abs(raw_val - bull_thresh)
                    dist_bear = abs(raw_val - bear_thresh)
                    closer = "bullish" if dist_bull < dist_bear else "bearish"
                    entry["to_neutral"] = 0
                    entry["description"] = f"Neutral — closest flip: {closer} ({min(dist_bull, dist_bear):.1f} away)"
            else:
                entry["description"] = "Data unavailable"

        proximities.append(entry)

    # Sort by to_neutral ascending (closest to flip first), None values last
    proximities.sort(key=lambda p: p["to_neutral"] if p["to_neutral"] is not None else float("inf"))

    return proximities


# =============================================================================
# Score Delta Computation (Phase 1.4)
# =============================================================================

def compute_score_delta(
    current_signals: List[SignalResult],
    current_score: float,
    previous_signals: List[dict],
    previous_score: float,
) -> dict:
    """Compute score delta and signal flips between current and previous update.

    Args:
        current_signals: List of current SignalResult objects.
        current_score: Current composite score.
        previous_signals: List of dicts with 'name', 'score', 'label' from previous update.
        previous_score: Previous composite score.

    Returns:
        Dict with score_delta (float) and signal_changes (list of flip dicts).
    """
    score_delta = round(current_score - previous_score, 3)

    # Build lookup of previous signals
    prev_by_name = {s["name"]: s for s in previous_signals}

    signal_changes = []
    for sig in current_signals:
        prev = prev_by_name.get(sig.name)
        if prev is None:
            continue
        prev_score = prev.get("score", 0)
        if sig.score != prev_score:
            # Determine labels
            def _bias_label(score_val):
                if score_val > 0:
                    return "BULLISH"
                elif score_val < 0:
                    return "BEARISH"
                return "NEUTRAL"

            signal_changes.append({
                "name": sig.name,
                "from_score": prev_score,
                "to_score": sig.score,
                "from_label": _bias_label(prev_score),
                "to_label": _bias_label(sig.score),
            })

    return {
        "score_delta": score_delta,
        "signal_changes": signal_changes,
    }


# =============================================================================
# Correlation Awareness (Phase 4.2)
# =============================================================================

# Signals that are structurally correlated (overlapping metrics from same data)
_CORRELATED_GROUPS = [
    {"BTCDOM", "USDT.D", "TOTAL3"},  # All derived from market cap ratios
]

_CORRELATION_DISCOUNT = 0.7  # 30% discount when all agree


def apply_correlation_discount(signals: List[SignalResult]) -> List[SignalResult]:
    """Apply correlation discount when overlapping signals unanimously agree.

    When BTCDOM, USDT.D, TOTAL3 all show the same direction (all positive
    or all negative), their scores are reduced by _CORRELATION_DISCOUNT
    to prevent over-confidence from correlated signals.

    Returns new list of SignalResult (does not mutate input).
    """
    adjusted = list(signals)
    sig_by_name = {s.name: (i, s) for i, s in enumerate(adjusted)}

    for group in _CORRELATED_GROUPS:
        group_signals = [(sig_by_name[name][0], sig_by_name[name][1])
                         for name in group if name in sig_by_name]
        if len(group_signals) < 2:
            continue

        # Check if all non-zero signals in the group agree on direction
        non_zero = [(i, s) for i, s in group_signals if s.score != 0]
        if len(non_zero) < 2:
            continue

        all_positive = all(s.score > 0 for _, s in non_zero)
        all_negative = all(s.score < 0 for _, s in non_zero)

        if all_positive or all_negative:
            # Apply discount
            for idx, sig in non_zero:
                adjusted[idx] = SignalResult(
                    name=sig.name,
                    weight=sig.weight,
                    score=round(sig.score * _CORRELATION_DISCOUNT, 3),
                    value=sig.value,
                    label=sig.label,
                    emoji=sig.emoji,
                )

    return adjusted


# =============================================================================
# Confidence Calibration (Phase 4.3)
# =============================================================================

def calibrate_confidence(
    raw_confidence_pct: int,
    history: Optional[dict] = None,
) -> dict:
    """Calibrate reported confidence against historical accuracy.

    Args:
        raw_confidence_pct: The computed confidence (0-100).
        history: Dict of bucket_label -> {predicted, correct, hit_rate}.
                 E.g. {"70-80": {"predicted": 20, "correct": 11, "hit_rate": 55.0}}.

    Returns:
        {"raw": int, "calibrated": int, "bucket": str|None, "sample_size": int}
    """
    result = {
        "raw": raw_confidence_pct,
        "calibrated": raw_confidence_pct,
        "bucket": None,
        "sample_size": 0,
    }

    if not history:
        return result

    # Find the matching bucket for raw confidence
    for bucket_label, stats in history.items():
        try:
            low, high = bucket_label.split("-")
            if int(low) <= raw_confidence_pct <= int(high):
                actual_hit_rate = stats.get("hit_rate", raw_confidence_pct)
                sample_size = stats.get("predicted", 0)
                # Blend: move 50% toward actual hit rate (conservative)
                if sample_size >= 5:  # Only calibrate with sufficient data
                    calibrated = int(raw_confidence_pct * 0.5 + actual_hit_rate * 0.5)
                else:
                    calibrated = raw_confidence_pct
                result["calibrated"] = calibrated
                result["bucket"] = bucket_label
                result["sample_size"] = sample_size
                break
        except (ValueError, KeyError):
            continue

    return result


# =============================================================================
# Volume Profile Signal (Phase 4.4)
# =============================================================================

def _score_volume_profile(
    volume_24h: Optional[float],
    avg_7d: Optional[float],
) -> SignalResult:
    """BTC Volume Profile — conviction modifier based on 24h vs 7d avg volume.

    Volume > 1.5x avg = High volume, confirms current direction (+0.5)
    Volume < 0.5x avg = Low volume, weakens conviction (-0.5)
    Volume 0.5-1.5x = Normal, neutral (0.0)
    """
    w = load_weights().get("volume_profile", 0.05)

    if volume_24h is None or avg_7d is None or avg_7d == 0:
        return SignalResult("Volume Profile", w, 0.0, None, "N/A", "⚪")

    ratio = volume_24h / avg_7d

    if ratio > 1.5:
        score = min((ratio - 1.5) / 1.5, 1.0) * 0.5 + 0.5  # 0.5 to 1.0
        return SignalResult(
            "Volume Profile", w, round(score, 3), round(ratio, 2),
            f"High Volume ({ratio:.1f}x avg)", "🟢",
        )
    elif ratio < 0.5:
        score = -(0.5 + min((0.5 - ratio) / 0.5, 1.0) * 0.5)  # -0.5 to -1.0
        return SignalResult(
            "Volume Profile", w, round(score, 3), round(ratio, 2),
            f"Low Volume ({ratio:.1f}x avg)", "🔴",
        )
    else:
        return SignalResult(
            "Volume Profile", w, 0.0, round(ratio, 2),
            f"Normal Volume ({ratio:.1f}x avg)", "🟡",
        )


# =============================================================================
# Regime Classification (Phase 3.4)
# =============================================================================

def classify_regime(
    bias: DirectionBias,
    score: float,
    signals_agreeing: int,
    signals_total: int,
) -> str:
    """Classify market regime from bias, score strength, and signal agreement.

    Returns one of:
      RISK_ON       — Strong bullish with high agreement
      RISK_OFF      — Strong bearish with high agreement
      ACCUMULATION  — Mildly bullish with low agreement (stealth buying)
      DISTRIBUTION  — Mildly bearish with low agreement (stealth selling)
      ROTATION      — Neutral with mixed signals
      CAPITULATION  — Extreme bearish with near-unanimous agreement
      SQUEEZE       — Strong move with extreme positioning (future: tie to L/S ratio)
    """
    abs_score = abs(score)
    agreement_pct = signals_agreeing / max(signals_total, 1)

    if bias == DirectionBias.BEARISH:
        if abs_score >= 0.80 and agreement_pct >= 0.85:
            return "CAPITULATION"
        if abs_score >= 0.50 and agreement_pct >= 0.60:
            return "RISK_OFF"
        return "DISTRIBUTION"

    if bias == DirectionBias.BULLISH:
        if abs_score >= 0.50 and agreement_pct >= 0.60:
            return "RISK_ON"
        return "ACCUMULATION"

    # NEUTRAL
    return "ROTATION"


# =============================================================================
# VIX + Bond Yield Helpers (Phase 3.5)
# =============================================================================

def _vix_score_modifier(vix_value: Optional[float]) -> float:
    """Compute score modifier from VIX level.

    VIX > 25: fear / risk-off → bearish modifier (up to -0.5)
    VIX < 15: complacency → bullish modifier (up to +0.3)
    VIX 15-25: normal → 0.0
    """
    if vix_value is None:
        return 0.0
    if vix_value > 25:
        return -min((vix_value - 25) / 25, 0.5)
    if vix_value < 15:
        return min((15 - vix_value) / 15, 0.3)
    return 0.0


def _bond_yield_score_modifier(change_pct: Optional[float]) -> float:
    """Compute score modifier from 10Y bond yield change.

    Rising yields (>0.1%): risk-off → bearish (up to -0.3)
    Falling yields (<-0.1%): easing expectations → bullish (up to +0.3)
    Small change: 0.0
    """
    if change_pct is None:
        return 0.0
    if change_pct > 0.1:
        return -min((change_pct - 0.1) / 1.0, 0.3)
    if change_pct < -0.1:
        return min((abs(change_pct) - 0.1) / 1.0, 0.3)
    return 0.0


def _build_external_macro_label(
    dxy_trend: str = "NEUTRAL",
    ndx_trend: str = "NEUTRAL",
    vix_value: Optional[float] = None,
    bond_10y_change: Optional[float] = None,
) -> str:
    """Build human-readable label for External Macro signal."""
    parts = [f"DXY {dxy_trend}", f"NDX {ndx_trend}"]
    if vix_value is not None:
        parts.append(f"VIX {vix_value:.1f}")
    if bond_10y_change is not None:
        parts.append(f"10Y {bond_10y_change:+.2f}%")
    return " | ".join(parts)


# =============================================================================
# Narrative Summary Generator
# =============================================================================

def generate_narrative_summary(update: DirectionUpdate) -> str:
    """Generate a rule-based narrative summary from a DirectionUpdate.

    Template-driven, zero LLM cost. Leads with strongest signal, mentions
    confirming signals, flags contradictions.
    """
    signals = update.signals
    if not signals:
        return "Insufficient signal data for narrative."

    bias = update.bias.value if isinstance(update.bias, DirectionBias) else str(update.bias)
    score = update.score

    # Rank signals by absolute weighted contribution (weight * |score|)
    ranked = sorted(
        [(s, abs(s.weight * s.score)) for s in signals if s.score != 0],
        key=lambda x: x[1],
        reverse=True,
    )

    # Separate agreeing vs dissenting signals relative to composite direction
    if score > 0:
        agreeing = [(s, c) for s, c in ranked if s.score > 0]
        dissenting = [(s, c) for s, c in ranked if s.score < 0]
    elif score < 0:
        agreeing = [(s, c) for s, c in ranked if s.score < 0]
        dissenting = [(s, c) for s, c in ranked if s.score > 0]
    else:
        agreeing = []
        dissenting = ranked

    # --- Build narrative parts ---
    parts = []

    # 1. Opening statement with strongest signal
    if bias == "BULLISH":
        if agreeing:
            strongest = agreeing[0][0]
            parts.append(f"Market leaning bullish: {strongest.name} ({strongest.label}) is the strongest signal")
        else:
            parts.append("Market leaning bullish despite mixed signals")
    elif bias == "BEARISH":
        if agreeing:
            strongest = agreeing[0][0]
            parts.append(f"Market turning bearish: {strongest.name} ({strongest.label}) is the strongest signal")
        else:
            parts.append("Market turning bearish despite mixed signals")
    else:
        parts.append("Market undecided with mixed signals")

    # 2. Confirming signals (up to 2 more)
    confirm_names = []
    for s, _ in agreeing[1:3]:
        confirm_names.append(f"{s.name} ({s.label})")
    if confirm_names:
        parts.append(", confirmed by " + " and ".join(confirm_names))

    # 3. Contradictions / contrarian signals
    if dissenting:
        contra_parts = []
        for s, _ in dissenting[:2]:
            contra_parts.append(f"{s.name} showing {s.label}")
        parts.append(". However, " + " and ".join(contra_parts) + " (contrarian)")

    # 4. BTC Structure context (if significant)
    structure_signal = next((s for s in signals if s.name == "BTC Structure" and abs(s.score) >= 0.5), None)
    if structure_signal and "MSS" in (structure_signal.label or ""):
        if not any("Structure" in p for p in parts):
            parts.append(f". Key risk: {structure_signal.label}")

    # 5. Extreme Fear & Greed contrarian warning
    fg_signal = next((s for s in signals if s.name == "Fear & Greed" and s.value is not None), None)
    if fg_signal:
        fg_val = int(fg_signal.value)
        if fg_val <= 15:
            parts.append(f". \u26a0\ufe0f Fear & Greed at {fg_val} (Extreme Fear) \u2014 historically a contrarian bounce zone")
        elif fg_val >= 85:
            parts.append(f". \u26a0\ufe0f Fear & Greed at {fg_val} (Extreme Greed) \u2014 historically a contrarian reversal zone")

    narrative = "".join(parts)
    if not narrative.endswith("."):
        narrative += "."

    return narrative


# =============================================================================
# File Loaders
# =============================================================================

def _load_btc_structure_data() -> dict:
    """Load full BTC structure data including top-level structure_bias."""
    path = DATA_DIR / "macro" / "btc_structure_levels.json"
    try:
        if path.exists():
            with open(path) as f:
                return json.load(f)
    except Exception as e:
        logger.debug(f"Failed to load BTC structure data: {e}")
    return {}


def _load_btc_structure_levels() -> dict:
    """Load BTC structure key levels from Sherlock data (for key level display)."""
    path = DATA_DIR / "macro" / "btc_structure_levels.json"
    try:
        if path.exists():
            with open(path) as f:
                data = json.load(f)
            return data.get("levels", data)
    except Exception as e:
        logger.debug(f"Failed to load BTC structure levels: {e}")
    return {}


def _load_sherlock_macro_levels() -> dict:
    """Load Sherlock macro index levels (BTCDOM, TOTAL3, OTHERS.D)."""
    path = DATA_DIR / "macro" / "sherlock_macro_levels.json"
    try:
        if path.exists():
            with open(path) as f:
                data = json.load(f)
            return data.get("levels", {})
    except Exception as e:
        logger.debug(f"Failed to load Sherlock macro levels: {e}")
    return {}
