from __future__ import annotations

import logging
import math
import uuid
from typing import Any, Collection

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import Settings, settings as default_settings
from backend.db.models.book import BookWork
from backend.db.models.embedding import BookEmbedding
from backend.db.models.recommendation import UserInterestProfile
from backend.services.profile.vector_builder import l2_norm, validate_vector
from backend.services.retrieval.interfaces import (
    SemanticCandidateHit,
    SemanticRetrievalBatch,
)

logger = logging.getLogger(__name__)

# Small tolerance for pgvector 32-bit float rounding on unit-norm cosine distance [0.0, 2.0].
_COSINE_DISTANCE_ROUNDING_TOLERANCE = 1e-5


def _clamp_cosine_rounding_drift(distance: float) -> float:
    """Clamp tiny pgvector float32 rounding overshoot while keeping domain errors invalid."""
    if -_COSINE_DISTANCE_ROUNDING_TOLERANCE <= distance < 0.0:
        return 0.0
    if 2.0 < distance <= 2.0 + _COSINE_DISTANCE_ROUNDING_TOLERANCE:
        return 2.0
    return distance


class SemanticRetriever:
    """Bounded single-query pgvector semantic candidate retriever (Milestone 5.2.2).

    Retrieves BookWork candidates using exact active embedding compatibility checks and
    cosine distance (<=>) against a user's cached UserInterestProfile.interest_vector.
    Orchestrated expansion across eligibility filtering is owned by Milestone 5.2.5.
    """

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or default_settings

    async def retrieve_for_user(
        self,
        user_id: uuid.UUID,
        limit: int | None = None,
        exclude_work_ids: Collection[uuid.UUID] = (),
    ) -> SemanticRetrievalBatch:
        """Load the user's interest profile and execute semantic candidate retrieval."""
        effective_limit = (
            limit if limit is not None else self.settings.RETRIEVAL_SEMANTIC_INITIAL_LIMIT
        )

        stmt = select(UserInterestProfile).where(UserInterestProfile.user_id == user_id)
        try:
            res = await self.session.execute(stmt)
            profile = res.scalar_one_or_none()
        except SQLAlchemyError:
            logger.exception(
                "Database error loading UserInterestProfile for user %s during semantic retrieval",
                user_id,
            )
            raise

        return await self.retrieve(
            profile=profile,
            limit=effective_limit,
            exclude_work_ids=exclude_work_ids,
        )

    async def retrieve(
        self,
        profile: UserInterestProfile | None,
        limit: int,
        exclude_work_ids: Collection[uuid.UUID] = (),
    ) -> SemanticRetrievalBatch:
        """Retrieve semantically close BookWork candidates for a validated interest profile."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError(f"Semantic retrieval limit must be a positive integer >= 1, got {limit!r}")

        effective_limit = min(limit, self.settings.RETRIEVAL_MAX_EXPANSION_LIMIT)
        target_model = self.settings.PROFILE_EMBEDDING_MODEL_NAME
        raw_rev = self.settings.PROFILE_EMBEDDING_MODEL_REVISION
        target_revision = raw_rev.strip() if raw_rev is not None and raw_rev.strip() else None

        # 1. Cold start: missing profile or null interest_vector
        if profile is None or profile.interest_vector is None:
            return SemanticRetrievalBatch(
                hits=(),
                status="cold_start_no_vector",
                model_name=target_model,
                model_revision=target_revision,
            )

        # 2. Unset configured revision: never run revision-agnostic semantic retrieval
        if target_revision is None:
            return SemanticRetrievalBatch(
                hits=(),
                status="revision_unspecified",
                model_name=target_model,
                model_revision=None,
            )

        # 3. Profile model or revision mismatch
        profile_rev = (
            profile.model_revision.strip()
            if isinstance(profile.model_revision, str) and profile.model_revision.strip()
            else None
        )
        if profile.model_name != target_model or profile_rev != target_revision:
            logger.info(
                "Semantic retrieval skipped for user %s due to model/revision mismatch "
                "(profile=%s:%s, configured=%s:%s)",
                profile.user_id,
                profile.model_name,
                profile.model_revision,
                target_model,
                target_revision,
            )
            return SemanticRetrievalBatch(
                hits=(),
                status="model_mismatch",
                model_name=target_model,
                model_revision=target_revision,
            )

        # 4. Validate profile vector dimensions, numeric types (reject bools), finiteness, and norm
        validated_vec = self._validate_profile_vector(profile)

        # 5. Execute bounded pgvector cosine-distance query against active compatible embeddings
        distance_expr = BookEmbedding.embedding.cosine_distance(validated_vec)
        stmt = (
            select(
                BookEmbedding.work_id,
                distance_expr.label("semantic_distance"),
            )
            .join(BookWork, BookEmbedding.work_id == BookWork.id)
            .where(
                BookEmbedding.is_active.is_(True),
                BookEmbedding.model_name == target_model,
                BookEmbedding.model_revision == target_revision,
            )
        )

        if exclude_work_ids:
            deduped_excludes = sorted(set(exclude_work_ids), key=str)
            stmt = stmt.where(BookEmbedding.work_id.notin_(deduped_excludes))

        stmt = stmt.order_by(distance_expr.asc(), BookEmbedding.work_id.asc()).limit(
            effective_limit
        )

        try:
            res = await self.session.execute(stmt)
            rows = res.all()
        except SQLAlchemyError:
            logger.exception(
                "Database error executing pgvector semantic retrieval for user %s (model=%s, rev=%s)",
                profile.user_id,
                target_model,
                target_revision,
            )
            raise

        hits: list[SemanticCandidateHit] = []
        for row in rows:
            dist = float(row.semantic_distance)
            if not math.isfinite(dist):
                logger.warning(
                    "Skipping non-finite cosine distance returned from pgvector for work %s",
                    row.work_id,
                )
                continue
            clamped_dist = _clamp_cosine_rounding_drift(dist)
            hits.append(
                SemanticCandidateHit(
                    work_id=row.work_id,
                    semantic_distance=clamped_dist,
                )
            )

        return SemanticRetrievalBatch(
            hits=tuple(hits),
            status="executed",
            model_name=target_model,
            model_revision=target_revision,
        )

    def _validate_profile_vector(self, profile: UserInterestProfile) -> list[float]:
        """Validate profile vector dimensionality, component finiteness, and non-zero norm."""
        raw_vec: Any = profile.interest_vector
        if isinstance(raw_vec, (str, bytes, dict)):
            logger.warning(
                "Rejected invalid profile vector type (%s) for user %s",
                type(raw_vec).__name__,
                profile.user_id,
            )
            raise ValueError(
                f"Profile interest_vector must be a numeric sequence, got {type(raw_vec).__name__}"
            )

        try:
            validated = validate_vector(
                raw_vec,
                expected_dim=self.settings.PROFILE_EMBEDDING_DIMENSION,
            )
            norm = l2_norm(validated)
            if norm < self.settings.PROFILE_NEAR_ZERO_EPSILON:
                raise ValueError(
                    f"Profile interest_vector norm ({norm}) is below near-zero epsilon "
                    f"({self.settings.PROFILE_NEAR_ZERO_EPSILON})"
                )
        except ValueError as exc:
            logger.warning(
                "Invalid interest_vector rejected for user %s: %s",
                profile.user_id,
                exc,
            )
            raise

        return validated
