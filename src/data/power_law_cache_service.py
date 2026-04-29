"""Power Law Cache Service — JSON file cache with TTL + on-demand compute (Session 585)."""

import json
import logging
import os
import time
from pathlib import Path

from src.analysis.power_law_engine import compute_power_law, PowerLawResult

logger = logging.getLogger(__name__)

CACHE_PATH = Path("data/cache/power_law_zones.json")
CACHE_TTL_SECONDS = 3600


def _fetch_btc_weekly_ohlcv() -> list:
    """Fetch BTC weekly closes from Blofin. Returns list of closes (oldest first)."""
    try:
        from src.data.blofin_fetcher import BlofinFetcher
        fetcher = BlofinFetcher()
        ohlcv = fetcher.fetch_ohlcv("BTC-USDT", timeframe="1w", limit=500)
        if not ohlcv:
            raise ValueError("Empty OHLCV response")
        closes = [float(c[4]) for c in ohlcv]
        closes.sort(key=lambda _: ohlcv[closes.index(_)][0] if isinstance(ohlcv[0], (list, tuple)) else 0)
        return [float(c[4]) for c in ohlcv]
    except Exception:
        pass

    try:
        import ccxt
        exchange = ccxt.binance()
        ohlcv = exchange.fetch_ohlcv("BTC/USDT", "1w", limit=500)
        if ohlcv and len(ohlcv) >= 50:
            return [float(c[4]) for c in ohlcv]
    except Exception:
        pass

    raise RuntimeError("BTC weekly OHLCV unavailable from all sources")


def refresh_power_law_cache() -> PowerLawResult:
    try:
        closes = _fetch_btc_weekly_ohlcv()
    except Exception as e:
        logger.warning("Power law: OHLCV fetch failed: %s", e)
        return PowerLawResult(zone="UNKNOWN", error=f"fetch_failed:{e}")

    result = compute_power_law(closes)

    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "zone": result.zone,
            "deviation_pct": result.deviation_pct,
            "regression_value": result.regression_value,
            "current_price": result.current_price,
            "sizing_multiplier": result.sizing_multiplier,
            "bars_used": result.bars_used,
            "r_squared": result.r_squared,
            "error": result.error,
            "cached_at": time.time(),
        }
        tmp = CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload))
        tmp.rename(CACHE_PATH)
        logger.info(
            "Power law cache refreshed: zone=%s deviation=%.1f%% r2=%.3f",
            result.zone, result.deviation_pct, result.r_squared,
        )
    except Exception as e:
        logger.warning("Power law: cache write failed: %s", e)

    return result


def get_power_law_zone() -> PowerLawResult:
    try:
        if CACHE_PATH.exists():
            cached = json.loads(CACHE_PATH.read_text())
            age = time.time() - cached.get("cached_at", 0)
            if age < CACHE_TTL_SECONDS:
                return PowerLawResult(
                    zone=cached.get("zone", "UNKNOWN"),
                    deviation_pct=cached.get("deviation_pct", 0.0),
                    regression_value=cached.get("regression_value", 0.0),
                    current_price=cached.get("current_price", 0.0),
                    sizing_multiplier=cached.get("sizing_multiplier", 0.0),
                    bars_used=cached.get("bars_used", 0),
                    r_squared=cached.get("r_squared", 0.0),
                    error=cached.get("error"),
                )
    except Exception as e:
        logger.debug("Power law: cache read failed: %s", e)

    return PowerLawResult(zone="UNKNOWN", error="cache_miss_or_stale")
