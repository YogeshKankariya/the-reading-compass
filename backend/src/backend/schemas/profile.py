from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _ensure_tz_aware(dt: datetime) -> datetime:
    """Ensure datetime is timezone-aware and normalized to UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class ExtractedInteractionSignal(BaseModel):
    """Normalized interaction event extracted for profile calculation."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    interaction_id: uuid.UUID
    user_id: uuid.UUID
    work_id: uuid.UUID | None = None
    interaction_type: str
    created_at: datetime
    rating_value: int | None = None
    recommendation_request_id: uuid.UUID | None = None
    recommendation_item_id: uuid.UUID | None = None
    source_record_id: uuid.UUID | None = None
    context: dict[str, Any] | None = None

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, v: datetime) -> datetime:
        return _ensure_tz_aware(v)

    @field_validator("rating_value")
    @classmethod
    def validate_rating_value(cls, v: int | None) -> int | None:
        if v is not None and not (1 <= v <= 5):
            raise ValueError("rating_value must be between 1 and 5")
        return v


class ResolvedWorkSignal(BaseModel):
    """Deduplicated, aggregated signal for a specific work."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    work_id: uuid.UUID
    effective_weight: float
    polarity: Literal["positive", "negative", "neutral"]
    contributing_interaction_ids: list[uuid.UUID] = Field(default_factory=list)
    effective_source: str = "interaction"
    signal_timestamp: datetime
    metadata: dict[str, Any] | None = None

    @field_validator("effective_weight")
    @classmethod
    def validate_effective_weight(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("effective_weight must be a finite number")
        return v

    @field_validator("signal_timestamp")
    @classmethod
    def validate_signal_timestamp(cls, v: datetime) -> datetime:
        return _ensure_tz_aware(v)

    @model_validator(mode="after")
    def validate_polarity_weight_consistency(self) -> ResolvedWorkSignal:
        if self.polarity == "positive" and self.effective_weight <= 0:
            raise ValueError(
                f"Polarity 'positive' requires effective_weight > 0, got {self.effective_weight}"
            )
        if self.polarity == "negative" and self.effective_weight >= 0:
            raise ValueError(
                f"Polarity 'negative' requires effective_weight < 0, got {self.effective_weight}"
            )
        if self.polarity == "neutral" and self.effective_weight < 0:
            raise ValueError(
                f"Polarity 'neutral' requires a non-negative weight (effective_weight >= 0), got {self.effective_weight}"
            )
        return self


class ExplicitPreferenceInput(BaseModel):
    """Normalized explicit user preference input for cold-start and blending."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    preference_type: str
    preference_value: str
    preference_weight: float = 1.0
    source: str = "onboarding"

    @field_validator("preference_weight")
    @classmethod
    def validate_preference_weight(cls, v: float) -> float:
        if not math.isfinite(v) or not (0.0 <= v <= 1.0):
            raise ValueError("preference_weight must be a finite number between 0.0 and 1.0")
        return v


class TopicAffinityEntry(BaseModel):
    """Affinity score and exposure counter for a single genre or subject."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    affinity: float = Field(ge=0.0, le=1.0)
    interaction_count: int = Field(ge=0)
    last_interacted_at: datetime | None = None

    @field_validator("affinity")
    @classmethod
    def validate_affinity(cls, v: float) -> float:
        if not math.isfinite(v) or not (0.0 <= v <= 1.0):
            raise ValueError("affinity must be a finite number between 0.0 and 1.0")
        return v

    @field_validator("last_interacted_at")
    @classmethod
    def validate_last_interacted_at(cls, v: datetime | None) -> datetime | None:
        if v is not None:
            return _ensure_tz_aware(v)
        return None


class InferredTopicsPayload(BaseModel):
    """Structured container for inferred genre, subject, and negative affinities."""

    model_config = ConfigDict(extra="ignore")

    genres: dict[str, TopicAffinityEntry] = Field(default_factory=dict)
    subjects: dict[str, TopicAffinityEntry] = Field(default_factory=dict)
    negative_signals: dict[str, list[str]] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)


class ProfileCalculationResult(BaseModel):
    """Result payload produced by profile vector calculation."""

    model_config = ConfigDict(extra="ignore")

    interest_vector: list[float] | None = None
    inferred_topics: InferredTopicsPayload | dict[str, Any] | None = None
    interaction_count: int = Field(ge=0)
    distinct_positive_works: int = Field(default=0, ge=0)
    model_name: str
    model_revision: str | None = None
    calculated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("interest_vector")
    @classmethod
    def validate_interest_vector(cls, v: list[float] | None) -> list[float] | None:
        if v is not None:
            if len(v) == 0:
                raise ValueError("interest_vector cannot be an empty list when provided")
            for idx, val in enumerate(v):
                if not math.isfinite(val):
                    raise ValueError(f"interest_vector element at index {idx} must be a finite float")
        return v

    @field_validator("calculated_at")
    @classmethod
    def validate_calculated_at(cls, v: datetime) -> datetime:
        return _ensure_tz_aware(v)

    def validate_dimension(self, expected_dim: int) -> None:
        """Validate that the interest vector matches the configured embedding dimension."""
        if self.interest_vector is not None and len(self.interest_vector) != expected_dim:
            raise ValueError(
                f"interest_vector dimension {len(self.interest_vector)} does not match expected {expected_dim}"
            )


class ProfileRefreshResult(BaseModel):
    """Result of evaluating whether a profile refresh was needed and/or executed."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    refresh_required: bool
    refresh_completed: bool
    reason: Literal[
        "initial_profile",
        "threshold_reached",
        "authoritative_state_change",
        "explicit_preference_change",
        "model_revision_mismatch",
        "forced",
        "none",
    ]
    previous_interaction_count: int | None = None
    current_interaction_count: int | None = None
    calculated_at: datetime | None = None

    @field_validator("previous_interaction_count", "current_interaction_count")
    @classmethod
    def validate_counts(cls, v: int | None) -> int | None:
        if v is not None and v < 0:
            raise ValueError("interaction counts cannot be negative")
        return v

    @field_validator("calculated_at")
    @classmethod
    def validate_calculated_at(cls, v: datetime | None) -> datetime | None:
        if v is not None:
            return _ensure_tz_aware(v)
        return None
