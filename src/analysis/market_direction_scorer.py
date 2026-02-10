"""
Market Direction Scorer — Periodic Market Bias Assessment

Calculates a composite direction bias (BULLISH / NEUTRAL / BEARISH) from 7 weighted
signals. Used by the market_direction_monitor cron script and the /api/macro/market-direction
endpoint to give David a clear "which way is the market likely to break?" assessment.

Signals (all scored -1 to +1):
    1. BTC Trend (25%)      — Price vs EMA20 from Binance 4H
    2. BTC RSI 4H (10%)     — Oversold/overbought momentum
    3. BTCDOM direction (15%) — Falling = alt-friendly, rising = alt-bearish
    4. USDT.D direction (15%) — Falling = risk-on, rising = risk-off
    5. TOTAL3 direction (15%) — Alt market cap trend
    6. Fear & Greed (10%)    — Sentiment extremes
    7. BTC Funding Rate (10%) — Derivatives positioning

Data Sources (all free, no API keys):
    - Binance REST API (BTC price, klines, funding)
    - CoinPaprika /v1/global + /v1/tickers (BTCDOM, TOTAL3, USDT.D)
    - Alternative.me (Fear & Greed)
    - Local JSON files (key levels from Sherlock)
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import List, Optional

import httpx

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"


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
    shift_detected: bool = False
    previous_bias: Optional[str] = None
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        d = {
            "bias": self.bias.value,
            "score": round(self.score, 3),
            "confidence_pct": self.confidence_pct,
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
            "key_levels": self.key_levels,
            "position_implications": self.position_implications,
            "shift_detected": self.shift_detected,
            "previous_bias": self.previous_bias,
            "timestamp": self.timestamp,
        }
        return d


# =============================================================================
# Signal Scorers
# =============================================================================

def _score_btc_trend(trend: str, price: float, ema20: float) -> SignalResult:
    """BTC Trend — 25% weight."""
    trend_upper = (trend or "").upper()
    if trend_upper == "UPTREND" or (price and ema20 and price > ema20):
        return SignalResult("BTC Trend", 0.25, 1.0, price, f"UPTREND (${price:,.0f})", "🟢")
    elif trend_upper == "DOWNTREND" or (price and ema20 and price < ema20):
        return SignalResult("BTC Trend", 0.25, -1.0, price, f"DOWNTREND (${price:,.0f})", "🔴")
    return SignalResult("BTC Trend", 0.25, 0.0, price, f"SIDEWAYS (${price:,.0f})", "🟡")


def _score_btc_rsi(rsi: Optional[float]) -> SignalResult:
    """BTC RSI(4H) — 10% weight."""
    if rsi is None:
        return SignalResult("BTC RSI", 0.10, 0.0, None, "N/A", "⚪")
    if rsi > 60:
        return SignalResult("BTC RSI", 0.10, 1.0, round(rsi, 1), f"{rsi:.1f} — Strong", "🟢")
    elif rsi < 40:
        return SignalResult("BTC RSI", 0.10, -1.0, round(rsi, 1), f"{rsi:.1f} — Weak", "🔴")
    return SignalResult("BTC RSI", 0.10, 0.0, round(rsi, 1), f"{rsi:.1f} — Neutral", "🟡")


def _score_btcdom(change_24h: Optional[float], value: Optional[float]) -> SignalResult:
    """BTCDOM direction — 15% weight. Falling BTCDOM = bullish for alts."""
    if change_24h is None:
        return SignalResult("BTCDOM", 0.15, 0.0, None, "N/A", "⚪")
    if change_24h < -0.3:
        return SignalResult("BTCDOM", 0.15, 1.0, round(value or 0, 1),
                            f"Falling {change_24h:+.1f}%/24h", "🟢")
    elif change_24h > 0.3:
        return SignalResult("BTCDOM", 0.15, -1.0, round(value or 0, 1),
                            f"Rising {change_24h:+.1f}%/24h", "🔴")
    return SignalResult("BTCDOM", 0.15, 0.0, round(value or 0, 1),
                        f"Flat {change_24h:+.1f}%/24h", "🟡")


def _score_usdt_d(change_24h: Optional[float], value: Optional[float]) -> SignalResult:
    """USDT.D direction — 15% weight. Falling = risk-on (bullish)."""
    if change_24h is None:
        return SignalResult("USDT.D", 0.15, 0.0, None, "N/A", "⚪")
    # USDT.D rising = risk-off = bearish, falling = risk-on = bullish
    if change_24h < -0.2:
        return SignalResult("USDT.D", 0.15, 1.0, round(value or 0, 2),
                            f"Falling {change_24h:+.2f}%/24h (risk-on)", "🟢")
    elif change_24h > 0.2:
        return SignalResult("USDT.D", 0.15, -1.0, round(value or 0, 2),
                            f"Rising {change_24h:+.2f}%/24h (risk-off)", "🔴")
    return SignalResult("USDT.D", 0.15, 0.0, round(value or 0, 2),
                        f"Flat {change_24h:+.2f}%/24h", "🟡")


def _score_total3(change_24h: Optional[float], value_b: Optional[float]) -> SignalResult:
    """TOTAL3 direction — 15% weight. Rising = bullish for alts."""
    if change_24h is None:
        return SignalResult("TOTAL3", 0.15, 0.0, None, "N/A", "⚪")
    if change_24h > 1.0:
        return SignalResult("TOTAL3", 0.15, 1.0, round(value_b or 0, 0),
                            f"Rising {change_24h:+.1f}%/24h", "🟢")
    elif change_24h < -1.0:
        return SignalResult("TOTAL3", 0.15, -1.0, round(value_b or 0, 0),
                            f"Falling {change_24h:+.1f}%/24h", "🔴")
    return SignalResult("TOTAL3", 0.15, 0.0, round(value_b or 0, 0),
                        f"Flat {change_24h:+.1f}%/24h", "🟡")


def _score_fear_greed(value: Optional[int]) -> SignalResult:
    """Fear & Greed — 10% weight."""
    if value is None:
        return SignalResult("Fear & Greed", 0.10, 0.0, None, "N/A", "⚪")
    if value >= 55:
        label = "Extreme Greed" if value >= 75 else "Greed"
        return SignalResult("Fear & Greed", 0.10, 1.0, value, f"{value} — {label}", "🟢")
    elif value <= 34:
        label = "Extreme Fear" if value <= 20 else "Fear"
        return SignalResult("Fear & Greed", 0.10, -1.0, value, f"{value} — {label}", "🔴")
    return SignalResult("Fear & Greed", 0.10, 0.0, value, f"{value} — Neutral", "🟡")


def _score_funding(rate_pct: Optional[float]) -> SignalResult:
    """BTC Funding Rate — 10% weight."""
    if rate_pct is None:
        return SignalResult("Funding", 0.10, 0.0, None, "N/A", "⚪")
    if rate_pct > 0.005:
        return SignalResult("Funding", 0.10, 1.0, round(rate_pct, 4),
                            f"{rate_pct:.4f}% — Positive (longs paying)", "🟢")
    elif rate_pct < -0.01:
        return SignalResult("Funding", 0.10, -1.0, round(rate_pct, 4),
                            f"{rate_pct:.4f}% — Negative (crowded shorts)", "🔴")
    return SignalResult("Funding", 0.10, 0.0, round(rate_pct, 4),
                        f"{rate_pct:.4f}% — Neutral", "🟡")


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

async def calculate_direction_bias() -> DirectionUpdate:
    """
    Fetch all 7 signals and compute composite market direction bias.

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
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Parallel fetch: global stats + BTC ticker + ETH ticker + USDT ticker
            global_resp, btc_ticker, eth_ticker, usdt_ticker = await asyncio.gather(
                client.get("https://api.coinpaprika.com/v1/global"),
                client.get("https://api.coinpaprika.com/v1/tickers/btc-bitcoin"),
                client.get("https://api.coinpaprika.com/v1/tickers/eth-ethereum"),
                client.get("https://api.coinpaprika.com/v1/tickers/usdt-tether"),
                return_exceptions=True,
            )

            if not isinstance(global_resp, Exception) and global_resp.status_code == 200:
                gd = global_resp.json()
                total_mc = gd.get("market_cap_usd", 0)
                btcdom_value = gd.get("bitcoin_dominance_percentage", 0)
                mc_change_24h = gd.get("market_cap_change_24h", 0)

                # BTCDOM change: use BTC 24h % vs total market 24h % as proxy
                btc_pct_change = 0
                if not isinstance(btc_ticker, Exception) and btc_ticker.status_code == 200:
                    btc_pct_change = btc_ticker.json().get("quotes", {}).get("USD", {}).get("percent_change_24h", 0) or 0
                # If BTC outperforms market, dominance rises; if underperforms, falls
                btcdom_change_24h = btc_pct_change - mc_change_24h

                # USDT.D: USDT market cap / total market cap
                if not isinstance(usdt_ticker, Exception) and usdt_ticker.status_code == 200:
                    usdt_mc = usdt_ticker.json().get("quotes", {}).get("USD", {}).get("market_cap", 0) or 0
                    usdt_d_value = (usdt_mc / total_mc * 100) if total_mc else 0
                    # USDT.D change: when total MC rises, USDT.D falls (inverse)
                    usdt_d_change_24h = -mc_change_24h * 0.1 if mc_change_24h else 0

                # TOTAL3: total MC minus BTC minus ETH
                eth_mc = 0
                if not isinstance(eth_ticker, Exception) and eth_ticker.status_code == 200:
                    eth_mc = eth_ticker.json().get("quotes", {}).get("USD", {}).get("market_cap", 0) or 0
                btc_mc = total_mc * (btcdom_value / 100) if btcdom_value else 0
                total3_mc = total_mc - btc_mc - eth_mc
                total3_value_b = total3_mc / 1e9 if total3_mc else 0

                # TOTAL3 change: use ETH 24h change as alt market proxy
                eth_pct_change = 0
                if not isinstance(eth_ticker, Exception) and eth_ticker.status_code == 200:
                    eth_pct_change = eth_ticker.json().get("quotes", {}).get("USD", {}).get("percent_change_24h", 0) or 0
                total3_change_24h = eth_pct_change  # alts tend to follow ETH
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

    # ---- Score all 7 signals ----
    signals = [
        _score_btc_trend(btc_trend or "", btc_price or 0, btc_ema20 or 0),
        _score_btc_rsi(btc_rsi),
        _score_btcdom(btcdom_change_24h, btcdom_value),
        _score_usdt_d(usdt_d_change_24h, usdt_d_value),
        _score_total3(total3_change_24h, total3_value_b),
        _score_fear_greed(fg_value),
        _score_funding(funding_rate_pct),
    ]

    # ---- Composite score = weighted sum ----
    composite = sum(s.weight * s.score for s in signals)
    composite = max(-1.0, min(1.0, composite))

    if composite > 0.30:
        bias = DirectionBias.BULLISH
    elif composite < -0.30:
        bias = DirectionBias.BEARISH
    else:
        bias = DirectionBias.NEUTRAL

    # Confidence: how many signals agree (non-zero and same sign as composite)
    agreeing = sum(1 for s in signals if s.score != 0 and (s.score > 0) == (composite > 0))
    total_non_zero = sum(1 for s in signals if s.score != 0)
    confidence_pct = int((agreeing / max(total_non_zero, 1)) * 100)

    # ---- Key Level Proximity ----
    btc_levels = _load_btc_structure_levels()
    sherlock_levels = _load_sherlock_macro_levels()
    key_levels = _build_key_levels(
        btc_price or 0, btc_levels, btcdom_value or 0,
        sherlock_levels, total3_value_b or 0
    )

    # ---- Position implications ----
    if bias == DirectionBias.BEARISH:
        position_impl = {
            "short_sizing": "1.0x (favorable)",
            "long_sizing": "0.5x (counter-trend)",
            "recommendation": "Look for SHORT setups",
        }
    elif bias == DirectionBias.BULLISH:
        position_impl = {
            "short_sizing": "0.5x (counter-trend)",
            "long_sizing": "1.0x (favorable)",
            "recommendation": "Look for LONG setups",
        }
    else:
        position_impl = {
            "short_sizing": "0.75x (mixed signals)",
            "long_sizing": "0.75x (mixed signals)",
            "recommendation": "Both directions viable, smaller size",
        }

    return DirectionUpdate(
        bias=bias,
        score=composite,
        confidence_pct=confidence_pct,
        signals=signals,
        key_levels=key_levels,
        position_implications=position_impl,
    )


# =============================================================================
# File Loaders
# =============================================================================

def _load_btc_structure_levels() -> dict:
    """Load BTC structure key levels from Sherlock data."""
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
