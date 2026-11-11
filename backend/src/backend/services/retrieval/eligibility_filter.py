from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Sequence

from sqlalchemy import select, func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import Settings, settings as default_settings
from backend.db.models.author import BookWorkAuthor
from backend.db.models.book import BookEdition, BookWork
from backend.db.models.library import BookRating, UserBook
from backend.db.models.recommendation import RecommendationItem, RecommendationRequest
from backend.schemas.profile import _ensure_tz_aware
from backend.schemas.retrieval import (
    CandidateBook,
    CandidateEligibilityInfo,
    EligibilityPolicy,
    UserBookLifecycleStatus,
)
from backend.services.retrieval.interfaces import EligibilityFilterBatch

logger = logging.getLogger(__name__)

# Deterministic exclusion reason constants
EXCLUSION_NOT_IN_TBR = "not_in_tbr"
EXCLUSION_COMPLETED = "completed"
EXCLUSION_CURRENTLY_READING = "currently_reading"
EXCLUSION_PAUSED = "paused"
EXCLUSION_ABANDONED = "abandoned"
EXCLUSION_TBR = "excluded_tbr"
EXCLUSION_DISMISSED = "dismissed_within_cooldown"
EXCLUSION_KNOWN_AUTHOR = "known_author"
EXCLUSION_LANGUAGE = "language_mismatch"
EXCLUSION_PAGE_COUNT = "page_count_mismatch"
EXCLUSION_DIFFICULTY = "difficulty_mismatch"


def _to_python_uuid(value: Any) -> uuid.UUID:
    if type(value) is uuid.UUID:
        return value
    return uuid.UUID(str(value))


class EligibilityFilter:
    """Independent candidate eligibility evaluator conforming to EligibilityFilterProtocol (Milestone 5.2.4).

    Evaluates UserBook lifecycle state, TBR policy, 90-day recommendation dismissal cooldown,
    author novelty constraints, original_language, BookEdition page_count, and difficulty
    using batched queries strictly scoped by user_id without modifying ranking or retrieval.
    """

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or default_settings

    async def filter_candidates(
        self,
        user_id: uuid.UUID,
        candidates: Sequence[CandidateBook],
        policy: EligibilityPolicy,
        reference_time: datetime,
    ) -> EligibilityFilterBatch:
        """Evaluate hard eligibility constraints independently across merged candidates."""
        if not candidates:
            return EligibilityFilterBatch(
                eligible=(),
                filtered_out=(),
                filtered_out_count=0,
            )

        ref_time = _ensure_tz_aware(reference_time)
        unique_work_ids = list(dict.fromkeys(c.work_id for c in candidates))

        # 1. Batch-load UserBook lifecycle states (user-scoped)
        user_book_statuses = await self._fetch_user_book_statuses(user_id, unique_work_ids)

        # 2. Batch-load recent dismissal timestamps (user-scoped)
        latest_dismissals: dict[uuid.UUID, datetime] = {}
        if not policy.ignore_dismissal_cooldown:
            latest_dismissals = await self._fetch_latest_dismissals(user_id, unique_work_ids)

        # 3. Batch-load known-author exclusions when request_type requires new authors
        excluded_author_works: set[uuid.UUID] = set()
        if policy.exclude_known_authors:
            excluded_author_works = await self._fetch_author_exclusion_works(
                user_id, unique_work_ids
            )

        # 4. Batch-load BookWork language/difficulty only when explicit filters are requested
        db_work_attrs: dict[uuid.UUID, tuple[str | None, str | None]] = {}
        if policy.allowed_languages or policy.allowed_difficulties:
            db_work_attrs = await self._fetch_work_attributes(unique_work_ids)

        # 5. Batch-load BookEdition page_count only when page range filters are requested
        db_page_counts: dict[uuid.UUID, list[int]] = {}
        has_page_filter = policy.min_pages is not None or policy.max_pages is not None
        if has_page_filter:
            db_page_counts = await self._fetch_edition_page_counts(unique_work_ids)

        allowed_langs = {lang.strip().lower() for lang in policy.allowed_languages}
        allowed_diffs = {diff.strip().lower() for diff in policy.allowed_difficulties}
        cooldown_delta = timedelta(days=policy.dismissal_cooldown_days)

        eligible_list: list[CandidateBook] = []
        filtered_out_list: list[CandidateBook] = []

        for cand in candidates:
            wid = cand.work_id
            reasons: list[str] = []

            # A. UserBook lifecycle & TBR policy
            status = user_book_statuses.get(wid)
            if policy.require_tbr_only and status != "want_to_read":
                reasons.append(EXCLUSION_NOT_IN_TBR)

            if status == "completed" and not policy.include_completed:
                reasons.append(EXCLUSION_COMPLETED)
            elif status == "currently_reading" and not policy.include_in_progress:
                reasons.append(EXCLUSION_CURRENTLY_READING)
            elif status == "paused" and not policy.include_paused:
                reasons.append(EXCLUSION_PAUSED)
            elif status == "abandoned" and not policy.include_abandoned:
                reasons.append(EXCLUSION_ABANDONED)
            elif status == "want_to_read" and not policy.include_tbr:
                reasons.append(EXCLUSION_TBR)

            # B. Dismissal cooldown (prefer feedback_at, fallback to created_at)
            dismissed_at = latest_dismissals.get(wid)
            if dismissed_at is not None and not policy.ignore_dismissal_cooldown:
                elapsed = ref_time - dismissed_at
                if elapsed < cooldown_delta:
                    reasons.append(EXCLUSION_DISMISSED)

            # C. Author novelty (new_author policy)
            if policy.exclude_known_authors and wid in excluded_author_works:
                reasons.append(EXCLUSION_KNOWN_AUTHOR)

            # D. Language filter (BookWork.original_language)
            if allowed_langs:
                db_attrs = db_work_attrs.get(wid)
                orig_lang = db_attrs[0] if db_attrs is not None else cand.work_metadata.original_language
                if not orig_lang or orig_lang.strip().lower() not in allowed_langs:
                    reasons.append(EXCLUSION_LANGUAGE)

            # E. Difficulty filter (BookWork.difficulty_level)
            if allowed_diffs:
                db_attrs = db_work_attrs.get(wid)
                diff_lvl = db_attrs[1] if db_attrs is not None else cand.work_metadata.difficulty_level
                if not diff_lvl or diff_lvl.strip().lower() not in allowed_diffs:
                    reasons.append(EXCLUSION_DIFFICULTY)

            # F. Page count filter (BookEdition.page_count - at least one edition satisfies range)
            if has_page_filter:
                page_counts: Sequence[int] = db_page_counts.get(
                    wid, cand.work_metadata.edition_page_counts
                )
                has_matching_edition = any(
                    (policy.min_pages is None or pc >= policy.min_pages)
                    and (policy.max_pages is None or pc <= policy.max_pages)
                    for pc in page_counts
                )
                if not has_matching_edition:
                    reasons.append(EXCLUSION_PAGE_COUNT)

            # Never exclude solely due to metadata_status == 'unverified'

            is_eligible = len(reasons) == 0
            eligibility_info = CandidateEligibilityInfo(
                is_eligible=is_eligible,
                exclusion_reasons=tuple(reasons),
                user_book_status=status,
                dismissed_at=dismissed_at,
            )
            updated_candidate = cand.model_copy(update={"eligibility": eligibility_info})

            if is_eligible:
                eligible_list.append(updated_candidate)
            else:
                filtered_out_list.append(updated_candidate)

        return EligibilityFilterBatch(
            eligible=tuple(eligible_list),
            filtered_out=tuple(filtered_out_list),
            filtered_out_count=len(filtered_out_list),
        )

    # --------------------------------------------------------------------------
    # Batched Data Access Helpers (Strict User Scoping, Zero N+1)
    # --------------------------------------------------------------------------

    async def _fetch_user_book_statuses(
        self,
        user_id: uuid.UUID,
        work_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, UserBookLifecycleStatus]:
        stmt = select(UserBook.work_id, UserBook.status).where(
            UserBook.user_id == user_id,
            UserBook.work_id.in_(work_ids),
        )
        try:
            res = await self.session.execute(stmt)
            return {
                _to_python_uuid(r.work_id): r.status  # type: ignore[misc]
                for r in res.all()
            }
        except SQLAlchemyError:
            logger.exception(
                "Database error loading UserBook statuses for user %s during eligibility filtering",
                user_id,
            )
            raise

    async def _fetch_latest_dismissals(
        self,
        user_id: uuid.UUID,
        work_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, datetime]:
        dismiss_ts = func.coalesce(
            RecommendationItem.feedback_at,
            RecommendationItem.created_at,
        )
        stmt = (
            select(
                RecommendationItem.work_id,
                func.max(dismiss_ts).label("latest_dismissed_at"),
            )
            .join(
                RecommendationRequest,
                RecommendationItem.request_id == RecommendationRequest.id,
            )
            .where(
                RecommendationRequest.user_id == user_id,
                RecommendationItem.work_id.in_(work_ids),
                RecommendationItem.feedback_type == "dismiss",
            )
            .group_by(RecommendationItem.work_id)
        )
        try:
            res = await self.session.execute(stmt)
            return {
                _to_python_uuid(r.work_id): _ensure_tz_aware(r.latest_dismissed_at)
                for r in res.all()
                if r.latest_dismissed_at is not None
            }
        except SQLAlchemyError:
            logger.exception(
                "Database error loading RecommendationItem dismissals for user %s",
                user_id,
            )
            raise

    async def _fetch_author_exclusion_works(
        self,
        user_id: uuid.UUID,
        work_ids: list[uuid.UUID],
    ) -> set[uuid.UUID]:
        """Return candidate work IDs authored by anyone already in user's library or positive ratings."""
        known_from_books = (
            select(BookWorkAuthor.author_id)
            .join(UserBook, BookWorkAuthor.work_id == UserBook.work_id)
            .where(UserBook.user_id == user_id)
        )
        known_from_ratings = (
            select(BookWorkAuthor.author_id)
            .join(BookRating, BookWorkAuthor.work_id == BookRating.work_id)
            .where(BookRating.user_id == user_id, BookRating.rating >= 4)
        )
        known_authors_subq = known_from_books.union(known_from_ratings).subquery()

        stmt = (
            select(BookWorkAuthor.work_id)
            .join(known_authors_subq, BookWorkAuthor.author_id == known_authors_subq.c.author_id)
            .where(BookWorkAuthor.work_id.in_(work_ids))
            .distinct()
        )
        try:
            res = await self.session.execute(stmt)
            return {_to_python_uuid(wid) for wid in res.scalars().all()}
        except SQLAlchemyError:
            logger.exception(
                "Database error evaluating known-author exclusions for user %s",
                user_id,
            )
            raise

    async def _fetch_work_attributes(
        self,
        work_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, tuple[str | None, str | None]]:
        stmt = select(
            BookWork.id,
            BookWork.original_language,
            BookWork.difficulty_level,
        ).where(BookWork.id.in_(work_ids))
        try:
            res = await self.session.execute(stmt)
            return {
                _to_python_uuid(r.id): (r.original_language, r.difficulty_level)
                for r in res.all()
            }
        except SQLAlchemyError:
            logger.exception("Database error loading BookWork attributes for eligibility filtering")
            raise

    async def _fetch_edition_page_counts(
        self,
        work_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, list[int]]:
        stmt = select(BookEdition.work_id, BookEdition.page_count).where(
            BookEdition.work_id.in_(work_ids),
            BookEdition.page_count.is_not(None),
        )
        try:
            res = await self.session.execute(stmt)
            counts: dict[uuid.UUID, list[int]] = defaultdict(list)
            for row in res.all():
                counts[_to_python_uuid(row.work_id)].append(int(row.page_count))
            return counts
        except SQLAlchemyError:
            logger.exception("Database error loading BookEdition page counts for eligibility filtering")
            raise
