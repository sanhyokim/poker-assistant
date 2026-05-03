"""Pydantic schemas for LLM pipeline responses.

These models validate the four LLM task outputs used by LLMPipeline while
preserving the existing dictionary return contracts.
"""

from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


class RangeEstimationResponse(BaseModel):
    """Response for range-estimation LLM tasks."""

    range_oop: str = Field(..., description="OOP player range string")
    range_ip: str = Field(..., description="IP player range string")
    adjustments_made: str = Field(default="")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reasoning: str = Field(default="")

    @field_validator("range_oop", "range_ip")
    @classmethod
    def validate_range_string(cls, value: str) -> str:
        """Validate basic range-string shape without full poker parsing."""
        if value == "":
            return value

        valid_chars = set("23456789TJQKAshcdo+-,:. ")
        for part in value.split(","):
            hand_part = part.strip().split(":", 1)[0].strip()
            if not hand_part:
                raise ValueError(f"Empty hand in range: '{value}'")
            if len(hand_part) < 2:
                raise ValueError(f"Invalid hand '{hand_part}' in range: '{value}'")
            if not all(char in valid_chars for char in hand_part):
                raise ValueError(f"Invalid chars in '{hand_part}'")
        return value


class ExploitAdjustmentResponse(BaseModel):
    """Response for exploit-adjustment LLM tasks."""

    model_config = ConfigDict(populate_by_name=True)

    adjusted_action: str = Field(...)
    adjusted_size: Any = Field(
        default=None,
        validation_alias=AliasChoices("adjusted_size", "adjusted_amount"),
    )
    reasoning: str = Field(
        default="",
        validation_alias=AliasChoices("reasoning", "adjustment_reason"),
    )
    confidence: float | str = Field(default="low")

    @field_validator("adjusted_size")
    @classmethod
    def validate_adjusted_size(cls, value: Any) -> Any:
        """Reject negative numeric sizes while allowing null and text sizes."""
        if value is None or value == "":
            return value
        if isinstance(value, bool):
            raise ValueError("Boolean adjusted size is invalid")
        if isinstance(value, (int, float)) and value < 0:
            raise ValueError("Adjusted size must be non-negative")
        if isinstance(value, str):
            try:
                numeric = float(value)
            except ValueError:
                return value
            if numeric < 0:
                raise ValueError("Adjusted size must be non-negative")
        return value

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, value: float | str) -> float | str:
        """Validate numeric confidence or existing high/medium/low labels."""
        if isinstance(value, bool):
            raise ValueError("Boolean confidence is invalid")
        if isinstance(value, (int, float)):
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError("Confidence must be between 0 and 1")
            return float(value)
        normalized = value.lower()
        if normalized not in {"high", "medium", "low"}:
            raise ValueError("Confidence must be high, medium, low, or 0..1")
        return normalized


class MultiwayDecisionResponse(BaseModel):
    """Response for multiway decision LLM tasks."""

    model_config = ConfigDict(populate_by_name=True)

    action: str = Field(...)
    amount: Any = Field(default=0, validation_alias=AliasChoices("amount", "size"))
    reason: str = Field(default="", validation_alias=AliasChoices("reason", "reasoning"))
    confidence: float | str = Field(default="low")

    @field_validator("action")
    @classmethod
    def validate_action(cls, value: str) -> str:
        """Normalize and validate action names."""
        normalized = value.upper()
        if normalized == "ALLIN":
            normalized = "ALL_IN"
        valid_actions = {"FOLD", "CHECK", "CALL", "BET", "RAISE", "ALL_IN"}
        if normalized not in valid_actions:
            raise ValueError(f"Invalid action '{value}'. Must be one of {valid_actions}")
        return normalized

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: Any) -> Any:
        """Reject negative numeric amounts while allowing null and text sizes."""
        if value is None or value == "":
            return value
        if isinstance(value, bool):
            raise ValueError("Boolean amount is invalid")
        if isinstance(value, (int, float)) and value < 0:
            raise ValueError("Amount must be non-negative")
        if isinstance(value, str):
            try:
                numeric = float(value)
            except ValueError:
                return value
            if numeric < 0:
                raise ValueError("Amount must be non-negative")
        return value

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, value: float | str) -> float | str:
        """Validate numeric confidence or existing high/medium/low labels."""
        if isinstance(value, bool):
            raise ValueError("Boolean confidence is invalid")
        if isinstance(value, (int, float)):
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError("Confidence must be between 0 and 1")
            return float(value)
        normalized = value.lower()
        if normalized not in {"high", "medium", "low"}:
            raise ValueError("Confidence must be high, medium, low, or 0..1")
        return normalized


class ReasonGenerationResponse(BaseModel):
    """Response for reason-generation LLM tasks."""

    reason: str = Field(..., min_length=1)


class PreflopDeltaResponse(BaseModel):
    """Response for preflop chart-anchored delta policy."""

    delta_probs: dict[str, float] = Field(
        ...,
        description="Action deltas such as {'raise': 0.05, 'fold': -0.05}",
    )
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reason: str = Field(default="")
