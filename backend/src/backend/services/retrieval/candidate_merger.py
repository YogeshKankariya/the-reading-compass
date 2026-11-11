from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from backend.schemas.retrieval import (
    CandidateBook,
    CandidateEligibilityInfo,
    CandidateWorkMetadata,
    RetrievalSource,
)
from backend.services.retrieval.interfaces import (
    MetadataCandidateHit,
    SemanticCandidateHit,
)

logger = logging.getLogger(__name__)

# Tolerance below which duplicate semantic distances for the same work are considered identical.
_DUPLICATE_DISTANCE_TOLERANCE = 1e-6


@dataclass
class _MergedAccumulator:
    """Internal per-work accumulator used to union provenance and matched IDs across channels."""

    work_id: uuid.UUID
    sources: set[RetrievalSource] = field(default_factory=set)
    semantic_distance: float | None = None
    matched_genres: set[uuid.UUID] = field(default_factory=set)
    matched_subjects: set[uuid.UUID] = field(default_factory=set)
    matched_authors: set[uuid.UUID] = field(default_factory=set)


class CandidateMerger:
    """Deterministic candidate merger conforming to CandidateMergerProtocol (Milestone 5.2.4).

    Deduplicates semantic and metadata hits by canonical BookWork work_id while preserving
    all retrieval provenance, matched taxonomy/author IDs, and semantic distance metadata
    without introducing any scoring, ranking, or source-count prioritization.
    """

    def merge(
        self,
        semantic_hits: Sequence[SemanticCandidateHit],
        metadata_hits: Sequence[MetadataCandidateHit],
        work_metadata: Mapping[uuid.UUID, CandidateWorkMetadata],
        default_eligibility: CandidateEligibilityInfo | None = None,
    ) -> tuple[CandidateBook, ...]:
        """Merge semantic and metadata hits into deduplicated CandidateBook contracts.

        Duplicate SemanticCandidateHit rule:
            If multiple semantic hits carry the same work_id, the minimum semantic_distance
            is retained deterministically; discrepancies exceeding 1e-6 emit a warning log.
        """
        if not semantic_hits and not metadata_hits:
            return ()

        accumulators: dict[uuid.UUID, _MergedAccumulator] = {}

        for sem_hit in semantic_hits:
            acc = accumulators.get(sem_hit.work_id)
            if acc is None:
                acc = _MergedAccumulator(work_id=sem_hit.work_id)
                accumulators[sem_hit.work_id] = acc

            acc.sources.add("semantic")
            if acc.semantic_distance is None:
                acc.semantic_distance = sem_hit.semantic_distance
            else:
                if abs(acc.semantic_distance - sem_hit.semantic_distance) > _DUPLICATE_DISTANCE_TOLERANCE:
                    logger.warning(
                        "Inconsistent duplicate semantic_distance for work %s (%.6f vs %.6f); "
                        "retaining deterministic minimum",
                        sem_hit.work_id,
                        acc.semantic_distance,
                        sem_hit.semantic_distance,
                    )
                acc.semantic_distance = min(acc.semantic_distance, sem_hit.semantic_distance)

        for meta_hit in metadata_hits:
            acc = accumulators.get(meta_hit.work_id)
            if acc is None:
                acc = _MergedAccumulator(work_id=meta_hit.work_id)
                accumulators[meta_hit.work_id] = acc

            acc.sources.update(meta_hit.retrieval_sources)
            acc.matched_genres.update(meta_hit.matched_genres)
            acc.matched_subjects.update(meta_hit.matched_subjects)
            acc.matched_authors.update(meta_hit.matched_authors)

        base_eligibility = (
            default_eligibility
            if default_eligibility is not None
            else CandidateEligibilityInfo(is_eligible=True)
        )

        # Deterministic canonical work_id ordering (never ranks by source count or weights)
        merged: list[CandidateBook] = []
        for wid in sorted(accumulators.keys(), key=str):
            acc = accumulators[wid]
            meta = work_metadata.get(wid)
            if meta is None:
                raise ValueError(
                    f"Missing CandidateWorkMetadata for merged candidate work_id={wid}"
                )

            merged.append(
                CandidateBook(
                    work_id=wid,
                    retrieval_sources=tuple(sorted(acc.sources)),  # type: ignore[arg-type]
                    semantic_distance=acc.semantic_distance,
                    matched_genres=tuple(sorted(acc.matched_genres, key=str)),
                    matched_subjects=tuple(sorted(acc.matched_subjects, key=str)),
                    matched_authors=tuple(sorted(acc.matched_authors, key=str)),
                    eligibility=base_eligibility,
                    work_metadata=meta,
                )
            )

        return tuple(merged)
