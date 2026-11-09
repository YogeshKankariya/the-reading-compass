import hashlib
import logging
import math
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.core.config import Settings, settings
from backend.db.models import (
    BookEmbedding,
    BookRating,
    BookWork,
    BookWorkGenre,
    BookWorkSubject,
    Genre,
    ReadingProgress,
    RecommendationItem,
    RecommendationRequest,
    Subject,
    User,
    UserBook,
    UserInteraction,
    UserInterestProfile,
    UserPreference,
)
from backend.schemas.profile import ExplicitPreferenceInput, ProfileRefreshResult
from backend.services.profile.profile_service import (
    ProfileService,
    compute_authoritative_state_fingerprint,
    compute_explicit_preference_hash,
    generate_user_profile_lock_key,
)
from backend.services.profile.profile_tasks import (
    _active_background_refreshes,
    _run_background_profile_refresh,
    schedule_profile_refresh,
)


# ==============================================================================
# Helper Context Manager for Database Session in Tests
# ==============================================================================

from contextlib import asynccontextmanager

test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)


@asynccontextmanager
async def get_test_session():
    """Yield an isolated AsyncSession within a rolled-back transaction."""
    async with test_engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()


def make_dummy_embedding(dim: int = 384, val: float = 0.1) -> list[float]:
    vec = [val] * dim
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec]


# ==============================================================================
# 1. Unit Tests: Preference Hash Calculation
# ==============================================================================

class TestPreferenceHashCalculation:
    """Verify deterministic explicit preference hashing detecting adds, edits, and deletes."""

    def test_deterministic_hash_independent_of_order(self):
        p1 = ExplicitPreferenceInput(preference_type="genre", preference_value="Sci-Fi", preference_weight=0.9, source="onboarding")
        p2 = ExplicitPreferenceInput(preference_type="subject", preference_value="AI", preference_weight=0.8, source="manual")

        hash_forward = compute_explicit_preference_hash([p1, p2])
        hash_backward = compute_explicit_preference_hash([p2, p1])

        assert hash_forward == hash_backward
        assert len(hash_forward) == 64  # SHA-256 hex length

    def test_hash_detects_addition(self):
        p1 = ExplicitPreferenceInput(preference_type="genre", preference_value="Sci-Fi", preference_weight=1.0)
        p2 = ExplicitPreferenceInput(preference_type="genre", preference_value="Fantasy", preference_weight=0.8)

        hash_before = compute_explicit_preference_hash([p1])
        hash_after = compute_explicit_preference_hash([p1, p2])

        assert hash_before != hash_after

    def test_hash_detects_weight_update(self):
        p_orig = ExplicitPreferenceInput(preference_type="genre", preference_value="Sci-Fi", preference_weight=0.800000)
        p_updated = ExplicitPreferenceInput(preference_type="genre", preference_value="Sci-Fi", preference_weight=0.850000)

        assert compute_explicit_preference_hash([p_orig]) != compute_explicit_preference_hash([p_updated])

    def test_hash_detects_deletion(self):
        p1 = ExplicitPreferenceInput(preference_type="genre", preference_value="Sci-Fi", preference_weight=1.0)
        p2 = ExplicitPreferenceInput(preference_type="genre", preference_value="Fantasy", preference_weight=1.0)

        hash_two = compute_explicit_preference_hash([p1, p2])
        hash_one = compute_explicit_preference_hash([p1])

        assert hash_two != hash_one

    def test_empty_preferences_hash_is_deterministic(self):
        empty_hash1 = compute_explicit_preference_hash([])
        empty_hash2 = compute_explicit_preference_hash([])

        assert empty_hash1 == empty_hash2
        assert empty_hash1 == hashlib.sha256(b"").hexdigest()


# ==============================================================================
# 2. Unit Tests: Advisory Lock Key Generation
# ==============================================================================

class TestAdvisoryLockKeyGeneration:
    """Verify deterministic signed 64-bit integer generation for pg_advisory_xact_lock."""

    def test_stable_deterministic_key(self):
        uid = uuid.UUID("12345678-1234-5678-1234-567812345678")
        key1 = generate_user_profile_lock_key(uid)
        key2 = generate_user_profile_lock_key(uid)

        assert key1 == key2
        # Must fit in signed 64-bit bigint (-2^63 <= key <= 2^63 - 1)
        assert -(2**63) <= key1 <= (2**63 - 1)

    def test_different_users_get_different_keys(self):
        u1 = uuid.uuid4()
        u2 = uuid.uuid4()
        assert generate_user_profile_lock_key(u1) != generate_user_profile_lock_key(u2)


# ==============================================================================
# 2b. Authoritative State Fingerprint & Freshness Tests
# ==============================================================================

class TestAuthoritativeStateFingerprint:
    """Verify deterministic calculation and freshness triggers of authoritative state fingerprint."""

    def test_deterministic_ordering_independent_of_input_order(self):
        w1 = uuid.uuid4()
        w2 = uuid.uuid4()
        r1 = BookRating(work_id=w1, rating=5)
        r2 = BookRating(work_id=w2, rating=3)

        ub1 = UserBook(work_id=w1, status="currently_reading")
        ub2 = UserBook(work_id=w2, status="completed")

        rec1 = RecommendationItem(work_id=w1, feedback_type="dislike", feedback_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        rec2 = RecommendationItem(work_id=w2, feedback_type="more_like_this", feedback_at=datetime(2026, 1, 2, tzinfo=timezone.utc))

        fp_a = compute_authoritative_state_fingerprint(
            ratings=[r1, r2],
            user_books=[ub1, ub2],
            rec_items=[rec1, rec2],
        )
        fp_b = compute_authoritative_state_fingerprint(
            ratings=[r2, r1],
            user_books=[ub2, ub1],
            rec_items=[rec2, rec1],
        )
        assert fp_a == fp_b
        assert len(fp_a) == 64

    def test_rating_value_modification_changes_fingerprint(self):
        w = uuid.uuid4()
        r_before = BookRating(work_id=w, rating=5)
        r_after = BookRating(work_id=w, rating=2)

        fp_before = compute_authoritative_state_fingerprint([r_before], [], [])
        fp_after = compute_authoritative_state_fingerprint([r_after], [], [])
        assert fp_before != fp_after

    def test_rating_deletion_changes_fingerprint(self):
        w1 = uuid.uuid4()
        w2 = uuid.uuid4()
        r1 = BookRating(work_id=w1, rating=4)
        r2 = BookRating(work_id=w2, rating=3)

        fp_with_both = compute_authoritative_state_fingerprint([r1, r2], [], [])
        fp_after_deletion = compute_authoritative_state_fingerprint([r1], [], [])
        assert fp_with_both != fp_after_deletion

    def test_user_book_status_modification_changes_fingerprint(self):
        w = uuid.uuid4()
        ub_cr = UserBook(work_id=w, status="currently_reading")
        ub_done = UserBook(work_id=w, status="completed")

        fp_cr = compute_authoritative_state_fingerprint([], [ub_cr], [])
        fp_done = compute_authoritative_state_fingerprint([], [ub_done], [])
        assert fp_cr != fp_done

    def test_reading_progress_change_changes_fingerprint(self):
        w = uuid.uuid4()
        ub1 = UserBook(work_id=w, status="currently_reading")
        ub1.progress = ReadingProgress(progress_percent=Decimal("45.00"), last_read_at=datetime(2026, 1, 1, tzinfo=timezone.utc))

        ub2 = UserBook(work_id=w, status="currently_reading")
        ub2.progress = ReadingProgress(progress_percent=Decimal("75.00"), last_read_at=datetime(2026, 1, 2, tzinfo=timezone.utc))

        fp1 = compute_authoritative_state_fingerprint([], [ub1], [])
        fp2 = compute_authoritative_state_fingerprint([], [ub2], [])
        assert fp1 != fp2

    def test_recommendation_feedback_changes_fingerprint(self):
        w = uuid.uuid4()
        rec_none = RecommendationItem(work_id=w, feedback_type=None, feedback_at=None)
        rec_dislike = RecommendationItem(work_id=w, feedback_type="dislike", feedback_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        rec_save = RecommendationItem(work_id=w, feedback_type="save_to_tbr", feedback_at=datetime(2026, 1, 1, tzinfo=timezone.utc))

        fp_none = compute_authoritative_state_fingerprint([], [], [rec_none])
        fp_dislike = compute_authoritative_state_fingerprint([], [], [rec_dislike])
        fp_save = compute_authoritative_state_fingerprint([], [], [rec_save])

        assert fp_none != fp_dislike
        assert fp_dislike != fp_save


# ==============================================================================
# 3. Integration Tests: Profile Freshness & Eligibility
# ==============================================================================

@pytest.mark.asyncio
class TestProfileFreshnessEvaluation:
    """Verify all 7 trigger conditions of should_refresh_profile."""

    async def test_initial_profile_when_missing(self):
        async with get_test_session() as session:
            user = User(email=f"fresh_{uuid.uuid4().hex[:8]}@example.com", display_name="Fresh User")
            session.add(user)
            await session.flush()

            service = ProfileService(session=session)
            needed, reason = await service.should_refresh_profile(user.id)

            assert needed is True
            assert reason == "initial_profile"

    async def test_forced_refresh_overrides_everything(self):
        async with get_test_session() as session:
            user = User(email=f"forced_{uuid.uuid4().hex[:8]}@example.com", display_name="Forced User")
            session.add(user)
            await session.flush()

            service = ProfileService(session=session)
            needed, reason = await service.should_refresh_profile(user.id, force=True)

            assert needed is True
            assert reason == "forced"

    async def test_threshold_reached_triggers_refresh(self):
        async with get_test_session() as session:
            user = User(email=f"thresh_{uuid.uuid4().hex[:8]}@example.com", display_name="Threshold User")
            session.add(user)
            await session.flush()

            now = datetime.now(timezone.utc)
            # Add 5 eligible interactions (threshold is 5)
            works = [BookWork(canonical_title=f"Work {i}") for i in range(5)]
            session.add_all(works)
            await session.flush()

            ratings = []
            for w in works:
                r = BookRating(user_id=user.id, work_id=w.id, rating=4, created_at=now, updated_at=now)
                session.add(r)
                ratings.append(r)
                session.add(UserInteraction(user_id=user.id, work_id=w.id, interaction_type="rate", rating_value=4, created_at=now))
            await session.flush()

            state_fp = compute_authoritative_state_fingerprint(ratings=ratings, user_books=[], rec_items=[])

            # Existing profile with watermark 0 and state fingerprint matching current ratings
            profile = UserInterestProfile(
                user_id=user.id,
                model_name=settings.PROFILE_EMBEDDING_MODEL_NAME,
                model_revision=settings.PROFILE_EMBEDDING_MODEL_REVISION or "unset",
                interaction_count=0,
                last_calculated_at=now,
                inferred_topics={
                    "meta": {
                        "explicit_preference_hash": compute_explicit_preference_hash([]),
                        "authoritative_state_fingerprint": state_fp,
                    }
                },
            )
            session.add(profile)
            await session.flush()

            service = ProfileService(session=session)
            needed, reason = await service.should_refresh_profile(user.id, profile=profile)

            assert needed is True
            assert reason == "threshold_reached"

    async def test_below_threshold_returns_none(self):
        async with get_test_session() as session:
            user = User(email=f"below_{uuid.uuid4().hex[:8]}@example.com", display_name="Below User")
            session.add(user)
            await session.flush()

            now = datetime.now(timezone.utc)

            # Add 4 interactions (below threshold of 5)
            works = [BookWork(canonical_title=f"Work {i}") for i in range(4)]
            session.add_all(works)
            await session.flush()

            ratings = []
            for w in works:
                r = BookRating(user_id=user.id, work_id=w.id, rating=4, created_at=now, updated_at=now)
                session.add(r)
                ratings.append(r)
                session.add(UserInteraction(user_id=user.id, work_id=w.id, interaction_type="rate", rating_value=4, created_at=now))
            await session.flush()

            state_fp = compute_authoritative_state_fingerprint(ratings=ratings, user_books=[], rec_items=[])
            profile = UserInterestProfile(
                user_id=user.id,
                model_name=settings.PROFILE_EMBEDDING_MODEL_NAME,
                model_revision=settings.PROFILE_EMBEDDING_MODEL_REVISION or "unset",
                interaction_count=0,
                last_calculated_at=now,
                inferred_topics={
                    "meta": {
                        "explicit_preference_hash": compute_explicit_preference_hash([]),
                        "authoritative_state_fingerprint": state_fp,
                    }
                },
            )
            session.add(profile)
            await session.flush()

            service = ProfileService(session=session)
            needed, reason = await service.should_refresh_profile(user.id, profile=profile)

            assert needed is False
            assert reason == "none"

    async def test_preference_addition_triggers_refresh(self):
        async with get_test_session() as session:
            user = User(email=f"prefadd_{uuid.uuid4().hex[:8]}@example.com", display_name="Pref Add")
            session.add(user)
            await session.flush()

            now = datetime.now(timezone.utc)
            empty_state_fp = compute_authoritative_state_fingerprint([], [], [])
            # Profile stored with hash of empty preferences
            profile = UserInterestProfile(
                user_id=user.id,
                model_name=settings.PROFILE_EMBEDDING_MODEL_NAME,
                model_revision=settings.PROFILE_EMBEDDING_MODEL_REVISION or "unset",
                interaction_count=0,
                last_calculated_at=now,
                inferred_topics={
                    "meta": {
                        "explicit_preference_hash": compute_explicit_preference_hash([]),
                        "authoritative_state_fingerprint": empty_state_fp,
                    }
                },
            )
            session.add(profile)
            await session.flush()

            # Add explicit preference
            pref = UserPreference(user_id=user.id, preference_type="genre", preference_value="Sci-Fi", preference_weight=Decimal("1.000"))
            session.add(pref)
            await session.flush()

            service = ProfileService(session=session)
            needed, reason = await service.should_refresh_profile(user.id, profile=profile)

            assert needed is True
            assert reason == "explicit_preference_change"

    async def test_preference_deletion_triggers_refresh(self):
        async with get_test_session() as session:
            user = User(email=f"prefdel_{uuid.uuid4().hex[:8]}@example.com", display_name="Pref Del")
            session.add(user)
            await session.flush()

            now = datetime.now(timezone.utc)
            empty_state_fp = compute_authoritative_state_fingerprint([], [], [])
            # Profile stored with hash of ONE preference
            dummy_pref = ExplicitPreferenceInput(preference_type="genre", preference_value="Sci-Fi", preference_weight=1.0)
            pref_hash = compute_explicit_preference_hash([dummy_pref])

            profile = UserInterestProfile(
                user_id=user.id,
                model_name=settings.PROFILE_EMBEDDING_MODEL_NAME,
                model_revision=settings.PROFILE_EMBEDDING_MODEL_REVISION or "unset",
                interaction_count=0,
                last_calculated_at=now,
                inferred_topics={
                    "meta": {
                        "explicit_preference_hash": pref_hash,
                        "authoritative_state_fingerprint": empty_state_fp,
                    }
                },
            )
            session.add(profile)
            await session.flush()

            # No actual UserPreference rows in DB (simulating deletion)
            service = ProfileService(session=session)
            needed, reason = await service.should_refresh_profile(user.id, profile=profile)

            assert needed is True
            assert reason == "explicit_preference_change"

    async def test_model_revision_mismatch_triggers_refresh(self):
        async with get_test_session() as session:
            user = User(email=f"rev_{uuid.uuid4().hex[:8]}@example.com", display_name="Rev User")
            session.add(user)
            await session.flush()

            profile = UserInterestProfile(
                user_id=user.id,
                model_name=settings.PROFILE_EMBEDDING_MODEL_NAME,
                model_revision="old-revision-v0",  # mismatched revision
                interaction_count=0,
                last_calculated_at=datetime.now(timezone.utc),
                inferred_topics={"meta": {"explicit_preference_hash": compute_explicit_preference_hash([])}},
            )
            session.add(profile)
            await session.flush()

            service = ProfileService(session=session)
            needed, reason = await service.should_refresh_profile(user.id, profile=profile)

            assert needed is True
            assert reason == "model_revision_mismatch"

    async def test_rating_value_modification_triggers_authoritative_state_change(self):
        async with get_test_session() as session:
            user = User(email=f"ratechg_{uuid.uuid4().hex[:8]}@example.com", display_name="Rate Change")
            session.add(user)
            await session.flush()

            service = ProfileService(session=session)
            work = BookWork(canonical_title="Rating Mod Work")
            session.add(work)
            await session.flush()

            rating = BookRating(user_id=user.id, work_id=work.id, rating=5)
            session.add(rating)
            await session.flush()

            # Refresh profile so fingerprint is recorded
            res = await service.refresh_profile(user.id)
            assert res.refresh_completed is True

            # Verify it is fresh right now
            needed, reason = await service.should_refresh_profile(user.id)
            assert needed is False
            assert reason == "none"

            # Mutate rating from 5 to 2 without adding any new interactions
            rating.rating = 2
            await session.flush()

            # should_refresh_profile must detect authoritative_state_change
            needed, reason = await service.should_refresh_profile(user.id)
            assert needed is True
            assert reason == "authoritative_state_change"

    async def test_rating_deletion_triggers_authoritative_state_change(self):
        async with get_test_session() as session:
            user = User(email=f"ratedel_{uuid.uuid4().hex[:8]}@example.com", display_name="Rate Del")
            session.add(user)
            await session.flush()

            service = ProfileService(session=session)
            work = BookWork(canonical_title="Rating Del Work")
            session.add(work)
            await session.flush()

            rating = BookRating(user_id=user.id, work_id=work.id, rating=5)
            session.add(rating)
            await session.flush()

            await service.refresh_profile(user.id)

            needed, reason = await service.should_refresh_profile(user.id)
            assert needed is False

            # Delete the rating without adding any new interactions
            await session.delete(rating)
            await session.flush()

            needed, reason = await service.should_refresh_profile(user.id)
            assert needed is True
            assert reason == "authoritative_state_change"

    async def test_user_book_status_change_triggers_authoritative_state_change(self):
        async with get_test_session() as session:
            user = User(email=f"ubchg_{uuid.uuid4().hex[:8]}@example.com", display_name="UB Change")
            session.add(user)
            await session.flush()

            service = ProfileService(session=session)
            work = BookWork(canonical_title="UB Lifecycle Work")
            session.add(work)
            await session.flush()

            ub = UserBook(user_id=user.id, work_id=work.id, status="currently_reading")
            session.add(ub)
            await session.flush()

            await service.refresh_profile(user.id)

            needed, reason = await service.should_refresh_profile(user.id)
            assert needed is False

            # Change status to completed without adding new interactions
            ub.status = "completed"
            await session.flush()

            needed, reason = await service.should_refresh_profile(user.id)
            assert needed is True
            assert reason == "authoritative_state_change"

    async def test_reading_progress_change_triggers_authoritative_state_change(self):
        async with get_test_session() as session:
            user = User(email=f"progchg_{uuid.uuid4().hex[:8]}@example.com", display_name="Prog Change")
            session.add(user)
            await session.flush()

            service = ProfileService(session=session)
            work = BookWork(canonical_title="Prog Work")
            session.add(work)
            await session.flush()

            now = datetime.now(timezone.utc)
            ub = UserBook(user_id=user.id, work_id=work.id, status="currently_reading")
            session.add(ub)
            await session.flush()

            prog = ReadingProgress(user_book_id=ub.id, progress_percent=Decimal("20.00"), last_read_at=now)
            session.add(prog)
            await session.flush()

            await service.refresh_profile(user.id)

            needed, reason = await service.should_refresh_profile(user.id)
            assert needed is False

            # Advance progress to 80% without adding new interactions
            prog.progress_percent = Decimal("80.00")
            prog.last_read_at = now + timedelta(days=1)
            await session.flush()

            needed, reason = await service.should_refresh_profile(user.id)
            assert needed is True
            assert reason == "authoritative_state_change"

    async def test_recommendation_feedback_change_triggers_authoritative_state_change(self):
        async with get_test_session() as session:
            user = User(email=f"recfb_{uuid.uuid4().hex[:8]}@example.com", display_name="Rec FB")
            session.add(user)
            await session.flush()

            service = ProfileService(session=session)
            work = BookWork(canonical_title="Rec Item Work")
            session.add(work)
            await session.flush()

            rec_req = RecommendationRequest(user_id=user.id, request_type="discover", algorithm_version="1.0.0")
            session.add(rec_req)
            await session.flush()

            rec_item = RecommendationItem(
                request_id=rec_req.id,
                work_id=work.id,
                rank_position=1,
                score=Decimal("0.850000"),
            )
            session.add(rec_item)
            await session.flush()

            await service.refresh_profile(user.id)

            needed, reason = await service.should_refresh_profile(user.id)
            assert needed is False

            # Add feedback without adding new interactions
            now = datetime.now(timezone.utc)
            rec_item.feedback_type = "dismiss"
            rec_item.feedback_at = now
            await session.flush()

            needed, reason = await service.should_refresh_profile(user.id)
            assert needed is True
            assert reason == "authoritative_state_change"


# ==============================================================================
# 4. End-to-End Orchestration & Persistence Tests
# ==============================================================================

@pytest.mark.asyncio
class TestProfileOrchestrationExecution:
    """Verify end-to-end profile calculation and atomic persistence."""

    async def test_initial_profile_creation_cold_start_empty(self):
        async with get_test_session() as session:
            user = User(email=f"cold_{uuid.uuid4().hex[:8]}@example.com", display_name="Cold User")
            session.add(user)
            await session.flush()

            service = ProfileService(session=session)
            result = await service.refresh_profile(user.id)

            assert result.refresh_required is True
            assert result.refresh_completed is True
            assert result.reason == "initial_profile"
            assert result.previous_interaction_count is None
            assert result.current_interaction_count == 0

            # Verify persisted database row
            profile = await service.get_profile(user.id)
            assert profile is not None
            assert profile.user_id == user.id
            assert profile.interest_vector is None
            assert profile.interaction_count == 0
            assert profile.inferred_topics is not None
            assert profile.inferred_topics["genres"] == {}
            assert profile.inferred_topics["subjects"] == {}

    async def test_explicit_only_profile_creation(self):
        async with get_test_session() as session:
            user = User(email=f"exp_{uuid.uuid4().hex[:8]}@example.com", display_name="Explicit User")
            session.add(user)
            await session.flush()

            # Add explicit preferences (no interactions)
            p1 = UserPreference(user_id=user.id, preference_type="genre", preference_value="Science Fiction", preference_weight=Decimal("1.000"))
            p2 = UserPreference(user_id=user.id, preference_type="genre", preference_value="Fantasy", preference_weight=Decimal("0.800"))
            session.add_all([p1, p2])
            await session.flush()

            service = ProfileService(session=session)
            result = await service.refresh_profile(user.id)

            assert result.refresh_completed is True
            profile = await service.get_profile(user.id)
            assert profile is not None
            # No book embeddings available, so interest_vector is None
            assert profile.interest_vector is None
            assert profile.inferred_topics["meta"]["explicit_preference_count"] == 2

    async def test_blended_profile_with_interactions_and_embeddings(self):
        async with get_test_session() as session:
            user = User(email=f"blend_{uuid.uuid4().hex[:8]}@example.com", display_name="Blended User")
            work = BookWork(canonical_title="Hyperion")
            session.add_all([user, work])
            await session.flush()

            # Add Genre and Subject associations
            genre = Genre(name=f"SciFi_{uuid.uuid4().hex[:6]}", slug=f"scifi-{uuid.uuid4().hex[:6]}")
            subject = Subject(name=f"Space_{uuid.uuid4().hex[:6]}", slug=f"space-{uuid.uuid4().hex[:6]}")
            session.add_all([genre, subject])
            await session.flush()

            session.add(BookWorkGenre(work_id=work.id, genre_id=genre.id))
            session.add(BookWorkSubject(work_id=work.id, subject_id=subject.id, relevance_weight=Decimal("0.900")))

            # Add 5-star rating interaction
            now = datetime.now(timezone.utc)
            session.add(BookRating(user_id=user.id, work_id=work.id, rating=5, created_at=now, updated_at=now))
            session.add(UserInteraction(user_id=user.id, work_id=work.id, interaction_type="rate", rating_value=5, created_at=now))

            # Add Active Compatible Embedding
            emb_vec = make_dummy_embedding(384, 0.2)
            active_emb = BookEmbedding(
                work_id=work.id,
                model_name=settings.PROFILE_EMBEDDING_MODEL_NAME,
                model_revision="test-revision-1",
                embedding=emb_vec,
                source_text_hash="h1",
                is_active=True,
            )
            session.add(active_emb)
            await session.flush()

            # Configure revision
            custom_settings = Settings(
                DATABASE_URL=settings.DATABASE_URL,
                PROFILE_EMBEDDING_MODEL_REVISION="test-revision-1",
            )
            service = ProfileService(session=session, settings=custom_settings)
            result = await service.refresh_profile(user.id)

            assert result.refresh_completed is True
            assert result.current_interaction_count == 1

            profile = await service.get_profile(user.id)
            assert profile is not None
            assert profile.interest_vector is not None
            assert len(profile.interest_vector) == 384
            # Genre and Subject affinity should be present
            assert str(genre.id) in profile.inferred_topics["genres"]
            assert str(subject.id) in profile.inferred_topics["subjects"]

    async def test_atomic_update_preserves_id_and_created_at(self):
        async with get_test_session() as session:
            user = User(email=f"atomic_{uuid.uuid4().hex[:8]}@example.com", display_name="Atomic User")
            session.add(user)
            await session.flush()

            service = ProfileService(session=session)
            # 1. Initial creation
            res1 = await service.refresh_profile(user.id)
            assert res1.reason == "initial_profile"

            profile1 = await service.get_profile(user.id)
            original_id = profile1.id
            original_created_at = profile1.created_at

            # 2. Second forced refresh
            res2 = await service.refresh_profile(user.id, force=True)
            assert res2.reason == "forced"

            profile2 = await service.get_profile(user.id)
            assert profile2.id == original_id
            assert profile2.created_at == original_created_at
            assert profile2.updated_at >= original_created_at

    async def test_failed_calculation_preserves_previous_profile(self):
        async with get_test_session() as session:
            user = User(email=f"fail_{uuid.uuid4().hex[:8]}@example.com", display_name="Fail User")
            session.add(user)
            await session.flush()

            service = ProfileService(session=session)
            # Initial valid profile
            await service.refresh_profile(user.id)
            profile_before = await service.get_profile(user.id)
            assert profile_before is not None
            calc_time_before = profile_before.last_calculated_at

            # Induce failure during calculation
            with patch.object(service.signal_extractor, "extract_profile_signals", side_effect=RuntimeError("Simulated DB error")):
                with pytest.raises(RuntimeError, match="Simulated DB error"):
                    await service.refresh_profile(user.id, force=True)

            # Previous profile must remain intact
            profile_after = await service.get_profile(user.id)
            assert profile_after.last_calculated_at == calc_time_before


# ==============================================================================
# 5. Concurrency & Double-Checked Locking Tests
# ==============================================================================

@pytest.mark.asyncio
class TestConcurrencyAndAdvisoryLocking:
    """Verify double-checked locking and serialized execution."""

    async def test_second_caller_reuses_fresh_profile(self):
        async with get_test_session() as session:
            user = User(email=f"double_{uuid.uuid4().hex[:8]}@example.com", display_name="Double Check User")
            session.add(user)
            await session.flush()

            service = ProfileService(session=session)
            # Caller 1 refreshes profile
            res1 = await service.refresh_profile(user.id)
            assert res1.refresh_required is True
            assert res1.refresh_completed is True

            # Caller 2 calls refresh immediately (without force)
            res2 = await service.refresh_profile(user.id, force=False)
            assert res2.refresh_required is False
            assert res2.refresh_completed is False
            assert res2.reason == "none"

    async def test_pg_advisory_lock_query_executes_on_postgresql(self):
        async with get_test_session() as session:
            user = User(email=f"lock_{uuid.uuid4().hex[:8]}@example.com", display_name="Lock User")
            session.add(user)
            await session.flush()

            service = ProfileService(session=session)
            # Call refresh_profile - on PostgreSQL, it acquires and releases pg_advisory_xact_lock
            res = await service.refresh_profile(user.id)
            assert res.refresh_completed is True

    async def test_concurrent_sessions_double_checked_locking_and_snapshot_timing(self):
        """Verify transaction isolation and snapshot timing across distinct DB connections.

        PostgreSQL Snapshot Timing Mechanics:
        1. Worker 1 enters transaction, executes SELECT pg_advisory_xact_lock(:key),
           extracts signals, builds vector, and writes UserInterestProfile.
        2. Worker 2 enters transaction and requests SELECT pg_advisory_xact_lock(:key).
           Because Worker 1 holds the transaction advisory lock, Worker 2 blocks.
        3. Worker 1 commits its transaction. PostgreSQL automatically releases the
           transaction advisory lock on COMMIT.
        4. Worker 2 unblocks and acquires the lock. Under READ COMMITTED isolation,
           Worker 2's subsequent SELECT query executes with a NEW statement snapshot
           established AFTER the lock acquisition, cleanly observing Worker 1's committed row.
        5. Worker 2's double-checked should_refresh_profile evaluates to (False, 'none'),
           bypassing redundant vector calculation and returning immediately.

        Why a 2-transaction lock-then-repeatable-read sequence is flawed in PostgreSQL:
        - pg_advisory_xact_lock is strictly transaction-scoped. Committing Transaction 1
          releases the lock, leaving Transaction 2 completely unprotected against concurrent writers.
        - Session-level locks (pg_advisory_lock) leak if connections fail or return to connection pools.
        - Using REPEATABLE READ inside the locked transaction establishes the snapshot when the first
          query (the advisory lock SELECT) executes. If Worker 2 waited on the lock, its snapshot
          is frozen BEFORE Worker 1's commit, blinding Worker 2 to the newly created profile and
          triggering PostgreSQL serialization failures (SQLSTATE 40001) on UPDATE.
        - READ COMMITTED with per-user pg_advisory_xact_lock guarantees serialization without
          stale read anomalies or serialization failures.
        """
        user_id = uuid.uuid4()
        async with test_engine.connect() as setup_conn:
            await setup_conn.execute(
                text("INSERT INTO users (id, email, display_name) VALUES (:id, :email, :name)"),
                {"id": user_id, "email": f"concur_{user_id.hex[:8]}@example.com", "name": "Concur User"},
            )
            await setup_conn.commit()

        try:
            # Session 1: Performs initial profile calculation and commits
            async with test_engine.connect() as conn1:
                session1 = AsyncSession(bind=conn1, expire_on_commit=False)
                service1 = ProfileService(session=session1)
                res1 = await service1.refresh_profile(user_id)
                await session1.commit()
                assert res1.refresh_completed is True
                assert res1.reason == "initial_profile"

            # Session 2: Enters independent connection, acquires advisory lock,
            # reads committed state under READ COMMITTED, and skips recalculation
            async with test_engine.connect() as conn2:
                session2 = AsyncSession(bind=conn2, expire_on_commit=False)
                service2 = ProfileService(session=session2)
                res2 = await service2.refresh_profile(user_id, force=False)
                assert res2.refresh_required is False
                assert res2.refresh_completed is False
                assert res2.reason == "none"

        finally:
            async with test_engine.connect() as cleanup_conn:
                await cleanup_conn.execute(
                    text("DELETE FROM users WHERE id = :id"),
                    {"id": user_id},
                )
                await cleanup_conn.commit()


# ==============================================================================
# 5b. Authoritative-State Fingerprint Race Condition Tests
# ==============================================================================

@pytest.mark.asyncio
class TestAuthoritativeStateFingerprintRaceCondition:
    """Verify that capturing fingerprint immediately before extraction prevents silent stale profiles.

    If a concurrent transaction modifies user state between fingerprint capture and persistence:
    1. The persisted profile must retain the captured pre-extraction fingerprint.
    2. It must not be silently overwritten with a post-calculation fingerprint.
    3. The next should_refresh_profile check must detect the disparity (authoritative_state_change).
    4. The next refresh must recalculate with the updated data and persist the new fingerprint.
    """

    async def test_mutation_after_capture_preserves_captured_fingerprint_and_triggers_refresh(self):
        """A mutation committed during calculation must not be masked by post-calculation re-fingerprinting."""
        async with get_test_session() as session:
            user = User(email=f"race_{uuid.uuid4().hex[:8]}@example.com", display_name="Race User")
            session.add(user)
            await session.flush()

            w1 = BookWork(canonical_title="Race Work 1")
            w2 = BookWork(canonical_title="Race Work 2")
            session.add_all([w1, w2])
            await session.flush()

            # Initial state: Work 1 has a 5-star rating
            r1 = BookRating(user_id=user.id, work_id=w1.id, rating=5)
            session.add(r1)
            await session.flush()

            service = ProfileService(session=session)

            # Expected initial fingerprint (Work 1 only)
            expected_initial_fp = compute_authoritative_state_fingerprint([r1], [], [])

            # We hook extract_profile_signals: immediately after fingerprint capture,
            # we simulate a concurrent transaction adding a second rating (Work 2: 4 stars).
            original_extract = service.signal_extractor.extract_profile_signals

            async def mock_extract_signals(user_id, model_revision=None, calculation_time=None):
                # Mutation occurs after calculation_fingerprint was captured in _execute_refresh:
                r2 = BookRating(user_id=user.id, work_id=w2.id, rating=4)
                session.add(r2)
                await session.flush()
                return await original_extract(user_id=user_id, model_revision=model_revision, calculation_time=calculation_time)

            with patch.object(service.signal_extractor, "extract_profile_signals", side_effect=mock_extract_signals):
                res1 = await service.refresh_profile(user.id)
                assert res1.refresh_completed is True

            # Verify persisted profile
            profile = await service.get_profile(user.id)
            assert profile is not None
            persisted_fp = profile.inferred_topics["meta"]["authoritative_state_fingerprint"]

            # Must equal the pre-extraction calculation_fingerprint
            assert persisted_fp == expected_initial_fp

            # Must NOT equal the fingerprint of the modified state (Work 1 + Work 2)
            ratings_now = [r1, BookRating(work_id=w2.id, rating=4)]
            fp_with_both = compute_authoritative_state_fingerprint(ratings_now, [], [])
            assert persisted_fp != fp_with_both

            # Next freshness check MUST detect authoritative_state_change
            needed, reason = await service.should_refresh_profile(user.id)
            assert needed is True
            assert reason == "authoritative_state_change"

            # A subsequent refresh recalculates and adopts the new fingerprint
            res2 = await service.refresh_profile(user.id)
            assert res2.refresh_completed is True
            assert res2.reason == "authoritative_state_change"

            profile_updated = await service.get_profile(user.id)
            assert profile_updated.inferred_topics["meta"]["authoritative_state_fingerprint"] == fp_with_both

            # Now the profile is completely fresh
            needed_after, reason_after = await service.should_refresh_profile(user.id)
            assert needed_after is False
            assert reason_after == "none"

    async def test_mutation_during_topic_calculation_preserves_captured_fingerprint(self):
        """Simulate a user mutating shelf status while vector/topic calculation is executing."""
        async with get_test_session() as session:
            user = User(email=f"race_topic_{uuid.uuid4().hex[:8]}@example.com", display_name="Race Topic User")
            session.add(user)
            await session.flush()

            w = BookWork(canonical_title="Race Topic Work")
            session.add(w)
            await session.flush()

            # Initial shelf status: currently_reading
            ub = UserBook(user_id=user.id, work_id=w.id, status="currently_reading")
            session.add(ub)
            await session.flush()

            service = ProfileService(session=session)
            expected_initial_fp = await service.get_authoritative_state_fingerprint(user.id)

            # Hook topic_calculator.calculate to mutate UserBook status during calculation
            original_calc = service.topic_calculator.calculate

            async def mock_calculate(extracted_signals, calculation_time=None):
                # User changes status to completed while background calculation is computing
                ub.status = "completed"
                await session.flush()
                return await original_calc(extracted_signals=extracted_signals, calculation_time=calculation_time)

            with patch.object(service.topic_calculator, "calculate", side_effect=mock_calculate):
                res1 = await service.refresh_profile(user.id)
                assert res1.refresh_completed is True

            # Verify persisted profile has the captured fingerprint (currently_reading), NOT completed
            profile = await service.get_profile(user.id)
            persisted_fp = profile.inferred_topics["meta"]["authoritative_state_fingerprint"]
            assert persisted_fp == expected_initial_fp

            fp_completed = await service.get_authoritative_state_fingerprint(user.id)
            assert persisted_fp != fp_completed

            # Next check detects that authoritative state changed
            needed, reason = await service.should_refresh_profile(user.id)
            assert needed is True
            assert reason == "authoritative_state_change"

            # Subsequent refresh persists the updated completed state
            res2 = await service.refresh_profile(user.id)
            assert res2.refresh_completed is True

            profile2 = await service.get_profile(user.id)
            assert profile2.inferred_topics["meta"]["authoritative_state_fingerprint"] == fp_completed


# ==============================================================================
# 6. Background Task Helper Tests
# ==============================================================================

@pytest.mark.asyncio
class TestBackgroundTasksHelper:
    """Verify background task execution, deduplication, and isolated sessions."""

    async def test_schedule_profile_refresh_deduplicates_in_flight_tasks(self):
        uid = uuid.uuid4()
        bg_tasks = BackgroundTasks()

        # Clean deduplication set
        _active_background_refreshes.clear()

        # First schedule: succeeds
        scheduled1 = schedule_profile_refresh(bg_tasks, uid)
        assert scheduled1 is True
        assert uid in _active_background_refreshes
        assert len(bg_tasks.tasks) == 1

        # Second schedule for same user: skipped
        scheduled2 = schedule_profile_refresh(bg_tasks, uid)
        assert scheduled2 is False
        assert len(bg_tasks.tasks) == 1

        # Cleanup
        _active_background_refreshes.clear()

    async def test_run_background_profile_refresh_cleans_up_on_completion(self):
        uid = uuid.uuid4()
        _active_background_refreshes.add(uid)

        # Mock sessionmaker
        mock_session = AsyncMock()
        mock_factory = MagicMock(return_value=mock_session)
        mock_session.__aenter__.return_value = mock_session

        with patch("backend.services.profile.profile_tasks.ProfileService.refresh_profile", new_callable=AsyncMock) as mock_refresh:
            await _run_background_profile_refresh(uid, mock_factory)
            mock_refresh.assert_awaited_once_with(user_id=uid, force=False)

        # UID must be removed from deduplication set
        assert uid not in _active_background_refreshes

    async def test_run_background_profile_refresh_handles_and_logs_exception(self, caplog):
        uid = uuid.uuid4()
        _active_background_refreshes.add(uid)

        mock_session = AsyncMock()
        mock_factory = MagicMock(return_value=mock_session)
        mock_session.__aenter__.return_value = mock_session

        with caplog.at_level(logging.ERROR):
            with patch("backend.services.profile.profile_tasks.ProfileService.refresh_profile", side_effect=ValueError("Test background failure")):
                await _run_background_profile_refresh(uid, mock_factory)

        # UID removed despite exception
        assert uid not in _active_background_refreshes
        assert "Background profile refresh failed for user" in caplog.text
