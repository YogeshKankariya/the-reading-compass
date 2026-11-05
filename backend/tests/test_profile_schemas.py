import math
import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from backend.core.config import Settings
from backend.schemas.profile import (
    ExplicitPreferenceInput,
    ExtractedInteractionSignal,
    InferredTopicsPayload,
    ProfileCalculationResult,
    ProfileRefreshResult,
    ResolvedWorkSignal,
    TopicAffinityEntry,
)


# ==============================================================================
# Configuration Tests
# ==============================================================================

class TestProfileConfiguration:
    """Verify configuration defaults, overrides, and validation for profile generation."""

    def test_configuration_defaults(self):
        settings = Settings(DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/test_db")
        assert settings.PROFILE_EMBEDDING_MODEL_NAME == "sentence-transformers/all-MiniLM-L6-v2"
        assert settings.PROFILE_EMBEDDING_DIMENSION == 384
        assert settings.PROFILE_EMBEDDING_MODEL_REVISION is None
        assert settings.PROFILE_RECENCY_HALF_LIFE_DAYS == 180.0
        assert settings.PROFILE_NEGATIVE_DISCOUNT_ALPHA == 0.25
        assert settings.PROFILE_BLENDING_M0 == 5.0
        assert settings.PROFILE_NEAR_ZERO_EPSILON == 1e-6
        assert settings.PROFILE_REFRESH_THRESHOLD == 5

        # Signal weights defaults
        weights = settings.get_signal_weights()
        assert weights["rate_5"] == 1.00
        assert weights["rate_4"] == 0.75
        assert weights["rate_3"] == 0.10
        assert weights["rate_2"] == -0.40
        assert weights["rate_1"] == -0.80
        assert weights["recommendation_helpful"] == 0.50
        assert weights["recommendation_not_helpful"] == -0.40
        assert weights["save"] == 0.60
        assert weights["complete"] == 0.50
        assert weights["start"] == 0.30
        assert weights["progress"] == 0.25
        assert weights["abandon"] == -0.20
        assert weights["dismiss"] == -0.15
        assert weights["recommendation_click"] == 0.15
        assert weights["click"] == 0.10
        assert weights["view"] == 0.05

    def test_configuration_environment_overrides(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/test_db")
        monkeypatch.setenv("PROFILE_EMBEDDING_DIMENSION", "768")
        monkeypatch.setenv("PROFILE_RECENCY_HALF_LIFE_DAYS", "90")
        monkeypatch.setenv("PROFILE_NEGATIVE_DISCOUNT_ALPHA", "0.35")
        monkeypatch.setenv("PROFILE_BLENDING_M0", "10")
        monkeypatch.setenv("PROFILE_REFRESH_THRESHOLD", "12")
        monkeypatch.setenv("PROFILE_WEIGHT_RATING_5", "1.5")
        monkeypatch.setenv("PROFILE_WEIGHT_DISMISS", "-0.3")

        settings = Settings()
        assert settings.PROFILE_EMBEDDING_DIMENSION == 768
        assert settings.PROFILE_RECENCY_HALF_LIFE_DAYS == 90.0
        assert settings.PROFILE_NEGATIVE_DISCOUNT_ALPHA == 0.35
        assert settings.PROFILE_BLENDING_M0 == 10.0
        assert settings.PROFILE_REFRESH_THRESHOLD == 12
        assert settings.PROFILE_WEIGHT_RATING_5 == 1.5
        assert settings.PROFILE_WEIGHT_DISMISS == -0.3

    def test_unverified_model_revision_is_explicitly_none(self):
        settings = Settings(
            DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/test_db",
            PROFILE_EMBEDDING_MODEL_REVISION=None,
        )
        assert settings.PROFILE_EMBEDDING_MODEL_REVISION is None

        # Empty string should normalize to None
        settings_empty = Settings(
            DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/test_db",
            PROFILE_EMBEDDING_MODEL_REVISION="   ",
        )
        assert settings_empty.PROFILE_EMBEDDING_MODEL_REVISION is None

    @pytest.mark.parametrize(
        "invalid_field,invalid_value",
        [
            ("PROFILE_EMBEDDING_DIMENSION", 0),
            ("PROFILE_EMBEDDING_DIMENSION", -384),
            ("PROFILE_RECENCY_HALF_LIFE_DAYS", 0.0),
            ("PROFILE_RECENCY_HALF_LIFE_DAYS", -10.0),
            ("PROFILE_RECENCY_HALF_LIFE_DAYS", float("nan")),
            ("PROFILE_RECENCY_HALF_LIFE_DAYS", float("inf")),
            ("PROFILE_BLENDING_M0", 0.0),
            ("PROFILE_BLENDING_M0", -5.0),
            ("PROFILE_BLENDING_M0", float("nan")),
            ("PROFILE_NEAR_ZERO_EPSILON", 0.0),
            ("PROFILE_NEAR_ZERO_EPSILON", -1e-6),
            ("PROFILE_NEAR_ZERO_EPSILON", float("nan")),
            ("PROFILE_REFRESH_THRESHOLD", 0),
            ("PROFILE_REFRESH_THRESHOLD", -5),
            ("PROFILE_NEGATIVE_DISCOUNT_ALPHA", -0.1),
            ("PROFILE_NEGATIVE_DISCOUNT_ALPHA", 1.05),
            ("PROFILE_NEGATIVE_DISCOUNT_ALPHA", float("nan")),
            ("PROFILE_NEGATIVE_DISCOUNT_ALPHA", float("inf")),
            ("PROFILE_WEIGHT_RATING_5", float("nan")),
            ("PROFILE_WEIGHT_RATING_5", float("inf")),
            ("PROFILE_WEIGHT_DISMISS", float("-inf")),
        ],
    )
    def test_invalid_configuration_values_rejected(self, invalid_field, invalid_value):
        kwargs = {
            "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/test_db",
            invalid_field: invalid_value,
        }
        with pytest.raises(ValidationError):
            Settings(**kwargs)


# ==============================================================================
# Schema Tests
# ==============================================================================

class TestExtractedInteractionSignalSchema:
    """Verify validation of extracted interaction signals."""

    def test_valid_minimal_and_full_extracted_signal(self):
        uid = uuid.uuid4()
        iid = uuid.uuid4()
        wid = uuid.uuid4()

        # Minimal valid signal
        s1 = ExtractedInteractionSignal(
            interaction_id=iid,
            user_id=uid,
            interaction_type="view",
            created_at=datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc),
        )
        assert s1.interaction_id == iid
        assert s1.work_id is None
        assert s1.rating_value is None
        assert s1.context is None

        # Full valid signal
        req_id = uuid.uuid4()
        item_id = uuid.uuid4()
        src_id = uuid.uuid4()
        s2 = ExtractedInteractionSignal(
            interaction_id=iid,
            user_id=uid,
            work_id=wid,
            interaction_type="rate",
            rating_value=5,
            created_at=datetime.now(timezone.utc),
            recommendation_request_id=req_id,
            recommendation_item_id=item_id,
            source_record_id=src_id,
            context={"source": "recommendation"},
        )
        assert s2.rating_value == 5
        assert s2.recommendation_request_id == req_id
        assert s2.context["source"] == "recommendation"

    def test_rating_value_bounds(self):
        uid = uuid.uuid4()
        iid = uuid.uuid4()
        now = datetime.now(timezone.utc)

        # Valid ratings 1 to 5
        for r in range(1, 6):
            sig = ExtractedInteractionSignal(
                interaction_id=iid,
                user_id=uid,
                interaction_type="rate",
                rating_value=r,
                created_at=now,
            )
            assert sig.rating_value == r

        # Invalid ratings
        with pytest.raises(ValidationError):
            ExtractedInteractionSignal(
                interaction_id=iid,
                user_id=uid,
                interaction_type="rate",
                rating_value=0,
                created_at=now,
            )

        with pytest.raises(ValidationError):
            ExtractedInteractionSignal(
                interaction_id=iid,
                user_id=uid,
                interaction_type="rate",
                rating_value=6,
                created_at=now,
            )

    def test_naive_datetime_converted_to_utc(self):
        uid = uuid.uuid4()
        iid = uuid.uuid4()
        naive = datetime(2026, 11, 4, 10, 30, 0)
        sig = ExtractedInteractionSignal(
            interaction_id=iid,
            user_id=uid,
            interaction_type="click",
            created_at=naive,
        )
        assert sig.created_at.tzinfo is not None
        assert sig.created_at.tzinfo == timezone.utc

    def test_datetime_normalization_across_offsets(self):
        from datetime import timedelta

        uid = uuid.uuid4()
        iid = uuid.uuid4()

        # Naive datetime
        naive = datetime(2026, 11, 5, 12, 0, 0)
        s_naive = ExtractedInteractionSignal(
            interaction_id=iid,
            user_id=uid,
            interaction_type="view",
            created_at=naive,
        )
        assert s_naive.created_at.tzinfo == timezone.utc
        assert s_naive.created_at == datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)

        # UTC-aware datetime
        utc_dt = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)
        s_utc = ExtractedInteractionSignal(
            interaction_id=iid,
            user_id=uid,
            interaction_type="view",
            created_at=utc_dt,
        )
        assert s_utc.created_at.tzinfo == timezone.utc
        assert s_utc.created_at == utc_dt

        # Positive offset (+05:30)
        tz_plus_530 = timezone(timedelta(hours=5, minutes=30))
        dt_plus = datetime(2026, 11, 5, 17, 30, 0, tzinfo=tz_plus_530)
        s_plus = ExtractedInteractionSignal(
            interaction_id=iid,
            user_id=uid,
            interaction_type="view",
            created_at=dt_plus,
        )
        assert s_plus.created_at.tzinfo == timezone.utc
        assert s_plus.created_at == datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)

        # Negative offset (-04:00)
        tz_minus_4 = timezone(timedelta(hours=-4))
        dt_minus = datetime(2026, 11, 5, 8, 0, 0, tzinfo=tz_minus_4)
        s_minus = ExtractedInteractionSignal(
            interaction_id=iid,
            user_id=uid,
            interaction_type="view",
            created_at=dt_minus,
        )
        assert s_minus.created_at.tzinfo == timezone.utc
        assert s_minus.created_at == datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)

        # All represent the exact same UTC instant
        assert s_naive.created_at == s_utc.created_at == s_plus.created_at == s_minus.created_at


class TestResolvedWorkSignalSchema:
    """Verify resolved work-level signal schema."""

    def test_valid_resolved_signals(self):
        wid = uuid.uuid4()
        now = datetime.now(timezone.utc)

        # Positive signal
        pos = ResolvedWorkSignal(
            work_id=wid,
            effective_weight=1.0,
            polarity="positive",
            contributing_interaction_ids=[uuid.uuid4(), uuid.uuid4()],
            effective_source="rating",
            signal_timestamp=now,
        )
        assert pos.effective_weight == 1.0
        assert pos.polarity == "positive"
        assert len(pos.contributing_interaction_ids) == 2

        # Negative signal
        neg = ResolvedWorkSignal(
            work_id=wid,
            effective_weight=-0.8,
            polarity="negative",
            signal_timestamp=now,
        )
        assert neg.effective_weight == -0.8
        assert neg.polarity == "negative"

        # Neutral signal
        neu = ResolvedWorkSignal(
            work_id=wid,
            effective_weight=0.1,
            polarity="neutral",
            signal_timestamp=now,
        )
        assert neu.polarity == "neutral"

    def test_reject_non_finite_weight(self):
        wid = uuid.uuid4()
        now = datetime.now(timezone.utc)
        with pytest.raises(ValidationError):
            ResolvedWorkSignal(
                work_id=wid,
                effective_weight=float("nan"),
                polarity="positive",
                signal_timestamp=now,
            )

    @pytest.mark.parametrize(
        "polarity,effective_weight",
        [
            ("positive", 1.0),
            ("positive", 0.05),
            ("positive", 0.0001),
            ("negative", -0.8),
            ("negative", -0.15),
            ("negative", -0.0001),
            ("neutral", 0.0),
            ("neutral", 0.10),
            ("neutral", 0.50),
        ],
    )
    def test_valid_polarity_weight_combinations(self, polarity, effective_weight):
        sig = ResolvedWorkSignal(
            work_id=uuid.uuid4(),
            effective_weight=effective_weight,
            polarity=polarity,
            signal_timestamp=datetime.now(timezone.utc),
        )
        assert sig.polarity == polarity
        assert sig.effective_weight == effective_weight

    @pytest.mark.parametrize(
        "polarity,effective_weight",
        [
            ("positive", 0.0),
            ("positive", -0.5),
            ("positive", -0.01),
            ("negative", 0.0),
            ("negative", 0.5),
            ("negative", 0.01),
            ("neutral", -0.1),
            ("neutral", -0.4),
            ("neutral", -0.0001),
        ],
    )
    def test_contradictory_polarity_weight_combinations_rejected(
        self, polarity, effective_weight
    ):
        with pytest.raises(ValidationError):
            ResolvedWorkSignal(
                work_id=uuid.uuid4(),
                effective_weight=effective_weight,
                polarity=polarity,
                signal_timestamp=datetime.now(timezone.utc),
            )

    def test_signal_timestamp_normalization(self):
        from datetime import timedelta

        wid = uuid.uuid4()
        naive = datetime(2026, 11, 5, 12, 0, 0)
        sig = ResolvedWorkSignal(
            work_id=wid,
            effective_weight=0.5,
            polarity="positive",
            signal_timestamp=naive,
        )
        assert sig.signal_timestamp.tzinfo == timezone.utc
        assert sig.signal_timestamp == datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)

        # Aware with offset
        tz_plus = timezone(timedelta(hours=5, minutes=30))
        sig_aware = ResolvedWorkSignal(
            work_id=wid,
            effective_weight=0.5,
            polarity="positive",
            signal_timestamp=datetime(2026, 11, 5, 17, 30, 0, tzinfo=tz_plus),
        )
        assert sig_aware.signal_timestamp.tzinfo == timezone.utc
        assert sig_aware.signal_timestamp == datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)


class TestExplicitPreferenceInputSchema:
    """Verify explicit preference input schema."""

    def test_valid_explicit_preferences(self):
        pref = ExplicitPreferenceInput(
            preference_type="genre",
            preference_value="Science Fiction",
            preference_weight=0.85,
            source="onboarding",
        )
        assert pref.preference_type == "genre"
        assert pref.preference_value == "Science Fiction"
        assert pref.preference_weight == 0.85

    @pytest.mark.parametrize("invalid_weight", [-0.1, 1.1, float("nan"), float("inf")])
    def test_invalid_preference_weight_rejected(self, invalid_weight):
        with pytest.raises(ValidationError):
            ExplicitPreferenceInput(
                preference_type="subject",
                preference_value="Artificial Intelligence",
                preference_weight=invalid_weight,
            )


class TestProfileCalculationResultSchema:
    """Verify profile calculation result payload schema and vector validation."""

    def test_valid_result_with_vector(self):
        vec = [0.1] * 384
        res = ProfileCalculationResult(
            interest_vector=vec,
            inferred_topics={"genres": {"Sci-Fi": {"affinity": 0.9}}},
            interaction_count=10,
            distinct_positive_works=7,
            model_name="sentence-transformers/all-MiniLM-L6-v2",
            model_revision=None,
        )
        assert res.interest_vector == vec
        assert res.interaction_count == 10
        assert res.distinct_positive_works == 7
        assert res.model_revision is None

        # Validate dimension matches 384
        res.validate_dimension(384)

    def test_dimension_mismatch_raises_error(self):
        vec = [0.1] * 128
        res = ProfileCalculationResult(
            interest_vector=vec,
            interaction_count=5,
            model_name="sentence-transformers/all-MiniLM-L6-v2",
        )
        with pytest.raises(ValueError, match="does not match expected 384"):
            res.validate_dimension(384)

    def test_cold_start_null_vector(self):
        res = ProfileCalculationResult(
            interest_vector=None,
            inferred_topics=None,
            interaction_count=0,
            distinct_positive_works=0,
            model_name="sentence-transformers/all-MiniLM-L6-v2",
            model_revision=None,
        )
        assert res.interest_vector is None
        # Validating dimension on None does not raise
        res.validate_dimension(384)

    def test_reject_empty_vector_list(self):
        with pytest.raises(ValidationError, match="cannot be an empty list"):
            ProfileCalculationResult(
                interest_vector=[],
                interaction_count=0,
                model_name="sentence-transformers/all-MiniLM-L6-v2",
            )

    def test_reject_non_finite_vector_elements(self):
        bad_vec = [0.1] * 383 + [float("nan")]
        with pytest.raises(ValidationError, match="must be a finite float"):
            ProfileCalculationResult(
                interest_vector=bad_vec,
                interaction_count=1,
                model_name="sentence-transformers/all-MiniLM-L6-v2",
            )

    def test_negative_interaction_counts_rejected(self):
        with pytest.raises(ValidationError):
            ProfileCalculationResult(
                interest_vector=None,
                interaction_count=-1,
                model_name="sentence-transformers/all-MiniLM-L6-v2",
            )

    def test_inferred_topics_payload_types(self):
        # 1. Typed InferredTopicsPayload instance accepted
        typed_payload = InferredTopicsPayload(
            genres={"Science Fiction": TopicAffinityEntry(affinity=0.85, interaction_count=4)},
            subjects={"Cyberpunk": TopicAffinityEntry(affinity=0.75, interaction_count=2)},
            negative_signals={"dismissed_work_ids": ["work-abc"]},
            meta={"distinct_works": 5},
        )
        res_typed = ProfileCalculationResult(
            interest_vector=[0.1] * 384,
            inferred_topics=typed_payload,
            interaction_count=6,
            model_name="sentence-transformers/all-MiniLM-L6-v2",
        )
        assert isinstance(res_typed.inferred_topics, InferredTopicsPayload)
        assert "Science Fiction" in res_typed.inferred_topics.genres
        assert res_typed.inferred_topics.genres["Science Fiction"].affinity == 0.85

        # 2. Raw JSON-compatible dict accepted
        raw_dict = {
            "genres": {"Fantasy": {"affinity": 0.9, "interaction_count": 3}},
            "subjects": {"Magic": {"affinity": 0.6, "interaction_count": 1}},
            "meta": {"source": "legacy"},
        }
        res_dict = ProfileCalculationResult(
            interest_vector=[0.2] * 384,
            inferred_topics=raw_dict,
            interaction_count=4,
            model_name="sentence-transformers/all-MiniLM-L6-v2",
        )
        assert res_dict.inferred_topics is not None
        if isinstance(res_dict.inferred_topics, InferredTopicsPayload):
            assert "Fantasy" in res_dict.inferred_topics.genres
            assert res_dict.inferred_topics.genres["Fantasy"].affinity == 0.9
        else:
            assert res_dict.inferred_topics["genres"]["Fantasy"]["affinity"] == 0.9

        # 3. None accepted (e.g. cold-start or empty)
        res_none = ProfileCalculationResult(
            interest_vector=None,
            inferred_topics=None,
            interaction_count=0,
            model_name="sentence-transformers/all-MiniLM-L6-v2",
        )
        assert res_none.inferred_topics is None


class TestProfileRefreshResultSchema:
    """Verify profile refresh decision and execution result schema."""

    def test_valid_refresh_results(self):
        r1 = ProfileRefreshResult(
            refresh_required=True,
            refresh_completed=True,
            reason="threshold_reached",
            previous_interaction_count=5,
            current_interaction_count=10,
            calculated_at=datetime.now(timezone.utc),
        )
        assert r1.refresh_required is True
        assert r1.refresh_completed is True
        assert r1.reason == "threshold_reached"

        r2 = ProfileRefreshResult(
            refresh_required=False,
            refresh_completed=False,
            reason="none",
            previous_interaction_count=3,
            current_interaction_count=3,
        )
        assert r2.refresh_required is False
        assert r2.reason == "none"

    def test_invalid_reason_rejected(self):
        with pytest.raises(ValidationError):
            ProfileRefreshResult(
                refresh_required=True,
                refresh_completed=False,
                reason="unauthorized_reason",  # type: ignore
            )


class TestTopicAffinityPayloadSchemas:
    """Verify topic affinity and structured payload schemas."""

    def test_valid_topic_affinity_entry(self):
        entry = TopicAffinityEntry(
            affinity=0.82,
            interaction_count=4,
            last_interacted_at=datetime.now(timezone.utc),
        )
        assert entry.affinity == 0.82
        assert entry.interaction_count == 4

    def test_topic_affinity_entry_datetime_normalization(self):
        from datetime import timedelta

        # Naive datetime
        naive = datetime(2026, 11, 5, 14, 0, 0)
        e1 = TopicAffinityEntry(
            affinity=0.5,
            interaction_count=1,
            last_interacted_at=naive,
        )
        assert e1.last_interacted_at.tzinfo == timezone.utc
        assert e1.last_interacted_at == datetime(2026, 11, 5, 14, 0, 0, tzinfo=timezone.utc)

        # Aware datetime with negative offset
        tz_neg = timezone(timedelta(hours=-5))
        aware = datetime(2026, 11, 5, 9, 0, 0, tzinfo=tz_neg)
        e2 = TopicAffinityEntry(
            affinity=0.5,
            interaction_count=1,
            last_interacted_at=aware,
        )
        assert e2.last_interacted_at.tzinfo == timezone.utc
        assert e2.last_interacted_at == datetime(2026, 11, 5, 14, 0, 0, tzinfo=timezone.utc)

    def test_invalid_topic_affinity_bounds(self):
        with pytest.raises(ValidationError):
            TopicAffinityEntry(affinity=1.5, interaction_count=1)

        with pytest.raises(ValidationError):
            TopicAffinityEntry(affinity=-0.1, interaction_count=1)

        with pytest.raises(ValidationError):
            TopicAffinityEntry(affinity=0.5, interaction_count=-1)

    def test_inferred_topics_payload_container(self):
        payload = InferredTopicsPayload(
            genres={
                "Sci-Fi": TopicAffinityEntry(affinity=0.9, interaction_count=5),
            },
            subjects={
                "AI": TopicAffinityEntry(affinity=0.85, interaction_count=3),
            },
            negative_signals={"dismissed_work_ids": ["work-1", "work-2"]},
            meta={"distinct_works": 6},
        )
        assert "Sci-Fi" in payload.genres
        assert payload.genres["Sci-Fi"].affinity == 0.9
        assert len(payload.negative_signals["dismissed_work_ids"]) == 2
