"""
Pre-Report Validation System - Session 52 Data Quality Framework

Purpose: Prevent GAIB-style violations BEFORE report generation
Validates agent outputs against 4 critical rules learned from Session 52 audits

Rules:
1. Funding Conflict Detection → Force consensus resolution
2. FDV Confirmation Check → Block EXECUTE if null/unconfirmed
3. Cross-Validation Veto → CONFLICTS = MONITOR (max 7.0/10)
4. Float Penalty Calculation → Auto-apply based on thresholds

Integration: Called in Phase 6.5 (AFTER agents, BEFORE report generation)

Reference: docs/DATA_QUALITY_AUDIT_2025-11-25.md
"""

import json
import sys
from pathlib import Path
from typing import Dict, Any, List, Tuple
from datetime import datetime

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.helpers.tge_output import (
    print_phase, print_info, print_success, print_warning, print_error
)


class ValidationResult:
    """Single validation rule result"""

    def __init__(self, rule_name: str, passed: bool, message: str,
                 severity: str = "INFO", correction: Dict[str, Any] = None):
        self.rule_name = rule_name
        self.passed = passed
        self.message = message
        self.severity = severity  # INFO, WARNING, ERROR, CRITICAL
        self.correction = correction or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule": self.rule_name,
            "passed": self.passed,
            "message": self.message,
            "severity": self.severity,
            "correction": self.correction
        }


class PreReportValidator:
    """
    Pre-Report Validation System

    Enforces 4 critical rules before report generation:
    1. Funding consensus (if conflicts exist)
    2. FDV confirmation (block EXECUTE if null)
    3. Cross-validation veto (conflicts = MONITOR)
    4. Float penalty (auto-apply thresholds)
    """

    def __init__(self, token: str, agent_outputs: Dict[str, Any]):
        self.token = token
        self.agent_outputs = agent_outputs
        self.results: List[ValidationResult] = []
        self.corrections_applied = False

    def validate_all(self) -> Tuple[bool, List[ValidationResult]]:
        """
        Run all 4 validation rules

        Returns:
            Tuple of (all_passed, results_list)
        """
        print_phase("6.5", "Pre-Report Validation (4 Rules)")
        print_info(f"🔍 Validating {self.token} against Session 52 quality rules...\n")

        # Rule 1: Funding Conflict Detection
        self._validate_funding_consensus()

        # Rule 2: FDV Confirmation Requirement
        self._validate_fdv_confirmation()

        # Rule 3: Cross-Validation Enforcement
        self._validate_cross_validation()

        # Rule 4: Float Penalty System
        self._validate_float_penalty()

        # Print summary
        self._print_summary()

        # Return overall pass/fail
        critical_failures = [r for r in self.results if not r.passed and r.severity == "CRITICAL"]
        all_passed = len(critical_failures) == 0

        return all_passed, self.results

    def _validate_funding_consensus(self):
        """
        Rule 1: Funding Conflict Detection

        IF 2+ sources disagree on funding amount:
          - Use CONSENSUS value (from 3+ sources if available)
          - Apply -0.5 data_quality_penalty to conviction
          - Set data_confidence ≤ 70%
          - Add warning note to report
        """
        print_info("📋 Rule 1: Funding Conflict Detection")

        # Check if consolidation metadata exists
        consolidation_meta = self.agent_outputs.get("_consolidation_metadata", {})

        if not consolidation_meta:
            result = ValidationResult(
                "Rule 1: Funding Consensus",
                True,
                "No consolidation metadata found (single source analysis)",
                "INFO"
            )
            self.results.append(result)
            print_success("  ✅ PASS - Single source, no conflicts possible\n")
            return

        # Check for funding conflicts
        conflicts = consolidation_meta.get("conflicts_resolved", 0)
        conflict_details = consolidation_meta.get("conflict_details", [])

        funding_conflict = any(
            "funding" in c.lower() or "raised" in c.lower()
            for c in conflict_details
        ) if conflict_details else False

        if not funding_conflict and conflicts == 0:
            result = ValidationResult(
                "Rule 1: Funding Consensus",
                True,
                "No funding conflicts detected across sources",
                "INFO"
            )
            self.results.append(result)
            print_success("  ✅ PASS - No funding conflicts\n")
            return

        # Funding conflict detected
        funding_total = self.agent_outputs.get("total_raised_usd")
        data_confidence = self.agent_outputs.get("data_confidence", 0)
        conviction = self.agent_outputs.get("conviction_score", 0)

        corrections = {}
        warnings = []

        # Check if consensus value was used
        if funding_total is not None:
            print_warning(f"  ⚠️  Funding conflict detected (${funding_total:,.0f})")
            warnings.append("Multiple sources disagreed on funding amount")

        # Check if data confidence was reduced
        if data_confidence > 70:
            print_error(f"  ❌ Data confidence too high: {data_confidence}% (should be ≤70%)")
            corrections["data_confidence"] = 70
            corrections["data_confidence_note"] = "Reduced due to funding conflicts"

        # Check if conviction penalty was applied
        # We can't easily detect if -0.5 was applied, but we can warn
        print_warning(f"  ⚠️  Current conviction: {conviction}/10")
        warnings.append("Verify -0.5 penalty applied to conviction for funding conflict")

        passed = data_confidence <= 70 if funding_conflict else True
        severity = "WARNING" if not passed else "INFO"

        result = ValidationResult(
            "Rule 1: Funding Consensus",
            passed,
            f"Funding conflict detected. Warnings: {'; '.join(warnings)}",
            severity,
            corrections
        )
        self.results.append(result)

        if passed:
            print_warning("  ⚠️  PARTIAL PASS - Conflict handled, verify penalties\n")
        else:
            print_error("  ❌ FAIL - Data confidence not reduced\n")

    def _validate_fdv_confirmation(self):
        """
        Rule 2: FDV Confirmation Requirement

        IF no official FDV disclosed:
          - Set fdv = null (not estimate)
          - Show range: fdv_estimate_low / fdv_estimate_high
          - Block EXECUTE recommendation
          - Show "TBD" in dashboard
          - Max conviction = 7.0/10

        CRITICAL: This prevents GAIB-style over-confidence (176M shown, 79M reality)
        """
        print_info("📋 Rule 2: FDV Confirmation Requirement")

        fdv = self.agent_outputs.get("fdv") or self.agent_outputs.get("fully_diluted_valuation")
        fdv_source = self.agent_outputs.get("fdv_source", "")
        fdv_confidence = self.agent_outputs.get("fdv_estimation_confidence", 100)
        recommendation = self.agent_outputs.get("recommendation", "")
        conviction = self.agent_outputs.get("conviction_score", 0)

        # Check if FDV is unconfirmed (null, estimated, or low confidence)
        fdv_unconfirmed = (
            fdv is None or
            fdv_confidence < 70 or
            "estimate" in fdv_source.lower() or
            "unconfirmed" in fdv_source.lower()
        )

        if not fdv_unconfirmed:
            result = ValidationResult(
                "Rule 2: FDV Confirmation",
                True,
                f"FDV confirmed: ${fdv:,.0f} (confidence: {fdv_confidence}%)",
                "INFO"
            )
            self.results.append(result)
            print_success(f"  ✅ PASS - FDV confirmed: ${fdv:,.0f}\n")
            return

        # FDV is unconfirmed - apply restrictions
        print_warning(f"  ⚠️  FDV unconfirmed (source: {fdv_source}, confidence: {fdv_confidence}%)")

        corrections = {}
        violations = []

        # Check 1: Recommendation should NOT be EXECUTE
        if recommendation == "EXECUTE":
            print_error("  ❌ CRITICAL: EXECUTE blocked - FDV unconfirmed")
            corrections["recommendation"] = "MONITOR"
            corrections["recommendation_note"] = "Downgraded from EXECUTE due to unconfirmed FDV (Rule 2)"
            violations.append("EXECUTE not allowed with unconfirmed FDV")

        # Check 2: Conviction should be ≤ 7.0
        if conviction > 7.0:
            print_error(f"  ❌ CRITICAL: Conviction {conviction}/10 too high (max 7.0 with unconfirmed FDV)")
            corrections["conviction_score"] = 7.0
            corrections["conviction_note"] = f"Capped from {conviction} due to unconfirmed FDV (Rule 2)"
            corrections["final_conviction"] = 7.0
            violations.append(f"Conviction reduced from {conviction} to 7.0")

        # Check 3: Execution alert should be false
        execution_alert = self.agent_outputs.get("execution_alert")
        if execution_alert:
            print_error("  ❌ Execution alert disabled - FDV unconfirmed")
            corrections["execution_alert"] = False
            violations.append("Execution alert disabled")

        # Check 4: FDV should show as null with range
        if fdv is not None and fdv_confidence < 70:
            print_warning("  ⚠️  FDV should be null (not estimated value)")
            corrections["fdv"] = None
            corrections["fdv_estimate_low"] = int(fdv * 0.5)  # 50% buffer
            corrections["fdv_estimate_high"] = int(fdv * 1.5)  # 50% buffer
            corrections["fdv_note"] = f"UNCONFIRMED - Estimate: ${fdv:,.0f} (confidence: {fdv_confidence}%)"
            violations.append("FDV set to null with estimate range")

        passed = len(violations) == 0
        severity = "CRITICAL" if not passed else "WARNING"

        result = ValidationResult(
            "Rule 2: FDV Confirmation",
            passed,
            f"FDV unconfirmed. Violations: {'; '.join(violations) if violations else 'None'}",
            severity,
            corrections
        )
        self.results.append(result)

        if passed:
            print_success("  ✅ PASS - Unconfirmed FDV handled correctly\n")
        else:
            print_error(f"  ❌ FAIL - {len(violations)} violation(s) detected\n")

    def _validate_cross_validation(self):
        """
        Rule 3: Cross-Validation Enforcement

        IF validation_status = "CONFLICTS_DETECTED":
          - Recommendation CANNOT be "EXECUTE"
          - Max conviction = 7.0/10
          - execution_alert = false
          - Add conflict warning to report

        CRITICAL: Conflicts are VETO power - no EXECUTE allowed
        """
        print_info("📋 Rule 3: Cross-Validation Enforcement")

        # Check for conflicts in consolidation metadata
        consolidation_meta = self.agent_outputs.get("_consolidation_metadata", {})
        conflicts_resolved = consolidation_meta.get("conflicts_resolved", 0)
        conflict_details = consolidation_meta.get("conflict_details", [])

        # Also check validation_blockers
        validation_blockers = self.agent_outputs.get("validation_blockers", [])

        has_conflicts = (
            conflicts_resolved > 0 or
            len(conflict_details) > 0 or
            len(validation_blockers) > 0
        )

        if not has_conflicts:
            result = ValidationResult(
                "Rule 3: Cross-Validation",
                True,
                "No conflicts detected - cross-validation passed",
                "INFO"
            )
            self.results.append(result)
            print_success("  ✅ PASS - No conflicts detected\n")
            return

        # Conflicts detected - enforce restrictions
        recommendation = self.agent_outputs.get("recommendation", "")
        conviction = self.agent_outputs.get("conviction_score", 0)
        execution_alert = self.agent_outputs.get("execution_alert", False)

        print_warning(f"  ⚠️  Conflicts detected: {conflicts_resolved} resolved, {len(conflict_details)} details")
        if validation_blockers:
            print_warning(f"  ⚠️  Validation blockers: {validation_blockers}")

        corrections = {}
        violations = []

        # Check 1: Recommendation CANNOT be EXECUTE
        if recommendation == "EXECUTE":
            print_error("  ❌ CRITICAL: EXECUTE blocked - conflicts detected")
            corrections["recommendation"] = "MONITOR"
            corrections["recommendation_note"] = "Downgraded from EXECUTE due to data conflicts (Rule 3)"
            violations.append("EXECUTE not allowed with unresolved conflicts")

        # Check 2: Conviction capped at 7.0
        if conviction > 7.0:
            print_error(f"  ❌ CRITICAL: Conviction {conviction}/10 too high (max 7.0 with conflicts)")
            corrections["conviction_score"] = 7.0
            corrections["conviction_note"] = f"Capped from {conviction} due to data conflicts (Rule 3)"
            corrections["final_conviction"] = 7.0
            violations.append(f"Conviction reduced from {conviction} to 7.0")

        # Check 3: Execution alert disabled
        if execution_alert:
            print_error("  ❌ Execution alert disabled - conflicts detected")
            corrections["execution_alert"] = False
            violations.append("Execution alert disabled")

        passed = len(violations) == 0
        severity = "CRITICAL" if not passed else "WARNING"

        result = ValidationResult(
            "Rule 3: Cross-Validation",
            passed,
            f"Data conflicts detected. Violations: {'; '.join(violations) if violations else 'None'}",
            severity,
            corrections
        )
        self.results.append(result)

        if passed:
            print_success("  ✅ PASS - Conflicts handled correctly\n")
        else:
            print_error(f"  ❌ FAIL - {len(violations)} violation(s) detected\n")

    def _validate_float_penalty(self):
        """
        Rule 4: Float Penalty System

        IF float > 15% → Apply penalty to circulating_supply component:
          - 16-20%: -1.0 penalty
          - 21-25%: -1.5 penalty
          - >25%: -2.0 penalty

        Float penalties reduce conviction when too much supply is unlocked at TGE.
        """
        print_info("📋 Rule 4: Float Penalty Calculation")

        float_pct = self.agent_outputs.get("float_percentage") or self.agent_outputs.get("tge_unlock_pct", 0)
        conviction = self.agent_outputs.get("conviction_score", 0)
        conviction_components = self.agent_outputs.get("conviction_components", {})
        circulating_supply_component = conviction_components.get("circulating_supply", 0)

        print_info(f"  Float: {float_pct}% | Conviction: {conviction}/10")

        # Determine expected penalty
        if float_pct <= 15:
            expected_penalty = 0.0
            bracket = "Optimal (≤15%)"
        elif float_pct <= 20:
            expected_penalty = -1.0
            bracket = "Moderate (16-20%)"
        elif float_pct <= 25:
            expected_penalty = -1.5
            bracket = "High (21-25%)"
        else:
            expected_penalty = -2.0
            bracket = "Very High (>25%)"

        print_info(f"  Bracket: {bracket} | Expected penalty: {expected_penalty}")

        if expected_penalty == 0.0:
            result = ValidationResult(
                "Rule 4: Float Penalty",
                True,
                f"Float {float_pct}% is optimal (≤15%) - no penalty required",
                "INFO"
            )
            self.results.append(result)
            print_success("  ✅ PASS - Optimal float, no penalty needed\n")
            return

        # Penalty should be applied
        corrections = {}
        violations = []

        # Check if penalty was applied
        # We look at circulating_supply component - should be negative or reduced
        if circulating_supply_component > 0:
            print_error(f"  ❌ Float penalty not applied (component: +{circulating_supply_component})")
            corrections["conviction_components.circulating_supply"] = expected_penalty
            corrections["conviction_components.float_penalty_note"] = f"Float {float_pct}% → {expected_penalty} penalty"
            violations.append(f"Penalty {expected_penalty} not applied to circulating_supply component")

            # Recalculate conviction
            adjusted_conviction = conviction + expected_penalty
            if adjusted_conviction != conviction:
                corrections["conviction_score"] = max(0, adjusted_conviction)
                corrections["final_conviction"] = max(0, adjusted_conviction)
                violations.append(f"Conviction adjusted from {conviction} to {adjusted_conviction}")
        else:
            # Component is already negative or zero - check if correct magnitude
            if abs(circulating_supply_component - expected_penalty) > 0.1:
                print_warning(f"  ⚠️  Penalty applied ({circulating_supply_component}) but magnitude may be incorrect (expected: {expected_penalty})")
            else:
                print_success(f"  ✅ Penalty correctly applied: {expected_penalty}")

        passed = len(violations) == 0
        severity = "ERROR" if not passed else "INFO"

        result = ValidationResult(
            "Rule 4: Float Penalty",
            passed,
            f"Float {float_pct}% ({bracket}). Violations: {'; '.join(violations) if violations else 'None'}",
            severity,
            corrections
        )
        self.results.append(result)

        if passed:
            print_success("  ✅ PASS - Float penalty applied correctly\n")
        else:
            print_error(f"  ❌ FAIL - {len(violations)} violation(s) detected\n")

    def _print_summary(self):
        """Print validation summary"""
        print_info("\n" + "="*70)
        print_info("VALIDATION SUMMARY")
        print_info("="*70 + "\n")

        passed = [r for r in self.results if r.passed]
        failed = [r for r in self.results if not r.passed]

        critical_failures = [r for r in failed if r.severity == "CRITICAL"]
        errors = [r for r in failed if r.severity == "ERROR"]
        warnings = [r for r in failed if r.severity == "WARNING"]

        print_info(f"Total Rules: {len(self.results)}")
        print_success(f"✅ Passed: {len(passed)}")

        if failed:
            print_error(f"❌ Failed: {len(failed)}")
            if critical_failures:
                print_error(f"  🚨 Critical: {len(critical_failures)}")
            if errors:
                print_error(f"  ⚠️  Errors: {len(errors)}")
            if warnings:
                print_warning(f"  ⚠️  Warnings: {len(warnings)}")

        print_info("\n" + "="*70 + "\n")

        # Print each result
        for result in self.results:
            icon = "✅" if result.passed else "❌"
            print_info(f"{icon} {result.rule_name}: {result.message}")

        print_info("\n" + "="*70 + "\n")

        # Overall verdict
        if len(critical_failures) > 0:
            print_error("🚨 VALIDATION FAILED - Critical violations detected")
            print_error("Report generation should be BLOCKED until issues are resolved\n")
        elif len(errors) > 0:
            print_warning("⚠️  VALIDATION WARNINGS - Review corrections before proceeding\n")
        else:
            print_success("✅ VALIDATION PASSED - Report generation approved\n")

    def apply_corrections(self) -> Dict[str, Any]:
        """
        Apply all corrections to agent_outputs

        Returns:
            Corrected agent_outputs dict
        """
        corrected_outputs = self.agent_outputs.copy()

        for result in self.results:
            if not result.passed and result.correction:
                print_warning(f"Applying corrections for {result.rule_name}...")
                for key, value in result.correction.items():
                    # Handle nested keys (e.g., "conviction_components.float_penalty")
                    if "." in key:
                        parent_key, child_key = key.split(".", 1)
                        if parent_key not in corrected_outputs:
                            corrected_outputs[parent_key] = {}
                        corrected_outputs[parent_key][child_key] = value
                    else:
                        corrected_outputs[key] = value

        self.corrections_applied = True
        return corrected_outputs

    def get_validation_report(self) -> Dict[str, Any]:
        """
        Generate validation report for logging

        Returns:
            Dict with validation results and metadata
        """
        return {
            "token": self.token,
            "validation_timestamp": datetime.now().isoformat(),
            "validation_version": "1.0",
            "rules_checked": len(self.results),
            "rules_passed": len([r for r in self.results if r.passed]),
            "rules_failed": len([r for r in self.results if not r.passed]),
            "critical_failures": len([r for r in self.results if not r.passed and r.severity == "CRITICAL"]),
            "corrections_applied": self.corrections_applied,
            "results": [r.to_dict() for r in self.results],
            "overall_status": "PASSED" if all(r.passed or r.severity != "CRITICAL" for r in self.results) else "FAILED"
        }


def validate_before_report(token: str, agent_outputs: Dict[str, Any],
                          auto_correct: bool = False) -> Tuple[bool, Dict[str, Any], Dict[str, Any]]:
    """
    Main entry point for pre-report validation

    Args:
        token: Token symbol
        agent_outputs: Agent pipeline outputs to validate
        auto_correct: If True, automatically apply corrections

    Returns:
        Tuple of (validation_passed, corrected_outputs, validation_report)
    """
    validator = PreReportValidator(token, agent_outputs)
    validation_passed, results = validator.validate_all()

    corrected_outputs = agent_outputs
    if auto_correct and not validation_passed:
        print_warning("\n🔧 Auto-correction enabled - applying fixes...\n")
        corrected_outputs = validator.apply_corrections()
        print_success("✅ Corrections applied\n")

    validation_report = validator.get_validation_report()

    return validation_passed, corrected_outputs, validation_report


if __name__ == "__main__":
    """
    Standalone testing mode

    Usage:
        python scripts/helpers/pre_report_validator.py IRYS
        python scripts/helpers/pre_report_validator.py GAIB --auto-correct
    """
    import argparse

    parser = argparse.ArgumentParser(description="Pre-Report Validation System")
    parser.add_argument("token", help="Token symbol to validate")
    parser.add_argument("--auto-correct", action="store_true", help="Automatically apply corrections")
    args = parser.parse_args()

    # Load agent outputs
    token = args.token.upper()
    agent_file = PROJECT_ROOT / "data" / "tokens" / token / "sources" / f"2_agents_{datetime.now().strftime('%Y-%m-%d')}.json"

    if not agent_file.exists():
        # Try latest file
        sources_dir = PROJECT_ROOT / "data" / "tokens" / token / "sources"
        agent_files = list(sources_dir.glob("2_agents_*.json"))
        if agent_files:
            agent_file = max(agent_files, key=lambda p: p.stat().st_mtime)
        else:
            print_error(f"No agent outputs found for {token}")
            sys.exit(1)

    print_info(f"Loading: {agent_file}")
    with open(agent_file, 'r') as f:
        agent_outputs = json.load(f)

    # Run validation
    passed, corrected, report = validate_before_report(token, agent_outputs, args.auto_correct)

    # Save validation report
    report_file = PROJECT_ROOT / "data" / "tokens" / token / "sources" / f"validation_report_{datetime.now().strftime('%Y-%m-%d')}.json"
    with open(report_file, 'w') as f:
        json.dump(report, f, indent=2)

    print_success(f"\n📄 Validation report saved: {report_file}")

    # Exit with appropriate code
    sys.exit(0 if passed else 1)
