import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from backend.core.config import settings
from backend.db.models.author import Author, BookWorkAuthor
from backend.db.models.book import BookEdition, BookWork
from backend.db.models.library import BookRating, UserBook
from backend.db.models.recommendation import RecommendationItem, RecommendationRequest
from backend.db.models.user import User
from backend.schemas.retrieval import (
    CandidateBook,
    CandidateEligibilityInfo,
    CandidateWorkMetadata,
    EligibilityPolicy,
)
from backend.services.retrieval import (
    EXCLUSION_ABANDONED,
    EXCLUSION_COMPLETED,
    EXCLUSION_CURRENTLY_READING,
    EXCLUSION_DIFFICULTY,
    EXCLUSION_DISMISSED,
    EXCLUSION_KNOWN_AUTHOR,
    EXCLUSION_LANGUAGE,
    EXCLUSION_NOT_IN_TBR,
    EXCLUSION_PAGE_COUNT,
    EXCLUSION_PAUSED,
    EXCLUSION_TBR,
    EligibilityFilter,
    EligibilityFilterProtocol,
)

test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)


@asynccontextmanager
async def get_test_session():
    """Yield an isolated AsyncSession inside a rolled-back PostgreSQL transaction."""
    async with test_engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()


def make_candidate(
    work_id: uuid.UUID,
    title: str = "Sample Book",
    original_language: str | None = None,
    difficulty_level: str | None = None,
    metadata_status: str = "unverified",
    edition_page_counts: tuple[int, ...] = (),
) -> CandidateBook:
    return CandidateBook(
        work_id=work_id,
        retrieval_sources=("cold_start_baseline",),
        eligibility=CandidateEligibilityInfo(is_eligible=True),
        work_metadata=CandidateWorkMetadata(
            canonical_title=title,
            original_language=original_language,
            difficulty_level=difficulty_level,
            metadata_status=metadata_status,
            edition_page_counts=edition_page_counts,
        ),
    )


# ==============================================================================
# 1. Unit Tests: Protocol, Empty Pool & Unverified Metadata
# ==============================================================================

@pytest.mark.asyncio
class TestEligibilityFilterUnit:
    """Verify protocol conformance and zero-query empty-pool handling."""

    async def test_implements_eligibility_filter_protocol(self):
        mock_session = AsyncMock(spec=AsyncSession)
        filt = EligibilityFilter(session=mock_session)
        assert isinstance(filt, EligibilityFilterProtocol)

    async def test_empty_candidate_pool_returns_empty_without_db_queries(self):
        mock_session = AsyncMock(spec=AsyncSession)
        filt = EligibilityFilter(session=mock_session)
        batch = await filt.filter_candidates(
            user_id=uuid.uuid4(),
            candidates=(),
            policy=EligibilityPolicy.default_for_request_type("next_read"),
            reference_time=datetime.now(timezone.utc),
        )
        assert batch.eligible == ()
        assert batch.filtered_out == ()
        assert batch.filtered_out_count == 0
        mock_session.execute.assert_not_called()


# ==============================================================================
# 2. PostgreSQL Integration Tests: Lifecycle, TBR, Dismissals & Isolation
# ==============================================================================

@pytest.mark.asyncio
class TestEligibilityFilterLifecycleAndDismissals:
    """Verify UserBook lifecycle states, TBR rules across request types, and 90-day cooldown."""

    async def test_all_request_types_tbr_and_lifecycle_policies(self):
        async with get_test_session() as session:
            filt = EligibilityFilter(session=session)
            user = User(email=f"elig_{uuid.uuid4().hex[:8]}@example.com", display_name="Elig User")
            session.add(user)
            await session.flush()

            w_none = BookWork(canonical_title="Untracked Book", metadata_status="unverified")
            w_tbr = BookWork(canonical_title="TBR Book")
            w_reading = BookWork(canonical_title="Reading Book")
            w_paused = BookWork(canonical_title="Paused Book")
            w_abandoned = BookWork(canonical_title="Abandoned Book")
            w_completed = BookWork(canonical_title="Completed Book")
            session.add_all([w_none, w_tbr, w_reading, w_paused, w_abandoned, w_completed])
            await session.flush()

            session.add_all([
                UserBook(user_id=user.id, work_id=w_tbr.id, status="want_to_read"),
                UserBook(user_id=user.id, work_id=w_reading.id, status="currently_reading"),
                UserBook(user_id=user.id, work_id=w_paused.id, status="paused"),
                UserBook(user_id=user.id, work_id=w_abandoned.id, status="abandoned"),
                UserBook(user_id=user.id, work_id=w_completed.id, status="completed"),
            ])
            await session.flush()

            cands = [
                make_candidate(w.id, title=w.canonical_title, metadata_status=w.metadata_status)
                for w in [w_none, w_tbr, w_reading, w_paused, w_abandoned, w_completed]
            ]
            now = datetime.now(timezone.utc)

            # 1. next_read (and goal_based, mood_based): include untracked + want_to_read; exclude others
            for req_type in ("next_read", "goal_based", "mood_based"):
                batch = await filt.filter_candidates(
                    user_id=user.id,
                    candidates=cands,
                    policy=EligibilityPolicy.default_for_request_type(req_type),
                    reference_time=now,
                )
                assert {c.work_id for c in batch.eligible} == {w_none.id, w_tbr.id}
                excl_map = {c.work_id: c.eligibility for c in batch.filtered_out}
                assert excl_map[w_reading.id].exclusion_reasons == (EXCLUSION_CURRENTLY_READING,)
                assert excl_map[w_paused.id].exclusion_reasons == (EXCLUSION_PAUSED,)
                assert excl_map[w_abandoned.id].exclusion_reasons == (EXCLUSION_ABANDONED,)
                assert excl_map[w_completed.id].exclusion_reasons == (EXCLUSION_COMPLETED,)

            # 2. discover: exclude want_to_read as well
            batch_disc = await filt.filter_candidates(
                user_id=user.id,
                candidates=cands,
                policy=EligibilityPolicy.default_for_request_type("discover"),
                reference_time=now,
            )
            assert {c.work_id for c in batch_disc.eligible} == {w_none.id}
            disc_excl = {c.work_id: c.eligibility for c in batch_disc.filtered_out}
            assert disc_excl[w_tbr.id].exclusion_reasons == (EXCLUSION_TBR,)

            # 3. tbr_priority: strictly ONLY want_to_read eligible
            batch_tbr = await filt.filter_candidates(
                user_id=user.id,
                candidates=cands,
                policy=EligibilityPolicy.default_for_request_type("tbr_priority"),
                reference_time=now,
            )
            assert [c.work_id for c in batch_tbr.eligible] == [w_tbr.id]
            tbr_excl = {c.work_id: c.eligibility for c in batch_tbr.filtered_out}
            assert tbr_excl[w_none.id].exclusion_reasons == (EXCLUSION_NOT_IN_TBR,)
            assert EXCLUSION_NOT_IN_TBR in tbr_excl[w_completed.id].exclusion_reasons
            assert EXCLUSION_COMPLETED in tbr_excl[w_completed.id].exclusion_reasons

            # 4. Rereads / explicit inclusion policy overrides work cleanly
            reread_policy = EligibilityPolicy(
                request_type="next_read",
                include_completed=True,
                include_paused=True,
            )
            batch_reread = await filt.filter_candidates(
                user_id=user.id,
                candidates=cands,
                policy=reread_policy,
                reference_time=now,
            )
            assert {c.work_id for c in batch_reread.eligible} == {
                w_none.id,
                w_tbr.id,
                w_paused.id,
                w_completed.id,
            }

    async def test_dismissal_cooldown_boundaries_feedback_null_fallback_and_non_dismiss(self):
        async with get_test_session() as session:
            filt = EligibilityFilter(session=session)
            user = User(email=f"dism_{uuid.uuid4().hex[:8]}@example.com", display_name="Dism User")
            session.add(user)
            await session.flush()

            ref_time = datetime(2026, 11, 11, 12, 0, 0, tzinfo=timezone.utc)

            w_recent = BookWork(canonical_title="Dismissed 30d ago")
            w_just_inside = BookWork(canonical_title="Dismissed 89d 23h 59m 59s ago")
            w_exact_boundary = BookWork(canonical_title="Dismissed exactly 90d ago")
            w_old = BookWork(canonical_title="Dismissed 120d ago")
            w_null_fb_recent = BookWork(canonical_title="Dismissed feedback_at NULL created_at 10d ago")
            w_null_fb_old = BookWork(canonical_title="Dismissed feedback_at NULL created_at 95d ago")
            w_not_helpful = BookWork(canonical_title="Not helpful feedback (not dismiss)")
            w_helpful = BookWork(canonical_title="Helpful feedback (not dismiss)")
            works = [
                w_recent,
                w_just_inside,
                w_exact_boundary,
                w_old,
                w_null_fb_recent,
                w_null_fb_old,
                w_not_helpful,
                w_helpful,
            ]
            session.add_all(works)
            await session.flush()

            req = RecommendationRequest(
                user_id=user.id,
                request_type="next_read",
                algorithm_version="v1",
                created_at=ref_time - timedelta(days=100),
            )
            session.add(req)
            await session.flush()

            items = [
                RecommendationItem(
                    request_id=req.id, work_id=w_recent.id, rank_position=1, score=Decimal("0.8"),
                    feedback_type="dismiss", feedback_at=ref_time - timedelta(days=30),
                ),
                RecommendationItem(
                    request_id=req.id, work_id=w_just_inside.id, rank_position=2, score=Decimal("0.8"),
                    feedback_type="dismiss", feedback_at=ref_time - timedelta(days=90) + timedelta(seconds=1),
                ),
                RecommendationItem(
                    request_id=req.id, work_id=w_exact_boundary.id, rank_position=3, score=Decimal("0.8"),
                    feedback_type="dismiss", feedback_at=ref_time - timedelta(days=90),
                ),
                RecommendationItem(
                    request_id=req.id, work_id=w_old.id, rank_position=4, score=Decimal("0.8"),
                    feedback_type="dismiss", feedback_at=ref_time - timedelta(days=120),
                ),
                RecommendationItem(
                    request_id=req.id, work_id=w_null_fb_recent.id, rank_position=5, score=Decimal("0.8"),
                    feedback_type="dismiss", feedback_at=None, created_at=ref_time - timedelta(days=10),
                ),
                RecommendationItem(
                    request_id=req.id, work_id=w_null_fb_old.id, rank_position=6, score=Decimal("0.8"),
                    feedback_type="dismiss", feedback_at=None, created_at=ref_time - timedelta(days=95),
                ),
                RecommendationItem(
                    request_id=req.id, work_id=w_not_helpful.id, rank_position=7, score=Decimal("0.8"),
                    feedback_type="not_helpful", feedback_at=ref_time - timedelta(days=5),
                ),
                RecommendationItem(
                    request_id=req.id, work_id=w_helpful.id, rank_position=8, score=Decimal("0.8"),
                    feedback_type="helpful", feedback_at=ref_time - timedelta(days=5),
                ),
            ]
            session.add_all(items)
            await session.flush()

            cands = [make_candidate(w.id, title=w.canonical_title) for w in works]
            batch = await filt.filter_candidates(
                user_id=user.id,
                candidates=cands,
                policy=EligibilityPolicy.default_for_request_type("next_read", dismissal_cooldown_days=90),
                reference_time=ref_time,
            )

            eligible_ids = {c.work_id for c in batch.eligible}
            excluded_ids = {c.work_id for c in batch.filtered_out}

            assert eligible_ids == {
                w_exact_boundary.id,
                w_old.id,
                w_null_fb_old.id,
                w_not_helpful.id,
                w_helpful.id,
            }
            assert excluded_ids == {w_recent.id, w_just_inside.id, w_null_fb_recent.id}
            for exc_cand in batch.filtered_out:
                assert exc_cand.eligibility.exclusion_reasons == (EXCLUSION_DISMISSED,)
                assert exc_cand.eligibility.dismissed_at is not None

            # Override policy ignores cooldown cleanly
            batch_override = await filt.filter_candidates(
                user_id=user.id,
                candidates=cands,
                policy=EligibilityPolicy(request_type="next_read", ignore_dismissal_cooldown=True),
                reference_time=ref_time,
            )
            assert len(batch_override.eligible) == len(works)

    async def test_user_ownership_isolation_between_distinct_users(self):
        async with get_test_session() as session:
            filt = EligibilityFilter(session=session)
            u_a = User(email=f"ua_{uuid.uuid4().hex[:8]}@example.com", display_name="User A")
            u_b = User(email=f"ub_{uuid.uuid4().hex[:8]}@example.com", display_name="User B")
            session.add_all([u_a, u_b])
            await session.flush()

            w = BookWork(canonical_title="Shared Work")
            session.add(w)
            await session.flush()

            # User B completed and dismissed the work
            session.add(UserBook(user_id=u_b.id, work_id=w.id, status="completed"))
            req_b = RecommendationRequest(user_id=u_b.id, request_type="discover", algorithm_version="v1")
            session.add(req_b)
            await session.flush()
            now = datetime.now(timezone.utc)
            session.add(
                RecommendationItem(
                    request_id=req_b.id, work_id=w.id, rank_position=1, score=Decimal("0.9"),
                    feedback_type="dismiss", feedback_at=now - timedelta(days=1),
                )
            )
            await session.flush()

            # For User A, the work MUST remain eligible and show no UserBook status/dismissal from User B
            cand = make_candidate(w.id, title=w.canonical_title)
            batch_a = await filt.filter_candidates(
                user_id=u_a.id,
                candidates=[cand],
                policy=EligibilityPolicy.default_for_request_type("next_read"),
                reference_time=now,
            )
            assert len(batch_a.eligible) == 1
            assert batch_a.eligible[0].eligibility.user_book_status is None
            assert batch_a.eligible[0].eligibility.dismissed_at is None


# ==============================================================================
# 3. PostgreSQL Integration Tests: Language, Editions, Author Novelty & Batched SQL
# ==============================================================================

@pytest.mark.asyncio
class TestEligibilityFilterMetadataAndBatchedSQL:
    """Verify language, multi-edition page counts, unverified metadata, new_author, and no N+1 queries."""

    async def test_language_page_range_multiple_editions_and_missing_metadata(self):
        async with get_test_session() as session:
            filt = EligibilityFilter(session=session)
            uid = uuid.uuid4()
            now = datetime.now(timezone.utc)

            w_en_multi = BookWork(canonical_title="EN Multi Edition", original_language="en", metadata_status="unverified")
            w_en_too_long = BookWork(canonical_title="EN Too Long", original_language="en")
            w_fr = BookWork(canonical_title="FR Book", original_language="fr")
            w_missing_all = BookWork(canonical_title="Missing Lang and Pages", original_language=None, metadata_status="unverified")
            works = [w_en_multi, w_en_too_long, w_fr, w_missing_all]
            session.add_all(works)
            await session.flush()

            # Multiple editions for w_en_multi: [150, 520, NULL]
            session.add_all([
                BookEdition(work_id=w_en_multi.id, page_count=150),
                BookEdition(work_id=w_en_multi.id, page_count=520),
                BookEdition(work_id=w_en_multi.id, page_count=None),
                BookEdition(work_id=w_en_too_long.id, page_count=450),
                BookEdition(work_id=w_fr.id, page_count=200),
                BookEdition(work_id=w_missing_all.id, page_count=None),
            ])
            await session.flush()

            cands = [make_candidate(w.id, title=w.canonical_title, metadata_status=w.metadata_status) for w in works]

            # 1. No language or page filters -> missing metadata and 'unverified' are NOT excluded
            batch_open = await filt.filter_candidates(
                user_id=uid,
                candidates=cands,
                policy=EligibilityPolicy.default_for_request_type("next_read"),
                reference_time=now,
            )
            assert {c.work_id for c in batch_open.eligible} == {w.id for w in works}

            # 2. Explicit language=["en"] & max_pages=300
            policy_filtered = EligibilityPolicy.default_for_request_type(
                "next_read",
                allowed_languages=("EN",),
                min_pages=100,
                max_pages=300,
            )
            batch_filtered = await filt.filter_candidates(
                user_id=uid,
                candidates=cands,
                policy=policy_filtered,
                reference_time=now,
            )
            # w_en_multi qualifies via its 150-page edition!
            assert [c.work_id for c in batch_filtered.eligible] == [w_en_multi.id]
            excl = {c.work_id: c.eligibility.exclusion_reasons for c in batch_filtered.filtered_out}
            assert excl[w_en_too_long.id] == (EXCLUSION_PAGE_COUNT,)
            assert excl[w_fr.id] == (EXCLUSION_LANGUAGE,)
            assert set(excl[w_missing_all.id]) == {EXCLUSION_LANGUAGE, EXCLUSION_PAGE_COUNT}

    async def test_new_author_excludes_authors_from_user_books_and_positive_ratings(self):
        async with get_test_session() as session:
            filt = EligibilityFilter(session=session)
            user = User(email=f"na_{uuid.uuid4().hex[:8]}@example.com", display_name="New Author User")
            session.add(user)
            await session.flush()

            tag = uuid.uuid4().hex[:6]
            a_in_lib = Author(name="Library Author", normalized_name=f"lib author {tag}")
            a_rated_high = Author(name="Liked Author", normalized_name=f"liked author {tag}")
            a_rated_low = Author(name="Disliked Author", normalized_name=f"disliked author {tag}")
            a_fresh = Author(name="Fresh Author", normalized_name=f"fresh author {tag}")
            session.add_all([a_in_lib, a_rated_high, a_rated_low, a_fresh])
            await session.flush()

            w_read = BookWork(canonical_title="Read By Lib Author")
            w_rated5 = BookWork(canonical_title="Rated 5 By Liked Author")
            w_rated2 = BookWork(canonical_title="Rated 2 By Disliked Author")
            w_cand_lib = BookWork(canonical_title="Other By Lib Author")
            w_cand_liked = BookWork(canonical_title="Other By Liked Author")
            w_cand_low_rated = BookWork(canonical_title="Other By Low Rated Author")
            w_cand_fresh = BookWork(canonical_title="Book By Fresh Author")
            session.add_all([w_read, w_rated5, w_rated2, w_cand_lib, w_cand_liked, w_cand_low_rated, w_cand_fresh])
            await session.flush()

            session.add_all([
                BookWorkAuthor(work_id=w_read.id, author_id=a_in_lib.id),
                BookWorkAuthor(work_id=w_cand_lib.id, author_id=a_in_lib.id),
                BookWorkAuthor(work_id=w_rated5.id, author_id=a_rated_high.id),
                BookWorkAuthor(work_id=w_cand_liked.id, author_id=a_rated_high.id),
                BookWorkAuthor(work_id=w_rated2.id, author_id=a_rated_low.id),
                BookWorkAuthor(work_id=w_cand_low_rated.id, author_id=a_rated_low.id),
                BookWorkAuthor(work_id=w_cand_fresh.id, author_id=a_fresh.id),
            ])
            session.add(UserBook(user_id=user.id, work_id=w_read.id, status="want_to_read"))
            session.add(BookRating(user_id=user.id, work_id=w_rated5.id, rating=5))
            session.add(BookRating(user_id=user.id, work_id=w_rated2.id, rating=2))
            await session.flush()

            cands = [
                make_candidate(w.id, title=w.canonical_title)
                for w in [w_cand_lib, w_cand_liked, w_cand_low_rated, w_cand_fresh]
            ]
            batch = await filt.filter_candidates(
                user_id=user.id,
                candidates=cands,
                policy=EligibilityPolicy.default_for_request_type("new_author"),
                reference_time=datetime.now(timezone.utc),
            )
            assert {c.work_id for c in batch.eligible} == {w_cand_low_rated.id, w_cand_fresh.id}
            excl = {c.work_id: c.eligibility.exclusion_reasons for c in batch.filtered_out}
            assert excl[w_cand_lib.id] == (EXCLUSION_KNOWN_AUTHOR,)
            assert excl[w_cand_liked.id] == (EXCLUSION_KNOWN_AUTHOR,)

    async def test_batched_queries_constant_bound_no_n_plus_one(self):
        async with get_test_session() as session:
            filt = EligibilityFilter(session=session)
            user = User(email=f"batch_{uuid.uuid4().hex[:8]}@example.com", display_name="Batch User")
            session.add(user)
            await session.flush()

            works = [BookWork(canonical_title=f"Batch Work {i}", original_language="en") for i in range(6)]
            session.add_all(works)
            await session.flush()
            for w in works:
                session.add(BookEdition(work_id=w.id, page_count=200))
            await session.flush()

            cands = [make_candidate(w.id, title=w.canonical_title) for w in works]
            policy = EligibilityPolicy.default_for_request_type(
                "new_author",
                allowed_languages=("en",),
                max_pages=300,
            )

            executed_sql: list[str] = []

            def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
                executed_sql.append(statement)

            sync_conn = await session.connection()
            event.listen(sync_conn.sync_connection, "before_cursor_execute", before_cursor_execute)
            try:
                batch = await filt.filter_candidates(
                    user_id=user.id,
                    candidates=cands,
                    policy=policy,
                    reference_time=datetime.now(timezone.utc),
                )
            finally:
                event.remove(sync_conn.sync_connection, "before_cursor_execute", before_cursor_execute)

            assert len(batch.eligible) == 6
            # Constant 5 batched queries (UserBook, RecommendationItem, BookWorkAuthor, BookWork, BookEdition) for 6 candidates -> no N+1
            assert len(executed_sql) == 5
