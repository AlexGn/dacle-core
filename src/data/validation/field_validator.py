#!/usr/bin/env python3
"""
Field Validator - Session 79F
TGE Data Consolidation & Validation Pipeline - Phase 1

Purpose:
- Validate all 32 fields against schema with severity classification
- Calculate field-level confidence scores
- Check data freshness (flag if >7 days old)
- Generate validation reports with blocking recommendations

Gemini Critical Fixes Incorporated:
1. Listing Price Paradox: Pass if derivable from FDV/Total Supply
2. Single-Source Confidence: Don't penalize if passes sanity checks

CLI Mode (Session 79F - Gemini Approved):
    python scripts/helpers/field_validator.py --token MONAD --json > result.json
    python scripts/helpers/field_validator.py --batch MONAD,IRYS,LAYER --json

Reference: /Users/alex/.claude/plans/frolicking-wondering-sketch.md
"""

import sys
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import json
import argparse
import logging

logger = logging.getLogger(__name__)

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.conviction.field_definitions import (
    CRITICAL_FIELDS,
    IMPORTANT_FIELDS,
    NICE_TO_HAVE_FIELDS,
    FieldPriority,
    FIELD_MAP,
    get_critical_fields,
    get_important_fields
)


@dataclass
class FieldValidationResult:
    """Result of validating a single field"""
    field_name: str
    present: bool
    value: Any
    confidence: float  # 0-100%
    sources: List[str]  # ["cryptorank", "perplexity"]
    validation_status: str  # "PASS", "WARN", "FAIL"
    validation_message: str
    freshness_days: Optional[int] = None
    is_derivable: bool = False  # True if field can be calculated from other fields
    matched_via_alias: Optional[str] = None  # Session 79J: Track which alias was used


@dataclass
class AliasMatch:
    """Session 79J: Track field alias matches for verification"""
    expected_field: str
    matched_alias: str
    value: Any
    tier: str  # "CRITICAL" or "IMPORTANT"


@dataclass
class WaiverInfo:
    """Session 84 Phase 2: Information about a waived field"""
    field: str
    waived: bool
    reason: str
    confidence_penalty: int
    auto_waived: bool = True
    tier: str = "TIER_2"


@dataclass
class ValidationReport:
    """Complete validation report for a token"""
    token: str
    validated_at: str
    critical_coverage_pct: float
    important_coverage_pct: float
    nice_to_have_coverage_pct: float
    overall_confidence: float
    missing_critical_fields: List[str]
    missing_important_fields: List[str]
    low_confidence_fields: List[FieldValidationResult]
    stale_data_warnings: List[str]
    has_stale_data: bool
    data_age_days: int
    field_validations: List[FieldValidationResult]  # All field-level results
    alias_matches: List[AliasMatch] = None  # Session 79J: Track alias matches for verification
    consolidation_errors: List[str] = None  # Session 89: Cross-validation/sanity check errors
    waivers_applied: List[WaiverInfo] = None  # Session 84 Phase 2: Waived fields
    adjusted_confidence: Optional[float] = None  # Session 84 Phase 2: Confidence after penalties
    confidence_adjustment: Optional[int] = None  # Session 84 Phase 2: Total penalty from waivers

    def __post_init__(self):
        if self.alias_matches is None:
            self.alias_matches = []
        if self.waivers_applied is None:
            self.waivers_applied = []
        if self.consolidation_errors is None:
            self.consolidation_errors = []

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization"""
        return {
            **asdict(self),
            'field_validations': [asdict(fv) for fv in self.field_validations],
            'alias_matches': [asdict(am) for am in self.alias_matches] if self.alias_matches else [],
            'waivers_applied': [asdict(w) for w in self.waivers_applied] if self.waivers_applied else [],
            'consolidation_errors': self.consolidation_errors if self.consolidation_errors else []
        }


class FieldValidator:
    """
    Core validation engine for TGE data quality gate

    Validates all 32 fields against schema, calculates field-level confidence,
    and generates blocking recommendations based on CRITICAL field coverage.
    """

    # Thresholds
    CRITICAL_COVERAGE_THRESHOLD = 100.0  # Must be 100%
    IMPORTANT_COVERAGE_THRESHOLD = 70.0  # Warn if < 70%
    STALE_DATA_THRESHOLD_DAYS = 7
    LOW_CONFIDENCE_THRESHOLD = 50.0  # Flag fields <50% confidence

    # Session 84 Phase 2: Field Waiver System
    # Allows tokens with 92.9%+ coverage to pass with confidence penalties
    AUTO_WAIVER_CONDITIONS = {
        "investors": {
            "waive_if": [
                # Waive if we have funding data but no investor names
                lambda data: bool(data.get("funding_rounds") and len(data.get("funding_rounds", [])) > 0),
                # Waive if VC tier already assessed as WEAK (no marquee investors expected)
                lambda data: data.get("vc_tier_assessment") == "WEAK",
            ],
            "confidence_penalty": -10,
            "reasoning": "Investor list unavailable but funding data present",
            "tier": "TIER_2"  # High-value but waiveable
        },
        "whitepaper_url": {
            "waive_if": [
                lambda data: True  # Always waiveable - non-essential for short-term trades
            ],
            "confidence_penalty": 0,
            "reasoning": "Non-essential for short-term trades",
            "tier": "TIER_3"  # Nice-to-have
        },
        "vesting_schedule": {
            "waive_if": [
                lambda data: True  # Always waiveable - long-term projection
            ],
            "confidence_penalty": 0,
            "reasoning": "Vesting schedule is secondary indicator for short-term analysis",
            "tier": "TIER_3"  # Nice-to-have
        },
    }

    # Fields that CANNOT be waived (absolute blockers for trade execution)
    ABSOLUTE_BLOCKERS = [
        "fdv_low",
        "fdv_high",
        "float_percent",
        "listing_price_low",
        "listing_price_high",
        "tge_date",
        "listing_exchanges",
        "circulating_supply_at_tge",
        "total_supply",
    ]

    # Waiver guardrails
    MAX_WAIVERS_PER_TOKEN = 2  # No more than 2 critical fields can be waived
    MIN_CONFIDENCE_WITH_WAIVERS = 30  # Adjusted confidence must be >= 30/100

    # Session 79J: Field aliases for data normalization
    # Maps expected field names to alternative names found in consolidated.json
    FIELD_ALIASES = {
        # IMPORTANT fields with known aliases
        "token_allocation": [
            "community_allocation_pct",  # Partial allocation data
            "allocation_breakdown",
            "tokenomics_allocation",
        ],
        "tokenomics_model": [
            "category",  # e.g., "Layer_1", "DeFi"
            "project_category",
            "token_type",
        ],
        # CRITICAL fields with known aliases (for robustness)
        "float_percent": [
            "float_percentage",
            "tge_unlock_pct",
            "circulating_supply_percent",
        ],
        "funding_raised_usd": [
            "total_funding",
            "total_raised_usd",
        ],
        "listing_exchanges": [
            "exchanges",
        ],
    }

    def __init__(self):
        self.critical_field_names = get_critical_fields()
        self.important_field_names = get_important_fields()

    def validate_fields(
        self,
        data: Dict[str, Any],
        token: str,
        source_metadata: Optional[Dict[str, Any]] = None
    ) -> ValidationReport:
        """
        Validate all fields and generate validation report

        Args:
            data: Consolidated TGE data dictionary
            token: Token symbol
            source_metadata: Optional metadata about data sources and timestamps

        Returns:
            ValidationReport with field-level validation results
        """
        print(f"\n{'=' * 80}")
        print(f"FIELD-LEVEL VALIDATION: {token}")
        print(f"{'=' * 80}\n")

        field_validations = []

        # Validate each critical field
        print("🔴 CRITICAL FIELDS (14):")
        for field_def in CRITICAL_FIELDS:
            result = self._validate_field(field_def, data, source_metadata)
            field_validations.append(result)
            self._print_field_result(result)

        print(f"\n🟡 IMPORTANT FIELDS (9):")
        for field_def in IMPORTANT_FIELDS:
            result = self._validate_field(field_def, data, source_metadata)
            field_validations.append(result)
            self._print_field_result(result)

        print(f"\n🟢 NICE-TO-HAVE FIELDS (5):")
        for field_def in NICE_TO_HAVE_FIELDS:
            result = self._validate_field(field_def, data, source_metadata)
            field_validations.append(result)
            self._print_field_result(result)

        # Calculate coverage percentages
        critical_present = sum(
            1 for fv in field_validations
            if fv.field_name in self.critical_field_names and (fv.present or fv.is_derivable)
        )
        important_present = sum(
            1 for fv in field_validations
            if fv.field_name in self.important_field_names and fv.present
        )
        nice_to_have_present = sum(
            1 for fv in field_validations
            if fv.field_name not in self.critical_field_names + self.important_field_names
            and fv.present
        )

        critical_coverage_pct = (critical_present / len(self.critical_field_names)) * 100
        important_coverage_pct = (important_present / len(self.important_field_names)) * 100
        nice_to_have_coverage_pct = (nice_to_have_present / 5) * 100  # 5 nice-to-have fields

        # Identify missing fields
        missing_critical_raw = [
            fv.field_name for fv in field_validations
            if fv.field_name in self.critical_field_names
            and not fv.present
            and not fv.is_derivable
        ]
        missing_important = [
            fv.field_name for fv in field_validations
            if fv.field_name in self.important_field_names and not fv.present
        ]

        # Session 84 Phase 2: Apply field waiver system
        waivers_applied = []
        non_waiveable_missing = []

        for field in missing_critical_raw:
            # Check if field is an absolute blocker (cannot be waived)
            if field in self.ABSOLUTE_BLOCKERS:
                non_waiveable_missing.append(field)
                continue

            # Check if field qualifies for auto-waiver
            if field in self.AUTO_WAIVER_CONDITIONS:
                waiver_config = self.AUTO_WAIVER_CONDITIONS[field]
                conditions = waiver_config["waive_if"]

                # Evaluate all conditions (must pass ALL conditions to waive)
                all_conditions_met = all(condition(data) for condition in conditions)

                if all_conditions_met:
                    # Generate detailed reasoning with data context
                    reasoning = self._generate_waiver_reasoning(field, data, waiver_config)

                    waivers_applied.append(WaiverInfo(
                        field=field,
                        waived=True,
                        reason=reasoning,
                        confidence_penalty=waiver_config["confidence_penalty"],
                        auto_waived=True,
                        tier=waiver_config["tier"]
                    ))
                else:
                    # Conditions not met - field remains missing
                    non_waiveable_missing.append(field)
            else:
                # No waiver rule defined - field remains missing
                non_waiveable_missing.append(field)

        # Update missing_critical to only include non-waived fields
        missing_critical = non_waiveable_missing

        # Session 84 Phase 2: Recalculate critical coverage after waivers
        # If waivers were successfully applied, count those fields as "present"
        if waivers_applied:
            critical_present += len(waivers_applied)
            critical_coverage_pct = (critical_present / len(self.critical_field_names)) * 100

        # Identify low confidence fields
        low_confidence = [
            fv for fv in field_validations
            if fv.present and fv.confidence < self.LOW_CONFIDENCE_THRESHOLD
        ]

        # Check data freshness
        stale_warnings, has_stale, data_age = self._check_data_freshness(source_metadata)

        # Calculate overall confidence
        all_present = [fv for fv in field_validations if fv.present]
        overall_confidence = (
            sum(fv.confidence for fv in all_present) / len(all_present)
            if all_present else 0.0
        )

        # Session 84 Phase 2: Apply confidence penalties from waivers
        confidence_adjustment = None
        adjusted_confidence = None

        if waivers_applied:
            # Check waiver guardrails
            if len(waivers_applied) > self.MAX_WAIVERS_PER_TOKEN:
                # Too many waivers - reject all and mark as FAIL
                logger.warning(f"⚠️  {token}: Too many waivers ({len(waivers_applied)}). Maximum allowed: {self.MAX_WAIVERS_PER_TOKEN}")
                waivers_applied = []
                missing_critical = missing_critical_raw  # Restore all missing fields
            else:
                # Apply confidence penalties
                total_penalty = sum(w.confidence_penalty for w in waivers_applied)
                confidence_adjustment = total_penalty
                adjusted_confidence = max(0, overall_confidence + total_penalty)

                # Check minimum confidence threshold
                if adjusted_confidence < self.MIN_CONFIDENCE_WITH_WAIVERS:
                    logger.warning(f"⚠️  {token}: Adjusted confidence ({adjusted_confidence}) below minimum ({self.MIN_CONFIDENCE_WITH_WAIVERS})")
                    waivers_applied = []
                    missing_critical = missing_critical_raw  # Restore all missing fields
                    confidence_adjustment = None
                    adjusted_confidence = None

        # Session 79J: Collect alias matches for verification report
        alias_matches = []
        for fv in field_validations:
            if fv.matched_via_alias:
                tier = "CRITICAL" if fv.field_name in self.critical_field_names else \
                       "IMPORTANT" if fv.field_name in self.important_field_names else "NICE_TO_HAVE"
                alias_matches.append(AliasMatch(
                    expected_field=fv.field_name,
                    matched_alias=fv.matched_via_alias,
                    value=fv.value,
                    tier=tier
                ))

        # Session 89: Extract consolidation validation errors (cross-checks, sanity checks)
        consolidation_errors = data.get("_consolidation_metadata", {}).get("validation_errors", [])

        # Generate report
        report = ValidationReport(
            token=token,
            validated_at=datetime.now().isoformat(),
            critical_coverage_pct=round(critical_coverage_pct, 1),
            important_coverage_pct=round(important_coverage_pct, 1),
            nice_to_have_coverage_pct=round(nice_to_have_coverage_pct, 1),
            overall_confidence=round(overall_confidence, 1),
            missing_critical_fields=missing_critical,
            missing_important_fields=missing_important,
            low_confidence_fields=low_confidence,
            stale_data_warnings=stale_warnings,
            has_stale_data=has_stale,
            data_age_days=data_age,
            field_validations=field_validations,
            alias_matches=alias_matches,
            consolidation_errors=consolidation_errors,
            waivers_applied=waivers_applied,
            adjusted_confidence=round(adjusted_confidence, 1) if adjusted_confidence is not None else None,
            confidence_adjustment=confidence_adjustment
        )

        # Print summary
        self._print_summary(report)

        return report

    def _get_field_value_with_aliases(
        self,
        field_name: str,
        data: Dict[str, Any]
    ) -> tuple:
        """
        Get field value, checking aliases if primary field is missing.

        Session 79J: Enables field normalization without modifying source data.

        Returns:
            tuple: (value, matched_alias) where matched_alias is None if primary field used
        """
        # First check primary field name
        value = data.get(field_name)
        if value is not None and value != "" and value != []:
            return value, None  # No alias used

        # Check aliases if primary is missing
        aliases = self.FIELD_ALIASES.get(field_name, [])
        for alias in aliases:
            alias_value = data.get(alias)
            if alias_value is not None and alias_value != "" and alias_value != []:
                logger.debug(f"Field '{field_name}' found via alias '{alias}'")
                return alias_value, alias  # Return value and which alias matched

        return None, None

    def _validate_field(
        self,
        field_def,
        data: Dict[str, Any],
        source_metadata: Optional[Dict[str, Any]]
    ) -> FieldValidationResult:
        """Validate a single field"""
        field_name = field_def.name
        # Session 79J: Check aliases for field value (returns tuple)
        value, matched_alias = self._get_field_value_with_aliases(field_name, data)
        present = value is not None and value != "" and value != []

        # CRITICAL FIX (Gemini): Listing Price Paradox
        # listing_price often unknown until TGE, but derivable from FDV / Total Supply
        is_derivable = False
        if field_name in ["listing_price_low", "listing_price_high"] and not present:
            is_derivable = self._is_listing_price_derivable(data)

        # SESSION 79 FIX: Contract Address Pre-TGE Exception
        # contract_address not available until TGE launch - mark as derivable if TGE is future
        if field_name == "contract_address" and not present:
            is_derivable = self._is_pre_tge(data)

        # Determine sources
        sources = self._get_sources_for_field(field_name, source_metadata)

        # Calculate confidence
        confidence = self._calculate_field_confidence(
            field_name, value, sources, source_metadata
        )

        # Calculate freshness
        freshness_days = self._get_field_freshness_days(field_name, source_metadata)

        # Validate field value
        validation_status, validation_message = self._validate_field_value(
            field_def, value, data, is_derivable
        )

        return FieldValidationResult(
            field_name=field_name,
            present=present,
            value=value,
            confidence=confidence,
            sources=sources,
            validation_status=validation_status,
            validation_message=validation_message,
            freshness_days=freshness_days,
            is_derivable=is_derivable,
            matched_via_alias=matched_alias  # Session 79J: Track alias
        )

    def _is_listing_price_derivable(self, data: Dict[str, Any]) -> bool:
        """
        CRITICAL FIX (Gemini): Listing Price Paradox

        For many TGEs, listing_price is unknown until launch, but can be derived
        from FDV / Total Supply.

        Pass validation if EITHER:
        - listing_price exists, OR
        - (fdv AND total_supply exist)
        """
        has_fdv = data.get("fdv_low") or data.get("fdv_high")
        has_total_supply = data.get("total_supply")

        return bool(has_fdv and has_total_supply)

    def _is_pre_tge(self, data: Dict[str, Any]) -> bool:
        """
        SESSION 79 FIX: Contract Address Pre-TGE Exception

        Contract addresses are typically not announced until TGE launch day
        (or within hours before). This creates a quality gate problem for
        pre-TGE analysis.

        Pass validation if TGE is in the future (we can't expect contract_address yet).
        Fail validation if TGE has already happened (contract should be available).
        """
        tge_date_str = data.get("tge_date")
        if not tge_date_str:
            # No TGE date - assume future
            return True

        try:
            # Parse TGE date
            if isinstance(tge_date_str, str):
                # Try ISO format first
                if "T" in tge_date_str:
                    tge_date = datetime.fromisoformat(tge_date_str.replace("Z", "+00:00"))
                else:
                    tge_date = datetime.fromisoformat(tge_date_str)
            else:
                # Already a datetime object
                tge_date = tge_date_str

            # Check if TGE is in the future (allow 24-hour grace period after TGE)
            now = datetime.now(tge_date.tzinfo) if tge_date.tzinfo else datetime.now()
            is_future = tge_date > (now - timedelta(days=1))

            return is_future

        except Exception:
            # Parse error - assume future
            return True

    def _get_sources_for_field(
        self,
        field_name: str,
        source_metadata: Optional[Dict[str, Any]]
    ) -> List[str]:
        """Determine which sources provided this field"""
        if not source_metadata:
            return ["unknown"]

        sources = []

        # Check CryptoRank
        if source_metadata.get("cryptorank_data", {}).get(field_name):
            sources.append("cryptorank")

        # Check Perplexity
        if source_metadata.get("perplexity_data", {}).get(field_name):
            sources.append("perplexity")

        # Check Dropstab
        if source_metadata.get("dropstab_data", {}).get(field_name):
            sources.append("dropstab")

        return sources if sources else ["consolidated"]

    # Historical fields that don't change after TGE (Session 87)
    # These should NOT be penalized for "staleness" on LIVE tokens
    IMMUTABLE_HISTORICAL_FIELDS = {
        "tge_date", "float_percent", "total_supply", "circulating_supply_at_tge",
        "funding_raised_usd", "investors", "funding_rounds", "investor_tier",
        "listing_exchanges", "contract_address", "blockchain", "category",
        "whitepaper_url", "website_url", "twitter_url", "token_allocation",
        "vesting_schedule", "initial_market_cap_low", "initial_market_cap_high",
        "listing_price_low", "listing_price_high", "fdv_low", "fdv_high"
    }

    def _calculate_field_confidence(
        self,
        field_name: str,
        value: Any,
        sources: List[str],
        source_metadata: Optional[Dict[str, Any]]
    ) -> float:
        """
        Calculate field-level confidence score (0-100%)

        CRITICAL FIX (Gemini): Single-Source Confidence
        Don't penalize single-source data if it passes sanity checks.
        Often CryptoRank is the only structured source (Source count = 1).

        Session 87 FIX: Historical Field Staleness
        For LIVE tokens, historical/immutable fields (tge_date, float_percent,
        funding, VCs) should NOT be penalized as "stale" since they don't change.

        Factors:
        - Source count and quality (but don't penalize single reliable source)
        - Data freshness (< 3 days = high, >7 days = low) - EXCEPT for historical fields on LIVE tokens
        - Value sanity (no absurd values per Perplexity validator)
        """
        if value is None or value == "" or value == []:
            return 0.0

        confidence = 0.0

        # Base confidence from sources
        if "cryptorank" in sources:
            # CryptoRank is reliable structured data
            confidence = 90.0  # High confidence even if single source
        elif "perplexity" in sources:
            # Perplexity is LLM-extracted, slightly less reliable
            confidence = 80.0
        elif "dropstab" in sources:
            confidence = 75.0
        else:
            confidence = 50.0  # Fallback for unknown/consolidated

        # Boost confidence if multiple sources agree
        if len(sources) >= 2:
            confidence = min(95.0, confidence + 5.0)

        # Session 87: Check if token is LIVE (post-TGE)
        is_live_token = False
        if source_metadata:
            days_until_tge = source_metadata.get("days_until_tge")
            if days_until_tge is not None and days_until_tge < 0:
                is_live_token = True

        # Penalize for stale data - BUT skip for historical fields on LIVE tokens
        is_historical_field = field_name in self.IMMUTABLE_HISTORICAL_FIELDS
        skip_stale_penalty = is_live_token and is_historical_field

        if not skip_stale_penalty:
            freshness_days = self._get_field_freshness_days(field_name, source_metadata)
            if freshness_days:
                if freshness_days > 7:
                    confidence *= 0.7  # 30% penalty for stale data
                elif freshness_days > 3:
                    confidence *= 0.9  # 10% penalty for moderately old data

        # Sanity check: Validate value is reasonable
        if not self._passes_sanity_check(field_name, value):
            confidence *= 0.5  # 50% penalty for suspicious values

        return round(confidence, 1)

    def _passes_sanity_check(self, field_name: str, value: Any) -> bool:
        """
        Sanity check field values to detect parsing errors

        Reference: scripts/helpers/perplexity_validator.py
        """
        if value is None:
            return True

        # Funding sanity checks
        if field_name == "funding_raised_usd" and isinstance(value, (int, float)):
            if value > 1_000_000_000:  # $1B max
                return False
            if value > 100_000_000:  # $100M threshold (warning-worthy but not error)
                return True  # Allow but flag

        # FDV sanity checks
        if field_name in ["fdv_low", "fdv_high"] and isinstance(value, (int, float)):
            if value > 100_000_000_000:  # $100B max
                return False
            if value > 10_000_000_000:  # $10B threshold
                return True  # Allow but flag

        # Float sanity checks
        if field_name == "float_percent" and isinstance(value, (int, float)):
            if value > 50.0:  # 50% max (error-worthy)
                return False
            if value > 40.0:  # 40% threshold
                return True  # Allow but flag

        return True

    def _get_field_freshness_days(
        self,
        field_name: str,
        source_metadata: Optional[Dict[str, Any]]
    ) -> Optional[int]:
        """Calculate days since field was last updated"""
        if not source_metadata:
            return None

        # Check Perplexity data age (most common source of stale data)
        perplexity_timestamp = source_metadata.get("perplexity_timestamp")
        if perplexity_timestamp:
            try:
                last_updated = datetime.fromisoformat(perplexity_timestamp.replace('Z', '+00:00'))
                delta = datetime.now() - last_updated.replace(tzinfo=None)
                return delta.days
            except:
                return None

        return None

    def _validate_field_value(
        self,
        field_def,
        value: Any,
        data: Dict[str, Any],
        is_derivable: bool
    ) -> tuple[str, str]:
        """
        Validate field value against schema rules

        Returns:
            (status, message) where status is "PASS", "WARN", or "FAIL"
        """
        field_name = field_def.name

        # Handle derivable listing price (Gemini fix)
        if is_derivable:
            return ("PASS", f"Derivable from FDV/Total Supply")

        # Missing field
        if value is None or value == "" or value == []:
            return ("FAIL", "Missing")

        # Validate circulating_supply <= total_supply
        if field_name == "circulating_supply_at_tge":
            total_supply = data.get("total_supply")
            if total_supply and value > total_supply:
                return ("FAIL", f"Circulating supply ({value:,}) > Total supply ({total_supply:,})")

        # Validate float_percent <= 100%
        # Session 98 Learning 016: CoinGecko includes foundation/reserve in "circulating"
        if field_name == "float_percent":
            if value > 100.0:
                return ("FAIL", f"Float {value}% exceeds 100%")
            if value > 50.0:
                # Check if token is new (TGE within 7 days)
                tge_date = data.get("tge_date")
                is_new_token = False
                if tge_date:
                    try:
                        from datetime import datetime
                        tge = datetime.strptime(str(tge_date).split("T")[0], "%Y-%m-%d").date()
                        days_since_tge = (datetime.now().date() - tge).days
                        is_new_token = days_since_tge <= 7
                    except:
                        pass

                if is_new_token:
                    return ("WARN", f"Float {value}% is unusually high for new TGE - VERIFY with whitepaper (CoinGecko may include foundation tokens)")
                else:
                    return ("WARN", f"Float {value}% is unusually high")

        # Validate listing_exchanges has at least one major exchange
        if field_name == "listing_exchanges":
            if isinstance(value, list) and len(value) == 0:
                return ("FAIL", "No exchanges listed")
            # Could add check for major exchanges (Binance, Coinbase, etc.)

        # Validate investor_tier is valid
        if field_name == "investor_tier":
            valid_tiers = ["Tier 1", "Tier 2", "Tier 3", "Mixed", "Unknown"]
            if value not in valid_tiers:
                return ("WARN", f"Unknown tier: {value}")

        return ("PASS", "Valid")

    def _check_data_freshness(
        self,
        source_metadata: Optional[Dict[str, Any]]
    ) -> tuple[List[str], bool, int]:
        """
        Check data freshness and generate warnings

        Returns:
            (warnings, has_stale_data, data_age_days)
        """
        warnings = []
        has_stale = False
        data_age_days = 0

        if not source_metadata:
            return warnings, has_stale, data_age_days

        # Check Perplexity data age
        perplexity_timestamp = source_metadata.get("perplexity_timestamp")
        if perplexity_timestamp:
            try:
                last_updated = datetime.fromisoformat(perplexity_timestamp.replace('Z', '+00:00'))
                delta = datetime.now() - last_updated.replace(tzinfo=None)
                data_age_days = delta.days

                if data_age_days > self.STALE_DATA_THRESHOLD_DAYS:
                    has_stale = True
                    warnings.append(
                        f"⚠️ Perplexity data is {data_age_days} days old (threshold: {self.STALE_DATA_THRESHOLD_DAYS} days)"
                    )
            except Exception as e:
                warnings.append(f"⚠️ Could not parse Perplexity timestamp: {e}")

        return warnings, has_stale, data_age_days

    def _generate_waiver_reasoning(
        self,
        field: str,
        data: Dict[str, Any],
        waiver_config: Dict[str, Any]
    ) -> str:
        """
        Session 84 Phase 2: Generate detailed waiver reasoning with data context.

        Args:
            field: Field name being waived
            data: Token data dictionary
            waiver_config: Waiver configuration from AUTO_WAIVER_CONDITIONS

        Returns:
            Detailed reasoning string explaining why the waiver was applied
        """
        base_reasoning = waiver_config["reasoning"]

        # Add context-specific details based on field type
        if field == "investors":
            funding_rounds = data.get("funding_rounds", [])
            vc_tier = data.get("vc_tier_assessment", "Unknown")
            total_funding = data.get("funding_raised_usd") or data.get("total_funding", 0)

            context_details = []
            if funding_rounds:
                # Format funding rounds for display
                round_summaries = []
                for round_data in funding_rounds:
                    round_type = round_data.get("type", "Unknown")
                    amount = round_data.get("amount", 0)
                    valuation = round_data.get("valuation")

                    if valuation:
                        round_summaries.append(f"{round_type}: ${amount:,} @ ${valuation:,}")
                    else:
                        round_summaries.append(f"{round_type}: ${amount:,}")

                context_details.append(f"Funding data available: {', '.join(round_summaries)}")

            if vc_tier == "WEAK":
                context_details.append(f"VC tier assessed as {vc_tier} (low markup risk)")

            if total_funding:
                context_details.append(f"Total funding: ${total_funding:,}")

            if context_details:
                return f"{base_reasoning}. {' '.join(context_details)}"

        return base_reasoning

    def _print_field_result(self, result: FieldValidationResult):
        """Print field validation result"""
        status_emoji = {
            "PASS": "✅",
            "WARN": "⚠️",
            "FAIL": "❌"
        }[result.validation_status]

        confidence_str = f"{result.confidence:.0f}%" if result.present else "N/A"
        sources_str = ", ".join(result.sources) if result.sources else "none"

        derivable_marker = " (derivable)" if result.is_derivable else ""

        print(f"  {status_emoji} {result.field_name:30s} | {confidence_str:5s} | {sources_str:20s} | {result.validation_message}{derivable_marker}")

    def _print_summary(self, report: ValidationReport):
        """Print validation summary"""
        print(f"\n{'=' * 80}")
        print("VALIDATION SUMMARY")
        print(f"{'=' * 80}\n")

        print(f"Field Coverage:")
        print(f"  🔴 CRITICAL  : {len(self.critical_field_names) - len(report.missing_critical_fields)}/{len(self.critical_field_names)} ({report.critical_coverage_pct:.1f}%)")
        print(f"  🟡 IMPORTANT : {len(self.important_field_names) - len(report.missing_important_fields)}/{len(self.important_field_names)} ({report.important_coverage_pct:.1f}%)")
        print(f"  🟢 NICE-TO-HAVE: ({report.nice_to_have_coverage_pct:.1f}%)")

        print(f"\nOverall Confidence: {report.overall_confidence:.1f}%")

        if report.missing_critical_fields:
            print(f"\n❌ Missing CRITICAL Fields ({len(report.missing_critical_fields)}):")
            for field in report.missing_critical_fields:
                field_def = FIELD_MAP.get(field)
                desc = field_def.description if field_def else "No description"
                print(f"  • {field}: {desc}")

        if report.missing_important_fields:
            print(f"\n⚠️ Missing IMPORTANT Fields ({len(report.missing_important_fields)}):")
            for field in report.missing_important_fields[:5]:  # Show top 5
                field_def = FIELD_MAP.get(field)
                desc = field_def.description if field_def else "No description"
                print(f"  • {field}: {desc}")

        if report.low_confidence_fields:
            print(f"\n⚠️ Low Confidence Fields ({len(report.low_confidence_fields)}):")
            for fv in report.low_confidence_fields[:3]:  # Show top 3
                print(f"  • {fv.field_name}: {fv.confidence:.0f}% ({fv.validation_message})")

        if report.stale_data_warnings:
            print(f"\n⚠️ Data Freshness Warnings:")
            for warning in report.stale_data_warnings:
                print(f"  {warning}")

        # Session 79J: Show alias matches for verification
        if report.alias_matches:
            print(f"\n🔗 Field Alias Matches ({len(report.alias_matches)}):")
            print(f"   {'Expected Field':<25} {'Matched Via':<30} {'Tier':<10} {'Value'}")
            print(f"   {'-'*25} {'-'*30} {'-'*10} {'-'*30}")
            for am in report.alias_matches:
                # Truncate value for display
                value_str = str(am.value)[:30] + "..." if len(str(am.value)) > 30 else str(am.value)
                print(f"   {am.expected_field:<25} {am.matched_alias:<30} {am.tier:<10} {value_str}")
            print(f"\n   ⚠️  VERIFY: Check that alias mappings are semantically correct!")

        # Session 84 Phase 2: Show waivers applied
        if report.waivers_applied:
            print(f"\n✋ Field Waivers Applied ({len(report.waivers_applied)}):")
            for waiver in report.waivers_applied:
                penalty_str = f"{waiver.confidence_penalty}" if waiver.confidence_penalty < 0 else f"+{waiver.confidence_penalty}"
                auto_marker = "🤖 AUTO" if waiver.auto_waived else "👤 MANUAL"
                print(f"   {auto_marker} {waiver.field} ({waiver.tier}): {penalty_str} confidence")
                print(f"        Reason: {waiver.reason}")

            if report.confidence_adjustment is not None:
                print(f"\n   📊 Confidence Impact:")
                print(f"      Base confidence: {report.overall_confidence}%")
                print(f"      Adjustment: {report.confidence_adjustment}")
                print(f"      Adjusted confidence: {report.adjusted_confidence}%")

            print(f"\n   ⚠️  NOTE: Token passed validation with waivers. Position sizing should reflect reduced confidence.")

        # Session 89: Show consolidation validation errors (cross-checks, sanity checks)
        if hasattr(report, 'consolidation_errors') and report.consolidation_errors:
            print(f"\n🚨 DATA VALIDATION ERRORS ({len(report.consolidation_errors)}):")
            for error in report.consolidation_errors:
                print(f"   ❌ {error}")
            print(f"\n   ⚠️  CRITICAL: Fix validation errors before proceeding with analysis!")

        print(f"\n{'=' * 80}\n")


def load_token_data(token: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Load consolidated token data from data directory.

    Session 79G: Updated for new directory structure:
    - sources/raw/ contains raw API data (cryptorank.json, perplexity.json, dropstab.json)
    - sources/validation/ contains validation results
    - sources/metadata/ contains API attempt tracking

    Fallback logic:
    1. Try consolidated.json first (single source of truth)
    2. If not found, merge source files from sources/raw/
    3. Legacy support: also check old flat sources/ structure

    Args:
        token: Token symbol (e.g., "MONAD", "IRYS")

    Returns:
        (data, source_metadata) tuple
    """
    token_dir = PROJECT_ROOT / "data" / "tokens" / token.upper()

    if not token_dir.exists():
        raise FileNotFoundError(f"Token directory not found: {token_dir}")

    # Build source metadata from available source files
    source_metadata = {}
    sources_dir = token_dir / "sources"
    raw_dir = sources_dir / "raw"

    # Session 79G: New structure - check sources/raw/ first
    if raw_dir.exists():
        for source_file in raw_dir.glob("*.json"):
            try:
                with open(source_file, 'r') as f:
                    source_data = json.load(f)

                # Identify source type from filename (now clean names)
                filename = source_file.stem.lower()
                if filename == "cryptorank":
                    source_metadata["cryptorank_data"] = source_data
                elif filename == "perplexity":
                    source_metadata["perplexity_data"] = source_data
                    # Extract timestamp if available
                    if "analysis_date" in source_data:
                        source_metadata["perplexity_timestamp"] = source_data["analysis_date"]
                elif filename == "dropstab":
                    source_metadata["dropstab_data"] = source_data
            except json.JSONDecodeError:
                logger.warning(f"Could not parse source file: {source_file}")

    # Legacy fallback: check old flat sources/ structure
    elif sources_dir.exists():
        for source_file in sorted(sources_dir.glob("*.json")):
            try:
                with open(source_file, 'r') as f:
                    source_data = json.load(f)

                # Identify source type from filename (old format with dates)
                filename = source_file.stem.lower()
                if "cryptorank" in filename:
                    source_metadata["cryptorank_data"] = source_data
                elif "perplexity" in filename:
                    source_metadata["perplexity_data"] = source_data
                    if "timestamp" in source_data:
                        source_metadata["perplexity_timestamp"] = source_data["timestamp"]
                    elif "analysis_date" in source_data:
                        source_metadata["perplexity_timestamp"] = source_data["analysis_date"]
                elif "dropstab" in filename:
                    source_metadata["dropstab_data"] = source_data
                elif "agents" in filename:
                    source_metadata["agents_data"] = source_data
            except json.JSONDecodeError:
                logger.warning(f"Could not parse source file: {source_file}")

    # Try consolidated.json first
    consolidated_file = token_dir / "consolidated.json"
    if consolidated_file.exists():
        with open(consolidated_file, 'r') as f:
            data = json.load(f)

        # Session 87: Add days_until_tge to source_metadata for LIVE token detection
        if "days_until_tge" in data:
            source_metadata["days_until_tge"] = data["days_until_tge"]

        return data, source_metadata

    # Fallback: Merge source files to create consolidated view
    data = {}

    # Priority order: cryptorank (structured) > dropstab > perplexity > agents
    for source_key in ["agents_data", "perplexity_data", "dropstab_data", "cryptorank_data"]:
        if source_key in source_metadata:
            source_data = source_metadata[source_key]
            # Merge, with later sources overwriting earlier ones
            for key, value in source_data.items():
                if value is not None and value != "" and value != []:
                    data[key] = value

    if not data:
        raise FileNotFoundError(f"No data found for {token} - no consolidated.json or source files")

    return data, source_metadata


def validate_token(token: str, json_output: bool = False, quiet: bool = False) -> Dict:
    """
    Validate a single token and return results.

    Args:
        token: Token symbol
        json_output: If True, suppress print output
        quiet: If True, suppress all output except JSON

    Returns:
        Validation result dictionary
    """
    try:
        data, source_metadata = load_token_data(token)
    except FileNotFoundError as e:
        return {
            "token": token,
            "status": "ERROR",
            "error": str(e),
            "passed": False
        }

    validator = FieldValidator()

    # Suppress print output for JSON mode
    if json_output or quiet:
        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()):
            report = validator.validate_fields(data, token, source_metadata)
    else:
        report = validator.validate_fields(data, token, source_metadata)

    # Determine pass/fail - Session 84 Phase 2: Support PASS_WITH_WAIVERS status
    passed = report.critical_coverage_pct >= 100.0
    has_waivers = len(report.waivers_applied) > 0

    # Determine status
    if passed:
        if has_waivers:
            status = "PASS_WITH_WAIVERS"
        else:
            status = "PASS"
    else:
        status = "FAIL"

    result = {
        "token": token,
        "status": status,
        "passed": passed,
        "critical_coverage_pct": report.critical_coverage_pct,
        "important_coverage_pct": report.important_coverage_pct,
        "overall_confidence": report.overall_confidence,
        "missing_critical_fields": report.missing_critical_fields,
        "missing_important_fields": report.missing_important_fields,
        "validated_at": report.validated_at,
        "data_age_days": report.data_age_days,
        "has_stale_data": report.has_stale_data
    }

    # Session 84 Phase 2: Include waiver information if present
    if has_waivers:
        result["waivers_applied"] = [
            {
                "field": w.field,
                "waived": w.waived,
                "reason": w.reason,
                "confidence_penalty": w.confidence_penalty,
                "auto_waived": w.auto_waived,
                "tier": w.tier
            }
            for w in report.waivers_applied
        ]
        result["adjusted_confidence"] = report.adjusted_confidence
        result["confidence_adjustment"] = report.confidence_adjustment

    # Include full report if requested
    if json_output:
        result["full_report"] = report.to_dict()

    # Session 97: Check conviction data status
    conviction_status = check_conviction_data_status(token, data)
    result["conviction_data_status"] = conviction_status

    return result


def check_conviction_data_status(token: str, data: Dict) -> Dict:
    """
    Session 97: Check if conviction data exists and is up-to-date.

    Returns:
        Dict with conviction data status:
        - has_conviction_report: bool
        - conviction_score: float or None
        - recommendation: str or None
        - is_stale: bool (True if >24h old or never analyzed)
        - warning: str or None
    """
    token_path = PROJECT_ROOT / "data" / "tokens" / token.upper()
    analysis_path = token_path / "analysis"

    status = {
        "has_conviction_report": False,
        "conviction_score": None,
        "recommendation": None,
        "pipeline_status": None,
        "is_stale": True,
        "warning": None
    }

    # Check consolidated.json for conviction data
    pipeline_status = data.get("pipeline_status", "UNKNOWN")
    conviction_score = data.get("conviction_score", 0)
    recommendation = data.get("recommendation", "UNKNOWN")

    status["pipeline_status"] = pipeline_status
    status["conviction_score"] = conviction_score
    status["recommendation"] = recommendation

    # Check if analysis files exist
    if analysis_path.exists():
        conviction_files = list(analysis_path.glob("conviction_*.json"))
        if conviction_files:
            status["has_conviction_report"] = True
            # Check if most recent is today
            from datetime import datetime
            today = datetime.now().strftime("%Y-%m-%d")
            recent_file = sorted(conviction_files, reverse=True)[0]
            if today in recent_file.name:
                status["is_stale"] = False

    # Generate warnings
    if pipeline_status in ["REJECTED", "UNKNOWN", None]:
        status["warning"] = "CONVICTION_NOT_RUN: Run 'python3 scripts/analysis/analyze.py {} ' to generate conviction scores".format(token)
        status["is_stale"] = True
    elif conviction_score == 0 and recommendation == "REJECTED":
        status["warning"] = "CONVICTION_STALE: consolidated.json has placeholder values. Re-run analysis."
        status["is_stale"] = True
    elif not status["has_conviction_report"]:
        status["warning"] = "NO_CONVICTION_FILE: conviction_*.json not found in analysis/"
    elif status["is_stale"]:
        status["warning"] = "CONVICTION_STALE: Last analysis >24h ago. Consider refreshing."

    return status


def generate_html_report(report: ValidationReport, token: str, output_path: str):
    """
    Generate HTML validation report for visual data confidence transparency.

    Session 84 - Data Pipeline Analysis: P0 Priority
    Addresses critical gap in data confidence visibility.

    Args:
        report: ValidationReport object
        token: Token symbol
        output_path: Path to write HTML file
    """
    from pathlib import Path

    # Ensure output directory exists
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    # Determine overall status
    if report.critical_coverage_pct >= 100.0:
        if report.waivers_applied:
            overall_status = "PASS_WITH_WAIVERS"
            status_color = "#FFA500"  # Orange
            status_emoji = "⚠️"
        else:
            overall_status = "READY"
            status_color = "#22C55E"  # Green
            status_emoji = "✅"
    else:
        overall_status = "BLOCKED"
        status_color = "#EF4444"  # Red
        status_emoji = "❌"

    # Use adjusted confidence if available, otherwise overall confidence
    display_confidence = report.adjusted_confidence if report.adjusted_confidence is not None else report.overall_confidence

    # Generate confidence gauge bar
    confidence_width = int(display_confidence)
    confidence_color = "#22C55E" if display_confidence >= 80 else "#FFA500" if display_confidence >= 60 else "#EF4444"

    # Generate freshness status
    if report.data_age_days == 0:
        freshness_status = "✅ Data is fresh (< 24h old)"
        freshness_color = "#22C55E"
    elif report.data_age_days <= 1:
        freshness_status = f"✅ Data is fresh ({report.data_age_days} day old)"
        freshness_color = "#22C55E"
    elif report.data_age_days <= 3:
        freshness_status = f"⚠️ Data is {report.data_age_days} days old"
        freshness_color = "#FFA500"
    else:
        freshness_status = f"❌ Data is stale ({report.data_age_days} days old)"
        freshness_color = "#EF4444"

    # Build field tables by priority
    def build_field_table(field_list, tier_name, tier_color):
        rows = ""
        for fv in field_list:
            # Status icon
            if fv.validation_status == "PASS":
                status_icon = "✅"
                row_class = "pass"
            elif fv.validation_status == "WARN":
                status_icon = "⚠️"
                row_class = "warn"
            else:
                status_icon = "❌"
                row_class = "fail"

            # Value display
            if fv.present:
                value_display = str(fv.value)[:50] + ("..." if len(str(fv.value)) > 50 else "")
                confidence_display = f"{fv.confidence:.0f}%"
                sources_display = ", ".join(fv.sources)
            elif fv.is_derivable:
                value_display = "Derivable"
                confidence_display = "N/A"
                sources_display = "calculated"
            else:
                value_display = "MISSING"
                confidence_display = "0%"
                sources_display = "none"

            rows += f"""
                <tr class="{row_class}">
                    <td>{status_icon}</td>
                    <td><strong>{fv.field_name}</strong></td>
                    <td>{value_display}</td>
                    <td>{confidence_display}</td>
                    <td>{sources_display}</td>
                    <td>{fv.validation_message}</td>
                </tr>
            """

        return f"""
            <div class="field-section">
                <h3 style="color: {tier_color};">{tier_name}</h3>
                <table>
                    <thead>
                        <tr>
                            <th>Status</th>
                            <th>Field</th>
                            <th>Value</th>
                            <th>Confidence</th>
                            <th>Sources</th>
                            <th>Message</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows}
                    </tbody>
                </table>
            </div>
        """

    # Filter fields by tier
    critical_field_names = [f.name for f in CRITICAL_FIELDS]
    important_field_names = [f.name for f in IMPORTANT_FIELDS]

    critical_validations = [fv for fv in report.field_validations if fv.field_name in critical_field_names]
    important_validations = [fv for fv in report.field_validations if fv.field_name in important_field_names]
    nice_to_have_validations = [fv for fv in report.field_validations
                                 if fv.field_name not in critical_field_names + important_field_names]

    critical_table = build_field_table(critical_validations, "🔴 CRITICAL FIELDS", "#EF4444")
    important_table = build_field_table(important_validations, "🟡 IMPORTANT FIELDS", "#F59E0B")
    nice_to_have_table = build_field_table(nice_to_have_validations, "🟢 NICE-TO-HAVE FIELDS", "#22C55E")

    # Build waiver section
    waiver_section = ""
    if report.waivers_applied:
        waiver_rows = ""
        for waiver in report.waivers_applied:
            penalty_str = f"{waiver.confidence_penalty}" if waiver.confidence_penalty < 0 else f"+{waiver.confidence_penalty}"
            waiver_rows += f"""
                <tr>
                    <td>{'🤖 AUTO' if waiver.auto_waived else '👤 MANUAL'}</td>
                    <td><strong>{waiver.field}</strong></td>
                    <td>{waiver.tier}</td>
                    <td>{penalty_str}</td>
                    <td>{waiver.reason}</td>
                </tr>
            """

        confidence_impact = ""
        if report.confidence_adjustment is not None:
            confidence_impact = f"""
                <div style="margin-top: 15px; padding: 10px; background-color: #FEF3C7; border-left: 4px solid #F59E0B;">
                    <strong>📊 Confidence Impact:</strong><br>
                    Base confidence: {report.overall_confidence}%<br>
                    Adjustment: {report.confidence_adjustment}<br>
                    Adjusted confidence: {report.adjusted_confidence}%
                </div>
            """

        waiver_section = f"""
            <div class="waiver-section">
                <h3>✋ Field Waivers Applied ({len(report.waivers_applied)})</h3>
                <table>
                    <thead>
                        <tr>
                            <th>Type</th>
                            <th>Field</th>
                            <th>Tier</th>
                            <th>Penalty</th>
                            <th>Reason</th>
                        </tr>
                    </thead>
                    <tbody>
                        {waiver_rows}
                    </tbody>
                </table>
                {confidence_impact}
                <p style="margin-top: 15px; color: #F59E0B;">
                    ⚠️ <strong>NOTE:</strong> Token passed validation with waivers. Position sizing should reflect reduced confidence.
                </p>
            </div>
        """

    # Build HTML
    html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Validation Report - {token}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background-color: #0F172A;
            color: #E2E8F0;
            padding: 20px;
            line-height: 1.6;
        }}

        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}

        .header {{
            text-align: center;
            margin-bottom: 30px;
            padding: 20px;
            background: linear-gradient(135deg, #1E293B 0%, #334155 100%);
            border-radius: 12px;
        }}

        .header h1 {{
            font-size: 2.5em;
            margin-bottom: 10px;
        }}

        .status-card {{
            background-color: {status_color};
            color: white;
            padding: 20px;
            border-radius: 12px;
            text-align: center;
            margin-bottom: 30px;
            font-size: 1.5em;
            font-weight: bold;
        }}

        .confidence-gauge {{
            background-color: #1E293B;
            padding: 25px;
            border-radius: 12px;
            margin-bottom: 30px;
        }}

        .gauge-bar {{
            width: 100%;
            height: 40px;
            background-color: #334155;
            border-radius: 20px;
            overflow: hidden;
            margin: 15px 0;
        }}

        .gauge-fill {{
            height: 100%;
            background-color: {confidence_color};
            transition: width 0.5s ease;
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-weight: bold;
        }}

        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}

        .stat-card {{
            background-color: #1E293B;
            padding: 20px;
            border-radius: 12px;
            border-left: 4px solid #3B82F6;
        }}

        .stat-card h3 {{
            color: #94A3B8;
            font-size: 0.9em;
            margin-bottom: 10px;
        }}

        .stat-card .value {{
            font-size: 2em;
            font-weight: bold;
            color: #E2E8F0;
        }}

        .field-section {{
            background-color: #1E293B;
            padding: 25px;
            border-radius: 12px;
            margin-bottom: 30px;
        }}

        .field-section h3 {{
            margin-bottom: 20px;
            font-size: 1.3em;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            background-color: #0F172A;
            border-radius: 8px;
            overflow: hidden;
        }}

        thead {{
            background-color: #334155;
        }}

        th, td {{
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid #334155;
        }}

        th {{
            font-weight: 600;
            color: #CBD5E1;
        }}

        tr.pass {{
            background-color: rgba(34, 197, 94, 0.1);
        }}

        tr.warn {{
            background-color: rgba(245, 158, 11, 0.1);
        }}

        tr.fail {{
            background-color: rgba(239, 68, 68, 0.1);
        }}

        .waiver-section {{
            background-color: #FEF3C7;
            color: #78350F;
            padding: 25px;
            border-radius: 12px;
            margin-bottom: 30px;
        }}

        .waiver-section table {{
            background-color: white;
        }}

        .waiver-section th {{
            background-color: #FDE68A;
            color: #78350F;
        }}

        .waiver-section td {{
            color: #78350F;
            border-bottom: 1px solid #FDE68A;
        }}

        .footer {{
            text-align: center;
            margin-top: 40px;
            padding: 20px;
            color: #94A3B8;
            font-size: 0.9em;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>{token} Validation Report</h1>
            <p>Generated: {report.validated_at}</p>
        </div>

        <div class="status-card">
            {status_emoji} {overall_status}
        </div>

        <div class="confidence-gauge">
            <h2>Overall Confidence: {display_confidence}%</h2>
            <div class="gauge-bar">
                <div class="gauge-fill" style="width: {confidence_width}%;">
                    {display_confidence}%
                </div>
            </div>
        </div>

        <div class="stats-grid">
            <div class="stat-card">
                <h3>🔴 Critical Coverage</h3>
                <div class="value">{report.critical_coverage_pct:.1f}%</div>
            </div>
            <div class="stat-card">
                <h3>🟡 Important Coverage</h3>
                <div class="value">{report.important_coverage_pct:.1f}%</div>
            </div>
            <div class="stat-card">
                <h3>🟢 Nice-to-Have Coverage</h3>
                <div class="value">{report.nice_to_have_coverage_pct:.1f}%</div>
            </div>
            <div class="stat-card" style="border-left-color: {freshness_color};">
                <h3>Data Freshness</h3>
                <div class="value" style="font-size: 1em;">{freshness_status}</div>
            </div>
        </div>

        {waiver_section}

        {critical_table}

        {important_table}

        {nice_to_have_table}

        <div class="footer">
            <p>DACLE TGE Validation System - Session 84 Data Pipeline Analysis</p>
            <p>Confidence threshold for execution: ≥80%</p>
        </div>
    </div>
</body>
</html>
    """

    # Write HTML file
    with open(output_file, 'w') as f:
        f.write(html)

    print(f"\n✅ HTML report generated: {output_file}")
    print(f"   Open in browser: file://{output_file.absolute()}")


def main():
    """
    Field Validator CLI Mode

    Usage:
        python scripts/helpers/field_validator.py --token MONAD --json
        python scripts/helpers/field_validator.py --batch MONAD,IRYS,LAYER --json
        python scripts/helpers/field_validator.py --test  # Run with sample data
        python scripts/helpers/field_validator.py --token HUMIDIFI --output-html reports/humidifi_validation.html
    """
    parser = argparse.ArgumentParser(
        description="TGE Field Validator - Validate token data completeness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Single token (human-readable):
    python scripts/helpers/field_validator.py --token MONAD

  Single token (JSON for GitHub Actions):
    python scripts/helpers/field_validator.py --token MONAD --json

  Batch validation (JSON output):
    python scripts/helpers/field_validator.py --batch MONAD,IRYS,LAYER --json

  Test mode with sample data:
    python scripts/helpers/field_validator.py --test
        """
    )

    parser.add_argument(
        "--token", "-t",
        type=str,
        help="Single token symbol to validate (e.g., MONAD)"
    )

    parser.add_argument(
        "--batch", "-b",
        type=str,
        help="Comma-separated list of tokens to validate (e.g., MONAD,IRYS,LAYER)"
    )

    parser.add_argument(
        "--json", "-j",
        action="store_true",
        help="Output machine-readable JSON (for GitHub Actions)"
    )

    parser.add_argument(
        "--test",
        action="store_true",
        help="Run with sample test data"
    )

    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress all output except final result"
    )

    parser.add_argument(
        "--output-html",
        type=str,
        help="Generate HTML validation report at specified path (e.g., reports/validation.html)"
    )

    args = parser.parse_args()

    # Test mode with sample data
    if args.test:
        validator = FieldValidator()
        test_data = {
            "token_symbol": "TEST",
            "token_name": "Test Token",
            "tge_date": "2025-12-01T10:00:00Z",
            "float_percent": 20.48,
            "fdv_low": 100000000,
            "fdv_high": 120000000,
            "circulating_supply_at_tge": 204800000,
            "total_supply": 1000000000,
            "listing_price_low": 0.10,
            "listing_price_high": 0.12,
            "fdv_mc_ratio_low": 4.88,
            "fdv_mc_ratio_high": 4.88,
            "listing_exchanges": ["Binance", "Coinbase"],
            "investors": ["Paradigm", "a16z"],
            "funding_raised_usd": 50000000,
            "contract_address": "0x1234567890abcdef"
        }
        source_metadata = {
            "cryptorank_data": test_data,
            "perplexity_timestamp": "2025-11-28T10:00:00Z"
        }
        report = validator.validate_fields(test_data, "TEST", source_metadata)

        if args.json:
            print(json.dumps(report.to_dict(), indent=2, default=str))
        else:
            print(f"\n📊 Test validation complete. Critical coverage: {report.critical_coverage_pct}%")
        return

    # Single token validation
    if args.token:
        # Always get full report if HTML output is requested
        json_mode = args.json or args.output_html
        result = validate_token(args.token, json_output=json_mode, quiet=args.quiet)

        # Generate HTML report if requested
        if args.output_html and result.get("full_report"):
            from dataclasses import dataclass
            # Reconstruct ValidationReport from dict
            full_report_dict = result["full_report"]

            # Reconstruct field validations
            field_validations = [
                FieldValidationResult(**fv) for fv in full_report_dict["field_validations"]
            ]

            # Reconstruct waivers
            waivers_applied = [
                WaiverInfo(**w) for w in full_report_dict.get("waivers_applied", [])
            ] if full_report_dict.get("waivers_applied") else []

            # Reconstruct alias matches
            alias_matches = [
                AliasMatch(**am) for am in full_report_dict.get("alias_matches", [])
            ] if full_report_dict.get("alias_matches") else []

            # Create ValidationReport object
            report = ValidationReport(
                token=full_report_dict["token"],
                validated_at=full_report_dict["validated_at"],
                critical_coverage_pct=full_report_dict["critical_coverage_pct"],
                important_coverage_pct=full_report_dict["important_coverage_pct"],
                nice_to_have_coverage_pct=full_report_dict["nice_to_have_coverage_pct"],
                overall_confidence=full_report_dict["overall_confidence"],
                missing_critical_fields=full_report_dict["missing_critical_fields"],
                missing_important_fields=full_report_dict["missing_important_fields"],
                low_confidence_fields=[FieldValidationResult(**fv) for fv in full_report_dict["low_confidence_fields"]],
                stale_data_warnings=full_report_dict["stale_data_warnings"],
                has_stale_data=full_report_dict["has_stale_data"],
                data_age_days=full_report_dict["data_age_days"],
                field_validations=field_validations,
                alias_matches=alias_matches,
                waivers_applied=waivers_applied,
                adjusted_confidence=full_report_dict.get("adjusted_confidence"),
                confidence_adjustment=full_report_dict.get("confidence_adjustment")
            )

            generate_html_report(report, args.token, args.output_html)

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            status_emoji = "✅" if result["passed"] else "❌"
            print(f"\n{status_emoji} {args.token}: {result['status']}")
            if result.get("error"):
                print(f"   Error: {result['error']}")
            elif result.get("critical_coverage_pct") is not None:
                print(f"   Critical coverage: {result['critical_coverage_pct']}%")
                if result.get("missing_critical_fields"):
                    print(f"   Missing: {', '.join(result['missing_critical_fields'])}")

        # Exit with appropriate code for GitHub Actions
        sys.exit(0 if result["passed"] else 1)

    # Batch validation
    if args.batch:
        tokens = [t.strip().upper() for t in args.batch.split(",")]

        results = {
            "validated_at": datetime.now().isoformat(),
            "tokens_passed": [],
            "tokens_failed": [],
            "token_results": {}
        }

        for token in tokens:
            result = validate_token(token, json_output=True, quiet=True)
            results["token_results"][token] = result

            if result["passed"]:
                results["tokens_passed"].append(token)
            else:
                results["tokens_failed"].append(token)

        if args.json:
            print(json.dumps(results, indent=2, default=str))
        else:
            print(f"\n📊 Batch Validation Results:")
            print(f"   ✅ Passed: {', '.join(results['tokens_passed']) if results['tokens_passed'] else 'None'}")
            print(f"   ❌ Failed: {', '.join(results['tokens_failed']) if results['tokens_failed'] else 'None'}")

        # Exit with 1 if any failed
        sys.exit(0 if not results["tokens_failed"] else 1)

    # No arguments - show help
    parser.print_help()


if __name__ == "__main__":
    main()
