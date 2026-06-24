"""Sovereign position limits — immutable caps loaded once at process start.

Mutating PositionLimits at runtime raises SovereignImmutableError. Changes
require a code deploy. Loaded from config/sovereign.yaml.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Tuple, Union

import yaml

from dacle_core.governance.contracts import SovereignImmutableError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PositionLimits:
    """Immutable position caps."""

    max_total_notional_usd: float
    max_per_pillar_notional_usd: Mapping[str, float]
    max_daily_loss_usd: float
    max_open_positions: int
    max_correlation: float

    def __post_init__(self):
        if self.max_total_notional_usd <= 0:
            raise SovereignImmutableError("max_total_notional_usd must be > 0")
        if self.max_daily_loss_usd <= 0:
            raise SovereignImmutableError("max_daily_loss_usd must be > 0")
        if self.max_open_positions <= 0:
            raise SovereignImmutableError("max_open_positions must be > 0")
        if not 0.0 < self.max_correlation <= 1.0:
            raise SovereignImmutableError("max_correlation must be in (0,1]")
        if not self.max_per_pillar_notional_usd:
            raise SovereignImmutableError("max_per_pillar_notional_usd must not be empty")
        for pillar, val in self.max_per_pillar_notional_usd.items():
            if val <= 0:
                raise SovereignImmutableError(f"max_per_pillar_notional_usd[{pillar}] must be > 0")


class PositionLimitGuard:
    """Boundary check helpers. Pure dataclass reads — safe on the hot path."""

    def __init__(self, limits: PositionLimits):
        self._limits = limits

    @property
    def limits(self) -> PositionLimits:
        return self._limits

    def __setattr__(self, name, value):
        if name == "limits":
            raise SovereignImmutableError("PositionLimitGuard.limits is immutable")
        if name == "_limits" and getattr(self, "_limits", None) is not None:
            raise SovereignImmutableError("PositionLimitGuard.limits is immutable")
        object.__setattr__(self, name, value)

    def check_total_notional(self, current_total_usd: float, new_intent_notional_usd: float) -> Tuple[bool, str]:
        projected = float(current_total_usd) + float(new_intent_notional_usd)
        if projected > self._limits.max_total_notional_usd:
            return False, f"total_notional projected={projected:.2f} > cap={self._limits.max_total_notional_usd:.2f}"
        return True, "ok"

    def check_position_count(self, current_count: int) -> Tuple[bool, str]:
        if current_count >= self._limits.max_open_positions:
            return False, f"open_positions={current_count} >= cap={self._limits.max_open_positions}"
        return True, "ok"

    def check_daily_loss(self, current_loss_usd: float) -> Tuple[bool, str]:
        if current_loss_usd >= self._limits.max_daily_loss_usd:
            return False, f"daily_loss={current_loss_usd:.2f} >= cap={self._limits.max_daily_loss_usd:.2f}"
        return True, "ok"


def load_position_limits(yaml_path: str = "config/sovereign.yaml") -> PositionLimits:
    """Load position limits from sovereign.yaml. Raises SovereignImmutableError on bad config."""
    path = Path(yaml_path)
    if not path.exists():
        raise SovereignImmutableError(f"sovereign config not found: {path}")
    try:
        data = yaml.safe_load(path.read_text())
    except Exception as e:
        raise SovereignImmutableError(f"failed parsing {path}: {e}")
    section = (data or {}).get("sovereign", {}).get("position_limits")
    if not section:
        raise SovereignImmutableError(f"no sovereign.position_limits in {path}")
    try:
        per_pillar = dict(section["max_per_pillar_notional_usd"])
        return PositionLimits(
            max_total_notional_usd=float(section["max_total_notional_usd"]),
            max_per_pillar_notional_usd=per_pillar,
            max_daily_loss_usd=float(section["max_daily_loss_usd"]),
            max_open_positions=int(section["max_open_positions"]),
            max_correlation=float(section["max_correlation"]),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise SovereignImmutableError(f"malformed position_limits config: {e}")