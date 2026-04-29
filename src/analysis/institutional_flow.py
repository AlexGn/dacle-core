"""Institutional Flow Detector — GBTC/ETF/Coinbase signals (Session 585)."""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class InstitutionalFlowResult:
    composite_score: float = 0.5
    components: Dict[str, float] = field(default_factory=dict)
    is_bullish: bool = False
    error: Optional[str] = None


def _normalize_etf_flow(net_flow_5d: float) -> float:
    """Normalize ETF 5d flow to 0-1. 100M+ => 1.0, -100M => 0.0."""
    if net_flow_5d >= 100_000_000:
        return 1.0
    if net_flow_5d <= -100_000_000:
        return 0.0
    return 0.5 + (net_flow_5d / 200_000_000)


def _score_coinbase_premium(premium_pct: float) -> float:
    """Normalize Coinbase premium. 1%+ => 1.0, -1% => 0.0."""
    if premium_pct >= 1.0:
        return 1.0
    if premium_pct <= -1.0:
        return 0.0
    return 0.5 + (premium_pct / 2.0)


async def _fetch_gbtc_premium_async(timeout: float) -> dict:
    """Fetch GBTC premium vs NAV. Returns {premium_pct, score, error}."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                "https://api.coingecko.com/api/v3/coins/grayscale-bitcoin-trust",
                params={"localization": "false", "tickers": "false", "community_data": "false", "developer_data": "false"},
            )
            resp.raise_for_status()
            data = resp.json()
            gbtc_price = data.get("market_data", {}).get("current_price", {}).get("usd", 0)

            resp2 = await client.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "bitcoin", "vs_currencies": "usd"},
            )
            resp2.raise_for_status()
            btc_data = resp2.json()
            btc_price = btc_data.get("bitcoin", {}).get("usd", 0)

            if gbtc_price > 0 and btc_price > 0:
                premium = ((gbtc_price / btc_price) - 1) * 100
                score = 0.5 + (premium / 20.0)
                score = max(0.0, min(1.0, score))
                return {"premium_pct": round(premium, 2), "score": round(score, 3)}
            return {"premium_pct": None, "score": 0.5, "error": "zero_price"}
    except Exception as e:
        logger.debug("GBTC fetch failed: %s", e)
        return {"premium_pct": None, "score": 0.5, "error": str(e)}


async def _fetch_etf_flow_async(timeout: float) -> dict:
    """Fetch BTC ETF net flow from CoinGlass. Returns {flow_score, net_flow_5d, error}."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                "https://open-api-v3.coinglass.com/api/funding/BTCUSDT",
                headers={"coinglassSecret": "free"},
            )
            resp.raise_for_status()
            return {"flow_score": 0.5, "net_flow_5d": 0.0, "error": "not_implemented"}
    except Exception as e:
        logger.debug("ETF fetch failed: %s", e)
        return {"flow_score": 0.5, "net_flow_5d": 0.0, "error": str(e)}


async def _fetch_coinbase_premium_async(timeout: float) -> dict:
    """Fetch Coinbase-Binance premium via CCXT. Returns {premium_pct, score, error}."""
    try:
        import ccxt
        cb = ccxt.coinbase()
        bn = ccxt.binance()
        cb_ticker = cb.fetch_ticker("BTC/USD")
        bn_ticker = bn.fetch_ticker("BTC/USDT")
        cb_ask = float(cb_ticker.get("ask", 0))
        bn_ask = float(bn_ticker.get("ask", 0))
        if cb_ask > 0 and bn_ask > 0:
            premium = ((cb_ask - bn_ask) / bn_ask) * 100
            score = _score_coinbase_premium(premium)
            return {"premium_pct": round(premium, 2), "score": round(score, 3)}
        return {"premium_pct": None, "score": 0.5, "error": "zero_price"}
    except Exception as e:
        logger.debug("Coinbase premium fetch failed: %s", e)
        return {"premium_pct": None, "score": 0.5, "error": str(e)}


async def compute_institutional_flow(
    timeout_sec: float = 10.0,
    weights: Optional[dict] = None,
) -> InstitutionalFlowResult:
    """Aggregate institutional flow signals. Fail-closed: defaults to neutral."""
    if weights is None:
        weights = {"gbtc_premium": 0.30, "etf_flow": 0.30, "coinbase_premium": 0.40}

    gbtc, etf, coinbase = await asyncio.gather(
        _fetch_gbtc_premium_async(timeout_sec),
        _fetch_etf_flow_async(timeout_sec),
        _fetch_coinbase_premium_async(timeout_sec),
    )

    components = {
        "gbtc": gbtc.get("score") if gbtc.get("score") is not None else 0.5,
        "etf": etf.get("flow_score") if etf.get("flow_score") is not None else 0.5,
        "coinbase": coinbase.get("score") if coinbase.get("score") is not None else 0.5,
    }

    composite = (
        weights.get("gbtc_premium", 0.30) * components["gbtc"]
        + weights.get("etf_flow", 0.30) * components["etf"]
        + weights.get("coinbase_premium", 0.40) * components["coinbase"]
    )

    errors = []
    for name, result in [("gbtc", gbtc), ("etf", etf), ("coinbase", coinbase)]:
        if result.get("error"):
            errors.append(f"{name}:{result['error']}")

    error_str = "; ".join(errors) if errors else None
    is_bullish = composite > 0.6

    return InstitutionalFlowResult(
        composite_score=round(composite, 3),
        components={k: round(v, 3) for k, v in components.items()},
        is_bullish=is_bullish,
        error=error_str,
    )
