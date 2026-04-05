"""
Capital Rotation Detector

Detects capital rotation between crypto sectors by comparing cipher momentum
signals across sector indices (MEME.C, AI.C, LAYER1.C, DEPIN.C, RWA.C, SOLANA.C)
and cross-referencing with macro context (BTC.D, USDT.D).

Rotation logic:
  - A sector is "heating up"  when its cipher signal is REVERSAL_UP or BULLISH_MOMENTUM
  - A sector is "cooling down" when its cipher signal is REVERSAL_DOWN or BEARISH_MOMENTUM
  - Rotation is detected when ≥1 sector is cooling AND ≥1 different sector is heating,
    in the same snapshot window
  - USDT.D falling + sector heating = confirmed capital inflow (strong conviction)
  - USDT.D rising + all sectors cooling = risk-off / cash rotation

Output:
    RotationSignal dataclass with from_sector, to_sector, confidence, context.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from src.data.cipher_cache_service import get_all_cipher_snapshots
from src.ta.cipher_engine import CipherSnapshot, CompositeSignal

logger = logging.getLogger(__name__)

# Sector indices available for rotation analysis
SECTOR_INDICES = ["MEME.C", "AI.C", "LAYER1.C", "DEPIN.C", "RWA.C", "SOLANA.C"]

# Macro context indices
MACRO_INDICES = ["BTC.D", "USDT.D", "TOTAL", "TOTAL3"]

# Confidence floor — rotation signals below this are suppressed
MIN_ROTATION_CONFIDENCE = 0.25


@dataclass
class SectorMomentum:
    index_key: str
    signal: str
    confidence: float
    wt1: Optional[float] = None

    @property
    def is_heating(self) -> bool:
        return self.signal in (CompositeSignal.REVERSAL_UP, CompositeSignal.BULLISH_MOMENTUM)

    @property
    def is_cooling(self) -> bool:
        return self.signal in (CompositeSignal.REVERSAL_DOWN, CompositeSignal.BEARISH_MOMENTUM)

    @property
    def score(self) -> float:
        """Signed score: +heating, -cooling, 0 neutral."""
        if self.is_heating:
            return self.confidence
        if self.is_cooling:
            return -self.confidence
        return 0.0


@dataclass
class RotationSignal:
    """Represents detected capital flow between sectors."""
    from_sectors: List[str]          # sectors losing momentum
    to_sectors: List[str]            # sectors gaining momentum
    confidence: float                # 0.0 – 1.0
    context: str                     # human-readable context string
    usdt_d_falling: Optional[bool] = None  # True = confirmed capital inflow
    btc_d_direction: Optional[str] = None  # "rising" | "falling" | "neutral"
    risk_off: bool = False           # True when USDT.D rising + all cooling
    timestamp: Optional[str] = None
    sector_scores: Dict[str, float] = field(default_factory=dict)


def detect_rotation(resolution: str = "4H") -> Optional[RotationSignal]:
    """
    Read latest cipher snapshots and detect sector rotation.

    Returns a RotationSignal if rotation is detected, None if no rotation or
    insufficient data.
    """
    snapshots = get_all_cipher_snapshots(resolution=resolution)
    if not snapshots:
        logger.debug("[rotation] No cipher snapshots available — cache empty or not yet built")
        return None

    # Build sector momentum objects
    sector_moms: List[SectorMomentum] = []
    for key in SECTOR_INDICES:
        snap = snapshots.get(key)
        if snap is None or snap.error:
            continue
        wt1 = snap.wavetrend.wt1 if snap.wavetrend else None
        sector_moms.append(
            SectorMomentum(
                index_key=key,
                signal=snap.signal,
                confidence=snap.confidence,
                wt1=wt1,
            )
        )

    if len(sector_moms) < 2:
        logger.debug(f"[rotation] Only {len(sector_moms)} sector snapshots — need ≥2")
        return None

    heating = [s for s in sector_moms if s.is_heating]
    cooling = [s for s in sector_moms if s.is_cooling]

    # --- Macro context ---
    usdt_snap = snapshots.get("USDT.D")
    btc_d_snap = snapshots.get("BTC.D")

    usdt_d_falling: Optional[bool] = None
    btc_d_direction: Optional[str] = None

    if usdt_snap and not usdt_snap.error:
        if usdt_snap.signal in (CompositeSignal.BEARISH_MOMENTUM, CompositeSignal.REVERSAL_DOWN):
            usdt_d_falling = True
        elif usdt_snap.signal in (CompositeSignal.BULLISH_MOMENTUM, CompositeSignal.REVERSAL_UP):
            usdt_d_falling = False

    if btc_d_snap and not btc_d_snap.error:
        if btc_d_snap.signal in (CompositeSignal.BULLISH_MOMENTUM, CompositeSignal.REVERSAL_UP):
            btc_d_direction = "rising"
        elif btc_d_snap.signal in (CompositeSignal.BEARISH_MOMENTUM, CompositeSignal.REVERSAL_DOWN):
            btc_d_direction = "falling"
        else:
            btc_d_direction = "neutral"

    # --- Risk-off detection ---
    all_cooling = all(s.is_cooling for s in sector_moms) and len(cooling) >= 3
    risk_off = all_cooling and usdt_d_falling is False  # USDT.D rising (not falling) + all cool

    # --- Rotation detection ---
    if not heating or not cooling:
        if risk_off:
            return RotationSignal(
                from_sectors=[s.index_key for s in cooling],
                to_sectors=[],
                confidence=_avg_conf(cooling),
                context="Risk-off: all sectors cooling, USDT.D rising — cash rotation",
                usdt_d_falling=usdt_d_falling,
                btc_d_direction=btc_d_direction,
                risk_off=True,
                timestamp=_latest_ts(snapshots, cooling),
                sector_scores={s.index_key: s.score for s in sector_moms},
            )
        logger.debug(
            f"[rotation] No rotation — heating={[s.index_key for s in heating]}, "
            f"cooling={[s.index_key for s in cooling]}"
        )
        return None

    # Confidence = average of heating + cooling confidences, boosted by USDT.D confirmation
    base_conf = (_avg_conf(heating) + _avg_conf(cooling)) / 2.0
    if usdt_d_falling is True:
        base_conf = min(1.0, base_conf * 1.25)

    if base_conf < MIN_ROTATION_CONFIDENCE:
        logger.debug(
            f"[rotation] Rotation detected but confidence {base_conf:.2f} below threshold "
            f"{MIN_ROTATION_CONFIDENCE}"
        )
        return None

    from_keys = [s.index_key for s in cooling]
    to_keys = [s.index_key for s in heating]

    context_parts = [
        f"Capital rotating FROM {', '.join(from_keys)} → TO {', '.join(to_keys)}"
    ]
    if usdt_d_falling is True:
        context_parts.append("USDT.D falling (confirmed inflow)")
    elif usdt_d_falling is False:
        context_parts.append("USDT.D rising (defensive, tempered confidence)")
    if btc_d_direction:
        context_parts.append(f"BTC.D {btc_d_direction}")

    return RotationSignal(
        from_sectors=from_keys,
        to_sectors=to_keys,
        confidence=round(base_conf, 3),
        context=" | ".join(context_parts),
        usdt_d_falling=usdt_d_falling,
        btc_d_direction=btc_d_direction,
        risk_off=False,
        timestamp=_latest_ts(snapshots, heating + cooling),
        sector_scores={s.index_key: round(s.score, 3) for s in sector_moms},
    )


def get_sector_momentum_summary(resolution: str = "4H") -> Dict[str, SectorMomentum]:
    """Return SectorMomentum for all available sector indices."""
    snapshots = get_all_cipher_snapshots(resolution=resolution)
    result = {}
    for key in SECTOR_INDICES:
        snap = snapshots.get(key)
        if snap is None or snap.error:
            continue
        wt1 = snap.wavetrend.wt1 if snap.wavetrend else None
        result[key] = SectorMomentum(
            index_key=key,
            signal=snap.signal,
            confidence=snap.confidence,
            wt1=wt1,
        )
    return result


def _avg_conf(sectors: List[SectorMomentum]) -> float:
    if not sectors:
        return 0.0
    return sum(s.confidence for s in sectors) / len(sectors)


def _latest_ts(
    snapshots: Dict[str, CipherSnapshot],
    sectors: List[SectorMomentum],
) -> Optional[str]:
    """Return the most recent timestamp from the given sector snapshots."""
    ts_list = [
        snapshots[s.index_key].timestamp
        for s in sectors
        if s.index_key in snapshots and snapshots[s.index_key].timestamp
    ]
    return max(ts_list) if ts_list else None
