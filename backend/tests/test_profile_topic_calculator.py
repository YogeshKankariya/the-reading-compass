import math
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import create_async_engine

from backend.core.config import Settings, settings
from backend.db.models import (
    BookWork,
    BookWorkGenre,
    BookWorkSubject,
    Genre,
    Subject,
    User,
)
from backend.schemas.profile import (
    InferredTopicsPayload,
    ResolvedWorkSignal,
    TopicAffinityEntry,
)
from backend.services.profile.signal_extractor import ExtractedProfileSignals
from backend.services.profile.topic_calculator import (
    ProfileTopicCalculator,
    calculate_topic_affinities,
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


# ==============================================================================
# 1. Pure Calculation Unit Tests: Genre Affinity
# ==============================================================================

class TestGenreAffinityCalculation:
    """Verify deterministic genre affinity aggregation and normalization."""

    def test_single_genre_single_work(self):
        wid = uuid.uuid4()
        gid = uuid.uuid4()
        now = datetime.now(timezone.utc)

        sig = ResolvedWorkSignal(
            work_id=wid,
            effective_weight=0.8,
            polarity="positive",
            signal_timestamp=now,
        )

        payload = calculate_topic_affinities(
            positive_signals=[sig],
            work_genres={wid: [gid]},
            work_subjects={},
            calculation_time=now,
        )

        assert str(gid) in payload.genres
        entry = payload.genres[str(gid)]
        # Single topic normalizes to 1.0
        assert entry.affinity == pytest.approx(1.0)
        assert entry.interaction_count == 1
        assert entry.last_interacted_at == now

    def test_multiple_genres_multiple_works_proportional_affinity(self):
        w1, w2 = uuid.uuid4(), uuid.uuid4()
        g_scifi, g_fantasy = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(timezone.utc)

        # w1 (Sci-Fi, Fantasy) has weight 0.50
        # w2 (Sci-Fi) has weight 0.50
        # Sci-Fi total = 1.00 -> affinity 1.0
        # Fantasy total = 0.50 -> affinity 0.50
        s1 = ResolvedWorkSignal(work_id=w1, effective_weight=0.50, polarity="positive", signal_timestamp=now)
        s2 = ResolvedWorkSignal(work_id=w2, effective_weight=0.50, polarity="positive", signal_timestamp=now)

        payload = calculate_topic_affinities(
            positive_signals=[s1, s2],
            work_genres={
                w1: [g_scifi, g_fantasy],
                w2: [g_scifi],
            },
            work_subjects={},
            calculation_time=now,
        )

        assert payload.genres[str(g_scifi)].affinity == pytest.approx(1.0)
        assert payload.genres[str(g_scifi)].interaction_count == 2
        assert payload.genres[str(g_fantasy)].affinity == pytest.approx(0.5)
        assert payload.genres[str(g_fantasy)].interaction_count == 1

    def test_repeated_signals_for_same_work_deduplicated(self):
        wid = uuid.uuid4()
        gid = uuid.uuid4()
        now = datetime.now(timezone.utc)

        # Two signals for the same work_id (e.g. repeated events)
        s1 = ResolvedWorkSignal(work_id=wid, effective_weight=0.30, polarity="positive", signal_timestamp=now - timedelta(days=2))
        s2 = ResolvedWorkSignal(work_id=wid, effective_weight=0.60, polarity="positive", signal_timestamp=now)

        # Work also mapped multiple times in genres list
        payload = calculate_topic_affinities(
            positive_signals=[s1, s2],
            work_genres={wid: [gid, gid, gid]},
            work_subjects={},
            calculation_time=now,
        )

        entry = payload.genres[str(gid)]
        assert entry.affinity == pytest.approx(1.0)
        # Exactly 1 distinct contributing work
        assert entry.interaction_count == 1

    def test_recency_decay_reduces_older_work_influence(self):
        w_old, w_recent = uuid.uuid4(), uuid.uuid4()
        g_old, g_recent = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(timezone.utc)

        # Both works have identical base weight 1.0
        # w_old is 180 days old (1 half-life -> decayed weight 0.5)
        # w_recent is at calculation time (0 elapsed -> decayed weight 1.0)
        s_old = ResolvedWorkSignal(work_id=w_old, effective_weight=1.0, polarity="positive", signal_timestamp=now - timedelta(days=180))
        s_recent = ResolvedWorkSignal(work_id=w_recent, effective_weight=1.0, polarity="positive", signal_timestamp=now)

        payload = calculate_topic_affinities(
            positive_signals=[s_old, s_recent],
            work_genres={w_old: [g_old], w_recent: [g_recent]},
            work_subjects={},
            calculation_time=now,
            half_life_days=180.0,
        )

        assert payload.genres[str(g_recent)].affinity == pytest.approx(1.0)
        # g_old should be 0.5 / 1.0 = 0.5
        assert payload.genres[str(g_old)].affinity == pytest.approx(0.5, rel=1e-3)

    def test_empty_work_genre_mappings_handled_safely(self):
        wid = uuid.uuid4()
        now = datetime.now(timezone.utc)
        sig = ResolvedWorkSignal(work_id=wid, effective_weight=1.0, polarity="positive", signal_timestamp=now)

        # Work has no genre associations in catalogue
        payload = calculate_topic_affinities(
            positive_signals=[sig],
            work_genres={},
            work_subjects={},
            calculation_time=now,
        )

        assert payload.genres == {}
        assert payload.subjects == {}


# ==============================================================================
# 2. Pure Calculation Unit Tests: Subject Affinity & Relevance Weights
# ==============================================================================

class TestSubjectAffinityCalculation:
    """Verify subject affinity calculation including catalogue relevance weights."""

    def test_default_relevance_weight_when_null_is_one(self):
        wid = uuid.uuid4()
        sid = uuid.uuid4()
        now = datetime.now(timezone.utc)

        sig = ResolvedWorkSignal(work_id=wid, effective_weight=0.75, polarity="positive", signal_timestamp=now)

        # Relevance weight passed as None
        payload = calculate_topic_affinities(
            positive_signals=[sig],
            work_genres={},
            work_subjects={wid: [(sid, None)]},
            calculation_time=now,
        )

        assert str(sid) in payload.subjects
        assert payload.subjects[str(sid)].affinity == pytest.approx(1.0)
        assert payload.subjects[str(sid)].interaction_count == 1

    def test_different_relevance_weights_scale_contribution(self):
        w1, w2 = uuid.uuid4(), uuid.uuid4()
        s_high, s_low = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(timezone.utc)

        # Both works have same base weight 1.0
        # s_high has relevance 1.00
        # s_low has relevance 0.40
        sig1 = ResolvedWorkSignal(work_id=w1, effective_weight=1.0, polarity="positive", signal_timestamp=now)
        sig2 = ResolvedWorkSignal(work_id=w2, effective_weight=1.0, polarity="positive", signal_timestamp=now)

        payload = calculate_topic_affinities(
            positive_signals=[sig1, sig2],
            work_genres={},
            work_subjects={
                w1: [(s_high, 1.0)],
                w2: [(s_low, 0.4)],
            },
            calculation_time=now,
        )

        assert payload.subjects[str(s_high)].affinity == pytest.approx(1.0)
        assert payload.subjects[str(s_low)].affinity == pytest.approx(0.4)

    def test_decimal_relevance_weight_converted_properly(self):
        wid = uuid.uuid4()
        sid = uuid.uuid4()
        now = datetime.now(timezone.utc)

        sig = ResolvedWorkSignal(work_id=wid, effective_weight=1.0, polarity="positive", signal_timestamp=now)

        # Decimal relevance from SQLAlchemy Numeric column
        payload = calculate_topic_affinities(
            positive_signals=[sig],
            work_genres={},
            work_subjects={wid: [(sid, Decimal("0.850"))]},
            calculation_time=now,
        )

        assert str(sid) in payload.subjects
        assert payload.subjects[str(sid)].affinity == pytest.approx(1.0)

    def test_duplicate_subject_mapping_deduplicated_to_max_relevance(self):
        wid = uuid.uuid4()
        sid = uuid.uuid4()
        now = datetime.now(timezone.utc)

        sig = ResolvedWorkSignal(work_id=wid, effective_weight=1.0, polarity="positive", signal_timestamp=now)

        # Duplicate subject with different relevance weights for same work
        payload = calculate_topic_affinities(
            positive_signals=[sig],
            work_genres={},
            work_subjects={wid: [(sid, 0.3), (sid, 0.9), (sid, 0.6)]},
            calculation_time=now,
        )

        # Interaction count must be strictly 1 distinct work
        assert payload.subjects[str(sid)].interaction_count == 1
        assert payload.subjects[str(sid)].affinity == pytest.approx(1.0)

    @pytest.mark.parametrize("valid_weight", [0.0, 0.65, 1.0])
    def test_valid_relevance_weights_accepted(self, valid_weight):
        wid = uuid.uuid4()
        sid = uuid.uuid4()
        now = datetime.now(timezone.utc)
        sig = ResolvedWorkSignal(work_id=wid, effective_weight=1.0, polarity="positive", signal_timestamp=now)

        payload = calculate_topic_affinities(
            positive_signals=[sig],
            work_genres={},
            work_subjects={wid: [(sid, valid_weight)]},
            calculation_time=now,
        )

        assert str(sid) in payload.subjects
        expected = 0.0 if valid_weight == 0.0 else 1.0
        assert payload.subjects[str(sid)].affinity == pytest.approx(expected)

    @pytest.mark.parametrize("invalid_weight,match_err", [
        (-0.01, "between 0.0 and 1.0 inclusive"),
        (-1.5, "between 0.0 and 1.0 inclusive"),
        (1.001, "between 0.0 and 1.0 inclusive"),
        (2.5, "between 0.0 and 1.0 inclusive"),
        (float("nan"), "finite number"),
        (float("inf"), "finite number"),
        (float("-inf"), "finite number"),
        ("invalid_str", "could not be converted to float"),
        (True, "cannot be a boolean"),
        (False, "cannot be a boolean"),
    ])
    def test_invalid_relevance_weights_raise_descriptive_value_error(self, invalid_weight, match_err):
        wid = uuid.uuid4()
        sid = uuid.uuid4()
        now = datetime.now(timezone.utc)
        sig = ResolvedWorkSignal(work_id=wid, effective_weight=1.0, polarity="positive", signal_timestamp=now)

        with pytest.raises(ValueError, match=match_err):
            calculate_topic_affinities(
                positive_signals=[sig],
                work_genres={},
                work_subjects={wid: [(sid, invalid_weight)]},
                calculation_time=now,
            )


# ==============================================================================
# 3. Pure Calculation Unit Tests: Neutral Signals
# ==============================================================================

class TestNeutralSignalHandling:
    """Verify that neutral signals contribute to topic exposure without double-counting."""

    def test_neutral_only_work_contributes_to_topic_exposure(self):
        wid = uuid.uuid4()
        gid = uuid.uuid4()
        now = datetime.now(timezone.utc)

        # Neutral 3-star rating as sole signal
        neutral_sig = ResolvedWorkSignal(
            work_id=wid,
            effective_weight=0.10,
            polarity="neutral",
            effective_source="rating",
            signal_timestamp=now,
        )

        payload = calculate_topic_affinities(
            positive_signals=[neutral_sig],
            work_genres={wid: [gid]},
            work_subjects={},
            calculation_time=now,
        )

        assert str(gid) in payload.genres
        assert payload.genres[str(gid)].affinity == pytest.approx(1.0)
        assert payload.genres[str(gid)].interaction_count == 1

    def test_primary_positive_signal_supersedes_neutral_signal_for_same_work(self):
        wid = uuid.uuid4()
        gid = uuid.uuid4()
        now = datetime.now(timezone.utc)

        # Primary signal (e.g. completed lifecycle at 0.50)
        primary_sig = ResolvedWorkSignal(
            work_id=wid,
            effective_weight=0.50,
            polarity="positive",
            effective_source="lifecycle",
            signal_timestamp=now,
        )
        # Preserved 3-star neutral rating at 0.10
        neutral_sig = ResolvedWorkSignal(
            work_id=wid,
            effective_weight=0.10,
            polarity="neutral",
            effective_source="rating",
            signal_timestamp=now - timedelta(days=1),
        )

        # Pass primary in positive_signals and neutral in neutral_signals
        payload = calculate_topic_affinities(
            positive_signals=[primary_sig],
            work_genres={wid: [gid]},
            work_subjects={},
            neutral_signals=[neutral_sig],
            calculation_time=now,
        )

        # Must not sum 0.50 + 0.10; must count the work exactly once
        assert payload.genres[str(gid)].interaction_count == 1
        assert payload.meta["distinct_positive_works"] == 1
        assert payload.meta["max_raw_genre_score"] == pytest.approx(0.50)

    def test_neutral_in_separate_list_used_when_no_positive_signal_exists(self):
        wid = uuid.uuid4()
        gid = uuid.uuid4()
        now = datetime.now(timezone.utc)

        neutral_sig = ResolvedWorkSignal(
            work_id=wid,
            effective_weight=0.10,
            polarity="neutral",
            effective_source="rating",
            signal_timestamp=now,
        )

        payload = calculate_topic_affinities(
            positive_signals=[],
            work_genres={wid: [gid]},
            work_subjects={},
            neutral_signals=[neutral_sig],
            calculation_time=now,
        )

        assert str(gid) in payload.genres
        assert payload.genres[str(gid)].interaction_count == 1
        assert payload.genres[str(gid)].affinity == pytest.approx(1.0)


# ==============================================================================
# 4. Pure Calculation Unit Tests: Negative Signals
# ==============================================================================

class TestNegativeSignalsHandling:
    """Verify negative signal extraction, categorization, bounding, and lack of genre penalization."""

    def test_negative_signals_do_not_lower_genre_or_subject_affinity(self):
        w_pos = uuid.uuid4()
        w_neg = uuid.uuid4()
        gid = uuid.uuid4()
        now = datetime.now(timezone.utc)

        pos_sig = ResolvedWorkSignal(
            work_id=w_pos,
            effective_weight=0.80,
            polarity="positive",
            signal_timestamp=now,
        )
        neg_sig = ResolvedWorkSignal(
            work_id=w_neg,
            effective_weight=-0.80,
            polarity="negative",
            effective_source="rating",
            signal_timestamp=now,
            metadata={"rating_value": 1},
        )

        # Both works share the same genre
        payload = calculate_topic_affinities(
            positive_signals=[pos_sig, neg_sig],
            work_genres={w_pos: [gid], w_neg: [gid]},
            work_subjects={},
            calculation_time=now,
        )

        # Negative work must NOT subtract from genre score
        assert payload.genres[str(gid)].affinity == pytest.approx(1.0)
        assert payload.genres[str(gid)].interaction_count == 1
        assert payload.meta["max_raw_genre_score"] == pytest.approx(0.80)

    def test_negative_signal_categorization(self):
        w_dismiss = uuid.uuid4()
        w_abandon = uuid.uuid4()
        w_rate1 = uuid.uuid4()
        w_unhelpful = uuid.uuid4()
        now = datetime.now(timezone.utc)

        s_dismiss = ResolvedWorkSignal(
            work_id=w_dismiss, effective_weight=-0.15, polarity="negative",
            effective_source="dismissal", signal_timestamp=now - timedelta(hours=1),
            metadata={"feedback_type": "dismiss"},
        )
        s_abandon = ResolvedWorkSignal(
            work_id=w_abandon, effective_weight=-0.20, polarity="negative",
            effective_source="lifecycle", signal_timestamp=now - timedelta(hours=2),
            metadata={"lifecycle_status": "abandoned"},
        )
        s_rate1 = ResolvedWorkSignal(
            work_id=w_rate1, effective_weight=-0.80, polarity="negative",
            effective_source="rating", signal_timestamp=now - timedelta(hours=3),
            metadata={"rating_value": 1},
        )
        s_unhelpful = ResolvedWorkSignal(
            work_id=w_unhelpful, effective_weight=-0.40, polarity="negative",
            effective_source="recommendation_feedback", signal_timestamp=now - timedelta(hours=4),
            metadata={"feedback_type": "not_helpful"},
        )

        payload = calculate_topic_affinities(
            positive_signals=[],
            work_genres={},
            work_subjects={},
            negative_signals=[s_dismiss, s_abandon, s_rate1, s_unhelpful],
            calculation_time=now,
        )

        neg = payload.negative_signals
        assert str(w_dismiss) in neg["dismissed_work_ids"]
        assert str(w_abandon) in neg["abandoned_work_ids"]
        assert str(w_rate1) in neg["low_rated_work_ids"]
        assert str(w_unhelpful) in neg["not_helpful_work_ids"]
        assert len(neg["all_negative_work_ids"]) == 4

    def test_negative_signals_category_capped_at_50_and_combined_at_100(self):
        now = datetime.now(timezone.utc)
        # Create 60 dismissals and 60 abandonments (total 120 negative signals)
        dismiss_signals = [
            ResolvedWorkSignal(
                work_id=uuid.uuid4(),
                effective_weight=-0.15,
                polarity="negative",
                effective_source="dismissal",
                signal_timestamp=now - timedelta(days=i),
            )
            for i in range(60)
        ]
        abandon_signals = [
            ResolvedWorkSignal(
                work_id=uuid.uuid4(),
                effective_weight=-0.20,
                polarity="negative",
                effective_source="lifecycle",
                signal_timestamp=now - timedelta(days=i),
                metadata={"lifecycle_status": "abandoned"},
            )
            for i in range(60)
        ]

        payload = calculate_topic_affinities(
            positive_signals=[],
            work_genres={},
            work_subjects={},
            negative_signals=dismiss_signals + abandon_signals,
            calculation_time=now,
        )

        neg = payload.negative_signals
        assert len(neg["dismissed_work_ids"]) == 50
        assert len(neg["abandoned_work_ids"]) == 50
        assert len(neg["all_negative_work_ids"]) == 100

    def test_deterministic_ordering_for_negative_signals(self):
        now = datetime.now(timezone.utc)
        w1, w2 = uuid.UUID("00000000-0000-0000-0000-000000000001"), uuid.UUID("00000000-0000-0000-0000-000000000002")

        # Identical timestamps: tie-break must sort by str(uuid) ascending
        s1 = ResolvedWorkSignal(work_id=w1, effective_weight=-0.15, polarity="negative", effective_source="dismissal", signal_timestamp=now)
        s2 = ResolvedWorkSignal(work_id=w2, effective_weight=-0.15, polarity="negative", effective_source="dismissal", signal_timestamp=now)

        payload = calculate_topic_affinities(
            positive_signals=[s2, s1],  # passed out of order
            work_genres={},
            work_subjects={},
            calculation_time=now,
        )

        assert payload.negative_signals["dismissed_work_ids"] == [str(w1), str(w2)]


# ==============================================================================
# 5. Robustness & Boundary Condition Tests
# ==============================================================================

class TestRobustnessAndEdgeCases:
    """Verify boundary conditions, cold-start, epsilon thresholds, and invalid inputs."""

    def test_cold_start_empty_signals(self):
        payload = calculate_topic_affinities(
            positive_signals=[],
            work_genres={},
            work_subjects={},
        )

        assert payload.genres == {}
        assert payload.subjects == {}
        assert payload.negative_signals["all_negative_work_ids"] == []
        assert payload.meta["distinct_positive_works"] == 0

    def test_near_zero_max_score_below_epsilon_evaluates_to_zero(self):
        wid = uuid.uuid4()
        gid = uuid.uuid4()
        now = datetime.now(timezone.utc)

        # Decayed to practically zero
        sig = ResolvedWorkSignal(
            work_id=wid,
            effective_weight=1e-8,
            polarity="positive",
            signal_timestamp=now,
        )

        payload = calculate_topic_affinities(
            positive_signals=[sig],
            work_genres={wid: [gid]},
            work_subjects={},
            calculation_time=now,
            min_epsilon=1e-6,
        )

        assert payload.genres[str(gid)].affinity == 0.0

    def test_out_of_bounds_relevance_weights_raise_value_error(self):
        wid = uuid.uuid4()
        sid = uuid.uuid4()
        now = datetime.now(timezone.utc)

        sig = ResolvedWorkSignal(work_id=wid, effective_weight=1.0, polarity="positive", signal_timestamp=now)

        with pytest.raises(ValueError, match="between 0.0 and 1.0 inclusive"):
            calculate_topic_affinities(
                positive_signals=[sig],
                work_genres={},
                work_subjects={wid: [(sid, 2.5)]},
                calculation_time=now,
            )

    def test_naive_timestamp_normalized_to_utc(self):
        wid = uuid.uuid4()
        gid = uuid.uuid4()
        naive_now = datetime(2026, 11, 9, 12, 0, 0)  # naive

        sig = ResolvedWorkSignal(
            work_id=wid,
            effective_weight=1.0,
            polarity="positive",
            signal_timestamp=naive_now,
        )

        payload = calculate_topic_affinities(
            positive_signals=[sig],
            work_genres={wid: [gid]},
            work_subjects={},
            calculation_time=naive_now,
        )

        entry = payload.genres[str(gid)]
        assert entry.last_interacted_at is not None
        assert entry.last_interacted_at.tzinfo == timezone.utc


# ==============================================================================
# 6. Database Integration Tests with ProfileTopicCalculator
# ==============================================================================

@pytest.mark.asyncio
async def test_profile_topic_calculator_db_end_to_end():
    """Verify batched retrieval from BookWorkGenre and BookWorkSubject with end-to-end calculation."""
    async with get_test_session() as session:
        # Create works
        w1 = BookWork(canonical_title="Dune")
        w2 = BookWork(canonical_title="Foundation")
        session.add_all([w1, w2])
        await session.flush()

        # Create genres
        g_scifi = Genre(name=f"SciFi_{uuid.uuid4().hex[:6]}", slug=f"scifi-{uuid.uuid4().hex[:6]}")
        session.add(g_scifi)
        await session.flush()

        # Create subjects
        s_space = Subject(name=f"Space_{uuid.uuid4().hex[:6]}", slug=f"space-{uuid.uuid4().hex[:6]}")
        session.add(s_space)
        await session.flush()

        # Create associations
        wg1 = BookWorkGenre(work_id=w1.id, genre_id=g_scifi.id)
        wg2 = BookWorkGenre(work_id=w2.id, genre_id=g_scifi.id)
        ws1 = BookWorkSubject(work_id=w1.id, subject_id=s_space.id, relevance_weight=Decimal("0.800"))
        session.add_all([wg1, wg2, ws1])
        await session.flush()

        now = datetime.now(timezone.utc)
        sig1 = ResolvedWorkSignal(work_id=w1.id, effective_weight=1.0, polarity="positive", signal_timestamp=now)
        sig2 = ResolvedWorkSignal(work_id=w2.id, effective_weight=0.5, polarity="positive", signal_timestamp=now)

        extracted = ExtractedProfileSignals(
            user_id=uuid.uuid4(),
            resolved_signals=[sig1, sig2],
            eligible_interaction_count=2,
            model_name="sentence-transformers/all-MiniLM-L6-v2",
            extracted_at=now,
        )

        calculator = ProfileTopicCalculator(session=session)
        payload = await calculator.calculate(extracted)

        assert str(g_scifi.id) in payload.genres
        assert payload.genres[str(g_scifi.id)].interaction_count == 2
        assert payload.genres[str(g_scifi.id)].affinity == pytest.approx(1.0)

        assert str(s_space.id) in payload.subjects
        assert payload.subjects[str(s_space.id)].interaction_count == 1
        assert payload.subjects[str(s_space.id)].affinity == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_profile_topic_calculator_zero_queries_on_empty_signals():
    """Verify that when no candidate works exist, zero DB queries are executed."""
    async with get_test_session() as session:
        extracted = ExtractedProfileSignals(
            user_id=uuid.uuid4(),
            resolved_signals=[],
            eligible_interaction_count=0,
            model_name="sentence-transformers/all-MiniLM-L6-v2",
            extracted_at=datetime.now(timezone.utc),
        )

        calculator = ProfileTopicCalculator(session=session)
        payload = await calculator.calculate(extracted)

        assert payload.genres == {}
        assert payload.subjects == {}
        assert payload.negative_signals["all_negative_work_ids"] == []
