from __future__ import annotations

import uuid
from datetime import datetime
from typing import Collection, Mapping, Protocol, Sequence, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from backend.db.models.recommendation import UserInterestProfile
from backend.schemas.retrieval import (
    CandidateBook,
    CandidateEligibilityInfo,
    CandidatePoolResult,
    CandidateWorkMetadata,
    EligibilityPolicy,
    RetrievalRequestContext,
    RetrievalSource,
    SemanticRetrievalStatus,
)


class SemanticCandidateHit(BaseModel):
    """Raw work identifier and cosine distance produced by semantic pgvector retrieval."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    work_id: uuid.UUID
    semantic_distance: float = Field(ge=0.0, le=2.0)


class SemanticRetrievalBatch(BaseModel):
    """Result container returned by SemanticRetriever implementations."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    hits: tuple[SemanticCandidateHit, ...] = ()
    status: SemanticRetrievalStatus
    model_name: str
    model_revision: str | None = None


class MetadataCandidateHit(BaseModel):
    """Raw work identifier and matched taxonomy/author provenance from metadata retrieval."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    work_id: uuid.UUID
    retrieval_sources: tuple[RetrievalSource, ...] = Field(min_length=1)
    matched_genres: tuple[uuid.UUID, ...] = ()
    matched_subjects: tuple[uuid.UUID, ...] = ()
    matched_authors: tuple[uuid.UUID, ...] = ()


class MetadataRetrievalBatch(BaseModel):
    """Result container returned by MetadataRetriever implementations."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    hits: tuple[MetadataCandidateHit, ...] = ()
    used_cold_start_baseline: bool = False


class EligibilityFilterBatch(BaseModel):
    """Separated eligible and excluded candidate pools returned by EligibilityFilterService."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    eligible: tuple[CandidateBook, ...] = ()
    filtered_out: tuple[CandidateBook, ...] = ()
    filtered_out_count: int = Field(default=0, ge=0)


@runtime_checkable
class SemanticRetrieverProtocol(Protocol):
    """Contract for pgvector cosine-distance candidate retrieval (Milestone 5.2.2)."""

    async def retrieve(
        self,
        profile: UserInterestProfile | None,
        limit: int,
        exclude_work_ids: Collection[uuid.UUID] = (),
    ) -> SemanticRetrievalBatch:
        """Retrieve semantically close active embeddings matching pinned model and revision."""
        ...


@runtime_checkable
class MetadataRetrieverProtocol(Protocol):
    """Contract for deterministic explicit, inferred-topic, and cold-start retrieval (Milestone 5.2.3)."""

    async def retrieve(
        self,
        user_id: uuid.UUID,
        profile: UserInterestProfile | None,
        context: RetrievalRequestContext,
        limit: int,
    ) -> MetadataRetrievalBatch:
        """Retrieve candidates via genres, subjects, supported explicit authors, or cold-start fallback."""
        ...


@runtime_checkable
class CandidateMergerProtocol(Protocol):
    """Contract for merging multi-channel hits and deduplicating by work_id (Milestone 5.2.4)."""

    def merge(
        self,
        semantic_hits: Sequence[SemanticCandidateHit],
        metadata_hits: Sequence[MetadataCandidateHit],
        work_metadata: Mapping[uuid.UUID, CandidateWorkMetadata],
        default_eligibility: CandidateEligibilityInfo | None = None,
    ) -> tuple[CandidateBook, ...]:
        """Deduplicate by canonical work_id while preserving all retrieval provenance."""
        ...


@runtime_checkable
class EligibilityFilterProtocol(Protocol):
    """Contract for independent library, dismissal, and catalogue eligibility filtering (Milestone 5.2.4)."""

    async def filter_candidates(
        self,
        user_id: uuid.UUID,
        candidates: Sequence[CandidateBook],
        policy: EligibilityPolicy,
        reference_time: datetime,
    ) -> EligibilityFilterBatch:
        """Apply hard eligibility constraints independently of retrieval scoring."""
        ...


@runtime_checkable
class CandidateRetrievalServiceProtocol(Protocol):
    """Contract for end-to-end candidate retrieval orchestration targeting 100 eligible works (Milestone 5.2.5)."""

    async def retrieve_candidates(
        self,
        context: RetrievalRequestContext,
    ) -> CandidatePoolResult:
        """Orchestrate semantic/metadata channels, merging, eligibility filtering, and bounded expansion."""
        ...
