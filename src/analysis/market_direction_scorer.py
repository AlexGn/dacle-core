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
    context_signals: List[SignalResult] = field(default_factory=list)
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
        }
        return d


# =============================================================================
# Signal Scorers
# =============================================================================

def _score_btc_trend(trend: str, price: float, ema20: float) -> SignalResult:
    """BTC Trend — 20% weight."""
    trend_upper = (trend or "").upper()
    if trend_upper == "UPTREND" or (price and ema20 and price > ema20):
        return SignalResult("BTC Trend", 0.20, 1.0, price, f"UPTREND (${price:,.0f})", "🟢")
    elif trend_upper == "DOWNTREND" or (price and ema20 and price < ema20):
        return SignalResult("BTC Trend", 0.20, -1.0, price, f"DOWNTREND (${price:,.0f})", "🔴")
    return SignalResult("BTC Trend", 0.20, 0.0, price, f"SIDEWAYS (${price:,.0f})", "🟡")


def _score_btc_rsi(rsi: Optional[float]) -> SignalResult:
    """BTC RSI(4H) — 8% weight."""
    if rsi is None:
        return SignalResult("BTC RSI", 0.08, 0.0, None, "N/A", "⚪")
    if rsi > 60:
        return SignalResult("BTC RSI", 0.08, 1.0, round(rsi, 1), f"{rsi:.1f} — Strong", "🟢")
    elif rsi < 40:
        return SignalResult("BTC RSI", 0.08, -1.0, round(rsi, 1), f"{rsi:.1f} — Weak", "🔴")
    return SignalResult("BTC RSI", 0.08, 0.0, round(rsi, 1), f"{rsi:.1f} — Neutral", "🟡")


def _score_btcdom(change_24h: Optional[float], value: Optional[float]) -> SignalResult:
    """BTCDOM direction — 12% weight. Falling BTCDOM = bullish for alts."""
    if change_24h is None:
        return SignalResult("BTCDOM", 0.12, 0.0, None, "N/A", "⚪")
    if change_24h < -0.3:
        return SignalResult("BTCDOM", 0.12, 1.0, round(value or 0, 1),
                            f"Falling {change_24h:+.1f}%/24h", "🟢")
    elif change_24h > 0.3:
        return SignalResult("BTCDOM", 0.12, -1.0, round(value or 0, 1),
                            f"Rising {change_24h:+.1f}%/24h", "🔴")
    return SignalResult("BTCDOM", 0.12, 0.0, round(value or 0, 1),
                        f"Flat {change_24h:+.1f}%/24h", "🟡")


def _score_usdt_d(change_24h: Optional[float], value: Optional[float]) -> SignalResult:
    """USDT.D direction — 12% weight. Falling = risk-on (bullish)."""
    if change_24h is None:
        return SignalResult("USDT.D", 0.12, 0.0, None, "N/A", "⚪")
    # USDT.D rising = risk-off = bearish, falling = risk-on = bullish
    if change_24h < -0.2:
        return SignalResult("USDT.D", 0.12, 1.0, round(value or 0, 2),
                            f"Falling {change_24h:+.2f}%/24h (risk-on)", "🟢")
    elif change_24h > 0.2:
        return SignalResult("USDT.D", 0.12, -1.0, round(value or 0, 2),
                            f"Rising {change_24h:+.2f}%/24h (risk-off)", "🔴")
    return SignalResult("USDT.D", 0.12, 0.0, round(value or 0, 2),
                        f"Flat {change_24h:+.2f}%/24h", "🟡")


def _score_total3(change_24h: Optional[float], value_b: Optional[float]) -> SignalResult:
    """TOTAL3 direction — 12% weight. Rising = bullish for alts."""
    if change_24h is None:
        return SignalResult("TOTAL3", 0.12, 0.0, None, "N/A", "⚪")
    if change_24h > 1.0:
        return SignalResult("TOTAL3", 0.12, 1.0, round(value_b or 0, 0),
                            f"Rising {change_24h:+.1f}%/24h", "🟢")
    elif change_24h < -1.0:
        return SignalResult("TOTAL3", 0.12, -1.0, round(value_b or 0, 0),
                            f"Falling {change_24h:+.1f}%/24h", "🔴")
    return SignalResult("TOTAL3", 0.12, 0.0, round(value_b or 0, 0),
                        f"Flat {change_24h:+.1f}%/24h", "🟡")


def _score_fear_greed(value: Optional[int]) -> SignalResult:
    """Fear & Greed — 8% weight."""
    if value is None:
        return SignalResult("Fear & Greed", 0.08, 0.0, None, "N/A", "⚪")
    if value >= 55:
        label = "Extreme Greed" if value >= 75 else "Greed"
        return SignalResult("Fear & Greed", 0.08, 1.0, value, f"{value} — {label}", "🟢")
    elif value <= 34:
        label = "Extreme Fear" if value <= 20 else "Fear"
        return SignalResult("Fear & Greed", 0.08, -1.0, value, f"{value} — {label}", "🔴")
    return SignalResult("Fear & Greed", 0.08, 0.0, value, f"{value} — Neutral", "🟡")


def _score_funding(rate_pct: Optional[float]) -> SignalResult:
    """BTC Funding Rate — 8% weight."""
    if rate_pct is None:
        return SignalResult("Funding", 0.08, 0.0, None, "N/A", "⚪")
    if rate_pct > 0.005:
        return SignalResult("Funding", 0.08, 1.0, round(rate_pct, 4),
                            f"{rate_pct:.4f}% — Positive (longs paying)", "🟢")
    elif rate_pct < -0.01:
        return SignalResult("Funding", 0.08, -1.0, round(rate_pct, 4),
                            f"{rate_pct:.4f}% — Negative (crowded shorts)", "🔴")
    return SignalResult("Funding", 0.08, 0.0, round(rate_pct, 4),
                        f"{rate_pct:.4f}% — Neutral", "🟡")


def _score_btc_structure(btc_price: float, structure_bias: str, structure_shift: float) -> SignalResult:
    """BTC Structure — 20% weight. Higher-timeframe structural bias from daily/weekly levels."""
    bias_upper = (structure_bias or "").upper()

    # Missing data: no price or no bias
    if not btc_price or not bias_upper:
        return SignalResult("BTC Structure", 0.20, 0.0, None, "N/A", "⚪")

    if bias_upper == "NEUTRAL":
        return SignalResult("BTC Structure", 0.20, 0.0, btc_price,
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
        return SignalResult("BTC Structure", 0.20, score, btc_price, label, emoji)

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
        return SignalResult("BTC Structure", 0.20, score, btc_price, label, emoji)

    # Unknown bias string
    return SignalResult("BTC Structure", 0.20, 0.0, btc_price,
                        f"{bias_upper} (${btc_price:,.0f})", "🟡")


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

async def calculate_direction_bias() -> DirectionUpdate:
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

    # ---- Context indicators (weight=0, informational) ----
    context_signals = [
        _context_total1(total1_t, total1_change),
        _context_total2(total2_b, total2_change),
        _context_others_d(others_d, others_d_change_pp),
        _context_stable_d(stable_d, stable_d_change_pp),
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
        context_signals=context_signals,
    )


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
