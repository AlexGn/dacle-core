"""
Data Validation Module
======================

Contains data validation implementations:
- data_validators: Cross-field validation for consolidated token data
- tge_validators: TGE-specific validation helpers
- field_validator: Field-level validation with confidence scoring
- ta_snapshot_logger: TA context capture (Session 126)

Migration History:
- Session 126: Added ta_snapshot_logger
- Session 256: Added data_validators, tge_validators, and field_validator from scripts/helpers/

Note: field_validator requires config initialization. Import directly if needed:
    from dacle_core.data.validation.field_validator import FieldValidator
"""

from dacle_core.data.validation.data_validators import DataValidator
from dacle_core.data.validation.tge_validators import (
    extract_unlock_schedule,
    validate_listing_venues,
    detect_execution_blockers,
)

__all__ = [
    "DataValidator",
    "extract_unlock_schedule",
    "validate_listing_venues",
    "detect_execution_blockers",
    "FieldValidator",  # Lazy import due to config dependency
]


def __getattr__(name):
    """Lazy import handler for config-dependent modules."""
    if name == "FieldValidator":
        from dacle_core.data.validation.field_validator import FieldValidator
        return FieldValidator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
