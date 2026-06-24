"""
Smart Level Generator — Confluence-based entry/SL/TP level calculation.

Combines multiple pre-computed data sources (Fib retracement, S/R clusters,
Volume Profile, Order Blocks, Fair Value Gaps) into unified SmartLevels
with confluence scoring and quality classification.

Usage:
    from dacle_core.analysis.smart_level_generator import SmartLevelGenerator

    gen = SmartLevelGenerator()
    levels = gen.generate_levels(
        current_price=1.20,
        direction="SHORT",
        structure_data=structure,
        sr_data=sr,
        volume_profile=vp,
    )
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

CLUSTER_TOLERANCE_PCT = 2.0  # Group prices within 2% of each other
ENTRY_ZONE_HALF_PCT = 1.0    # Entry zone = cluster center +/- 1%
NAIVE_SL_PCT = 0.15           # 15% from entry for naive SL
NAIVE_TP1_PCT = 0.15          # 15% from entry for naive TP1
NAIVE_TP2_PCT = 0.30          # 30% from entry for naive TP2
RR_LOW_THRESHOLD = 1.5        # Below this, quality is demoted to LOW


@dataclass
class SmartLevels:
    entry_low: float
    entry_high: float
    ideal_entry: float
    stop_loss: float
    target_1: float
    target_2: Optional[float]
    rr_ratio: float
    confluence_count: int
    quality: str       # HIGH / MEDIUM / LOW / NAIVE
    method: str        # SMART / FIB_ONLY / NAIVE
    sources: Dict = field(default_factory=dict)


class SmartLevelGenerator:

    def generate_levels(
        self,
        current_price: float,
        direction: str,
        structure_data: Optional[Dict] = None,
        sr_data: Optional[Dict] = None,
        volume_profile: Optional[Dict] = None,
        ohlcv: Optional[list] = None,
    ) -> SmartLevels:
        direction = direction.upper()

        candidates = self._collect_candidates(
            current_price, direction, structure_data, sr_data, volume_profile,
        )

        if not candidates:
            return self._naive_levels(current_price, direction)

        clusters = self._cluster_candidates(candidates)
        best = self._pick_best_cluster(clusters, current_price, direction)

        if best is None:
            return self._naive_levels(current_price, direction)

        center = best["center"]
        sources = best["sources"]
        confluence = best["count"]

        entry_low = center * (1 - ENTRY_ZONE_HALF_PCT / 100)
        entry_high = center * (1 + ENTRY_ZONE_HALF_PCT / 100)

        sl = self._calculate_sl(entry_low, entry_high, center, direction, sr_data)
        tp1, tp2 = self._calculate_targets(
            center, direction, sr_data, volume_profile,
        )

        if direction == "SHORT":
            risk = sl - center
            reward = center - tp1
        else:
            risk = center - sl
            reward = tp1 - center

        rr = reward / risk if risk > 0 else 0.0

        quality = self._classify_quality(confluence, rr)
        method = "SMART"

        return SmartLevels(
            entry_low=round(entry_low, 6),
            entry_high=round(entry_high, 6),
            ideal_entry=round(center, 6),
            stop_loss=round(sl, 6),
            target_1=round(tp1, 6),
            target_2=round(tp2, 6) if tp2 is not None else None,
            rr_ratio=round(rr, 2),
            confluence_count=confluence,
            quality=quality,
            method=method,
            sources=sources,
        )

    # ------------------------------------------------------------------
    # Candidate collection
    # ------------------------------------------------------------------

    def _collect_candidates(
        self,
        current_price: float,
        direction: str,
        structure_data: Optional[Dict],
        sr_data: Optional[Dict],
        volume_profile: Optional[Dict],
    ) -> List[Tuple[float, str]]:
        """Return list of (price, source_name) candidates for the entry zone."""
        candidates: List[Tuple[float, str]] = []

        if direction == "SHORT":
            candidates += self._short_candidates(
                current_price, structure_data, sr_data, volume_profile,
            )
        else:
            candidates += self._long_candidates(
                current_price, structure_data, sr_data, volume_profile,
            )

        return candidates

    def _short_candidates(self, price, structure, sr, vp):
        out = []
        # Fib levels (above current price for short entry)
        if structure:
            fib = structure.get("fib_levels") or {}
            for key, label in [("0.618", "fib_618"), ("0.786", "fib_786")]:
                val = fib.get(key)
                if val is not None and val > price:
                    out.append((val, label))

            # Bearish Order Block midpoint
            for ob in structure.get("order_blocks") or []:
                if (
                    ob.get("direction") == "bearish"
                    and not ob.get("mitigated", True)
                    and ob.get("midpoint") is not None
                    and ob["midpoint"] > price
                ):
                    out.append((ob["midpoint"], "order_block"))

            # Bearish FVG midpoint
            fvg = structure.get("nearest_bearish_fvg")
            if fvg and fvg.get("midpoint") is not None and fvg["midpoint"] > price:
                out.append((fvg["midpoint"], "fvg"))

        # Resistance levels
        if sr:
            for r in sr.get("resistances") or []:
                if r.get("price") is not None and r["price"] > price:
                    out.append((r["price"], "resistance"))

        # Volume profile levels above price
        if vp:
            poc = vp.get("poc")
            if poc is not None and poc > price:
                out.append((poc, "poc"))
            vah = vp.get("vah")
            if vah is not None and vah > price:
                out.append((vah, "vah"))

        return out

    def _long_candidates(self, price, structure, sr, vp):
        out = []
        # Fib levels (below current price for long entry)
        if structure:
            fib = structure.get("fib_levels") or {}
            for key, label in [("0.618", "fib_618"), ("0.786", "fib_786")]:
                val = fib.get(key)
                if val is not None and val < price:
                    out.append((val, label))

            # Bullish Order Block midpoint
            for ob in structure.get("order_blocks") or []:
                if (
                    ob.get("direction") == "bullish"
                    and not ob.get("mitigated", True)
                    and ob.get("midpoint") is not None
                    and ob["midpoint"] < price
                ):
                    out.append((ob["midpoint"], "order_block"))

            # Bullish FVG midpoint
            fvg = structure.get("nearest_bullish_fvg")
            if fvg and fvg.get("midpoint") is not None and fvg["midpoint"] < price:
                out.append((fvg["midpoint"], "fvg"))

        # Support levels
        if sr:
            for s in sr.get("supports") or []:
                if s.get("price") is not None and s["price"] < price:
                    out.append((s["price"], "support"))

        # Volume profile levels below price
        if vp:
            poc = vp.get("poc")
            if poc is not None and poc < price:
                out.append((poc, "poc"))
            val = vp.get("val")
            if val is not None and val < price:
                out.append((val, "val"))

        return out

    # ------------------------------------------------------------------
    # Clustering
    # ------------------------------------------------------------------

    def _cluster_candidates(
        self, candidates: List[Tuple[float, str]]
    ) -> List[Dict]:
        """Group candidates within CLUSTER_TOLERANCE_PCT of each other."""
        if not candidates:
            return []

        sorted_cands = sorted(candidates, key=lambda c: c[0])
        clusters: List[Dict] = []

        current_cluster = {
            "prices": [sorted_cands[0][0]],
            "labels": [sorted_cands[0][1]],
        }

        for price, label in sorted_cands[1:]:
            cluster_center = sum(current_cluster["prices"]) / len(current_cluster["prices"])
            pct_diff = abs(price - cluster_center) / cluster_center * 100

            if pct_diff <= CLUSTER_TOLERANCE_PCT:
                current_cluster["prices"].append(price)
                current_cluster["labels"].append(label)
            else:
                clusters.append(self._finalize_cluster(current_cluster))
                current_cluster = {"prices": [price], "labels": [label]}

        clusters.append(self._finalize_cluster(current_cluster))
        return clusters

    @staticmethod
    def _finalize_cluster(raw: Dict) -> Dict:
        center = sum(raw["prices"]) / len(raw["prices"])
        sources = {}
        for p, l in zip(raw["prices"], raw["labels"]):
            sources[l] = round(p, 6)
        return {
            "center": center,
            "count": len(raw["prices"]),
            "sources": sources,
        }

    def _pick_best_cluster(
        self, clusters: List[Dict], current_price: float, direction: str
    ) -> Optional[Dict]:
        """Pick the cluster with highest confluence; tie-break by proximity."""
        if not clusters:
            return None

        def sort_key(c):
            # Primary: highest confluence count (negate for descending)
            # Secondary: closest to current price
            return (-c["count"], abs(c["center"] - current_price))

        clusters.sort(key=sort_key)
        return clusters[0]

    # ------------------------------------------------------------------
    # SL calculation
    # ------------------------------------------------------------------

    def _calculate_sl(
        self,
        entry_low: float,
        entry_high: float,
        center: float,
        direction: str,
        sr_data: Optional[Dict],
    ) -> float:
        if direction == "SHORT":
            return self._sl_for_short(entry_high, center, sr_data)
        else:
            return self._sl_for_long(entry_low, center, sr_data)

    def _sl_for_short(self, entry_high, center, sr_data):
        # Look for next resistance ABOVE the entry zone
        if sr_data:
            resistances = sr_data.get("resistances") or []
            above = [r["price"] for r in resistances if r["price"] > entry_high * 1.01]
            if above:
                return min(above)
        # Fallback: 15% above entry
        return entry_high * (1 + NAIVE_SL_PCT)

    def _sl_for_long(self, entry_low, center, sr_data):
        # Look for next support BELOW the entry zone
        if sr_data:
            supports = sr_data.get("supports") or []
            below = [s["price"] for s in supports if s["price"] < entry_low * 0.99]
            if below:
                return max(below)
        # Fallback: 15% below entry
        return entry_low * (1 - NAIVE_SL_PCT)

    # ------------------------------------------------------------------
    # Target calculation
    # ------------------------------------------------------------------

    def _calculate_targets(
        self,
        center: float,
        direction: str,
        sr_data: Optional[Dict],
        volume_profile: Optional[Dict],
    ) -> Tuple[float, Optional[float]]:
        if direction == "SHORT":
            return self._targets_for_short(center, sr_data, volume_profile)
        else:
            return self._targets_for_long(center, sr_data, volume_profile)

    def _targets_for_short(self, center, sr_data, vp):
        supports = []
        if sr_data:
            supports = sorted(
                (sr_data.get("supports") or []),
                key=lambda s: s.get("touch_count", 0),
                reverse=True,
            )

        tp1 = None
        tp2 = None

        if supports:
            # TP1 = strongest support below entry
            below = [s for s in supports if s["price"] < center]
            if below:
                tp1 = below[0]["price"]  # strongest (already sorted by touch_count)
                # TP2 = next support below TP1
                further = [s for s in below if s["price"] < tp1 * 0.99]
                if further:
                    tp2 = further[0]["price"]

        # VAL as TP2 alternative
        if tp2 is None and vp:
            val = vp.get("val")
            if val is not None and (tp1 is None or val < tp1 * 0.99):
                tp2 = val

        # Naive fallbacks
        if tp1 is None:
            tp1 = center * (1 - NAIVE_TP1_PCT)
        if tp2 is None:
            tp2 = center * (1 - NAIVE_TP2_PCT)

        return tp1, tp2

    def _targets_for_long(self, center, sr_data, vp):
        resistances = []
        if sr_data:
            resistances = sorted(
                (sr_data.get("resistances") or []),
                key=lambda r: r.get("touch_count", 0),
                reverse=True,
            )

        tp1 = None
        tp2 = None

        if resistances:
            above = [r for r in resistances if r["price"] > center]
            if above:
                tp1 = above[0]["price"]
                further = [r for r in above if r["price"] > tp1 * 1.01]
                if further:
                    tp2 = further[0]["price"]

        # VAH as TP2 alternative
        if tp2 is None and vp:
            vah = vp.get("vah")
            if vah is not None and (tp1 is None or vah > tp1 * 1.01):
                tp2 = vah

        if tp1 is None:
            tp1 = center * (1 + NAIVE_TP1_PCT)
        if tp2 is None:
            tp2 = center * (1 + NAIVE_TP2_PCT)

        return tp1, tp2

    # ------------------------------------------------------------------
    # Quality classification
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_quality(confluence: int, rr: float) -> str:
        if confluence >= 3:
            quality = "HIGH"
        elif confluence == 2:
            quality = "MEDIUM"
        elif confluence == 1:
            quality = "LOW"
        else:
            return "NAIVE"

        # Demote if R:R is poor
        if rr < RR_LOW_THRESHOLD:
            quality = "LOW"

        return quality

    # ------------------------------------------------------------------
    # NAIVE fallback
    # ------------------------------------------------------------------

    def _naive_levels(self, current_price: float, direction: str) -> SmartLevels:
        if direction == "SHORT":
            entry = current_price
            sl = entry * (1 + NAIVE_SL_PCT)
            tp1 = entry * (1 - NAIVE_TP1_PCT)
            tp2 = entry * (1 - NAIVE_TP2_PCT)
        else:
            entry = current_price
            sl = entry * (1 - NAIVE_SL_PCT)
            tp1 = entry * (1 + NAIVE_TP1_PCT)
            tp2 = entry * (1 + NAIVE_TP2_PCT)

        risk = abs(sl - entry)
        reward = abs(tp1 - entry)
        rr = reward / risk if risk > 0 else 0.0

        return SmartLevels(
            entry_low=round(entry * 0.99, 6),
            entry_high=round(entry * 1.01, 6),
            ideal_entry=round(entry, 6),
            stop_loss=round(sl, 6),
            target_1=round(tp1, 6),
            target_2=round(tp2, 6),
            rr_ratio=round(rr, 2),
            confluence_count=0,
            quality="NAIVE",
            method="NAIVE",
            sources={},
        )
