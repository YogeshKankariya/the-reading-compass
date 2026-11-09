import math
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import Settings
from backend.db.models import (
    BookEmbedding,
    BookRating,
    BookWork,
    ReadingProgress,
    RecommendationItem,
    RecommendationRequest,
    User,
    UserBook,
    UserInteraction,
    UserPreference,
)
from backend.db.session import engine
from backend.services.profile.signal_extractor import (
    ExtractedProfileSignals,
    ProfileSignalExtractor,
)


# ==============================================================================
# Helper Context Manager for Database Session in Tests
# ==============================================================================

from contextlib import asynccontextmanager
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import create_async_engine
from backend.core.config import settings

# Use NullPool for tests so each connection is strictly scoped to its test event loop
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


# ==============================================================================
# Helper Factories
# ==============================================================================

def make_dummy_embedding(dim: int = 384, val: float = 0.1) -> list[float]:
    vec = [val] * dim
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec]


# ==============================================================================
# Unit Tests: Signal Resolution & Precedence (Pure Logic)
# ==============================================================================

class TestSignalResolutionPrecedence:
    """Verify deterministic priority selection and neutral-rating exception."""

    def setup_method(self):
        self.settings = Settings(
            DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/test_db",
            PROFILE_EMBEDDING_MODEL_REVISION=None,
        )
        self.extractor = ProfileSignalExtractor(session=None, settings=self.settings)  # type: ignore
        self.weights = self.settings.get_signal_weights()

    def test_current_rating_takes_precedence_over_all_lower_families(self):
        wid = uuid.uuid4()
        uid = uuid.uuid4()
        now = datetime.now(timezone.utc)

        # 5-star rating (+1.0)
        rating = BookRating(
            id=uuid.uuid4(),
            user_id=uid,
            work_id=wid,
            rating=5,
            created_at=now,
            updated_at=now,
        )
        # Lower families present for same work
        ub = UserBook(
            id=uuid.uuid4(),
            user_id=uid,
            work_id=wid,
            status="completed",
            created_at=now - timedelta(days=5),
            updated_at=now - timedelta(days=5),
        )
        rec_item = RecommendationItem(
            id=uuid.uuid4(),
            request_id=uuid.uuid4(),
            work_id=wid,
            rank_position=1,
            score=Decimal("0.9"),
            feedback_type="not_helpful",  # Negative feedback
            feedback_at=now - timedelta(days=2),
        )

        res_sig, neu_sig = self.extractor._resolve_single_work(
            work_id=wid,
            rating=rating,
            user_book=ub,
            rec_items=[rec_item],
            interactions=[],
            weights=self.weights,
        )

        assert res_sig is not None
        assert res_sig.effective_source == "rating"
        assert res_sig.effective_weight == 1.00
        assert res_sig.polarity == "positive"
        assert res_sig.contributing_interaction_ids == [rating.id]
        assert neu_sig is None

    def test_neutral_3star_rating_does_not_suppress_non_neutral_lifecycle(self):
        wid = uuid.uuid4()
        uid = uuid.uuid4()
        now = datetime.now(timezone.utc)

        # 3-star rating (+0.10, neutral)
        rating = BookRating(
            id=uuid.uuid4(),
            user_id=uid,
            work_id=wid,
            rating=3,
            created_at=now,
            updated_at=now,
        )
        # Completed reading (+0.50, positive)
        ub = UserBook(
            id=uuid.uuid4(),
            user_id=uid,
            work_id=wid,
            status="completed",
            created_at=now - timedelta(days=1),
            updated_at=now - timedelta(days=1),
        )

        res_sig, neu_sig = self.extractor._resolve_single_work(
            work_id=wid,
            rating=rating,
            user_book=ub,
            rec_items=[],
            interactions=[],
            weights=self.weights,
        )

        # Primary signal must be the completed lifecycle signal (+0.50)
        assert res_sig is not None
        assert res_sig.effective_source == "lifecycle"
        assert res_sig.effective_weight == 0.50
        assert res_sig.polarity == "positive"

        # Neutral 3-star rating must be preserved as neutral_sig
        assert neu_sig is not None
        assert neu_sig.effective_source == "rating"
        assert neu_sig.effective_weight == 0.10
        assert neu_sig.polarity == "neutral"

    def test_neutral_3star_rating_when_alone_is_primary_and_neutral(self):
        wid = uuid.uuid4()
        uid = uuid.uuid4()
        now = datetime.now(timezone.utc)

        rating = BookRating(
            id=uuid.uuid4(),
            user_id=uid,
            work_id=wid,
            rating=3,
            created_at=now,
            updated_at=now,
        )

        res_sig, neu_sig = self.extractor._resolve_single_work(
            work_id=wid,
            rating=rating,
            user_book=None,
            rec_items=[],
            interactions=[],
            weights=self.weights,
        )

        assert res_sig is not None
        assert res_sig.effective_source == "rating"
        assert res_sig.effective_weight == 0.10
        assert res_sig.polarity == "neutral"
        assert neu_sig is not None
        assert neu_sig.effective_weight == 0.10

    def test_lifecycle_terminal_completion_dominance(self):
        wid = uuid.uuid4()
        uid = uuid.uuid4()
        now = datetime.now(timezone.utc)

        ub = UserBook(
            id=uuid.uuid4(),
            user_id=uid,
            work_id=wid,
            status="completed",
            created_at=now - timedelta(days=10),
            updated_at=now,
        )
        # Historical interactions: start, progress
        int_start = UserInteraction(
            id=uuid.uuid4(),
            user_id=uid,
            work_id=wid,
            interaction_type="start",
            created_at=now - timedelta(days=10),
        )
        int_prog = UserInteraction(
            id=uuid.uuid4(),
            user_id=uid,
            work_id=wid,
            interaction_type="progress",
            created_at=now - timedelta(days=5),
        )

        res_sig, _ = self.extractor._resolve_single_work(
            work_id=wid,
            rating=None,
            user_book=ub,
            rec_items=[],
            interactions=[int_start, int_prog],
            weights=self.weights,
        )

        assert res_sig is not None
        assert res_sig.effective_source == "lifecycle"
        assert res_sig.effective_weight == 0.50
        assert res_sig.polarity == "positive"
        # All contributing interaction IDs are recorded
        assert ub.id in res_sig.contributing_interaction_ids
        assert int_start.id in res_sig.contributing_interaction_ids

    def test_abandonment_is_negative(self):
        wid = uuid.uuid4()
        uid = uuid.uuid4()
        now = datetime.now(timezone.utc)

        ub = UserBook(
            id=uuid.uuid4(),
            user_id=uid,
            work_id=wid,
            status="abandoned",
            created_at=now - timedelta(days=3),
            updated_at=now - timedelta(days=1),
        )

        res_sig, _ = self.extractor._resolve_single_work(
            work_id=wid,
            rating=None,
            user_book=ub,
            rec_items=[],
            interactions=[],
            weights=self.weights,
        )

        assert res_sig is not None
        assert res_sig.effective_source == "lifecycle"
        assert res_sig.effective_weight == -0.20
        assert res_sig.polarity == "negative"

    def test_later_restart_supersedes_earlier_abandonment(self):
        wid = uuid.uuid4()
        uid = uuid.uuid4()
        now = datetime.now(timezone.utc)

        # user_book is currently_reading (restarted after prior abandonment interaction)
        ub = UserBook(
            id=uuid.uuid4(),
            user_id=uid,
            work_id=wid,
            status="currently_reading",
            created_at=now - timedelta(days=20),
            updated_at=now - timedelta(days=1),
        )
        int_abandon = UserInteraction(
            id=uuid.uuid4(),
            user_id=uid,
            work_id=wid,
            interaction_type="abandon",
            created_at=now - timedelta(days=10),
        )
        int_restart = UserInteraction(
            id=uuid.uuid4(),
            user_id=uid,
            work_id=wid,
            interaction_type="start",
            created_at=now - timedelta(days=2),
        )

        res_sig, _ = self.extractor._resolve_single_work(
            work_id=wid,
            rating=None,
            user_book=ub,
            rec_items=[],
            interactions=[int_abandon, int_restart],
            weights=self.weights,
        )

        assert res_sig is not None
        assert res_sig.effective_source == "lifecycle"
        assert res_sig.effective_weight == 0.30  # start weight
        assert res_sig.polarity == "positive"

    def test_paused_lifecycle_status_resolves_to_in_progress(self):
        wid = uuid.uuid4()
        uid = uuid.uuid4()
        now = datetime.now(timezone.utc)
        last_read_time = now - timedelta(days=2)
        ub_updated_time = now - timedelta(days=1)

        # 1. UserBook with status="paused" and reading progress
        progress = ReadingProgress(
            id=uuid.uuid4(),
            user_book_id=uuid.uuid4(),
            last_read_at=last_read_time,
        )
        ub_paused = UserBook(
            id=uuid.uuid4(),
            user_id=uid,
            work_id=wid,
            status="paused",
            created_at=now - timedelta(days=30),
            updated_at=ub_updated_time,
            progress=progress,
        )

        # Earlier abandonment interaction (e.g. 15 days ago)
        int_abandon = UserInteraction(
            id=uuid.uuid4(),
            user_id=uid,
            work_id=wid,
            interaction_type="abandon",
            created_at=now - timedelta(days=15),
        )

        res_sig, _ = self.extractor._resolve_single_work(
            work_id=wid,
            rating=None,
            user_book=ub_paused,
            rec_items=[],
            interactions=[int_abandon],
            weights=self.weights,
        )

        assert res_sig is not None
        # 1 & 3: Resolves to lifecycle family
        assert res_sig.effective_source == "lifecycle"
        # 2: Effective weight is configured start weight (+0.30)
        assert res_sig.effective_weight == 0.30
        assert res_sig.polarity == "positive"
        assert res_sig.metadata == {"lifecycle_status": "paused"}
        # 4: Not treated as a TBR save
        assert res_sig.effective_source != "save"
        # 5: Earlier abandonment does not override current paused state
        assert res_sig.effective_weight > 0
        assert res_sig.polarity != "negative"
        # 6: Timestamp follows progress.last_read_at precedence
        assert res_sig.signal_timestamp == last_read_time

        # Verify timestamp fallback to user_book.updated_at when progress.last_read_at is None
        ub_paused_no_read_time = UserBook(
            id=uuid.uuid4(),
            user_id=uid,
            work_id=wid,
            status="paused",
            created_at=now - timedelta(days=30),
            updated_at=ub_updated_time,
            progress=None,
        )
        res_fallback, _ = self.extractor._resolve_single_work(
            work_id=wid,
            rating=None,
            user_book=ub_paused_no_read_time,
            rec_items=[],
            interactions=[],
            weights=self.weights,
        )
        assert res_fallback is not None
        assert res_fallback.signal_timestamp == ub_updated_time

    def test_recommendation_feedback_helpful_and_not_helpful(self):
        wid = uuid.uuid4()
        now = datetime.now(timezone.utc)

        item_helpful = RecommendationItem(
            id=uuid.uuid4(),
            request_id=uuid.uuid4(),
            work_id=wid,
            rank_position=1,
            score=Decimal("0.85"),
            feedback_type="helpful",
            feedback_at=now,
        )
        res_sig, _ = self.extractor._resolve_single_work(
            work_id=wid,
            rating=None,
            user_book=None,
            rec_items=[item_helpful],
            interactions=[],
            weights=self.weights,
        )
        assert res_sig is not None
        assert res_sig.effective_source == "recommendation_feedback"
        assert res_sig.effective_weight == 0.50
        assert res_sig.polarity == "positive"

        # Not helpful
        item_unhelpful = RecommendationItem(
            id=uuid.uuid4(),
            request_id=uuid.uuid4(),
            work_id=wid,
            rank_position=1,
            score=Decimal("0.85"),
            feedback_type="not_helpful",
            feedback_at=now,
        )
        res_sig_neg, _ = self.extractor._resolve_single_work(
            work_id=wid,
            rating=None,
            user_book=None,
            rec_items=[item_unhelpful],
            interactions=[],
            weights=self.weights,
        )
        assert res_sig_neg is not None
        assert res_sig_neg.effective_source == "recommendation_feedback"
        assert res_sig_neg.effective_weight == -0.40
        assert res_sig_neg.polarity == "negative"

    def test_recommendation_feedback_save_and_dismiss_routing(self):
        wid = uuid.uuid4()
        now = datetime.now(timezone.utc)

        # Recommendation save routed to save family (+0.60)
        item_save = RecommendationItem(
            id=uuid.uuid4(),
            request_id=uuid.uuid4(),
            work_id=wid,
            rank_position=1,
            score=Decimal("0.85"),
            feedback_type="save",
            feedback_at=now,
        )
        res_save, _ = self.extractor._resolve_single_work(
            work_id=wid,
            rating=None,
            user_book=None,
            rec_items=[item_save],
            interactions=[],
            weights=self.weights,
        )
        assert res_save is not None
        assert res_save.effective_source == "save"
        assert res_save.effective_weight == 0.60
        assert res_save.polarity == "positive"

        # Recommendation dismiss routed to dismissal family (-0.15)
        item_dismiss = RecommendationItem(
            id=uuid.uuid4(),
            request_id=uuid.uuid4(),
            work_id=wid,
            rank_position=1,
            score=Decimal("0.85"),
            feedback_type="dismiss",
            feedback_at=now,
        )
        res_dismiss, _ = self.extractor._resolve_single_work(
            work_id=wid,
            rating=None,
            user_book=None,
            rec_items=[item_dismiss],
            interactions=[],
            weights=self.weights,
        )
        assert res_dismiss is not None
        assert res_dismiss.effective_source == "dismissal"
        assert res_dismiss.effective_weight == -0.15
        assert res_dismiss.polarity == "negative"

    def test_deterministic_tie_breaking_for_identical_timestamps(self):
        wid = uuid.uuid4()
        uid = uuid.uuid4()
        now = datetime(2026, 11, 9, 12, 0, 0, tzinfo=timezone.utc)

        # Two passive events with identical timestamp
        id1 = uuid.UUID("11111111-1111-1111-1111-111111111111")
        id2 = uuid.UUID("22222222-2222-2222-2222-222222222222")

        e1 = UserInteraction(id=id1, user_id=uid, work_id=wid, interaction_type="click", created_at=now)
        e2 = UserInteraction(id=id2, user_id=uid, work_id=wid, interaction_type="click", created_at=now)

        res1, _ = self.extractor._resolve_single_work(
            work_id=wid, rating=None, user_book=None, rec_items=[], interactions=[e1, e2], weights=self.weights
        )
        res2, _ = self.extractor._resolve_single_work(
            work_id=wid, rating=None, user_book=None, rec_items=[], interactions=[e2, e1], weights=self.weights
        )

        assert res1 is not None and res2 is not None
        # Must be deterministic regardless of input list ordering
        assert res1.signal_timestamp == res2.signal_timestamp
        assert res1.effective_weight == res2.effective_weight

    def test_library_tbr_save_resolved_correctly(self):
        wid = uuid.uuid4()
        uid = uuid.uuid4()
        now = datetime.now(timezone.utc)

        ub = UserBook(
            id=uuid.uuid4(),
            user_id=uid,
            work_id=wid,
            status="want_to_read",
            created_at=now,
            updated_at=now,
        )
        res_sig, _ = self.extractor._resolve_single_work(
            work_id=wid,
            rating=None,
            user_book=ub,
            rec_items=[],
            interactions=[],
            weights=self.weights,
        )
        assert res_sig is not None
        assert res_sig.effective_source == "save"
        assert res_sig.effective_weight == 0.60
        assert res_sig.polarity == "positive"

    def test_dismissal_is_negative(self):
        wid = uuid.uuid4()
        uid = uuid.uuid4()
        now = datetime.now(timezone.utc)

        int_dismiss = UserInteraction(
            id=uuid.uuid4(),
            user_id=uid,
            work_id=wid,
            interaction_type="dismiss",
            created_at=now,
        )
        res_sig, _ = self.extractor._resolve_single_work(
            work_id=wid,
            rating=None,
            user_book=None,
            rec_items=[],
            interactions=[int_dismiss],
            weights=self.weights,
        )
        assert res_sig is not None
        assert res_sig.effective_source == "dismissal"
        assert res_sig.effective_weight == -0.15
        assert res_sig.polarity == "negative"

    def test_later_positive_action_supersedes_earlier_dismissal(self):
        wid = uuid.uuid4()
        uid = uuid.uuid4()
        now = datetime.now(timezone.utc)

        # Dismissed 5 days ago
        int_dismiss = UserInteraction(
            id=uuid.uuid4(),
            user_id=uid,
            work_id=wid,
            interaction_type="dismiss",
            created_at=now - timedelta(days=5),
        )
        # Saved to TBR yesterday
        ub = UserBook(
            id=uuid.uuid4(),
            user_id=uid,
            work_id=wid,
            status="want_to_read",
            created_at=now - timedelta(days=1),
            updated_at=now - timedelta(days=1),
        )

        res_sig, _ = self.extractor._resolve_single_work(
            work_id=wid,
            rating=None,
            user_book=ub,
            rec_items=[],
            interactions=[int_dismiss],
            weights=self.weights,
        )
        assert res_sig is not None
        # Save supersedes earlier dismissal
        assert res_sig.effective_source == "save"
        assert res_sig.effective_weight == 0.60
        assert res_sig.polarity == "positive"

    def test_passive_interactions_ceiling_and_tie_breaking(self):
        wid = uuid.uuid4()
        uid = uuid.uuid4()
        now = datetime.now(timezone.utc)

        # 3 views, 1 click, 1 recommendation_click
        events = [
            UserInteraction(id=uuid.uuid4(), user_id=uid, work_id=wid, interaction_type="view", created_at=now - timedelta(hours=3)),
            UserInteraction(id=uuid.uuid4(), user_id=uid, work_id=wid, interaction_type="view", created_at=now - timedelta(hours=2)),
            UserInteraction(id=uuid.uuid4(), user_id=uid, work_id=wid, interaction_type="click", created_at=now - timedelta(hours=1)),
            UserInteraction(id=uuid.uuid4(), user_id=uid, work_id=wid, interaction_type="recommendation_click", created_at=now),
        ]

        res_sig, _ = self.extractor._resolve_single_work(
            work_id=wid,
            rating=None,
            user_book=None,
            rec_items=[],
            interactions=events,
            weights=self.weights,
        )

        assert res_sig is not None
        # recommendation_click (+0.15) is strongest and wins; never summed!
        assert res_sig.effective_source == "passive"
        assert res_sig.effective_weight == 0.15
        assert res_sig.polarity == "positive"
        assert len(res_sig.contributing_interaction_ids) == 4

    def test_explicit_signal_suppresses_passive_browsing(self):
        wid = uuid.uuid4()
        uid = uuid.uuid4()
        now = datetime.now(timezone.utc)

        ub = UserBook(
            id=uuid.uuid4(),
            user_id=uid,
            work_id=wid,
            status="want_to_read",
            created_at=now,
            updated_at=now,
        )
        click_event = UserInteraction(
            id=uuid.uuid4(),
            user_id=uid,
            work_id=wid,
            interaction_type="recommendation_click",
            created_at=now,
        )

        res_sig, _ = self.extractor._resolve_single_work(
            work_id=wid,
            rating=None,
            user_book=ub,
            rec_items=[],
            interactions=[click_event],
            weights=self.weights,
        )

        assert res_sig is not None
        assert res_sig.effective_source == "save"
        assert res_sig.effective_weight == 0.60  # Click was suppressed by Save


# ==============================================================================
# Unit Tests: Watermark & Event Eligibility Logic
# ==============================================================================

class TestWatermarkEligibilityCounting:
    """Verify event-level watermark counting with passive throttling and deduplication."""

    def setup_method(self):
        self.settings = Settings(
            DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/test_db",
        )
        self.extractor = ProfileSignalExtractor(session=None, settings=self.settings)  # type: ignore

    def test_null_work_id_excluded(self):
        uid = uuid.uuid4()
        now = datetime.now(timezone.utc)
        events = [
            UserInteraction(id=uuid.uuid4(), user_id=uid, work_id=None, interaction_type="click", created_at=now),
            UserInteraction(id=uuid.uuid4(), user_id=uid, work_id=None, interaction_type="view", created_at=now),
        ]
        count = self.extractor._count_eligible_events(events, set(), set())
        assert count == 0

    def test_unsupported_interaction_type_excluded(self):
        uid = uuid.uuid4()
        wid = uuid.uuid4()
        now = datetime.now(timezone.utc)
        events = [
            UserInteraction(id=uuid.uuid4(), user_id=uid, work_id=wid, interaction_type="unknown_event", created_at=now),
        ]
        count = self.extractor._count_eligible_events(events, set(), set())
        assert count == 0

    def test_duplicate_active_rating_events_counted_once(self):
        uid = uuid.uuid4()
        wid = uuid.uuid4()
        now = datetime.now(timezone.utc)

        # 3 rate events for the same active rated work
        events = [
            UserInteraction(id=uuid.uuid4(), user_id=uid, work_id=wid, interaction_type="rate", created_at=now - timedelta(days=2)),
            UserInteraction(id=uuid.uuid4(), user_id=uid, work_id=wid, interaction_type="rate", created_at=now - timedelta(days=1)),
            UserInteraction(id=uuid.uuid4(), user_id=uid, work_id=wid, interaction_type="rate", created_at=now),
        ]
        count = self.extractor._count_eligible_events(events, active_rated_work_ids={wid}, active_tbr_work_ids=set())
        assert count == 1

    def test_orphan_rate_event_without_active_rating_excluded(self):
        uid = uuid.uuid4()
        wid = uuid.uuid4()
        now = datetime.now(timezone.utc)
        events = [
            UserInteraction(id=uuid.uuid4(), user_id=uid, work_id=wid, interaction_type="rate", created_at=now),
        ]
        # Active ratings does NOT contain wid
        count = self.extractor._count_eligible_events(events, active_rated_work_ids=set(), active_tbr_work_ids=set())
        assert count == 0

    def test_passive_throttling_window_1_hour(self):
        uid = uuid.uuid4()
        wid = uuid.uuid4()
        base_time = datetime(2026, 11, 9, 12, 0, 0, tzinfo=timezone.utc)

        # Events at 12:00, 12:15, 12:45 (all in 1 hour) -> count as 1
        # Event at 13:30 (> 1 hour from 12:00) -> count as 2nd
        # Event at 13:40 (< 1 hour from 13:30) -> throttled
        events = [
            UserInteraction(id=uuid.uuid4(), user_id=uid, work_id=wid, interaction_type="view", created_at=base_time),
            UserInteraction(id=uuid.uuid4(), user_id=uid, work_id=wid, interaction_type="click", created_at=base_time + timedelta(minutes=15)),
            UserInteraction(id=uuid.uuid4(), user_id=uid, work_id=wid, interaction_type="view", created_at=base_time + timedelta(minutes=45)),
            UserInteraction(id=uuid.uuid4(), user_id=uid, work_id=wid, interaction_type="view", created_at=base_time + timedelta(minutes=90)),
            UserInteraction(id=uuid.uuid4(), user_id=uid, work_id=wid, interaction_type="click", created_at=base_time + timedelta(minutes=100)),
        ]
        count = self.extractor._count_eligible_events(events, set(), set())
        assert count == 2

    def test_distinct_lifecycle_events_are_all_counted(self):
        uid = uuid.uuid4()
        wid = uuid.uuid4()
        base = datetime(2026, 11, 9, 10, 0, 0, tzinfo=timezone.utc)

        # start, progress, complete are all distinct historical milestones
        events = [
            UserInteraction(id=uuid.uuid4(), user_id=uid, work_id=wid, interaction_type="start", created_at=base),
            UserInteraction(id=uuid.uuid4(), user_id=uid, work_id=wid, interaction_type="progress", created_at=base + timedelta(days=2)),
            UserInteraction(id=uuid.uuid4(), user_id=uid, work_id=wid, interaction_type="complete", created_at=base + timedelta(days=5)),
        ]
        count = self.extractor._count_eligible_events(events, set(), set())
        assert count == 3


# ==============================================================================
# Integration Tests with Database (AsyncSession)
# ==============================================================================

@pytest.mark.asyncio
async def test_complete_profile_signal_extraction_flow():
    async with get_test_session() as async_session:
        # 1. Create test user and works
        user = User(email=f"reader_{uuid.uuid4().hex[:8]}@example.com", display_name="Avid Learner")
        work1 = BookWork(canonical_title="Clean Architecture", first_published_year=2017)
        work2 = BookWork(canonical_title="Designing Data-Intensive Applications", first_published_year=2017)
        work3 = BookWork(canonical_title="Structure and Interpretation", first_published_year=1996)
        async_session.add_all([user, work1, work2, work3])
        await async_session.flush()

        now = datetime.now(timezone.utc)

        # 2. Add Authoritative Records
        # work1: 5-star rating
        rating = BookRating(user_id=user.id, work_id=work1.id, rating=5, created_at=now, updated_at=now)
        # work2: completed reading
        ub2 = UserBook(user_id=user.id, work_id=work2.id, status="completed", created_at=now, updated_at=now)
        # work3: want_to_read
        ub3 = UserBook(user_id=user.id, work_id=work3.id, status="want_to_read", created_at=now, updated_at=now)
        # Explicit preference
        pref = UserPreference(user_id=user.id, preference_type="genre", preference_value="Computer Science", preference_weight=Decimal("0.900"))
        async_session.add_all([rating, ub2, ub3, pref])

        # 3. Add Interactions
        int_rate1 = UserInteraction(user_id=user.id, work_id=work1.id, interaction_type="rate", created_at=now)
        int_start2 = UserInteraction(user_id=user.id, work_id=work2.id, interaction_type="start", created_at=now - timedelta(days=3))
        int_comp2 = UserInteraction(user_id=user.id, work_id=work2.id, interaction_type="complete", created_at=now)
        int_save3 = UserInteraction(user_id=user.id, work_id=work3.id, interaction_type="save", created_at=now)
        async_session.add_all([int_rate1, int_start2, int_comp2, int_save3])

        # 4. Add Active Embedding for work1 & work2
        emb_vec1 = make_dummy_embedding(384, 0.1)
        emb_vec2 = make_dummy_embedding(384, 0.2)
        emb1 = BookEmbedding(
            work_id=work1.id,
            model_name="sentence-transformers/all-MiniLM-L6-v2",
            model_revision="test-revision-1",
            embedding=emb_vec1,
            source_text_hash="hash1",
            is_active=True,
        )
        emb2 = BookEmbedding(
            work_id=work2.id,
            model_name="sentence-transformers/all-MiniLM-L6-v2",
            model_revision="test-revision-1",
            embedding=emb_vec2,
            source_text_hash="hash2",
            is_active=True,
        )
        async_session.add_all([emb1, emb2])
        await async_session.flush()

        # 5. Execute Extractor with explicit test revision
        extractor = ProfileSignalExtractor(session=async_session)
        payload = await extractor.extract_profile_signals(
            user_id=user.id,
            model_revision="test-revision-1",
            calculation_time=now,
        )

        assert isinstance(payload, ExtractedProfileSignals)
        assert payload.user_id == user.id
        # 3 works -> exactly 3 resolved signals
        assert len(payload.resolved_signals) == 3
        work_sources = {s.work_id: s.effective_source for s in payload.resolved_signals}
        assert work_sources[work1.id] == "rating"
        assert work_sources[work2.id] == "lifecycle"
        assert work_sources[work3.id] == "save"

        # Explicit preference
        assert len(payload.explicit_preferences) == 1
        assert payload.explicit_preferences[0].preference_value == "Computer Science"

        # Compatible embeddings: work1 and work2 available; work3 missing
        assert len(payload.work_embeddings) == 2
        assert work1.id in payload.work_embeddings
        assert work2.id in payload.work_embeddings
        assert work3.id not in payload.work_embeddings
        assert payload.embedding_status == "available"
        assert payload.model_revision == "test-revision-1"

        # Watermark count:
        # int_rate1 (1) + int_start2 (1) + int_comp2 (1) + int_save3 (1) = 4
        assert payload.eligible_interaction_count == 4


@pytest.mark.asyncio
async def test_embedding_unset_revision_does_not_wildcard():
    async with get_test_session() as async_session:
        user = User(email=f"user_{uuid.uuid4().hex[:8]}@example.com", display_name="Test User")
        work = BookWork(canonical_title="Test Book")
        async_session.add_all([user, work])
        await async_session.flush()

        # Add active embedding with revision "v1"
        emb = BookEmbedding(
            work_id=work.id,
            model_name="sentence-transformers/all-MiniLM-L6-v2",
            model_revision="v1",
            embedding=make_dummy_embedding(384),
            source_text_hash="h1",
            is_active=True,
        )
        async_session.add(emb)
        await async_session.flush()

        # Extractor called with model_revision=None and settings revision=None
        extractor = ProfileSignalExtractor(session=async_session)
        embs, status, rev = await extractor.get_compatible_embeddings(
            work_ids=[work.id],
            model_revision=None,
        )

        assert status == "revision_unspecified"
        assert embs == {}
        assert rev is None


@pytest.mark.asyncio
async def test_inactive_embeddings_are_excluded():
    async with get_test_session() as async_session:
        work = BookWork(canonical_title="Inactive Work")
        async_session.add(work)
        await async_session.flush()

        emb_inactive = BookEmbedding(
            work_id=work.id,
            model_name="sentence-transformers/all-MiniLM-L6-v2",
            model_revision="test-revision-1",
            embedding=make_dummy_embedding(384),
            source_text_hash="h_inactive",
            is_active=False,  # Inactive!
        )
        async_session.add(emb_inactive)
        await async_session.flush()

        extractor = ProfileSignalExtractor(session=async_session)
        embs, status, _ = await extractor.get_compatible_embeddings(
            work_ids=[work.id],
            model_revision="test-revision-1",
        )

        assert status == "no_compatible_embeddings"
        assert embs == {}


@pytest.mark.asyncio
async def test_user_isolation_enforced():
    async with get_test_session() as async_session:
        u1 = User(email=f"u1_{uuid.uuid4().hex[:8]}@example.com", display_name="User One")
        u2 = User(email=f"u2_{uuid.uuid4().hex[:8]}@example.com", display_name="User Two")
        work = BookWork(canonical_title="Shared Work")
        async_session.add_all([u1, u2, work])
        await async_session.flush()

        # u1 rated 5 stars; u2 has no ratings
        r1 = BookRating(user_id=u1.id, work_id=work.id, rating=5)
        async_session.add(r1)
        await async_session.flush()

        extractor = ProfileSignalExtractor(session=async_session)
        payload_u2 = await extractor.extract_profile_signals(user_id=u2.id)

        # u2 should have 0 signals despite u1 having rated the work
        assert len(payload_u2.resolved_signals) == 0
        assert payload_u2.eligible_interaction_count == 0


@pytest.mark.asyncio
async def test_cold_start_user_extraction():
    async with get_test_session() as async_session:
        user = User(email=f"cold_{uuid.uuid4().hex[:8]}@example.com", display_name="Cold Start User")
        async_session.add(user)
        await async_session.flush()

        extractor = ProfileSignalExtractor(session=async_session)
        payload = await extractor.extract_profile_signals(user_id=user.id, model_revision="test-revision-1")

        assert payload.user_id == user.id
        assert payload.resolved_signals == []
        assert payload.neutral_signals == []
        assert payload.explicit_preferences == []
        assert payload.eligible_interaction_count == 0
        assert payload.work_embeddings == {}
        assert payload.embedding_status == "no_compatible_embeddings"
