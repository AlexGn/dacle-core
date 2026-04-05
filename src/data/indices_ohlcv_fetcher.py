"""
Indices OHLCV Fetcher — TradingView Candle Data for Cipher Engine

Fetches current 4H and 1D OHLC snapshots for crypto indices via TradingView scanner
and accumulates them in rolling file caches. Each poll appends one row, building a
historical series over time that the cipher engine can consume.

Indices tracked:
  Tier 1 (core dominance):   BTC.D, USDT.D, TOTAL, TOTAL2, TOTAL3, OTHERS.D, ETH/BTC
  Tier 2 (sector rotation):  MEME.C, AI.C, LAYER1.C, DEPIN.C, RWA.C, SOLANA.C
  Tier 3 (macro):            DXY (via ECONOMICS:DXY), BTCUSDT as BTC proxy

TradingView scanner supports timeframe-suffixed columns:
  close|240  = current 4H candle close
  open|240   = current 4H candle open
  high|240   = current 4H candle high
  low|240    = current 4H candle low
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
OHLCV_CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "indices_ohlcv"
OHLCV_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Max rows to keep per index per timeframe (rolling window)
MAX_ROWS_4H = 500   # ~83 days
MAX_ROWS_1D = 300   # ~300 days

# Tier 1: core macro indices
TIER1_INDICES = {
    "BTC.D":    "CRYPTOCAP:BTC.D",
    "USDT.D":   "CRYPTOCAP:USDT.D",
    "TOTAL":    "CRYPTOCAP:TOTAL",
    "TOTAL2":   "CRYPTOCAP:TOTAL2",
    "TOTAL3":   "CRYPTOCAP:TOTAL3",
    "OTHERS.D": "CRYPTOCAP:OTHERS.D",
    "ETH/BTC":  "CRYPTOCAP:ETH/BTC",
}

# Tier 2: sector rotation indices
TIER2_INDICES = {
    "MEME.C":    "CRYPTOCAP:MEME.C",
    "AI.C":      "CRYPTOCAP:AI.C",
    "LAYER1.C":  "CRYPTOCAP:LAYER1.C",
    "DEPIN.C":   "CRYPTOCAP:DEPIN.C",
    "RWA.C":     "CRYPTOCAP:RWA.C",
    "SOLANA.C":  "CRYPTOCAP:SOLANA.C",
}

# Tier 3: macro
TIER3_INDICES = {
    "DXY": "ECONOMICS:DXY",
}

ALL_INDICES = {**TIER1_INDICES, **TIER2_INDICES, **TIER3_INDICES}


def _cache_file(index_key: str, resolution: str) -> Path:
    """Return path to rolling JSONL cache file for an index+resolution."""
    safe_key = index_key.replace("/", "_").replace(".", "_")
    return OHLCV_CACHE_DIR / f"{safe_key}_{resolution}.jsonl"


def load_ohlcv_series(index_key: str, resolution: str = "4H", limit: int = 300) -> Dict[str, List]:
    """
    Load accumulated OHLCV series from file cache.

    Returns dict with keys: opens, highs, lows, closes, volumes, timestamps.
    Returns empty lists if no data available.
    """
    path = _cache_file(index_key, resolution)
    if not path.exists():
        return {"opens": [], "highs": [], "lows": [], "closes": [], "volumes": [], "timestamps": []}

    rows = []
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except Exception as e:
        logger.warning(f"Failed to read OHLCV cache for {index_key}/{resolution}: {e}")
        return {"opens": [], "highs": [], "lows": [], "closes": [], "volumes": [], "timestamps": []}

    # Take most recent `limit` rows
    rows = rows[-limit:]
    return {
        "opens":      [r["o"] for r in rows],
        "highs":      [r["h"] for r in rows],
        "lows":       [r["l"] for r in rows],
        "closes":     [r["c"] for r in rows],
        "volumes":    [r.get("v", 0.0) for r in rows],
        "timestamps": [r["ts"] for r in rows],
    }


def _append_to_cache(index_key: str, resolution: str, row: dict, max_rows: int) -> None:
    """Append one OHLCV row to the rolling cache file."""
    path = _cache_file(index_key, resolution)

    # Read existing rows
    rows = []
    if path.exists():
        try:
            with open(path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))
        except Exception:
            rows = []

    # Skip if last row has same timestamp (duplicate candle)
    if rows and rows[-1].get("ts") == row.get("ts"):
        return

    rows.append(row)

    # Keep rolling window
    if len(rows) > max_rows:
        rows = rows[-max_rows:]

    # Rewrite file
    try:
        with open(path, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
    except Exception as e:
        logger.warning(f"Failed to write OHLCV cache for {index_key}/{resolution}: {e}")


def _tv_candle_timestamp(resolution: str) -> str:
    """
    Compute a canonical timestamp for the current candle of the given resolution.
    This buckets the current UTC time to the candle boundary.
    """
    now = datetime.now(timezone.utc)
    if resolution == "4H":
        # Bucket to 4H: 0, 4, 8, 12, 16, 20
        hour_bucket = (now.hour // 4) * 4
        return f"{now.year:04d}-{now.month:02d}-{now.day:02d}T{hour_bucket:02d}:00Z"
    elif resolution == "1D":
        return f"{now.year:04d}-{now.month:02d}-{now.day:02d}T00:00Z"
    else:
        return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_and_cache_all(tiers: Optional[List[int]] = None) -> Dict[str, Dict]:
    """
    Fetch current OHLC snapshot for all tracked indices via TradingView scanner
    and append to rolling caches.

    Args:
        tiers: List of tiers to fetch (1, 2, 3). Defaults to [1, 2].

    Returns:
        Dict mapping index_key -> {4H: {o,h,l,c}, 1D: {o,h,l,c}, success: bool}
    """
    if tiers is None:
        tiers = [1, 2]

    target_indices: Dict[str, str] = {}
    if 1 in tiers:
        target_indices.update(TIER1_INDICES)
    if 2 in tiers:
        target_indices.update(TIER2_INDICES)
    if 3 in tiers:
        target_indices.update(TIER3_INDICES)

    results: Dict[str, Dict] = {}

    try:
        tickers = list(target_indices.values())

        # Fetch 4H OHLC
        columns_4h = [
            "open|240", "high|240", "low|240", "close|240", "volume|240"
        ]
        # Fetch 1D OHLC
        columns_1d = [
            "open|D", "high|D", "low|D", "close|D", "volume|D"
        ]

        all_columns = columns_4h + columns_1d

        session = requests.Session()
        session.headers.update({"Content-Type": "application/json"})

        response = session.post(
            "https://scanner.tradingview.com/global/scan",
            json={"symbols": {"tickers": tickers}, "columns": all_columns},
            timeout=15,
        )
        response.raise_for_status()
        tv_data = response.json()

        data_map: Dict[str, List] = {}
        for item in tv_data.get("data", []):
            data_map[item["s"]] = item["d"]

    except Exception as e:
        logger.error(f"TradingView OHLCV fetch failed: {e}")
        return results

    ts_4h = _tv_candle_timestamp("4H")
    ts_1d = _tv_candle_timestamp("1D")

    for key, tv_symbol in target_indices.items():
        vals = data_map.get(tv_symbol)
        if not vals or len(vals) < 10:
            results[key] = {"success": False, "reason": "no_data"}
            continue

        # 4H columns: [open|240, high|240, low|240, close|240, volume|240]
        o4, h4, l4, c4, v4 = vals[0], vals[1], vals[2], vals[3], vals[4]
        # 1D columns: [open|D, high|D, low|D, close|D, volume|D]
        od, hd, ld, cd, vd = vals[5], vals[6], vals[7], vals[8], vals[9]

        # Validate: all values must be numeric and non-None
        def _ok(v) -> bool:
            return v is not None and isinstance(v, (int, float)) and v == v  # NaN check

        success_4h = all(_ok(x) for x in [o4, h4, l4, c4])
        success_1d = all(_ok(x) for x in [od, hd, ld, cd])

        if success_4h:
            row_4h = {
                "ts": ts_4h,
                "o": float(o4), "h": float(h4), "l": float(l4), "c": float(c4),
                "v": float(v4) if _ok(v4) else 0.0,
            }
            _append_to_cache(key, "4H", row_4h, MAX_ROWS_4H)

        if success_1d:
            row_1d = {
                "ts": ts_1d,
                "o": float(od), "h": float(hd), "l": float(ld), "c": float(cd),
                "v": float(vd) if _ok(vd) else 0.0,
            }
            _append_to_cache(key, "1D", row_1d, MAX_ROWS_1D)

        results[key] = {
            "success": success_4h or success_1d,
            "4H": {"o": o4, "h": h4, "l": l4, "c": c4} if success_4h else None,
            "1D": {"o": od, "h": hd, "l": ld, "c": cd} if success_1d else None,
        }

    logger.info(
        f"OHLCV fetch complete: {sum(1 for v in results.values() if v.get('success'))}/"
        f"{len(results)} indices updated"
    )
    return results


def get_series_length(index_key: str, resolution: str = "4H") -> int:
    """Return number of cached bars for an index."""
    path = _cache_file(index_key, resolution)
    if not path.exists():
        return 0
    count = 0
    try:
        with open(path, "r") as f:
            for line in f:
                if line.strip():
                    count += 1
    except Exception:
        pass
    return count


def get_available_indices(min_bars: int = 50, resolution: str = "4H") -> List[str]:
    """Return list of index keys with at least min_bars of cached data."""
    available = []
    for key in ALL_INDICES:
        if get_series_length(key, resolution) >= min_bars:
            available.append(key)
    return available
