import math
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from backend.core.config import Settings
from backend.schemas.profile import ExplicitPreferenceInput, ResolvedWorkSignal
from backend.services.profile.vector_builder import (
    BehavioralVectorResult,
    BlendedVectorResult,
    ProfileVectorBuildResult,
    blend_profile_vectors,
    build_behavioral_vector,
    build_explicit_preference_vector,
    build_user_interest_profile_vector,
    calculate_recency_decay,
    l2_norm,
    normalize_vector,
    validate_and_normalize_vector,
    validate_vector,
)


# ==============================================================================
# Synthetic Vector Helpers
# ==============================================================================

def make_axis_vector(dim: int = 384, axis: int = 0) -> list[float]:
    """Create a unit vector with 1.0 along the specified axis and 0.0 elsewhere."""
    vec = [0.0] * dim
    vec[axis % dim] = 1.0
    return vec


def make_dense_vector(dim: int = 384, phase: float = 0.0) -> list[float]:
    """Create a deterministic synthetic vector with non-zero elements and normalize it."""
    raw = [math.sin(i * 0.1 + phase) + math.cos(i * 0.05) for i in range(dim)]
    norm = math.sqrt(sum(x * x for x in raw))
    return [x / norm for x in raw]


# ==============================================================================
# Vector Validation & Normalization Tests
# ==============================================================================

class TestVectorValidation:
    """Tests for vector validation, L2-norm, and unit normalization."""

    def test_valid_vector_returns_list_of_floats(self):
        vec = [1, 2.5, 3]
        result = validate_vector(vec, expected_dim=3)
        assert result == [1.0, 2.5, 3.0]
        assert all(isinstance(x, float) for x in result)

    def test_dimension_mismatch_raises_value_error(self):
        vec = [0.1] * 10
        with pytest.raises(ValueError, match="Vector dimension mismatch"):
            validate_vector(vec, expected_dim=384)

    def test_empty_vector_raises_value_error(self):
        with pytest.raises(ValueError, match="Vector cannot be empty"):
            validate_vector([], expected_dim=384)

    def test_none_vector_raises_value_error(self):
        with pytest.raises(ValueError, match="Vector cannot be None"):
            validate_vector(None, expected_dim=384)  # type: ignore

    @pytest.mark.parametrize(
        "bad_val",
        [float("nan"), float("inf"), float("-inf")],
    )
    def test_non_finite_values_raise_value_error(self, bad_val):
        vec = [0.1] * 383 + [bad_val]
        with pytest.raises(ValueError, match="non-finite"):
            validate_vector(vec, expected_dim=384)

    @pytest.mark.parametrize(
        "bad_type",
        ["text", None, True, False, [1.0]],
    )
    def test_non_numeric_types_raise_value_error(self, bad_type):
        vec = [0.1] * 383 + [bad_type]
        with pytest.raises(ValueError, match="must be a numeric float"):
            validate_vector(vec, expected_dim=384)

    def test_l2_norm_calculation(self):
        # 3-4-5 triangle
        assert l2_norm([3.0, 4.0]) == pytest.approx(5.0)
        # Unit vector norm is 1.0
        assert l2_norm(make_axis_vector(384, 0)) == pytest.approx(1.0)

    def test_normalize_vector_success(self):
        vec = [3.0, 4.0]
        normed = normalize_vector(vec, epsilon=1e-6)
        assert normed is not None
        assert normed[0] == pytest.approx(0.6)
        assert normed[1] == pytest.approx(0.8)
        assert l2_norm(normed) == pytest.approx(1.0)

    def test_normalize_near_zero_returns_none(self):
        zero_vec = [0.0] * 384
        assert normalize_vector(zero_vec, epsilon=1e-6) is None

        tiny_vec = [1e-7] * 384
        assert l2_norm(tiny_vec) < 1e-5
        assert normalize_vector(tiny_vec, epsilon=1e-4) is None

    def test_validate_and_normalize_vector_convention(self):
        vec = [2.0] * 384
        normed = validate_and_normalize_vector(vec, expected_dim=384, epsilon=1e-6)
        assert len(normed) == 384
        assert l2_norm(normed) == pytest.approx(1.0, rel=1e-6)

    def test_validate_and_normalize_near_zero_raises_value_error(self):
        zero_vec = [0.0] * 384
        with pytest.raises(ValueError, match="below near-zero threshold"):
            validate_and_normalize_vector(zero_vec, expected_dim=384, epsilon=1e-6)


# ==============================================================================
# Explicit Preference Vector Tests
# ==============================================================================

class TestExplicitPreferenceVector:
    """Tests for explicit preference centroid construction and L2-normalization."""

    def test_single_explicit_preference(self):
        e1 = make_dense_vector(384, phase=0.0)
        result = build_explicit_preference_vector(
            preferences=[(e1, 1.0)],
            expected_dim=384,
        )
        assert result is not None
        assert l2_norm(result) == pytest.approx(1.0)
        assert result == pytest.approx(e1, rel=1e-5, abs=1e-7)

    def test_multiple_weighted_preferences(self):
        e0 = make_axis_vector(384, axis=0)
        e1 = make_axis_vector(384, axis=1)

        # Equal weights 1.0 and 1.0
        res_equal = build_explicit_preference_vector(
            preferences=[(e0, 1.0), (e1, 1.0)],
            expected_dim=384,
        )
        assert res_equal is not None
        assert l2_norm(res_equal) == pytest.approx(1.0)
        # Expected: [1/sqrt(2), 1/sqrt(2), 0, ...]
        expected_val = 1.0 / math.sqrt(2.0)
        assert res_equal[0] == pytest.approx(expected_val, rel=1e-5)
        assert res_equal[1] == pytest.approx(expected_val, rel=1e-5)
        assert res_equal[2] == pytest.approx(0.0)

    def test_unequal_preference_weights(self):
        e0 = make_axis_vector(384, axis=0)
        e1 = make_axis_vector(384, axis=1)

        # Weight 3.0 on e0 and 1.0 on e1 -> centroid is [0.75, 0.25, 0...]
        # Normalized: [3 / sqrt(10), 1 / sqrt(10), ...]
        res_unequal = build_explicit_preference_vector(
            preferences=[(e0, 3.0), (e1, 1.0)],
            expected_dim=384,
        )
        assert res_unequal is not None
        assert l2_norm(res_unequal) == pytest.approx(1.0)
        assert res_unequal[0] == pytest.approx(3.0 / math.sqrt(10.0), rel=1e-5)
        assert res_unequal[1] == pytest.approx(1.0 / math.sqrt(10.0), rel=1e-5)

    def test_explicit_preference_input_schema_tuple(self):
        e0 = make_axis_vector(384, axis=0)
        pref_input = ExplicitPreferenceInput(
            preference_type="genre",
            preference_value="Sci-Fi",
            preference_weight=0.8,
        )
        result = build_explicit_preference_vector(
            preferences=[(pref_input, e0)],
            expected_dim=384,
        )
        assert result is not None
        assert result == pytest.approx(e0, rel=1e-5, abs=1e-7)

    def test_empty_preferences_returns_none(self):
        assert build_explicit_preference_vector([], expected_dim=384) is None

    def test_zero_total_weight_returns_none(self):
        e0 = make_axis_vector(384, axis=0)
        result = build_explicit_preference_vector(
            preferences=[(e0, 0.0)],
            expected_dim=384,
        )
        assert result is None

    def test_invalid_negative_weight_raises_value_error(self):
        e0 = make_axis_vector(384, axis=0)
        with pytest.raises(ValueError, match="cannot be negative"):
            build_explicit_preference_vector(
                preferences=[(e0, -0.5)],
                expected_dim=384,
            )

    def test_near_zero_cancellation_returns_none(self):
        # Two opposite vectors with equal weight cancel to zero
        v1 = make_axis_vector(384, axis=0)
        v2 = [-x for x in v1]
        result = build_explicit_preference_vector(
            preferences=[(v1, 1.0), (v2, 1.0)],
            expected_dim=384,
            epsilon=1e-6,
        )
        assert result is None


# ==============================================================================
# Recency Decay Tests
# ==============================================================================

class TestRecencyDecay:
    """Tests for exponential recency half-life calculation."""

    def test_zero_elapsed_time_preserves_base_weight(self):
        now = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)
        weight = calculate_recency_decay(
            base_weight=0.75,
            signal_timestamp=now,
            calculation_time=now,
            half_life_days=180.0,
        )
        assert weight == pytest.approx(0.75)

    def test_exactly_one_half_life(self):
        now = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)
        past = now - timedelta(days=180)
        weight = calculate_recency_decay(
            base_weight=1.0,
            signal_timestamp=past,
            calculation_time=now,
            half_life_days=180.0,
        )
        assert weight == pytest.approx(0.50, rel=1e-5)

    def test_two_half_lives(self):
        now = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)
        past = now - timedelta(days=360)
        weight = calculate_recency_decay(
            base_weight=1.0,
            signal_timestamp=past,
            calculation_time=now,
            half_life_days=180.0,
        )
        assert weight == pytest.approx(0.25, rel=1e-5)

    def test_negative_weight_preserves_sign(self):
        now = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)
        past = now - timedelta(days=180)
        weight = calculate_recency_decay(
            base_weight=-0.80,
            signal_timestamp=past,
            calculation_time=now,
            half_life_days=180.0,
        )
        assert weight == pytest.approx(-0.40, rel=1e-5)
        assert weight < 0.0

    def test_future_timestamp_clamped_to_zero_elapsed(self):
        now = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)
        future = now + timedelta(days=10)
        # Future event must not produce weight > base_weight
        weight = calculate_recency_decay(
            base_weight=0.60,
            signal_timestamp=future,
            calculation_time=now,
            half_life_days=180.0,
        )
        assert weight == pytest.approx(0.60)

    def test_timezone_offset_normalization(self):
        tz_plus = timezone(timedelta(hours=5, minutes=30))
        tz_minus = timezone(timedelta(hours=-8))

        calc_utc = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)

        # 90 days ago in UTC = 2026-08-10 12:00:00 UTC
        # In +05:30 -> 2026-08-10 17:30:00
        sig_plus = datetime(2026, 8, 10, 17, 30, 0, tzinfo=tz_plus)
        # In -08:00 -> 2026-08-10 04:00:00
        sig_minus = datetime(2026, 8, 10, 4, 0, 0, tzinfo=tz_minus)

        w1 = calculate_recency_decay(1.0, sig_plus, calc_utc, 180.0)
        w2 = calculate_recency_decay(1.0, sig_minus, calc_utc, 180.0)

        assert w1 == pytest.approx(w2, rel=1e-7)
        assert w1 == pytest.approx(math.pow(2.0, -0.5), rel=1e-5)

    @pytest.mark.parametrize("invalid_hl", [0.0, -10.0, float("nan"), float("inf")])
    def test_invalid_half_life_raises_value_error(self, invalid_hl):
        now = datetime.now(timezone.utc)
        with pytest.raises(ValueError, match="half_life_days"):
            calculate_recency_decay(1.0, now, now, half_life_days=invalid_hl)


# ==============================================================================
# Behavioral Rocchio Vector Tests
# ==============================================================================

class TestBehavioralVector:
    """Tests for pure Rocchio behavioral vector construction."""

    def test_positive_only_signals(self):
        w1 = uuid.uuid4()
        w2 = uuid.uuid4()
        now = datetime.now(timezone.utc)

        sig1 = ResolvedWorkSignal(
            work_id=w1,
            effective_weight=1.0,
            polarity="positive",
            signal_timestamp=now,
        )
        sig2 = ResolvedWorkSignal(
            work_id=w2,
            effective_weight=0.5,
            polarity="positive",
            signal_timestamp=now,
        )

        e1 = make_axis_vector(384, axis=0)
        e2 = make_axis_vector(384, axis=1)

        result = build_behavioral_vector(
            signals=[sig1, sig2],
            work_embeddings={w1: e1, w2: e2},
            calculation_time=now,
            expected_dim=384,
        )

        assert result.behavioral_vector is not None
        assert result.distinct_positive_works == 2
        assert l2_norm(result.behavioral_vector) == pytest.approx(1.0)
        # Positive weights 1.0 and 0.5 -> norm sqrt(1^2 + 0.5^2) = sqrt(1.25)
        expected_0 = 1.0 / math.sqrt(1.25)
        expected_1 = 0.5 / math.sqrt(1.25)
        assert result.behavioral_vector[0] == pytest.approx(expected_0, rel=1e-5)
        assert result.behavioral_vector[1] == pytest.approx(expected_1, rel=1e-5)

    def test_negative_only_signals_returns_none(self):
        w1 = uuid.uuid4()
        now = datetime.now(timezone.utc)

        sig_neg = ResolvedWorkSignal(
            work_id=w1,
            effective_weight=-0.8,
            polarity="negative",
            signal_timestamp=now,
        )
        e1 = make_axis_vector(384, axis=0)

        # Must NOT construct a behavioral vector from negative signals alone
        result = build_behavioral_vector(
            signals=[sig_neg],
            work_embeddings={w1: e1},
            calculation_time=now,
            expected_dim=384,
        )
        assert result.behavioral_vector is None
        assert result.distinct_positive_works == 0

    def test_mixed_positive_and_negative_with_alpha_discount(self):
        w_pos = uuid.uuid4()
        w_neg = uuid.uuid4()
        now = datetime.now(timezone.utc)

        sig_pos = ResolvedWorkSignal(
            work_id=w_pos,
            effective_weight=1.0,
            polarity="positive",
            signal_timestamp=now,
        )
        sig_neg = ResolvedWorkSignal(
            work_id=w_neg,
            effective_weight=-1.0,
            polarity="negative",
            signal_timestamp=now,
        )

        e_pos = make_axis_vector(384, axis=0)
        e_neg = make_axis_vector(384, axis=1)

        # Alpha = 0.25 -> v_raw = [1.0, -0.25 * 1.0, 0, ...] = [1.0, -0.25, 0...]
        # Norm = sqrt(1 + 0.0625) = sqrt(1.0625)
        result = build_behavioral_vector(
            signals=[sig_pos, sig_neg],
            work_embeddings={w_pos: e_pos, w_neg: e_neg},
            calculation_time=now,
            negative_discount_alpha=0.25,
            expected_dim=384,
        )

        assert result.behavioral_vector is not None
        assert result.distinct_positive_works == 1
        raw_norm = math.sqrt(1.0 + 0.25 * 0.25)
        assert result.behavioral_vector[0] == pytest.approx(1.0 / raw_norm, rel=1e-5)
        assert result.behavioral_vector[1] == pytest.approx(-0.25 / raw_norm, rel=1e-5)
        assert result.behavioral_vector[2] == pytest.approx(0.0)

    def test_neutral_signals_are_excluded(self):
        w_pos = uuid.uuid4()
        w_neu = uuid.uuid4()
        now = datetime.now(timezone.utc)

        sig_pos = ResolvedWorkSignal(
            work_id=w_pos,
            effective_weight=1.0,
            polarity="positive",
            signal_timestamp=now,
        )
        sig_neu = ResolvedWorkSignal(
            work_id=w_neu,
            effective_weight=0.10,
            polarity="neutral",
            signal_timestamp=now,
        )

        e_pos = make_axis_vector(384, axis=0)
        e_neu = make_axis_vector(384, axis=1)

        result = build_behavioral_vector(
            signals=[sig_pos, sig_neu],
            work_embeddings={w_pos: e_pos, w_neu: e_neu},
            calculation_time=now,
            expected_dim=384,
        )

        assert result.behavioral_vector is not None
        assert result.distinct_positive_works == 1
        # Neutral signal didn't contribute -> vector is exactly e_pos
        assert result.behavioral_vector[0] == pytest.approx(1.0)
        assert result.behavioral_vector[1] == pytest.approx(0.0)

    def test_missing_embeddings_safely_skipped(self):
        w_with_emb = uuid.uuid4()
        w_without_emb = uuid.uuid4()
        now = datetime.now(timezone.utc)

        sig1 = ResolvedWorkSignal(
            work_id=w_with_emb,
            effective_weight=1.0,
            polarity="positive",
            signal_timestamp=now,
        )
        sig2 = ResolvedWorkSignal(
            work_id=w_without_emb,
            effective_weight=0.8,
            polarity="positive",
            signal_timestamp=now,
        )

        e1 = make_axis_vector(384, axis=0)

        # w_without_emb is omitted from mapping
        result = build_behavioral_vector(
            signals=[sig1, sig2],
            work_embeddings={w_with_emb: e1},
            calculation_time=now,
            expected_dim=384,
        )

        assert result.behavioral_vector is not None
        # Only w_with_emb actually contributed
        assert result.distinct_positive_works == 1
        assert result.behavioral_vector == pytest.approx(e1, rel=1e-5, abs=1e-7)

    def test_duplicate_work_signal_raises_value_error(self):
        w1 = uuid.uuid4()
        now = datetime.now(timezone.utc)

        sig1 = ResolvedWorkSignal(
            work_id=w1,
            effective_weight=1.0,
            polarity="positive",
            signal_timestamp=now,
        )
        sig2 = ResolvedWorkSignal(
            work_id=w1,
            effective_weight=0.5,
            polarity="positive",
            signal_timestamp=now,
        )

        with pytest.raises(ValueError, match="Duplicate work signal encountered"):
            build_behavioral_vector(
                signals=[sig1, sig2],
                work_embeddings={w1: make_axis_vector(384, 0)},
                calculation_time=now,
            )

    def test_recency_decay_reduces_old_signal_influence(self):
        w_old = uuid.uuid4()
        w_new = uuid.uuid4()
        now = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)
        past_180_days = now - timedelta(days=180)

        # Both have same base weight 1.0, but w_old is 180 days old (weight becomes 0.5)
        sig_old = ResolvedWorkSignal(
            work_id=w_old,
            effective_weight=1.0,
            polarity="positive",
            signal_timestamp=past_180_days,
        )
        sig_new = ResolvedWorkSignal(
            work_id=w_new,
            effective_weight=1.0,
            polarity="positive",
            signal_timestamp=now,
        )

        e_old = make_axis_vector(384, axis=0)
        e_new = make_axis_vector(384, axis=1)

        result = build_behavioral_vector(
            signals=[sig_old, sig_new],
            work_embeddings={w_old: e_old, w_new: e_new},
            calculation_time=now,
            half_life_days=180.0,
        )

        assert result.behavioral_vector is not None
        # Old weight = 0.5, New weight = 1.0
        # Ratio new / old = 2.0
        assert result.behavioral_vector[1] == pytest.approx(
            2.0 * result.behavioral_vector[0], rel=1e-4
        )

    def test_near_zero_cancellation_returns_none(self):
        w_pos = uuid.uuid4()
        w_neg = uuid.uuid4()
        now = datetime.now(timezone.utc)

        # Suppose pos has weight 0.25 and neg has weight -1.0 with alpha=0.25
        # v_raw = 0.25 * e0 - 0.25 * (1.0 * e0) = 0.0
        sig_pos = ResolvedWorkSignal(
            work_id=w_pos,
            effective_weight=0.25,
            polarity="positive",
            signal_timestamp=now,
        )
        sig_neg = ResolvedWorkSignal(
            work_id=w_neg,
            effective_weight=-1.0,
            polarity="negative",
            signal_timestamp=now,
        )

        e0 = make_axis_vector(384, axis=0)
        result = build_behavioral_vector(
            signals=[sig_pos, sig_neg],
            work_embeddings={w_pos: e0, w_neg: e0},
            calculation_time=now,
            negative_discount_alpha=0.25,
            epsilon=1e-6,
        )

        assert result.behavioral_vector is None
        assert result.distinct_positive_works == 1


# ==============================================================================
# Explicit / Behavioral Blending Tests
# ==============================================================================

class TestVectorBlending:
    """Tests for dynamic beta explicit/behavioral vector blending."""

    def test_explicit_only_profile(self):
        e_exp = make_axis_vector(384, axis=0)
        result = blend_profile_vectors(
            explicit_vector=e_exp,
            behavioral_vector=None,
            distinct_positive_works=0,
            m0=5.0,
        )
        assert result.blended_vector is not None
        assert result.beta == 1.0
        assert result.blended_vector == pytest.approx(e_exp, rel=1e-5, abs=1e-7)

    def test_behavioral_only_profile(self):
        e_beh = make_axis_vector(384, axis=1)
        result = blend_profile_vectors(
            explicit_vector=None,
            behavioral_vector=e_beh,
            distinct_positive_works=5,
            m0=5.0,
        )
        assert result.blended_vector is not None
        # beta = 1 / (1 + 5/5) = 0.5
        assert result.beta == pytest.approx(0.5)
        assert result.blended_vector == pytest.approx(e_beh, rel=1e-5, abs=1e-7)

    def test_neither_vector_exists_returns_none(self):
        result = blend_profile_vectors(
            explicit_vector=None,
            behavioral_vector=None,
            distinct_positive_works=0,
            m0=5.0,
        )
        assert result.blended_vector is None
        assert result.beta is None

    @pytest.mark.parametrize(
        "m,m0,expected_beta",
        [
            (0, 5.0, 1.0),
            (5, 5.0, 0.5),
            (20, 5.0, 0.2),
            (45, 5.0, 0.1),
        ],
    )
    def test_beta_values_across_positive_work_counts(self, m, m0, expected_beta):
        e_exp = make_axis_vector(384, axis=0)
        e_beh = make_axis_vector(384, axis=1)
        result = blend_profile_vectors(
            explicit_vector=e_exp,
            behavioral_vector=e_beh,
            distinct_positive_works=m,
            m0=m0,
        )
        assert result.beta == pytest.approx(expected_beta, rel=1e-5)
        assert result.blended_vector is not None

        # Verify blend proportions
        # v = [beta, 1 - beta, 0, ...] normalized
        unnorm = math.sqrt(expected_beta**2 + (1.0 - expected_beta)**2)
        assert result.blended_vector[0] == pytest.approx(expected_beta / unnorm, rel=1e-5)
        assert result.blended_vector[1] == pytest.approx((1.0 - expected_beta) / unnorm, rel=1e-5)

    def test_invalid_m_raises_value_error(self):
        with pytest.raises(ValueError, match="distinct_positive_works"):
            blend_profile_vectors(
                explicit_vector=[0.1] * 384,
                behavioral_vector=None,
                distinct_positive_works=-1,
                m0=5.0,
            )

    @pytest.mark.parametrize("bad_m0", [0.0, -5.0, float("nan"), float("inf")])
    def test_invalid_m0_raises_value_error(self, bad_m0):
        with pytest.raises(ValueError, match="blending baseline"):
            blend_profile_vectors(
                explicit_vector=[0.1] * 384,
                behavioral_vector=None,
                distinct_positive_works=0,
                m0=bad_m0,
            )

    def test_dimension_mismatch_raises_value_error(self):
        e_exp = [0.1] * 128
        e_beh = [0.1] * 384
        with pytest.raises(ValueError, match="dimension mismatch"):
            blend_profile_vectors(
                explicit_vector=e_exp,
                behavioral_vector=e_beh,
                distinct_positive_works=3,
                m0=5.0,
                expected_dim=384,
            )

    def test_near_zero_blend_fallback_to_explicit(self):
        # When explicit and behavioral are diametrically opposed and cancel:
        # e_exp = +axis0, e_beh = -axis0 with beta = 0.5 (M=5, M0=5)
        # 0.5 * e_exp + 0.5 * e_beh = 0.0
        e_exp = make_axis_vector(384, axis=0)
        e_beh = [-x for x in e_exp]

        result = blend_profile_vectors(
            explicit_vector=e_exp,
            behavioral_vector=e_beh,
            distinct_positive_works=5,
            m0=5.0,
            epsilon=1e-6,
        )
        assert result.blended_vector is not None
        # Fallback should prefer explicit vector
        assert result.blended_vector == pytest.approx(e_exp, rel=1e-5, abs=1e-7)


# ==============================================================================
# Public Profile-Vector Builder Tests
# ==============================================================================

class TestPublicProfileVectorBuilder:
    """Integration tests for the pure user interest profile vector builder."""

    def test_complete_valid_profile(self):
        w_pos = uuid.uuid4()
        w_neg = uuid.uuid4()
        now = datetime.now(timezone.utc)

        e_pref = make_dense_vector(384, phase=0.0)
        e_pos = make_dense_vector(384, phase=1.0)
        e_neg = make_dense_vector(384, phase=2.0)

        sig_pos = ResolvedWorkSignal(
            work_id=w_pos,
            effective_weight=1.0,
            polarity="positive",
            signal_timestamp=now,
        )
        sig_neg = ResolvedWorkSignal(
            work_id=w_neg,
            effective_weight=-0.8,
            polarity="negative",
            signal_timestamp=now,
        )

        result = build_user_interest_profile_vector(
            explicit_preferences=[(e_pref, 0.8)],
            resolved_signals=[sig_pos, sig_neg],
            work_embeddings={w_pos: e_pos, w_neg: e_neg},
            calculation_time=now,
        )

        assert isinstance(result, ProfileVectorBuildResult)
        assert result.interest_vector is not None
        assert result.explicit_vector is not None
        assert result.behavioral_vector is not None
        assert result.distinct_positive_works == 1
        assert result.beta is not None
        assert l2_norm(result.interest_vector) == pytest.approx(1.0)
        assert l2_norm(result.explicit_vector) == pytest.approx(1.0)
        assert l2_norm(result.behavioral_vector) == pytest.approx(1.0)

    def test_cold_start_profile(self):
        result = build_user_interest_profile_vector(
            explicit_preferences=[],
            resolved_signals=[],
            work_embeddings={},
        )
        assert result.interest_vector is None
        assert result.explicit_vector is None
        assert result.behavioral_vector is None
        assert result.beta is None
        assert result.distinct_positive_works == 0

    def test_explicit_only_profile(self):
        e_pref = make_axis_vector(384, axis=0)
        result = build_user_interest_profile_vector(
            explicit_preferences=[(e_pref, 1.0)],
            resolved_signals=[],
        )
        assert result.interest_vector is not None
        assert result.explicit_vector == pytest.approx(e_pref, rel=1e-5, abs=1e-7)
        assert result.behavioral_vector is None
        assert result.beta == 1.0
        assert result.distinct_positive_works == 0

    def test_behavioral_only_profile(self):
        w_pos = uuid.uuid4()
        now = datetime.now(timezone.utc)
        e_pos = make_axis_vector(384, axis=1)

        sig = ResolvedWorkSignal(
            work_id=w_pos,
            effective_weight=1.0,
            polarity="positive",
            signal_timestamp=now,
        )

        result = build_user_interest_profile_vector(
            explicit_preferences=[],
            resolved_signals=[sig],
            work_embeddings={w_pos: e_pos},
            calculation_time=now,
        )
        assert result.interest_vector is not None
        assert result.explicit_vector is None
        assert result.behavioral_vector == pytest.approx(e_pos, rel=1e-5, abs=1e-7)
        assert result.distinct_positive_works == 1
        # beta for M=1, M0=5: 1 / (1 + 1/5) = 5/6 ~= 0.8333
        assert result.beta == pytest.approx(1.0 / (1.0 + 1.0 / 5.0), rel=1e-5)

    def test_custom_settings_overrides(self):
        custom_settings = Settings(
            DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/test_db",
            PROFILE_EMBEDDING_DIMENSION=128,
            PROFILE_BLENDING_M0=10.0,
        )
        e_pref = make_axis_vector(128, axis=0)
        result = build_user_interest_profile_vector(
            explicit_preferences=[(e_pref, 1.0)],
            settings=custom_settings,
        )
        assert result.interest_vector is not None
        assert len(result.interest_vector) == 128
