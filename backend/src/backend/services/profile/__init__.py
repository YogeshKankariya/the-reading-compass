from __future__ import annotations

from backend.services.profile.signal_extractor import (
    ExtractedProfileSignals,
    ProfileSignalExtractor,
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
    "ProfileSignalExtractor",
    "ProfileVectorBuildResult",
    "blend_profile_vectors",
    "build_behavioral_vector",
    "build_explicit_preference_vector",
    "build_user_interest_profile_vector",
    "calculate_recency_decay",
    "l2_norm",
    "normalize_vector",
    "validate_and_normalize_vector",
    "validate_vector",
]
