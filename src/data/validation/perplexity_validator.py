"""
Perplexity Data Validator

Validates data from Perplexity research to catch parsing errors and absurd values.
Prevents bad data from entering the consolidation workflow.

Created: 2025-11-24 (Session 51.5 - Automation Priority #1)

Usage:
    from scripts.helpers.perplexity_validator import validate_perplexity_data

    # Validate Perplexity JSON data
    validation_result = validate_perplexity_data(perplexity_data)

    if validation_result["has_warnings"]:
        print("⚠️ Validation warnings:")
        for warning in validation_result["warnings"]:
            print(f"  - {warning['field']}: {warning['message']}")
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ValidationWarning:
    """Represents a validation warning for a specific field."""

    def __init__(self, field: str, value: Any, message: str, severity: str = "WARNING"):
        self.field = field
        self.value = value
        self.message = message
        self.severity = severity  # "WARNING" or "ERROR"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field": self.field,
            "value": self.value,
            "message": self.message,
            "severity": self.severity
        }


class PerplexityValidator:
    """Validates Perplexity data against defined thresholds."""

    # Validation thresholds
    THRESHOLDS = {
        "funding": {
            "max": 1_000_000_000,  # $1B - Flag if funding > $1B (likely parsing error)
            "suspicious_min": 100_000_000,  # $100M - Warn if funding > $100M (rare but possible)
        },
        "fdv": {
            "max": 100_000_000_000,  # $100B - Flag if FDV > $100B (likely parsing error)
            "suspicious_min": 10_000_000_000,  # $10B - Warn if FDV > $10B (rare for TGEs)
        },
        "market_cap": {
            "max": 100_000_000_000,  # $100B - Flag if MC > $100B
        },
        "float_percent": {
            "max": 50,  # 50% - Flag if float > 50% (uncommon for shorts)
            "suspicious_max": 40,  # 40% - Warn if float > 40%
        },
        "total_supply": {
            "max": 1_000_000_000_000_000,  # 1 quadrillion - Flag if absurdly high
            "suspicious_max": 100_000_000_000_000,  # 100 trillion - Warn if very high
        },
    }

    def __init__(self):
        self.warnings: List[ValidationWarning] = []

    def validate(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate Perplexity data and return validation result.

        Args:
            data: Perplexity JSON data dictionary

        Returns:
            Dict with validation results:
            {
                "is_valid": bool,
                "has_warnings": bool,
                "warnings": List[Dict],
                "errors": List[Dict],
                "validated_fields": List[str]
            }
        """
        self.warnings = []

        # Validate each field
        self._validate_funding(data)
        self._validate_fdv(data)
        self._validate_market_cap(data)
        self._validate_float_percent(data)
        self._validate_total_supply(data)

        # Separate warnings and errors
        warnings_list = [w for w in self.warnings if w.severity == "WARNING"]
        errors_list = [w for w in self.warnings if w.severity == "ERROR"]

        result = {
            "is_valid": len(errors_list) == 0,
            "has_warnings": len(warnings_list) > 0,
            "warnings": [w.to_dict() for w in warnings_list],
            "errors": [w.to_dict() for w in errors_list],
            "validated_fields": self._get_validated_fields(data)
        }

        return result

    def _validate_funding(self, data: Dict[str, Any]) -> None:
        """Validate funding amount."""
        funding = self._extract_numeric(data, ["total_funding", "funding", "raise_amount"])

        if funding is None:
            return

        thresholds = self.THRESHOLDS["funding"]

        # ERROR: Absurdly high (likely trillion vs million parsing error)
        if funding > thresholds["max"]:
            self.warnings.append(ValidationWarning(
                field="total_funding",
                value=funding,
                message=f"CRITICAL: Funding ${funding:,.0f} exceeds ${thresholds['max']:,.0f} threshold. "
                        f"Likely parsing error (e.g., $13.5T interpreted as trillion instead of total/million). "
                        f"Cross-validate with CryptoRank.",
                severity="ERROR"
            ))

        # WARNING: Suspiciously high but possible
        elif funding > thresholds["suspicious_min"]:
            self.warnings.append(ValidationWarning(
                field="total_funding",
                value=funding,
                message=f"Funding ${funding:,.0f} is unusually high (>${thresholds['suspicious_min']:,.0f}). "
                        f"Verify this is correct - most TGE projects raise <$100M.",
                severity="WARNING"
            ))

    def _validate_fdv(self, data: Dict[str, Any]) -> None:
        """Validate fully diluted valuation."""
        fdv = self._extract_numeric(data, ["fdv", "fully_diluted_valuation", "fdv_high"])

        if fdv is None:
            return

        thresholds = self.THRESHOLDS["fdv"]

        # ERROR: Absurdly high
        if fdv > thresholds["max"]:
            self.warnings.append(ValidationWarning(
                field="fdv",
                value=fdv,
                message=f"CRITICAL: FDV ${fdv:,.0f} exceeds ${thresholds['max']:,.0f} threshold. "
                        f"Likely parsing error or miscalculation.",
                severity="ERROR"
            ))

        # WARNING: Suspiciously high for TGE
        elif fdv > thresholds["suspicious_min"]:
            self.warnings.append(ValidationWarning(
                field="fdv",
                value=fdv,
                message=f"FDV ${fdv:,.0f} is very high for a TGE (>${thresholds['suspicious_min']:,.0f}). "
                        f"Verify calculation: Total Supply × Listing Price.",
                severity="WARNING"
            ))

    def _validate_market_cap(self, data: Dict[str, Any]) -> None:
        """Validate market cap."""
        mc = self._extract_numeric(data, ["market_cap", "mc", "initial_market_cap", "circulating_supply_value"])

        if mc is None:
            return

        thresholds = self.THRESHOLDS["market_cap"]

        if mc > thresholds["max"]:
            self.warnings.append(ValidationWarning(
                field="market_cap",
                value=mc,
                message=f"CRITICAL: Market Cap ${mc:,.0f} exceeds ${thresholds['max']:,.0f} threshold. "
                        f"Likely parsing error.",
                severity="ERROR"
            ))

    def _validate_float_percent(self, data: Dict[str, Any]) -> None:
        """Validate float percentage."""
        float_pct = self._extract_numeric(data, ["float_percentage", "float_percent", "tge_unlock_pct"])

        if float_pct is None:
            return

        thresholds = self.THRESHOLDS["float_percent"]

        # ERROR: Over 50% (bad for TGE shorts)
        if float_pct > thresholds["max"]:
            self.warnings.append(ValidationWarning(
                field="float_percent",
                value=float_pct,
                message=f"Float {float_pct:.1f}% exceeds {thresholds['max']}% threshold. "
                        f"High float reduces dump potential for shorts. Verify this is TGE unlock % (not total circulating).",
                severity="ERROR"
            ))

        # WARNING: Over 40% (approaching threshold)
        elif float_pct > thresholds["suspicious_max"]:
            self.warnings.append(ValidationWarning(
                field="float_percent",
                value=float_pct,
                message=f"Float {float_pct:.1f}% is high (>{thresholds['suspicious_max']}%). "
                        f"Higher float = lower dump potential. Confirm this is TGE unlock % vs future unlocks.",
                severity="WARNING"
            ))

    def _validate_total_supply(self, data: Dict[str, Any]) -> None:
        """Validate total token supply."""
        supply = self._extract_numeric(data, ["total_supply", "totalSupply", "max_supply"])

        if supply is None:
            return

        thresholds = self.THRESHOLDS["total_supply"]

        # ERROR: Absurdly high (quadrillion+ tokens)
        if supply > thresholds["max"]:
            self.warnings.append(ValidationWarning(
                field="total_supply",
                value=supply,
                message=f"Total supply {supply:,.0f} is absurdly high (>1 quadrillion). "
                        f"Likely parsing error or wrong unit (e.g., Wei instead of tokens).",
                severity="ERROR"
            ))

        # WARNING: Very high (100 trillion+)
        elif supply > thresholds["suspicious_max"]:
            self.warnings.append(ValidationWarning(
                field="total_supply",
                value=supply,
                message=f"Total supply {supply:,.0f} is very high (>100 trillion). "
                        f"Verify this is correct - most tokens have <100B supply.",
                severity="WARNING"
            ))

    def _extract_numeric(self, data: Dict[str, Any], field_names: List[str]) -> Optional[float]:
        """
        Extract numeric value from multiple possible field names.

        Args:
            data: Data dictionary
            field_names: List of possible field names to check

        Returns:
            Numeric value if found, None otherwise
        """
        for field in field_names:
            value = data.get(field)
            if value is not None:
                try:
                    # Handle string representations
                    if isinstance(value, str):
                        # Remove commas and convert
                        value = value.replace(",", "")
                        return float(value)
                    return float(value)
                except (ValueError, TypeError):
                    continue
        return None

    def _get_validated_fields(self, data: Dict[str, Any]) -> List[str]:
        """Get list of fields that were validated."""
        validated = []

        if self._extract_numeric(data, ["total_funding", "funding"]) is not None:
            validated.append("funding")
        if self._extract_numeric(data, ["fdv", "fully_diluted_valuation"]) is not None:
            validated.append("fdv")
        if self._extract_numeric(data, ["market_cap", "mc"]) is not None:
            validated.append("market_cap")
        if self._extract_numeric(data, ["float_percentage", "float_percent"]) is not None:
            validated.append("float_percent")
        if self._extract_numeric(data, ["total_supply"]) is not None:
            validated.append("total_supply")

        return validated


def validate_perplexity_data(data: Dict[str, Any], verbose: bool = True) -> Dict[str, Any]:
    """
    Validate Perplexity JSON data and return results.

    Args:
        data: Perplexity JSON data dictionary
        verbose: If True, print warnings and errors to console

    Returns:
        Validation result dictionary
    """
    validator = PerplexityValidator()
    result = validator.validate(data)

    if verbose:
        token_name = data.get("name") or data.get("token_name") or data.get("symbol", "UNKNOWN")

        if result["errors"]:
            logger.error(f"🚨 VALIDATION ERRORS for {token_name}:")
            for error in result["errors"]:
                logger.error(f"  [{error['field']}] {error['message']}")
                logger.error(f"    Current value: {error['value']}")

        if result["warnings"]:
            logger.warning(f"⚠️  VALIDATION WARNINGS for {token_name}:")
            for warning in result["warnings"]:
                logger.warning(f"  [{warning['field']}] {warning['message']}")
                logger.warning(f"    Current value: {warning['value']}")

        if not result["errors"] and not result["warnings"]:
            logger.info(f"✅ Validation passed for {token_name} ({len(result['validated_fields'])} fields checked)")

    return result


def cross_validate_against_primary_sources(
    perplexity_data: Dict[str, Any],
    primary_data: Dict[str, Any],
    token: str = "UNKNOWN"
) -> Dict[str, Any]:
    """
    Cross-validate Perplexity data against primary sources (Session 88).

    This catches Perplexity hallucinations by comparing critical fields against
    verified data from CryptoRank, Dropstab, ICODrops, CoinGecko.

    Learning: SEEK case - Perplexity couldn't find TGE date, but CoinGecko showed
    the token was already live. Cross-validation would have caught this.

    Args:
        perplexity_data: Data from Perplexity research
        primary_data: Data from primary sources (CryptoRank, Dropstab, etc.)
        token: Token symbol for logging

    Returns:
        Dict with:
        - valid: bool - True if no critical conflicts
        - conflicts: List[Dict] - Fields where values differ significantly
        - trust_recommendations: Dict - Which source to trust per field
        - perplexity_hallucinations: List[str] - Likely hallucinated fields
    """
    conflicts = []
    trust_recommendations = {}
    hallucinations = []

    # Fields to cross-validate with tolerance thresholds
    CRITICAL_FIELDS = {
        "fdv": {"tolerance_pct": 20},  # 20% difference allowed
        "fdv_low": {"tolerance_pct": 20},
        "fdv_high": {"tolerance_pct": 20},
        "float_percent": {"tolerance_pct": 10},  # 10% difference
        "total_supply": {"tolerance_pct": 5},  # 5% difference
        "tge_date": {"type": "date"},  # Special handling
    }

    def get_value(data: Dict, keys: List[str]) -> Any:
        """Get first non-None value from list of keys."""
        for key in keys:
            val = data.get(key)
            if val is not None:
                return val
        return None

    for field, config in CRITICAL_FIELDS.items():
        perp_val = get_value(perplexity_data, [field])
        primary_val = get_value(primary_data, [field])

        # Skip if either is missing
        if perp_val is None or primary_val is None:
            continue

        # Handle date comparison
        if config.get("type") == "date":
            # Normalize dates for comparison
            try:
                from datetime import datetime

                def parse_date(d):
                    if isinstance(d, str):
                        d = d.replace("Z", "+00:00")
                        if "T" in d:
                            return datetime.fromisoformat(d.split("+")[0])
                        return datetime.strptime(d, "%Y-%m-%d")
                    return d

                perp_dt = parse_date(perp_val)
                primary_dt = parse_date(primary_val)

                days_diff = abs((perp_dt - primary_dt).days)

                if days_diff > 7:  # More than 7 days difference
                    conflicts.append({
                        "field": field,
                        "perplexity_value": perp_val,
                        "primary_value": primary_val,
                        "difference": f"{days_diff} days",
                        "severity": "CRITICAL" if days_diff > 30 else "WARNING"
                    })
                    trust_recommendations[field] = "primary"

                    if days_diff > 30:
                        hallucinations.append(field)

            except Exception as e:
                logger.warning(f"Date comparison failed for {field}: {e}")

        else:
            # Numeric comparison
            try:
                perp_num = float(perp_val) if perp_val else 0
                primary_num = float(primary_val) if primary_val else 0

                if primary_num == 0:
                    continue

                diff_pct = abs((perp_num - primary_num) / primary_num) * 100
                tolerance = config.get("tolerance_pct", 20)

                if diff_pct > tolerance:
                    conflicts.append({
                        "field": field,
                        "perplexity_value": perp_val,
                        "primary_value": primary_val,
                        "difference_pct": f"{diff_pct:.1f}%",
                        "tolerance_pct": f"{tolerance}%",
                        "severity": "CRITICAL" if diff_pct > tolerance * 2 else "WARNING"
                    })
                    trust_recommendations[field] = "primary"

                    if diff_pct > tolerance * 3:  # 3x tolerance = likely hallucination
                        hallucinations.append(field)

            except (ValueError, TypeError) as e:
                logger.warning(f"Numeric comparison failed for {field}: {e}")

    # Log findings
    valid = len([c for c in conflicts if c["severity"] == "CRITICAL"]) == 0

    if conflicts:
        logger.warning(f"⚠️ Cross-validation conflicts for {token}:")
        for conflict in conflicts:
            logger.warning(
                f"  {conflict['field']}: Perplexity={conflict['perplexity_value']} "
                f"vs Primary={conflict['primary_value']} "
                f"({conflict.get('difference_pct', conflict.get('difference', 'N/A'))})"
            )

    if hallucinations:
        logger.error(f"🚨 Likely Perplexity hallucinations for {token}: {hallucinations}")

    return {
        "valid": valid,
        "conflicts": conflicts,
        "trust_recommendations": trust_recommendations,
        "perplexity_hallucinations": hallucinations,
        "fields_checked": list(CRITICAL_FIELDS.keys()),
        "token": token
    }


# CLI for testing
if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path

    # Test with IRYS data
    if len(sys.argv) > 1:
        file_path = sys.argv[1]
    else:
        # Default to IRYS Perplexity file
        file_path = Path(__file__).parent.parent.parent / "data/tokens/IRYS/sources/1_perplexity_2025-11-23.json"

    print(f"Testing Perplexity validator with: {file_path}")

    if not Path(file_path).exists():
        print(f"❌ File not found: {file_path}")
        sys.exit(1)

    with open(file_path) as f:
        data = json.load(f)

    # Test validation
    result = validate_perplexity_data(data, verbose=True)

    print("\n" + "="*60)
    print("VALIDATION SUMMARY")
    print("="*60)
    print(f"Valid: {result['is_valid']}")
    print(f"Has Warnings: {result['has_warnings']}")
    print(f"Validated Fields: {', '.join(result['validated_fields'])}")
    print(f"Errors: {len(result['errors'])}")
    print(f"Warnings: {len(result['warnings'])}")

    if not result["is_valid"]:
        print("\n⚠️  Data requires manual review before consolidation")
        sys.exit(1)
    else:
        print("\n✅ Data passed validation")
        sys.exit(0)
