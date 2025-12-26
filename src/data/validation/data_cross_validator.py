#!/usr/bin/env python3
"""
Data Cross-Validation Module

Compares data from multiple sources (Perplexity vs Automated Scraping) to detect conflicts
and calculate data confidence based on agreement.

Philosophy (Session 48+ - Agent 4 Execution Reality Check):
- Trust but verify: Never rely on single data source
- When sources agree → High confidence
- When sources conflict → Flag for manual review
- Missing data from automation → Acceptable if Perplexity has it (but lower confidence)

Created: 2025-11-23 (Session 48+)
Updated: 2025-11-24 (Session 51.5 - Added Perplexity validation)
"""

from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
import re

# Import Perplexity validator (Session 51.5)
try:
    from .perplexity_validator import validate_perplexity_data
    PERPLEXITY_VALIDATOR_AVAILABLE = True
except ImportError:
    PERPLEXITY_VALIDATOR_AVAILABLE = False


class DataCrossValidator:
    """Cross-validates data from multiple sources"""

    # Tolerance thresholds for numeric comparisons
    TOLERANCE_FLOAT_PCT = 3.0  # ±3% for float percentage
    TOLERANCE_SUPPLY = 0.05    # ±5% for supply numbers
    TOLERANCE_FUNDING = 0.10   # ±10% for funding amounts

    def __init__(self):
        self.conflicts = []
        self.agreements = []
        self.missing_fields = []

    def validate(
        self,
        perplexity_data: Dict[str, Any],
        automated_data: Dict[str, Any],
        token: str
    ) -> Dict[str, Any]:
        """
        Cross-validate data from Perplexity vs automated scraping.

        Args:
            perplexity_data: Data from Perplexity research
            automated_data: Data from CryptoRank/WebFetch scraping
            token: Token symbol for logging

        Returns:
            Dict with validation results, confidence score, and merged data
        """
        self.conflicts = []
        self.agreements = []
        self.missing_fields = []

        # SESSION 51.5: Validate Perplexity data first (catch parsing errors)
        perplexity_validation = None
        if PERPLEXITY_VALIDATOR_AVAILABLE:
            perplexity_validation = validate_perplexity_data(perplexity_data, verbose=False)
            if not perplexity_validation["is_valid"]:
                print(f"⚠️  Perplexity data for {token} has validation errors:")
                for error in perplexity_validation["errors"]:
                    print(f"   [{error['field']}] {error['message']}")
            elif perplexity_validation["has_warnings"]:
                print(f"⚠️  Perplexity data for {token} has validation warnings:")
                for warning in perplexity_validation["warnings"]:
                    print(f"   [{warning['field']}] {warning['message']}")

        # Critical fields to validate
        validations = {
            "total_supply": self._validate_supply,
            "float_percent": self._validate_float,
            "tge_date": self._validate_date,
            "funding": self._validate_funding,
        }

        for field, validator in validations.items():
            result = validator(perplexity_data, automated_data, field)

            if result["status"] == "MATCH":
                self.agreements.append(result)
            elif result["status"] == "CONFLICT":
                self.conflicts.append(result)
            elif result["status"] == "MISSING":
                self.missing_fields.append(result)

        # Calculate confidence score
        confidence = self._calculate_confidence()

        # Merge data (prefer Perplexity when conflict, use automation when Perplexity missing)
        merged_data = self._merge_data(perplexity_data, automated_data)

        result = {
            "token": token,
            "validation_status": "PASS" if len(self.conflicts) == 0 else "CONFLICTS_DETECTED",
            "data_confidence": confidence,
            "agreements": len(self.agreements),
            "conflicts": len(self.conflicts),
            "missing_automated": len(self.missing_fields),
            "conflict_details": self.conflicts,
            "agreement_details": self.agreements,
            "missing_details": self.missing_fields,
            "merged_data": merged_data,
            "recommendation": self._get_recommendation(confidence, len(self.conflicts))
        }

        # SESSION 51.5: Add Perplexity validation results
        if perplexity_validation:
            result["perplexity_validation"] = {
                "is_valid": perplexity_validation["is_valid"],
                "has_warnings": perplexity_validation["has_warnings"],
                "errors": perplexity_validation["errors"],
                "warnings": perplexity_validation["warnings"]
            }

        return result

    def _validate_supply(
        self,
        perplexity: Dict,
        automated: Dict,
        field: str
    ) -> Dict[str, Any]:
        """Validate total supply"""
        p_supply = perplexity.get("total_supply")
        a_supply = automated.get("total_supply")

        if not a_supply:
            return {
                "field": "total_supply",
                "status": "MISSING",
                "perplexity": p_supply,
                "automated": None,
                "note": "Automation did not extract supply"
            }

        if not p_supply:
            return {
                "field": "total_supply",
                "status": "MATCH",
                "perplexity": None,
                "automated": a_supply,
                "note": "Using automated value (Perplexity didn't provide)"
            }

        # Check if within tolerance
        diff_pct = abs(p_supply - a_supply) / p_supply * 100

        if diff_pct <= self.TOLERANCE_SUPPLY * 100:
            return {
                "field": "total_supply",
                "status": "MATCH",
                "perplexity": p_supply,
                "automated": a_supply,
                "diff_pct": diff_pct,
                "note": f"✅ Verified - {diff_pct:.1f}% difference (within {self.TOLERANCE_SUPPLY*100}% tolerance)"
            }
        else:
            return {
                "field": "total_supply",
                "status": "CONFLICT",
                "perplexity": p_supply,
                "automated": a_supply,
                "diff_pct": diff_pct,
                "note": f"⚠️ CONFLICT - {diff_pct:.1f}% difference (exceeds {self.TOLERANCE_SUPPLY*100}% tolerance)"
            }

    def _resolve_float_conflict_time_based(
        self,
        perplexity_float: float,
        automated_float: float,
        tge_date: str
    ) -> Tuple[float, str, str]:
        """
        Resolve float % conflict based on TGE timing (Session 49 - Time-Based Resolution).

        Logic:
        - Pre-TGE: Use Perplexity's planned unlock % (from tokenomics)
        - Post-TGE: Use CMC/CryptoRank's actual circulating supply %

        Args:
            perplexity_float: Float % from Perplexity (planned tokenomics)
            automated_float: Float % from CMC/CryptoRank (actual market data)
            tge_date: TGE date in ISO format

        Returns:
            Tuple of (chosen_value, source, reasoning)
        """
        now = datetime.now()

        # Parse TGE date
        try:
            tge = datetime.fromisoformat(tge_date.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            # If TGE date is invalid, default to conservative (lower value)
            if perplexity_float < automated_float:
                return perplexity_float, "perplexity_conservative", "Invalid TGE date - using conservative (lower) value"
            else:
                return automated_float, "automated_conservative", "Invalid TGE date - using conservative (lower) value"

        if now < tge:
            # Pre-TGE: Use planned unlock from tokenomics (Perplexity)
            return (
                perplexity_float,
                "perplexity_pre_tge",
                f"Pre-TGE ({(tge - now).days} days until launch) - using planned unlock from tokenomics"
            )
        else:
            # Post-TGE: Use actual circulating supply from market data (CMC/CryptoRank)
            return (
                automated_float,
                "automated_post_tge",
                f"Post-TGE ({(now - tge).days} days since launch) - using actual circulating supply from market"
            )

    def _validate_float(
        self,
        perplexity: Dict,
        automated: Dict,
        field: str
    ) -> Dict[str, Any]:
        """Validate float percentage with time-based conflict resolution"""
        p_float = perplexity.get("float_percent") or perplexity.get("tge_unlock_pct")
        a_float = automated.get("float_percent")

        if not a_float:
            return {
                "field": "float_percent",
                "status": "MISSING",
                "perplexity": p_float,
                "automated": None,
                "note": "Automation did not extract float %"
            }

        if not p_float:
            return {
                "field": "float_percent",
                "status": "MATCH",
                "perplexity": None,
                "automated": a_float,
                "note": "Using automated value (Perplexity didn't provide)"
            }

        # Check if within tolerance
        diff = abs(p_float - a_float)

        if diff <= self.TOLERANCE_FLOAT_PCT:
            return {
                "field": "float_percent",
                "status": "MATCH",
                "perplexity": f"{p_float}%",
                "automated": f"{a_float}%",
                "diff": diff,
                "note": f"✅ Verified - {diff:.1f}% difference (within {self.TOLERANCE_FLOAT_PCT}% tolerance)"
            }
        else:
            # CONFLICT DETECTED - Apply time-based resolution
            tge_date = perplexity.get("tge_date") or automated.get("tge_date")

            if tge_date:
                # Use time-based resolution
                resolved_value, source, reasoning = self._resolve_float_conflict_time_based(
                    p_float, a_float, tge_date
                )

                return {
                    "field": "float_percent",
                    "status": "CONFLICT",
                    "perplexity": f"{p_float}%",
                    "automated": f"{a_float}%",
                    "diff": diff,
                    "resolved_value": f"{resolved_value}%",
                    "resolution_source": source,
                    "note": f"⚠️ CONFLICT (resolved via time-based logic) - {reasoning}\n   • Perplexity: {p_float}% (planned tokenomics)\n   • CMC/CryptoRank: {a_float}% (actual market data)\n   • Resolution: Using {resolved_value}% from {source}"
                }
            else:
                # No TGE date available - fall back to conservative approach
                resolved_value = min(p_float, a_float)
                return {
                    "field": "float_percent",
                    "status": "CONFLICT",
                    "perplexity": f"{p_float}%",
                    "automated": f"{a_float}%",
                    "diff": diff,
                    "resolved_value": f"{resolved_value}%",
                    "resolution_source": "conservative_fallback",
                    "note": f"⚠️ CONFLICT (resolved conservatively) - No TGE date available, using lower value: {resolved_value}%"
                }

    def _validate_date(
        self,
        perplexity: Dict,
        automated: Dict,
        field: str
    ) -> Dict[str, Any]:
        """Validate TGE date"""
        p_date = perplexity.get("tge_date") or perplexity.get("releaseDate")
        a_date = automated.get("tge_date")

        if not a_date:
            return {
                "field": "tge_date",
                "status": "MISSING",
                "perplexity": p_date,
                "automated": None,
                "note": "Automation did not extract TGE date"
            }

        if not p_date:
            return {
                "field": "tge_date",
                "status": "MATCH",
                "perplexity": None,
                "automated": a_date,
                "note": "Using automated value (Perplexity didn't provide)"
            }

        # Normalize dates for comparison
        p_date_norm = self._normalize_date(p_date)
        a_date_norm = self._normalize_date(a_date)

        if p_date_norm == a_date_norm:
            return {
                "field": "tge_date",
                "status": "MATCH",
                "perplexity": p_date,
                "automated": a_date,
                "note": f"✅ Verified - Both sources agree: {p_date_norm}"
            }
        else:
            return {
                "field": "tge_date",
                "status": "CONFLICT",
                "perplexity": p_date,
                "automated": a_date,
                "note": f"⚠️ CONFLICT - Perplexity: {p_date_norm}, Automated: {a_date_norm}"
            }

    def _validate_funding(
        self,
        perplexity: Dict,
        automated: Dict,
        field: str
    ) -> Dict[str, Any]:
        """Validate VC funding amounts"""
        # Extract total funding from Perplexity
        p_funding = None
        if "total_funding" in perplexity:
            # Parse "$18.7M" format
            match = re.search(r'[\d.]+', str(perplexity["total_funding"]))
            if match:
                p_funding = float(match.group()) * 1_000_000

        # Extract from automated (sum of rounds if available)
        a_funding = None
        if automated.get("funding"):
            # New format can be either dict or int (Agent 0 extracts total_raised value)
            if isinstance(automated["funding"], dict):
                a_funding = automated["funding"].get("total_raised")
            else:
                a_funding = automated["funding"]  # Already an int from Agent 0

        if not a_funding:
            return {
                "field": "funding",
                "status": "MISSING",
                "perplexity": perplexity.get("total_funding"),
                "automated": None,
                "note": "Automation did not extract VC funding"
            }

        if not p_funding:
            return {
                "field": "funding",
                "status": "MATCH",
                "perplexity": None,
                "automated": a_funding,
                "note": "Using automated value (Perplexity didn't provide)"
            }

        # Check if within tolerance
        diff_pct = abs(p_funding - a_funding) / p_funding * 100

        if diff_pct <= self.TOLERANCE_FUNDING * 100:
            return {
                "field": "funding",
                "status": "MATCH",
                "perplexity": p_funding,
                "automated": a_funding,
                "diff_pct": diff_pct,
                "note": f"✅ Verified - {diff_pct:.1f}% difference"
            }
        else:
            return {
                "field": "funding",
                "status": "CONFLICT",
                "perplexity": p_funding,
                "automated": a_funding,
                "diff_pct": diff_pct,
                "note": f"⚠️ CONFLICT - {diff_pct:.1f}% difference"
            }

    def _normalize_date(self, date_str: str) -> str:
        """Normalize date string to YYYY-MM-DD format"""
        if not date_str:
            return ""

        # Try parsing ISO format
        try:
            dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            return dt.strftime("%Y-%m-%d")
        except:
            pass

        # Try parsing common formats
        for fmt in ["%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y"]:
            try:
                dt = datetime.strptime(date_str, fmt)
                return dt.strftime("%Y-%m-%d")
            except:
                continue

        return date_str

    def _calculate_confidence(self) -> float:
        """
        Calculate data confidence score based on agreements, conflicts, and missing data.

        Returns:
            Confidence score 0-100%
        """
        total_checks = len(self.agreements) + len(self.conflicts) + len(self.missing_fields)

        if total_checks == 0:
            return 0.0

        # Scoring:
        # - Agreement: +25 points each
        # - Missing from automation: +15 points (Perplexity has it, automation doesn't)
        # - Conflict: -10 points each

        agreement_score = len(self.agreements) * 25
        missing_score = len(self.missing_fields) * 15
        conflict_penalty = len(self.conflicts) * 10

        raw_score = agreement_score + missing_score - conflict_penalty
        confidence = min(100.0, max(0.0, raw_score))

        return confidence

    def _merge_data(
        self,
        perplexity: Dict[str, Any],
        automated: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Merge data from both sources using Time-Based + Conservative approach.

        Resolution Strategy (Session 49 - Time-Based Float Resolution):
        - For float_percent conflicts: Use time-based resolution (pre-TGE vs post-TGE)
        - For other numeric conflicts: Use conservative approach (lower value)
        - For non-numeric fields: Prefer Perplexity (manual research)
        - For missing fields: Use available source

        Rationale:
        - Float % has different meanings before/after TGE:
          * Pre-TGE: Planned unlock % from tokenomics (Perplexity)
          * Post-TGE: Actual circulating supply % from market (CMC/CryptoRank)
        - Other metrics: Conservative approach for risk management

        Priority:
        1. Time-based (for float_percent conflicts)
        2. Conservative (for other numeric conflicts - choose lower value)
        3. Perplexity (manual research for non-numeric fields)
        4. Automated (fills gaps when Perplexity missing)
        """
        merged = perplexity.copy()

        # Create a map of conflicts with their resolved values
        conflict_resolutions = {}
        for conflict in self.conflicts:
            field = conflict["field"]
            if "resolved_value" in conflict:
                # Extract numeric value from string like "10.8%"
                resolved_str = conflict["resolved_value"]
                if isinstance(resolved_str, str) and '%' in resolved_str:
                    resolved_value = float(resolved_str.replace('%', ''))
                else:
                    resolved_value = resolved_str
                conflict_resolutions[field] = {
                    "value": resolved_value,
                    "source": conflict.get("resolution_source", "unknown")
                }

        # Track which fields had conflicts (for conservative resolution)
        conflicted_fields = {c["field"] for c in self.conflicts}

        # Numeric fields where we apply conservative approach (choose lower value)
        # Note: float_percent uses time-based resolution, not conservative
        numeric_conservative_fields = {
            "total_supply", "tge_unlock_pct",
            "fdv", "mc", "funding", "valuation"
        }

        # Fill gaps from automated data
        for key, value in automated.items():
            if key not in merged or merged[key] is None:
                # Perplexity missing this field - use automated
                merged[key] = value
                merged[f"{key}_source"] = "automated"
            elif key in conflict_resolutions:
                # Use the resolved value from time-based or conservative logic
                resolution = conflict_resolutions[key]
                merged[key] = resolution["value"]
                merged[f"{key}_source"] = resolution["source"]
            elif key in conflicted_fields and key in numeric_conservative_fields:
                # CONSERVATIVE APPROACH: On conflict, choose LOWER value for shorts
                p_val = merged[key]
                a_val = value

                if isinstance(p_val, (int, float)) and isinstance(a_val, (int, float)):
                    if a_val < p_val:
                        # Automated value is LOWER - use it (conservative for shorts)
                        merged[key] = a_val
                        merged[f"{key}_source"] = "automated_conservative"
                    else:
                        # Perplexity value is LOWER - keep it (conservative for shorts)
                        merged[f"{key}_source"] = "perplexity_conservative"
                else:
                    # Not numeric - default to Perplexity
                    merged[f"{key}_source"] = "perplexity"
            else:
                # No conflict or non-numeric field - trust Perplexity
                merged[f"{key}_source"] = "perplexity"

        # Add cross-validation metadata
        merged["_cross_validation"] = {
            "agreements": len(self.agreements),
            "conflicts": len(self.conflicts),
            "missing_automated": len(self.missing_fields),
            "confidence": self._calculate_confidence(),
            "conservative_resolution": len([c for c in self.conflicts if c["field"] in numeric_conservative_fields]),
            "time_based_resolution": len([c for c in self.conflicts if c["field"] == "float_percent" and "resolved_value" in c])
        }

        return merged

    def _get_recommendation(self, confidence: float, conflicts: int) -> str:
        """Get execution recommendation based on validation results"""
        if conflicts > 0:
            return "⚠️ MANUAL REVIEW REQUIRED - Data conflicts detected, verify before executing"
        elif confidence >= 75:
            return "✅ HIGH CONFIDENCE - Both sources agree, safe to proceed"
        elif confidence >= 50:
            return "⚠️ MEDIUM CONFIDENCE - Some data missing from automation, Perplexity fills gaps"
        else:
            return "❌ LOW CONFIDENCE - Insufficient automated verification, rely on Perplexity only"
