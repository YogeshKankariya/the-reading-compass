from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from backend.core.config import Settings, settings as default_settings
from backend.schemas.profile import ExplicitPreferenceInput, ResolvedWorkSignal


# ==============================================================================
# Result Containers
# ==============================================================================

class BehavioralVectorResult(BaseModel):
    """Result of pure behavioral Rocchio vector calculation."""

    model_config = ConfigDict(frozen=True)

    behavioral_vector: list[float] | None = None
    distinct_positive_works: int = Field(default=0, ge=0)


class BlendedVectorResult(BaseModel):
    """Result of pure explicit/behavioral vector blending."""

    model_config = ConfigDict(frozen=True)

    blended_vector: list[float] | None = None
    beta: float | None = None


class ProfileVectorBuildResult(BaseModel):
    """Combined output of the user interest profile vector builder."""

    model_config = ConfigDict(frozen=True)

    interest_vector: list[float] | None = None
    explicit_vector: list[float] | None = None
    behavioral_vector: list[float] | None = None
    beta: float | None = None
    distinct_positive_works: int = Field(default=0, ge=0)


# ==============================================================================
# Vector Validation & Normalization
# ==============================================================================

def validate_vector(vector: Sequence[float], expected_dim: int) -> list[float]:
    """Validate that input vector is non-empty, has expected dimensions, and finite numeric values.

    Args:
        vector: Sequence of floating point numbers.
        expected_dim: Required dimension count.

    Returns:
        List of floats converted from input sequence.

    Raises:
        ValueError: If vector has wrong length, contains NaN/infinity, or non-numeric types.
    """
    if vector is None:
        raise ValueError("Vector cannot be None")

    if not isinstance(vector, (list, tuple)):
        # Convert iterable if sequence
        vector = list(vector)

    if len(vector) == 0:
        raise ValueError("Vector cannot be empty")

    if len(vector) != expected_dim:
        raise ValueError(
            f"Vector dimension mismatch: expected {expected_dim}, got {len(vector)}"
        )

    validated: list[float] = []
    for idx, val in enumerate(vector):
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            raise ValueError(
                f"Vector element at index {idx} must be a numeric float, got {type(val).__name__}"
            )
        f_val = float(val)
        if not math.isfinite(f_val):
            raise ValueError(
                f"Vector element at index {idx} is non-finite ({f_val})"
            )
        validated.append(f_val)

    return validated


def l2_norm(vector: Sequence[float]) -> float:
    """Calculate the Euclidean (L2) norm of a numeric vector."""
    return math.sqrt(sum(x * x for x in vector))


def normalize_vector(
    vector: Sequence[float], epsilon: float = 1e-6
) -> list[float] | None:
    """L2-normalize a numeric vector to unit length.

    Args:
        vector: Sequence of float values.
        epsilon: Near-zero threshold. If ||vector|| < epsilon, normalization fails.

    Returns:
        Unit-length list of floats, or None if the vector norm is below epsilon.
    """
    norm = l2_norm(vector)
    if norm < epsilon:
        return None
    return [x / norm for x in vector]


def validate_and_normalize_vector(
    vector: Sequence[float], expected_dim: int, epsilon: float = 1e-6
) -> list[float]:
    """Validate vector dimensions and numeric values, then L2-normalize to unit length.

    All input embeddings used in weighted centroids follow this normalization convention
    to prevent differences in embedding magnitude from distorting signal weights.

    Args:
        vector: Sequence of numeric values.
        expected_dim: Expected embedding dimension.
        epsilon: Near-zero threshold.

    Returns:
        L2-normalized unit vector.

    Raises:
        ValueError: If vector fails validation or has an L2 norm below epsilon.
    """
    valid_vec = validate_vector(vector, expected_dim)
    normalized = normalize_vector(valid_vec, epsilon=epsilon)
    if normalized is None:
        raise ValueError(
            f"Vector L2 norm ({l2_norm(valid_vec):.8f}) is below near-zero threshold ({epsilon})"
        )
    return normalized


# ==============================================================================
# Recency Decay
# ==============================================================================

def calculate_recency_decay(
    base_weight: float,
    signal_timestamp: datetime,
    calculation_time: datetime,
    half_life_days: float = 180.0,
) -> float:
    """Calculate time-decayed signal weight using exponential half-life decay.

    Formula:
        w_recency = w_base * 2^(-Δt / τ)

    Where:
        w_base: Signed resolved signal weight.
        Δt: Elapsed time in days between signal timestamp and calculation time.
        τ: Half-life in days (must be positive and finite).

    Future timestamps (Δt < 0) are treated with elapsed time zero (w_recency = w_base)
    to prevent future timestamps from amplifying signal weights beyond base weight.

    Args:
        base_weight: Signed original signal weight.
        signal_timestamp: When the interaction occurred.
        calculation_time: Time of profile calculation.
        half_life_days: Half-life period in days.

    Returns:
        Decayed signed float weight.

    Raises:
        ValueError: If half_life_days <= 0 or non-finite, or base_weight non-finite.
    """
    if not math.isfinite(base_weight):
        raise ValueError("base_weight must be a finite number")
    if not math.isfinite(half_life_days) or half_life_days <= 0:
        raise ValueError("half_life_days must be a finite positive number")

    # Normalize datetimes to UTC
    if signal_timestamp.tzinfo is None:
        sig_utc = signal_timestamp.replace(tzinfo=timezone.utc)
    else:
        sig_utc = signal_timestamp.astimezone(timezone.utc)

    if calculation_time.tzinfo is None:
        calc_utc = calculation_time.replace(tzinfo=timezone.utc)
    else:
        calc_utc = calculation_time.astimezone(timezone.utc)

    elapsed_seconds = (calc_utc - sig_utc).total_seconds()

    # Policy: treat future timestamps as having zero elapsed time (no amplification)
    if elapsed_seconds < 0:
        elapsed_days = 0.0
    else:
        elapsed_days = elapsed_seconds / 86400.0

    decay_factor = math.pow(2.0, -elapsed_days / half_life_days)
    return base_weight * decay_factor


# ==============================================================================
# Explicit Preference Vector Construction
# ==============================================================================

def build_explicit_preference_vector(
    preferences: Sequence[tuple[Sequence[float], float]]
    | Sequence[tuple[ExplicitPreferenceInput, Sequence[float]]],
    expected_dim: int = 384,
    epsilon: float = 1e-6,
) -> list[float] | None:
    """Construct an L2-normalized centroid from explicit preference embeddings and weights.

    Mathematical definition:
        v_explicit = sum(w_i * e_i) / sum(w_i)
        p_explicit = v_explicit / ||v_explicit||

    Normalization convention:
        Input embeddings e_i are validated and L2-normalized to unit length before
        computing the weighted sum.

    Args:
        preferences: Sequence of either (embedding, weight) or (ExplicitPreferenceInput, embedding).
        expected_dim: Expected embedding dimension (default: 384).
        epsilon: Near-zero threshold for norm checking (default: 1e-6).

    Returns:
        L2-normalized explicit preference vector, or None if no usable preferences exist
        or if total weight is zero / centroid is near-zero.

    Raises:
        ValueError: If weights are negative or non-finite, or embeddings fail validation.
    """
    if not preferences:
        return None

    accumulated: list[float] = [0.0] * expected_dim
    total_weight = 0.0

    for item in preferences:
        if len(item) != 2:
            raise ValueError("Each preference item must be a 2-tuple")

        first, second = item
        if isinstance(first, ExplicitPreferenceInput):
            pref_input = first
            raw_emb = second
            weight = float(pref_input.preference_weight)
        else:
            raw_emb = first
            weight = float(second)

        if not math.isfinite(weight):
            raise ValueError(f"Preference weight must be a finite number, got {weight}")
        if weight < 0:
            raise ValueError(f"Preference weight cannot be negative, got {weight}")

        if weight == 0.0:
            continue

        # Normalization convention: input embeddings must be unit-normalized
        unit_emb = validate_and_normalize_vector(raw_emb, expected_dim, epsilon=epsilon)

        for d in range(expected_dim):
            accumulated[d] += weight * unit_emb[d]

        total_weight += weight

    if total_weight <= 0:
        return None

    # Compute weighted centroid
    centroid = [x / total_weight for x in accumulated]

    # L2-normalize centroid
    return normalize_vector(centroid, epsilon=epsilon)


# ==============================================================================
# Behavioral Rocchio Vector Construction
# ==============================================================================

def build_behavioral_vector(
    signals: Sequence[ResolvedWorkSignal],
    work_embeddings: Mapping[uuid.UUID, Sequence[float]]
    | Sequence[Sequence[float] | None],
    calculation_time: datetime | None = None,
    half_life_days: float = 180.0,
    negative_discount_alpha: float = 0.25,
    expected_dim: int = 384,
    epsilon: float = 1e-6,
) -> BehavioralVectorResult:
    """Construct an L2-normalized behavioral vector using modified Rocchio aggregation.

    Formula:
        v_raw = sum_{positive}(w_i * e_i) - alpha * sum_{negative}(|w_j| * e_j)
        p_behavioral = normalize(v_raw)

    Rules & Safeguards:
        1. Recency decay is applied to each signal's weight before vector aggregation.
        2. Neutral signals do not contribute to vector aggregation.
        3. Works whose embeddings are missing or None are safely skipped.
        4. Duplicate ResolvedWorkSignal entries for the same work_id are rejected (raise ValueError).
        5. A work contributes at most once. Distinct positive works with valid embeddings are counted.
        6. Negative-only signals return None (no negative-only behavioral vector).
        7. If v_raw is near-zero (||v_raw|| < epsilon), returns None.

    Args:
        signals: Sequence of deduplicated ResolvedWorkSignal models.
        work_embeddings: Mapping of work_id -> embedding, or sequence aligned with signals.
        calculation_time: UTC timestamp for recency calculation (defaults to now).
        half_life_days: Recency half-life in days (default: 180.0).
        negative_discount_alpha: Negative signal discount factor (default: 0.25).
        expected_dim: Expected embedding dimension (default: 384).
        epsilon: Near-zero threshold (default: 1e-6).

    Returns:
        BehavioralVectorResult containing behavioral_vector and distinct_positive_works.

    Raises:
        ValueError: If duplicate work signals exist, or alpha/half-life are invalid.
    """
    if not math.isfinite(negative_discount_alpha) or not (0.0 <= negative_discount_alpha <= 1.0):
        raise ValueError(
            f"negative_discount_alpha must be between 0.0 and 1.0, got {negative_discount_alpha}"
        )
    if not math.isfinite(half_life_days) or half_life_days <= 0:
        raise ValueError(
            f"half_life_days must be a finite positive number, got {half_life_days}"
        )

    if calculation_time is None:
        calculation_time = datetime.now(timezone.utc)

    # Detect duplicate work_ids
    seen_work_ids: set[uuid.UUID] = set()
    for sig in signals:
        if sig.work_id in seen_work_ids:
            raise ValueError(
                f"Duplicate work signal encountered for work_id={sig.work_id}. "
                "Work-level deduplication must be performed before vector calculation."
            )
        seen_work_ids.add(sig.work_id)

    pos_accumulator: list[float] = [0.0] * expected_dim
    neg_accumulator: list[float] = [0.0] * expected_dim
    distinct_positive_works: int = 0
    has_positive_contribution: bool = False

    is_mapping = isinstance(work_embeddings, Mapping)

    for idx, sig in enumerate(signals):
        # Neutral signals do not contribute to the behavioral vector
        if sig.polarity == "neutral":
            continue

        # Retrieve embedding
        raw_emb: Sequence[float] | None = None
        if is_mapping:
            raw_emb = work_embeddings.get(sig.work_id)
        else:
            if idx < len(work_embeddings):
                raw_emb = work_embeddings[idx]

        if raw_emb is None:
            # Embedding unavailable for this work: skip
            continue

        # Normalization convention: input embeddings must be unit-normalized
        unit_emb = validate_and_normalize_vector(raw_emb, expected_dim, epsilon=epsilon)

        decayed_weight = calculate_recency_decay(
            base_weight=sig.effective_weight,
            signal_timestamp=sig.signal_timestamp,
            calculation_time=calculation_time,
            half_life_days=half_life_days,
        )

        if sig.polarity == "positive":
            distinct_positive_works += 1
            has_positive_contribution = True
            for d in range(expected_dim):
                pos_accumulator[d] += decayed_weight * unit_emb[d]

        elif sig.polarity == "negative":
            # Rocchio negative term uses absolute magnitude: alpha * |w| * e_j
            abs_weight = abs(decayed_weight)
            for d in range(expected_dim):
                neg_accumulator[d] += abs_weight * unit_emb[d]

    # Safeguard: Do not construct a behavioral vector from negative signals alone
    if not has_positive_contribution:
        return BehavioralVectorResult(
            behavioral_vector=None,
            distinct_positive_works=0,
        )

    # v_raw = sum(pos) - alpha * sum(neg)
    raw_vector: list[float] = [
        pos_accumulator[d] - (negative_discount_alpha * neg_accumulator[d])
        for d in range(expected_dim)
    ]

    # L2-normalize behavioral vector
    normalized = normalize_vector(raw_vector, epsilon=epsilon)
    return BehavioralVectorResult(
        behavioral_vector=normalized,
        distinct_positive_works=distinct_positive_works,
    )


# ==============================================================================
# Explicit/Behavioral Blending
# ==============================================================================

def blend_profile_vectors(
    explicit_vector: Sequence[float] | None,
    behavioral_vector: Sequence[float] | None,
    distinct_positive_works: int,
    m0: float = 5.0,
    expected_dim: int = 384,
    epsilon: float = 1e-6,
) -> BlendedVectorResult:
    """Blend explicit preference vector and behavioral vector using dynamic weight beta.

    Formula:
        beta = 1 / (1 + M / M_0)
        p_user = normalize(beta * p_explicit + (1 - beta) * p_behavioral)

    Where:
        M: Distinct positive work count.
        M_0: Blending baseline (must be positive and finite).

    Behavior & Fallbacks:
        - If only explicit vector exists: returns explicit vector, beta = 1.0.
        - If only behavioral vector exists: returns behavioral vector, beta = 1 / (1 + M / M_0).
        - If neither exists: returns None, beta = None.
        - If blended vector cancels to near-zero: fallback prefers explicit vector,
          then behavioral vector.

    Args:
        explicit_vector: L2-normalized explicit vector or None.
        behavioral_vector: L2-normalized behavioral vector or None.
        distinct_positive_works: Number of distinct positive works (M >= 0).
        m0: Blending baseline parameter (M0 > 0).
        expected_dim: Expected embedding dimension (default: 384).
        epsilon: Near-zero threshold (default: 1e-6).

    Returns:
        BlendedVectorResult with blended_vector and beta.

    Raises:
        ValueError: If M < 0, M0 <= 0, or vector dimensions mismatch.
    """
    if distinct_positive_works < 0:
        raise ValueError(f"distinct_positive_works (M) must be >= 0, got {distinct_positive_works}")
    if not math.isfinite(m0) or m0 <= 0:
        raise ValueError(f"blending baseline (M0) must be a finite positive number, got {m0}")

    p_explicit: list[float] | None = None
    if explicit_vector is not None:
        p_explicit = validate_and_normalize_vector(explicit_vector, expected_dim, epsilon=epsilon)

    p_behavioral: list[float] | None = None
    if behavioral_vector is not None:
        p_behavioral = validate_and_normalize_vector(behavioral_vector, expected_dim, epsilon=epsilon)

    # Neither exists
    if p_explicit is None and p_behavioral is None:
        return BlendedVectorResult(blended_vector=None, beta=None)

    beta = 1.0 / (1.0 + (distinct_positive_works / m0))

    # Explicit-only
    if p_explicit is not None and p_behavioral is None:
        return BlendedVectorResult(blended_vector=p_explicit, beta=1.0)

    # Behavioral-only
    if p_explicit is None and p_behavioral is not None:
        return BlendedVectorResult(blended_vector=p_behavioral, beta=beta)

    # Both exist: blend
    # p_user = normalize(beta * p_explicit + (1 - beta) * p_behavioral)
    assert p_explicit is not None and p_behavioral is not None  # Type-check reassurance

    blended_raw = [
        (beta * p_explicit[d]) + ((1.0 - beta) * p_behavioral[d])
        for d in range(expected_dim)
    ]

    normalized = normalize_vector(blended_raw, epsilon=epsilon)

    # Near-zero cancellation fallback: prefer explicit, then behavioral
    if normalized is None:
        return BlendedVectorResult(blended_vector=p_explicit, beta=beta)

    return BlendedVectorResult(blended_vector=normalized, beta=beta)


# ==============================================================================
# Public Profile-Vector Builder
# ==============================================================================

def build_user_interest_profile_vector(
    explicit_preferences: Sequence[tuple[Sequence[float], float]]
    | Sequence[tuple[ExplicitPreferenceInput, Sequence[float]]] = (),
    resolved_signals: Sequence[ResolvedWorkSignal] = (),
    work_embeddings: Mapping[uuid.UUID, Sequence[float]]
    | Sequence[Sequence[float] | None]
    | None = None,
    calculation_time: datetime | None = None,
    settings: Settings | None = None,
) -> ProfileVectorBuildResult:
    """Orchestrate pure vector mathematics to construct a user interest profile vector.

    Pipeline:
        1. Validate and construct explicit preference centroid vector.
        2. Validate, decay, and construct behavioral Rocchio vector.
        3. Blend available vectors using dynamic beta weighting.
        4. Return combined result with intermediate vectors, beta, and work count.

    This function is completely pure and deterministic:
        - No database access or queries.
        - No SQLAlchemy sessions or models.
        - No external model loading or inference.
        - No topic affinity calculations or state mutations.

    Args:
        explicit_preferences: Preference vectors with associated weights.
        resolved_signals: Deduplicated ResolvedWorkSignal entries.
        work_embeddings: Mapping of work_id -> embedding (or aligned sequence).
        calculation_time: Reference timestamp for recency decay.
        settings: Application settings (defaults to core settings singleton).

    Returns:
        ProfileVectorBuildResult containing interest_vector, intermediate vectors,
        beta, and distinct_positive_works.
    """
    cfg = settings or default_settings
    dim = cfg.PROFILE_EMBEDDING_DIMENSION
    epsilon = cfg.PROFILE_NEAR_ZERO_EPSILON
    half_life = cfg.PROFILE_RECENCY_HALF_LIFE_DAYS
    alpha = cfg.PROFILE_NEGATIVE_DISCOUNT_ALPHA
    m0 = cfg.PROFILE_BLENDING_M0

    if calculation_time is None:
        calculation_time = datetime.now(timezone.utc)

    # 1. Build explicit vector
    p_explicit = build_explicit_preference_vector(
        preferences=explicit_preferences,
        expected_dim=dim,
        epsilon=epsilon,
    )

    # 2. Build behavioral vector
    embeddings_map = work_embeddings if work_embeddings is not None else {}
    behavioral_res = build_behavioral_vector(
        signals=resolved_signals,
        work_embeddings=embeddings_map,
        calculation_time=calculation_time,
        half_life_days=half_life,
        negative_discount_alpha=alpha,
        expected_dim=dim,
        epsilon=epsilon,
    )

    # 3. Blend vectors
    blend_res = blend_profile_vectors(
        explicit_vector=p_explicit,
        behavioral_vector=behavioral_res.behavioral_vector,
        distinct_positive_works=behavioral_res.distinct_positive_works,
        m0=m0,
        expected_dim=dim,
        epsilon=epsilon,
    )

    return ProfileVectorBuildResult(
        interest_vector=blend_res.blended_vector,
        explicit_vector=p_explicit,
        behavioral_vector=behavioral_res.behavioral_vector,
        beta=blend_res.beta,
        distinct_positive_works=behavioral_res.distinct_positive_works,
    )
