from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.schemas.profile import _ensure_tz_aware

RetrievalSource = Literal[
    "semantic",
    "explicit_genre",
    "explicit_subject",
    "explicit_author",
    "inferred_genre",
    "inferred_subject",
    "cold_start_baseline",
]

RecommendationRequestType = Literal[
    "next_read",
    "discover",
    "tbr_priority",
    "goal_based",
    "mood_based",
    "new_author",
]

SemanticRetrievalStatus = Literal[
    "executed",
    "cold_start_no_vector",
    "revision_unspecified",
    "model_mismatch",
    "skipped",
]

UserBookLifecycleStatus = Literal[
    "want_to_read",
    "currently_reading",
    "completed",
    "paused",
    "abandoned",
]


def _validate_unique_str_tuple(values: Any, field_name: str) -> tuple[str, ...]:
    """Validate string sequence has no blanks or duplicates and return canonical sorted tuple."""
    items = [str(v).strip() for v in values]
    if any(not item for item in items):
        raise ValueError(f"{field_name} cannot contain blank strings")
    if len(items) != len(set(items)):
        raise ValueError(f"{field_name} cannot contain duplicate values")
    return tuple(sorted(items))


def _validate_unique_uuid_tuple(values: Any, field_name: str) -> tuple[uuid.UUID, ...]:
    """Validate UUID sequence has no duplicates and return canonical sorted tuple."""
    items = list(values)
    if len(items) != len(set(items)):
        raise ValueError(f"{field_name} cannot contain duplicate UUIDs")
    return tuple(sorted(items, key=lambda u: str(u)))


class CandidateWorkMetadata(BaseModel):
    """Relevant canonical catalogue metadata grounded in BookWork, BookEdition, Author, Genre, and Subject."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    canonical_title: str
    subtitle: str | None = None
    original_language: str | None = None
    first_published_year: int | None = None
    difficulty_level: str | None = None
    estimated_reading_minutes: int | None = Field(default=None, ge=0)
    cover_image_url: str | None = None
    metadata_status: str = "unverified"
    edition_page_counts: tuple[int, ...] = ()
    author_names: tuple[str, ...] = ()
    genre_ids: tuple[uuid.UUID, ...] = ()
    subject_ids: tuple[uuid.UUID, ...] = ()

    @field_validator("canonical_title")
    @classmethod
    def validate_canonical_title(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("canonical_title cannot be empty or whitespace")
        return stripped

    @field_validator("edition_page_counts", mode="before")
    @classmethod
    def validate_edition_page_counts(cls, v: Any) -> tuple[int, ...]:
        if isinstance(v, (str, bytes)):
            raise ValueError("edition_page_counts must be a sequence of integers")
        cleaned: list[int] = []
        for count in v:
            if isinstance(count, bool) or not isinstance(count, int):
                raise ValueError(f"edition_page_counts elements must be integers, got {type(count).__name__}")
            if count < 0:
                raise ValueError(f"edition_page_counts cannot contain negative values, got {count}")
            cleaned.append(count)
        return tuple(sorted(set(cleaned)))

    @field_validator("genre_ids", "subject_ids")
    @classmethod
    def validate_taxonomy_ids(
        cls, v: tuple[uuid.UUID, ...], info: Any
    ) -> tuple[uuid.UUID, ...]:
        return _validate_unique_uuid_tuple(v, info.field_name)


class CandidateEligibilityInfo(BaseModel):
    """Structured eligibility evaluation result for a retrieved candidate work."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    is_eligible: bool
    exclusion_reasons: tuple[str, ...] = ()
    user_book_status: UserBookLifecycleStatus | None = None
    dismissed_at: datetime | None = None

    @field_validator("exclusion_reasons")
    @classmethod
    def validate_exclusion_reasons(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_unique_str_tuple(v, "exclusion_reasons")

    @field_validator("dismissed_at")
    @classmethod
    def validate_dismissed_at(cls, v: datetime | None) -> datetime | None:
        if v is not None:
            return _ensure_tz_aware(v)
        return None

    @model_validator(mode="after")
    def validate_eligibility_consistency(self) -> CandidateEligibilityInfo:
        if self.is_eligible and self.exclusion_reasons:
            raise ValueError("Eligible candidate cannot have exclusion_reasons")
        if not self.is_eligible and not self.exclusion_reasons:
            raise ValueError("Ineligible candidate must include at least one exclusion_reason")
        return self


class CandidateBook(BaseModel):
    """Normalized candidate work contract preserving full multi-channel retrieval provenance."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    work_id: uuid.UUID
    retrieval_sources: tuple[RetrievalSource, ...] = Field(min_length=1)
    semantic_distance: float | None = None
    matched_genres: tuple[uuid.UUID, ...] = ()
    matched_subjects: tuple[uuid.UUID, ...] = ()
    matched_authors: tuple[uuid.UUID, ...] = ()
    eligibility: CandidateEligibilityInfo
    work_metadata: CandidateWorkMetadata

    @field_validator("retrieval_sources")
    @classmethod
    def validate_retrieval_sources(
        cls, v: tuple[RetrievalSource, ...]
    ) -> tuple[RetrievalSource, ...]:
        if len(v) != len(set(v)):
            raise ValueError("retrieval_sources cannot contain duplicate provenance entries")
        return tuple(sorted(v))  # type: ignore[return-value]

    @field_validator("semantic_distance", mode="before")
    @classmethod
    def reject_bool_semantic_distance(cls, v: Any) -> Any:
        if isinstance(v, bool):
            raise ValueError("semantic_distance cannot be a boolean")
        return v

    @field_validator("semantic_distance")
    @classmethod
    def validate_semantic_distance(cls, v: float | None) -> float | None:
        if v is None:
            return None
        if not math.isfinite(v) or not (0.0 <= v <= 2.0):
            raise ValueError(
                f"semantic_distance must be a finite cosine distance in [0.0, 2.0], got {v}"
            )
        return v

    @field_validator("matched_genres", "matched_subjects", "matched_authors")
    @classmethod
    def validate_matched_ids(
        cls, v: tuple[uuid.UUID, ...], info: Any
    ) -> tuple[uuid.UUID, ...]:
        return _validate_unique_uuid_tuple(v, info.field_name)

    @model_validator(mode="after")
    def validate_provenance_consistency(self) -> CandidateBook:
        sources = set(self.retrieval_sources)

        if "semantic" in sources and self.semantic_distance is None:
            raise ValueError(
                "Candidate retrieved with 'semantic' source must provide semantic_distance"
            )
        if "semantic" not in sources and self.semantic_distance is not None:
            raise ValueError(
                "semantic_distance cannot be set when 'semantic' is not in retrieval_sources"
            )
        if ("explicit_genre" in sources or "inferred_genre" in sources) and not self.matched_genres:
            raise ValueError(
                "Candidate retrieved via genre channel must provide matched_genres"
            )
        if (
            "explicit_subject" in sources or "inferred_subject" in sources
        ) and not self.matched_subjects:
            raise ValueError(
                "Candidate retrieved via subject channel must provide matched_subjects"
            )
        if "explicit_author" in sources and not self.matched_authors:
            raise ValueError(
                "Candidate retrieved via explicit_author channel must provide matched_authors"
            )
        return self


class EligibilityPolicy(BaseModel):
    """Configurable request-specific eligibility and metadata filter rules."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    request_type: RecommendationRequestType
    include_completed: bool = False
    include_in_progress: bool = False
    include_paused: bool = False
    include_abandoned: bool = False
    include_tbr: bool = True
    require_tbr_only: bool = False
    exclude_known_authors: bool = False
    dismissal_cooldown_days: int = Field(default=90, ge=1)
    ignore_dismissal_cooldown: bool = False
    allowed_languages: tuple[str, ...] = ()
    min_pages: int | None = Field(default=None, ge=1)
    max_pages: int | None = Field(default=None, ge=1)
    allowed_difficulties: tuple[str, ...] = ()

    @field_validator("allowed_languages", "allowed_difficulties")
    @classmethod
    def validate_filter_tuples(cls, v: tuple[str, ...], info: Any) -> tuple[str, ...]:
        return _validate_unique_str_tuple(v, info.field_name)

    @model_validator(mode="after")
    def validate_policy_consistency(self) -> EligibilityPolicy:
        if self.request_type == "tbr_priority" and not self.require_tbr_only:
            raise ValueError("EligibilityPolicy for 'tbr_priority' must set require_tbr_only=True")
        if self.require_tbr_only and not self.include_tbr:
            raise ValueError("require_tbr_only=True requires include_tbr=True")
        if self.min_pages is not None and self.max_pages is not None and self.min_pages > self.max_pages:
            raise ValueError(
                f"min_pages ({self.min_pages}) cannot exceed max_pages ({self.max_pages})"
            )
        return self

    @classmethod
    def default_for_request_type(
        cls,
        request_type: RecommendationRequestType,
        *,
        dismissal_cooldown_days: int = 90,
        allowed_languages: tuple[str, ...] = (),
        min_pages: int | None = None,
        max_pages: int | None = None,
        allowed_difficulties: tuple[str, ...] = (),
    ) -> EligibilityPolicy:
        """Build the approved default EligibilityPolicy for a given recommendation request type."""
        include_tbr = request_type != "discover"
        require_tbr_only = request_type == "tbr_priority"
        exclude_known_authors = request_type == "new_author"

        return cls(
            request_type=request_type,
            include_completed=False,
            include_in_progress=False,
            include_paused=False,
            include_abandoned=False,
            include_tbr=include_tbr,
            require_tbr_only=require_tbr_only,
            exclude_known_authors=exclude_known_authors,
            dismissal_cooldown_days=dismissal_cooldown_days,
            ignore_dismissal_cooldown=False,
            allowed_languages=allowed_languages,
            min_pages=min_pages,
            max_pages=max_pages,
            allowed_difficulties=allowed_difficulties,
        )


class RetrievalRequestContext(BaseModel):
    """Validated retrieval input context passed into candidate retrieval orchestration."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    user_id: uuid.UUID
    request_type: RecommendationRequestType = "next_read"
    target_count: int = Field(default=100, ge=1)
    eligibility_policy: EligibilityPolicy | None = None
    goal_id: uuid.UUID | None = None
    language_filters: tuple[str, ...] = ()
    min_pages: int | None = Field(default=None, ge=1)
    max_pages: int | None = Field(default=None, ge=1)
    difficulty_filters: tuple[str, ...] = ()
    reference_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("language_filters", "difficulty_filters")
    @classmethod
    def validate_filters(cls, v: tuple[str, ...], info: Any) -> tuple[str, ...]:
        return _validate_unique_str_tuple(v, info.field_name)

    @field_validator("reference_time")
    @classmethod
    def validate_reference_time(cls, v: datetime) -> datetime:
        return _ensure_tz_aware(v)

    @model_validator(mode="after")
    def validate_and_resolve_policy(self) -> RetrievalRequestContext:
        if self.min_pages is not None and self.max_pages is not None and self.min_pages > self.max_pages:
            raise ValueError(
                f"min_pages ({self.min_pages}) cannot exceed max_pages ({self.max_pages})"
            )

        if self.eligibility_policy is None:
            resolved = EligibilityPolicy.default_for_request_type(
                self.request_type,
                allowed_languages=self.language_filters,
                min_pages=self.min_pages,
                max_pages=self.max_pages,
                allowed_difficulties=self.difficulty_filters,
            )
            object.__setattr__(self, "eligibility_policy", resolved)
        elif self.eligibility_policy.request_type != self.request_type:
            raise ValueError(
                f"eligibility_policy.request_type ({self.eligibility_policy.request_type}) "
                f"must match request_type ({self.request_type})"
            )
        return self


class CandidatePoolResult(BaseModel):
    """Structured output returned by Milestone 5.2 candidate retrieval orchestration."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    user_id: uuid.UUID
    request_type: RecommendationRequestType
    candidates: tuple[CandidateBook, ...] = ()
    target_count: int = Field(ge=1)
    actual_count: int = Field(ge=0)
    target_reached: bool
    expansion_steps_executed: int = Field(default=0, ge=0)
    semantic_status: SemanticRetrievalStatus
    cold_start_mode: bool = False
    total_raw_retrieved: int = Field(default=0, ge=0)
    filtered_out_count: int = Field(default=0, ge=0)
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("retrieved_at")
    @classmethod
    def validate_retrieved_at(cls, v: datetime) -> datetime:
        return _ensure_tz_aware(v)

    @model_validator(mode="after")
    def validate_pool_invariants(self) -> CandidatePoolResult:
        if self.actual_count != len(self.candidates):
            raise ValueError(
                f"actual_count ({self.actual_count}) must match len(candidates) ({len(self.candidates)})"
            )

        seen_work_ids: set[uuid.UUID] = set()
        for candidate in self.candidates:
            if candidate.work_id in seen_work_ids:
                raise ValueError(f"Duplicate work_id {candidate.work_id} in CandidatePoolResult")
            seen_work_ids.add(candidate.work_id)
            if not candidate.eligibility.is_eligible:
                raise ValueError(
                    f"Ineligible candidate {candidate.work_id} cannot appear in eligible CandidatePoolResult"
                )

        expected_reached = self.actual_count >= self.target_count
        if self.target_reached != expected_reached:
            raise ValueError(
                f"target_reached ({self.target_reached}) is inconsistent with "
                f"actual_count={self.actual_count} and target_count={self.target_count}"
            )
        return self
