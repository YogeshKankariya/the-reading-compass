from __future__ import annotations

import logging
import math
import uuid
from collections import defaultdict
from typing import Any, Awaitable, Callable, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import Settings, settings as default_settings
from backend.db.models.author import Author, BookWorkAuthor
from backend.db.models.book import BookEdition, BookWork
from backend.db.models.genre import BookWorkGenre
from backend.db.models.recommendation import UserInterestProfile
from backend.db.models.subject import BookWorkSubject
from backend.services.profile.profile_service import ProfileService
from backend.schemas.retrieval import (
    CandidateBook,
    CandidatePoolResult,
    CandidateWorkMetadata,
    EligibilityPolicy,
    RecommendationRequestType,
    RetrievalRequestContext,
    SemanticRetrievalStatus,
)
from backend.services.retrieval.candidate_merger import CandidateMerger
from backend.services.retrieval.eligibility_filter import EligibilityFilter
from backend.services.retrieval.interfaces import (
    CandidateMergerProtocol,
    EligibilityFilterBatch,
    EligibilityFilterProtocol,
    MetadataCandidateHit,
    MetadataRetrievalBatch,
    MetadataRetrieverProtocol,
    SemanticCandidateHit,
    SemanticRetrievalBatch,
    SemanticRetrieverProtocol,
)
from backend.services.retrieval.metadata_retriever import MetadataRetriever
from backend.services.retrieval.semantic_retriever import SemanticRetriever

logger = logging.getLogger(__name__)

WorkMetadataLoader = Callable[
    [Sequence[uuid.UUID]],
    Awaitable[Mapping[uuid.UUID, CandidateWorkMetadata]],
]


def _to_python_uuid(value: Any) -> uuid.UUID:
    """Convert native or asyncpg pgproto UUID values cleanly to stdlib uuid.UUID."""
    if type(value) is uuid.UUID:
        return value
    return uuid.UUID(str(value))


def _next_expanded_limit(current_limit: int, multiplier: float, max_limit: int) -> int:
    """Compute the next monotonically increasing retrieval limit capped at max_limit."""
    if current_limit >= max_limit:
        return max_limit
    scaled = int(math.ceil(current_limit * multiplier))
    return min(max(current_limit + 1, scaled), max_limit)


def _merge_metadata_hit(
    existing: MetadataCandidateHit | None,
    incoming: MetadataCandidateHit,
) -> MetadataCandidateHit:
    """Union provenance and matched taxonomy/author IDs for a metadata candidate across steps."""
    if existing is None:
        return incoming

    sources = tuple(sorted(set(existing.retrieval_sources) | set(incoming.retrieval_sources)))
    genres = tuple(sorted(set(existing.matched_genres) | set(incoming.matched_genres), key=str))
    subjects = tuple(sorted(set(existing.matched_subjects) | set(incoming.matched_subjects), key=str))
    authors = tuple(sorted(set(existing.matched_authors) | set(incoming.matched_authors), key=str))

    return MetadataCandidateHit(
        work_id=existing.work_id,
        retrieval_sources=sources,  # type: ignore[arg-type]
        matched_genres=genres,
        matched_subjects=subjects,
        matched_authors=authors,
    )


class CandidateRetrievalService:
    """End-to-end candidate retrieval orchestrator (Milestone 5.2.5).

    Conforms to CandidateRetrievalServiceProtocol. Coordinates profile loading,
    semantic and metadata retrieval, cumulative work-level merging, independent
    eligibility filtering, and bounded expansion toward CANDIDATE_TARGET_COUNT
    without introducing ranking, scoring, LLM calls, or public HTTP routes.
    """

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
        *,
        profile_service: ProfileService | None = None,
        semantic_retriever: SemanticRetrieverProtocol | None = None,
        metadata_retriever: MetadataRetrieverProtocol | None = None,
        candidate_merger: CandidateMergerProtocol | None = None,
        eligibility_filter: EligibilityFilterProtocol | None = None,
        work_metadata_loader: WorkMetadataLoader | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or default_settings
        self.profile_service = profile_service or ProfileService(
            session=self.session,
            settings=self.settings,
        )
        self.semantic_retriever: SemanticRetrieverProtocol = (
            semantic_retriever
            or SemanticRetriever(session=self.session, settings=self.settings)
        )
        self.metadata_retriever: MetadataRetrieverProtocol = (
            metadata_retriever
            or MetadataRetriever(session=self.session, settings=self.settings)
        )
        self.candidate_merger: CandidateMergerProtocol = (
            candidate_merger or CandidateMerger()
        )
        self.eligibility_filter: EligibilityFilterProtocol = (
            eligibility_filter
            or EligibilityFilter(session=self.session, settings=self.settings)
        )
        self._work_metadata_loader = work_metadata_loader or self._load_work_metadata

    def build_request_context(
        self,
        user_id: uuid.UUID,
        *,
        request_type: RecommendationRequestType = "next_read",
        eligibility_policy: EligibilityPolicy | None = None,
        goal_id: uuid.UUID | None = None,
        language_filters: tuple[str, ...] = (),
        min_pages: int | None = None,
        max_pages: int | None = None,
        difficulty_filters: tuple[str, ...] = (),
    ) -> RetrievalRequestContext:
        """Build a validated RetrievalRequestContext populated with configured defaults."""
        resolved_policy = eligibility_policy or EligibilityPolicy.default_for_request_type(
            request_type,
            dismissal_cooldown_days=self.settings.RETRIEVAL_DISMISSAL_COOLDOWN_DAYS,
            allowed_languages=language_filters,
            min_pages=min_pages,
            max_pages=max_pages,
            allowed_difficulties=difficulty_filters,
        )
        return RetrievalRequestContext(
            user_id=user_id,
            request_type=request_type,
            target_count=self.settings.CANDIDATE_TARGET_COUNT,
            eligibility_policy=resolved_policy,
            goal_id=goal_id,
            language_filters=language_filters,
            min_pages=min_pages,
            max_pages=max_pages,
            difficulty_filters=difficulty_filters,
        )

    async def retrieve_for_user(
        self,
        user_id: uuid.UUID,
        context: RetrievalRequestContext | None = None,
        *,
        request_type: RecommendationRequestType = "next_read",
    ) -> CandidatePoolResult:
        """Validate user ownership and execute candidate retrieval for the authenticated user."""
        if not isinstance(user_id, uuid.UUID):
            raise ValueError(f"user_id must be a valid UUID, got {type(user_id).__name__}")

        resolved_context = context or self.build_request_context(
            user_id=user_id,
            request_type=request_type,
        )
        return await self.retrieve_candidates(
            resolved_context,
            expected_user_id=user_id,
        )

    async def retrieve_candidates(
        self,
        context: RetrievalRequestContext,
        *,
        expected_user_id: uuid.UUID | None = None,
    ) -> CandidatePoolResult:
        """Orchestrate semantic/metadata retrieval, merging, filtering, and bounded expansion."""
        effective_context = self._validate_and_resolve_context(
            context=context,
            expected_user_id=expected_user_id,
        )
        user_id = effective_context.user_id
        policy = effective_context.eligibility_policy
        assert policy is not None
        target_count = effective_context.target_count

        logger.info(
            "Starting candidate retrieval (user_id=%s, request_type=%s, target_count=%d, "
            "semantic_initial_limit=%d, metadata_initial_limit=%d)",
            user_id,
            effective_context.request_type,
            target_count,
            self.settings.RETRIEVAL_SEMANTIC_INITIAL_LIMIT,
            self.settings.RETRIEVAL_METADATA_INITIAL_LIMIT,
        )

        # 1. Load cached UserInterestProfile scoped exclusively to requesting user
        profile = await self._load_user_profile(user_id)
        logger.debug(
            "Loaded user profile state (user_id=%s, has_profile=%s, has_interest_vector=%s)",
            user_id,
            profile is not None,
            profile is not None and profile.interest_vector is not None,
        )

        # 2. Initialize bounded expansion state
        max_limit = self.settings.RETRIEVAL_MAX_EXPANSION_LIMIT
        multiplier = self.settings.RETRIEVAL_EXPANSION_MULTIPLIER
        max_steps = self.settings.RETRIEVAL_MAX_EXPANSION_STEPS

        sem_limit = min(self.settings.RETRIEVAL_SEMANTIC_INITIAL_LIMIT, max_limit)
        meta_limit = min(self.settings.RETRIEVAL_METADATA_INITIAL_LIMIT, max_limit)

        semantic_available = True
        semantic_exhausted = False
        metadata_exhausted = False
        semantic_status: SemanticRetrievalStatus = "skipped"
        used_cold_start_baseline = False

        discovered_semantic: dict[uuid.UUID, SemanticCandidateHit] = {}
        discovered_metadata: dict[uuid.UUID, MetadataCandidateHit] = {}
        work_metadata_cache: dict[uuid.UUID, CandidateWorkMetadata] = {}

        expansion_steps_executed = 0
        last_merged: tuple[CandidateBook, ...] = ()
        last_filter_batch = EligibilityFilterBatch()

        # 3. Initial retrieval (step 0) + bounded expansion steps (1..max_steps)
        for step in range(max_steps + 1):
            run_semantic = step == 0
            run_metadata = step == 0

            if step > 0:
                next_sem_limit: int | None = None
                if semantic_available and not semantic_exhausted and sem_limit < max_limit:
                    candidate_sem_limit = _next_expanded_limit(sem_limit, multiplier, max_limit)
                    if candidate_sem_limit > sem_limit:
                        next_sem_limit = candidate_sem_limit

                next_meta_limit: int | None = None
                if not metadata_exhausted and meta_limit < max_limit:
                    candidate_meta_limit = _next_expanded_limit(meta_limit, multiplier, max_limit)
                    if candidate_meta_limit > meta_limit:
                        next_meta_limit = candidate_meta_limit

                if next_sem_limit is None and next_meta_limit is None:
                    logger.info(
                        "Stopping candidate expansion before step %d: channels exhausted or at max_limit=%d "
                        "(user_id=%s, semantic_available=%s, semantic_exhausted=%s, metadata_exhausted=%s)",
                        step,
                        max_limit,
                        user_id,
                        semantic_available,
                        semantic_exhausted,
                        metadata_exhausted,
                    )
                    break

                if next_sem_limit is not None:
                    sem_limit = next_sem_limit
                    run_semantic = True
                if next_meta_limit is not None:
                    meta_limit = next_meta_limit
                    run_metadata = True
                expansion_steps_executed = step

            # Sequential execution on request-scoped AsyncSession (never concurrent on same session)
            sem_raw_count = 0
            if run_semantic:
                sem_batch: SemanticRetrievalBatch = await self.semantic_retriever.retrieve(
                    profile=profile,
                    limit=sem_limit,
                )
                semantic_status = sem_batch.status
                sem_raw_count = len(sem_batch.hits)
                if sem_batch.status != "executed":
                    semantic_available = False
                else:
                    new_sem_ids = self._accumulate_semantic_hits(discovered_semantic, sem_batch)
                    if sem_raw_count == 0 or (step > 0 and new_sem_ids == 0):
                        semantic_exhausted = True

            meta_raw_count = 0
            if run_metadata:
                meta_batch: MetadataRetrievalBatch = await self.metadata_retriever.retrieve(
                    user_id=user_id,
                    profile=profile,
                    context=effective_context,
                    limit=meta_limit,
                )
                if meta_batch.used_cold_start_baseline:
                    used_cold_start_baseline = True
                meta_raw_count = len(meta_batch.hits)
                new_meta_ids = self._accumulate_metadata_hits(discovered_metadata, meta_batch)
                if meta_raw_count == 0 or meta_raw_count < meta_limit or (step > 0 and new_meta_ids == 0):
                    metadata_exhausted = True

            # Hydrate CandidateWorkMetadata only for newly discovered works
            all_discovered_ids = sorted(
                discovered_semantic.keys() | discovered_metadata.keys(),
                key=str,
            )
            missing_ids = [wid for wid in all_discovered_ids if wid not in work_metadata_cache]
            if missing_ids:
                loaded_metadata = await self._work_metadata_loader(missing_ids)
                work_metadata_cache.update(loaded_metadata)

            # Merge cumulative discoveries and evaluate eligibility independently
            last_merged = self.candidate_merger.merge(
                semantic_hits=tuple(discovered_semantic.values()),
                metadata_hits=tuple(discovered_metadata.values()),
                work_metadata=work_metadata_cache,
            )
            last_filter_batch = await self.eligibility_filter.filter_candidates(
                user_id=user_id,
                candidates=last_merged,
                policy=policy,
                reference_time=effective_context.reference_time,
            )

            eligible_count = len(last_filter_batch.eligible)
            logger.info(
                "Retrieval pass completed (user_id=%s, stage=%s, step=%d, semantic_limit=%s, "
                "metadata_limit=%s, semantic_status=%s, semantic_hits=%d, metadata_hits=%d, "
                "merged_count=%d, eligible_count=%d, filtered_out_count=%d)",
                user_id,
                "initial" if step == 0 else "expansion",
                step,
                sem_limit if run_semantic else None,
                meta_limit if run_metadata else None,
                semantic_status,
                sem_raw_count,
                meta_raw_count,
                len(last_merged),
                eligible_count,
                last_filter_batch.filtered_out_count,
            )

            if eligible_count >= target_count:
                break

            if step == max_steps:
                logger.info(
                    "Reached max expansion steps (%d) for user %s with %d/%d eligible candidates",
                    max_steps,
                    user_id,
                    eligible_count,
                    target_count,
                )

        eligible_candidates = last_filter_batch.eligible
        actual_count = len(eligible_candidates)

        # cold_start_mode indicates whether candidate generation operated in a cold-start regime:
        # 1. Genuine vector cold start: user has no interest vector (semantic_status == "cold_start_no_vector",
        #    which occurs when UserInterestProfile is missing or profile.interest_vector is None).
        #    Note: "revision_unspecified" or "model_mismatch" means a vector exists but was bypassed due to
        #    configuration or model incompatibility; this is not a reader cold-start unless metadata also falls back.
        # 2. Metadata baseline fallback: user possessed zero explicit preferences and zero inferred topics,
        #    forcing metadata retrieval to fall back to the generic catalogue baseline (used_cold_start_baseline is True).
        cold_start_mode = (
            semantic_status == "cold_start_no_vector" or used_cold_start_baseline
        )

        return CandidatePoolResult(
            user_id=user_id,
            request_type=effective_context.request_type,
            candidates=eligible_candidates,
            target_count=target_count,
            actual_count=actual_count,
            target_reached=actual_count >= target_count,
            expansion_steps_executed=expansion_steps_executed,
            semantic_status=semantic_status,
            cold_start_mode=cold_start_mode,
            total_raw_retrieved=len(last_merged),
            filtered_out_count=last_filter_batch.filtered_out_count,
            retrieved_at=effective_context.reference_time,
        )

    def _validate_and_resolve_context(
        self,
        context: RetrievalRequestContext,
        expected_user_id: uuid.UUID | None,
    ) -> RetrievalRequestContext:
        """Validate request context, user identity consistency, and configured policy defaults."""
        if not isinstance(context, RetrievalRequestContext):
            raise ValueError("context must be a RetrievalRequestContext instance")
        if not isinstance(context.user_id, uuid.UUID):
            raise ValueError("context.user_id must be a valid UUID")
        if expected_user_id is not None:
            if not isinstance(expected_user_id, uuid.UUID):
                raise ValueError("expected_user_id must be a valid UUID")
            if context.user_id != expected_user_id:
                raise ValueError(
                    f"context.user_id ({context.user_id}) does not match requested user_id ({expected_user_id})"
                )

        target_count = (
            context.target_count
            if "target_count" in context.model_fields_set
            else self.settings.CANDIDATE_TARGET_COUNT
        )
        policy = context.eligibility_policy
        if policy is None:
            policy = EligibilityPolicy.default_for_request_type(
                context.request_type,
                dismissal_cooldown_days=self.settings.RETRIEVAL_DISMISSAL_COOLDOWN_DAYS,
                allowed_languages=context.language_filters,
                min_pages=context.min_pages,
                max_pages=context.max_pages,
                allowed_difficulties=context.difficulty_filters,
            )
        elif (
            "eligibility_policy" not in context.model_fields_set
            and policy.dismissal_cooldown_days != self.settings.RETRIEVAL_DISMISSAL_COOLDOWN_DAYS
        ):
            policy = policy.model_copy(
                update={"dismissal_cooldown_days": self.settings.RETRIEVAL_DISMISSAL_COOLDOWN_DAYS}
            )

        if policy.request_type != context.request_type:
            raise ValueError(
                f"eligibility_policy.request_type ({policy.request_type}) "
                f"must match context.request_type ({context.request_type})"
            )

        return context.model_copy(
            update={
                "target_count": target_count,
                "eligibility_policy": policy,
            }
        )

    async def _load_user_profile(
        self,
        user_id: uuid.UUID,
    ) -> UserInterestProfile | None:
        """Load the cached interest profile strictly scoped to user_id."""
        try:
            return await self.profile_service.get_profile(user_id)
        except SQLAlchemyError:
            logger.exception(
                "Database error loading UserInterestProfile for user %s",
                user_id,
            )
            raise

    @staticmethod
    def _accumulate_semantic_hits(
        discovered: dict[uuid.UUID, SemanticCandidateHit],
        batch: SemanticRetrievalBatch,
    ) -> int:
        """Accumulate semantic hits across steps, keeping minimum distance per work_id."""
        new_count = 0
        for hit in batch.hits:
            wid = _to_python_uuid(hit.work_id)
            normalized_hit = (
                hit
                if type(hit.work_id) is uuid.UUID
                else SemanticCandidateHit(work_id=wid, semantic_distance=hit.semantic_distance)
            )
            existing = discovered.get(wid)
            if existing is None:
                discovered[wid] = normalized_hit
                new_count += 1
            elif normalized_hit.semantic_distance < existing.semantic_distance:
                discovered[wid] = normalized_hit
        return new_count

    @staticmethod
    def _accumulate_metadata_hits(
        discovered: dict[uuid.UUID, MetadataCandidateHit],
        batch: MetadataRetrievalBatch,
    ) -> int:
        """Accumulate metadata hits across steps while preserving unioned channel provenance."""
        new_count = 0
        for hit in batch.hits:
            wid = _to_python_uuid(hit.work_id)
            normalized_hit = (
                hit
                if type(hit.work_id) is uuid.UUID
                else MetadataCandidateHit(
                    work_id=wid,
                    retrieval_sources=hit.retrieval_sources,
                    matched_genres=tuple(_to_python_uuid(g) for g in hit.matched_genres),
                    matched_subjects=tuple(_to_python_uuid(s) for s in hit.matched_subjects),
                    matched_authors=tuple(_to_python_uuid(a) for a in hit.matched_authors),
                )
            )
            existing = discovered.get(wid)
            if existing is None:
                discovered[wid] = normalized_hit
                new_count += 1
            else:
                discovered[wid] = _merge_metadata_hit(existing, normalized_hit)
        return new_count

    async def _load_work_metadata(
        self,
        work_ids: Sequence[uuid.UUID],
    ) -> dict[uuid.UUID, CandidateWorkMetadata]:
        """Batch-load grounded BookWork, edition, author, genre, and subject metadata (zero N+1)."""
        if not work_ids:
            return {}

        unique_ids = sorted({_to_python_uuid(wid) for wid in work_ids}, key=str)

        works_stmt = select(
            BookWork.id,
            BookWork.canonical_title,
            BookWork.subtitle,
            BookWork.original_language,
            BookWork.first_published_year,
            BookWork.difficulty_level,
            BookWork.estimated_reading_minutes,
            BookWork.cover_image_url,
            BookWork.metadata_status,
        ).where(BookWork.id.in_(unique_ids))

        editions_stmt = select(BookEdition.work_id, BookEdition.page_count).where(
            BookEdition.work_id.in_(unique_ids),
            BookEdition.page_count.is_not(None),
            BookEdition.page_count >= 0,
        )

        authors_stmt = (
            select(BookWorkAuthor.work_id, Author.name)
            .join(Author, BookWorkAuthor.author_id == Author.id)
            .where(BookWorkAuthor.work_id.in_(unique_ids))
            .order_by(
                BookWorkAuthor.work_id.asc(),
                BookWorkAuthor.display_order.asc(),
                Author.name.asc(),
            )
        )

        genres_stmt = select(BookWorkGenre.work_id, BookWorkGenre.genre_id).where(
            BookWorkGenre.work_id.in_(unique_ids)
        )

        subjects_stmt = select(BookWorkSubject.work_id, BookWorkSubject.subject_id).where(
            BookWorkSubject.work_id.in_(unique_ids)
        )

        try:
            works_res = await self.session.execute(works_stmt)
            work_rows = works_res.all()

            if not work_rows:
                return {}

            editions_res = await self.session.execute(editions_stmt)
            authors_res = await self.session.execute(authors_stmt)
            genres_res = await self.session.execute(genres_stmt)
            subjects_res = await self.session.execute(subjects_stmt)
        except SQLAlchemyError:
            logger.exception(
                "Database error loading CandidateWorkMetadata for %d works",
                len(unique_ids),
            )
            raise

        page_counts_map: dict[uuid.UUID, set[int]] = defaultdict(set)
        for row in editions_res.all():
            page_counts_map[_to_python_uuid(row.work_id)].add(int(row.page_count))

        author_names_map: dict[uuid.UUID, list[str]] = defaultdict(list)
        seen_authors_per_work: dict[uuid.UUID, set[str]] = defaultdict(set)
        for row in authors_res.all():
            wid = _to_python_uuid(row.work_id)
            name = str(row.name).strip()
            if name and name not in seen_authors_per_work[wid]:
                seen_authors_per_work[wid].add(name)
                author_names_map[wid].append(name)

        genre_ids_map: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
        for row in genres_res.all():
            genre_ids_map[_to_python_uuid(row.work_id)].add(_to_python_uuid(row.genre_id))

        subject_ids_map: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
        for row in subjects_res.all():
            subject_ids_map[_to_python_uuid(row.work_id)].add(_to_python_uuid(row.subject_id))

        result: dict[uuid.UUID, CandidateWorkMetadata] = {}
        for row in work_rows:
            wid = _to_python_uuid(row.id)
            est_mins = (
                int(row.estimated_reading_minutes)
                if row.estimated_reading_minutes is not None and row.estimated_reading_minutes >= 0
                else None
            )
            result[wid] = CandidateWorkMetadata(
                canonical_title=row.canonical_title,
                subtitle=row.subtitle,
                original_language=row.original_language,
                first_published_year=row.first_published_year,
                difficulty_level=row.difficulty_level,
                estimated_reading_minutes=est_mins,
                cover_image_url=row.cover_image_url,
                metadata_status=row.metadata_status or "unverified",
                edition_page_counts=tuple(sorted(page_counts_map.get(wid, ()))),
                author_names=tuple(author_names_map.get(wid, ())),
                genre_ids=tuple(sorted(genre_ids_map.get(wid, ()), key=str)),
                subject_ids=tuple(sorted(subject_ids_map.get(wid, ()), key=str)),
            )

        return result
