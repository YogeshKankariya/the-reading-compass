from __future__ import annotations

from backend.schemas.profile import ProfileRefreshResult
from backend.services.profile.profile_service import (
    ProfileService,
    compute_authoritative_state_fingerprint,
    compute_explicit_preference_hash,
    generate_user_profile_lock_key,
)
from backend.services.profile.profile_tasks import schedule_profile_refresh
from backend.services.profile.signal_extractor import (
    ExtractedProfileSignals,
    ProfileSignalExtractor,
)
from backend.services.profile.topic_calculator import (
    ProfileTopicCalculator,
    calculate_topic_affinities,
)
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

__all__ = [
    "BehavioralVectorResult",
    "BlendedVectorResult",
    "ExtractedProfileSignals",
    "ProfileRefreshResult",
    "ProfileService",
    "ProfileSignalExtractor",
    "ProfileTopicCalculator",
    "ProfileVectorBuildResult",
    "blend_profile_vectors",
    "build_behavioral_vector",
    "build_explicit_preference_vector",
    "build_user_interest_profile_vector",
    "calculate_recency_decay",
    "calculate_topic_affinities",
    "compute_authoritative_state_fingerprint",
    "compute_explicit_preference_hash",
    "generate_user_profile_lock_key",
    "l2_norm",
    "normalize_vector",
    "schedule_profile_refresh",
    "validate_and_normalize_vector",
    "validate_vector",
]
