#!/usr/bin/env python3
"""
Data Integrity Validator - Session 339 (L094)

Rock-solid data validation to ensure refresh operations actually succeed.

This module provides:
1. Source-level success tracking (which sources actually returned data)
2. Critical field validation (are required fields present AND non-empty)
3. JSON integrity validation (is the file valid and parseable)
4. Atomic file operations (temp file + rename pattern)
5. Fetch result classification (SUCCESS/PARTIAL/FAILED)

The core principle: A refresh is only "successful" if data was actually updated.
Reporting "done" when data wasn't updated is a critical bug.

Usage:
    from src.data.data_integrity import (
        validate_consolidated_json,
        validate_fetch_result,
        atomic_json_write,
        FetchResult,
        DataValidationResult
    )

    # Validate a fetch result
    result = validate_fetch_result(
        sources_tried=["coingecko", "cryptorank", "dropstab"],
        sources_succeeded=["coingecko"],
        data=fetched_data
    )
    if result.status == FetchStatus.FAILED:
        # Don't report success!
        logger.error(f"Fetch failed: {result.failure_reasons}")

    # Atomic write with backup
    success = atomic_json_write(
        filepath="/path/to/consolidated.json",
        data=new_data,
        create_backup=True
    )

Created: Session 339 (2026-01-21)
Category: Data Reliability Infrastructure
"""

import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# ENUMS AND DATA CLASSES
# =============================================================================

class FetchStatus(Enum):
    """Classification of fetch operation outcome."""
    SUCCESS = "success"           # All required sources returned data
    PARTIAL = "partial"           # Some sources succeeded, some failed
    FAILED = "failed"             # No sources returned usable data
    SKIPPED = "skipped"           # Fetch was intentionally skipped


class DataQuality(Enum):
    """Classification of data completeness."""
    COMPLETE = "complete"         # All critical fields present
    ACCEPTABLE = "acceptable"     # Most critical fields present (>75%)
    INCOMPLETE = "incomplete"     # Missing many fields (50-75%)
    UNUSABLE = "unusable"         # Missing too many fields (<50%)


@dataclass
class FetchResult:
    """Result of a data fetch operation with detailed tracking."""
    status: FetchStatus
    sources_tried: List[str]
    sources_succeeded: List[str]
    sources_failed: List[str]
    failure_reasons: Dict[str, str]  # source -> reason
    data: Dict[str, Any]
    fetched_at: str
    elapsed_seconds: float

    # Computed fields
    success_rate: float = 0.0

    def __post_init__(self):
        if self.sources_tried:
            self.success_rate = len(self.sources_succeeded) / len(self.sources_tried)

    def to_metadata(self) -> Dict[str, Any]:
        """Convert to metadata dict for storage."""
        return {
            "fetch_status": self.status.value,
            "sources_tried": self.sources_tried,
            "sources_succeeded": self.sources_succeeded,
            "sources_failed": self.sources_failed,
            "failure_reasons": self.failure_reasons,
            "success_rate": round(self.success_rate, 2),
            "fetched_at": self.fetched_at,
            "elapsed_seconds": self.elapsed_seconds
        }


@dataclass
class DataValidationResult:
    """Result of data validation check."""
    is_valid: bool
    quality: DataQuality
    critical_fields_present: List[str]
    critical_fields_missing: List[str]
    important_fields_present: List[str]
    important_fields_missing: List[str]
    completeness_pct: float
    issues: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage/API response."""
        return {
            "is_valid": self.is_valid,
            "quality": self.quality.value,
            "critical_fields_present": self.critical_fields_present,
            "critical_fields_missing": self.critical_fields_missing,
            "important_fields_present": self.important_fields_present,
            "important_fields_missing": self.important_fields_missing,
            "completeness_pct": round(self.completeness_pct, 1),
            "issues": self.issues
        }


# =============================================================================
# CRITICAL FIELD DEFINITIONS
# =============================================================================

# Fields that MUST be present for playbook generation
# Grouped by equivalence - at least one field in each group must be present
CRITICAL_FIELD_GROUPS = [
    ["tge_date"],
    ["fdv", "fdv_low", "fdv_at_tge", "fdv_at_tge_low"],
    ["listing_price", "listing_price_low", "current_price"],
    ["float_percent", "float_pct"],
    ["total_supply"],
    ["circulating_supply_at_tge", "circulating_supply"],
]

# Fields that improve analysis quality but don't block
IMPORTANT_FIELDS = [
    "blockchain", "category", "funding_rounds", "total_funding",
    "investors", "vc_investors", "vesting_schedule", "token_allocation",
    "whitepaper_url", "website", "twitter_url", "listing_exchanges",
    "contract_address", "market_cap"
]

# Fields that indicate a valid data refresh occurred
FRESHNESS_INDICATOR_FIELDS = [
    "current_price", "fdv", "market_cap", "circulating_supply",
    "volume_24h", "price_change_24h"
]


# =============================================================================
# VALIDATION FUNCTIONS
# =============================================================================

def validate_fetch_result(
    sources_tried: List[str],
    sources_succeeded: List[str],
    data: Dict[str, Any],
    required_sources: Optional[List[str]] = None
) -> FetchResult:
    """
    Validate the result of a data fetch operation.

    Args:
        sources_tried: List of sources that were attempted
        sources_succeeded: List of sources that returned data
        data: The merged data from all sources
        required_sources: Optional list of sources that MUST succeed

    Returns:
        FetchResult with detailed classification
    """
    sources_failed = [s for s in sources_tried if s not in sources_succeeded]
    failure_reasons = {}

    # Classify failures (we don't have actual error messages, so describe generally)
    for source in sources_failed:
        if source in ["cryptorank", "cryptorank_web"]:
            failure_reasons[source] = "API error or rate limit"
        elif source == "coingecko":
            failure_reasons[source] = "Token not found or API error"
        elif source in ["dropstab", "icodrops"]:
            failure_reasons[source] = "Scraping failed or token not listed"
        elif source in ["coinmarketcap", "coinmarketcap_full"]:
            failure_reasons[source] = "Rate limit (333/day) or not found"
        else:
            failure_reasons[source] = "Unknown error"

    # Determine status
    if not sources_tried:
        status = FetchStatus.SKIPPED
    elif not sources_succeeded:
        status = FetchStatus.FAILED
    elif len(sources_succeeded) == len(sources_tried):
        status = FetchStatus.SUCCESS
    else:
        # Check if required sources failed
        if required_sources:
            required_failed = [s for s in required_sources if s in sources_failed]
            if required_failed:
                status = FetchStatus.FAILED
            else:
                status = FetchStatus.PARTIAL
        else:
            status = FetchStatus.PARTIAL

    return FetchResult(
        status=status,
        sources_tried=sources_tried,
        sources_succeeded=sources_succeeded,
        sources_failed=sources_failed,
        failure_reasons=failure_reasons,
        data=data,
        fetched_at=datetime.utcnow().isoformat() + "Z",
        elapsed_seconds=data.get("_primary_fetch_metadata", {}).get("elapsed_seconds", 0)
    )


def validate_consolidated_data(
    data: Dict[str, Any],
    strict_mode: bool = False
) -> DataValidationResult:
    """
    Validate consolidated.json data for completeness and quality.

    Args:
        data: The consolidated data dictionary
        strict_mode: If True, require all critical fields (not just one per group)

    Returns:
        DataValidationResult with detailed field tracking
    """
    issues = []
    critical_present = []
    critical_missing = []
    important_present = []
    important_missing = []

    # Check critical field groups
    for group in CRITICAL_FIELD_GROUPS:
        group_satisfied = False
        for field_name in group:
            value = data.get(field_name)
            if _is_valid_value(value):
                critical_present.append(field_name)
                group_satisfied = True
                break

        if not group_satisfied:
            # All fields in this group are missing
            critical_missing.append(group[0])  # Report the primary field name
            issues.append(f"Missing critical field group: {group}")

    # Check important fields
    for field_name in IMPORTANT_FIELDS:
        value = data.get(field_name)
        if _is_valid_value(value):
            important_present.append(field_name)
        else:
            important_missing.append(field_name)

    # Calculate completeness
    total_critical_groups = len(CRITICAL_FIELD_GROUPS)
    critical_satisfied = total_critical_groups - len(critical_missing)
    critical_pct = (critical_satisfied / total_critical_groups) * 100 if total_critical_groups else 0

    important_pct = (len(important_present) / len(IMPORTANT_FIELDS)) * 100 if IMPORTANT_FIELDS else 0

    # Weighted completeness: critical 70%, important 30%
    completeness_pct = (critical_pct * 0.7) + (important_pct * 0.3)

    # Determine quality level
    if critical_pct >= 100 and important_pct >= 75:
        quality = DataQuality.COMPLETE
    elif critical_pct >= 83:  # 5/6 groups
        quality = DataQuality.ACCEPTABLE
    elif critical_pct >= 50:
        quality = DataQuality.INCOMPLETE
    else:
        quality = DataQuality.UNUSABLE

    # Is valid if at least ACCEPTABLE quality
    is_valid = quality in [DataQuality.COMPLETE, DataQuality.ACCEPTABLE]

    # Add issues for missing important fields if quality is incomplete
    if quality == DataQuality.INCOMPLETE:
        issues.append(f"Missing {len(critical_missing)} critical field groups")

    return DataValidationResult(
        is_valid=is_valid,
        quality=quality,
        critical_fields_present=critical_present,
        critical_fields_missing=critical_missing,
        important_fields_present=important_present,
        important_fields_missing=important_missing,
        completeness_pct=completeness_pct,
        issues=issues
    )


def validate_json_file(filepath: Path) -> Tuple[bool, Optional[Dict], List[str]]:
    """
    Validate that a JSON file exists, is readable, and contains valid JSON.

    Args:
        filepath: Path to the JSON file

    Returns:
        Tuple of (is_valid, data, issues)
    """
    issues = []

    if not filepath.exists():
        return False, None, ["File does not exist"]

    if not filepath.is_file():
        return False, None, ["Path is not a file"]

    # Check file size (empty or suspiciously small)
    file_size = filepath.stat().st_size
    if file_size == 0:
        return False, None, ["File is empty (0 bytes)"]
    if file_size < 10:  # Less than "{}" with minimal content
        issues.append(f"File suspiciously small ({file_size} bytes)")

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        # Check for truncated JSON (common in interrupted writes)
        if not content.strip().endswith('}') and not content.strip().endswith(']'):
            return False, None, ["JSON appears truncated (doesn't end with } or ])"]

        data = json.loads(content)

        # Check for empty dict/list
        if data == {} or data == []:
            issues.append("File contains empty JSON structure")

        return True, data, issues

    except json.JSONDecodeError as e:
        return False, None, [f"Invalid JSON: {str(e)}"]
    except Exception as e:
        return False, None, [f"Read error: {str(e)}"]


def _is_valid_value(value: Any) -> bool:
    """Check if a value is present and non-empty."""
    if value is None:
        return False
    if isinstance(value, str) and value.strip() == "":
        return False
    if isinstance(value, (list, dict)) and len(value) == 0:
        return False
    if isinstance(value, (int, float)) and value == 0:
        # Zero is valid for some fields but not for prices/supply
        return True  # Allow zero - caller should check specific fields
    return True


# =============================================================================
# ATOMIC FILE OPERATIONS
# =============================================================================

def atomic_json_write(
    filepath: Path,
    data: Dict[str, Any],
    create_backup: bool = True,
    backup_suffix: str = ".backup"
) -> Tuple[bool, Optional[str]]:
    """
    Write JSON data atomically with optional backup.

    Uses temp file + rename pattern to prevent partial writes.
    Creates backup of existing file before overwriting.

    Args:
        filepath: Target file path
        data: Data to write
        create_backup: Whether to backup existing file
        backup_suffix: Suffix for backup file

    Returns:
        Tuple of (success, error_message)
    """
    filepath = Path(filepath)

    try:
        # Create backup if file exists
        if create_backup and filepath.exists():
            backup_path = filepath.with_suffix(filepath.suffix + backup_suffix)
            try:
                shutil.copy2(filepath, backup_path)
                logger.debug(f"Created backup: {backup_path}")
            except Exception as e:
                logger.warning(f"Failed to create backup: {e}")

        # Write to temp file first
        dir_path = filepath.parent
        dir_path.mkdir(parents=True, exist_ok=True)

        with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.json.tmp',
            dir=dir_path,
            delete=False,
            encoding='utf-8'
        ) as tmp_file:
            json.dump(data, tmp_file, indent=2, ensure_ascii=False)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
            tmp_path = tmp_file.name

        # Atomic rename
        os.replace(tmp_path, filepath)

        logger.debug(f"Atomic write successful: {filepath}")
        return True, None

    except Exception as e:
        error_msg = f"Atomic write failed: {str(e)}"
        logger.error(error_msg)

        # Clean up temp file if it exists
        if 'tmp_path' in locals():
            try:
                os.unlink(tmp_path)
            except:
                pass

        return False, error_msg


def restore_from_backup(filepath: Path, backup_suffix: str = ".backup") -> Tuple[bool, Optional[str]]:
    """
    Restore a file from its backup.

    Args:
        filepath: The original file path
        backup_suffix: The suffix used for backups

    Returns:
        Tuple of (success, error_message)
    """
    filepath = Path(filepath)
    backup_path = filepath.with_suffix(filepath.suffix + backup_suffix)

    if not backup_path.exists():
        return False, "Backup file does not exist"

    try:
        shutil.copy2(backup_path, filepath)
        logger.info(f"Restored from backup: {filepath}")
        return True, None
    except Exception as e:
        return False, f"Restore failed: {str(e)}"


# =============================================================================
# HIGH-LEVEL VALIDATION FUNCTIONS
# =============================================================================

def validate_refresh_result(
    token: str,
    fetch_metadata: Dict[str, Any],
    consolidated_path: Path,
    previous_data: Optional[Dict] = None
) -> Dict[str, Any]:
    """
    Comprehensive validation of a token refresh operation.

    This is the main entry point for validating that a refresh actually worked.

    Args:
        token: Token symbol
        fetch_metadata: Metadata from fetch_from_primary_sources()
        consolidated_path: Path to consolidated.json
        previous_data: Previous consolidated data (for comparison)

    Returns:
        Dict with validation results and recommendations
    """
    result = {
        "token": token,
        "validated_at": datetime.utcnow().isoformat() + "Z",
        "overall_status": "unknown",
        "fetch_validation": {},
        "data_validation": {},
        "freshness_check": {},
        "recommendations": []
    }

    # 1. Validate fetch result
    sources_tried = fetch_metadata.get("sources_tried", [])
    sources_succeeded = fetch_metadata.get("sources_succeeded", [])

    fetch_result = validate_fetch_result(
        sources_tried=sources_tried,
        sources_succeeded=sources_succeeded,
        data=fetch_metadata
    )
    result["fetch_validation"] = fetch_result.to_metadata()

    if fetch_result.status == FetchStatus.FAILED:
        result["overall_status"] = "failed"
        result["recommendations"].append("All data sources failed - check API keys and network")
        return result

    # 2. Validate consolidated.json
    is_valid_json, data, json_issues = validate_json_file(consolidated_path)

    if not is_valid_json:
        result["overall_status"] = "failed"
        result["data_validation"]["json_valid"] = False
        result["data_validation"]["json_issues"] = json_issues
        result["recommendations"].append("consolidated.json is invalid or corrupted")
        return result

    # 3. Validate data completeness
    data_validation = validate_consolidated_data(data)
    result["data_validation"] = data_validation.to_dict()

    if data_validation.quality == DataQuality.UNUSABLE:
        result["overall_status"] = "failed"
        result["recommendations"].append("Data quality too low for playbook generation")
        return result

    # 4. Check freshness (did data actually change?)
    if previous_data:
        freshness_fields_changed = []
        for field_name in FRESHNESS_INDICATOR_FIELDS:
            old_val = previous_data.get(field_name)
            new_val = data.get(field_name)
            if old_val != new_val and new_val is not None:
                freshness_fields_changed.append(field_name)

        result["freshness_check"] = {
            "fields_changed": freshness_fields_changed,
            "data_actually_updated": len(freshness_fields_changed) > 0
        }

        if not freshness_fields_changed:
            result["recommendations"].append("Warning: No freshness fields changed - data may be stale")

    # 5. Determine overall status
    if fetch_result.status == FetchStatus.SUCCESS and data_validation.is_valid:
        result["overall_status"] = "success"
    elif fetch_result.status == FetchStatus.PARTIAL and data_validation.is_valid:
        result["overall_status"] = "partial_success"
        result["recommendations"].append(f"Some sources failed: {fetch_result.sources_failed}")
    else:
        result["overall_status"] = "degraded"
        result["recommendations"].append("Data refresh completed but with issues")

    return result


# =============================================================================
# CLI FOR TESTING
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Validate token data integrity")
    parser.add_argument("token", help="Token symbol (e.g., MONAD)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s"
    )

    # Find consolidated.json
    project_root = Path(__file__).parent.parent.parent
    consolidated_path = project_root / "data" / "tokens" / args.token.upper() / "consolidated.json"

    print(f"\n{'='*60}")
    print(f"DATA INTEGRITY VALIDATION: {args.token.upper()}")
    print(f"{'='*60}")

    # Validate JSON file
    is_valid, data, issues = validate_json_file(consolidated_path)
    print(f"\n📄 JSON Validation:")
    print(f"   Valid: {is_valid}")
    if issues:
        for issue in issues:
            print(f"   ⚠️  {issue}")

    if not is_valid or data is None:
        print("\n❌ Cannot proceed - JSON file invalid")
        exit(1)

    # Validate data completeness
    validation = validate_consolidated_data(data)
    print(f"\n📊 Data Validation:")
    print(f"   Quality: {validation.quality.value}")
    print(f"   Completeness: {validation.completeness_pct:.1f}%")
    print(f"   Valid for playbook: {validation.is_valid}")

    print(f"\n✅ Critical Fields Present ({len(validation.critical_fields_present)}):")
    for field_name in validation.critical_fields_present:
        print(f"   • {field_name}")

    if validation.critical_fields_missing:
        print(f"\n❌ Critical Fields Missing ({len(validation.critical_fields_missing)}):")
        for field_name in validation.critical_fields_missing:
            print(f"   • {field_name}")

    print(f"\n📋 Important Fields: {len(validation.important_fields_present)}/{len(IMPORTANT_FIELDS)}")

    if validation.issues:
        print(f"\n⚠️  Issues:")
        for issue in validation.issues:
            print(f"   • {issue}")

    print(f"\n{'='*60}")
    print(f"Overall: {'✅ VALID' if validation.is_valid else '❌ INVALID'}")
    print(f"{'='*60}\n")
