#!/usr/bin/env python3
"""
LONG Data Completeness Validator - Session 292 Phase 5

Validates data completeness for LONG analysis before scoring.
Provides clear visibility into data quality and actionable recommendations.

Usage:
    from dacle_core.data.validation.long_data_validator import LongDataValidator, validate_long_data

    # Full validation
    validator = LongDataValidator()
    result = validator.validate(consolidated_data)
    print(result.summary())

    # Quick check
    is_valid, message = validate_long_data(consolidated_data)

Author: Claude Code (Session 292)
Created: 2026-01-06
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of data completeness validation."""

    is_valid: bool  # True if minimum required fields present
    completeness_pct: float  # 0-100% based on weighted fields
    missing_required: List[str]  # Critical fields that are missing
    missing_recommended: List[str]  # Nice-to-have fields that are missing
    present_fields: List[str]  # All fields that have data
    recommendation: str  # PROCEED, WATCHLIST, or SKIP
    data_quality_score: float  # 0-10 overall quality score
    details: Dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        """Generate human-readable summary."""
        lines = [
            f"{'='*60}",
            f"  DATA QUALITY VALIDATION",
            f"{'='*60}",
            f"  Status: {'✅ VALID' if self.is_valid else '❌ INVALID'}",
            f"  Completeness: {self.completeness_pct:.1f}%",
            f"  Quality Score: {self.data_quality_score:.1f}/10",
            f"  Recommendation: {self.recommendation}",
            "",
        ]

        if self.missing_required:
            lines.append(f"  ❌ Missing Required ({len(self.missing_required)}):")
            for field in self.missing_required[:5]:  # Show first 5
                lines.append(f"     - {field}")
            if len(self.missing_required) > 5:
                lines.append(f"     ... and {len(self.missing_required) - 5} more")
            lines.append("")

        if self.missing_recommended:
            lines.append(f"  ⚠️  Missing Recommended ({len(self.missing_recommended)}):")
            for field in self.missing_recommended[:5]:
                lines.append(f"     - {field}")
            if len(self.missing_recommended) > 5:
                lines.append(f"     ... and {len(self.missing_recommended) - 5} more")
            lines.append("")

        lines.append(f"  ✅ Present Fields: {len(self.present_fields)}")
        lines.append(f"{'='*60}")

        return "\n".join(lines)


class LongDataValidator:
    """
    Validates data completeness for LONG analysis.

    Categorizes fields by importance for LONG scoring:
    - REQUIRED: Must have for any analysis (symbol, price)
    - HIGH_VALUE: Important for accurate scoring (FDV/MC, category)
    - TA_FIELDS: Technical analysis for conviction (RSI, drawdown)
    - NICE_TO_HAVE: Enhance analysis but not critical (VC data)

    Weight distribution follows LONG scorer component weights.
    """

    # Required fields - analysis fails without these
    REQUIRED_FIELDS = [
        "symbol",
        "name",
        "current_price",
    ]

    # High-value fundamental fields (30% weight in LONG scorer)
    HIGH_VALUE_FIELDS = [
        "fdv",
        "market_cap",
        "fdv_mc_ratio",
        "category",
        "float_pct",
    ]

    # TA fields (53% weight in LONG scorer)
    TA_FIELDS = [
        "rsi_4h",
        "rsi_14",
        "drawdown_from_ath",
        "ath_price",
        "days_since_ath",
        "at_ema_200_support",
        "dump_volume_ratio",
        "bottom_signals_count",
    ]

    # Nice-to-have fields (17% weight in LONG scorer)
    NICE_TO_HAVE_FIELDS = [
        "vc_backed",
        "investors",
        "exchange_tier",
        "binance_listing",
        "tge_date",
        "circulating_supply",
        "total_supply",
    ]

    # Weights for completeness calculation
    FIELD_WEIGHTS = {
        # Required (must have)
        "symbol": 10,
        "name": 5,
        "current_price": 15,
        # High-value fundamentals
        "fdv": 8,
        "market_cap": 8,
        "fdv_mc_ratio": 10,
        "category": 5,
        "float_pct": 6,
        # TA fields
        "rsi_4h": 8,
        "rsi_14": 8,
        "drawdown_from_ath": 12,
        "ath_price": 5,
        "days_since_ath": 4,
        "at_ema_200_support": 5,
        "dump_volume_ratio": 4,
        "bottom_signals_count": 3,
        # Nice-to-have
        "vc_backed": 4,
        "investors": 3,
        "exchange_tier": 3,
        "binance_listing": 3,
        "tge_date": 2,
        "circulating_supply": 2,
        "total_supply": 2,
    }

    def validate(self, data: Dict[str, Any]) -> ValidationResult:
        """
        Validate data completeness for LONG analysis.

        Args:
            data: Consolidated.json data dict

        Returns:
            ValidationResult with completeness metrics and recommendation
        """
        # Check each field category
        missing_required = self._check_fields(data, self.REQUIRED_FIELDS)
        missing_high_value = self._check_fields(data, self.HIGH_VALUE_FIELDS)
        missing_ta = self._check_fields(data, self.TA_FIELDS)
        missing_nice_to_have = self._check_fields(data, self.NICE_TO_HAVE_FIELDS)

        # Combine into categories
        missing_recommended = missing_high_value + missing_ta + missing_nice_to_have

        # Calculate completeness percentage
        completeness_pct = self._calculate_completeness(data)

        # Determine if valid (required fields present)
        is_valid = len(missing_required) == 0

        # Calculate quality score (0-10)
        data_quality_score = completeness_pct / 10

        # Generate recommendation
        recommendation = self._get_recommendation(
            is_valid, completeness_pct, len(missing_ta)
        )

        # Get all present fields
        present_fields = self._get_present_fields(data)

        return ValidationResult(
            is_valid=is_valid,
            completeness_pct=completeness_pct,
            missing_required=missing_required,
            missing_recommended=missing_recommended,
            present_fields=present_fields,
            recommendation=recommendation,
            data_quality_score=data_quality_score,
            details={
                "missing_high_value": missing_high_value,
                "missing_ta": missing_ta,
                "missing_nice_to_have": missing_nice_to_have,
                "ta_completeness_pct": self._calculate_category_completeness(data, self.TA_FIELDS),
                "fundamental_completeness_pct": self._calculate_category_completeness(data, self.HIGH_VALUE_FIELDS),
            }
        )

    def _check_fields(self, data: Dict, fields: List[str]) -> List[str]:
        """Return list of missing fields from the given field list."""
        missing = []
        for field in fields:
            value = data.get(field)
            if value is None or value == "" or value == "MISSING" or value == "N/A":
                missing.append(field)
        return missing

    def _calculate_completeness(self, data: Dict) -> float:
        """Calculate weighted completeness percentage."""
        total_weight = sum(self.FIELD_WEIGHTS.values())
        present_weight = 0

        for field, weight in self.FIELD_WEIGHTS.items():
            value = data.get(field)
            if value is not None and value != "" and value != "MISSING" and value != "N/A":
                present_weight += weight

        return (present_weight / total_weight) * 100 if total_weight > 0 else 0

    def _calculate_category_completeness(self, data: Dict, fields: List[str]) -> float:
        """Calculate completeness for a specific category of fields."""
        if not fields:
            return 100.0

        present = 0
        for field in fields:
            value = data.get(field)
            if value is not None and value != "" and value != "MISSING" and value != "N/A":
                present += 1

        return (present / len(fields)) * 100

    def _get_present_fields(self, data: Dict) -> List[str]:
        """Get list of fields that have valid data."""
        all_fields = (
            self.REQUIRED_FIELDS +
            self.HIGH_VALUE_FIELDS +
            self.TA_FIELDS +
            self.NICE_TO_HAVE_FIELDS
        )
        present = []
        for field in all_fields:
            value = data.get(field)
            if value is not None and value != "" and value != "MISSING" and value != "N/A":
                present.append(field)
        return present

    def _get_recommendation(
        self,
        is_valid: bool,
        completeness: float,
        missing_ta_count: int
    ) -> str:
        """Generate recommendation based on validation results."""
        if not is_valid:
            return "❌ SKIP - Missing required fields, run /refetch first"

        if completeness < 30:
            return "❌ SKIP - Insufficient data (<30%), run /refetch first"
        elif completeness < 50:
            return "⚠️  WATCHLIST - Partial data (30-50%), results may be unreliable"
        elif completeness < 70:
            return "⚠️  WATCHLIST - Moderate data (50-70%), proceed with caution"
        elif missing_ta_count > 4:
            return "⚠️  WATCHLIST - Missing TA data, using v1.3 fallback weights"
        else:
            return "✅ PROCEED - Data quality acceptable (≥70%)"


def validate_long_data(data: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Quick validation check for LONG data.

    Args:
        data: Consolidated.json data dict

    Returns:
        Tuple of (is_valid, message)
    """
    validator = LongDataValidator()
    result = validator.validate(data)
    return result.is_valid, result.recommendation


def print_validation_summary(data: Dict[str, Any], token: str = "UNKNOWN") -> ValidationResult:
    """
    Print validation summary and return result.

    Args:
        data: Consolidated.json data dict
        token: Token symbol for display

    Returns:
        ValidationResult
    """
    validator = LongDataValidator()
    result = validator.validate(data)

    print(f"\n  📊 Data Quality Check for {token}")
    print(result.summary())

    return result


# CLI support
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Usage: python3 long_data_validator.py <TOKEN>")
        print("Example: python3 long_data_validator.py POWER")
        sys.exit(1)

    token = sys.argv[1].upper()
    consolidated_path = Path(f"data/tokens/{token}/consolidated.json")

    if not consolidated_path.exists():
        print(f"❌ No consolidated.json found for {token}")
        sys.exit(1)

    with open(consolidated_path, 'r') as f:
        data = json.load(f)

    result = print_validation_summary(data, token)
    sys.exit(0 if result.is_valid else 1)
