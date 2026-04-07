"""
Cipher Cache Service — Orchestrates OHLCV Fetching + Cipher Computation

Runs every 4 hours (or on-demand), computes cipher snapshots for all tracked
indices, and persists results to `data/cache/cipher_snapshots.json`.

Consumers (market_direction_scorer, capital_rotation_detector, permission_writer)
call `get_cipher_snapshot()` or `get_all_cipher_snapshots()` to read without
triggering a fetch.

Usage:
    # Scheduled refresh (call from cron / monitor script):
    from src.data.cipher_cache_service import refresh_cipher_cache
    refresh_cipher_cache()

    # Read from any consumer:
    from src.data.cipher_cache_service import get_cipher_snapshot, get_all_cipher_snapshots
    snap = get_cipher_snapshot("BTC.D")
    all_snaps = get_all_cipher_snapshots()
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional

from src.data.indices_ohlcv_fetcher import (
    ALL_INDICES,
    TIER1_INDICES,
    TIER2_INDICES,
    fetch_and_cache_all,
    load_ohlcv_series,
    get_series_length,
)
from src.ta.cipher_engine import CipherSnapshot, run_cipher_on_series

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
CIPHER_CACHE_PATH = PROJECT_ROOT / "data" / "cache" / "cipher_snapshots.json"
CIPHER_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)

# Stale after 5 hours — slightly longer than the 4H fetch cadence to avoid gaps
CACHE_TTL_SECONDS = 5 * 3600

# Indices computed by default (Tier 1 + 2); Tier 3 excluded (DXY volume-less)
DEFAULT_INDICES = list({**TIER1_INDICES, **TIER2_INDICES}.keys())

# Minimum bars before we attempt cipher computation
MIN_BARS_FOR_CIPHER = 35


def refresh_cipher_cache(
    tiers: Optional[List[int]] = None,
    resolution: str = "4H",
    skip_fetch: bool = False,
) -> Dict[str, CipherSnapshot]:
    """
    Fetch fresh OHLCV candles (unless skip_fetch=True), compute cipher snapshots
    for all target indices, and write results to the JSON cache.

    Args:
        tiers: OHLCV fetch tiers [1, 2, 3]. Defaults to [1, 2].
        resolution: Timeframe for cipher computation ("4H" or "1D").
        skip_fetch: If True, compute from existing cached OHLCV without re-fetching.

    Returns:
        Dict of index_key -> CipherSnapshot (may be empty on errors).
    """
    if not skip_fetch:
        logger.info("[cipher_cache] Fetching OHLCV...")
        fetch_and_cache_all(tiers=tiers or [1, 2])

    target_keys = DEFAULT_INDICES if (tiers is None or set(tiers) <= {1, 2}) else list(ALL_INDICES.keys())

    snapshots: Dict[str, CipherSnapshot] = {}
    for key in target_keys:
        bars = get_series_length(key, resolution)
        if bars < MIN_BARS_FOR_CIPHER:
            logger.debug(
                f"[cipher_cache] {key}/{resolution}: only {bars} bars "
                f"(need {MIN_BARS_FOR_CIPHER}), skipping"
            )
            continue
        try:
            series = load_ohlcv_series(key, resolution, limit=500)
            snap = run_cipher_on_series(key, resolution, series)
            snapshots[key] = snap
            logger.debug(
                f"[cipher_cache] {key}/{resolution}: "
                f"signal={snap.signal} conf={snap.confidence:.2f} bars={snap.bars_used}"
            )
        except Exception as e:
            logger.warning(f"[cipher_cache] Cipher computation failed for {key}/{resolution}: {e}")

    _write_cache(snapshots, resolution)
    logger.info(
        f"[cipher_cache] Refresh complete: {len(snapshots)}/{len(target_keys)} indices computed"
    )
    return snapshots


def _write_cache(snapshots: Dict[str, CipherSnapshot], resolution: str) -> None:
    """Persist snapshots to JSON cache file."""
    try:
        existing: dict = {}
        if CIPHER_CACHE_PATH.exists():
            try:
                existing = json.loads(CIPHER_CACHE_PATH.read_text())
            except Exception:
                existing = {}

        # Update only the resolution bucket that was just computed
        if "by_resolution" not in existing:
            existing["by_resolution"] = {}
        existing["by_resolution"][resolution] = {
            key: _snapshot_to_dict(snap)
            for key, snap in snapshots.items()
        }
        existing["last_updated"] = {
            **existing.get("last_updated", {}),
            resolution: int(time.time()),
        }

        CIPHER_CACHE_PATH.write_text(json.dumps(existing, indent=2))
    except Exception as e:
        logger.error(f"[cipher_cache] Failed to write cache: {e}")


def _snapshot_to_dict(snap: CipherSnapshot) -> dict:
    """Convert CipherSnapshot to a JSON-serialisable dict."""
    d = asdict(snap)
    return d


def _dict_to_snapshot(d: dict) -> CipherSnapshot:
    """Reconstruct a CipherSnapshot from cached dict."""
    from src.ta.cipher_engine import (
        WaveTrendSnapshot,
        MFISnapshot,
        VWAPSnapshot,
        CVDSnapshot,
        MACDSnapshot,
        StochasticSnapshot,
        MomentumSnapshot,
    )

    def _maybe(cls, val):
        if val is None:
            return None
        return cls(**val)

    return CipherSnapshot(
        index_key=d["index_key"],
        resolution=d["resolution"],
        timestamp=d["timestamp"],
        wavetrend=_maybe(WaveTrendSnapshot, d.get("wavetrend")),
        mfi=_maybe(MFISnapshot, d.get("mfi")),
        vwap=_maybe(VWAPSnapshot, d.get("vwap")),
        cvd=_maybe(CVDSnapshot, d.get("cvd")),
        macd=_maybe(MACDSnapshot, d.get("macd")),
        stochastic=_maybe(StochasticSnapshot, d.get("stochastic")),
        momentum=_maybe(MomentumSnapshot, d.get("momentum")),
        choppiness=d.get("choppiness"),
        signal=d.get("signal", "NEUTRAL"),
        confidence=d.get("confidence", 0.0),
        reasons=d.get("reasons", []),
        bars_used=d.get("bars_used", 0),
        error=d.get("error"),
    )


def _load_cache(resolution: str = "4H") -> Dict[str, CipherSnapshot]:
    """Load all snapshots for a resolution from the JSON cache."""
    if not CIPHER_CACHE_PATH.exists():
        return {}
    try:
        data = json.loads(CIPHER_CACHE_PATH.read_text())
        bucket = data.get("by_resolution", {}).get(resolution, {})
        return {key: _dict_to_snapshot(val) for key, val in bucket.items()}
    except Exception as e:
        logger.warning(f"[cipher_cache] Failed to load cache: {e}")
        return {}


def _cache_age_seconds(resolution: str = "4H") -> Optional[float]:
    """Return age of cache in seconds, or None if cache doesn't exist."""
    if not CIPHER_CACHE_PATH.exists():
        return None
    try:
        data = json.loads(CIPHER_CACHE_PATH.read_text())
        ts = data.get("last_updated", {}).get(resolution)
        if ts is None:
            return None
        return time.time() - ts
    except Exception:
        return None


def get_cipher_snapshot(
    index_key: str,
    resolution: str = "4H",
    allow_stale: bool = True,
    compute_on_miss: bool = True,
) -> Optional[CipherSnapshot]:
    """
    Return the cached CipherSnapshot for an index or token.

    Args:
        index_key: e.g. "BTC.D", "MEME.C", "BTC", "ETH"
        resolution: "4H" or "1D"
        allow_stale: If False, returns None when cache age > CACHE_TTL_SECONDS.
        compute_on_miss: If True and cache miss, compute from OHLCV and cache.

    Returns:
        CipherSnapshot or None if not available.
    """
    if not allow_stale:
        age = _cache_age_seconds(resolution)
        if age is None or age > CACHE_TTL_SECONDS:
            return None

    cache = _load_cache(resolution)
    snapshot = cache.get(index_key)

    # Cache miss - compute on-the-fly if requested
    if snapshot is None and compute_on_miss:
        snapshot = compute_and_cache_token_snapshot(index_key, resolution)

    return snapshot


def get_all_cipher_snapshots(
    resolution: str = "4H",
    allow_stale: bool = True,
) -> Dict[str, CipherSnapshot]:
    """
    Return all cached CipherSnapshots for a resolution.

    Returns empty dict if cache doesn't exist or is stale (when allow_stale=False).
    """
    if not allow_stale:
        age = _cache_age_seconds(resolution)
        if age is None or age > CACHE_TTL_SECONDS:
            return {}

    return _load_cache(resolution)


def compute_and_cache_token_snapshot(
    token_symbol: str,
    resolution: str = "4H",
) -> Optional[CipherSnapshot]:
    """
    Compute Cipher snapshot for a token (not just indices) and cache it.

    This is used by the Entry Model to get cipher snapshots for tokens like
    BTC, ETH, SOL, etc. that have OHLCV data in the rolling cache.

    Args:
        token_symbol: Token symbol (e.g., "BTC", "ETH")
        resolution: Timeframe ("4H" or "1D")

    Returns:
        CipherSnapshot or None if insufficient data
    """
    # Load OHLCV series from rolling cache
    series = load_ohlcv_series(token_symbol, resolution, limit=500)
    if not series or len(series.get("closes", [])) < MIN_BARS_FOR_CIPHER:
        logger.debug(
            f"[cipher_cache] {token_symbol}/{resolution}: insufficient bars for cipher"
        )
        return None

    try:
        snap = run_cipher_on_series(token_symbol, resolution, series)

        # Write to cache (merge with existing)
        existing = _load_cache(resolution)
        existing[token_symbol] = snap
        _write_cache(existing, resolution)

        logger.debug(
            f"[cipher_cache] {token_symbol}/{resolution}: "
            f"signal={snap.signal} conf={snap.confidence:.2f} bars={snap.bars_used}"
        )
        return snap
    except Exception as e:
        logger.warning(f"[cipher_cache] Cipher computation failed for {token_symbol}/{resolution}: {e}")
        return None


def get_cipher_composite_score(resolution: str = "4H") -> float:
    """
    Aggregate cipher momentum across Tier 1 indices into a single score [-1, +1].

    Used by market_direction_scorer as signal #16 (cipher_composite).

    Scoring per index:
      REVERSAL_UP    → +1.0 × confidence
      BULLISH_MOMENTUM → +0.6 × confidence
      REVERSAL_DOWN  → -1.0 × confidence
      BEARISH_MOMENTUM → -0.6 × confidence
      CHOPPY / NEUTRAL → 0.0
    """
    tier1_keys = list(TIER1_INDICES.keys())
    snapshots = get_all_cipher_snapshots(resolution=resolution)

    scores = []
    for key in tier1_keys:
        snap = snapshots.get(key)
        if snap is None or snap.error:
            continue
        sig = snap.signal
        conf = snap.confidence
        if sig == "REVERSAL_UP":
            scores.append(1.0 * conf)
        elif sig == "BULLISH_MOMENTUM":
            scores.append(0.6 * conf)
        elif sig == "REVERSAL_DOWN":
            scores.append(-1.0 * conf)
        elif sig == "BEARISH_MOMENTUM":
            scores.append(-0.6 * conf)
        else:
            scores.append(0.0)

    if not scores:
        return 0.0
    return sum(scores) / len(scores)
