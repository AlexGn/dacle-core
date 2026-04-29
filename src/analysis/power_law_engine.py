"""BTC Power Law Engine — log-linear regression zone detection (Session 585)."""

import math
from dataclasses import dataclass, field
from typing import List, Optional

ZONE_CONFIG = {
    "DEEP": {"threshold_pct": -30, "sizing_multiplier": 2.0},
    "HEAVY": {"threshold_pct": -22, "sizing_multiplier": 1.5},
    "STANDARD": {"threshold_pct": -12, "sizing_multiplier": 1.0},
    "LIGHT": {"threshold_pct": 0, "sizing_multiplier": 0.5},
    "MIN": {"threshold_pct": 15, "sizing_multiplier": 0.25},
    "NONE": {"threshold_pct": 35, "sizing_multiplier": 0.0},
    "BUBBLE": {"threshold_pct": 999, "sizing_multiplier": -1.0},
}

MIN_WEEKLY_BARS = 50


@dataclass
class PowerLawResult:
    zone: str = "UNKNOWN"
    deviation_pct: float = 0.0
    regression_value: float = 0.0
    current_price: float = 0.0
    sizing_multiplier: float = 0.0
    bars_used: int = 0
    r_squared: float = 0.0
    error: Optional[str] = None


def _fit_log_linear(weekly_closes: List[float]):
    """Fit log(price) = a + b*i via least squares. Returns (a, b, r_squared)."""
    valid = [(i, p) for i, p in enumerate(weekly_closes) if not math.isnan(p) and p > 0]
    if len(valid) < 2:
        return 0.0, 0.0, 0.0

    n = len(valid)
    sum_x = sum(i for i, _ in valid)
    sum_y = sum(math.log(p) for _, p in valid)
    sum_xy = sum(i * math.log(p) for i, p in valid)
    sum_x2 = sum(i * i for i, _ in valid)
    sum_y2 = sum(math.log(p) ** 2 for _, p in valid)

    denom = n * sum_x2 - sum_x * sum_x
    if denom == 0:
        return sum_y / n, 0.0, 0.0

    b = (n * sum_xy - sum_x * sum_y) / denom
    a = (sum_y - b * sum_x) / n

    # R-squared
    mean_y = sum_y / n
    ss_res = sum((math.log(p) - (a + b * i)) ** 2 for i, p in valid)
    ss_tot = sum((math.log(p) - mean_y) ** 2 for _, p in valid)
    r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

    return a, b, r_squared


def _zone_for_deviation(deviation_pct: float) -> str:
    for zone, cfg in ZONE_CONFIG.items():
        if deviation_pct <= cfg["threshold_pct"]:
            return zone
    return "BUBBLE"


def compute_power_law(weekly_closes: List[float]) -> PowerLawResult:
    if not weekly_closes:
        return PowerLawResult(zone="UNKNOWN", error="empty_input")

    clean = [p for p in weekly_closes if not math.isnan(p) and p > 0]
    if len(clean) < MIN_WEEKLY_BARS:
        return PowerLawResult(
            zone="UNKNOWN",
            bars_used=len(clean),
            error=f"insufficient_bars:{len(clean)}<{MIN_WEEKLY_BARS}",
        )

    a, b, r_squared = _fit_log_linear(weekly_closes)
    last_idx = len(weekly_closes) - 1
    regression_value = math.exp(a + b * last_idx)
    current_price = weekly_closes[-1]

    if regression_value <= 0:
        return PowerLawResult(
            zone="UNKNOWN", bars_used=len(clean), error="zero_regression_value"
        )

    deviation_pct = ((current_price - regression_value) / regression_value) * 100.0
    zone = _zone_for_deviation(deviation_pct)
    sizing_multiplier = ZONE_CONFIG.get(zone, {}).get("sizing_multiplier", 0.0)

    return PowerLawResult(
        zone=zone,
        deviation_pct=round(deviation_pct, 2),
        regression_value=round(regression_value, 2),
        current_price=current_price,
        sizing_multiplier=sizing_multiplier,
        bars_used=len(clean),
        r_squared=round(r_squared, 4),
    )
