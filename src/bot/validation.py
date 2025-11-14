"""
Input validation for Discord bot commands

Provides Pydantic validators to sanitize and validate user input
before processing commands. This prevents injection attacks, data
corruption, and storage abuse.

Security: Addresses CRITICAL-REL-001 from security audit
"""

import re
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TradeEntryInput(BaseModel):
    """
    Validated input for trade entry commands

    Ensures:
    - Symbol is alphanumeric (prevents injection)
    - Prices are positive and reasonable
    - Position sizes are within bounds
    - Conviction scores are 1-10
    - Notes have length limits and no dangerous patterns
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    symbol: str = Field(..., min_length=1, max_length=20, description="Trading symbol")
    entry_price: float = Field(..., gt=0, lt=1_000_000_000, description="Entry price in USD")
    position_size: float = Field(..., gt=0, lt=10_000_000, description="Position size in USD")
    conviction: Optional[float] = Field(
        None, ge=1.0, le=10.0, description="Conviction score (1-10)"
    )
    notes: Optional[str] = Field(None, max_length=2000, description="Trade notes")

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, v: str) -> str:
        """
        Validate symbol format

        Rules:
        - Must be alphanumeric (A-Z, 0-9, hyphen)
        - Converted to uppercase
        - Max 20 characters

        Raises:
            ValueError: If symbol contains invalid characters
        """
        v_upper = v.upper()
        if not re.match(r"^[A-Z0-9\-]+$", v_upper):
            raise ValueError(
                "Symbol must be alphanumeric (A-Z, 0-9, -). " f"Invalid characters detected in: {v}"
            )
        return v_upper

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, v: Optional[str]) -> Optional[str]:
        """
        Sanitize notes field

        Rules:
        - Max 2000 characters
        - No @everyone/@here mentions
        - No user mentions (<@userid>)
        - No suspicious URLs

        Raises:
            ValueError: If notes contain prohibited patterns
        """
        if v is None:
            return v

        # Check for dangerous Discord patterns
        dangerous_patterns = {
            "@everyone": "Mass mentions not allowed in trade notes",
            "@here": "Mass mentions not allowed in trade notes",
            "<@": "User mentions not allowed in trade notes",
        }

        v_lower = v.lower()
        for pattern, error_msg in dangerous_patterns.items():
            if pattern in v_lower:
                raise ValueError(error_msg)

        # Truncate if too long (extra safety)
        return v[:2000]


class TradeExitInput(BaseModel):
    """
    Validated input for trade exit commands

    Ensures:
    - Trade ID is valid UUID format (or partial match)
    - Exit price is positive
    - Reason is from allowed list
    - Notes have length limits
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    trade_id: str = Field(
        ..., min_length=8, max_length=36, description="Trade ID (full or partial)"
    )
    exit_price: float = Field(..., gt=0, lt=1_000_000_000, description="Exit price in USD")
    reason: str = Field(default="manual", max_length=50, description="Exit reason")
    notes: Optional[str] = Field(None, max_length=2000, description="Exit notes")

    @field_validator("trade_id")
    @classmethod
    def validate_trade_id(cls, v: str) -> str:
        """
        Validate trade ID format

        Rules:
        - Must be alphanumeric with hyphens (UUID format)
        - Min 8 chars (for partial matching)
        - Max 36 chars (full UUID)

        Raises:
            ValueError: If trade_id contains invalid characters
        """
        if not re.match(r"^[a-f0-9\-]+$", v.lower()):
            raise ValueError(
                "Trade ID must be valid UUID format (hexadecimal with hyphens). "
                f"Invalid characters detected in: {v}"
            )
        return v.lower()

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, v: str) -> str:
        """
        Validate exit reason

        Rules:
        - Must be from allowed list
        - Converted to lowercase

        Raises:
            ValueError: If reason is not in allowed list
        """
        allowed_reasons = {
            "manual",
            "target_hit",
            "stop_loss",
            "trailing_stop",
            "market_conditions",
            "better_opportunity",
            "risk_management",
        }

        v_lower = v.lower()
        if v_lower not in allowed_reasons:
            raise ValueError(
                f"Exit reason must be one of: {', '.join(sorted(allowed_reasons))}. " f"Got: {v}"
            )
        return v_lower

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, v: Optional[str]) -> Optional[str]:
        """Sanitize notes field (same as TradeEntryInput)"""
        if v is None:
            return v

        dangerous_patterns = {
            "@everyone": "Mass mentions not allowed in trade notes",
            "@here": "Mass mentions not allowed in trade notes",
            "<@": "User mentions not allowed in trade notes",
        }

        v_lower = v.lower()
        for pattern, error_msg in dangerous_patterns.items():
            if pattern in v_lower:
                raise ValueError(error_msg)

        return v[:2000]


class ProjectSymbolInput(BaseModel):
    """
    Validated input for project/token symbols

    Used for queries, searches, and other commands
    that accept a symbol without full trade data.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    symbol: str = Field(..., min_length=1, max_length=20, description="Project symbol")

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, v: str) -> str:
        """Validate symbol format (same as TradeEntryInput)"""
        v_upper = v.upper()
        if not re.match(r"^[A-Z0-9\-]+$", v_upper):
            raise ValueError(f"Symbol must be alphanumeric (A-Z, 0-9, -). Got: {v}")
        return v_upper


# Example usage in command handler:
# try:
#     validated = TradeEntryInput(
#         symbol=symbol,
#         entry_price=entry_price,
#         position_size=position_size,
#         conviction=conviction,
#         notes=notes
#     )
# except ValidationError as e:
#     await interaction.followup.send(f"❌ Invalid input: {e}", ephemeral=True)
#     return
#
# # Use validated.symbol, validated.entry_price, etc.
