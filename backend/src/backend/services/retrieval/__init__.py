from __future__ import annotations

from backend.schemas.retrieval import (
    CandidateBook,
    CandidateEligibilityInfo,
    CandidatePoolResult,
    CandidateWorkMetadata,
    EligibilityPolicy,
    RecommendationRequestType,
    RetrievalRequestContext,
    RetrievalSource,
    SemanticRetrievalStatus,
    UserBookLifecycleStatus,
)
from backend.services.retrieval.interfaces import (
    CandidateMergerProtocol,
    CandidateRetrievalServiceProtocol,
    EligibilityFilterBatch,
    EligibilityFilterProtocol,
    MetadataCandidateHit,
    MetadataRetrievalBatch,
    MetadataRetrieverProtocol,
    SemanticCandidateHit,
    SemanticRetrievalBatch,
    SemanticRetrieverProtocol,
)
from backend.services.retrieval.semantic_retriever import SemanticRetriever

__all__ = [
    "CandidateBook",
    "CandidateEligibilityInfo",
    "CandidateMergerProtocol",
    "CandidatePoolResult",
    "CandidateRetrievalServiceProtocol",
    "CandidateWorkMetadata",
    "EligibilityFilterBatch",
    "EligibilityFilterProtocol",
    "EligibilityPolicy",
    "MetadataCandidateHit",
    "MetadataRetrievalBatch",
    "MetadataRetrieverProtocol",
    "RecommendationRequestType",
    "RetrievalRequestContext",
    "RetrievalSource",
    "SemanticCandidateHit",
    "SemanticRetrievalBatch",
    "SemanticRetrievalStatus",
    "SemanticRetriever",
    "SemanticRetrieverProtocol",
    "UserBookLifecycleStatus",
]
