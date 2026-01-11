"""
FDV Estimator - Estimate FDV for Pre-TGE Projects

Session 316: Created based on "Where to find the FDV" Notion task

Estimation Methods (Priority Order):
1. Official Sources: CryptoRank, launchpads, project docs
2. VC Markup: Seed valuation × typical markup multiplier
3. Category Benchmarks: Based on comparable projects
4. Sentiment Analysis: Market conditions + hype level

Integration:
- Uses VC fundraising data from fundraising_tracker.py
- Uses category benchmarks from fdv_semantic_validator.py
- Provides fallback when actual FDV is unavailable
"""

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Tuple

from src.data.fundraising_tracker import get_fundraising_tracker
from src.data.vc_database import get_vc_database
from src.data.validation.fdv_semantic_validator import CATEGORY_BENCHMARKS

logger = logging.getLogger(__name__)


class EstimationMethod(Enum):
    """FDV estimation method used"""
    OFFICIAL = "official"  # From project docs, CryptoRank, launchpad
    VC_MARKUP = "vc_markup"  # Calculated from seed valuation
    CATEGORY_MEDIAN = "category_median"  # Based on category benchmarks
    HYPE_ADJUSTED = "hype_adjusted"  # Category median adjusted for hype
    UNKNOWN = "unknown"


class ConfidenceLevel(Enum):
    """Confidence in the FDV estimate"""
    HIGH = "high"  # Official source or well-documented
    MEDIUM = "medium"  # VC markup with known valuation
    LOW = "low"  # Category estimate or hype-based
    VERY_LOW = "very_low"  # Rough guess


# VC Markup multipliers by round type (Session 316)
VC_MARKUP_MULTIPLIERS = {
    "seed": {
        "conservative": 5.0,
        "typical": 10.0,
        "aggressive": 20.0
    },
    "pre_seed": {
        "conservative": 8.0,
        "typical": 15.0,
        "aggressive": 30.0
    },
    "series_a": {
        "conservative": 3.0,
        "typical": 5.0,
        "aggressive": 10.0
    },
    "series_b": {
        "conservative": 2.0,
        "typical": 3.0,
        "aggressive": 5.0
    },
    "strategic": {
        "conservative": 1.5,
        "typical": 2.5,
        "aggressive": 4.0
    },
    "private_sale": {
        "conservative": 2.0,
        "typical": 4.0,
        "aggressive": 8.0
    }
}


# Category median FDVs for new TGEs (derived from benchmarks)
CATEGORY_MEDIAN_FDV = {
    "L1": 5_000_000_000,  # $5B median for new L1
    "L2": 2_000_000_000,  # $2B median for new L2
    "DeFi": 500_000_000,  # $500M median for new DeFi
    "Gaming": 300_000_000,  # $300M median for new Gaming
    "AI": 1_000_000_000,  # $1B median for new AI
    "Meme": 100_000_000,  # $100M median for new meme
    "Infrastructure": 1_000_000_000,  # $1B median for new infra
    "Unknown": 500_000_000  # $500M conservative default
}


# Hype level multipliers
HYPE_MULTIPLIERS = {
    "extreme": 3.0,  # MONAD-level hype
    "high": 2.0,  # Strong anticipation
    "moderate": 1.0,  # Normal interest
    "low": 0.5  # Under the radar
}


@dataclass
class FDVEstimate:
    """FDV estimation result"""
    project_name: str
    estimated_fdv: float
    method: EstimationMethod
    confidence: ConfidenceLevel
    range_low: float
    range_high: float
    reasoning: str
    data_sources: List[str] = field(default_factory=list)
    vc_data: Optional[Dict] = None
    category: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class FDVEstimator:
    """
    Estimate FDV for pre-TGE projects using multiple methods.

    Priority:
    1. Official FDV (if available from CryptoRank, docs)
    2. VC Markup estimation (if fundraising data available)
    3. Category median (fallback)
    """

    def __init__(self):
        """Initialize FDV estimator with data sources"""
        self.fundraising_tracker = get_fundraising_tracker()
        self.vc_database = get_vc_database()

    def estimate_fdv(
        self,
        project_name: str,
        category: Optional[str] = None,
        official_fdv: Optional[float] = None,
        hype_level: str = "moderate",
        token_symbol: Optional[str] = None
    ) -> FDVEstimate:
        """
        Estimate FDV using best available method.

        Args:
            project_name: Name of the project
            category: Token category (L1, L2, DeFi, etc.)
            official_fdv: Official FDV if known
            hype_level: Market hype level (extreme, high, moderate, low)
            token_symbol: Token symbol if known

        Returns:
            FDVEstimate with estimated FDV and confidence
        """
        # Normalize category
        category = self._normalize_category(category)

        # Method 1: Use official FDV if provided
        if official_fdv and official_fdv > 0:
            return FDVEstimate(
                project_name=project_name,
                estimated_fdv=official_fdv,
                method=EstimationMethod.OFFICIAL,
                confidence=ConfidenceLevel.HIGH,
                range_low=official_fdv * 0.9,
                range_high=official_fdv * 1.1,
                reasoning="Official FDV from project documentation or listing",
                data_sources=["official"],
                category=category
            )

        # Method 2: VC Markup estimation
        vc_estimate = self._estimate_from_vc_data(project_name, category)
        if vc_estimate:
            return vc_estimate

        # Method 3: Category median with hype adjustment
        return self._estimate_from_category(project_name, category, hype_level)

    def _normalize_category(self, category: Optional[str]) -> str:
        """Normalize category string"""
        if not category:
            return "Unknown"

        category = category.strip()

        # Map common variations
        category_map = {
            "LAYER1": "L1", "LAYER 1": "L1", "LAYER-1": "L1",
            "LAYER2": "L2", "LAYER 2": "L2", "LAYER-2": "L2",
            "DEFI": "DeFi", "DEFI_INFRA": "DeFi",
            "GAMING": "Gaming", "GAMEFI": "Gaming",
            "AI_AGENTS": "AI", "AI/ML": "AI", "ARTIFICIAL INTELLIGENCE": "AI",
            "MEME": "Meme", "MEMECOIN": "Meme",
            "INFRA": "Infrastructure", "ORACLE": "Infrastructure"
        }

        upper = category.upper()
        if upper in category_map:
            return category_map[upper]

        # Check if it matches a known category
        for known in CATEGORY_BENCHMARKS:
            if known.upper() == upper:
                return known

        return "Unknown"

    def _estimate_from_vc_data(
        self,
        project_name: str,
        category: str
    ) -> Optional[FDVEstimate]:
        """
        Estimate FDV from VC fundraising data.

        Uses earliest round valuation × typical markup multiplier.
        """
        # Try to find project in fundraising tracker
        project = self.fundraising_tracker.projects.get(project_name)

        if not project or not project.rounds:
            logger.debug(f"No fundraising data for {project_name}")
            return None

        # Find earliest round with valuation
        earliest_round = None
        for rd in project.rounds:
            if rd.valuation_usd:
                if earliest_round is None or (rd.date and earliest_round.date and rd.date < earliest_round.date):
                    earliest_round = rd

        if not earliest_round or not earliest_round.valuation_usd:
            logger.debug(f"No valuation data in rounds for {project_name}")
            return None

        # Get markup multipliers for round type
        round_type = earliest_round.round_type.value
        multipliers = VC_MARKUP_MULTIPLIERS.get(
            round_type,
            VC_MARKUP_MULTIPLIERS["private_sale"]  # Default to private sale
        )

        # Adjust multipliers based on VC quality
        vc_classification = self.vc_database.classify_investor_list(project.all_investors)
        tier_1_count = vc_classification["tier_1_count"]

        # Tier 1 VCs tend to push for higher valuations
        vc_adjustment = 1.0
        if tier_1_count >= 3:
            vc_adjustment = 1.3  # Premium VCs = higher TGE FDV
        elif tier_1_count >= 1:
            vc_adjustment = 1.15

        # Calculate estimates
        base_valuation = earliest_round.valuation_usd
        typical_fdv = base_valuation * multipliers["typical"] * vc_adjustment
        low_fdv = base_valuation * multipliers["conservative"]
        high_fdv = base_valuation * multipliers["aggressive"] * vc_adjustment

        reasoning = (
            f"Based on {earliest_round.round_type.value} round at ${base_valuation/1e6:.1f}M valuation. "
            f"Typical {round_type} markup is {multipliers['typical']}x "
            f"({multipliers['conservative']}x-{multipliers['aggressive']}x range). "
        )

        if tier_1_count > 0:
            reasoning += f"Adjusted +{int((vc_adjustment-1)*100)}% for {tier_1_count} Tier 1 VC(s)."

        return FDVEstimate(
            project_name=project_name,
            estimated_fdv=typical_fdv,
            method=EstimationMethod.VC_MARKUP,
            confidence=ConfidenceLevel.MEDIUM,
            range_low=low_fdv,
            range_high=high_fdv,
            reasoning=reasoning,
            data_sources=["fundraising_tracker", "vc_database"],
            vc_data={
                "round_type": round_type,
                "seed_valuation": base_valuation,
                "markup_used": multipliers["typical"] * vc_adjustment,
                "tier_1_count": tier_1_count,
                "investors": project.all_investors[:5]  # Top 5
            },
            category=category
        )

    def _estimate_from_category(
        self,
        project_name: str,
        category: str,
        hype_level: str
    ) -> FDVEstimate:
        """
        Estimate FDV from category benchmarks with hype adjustment.

        This is the fallback method when no official or VC data is available.
        """
        # Get category median
        median_fdv = CATEGORY_MEDIAN_FDV.get(category, CATEGORY_MEDIAN_FDV["Unknown"])

        # Get benchmark data
        benchmarks = CATEGORY_BENCHMARKS.get(category, CATEGORY_BENCHMARKS["Unknown"])
        min_fdv, max_fdv = benchmarks["typical_fdv_range"]
        max_new = benchmarks.get("max_reasonable_new_launch", max_fdv)

        # Apply hype multiplier
        hype_mult = HYPE_MULTIPLIERS.get(hype_level.lower(), 1.0)
        adjusted_fdv = median_fdv * hype_mult

        # Clamp to reasonable range
        adjusted_fdv = min(adjusted_fdv, max_new)
        adjusted_fdv = max(adjusted_fdv, min_fdv)

        # Calculate range
        range_low = median_fdv * 0.5
        range_high = min(median_fdv * hype_mult * 1.5, max_new)

        reasoning = (
            f"Category-based estimate for {category}. "
            f"Median new TGE FDV: ${median_fdv/1e9:.2f}B. "
        )

        if hype_mult != 1.0:
            reasoning += f"Adjusted {int((hype_mult-1)*100):+}% for {hype_level} hype level."
        else:
            reasoning += "Normal market interest assumed."

        confidence = ConfidenceLevel.LOW if hype_level == "moderate" else ConfidenceLevel.VERY_LOW
        method = EstimationMethod.HYPE_ADJUSTED if hype_mult != 1.0 else EstimationMethod.CATEGORY_MEDIAN

        return FDVEstimate(
            project_name=project_name,
            estimated_fdv=adjusted_fdv,
            method=method,
            confidence=confidence,
            range_low=range_low,
            range_high=range_high,
            reasoning=reasoning,
            data_sources=["category_benchmarks"],
            category=category
        )

    def get_estimation_breakdown(
        self,
        project_name: str,
        category: Optional[str] = None
    ) -> Dict:
        """
        Get detailed breakdown of all estimation methods for a project.

        Useful for debugging and transparency.
        """
        category = self._normalize_category(category)

        results = {
            "project_name": project_name,
            "category": category,
            "methods_available": [],
            "estimates": {}
        }

        # Check VC data availability
        project = self.fundraising_tracker.projects.get(project_name)
        if project:
            vc_estimate = self._estimate_from_vc_data(project_name, category)
            if vc_estimate:
                results["methods_available"].append("vc_markup")
                results["estimates"]["vc_markup"] = asdict(vc_estimate)

        # Category estimate is always available
        results["methods_available"].append("category_median")
        cat_estimate = self._estimate_from_category(project_name, category, "moderate")
        results["estimates"]["category_median"] = asdict(cat_estimate)

        # Hype-adjusted estimate
        results["methods_available"].append("hype_adjusted")
        for level in ["extreme", "high", "low"]:
            hype_est = self._estimate_from_category(project_name, category, level)
            results["estimates"][f"hype_{level}"] = asdict(hype_est)

        return results


# Singleton instance
_estimator: Optional[FDVEstimator] = None


def get_fdv_estimator() -> FDVEstimator:
    """Get singleton FDV estimator instance"""
    global _estimator
    if _estimator is None:
        _estimator = FDVEstimator()
    return _estimator


def estimate_fdv(
    project_name: str,
    category: Optional[str] = None,
    official_fdv: Optional[float] = None,
    hype_level: str = "moderate"
) -> FDVEstimate:
    """
    Convenience function to estimate FDV.

    Args:
        project_name: Name of the project
        category: Token category
        official_fdv: Official FDV if known
        hype_level: Market hype level

    Returns:
        FDVEstimate with estimated FDV and confidence
    """
    return get_fdv_estimator().estimate_fdv(
        project_name=project_name,
        category=category,
        official_fdv=official_fdv,
        hype_level=hype_level
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    estimator = get_fdv_estimator()

    # Test with known projects
    print("\n" + "="*60)
    print("FDV ESTIMATOR TEST")
    print("="*60)

    # Monad - has VC data
    result = estimator.estimate_fdv("Monad", category="L1", hype_level="extreme")
    print(f"\nMonad (L1, extreme hype):")
    print(f"  Estimated FDV: ${result.estimated_fdv/1e9:.2f}B")
    print(f"  Range: ${result.range_low/1e9:.2f}B - ${result.range_high/1e9:.2f}B")
    print(f"  Method: {result.method.value}")
    print(f"  Confidence: {result.confidence.value}")
    print(f"  Reasoning: {result.reasoning}")

    # Unknown project - category fallback
    result = estimator.estimate_fdv("NewToken", category="DeFi", hype_level="moderate")
    print(f"\nNewToken (DeFi, moderate hype):")
    print(f"  Estimated FDV: ${result.estimated_fdv/1e6:.0f}M")
    print(f"  Range: ${result.range_low/1e6:.0f}M - ${result.range_high/1e6:.0f}M")
    print(f"  Method: {result.method.value}")
    print(f"  Confidence: {result.confidence.value}")

    # Breakdown
    print("\n" + "="*60)
    print("MONAD ESTIMATION BREAKDOWN:")
    print("="*60)
    import json
    breakdown = estimator.get_estimation_breakdown("Monad", "L1")
    print(json.dumps(breakdown, indent=2, default=str))
