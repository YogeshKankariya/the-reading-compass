import math
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from backend.core.config import Settings
from backend.schemas.retrieval import (
    CandidateBook,
    CandidateEligibilityInfo,
    CandidatePoolResult,
    CandidateWorkMetadata,
    EligibilityPolicy,
    RetrievalRequestContext,
)
from backend.services.retrieval import (
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


def _sample_metadata(**overrides) -> CandidateWorkMetadata:
    base = {
        "canonical_title": "Thinking in Systems",
        "subtitle": "A Primer",
        "original_language": "en",
        "first_published_year": 2008,
        "difficulty_level": "intermediate",
        "estimated_reading_minutes": 360,
        "metadata_status": "unverified",
        "edition_page_counts": (218, 240),
        "author_names": ("Donella H. Meadows",),
    }
    base.update(overrides)
    return CandidateWorkMetadata(**base)


def _eligible_info() -> CandidateEligibilityInfo:
    return CandidateEligibilityInfo(is_eligible=True)


# ==============================================================================
# 1. Retrieval Configuration Tests
# ==============================================================================

class TestRetrievalConfiguration:
    """Verify Milestone 5.2 retrieval defaults, overrides, and strict validation."""

    def test_retrieval_configuration_defaults(self):
        cfg = Settings(DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/test_db")
        assert cfg.CANDIDATE_TARGET_COUNT == 100
        assert cfg.RETRIEVAL_SEMANTIC_INITIAL_LIMIT == 200
        assert cfg.RETRIEVAL_METADATA_INITIAL_LIMIT == 200
        assert cfg.RETRIEVAL_METADATA_TOPIC_TOP_N == 10
        assert cfg.RETRIEVAL_MIN_INFERRED_TOPIC_AFFINITY == 0.05
        assert cfg.RETRIEVAL_EXPANSION_MULTIPLIER == 2.0
        assert cfg.RETRIEVAL_MAX_EXPANSION_LIMIT == 800
        assert cfg.RETRIEVAL_MAX_EXPANSION_STEPS == 3
        assert cfg.RETRIEVAL_DISMISSAL_COOLDOWN_DAYS == 90

    def test_retrieval_configuration_environment_overrides(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/test_db")
        monkeypatch.setenv("CANDIDATE_TARGET_COUNT", "50")
        monkeypatch.setenv("RETRIEVAL_SEMANTIC_INITIAL_LIMIT", "120")
        monkeypatch.setenv("RETRIEVAL_METADATA_INITIAL_LIMIT", "150")
        monkeypatch.setenv("RETRIEVAL_METADATA_TOPIC_TOP_N", "15")
        monkeypatch.setenv("RETRIEVAL_MIN_INFERRED_TOPIC_AFFINITY", "0.10")
        monkeypatch.setenv("RETRIEVAL_EXPANSION_MULTIPLIER", "1.5")
        monkeypatch.setenv("RETRIEVAL_MAX_EXPANSION_LIMIT", "600")
        monkeypatch.setenv("RETRIEVAL_MAX_EXPANSION_STEPS", "4")
        monkeypatch.setenv("RETRIEVAL_DISMISSAL_COOLDOWN_DAYS", "30")

        cfg = Settings()
        assert cfg.CANDIDATE_TARGET_COUNT == 50
        assert cfg.RETRIEVAL_SEMANTIC_INITIAL_LIMIT == 120
        assert cfg.RETRIEVAL_METADATA_INITIAL_LIMIT == 150
        assert cfg.RETRIEVAL_METADATA_TOPIC_TOP_N == 15
        assert cfg.RETRIEVAL_MIN_INFERRED_TOPIC_AFFINITY == 0.10
        assert cfg.RETRIEVAL_EXPANSION_MULTIPLIER == 1.5
        assert cfg.RETRIEVAL_MAX_EXPANSION_LIMIT == 600
        assert cfg.RETRIEVAL_MAX_EXPANSION_STEPS == 4
        assert cfg.RETRIEVAL_DISMISSAL_COOLDOWN_DAYS == 30

    @pytest.mark.parametrize(
        "invalid_field,invalid_value",
        [
            ("CANDIDATE_TARGET_COUNT", 0),
            ("CANDIDATE_TARGET_COUNT", -10),
            ("CANDIDATE_TARGET_COUNT", True),
            ("RETRIEVAL_SEMANTIC_INITIAL_LIMIT", 0),
            ("RETRIEVAL_METADATA_INITIAL_LIMIT", -1),
            ("RETRIEVAL_METADATA_TOPIC_TOP_N", 0),
            ("RETRIEVAL_MAX_EXPANSION_STEPS", 0),
            ("RETRIEVAL_DISMISSAL_COOLDOWN_DAYS", 0),
            ("RETRIEVAL_DISMISSAL_COOLDOWN_DAYS", -90),
            ("RETRIEVAL_DISMISSAL_COOLDOWN_DAYS", False),
            ("RETRIEVAL_MIN_INFERRED_TOPIC_AFFINITY", -0.01),
            ("RETRIEVAL_MIN_INFERRED_TOPIC_AFFINITY", 1.01),
            ("RETRIEVAL_MIN_INFERRED_TOPIC_AFFINITY", float("nan")),
            ("RETRIEVAL_MIN_INFERRED_TOPIC_AFFINITY", float("inf")),
            ("RETRIEVAL_EXPANSION_MULTIPLIER", 1.0),
            ("RETRIEVAL_EXPANSION_MULTIPLIER", 0.5),
            ("RETRIEVAL_EXPANSION_MULTIPLIER", float("nan")),
            ("RETRIEVAL_EXPANSION_MULTIPLIER", float("inf")),
        ],
    )
    def test_invalid_retrieval_settings_rejected(self, invalid_field, invalid_value):
        with pytest.raises(ValidationError):
            Settings(
                DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/test_db",
                **{invalid_field: invalid_value},
            )

    def test_inconsistent_expansion_bounds_rejected(self):
        with pytest.raises(ValidationError, match="RETRIEVAL_MAX_EXPANSION_LIMIT"):
            Settings(
                DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/test_db",
                RETRIEVAL_SEMANTIC_INITIAL_LIMIT=300,
                RETRIEVAL_MAX_EXPANSION_LIMIT=250,
            )

        with pytest.raises(ValidationError, match="CANDIDATE_TARGET_COUNT"):
            Settings(
                DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/test_db",
                CANDIDATE_TARGET_COUNT=150,
                RETRIEVAL_SEMANTIC_INITIAL_LIMIT=50,
                RETRIEVAL_METADATA_INITIAL_LIMIT=50,
                RETRIEVAL_MAX_EXPANSION_LIMIT=100,
            )


# ==============================================================================
# 2. Candidate Metadata & Eligibility Schema Tests
# ==============================================================================

class TestCandidateMetadataAndEligibilitySchemas:
    """Verify work metadata and eligibility state contracts."""

    def test_valid_work_metadata_and_blank_title_rejected(self):
        meta = _sample_metadata()
        assert meta.canonical_title == "Thinking in Systems"
        assert meta.edition_page_counts == (218, 240)

        with pytest.raises(ValidationError, match="canonical_title"):
            _sample_metadata(canonical_title="   ")

        with pytest.raises(ValidationError, match="negative"):
            _sample_metadata(edition_page_counts=(-5,))

        with pytest.raises(ValidationError, match="integers"):
            _sample_metadata(edition_page_counts=(True,))

    def test_eligibility_info_consistency_and_timezone_normalization(self):
        ok = CandidateEligibilityInfo(is_eligible=True, user_book_status="want_to_read")
        assert ok.exclusion_reasons == ()

        naive_dt = datetime(2026, 10, 1, 12, 0, 0)
        rejected = CandidateEligibilityInfo(
            is_eligible=False,
            exclusion_reasons=("dismissed_within_cooldown", "completed"),
            user_book_status="completed",
            dismissed_at=naive_dt,
        )
        assert rejected.exclusion_reasons == ("completed", "dismissed_within_cooldown")
        assert rejected.dismissed_at == datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)

        # Inconsistent states
        with pytest.raises(ValidationError, match="Eligible candidate cannot have exclusion_reasons"):
            CandidateEligibilityInfo(is_eligible=True, exclusion_reasons=("completed",))

        with pytest.raises(ValidationError, match="Ineligible candidate must include"):
            CandidateEligibilityInfo(is_eligible=False, exclusion_reasons=())

        # Duplicate exclusion reasons
        with pytest.raises(ValidationError, match="duplicate"):
            CandidateEligibilityInfo(is_eligible=False, exclusion_reasons=("completed", "completed"))


# ==============================================================================
# 3. CandidateBook Provenance & Validation Tests
# ==============================================================================

class TestCandidateBookContract:
    """Verify multi-channel candidate construction, provenance determinism, and invalid input rejection."""

    def test_valid_multi_channel_candidate_construction(self):
        wid = uuid.uuid4()
        g1, g2 = uuid.uuid4(), uuid.uuid4()
        s1 = uuid.uuid4()
        a1 = uuid.uuid4()

        cand = CandidateBook(
            work_id=wid,
            retrieval_sources=("explicit_subject", "semantic", "explicit_genre", "explicit_author"),
            semantic_distance=0.1425,
            matched_genres=(g2, g1),
            matched_subjects=(s1,),
            matched_authors=(a1,),
            eligibility=_eligible_info(),
            work_metadata=_sample_metadata(genre_ids=(g1, g2), subject_ids=(s1,)),
        )

        # Provenance and IDs are deterministically sorted
        assert cand.retrieval_sources == ("explicit_author", "explicit_genre", "explicit_subject", "semantic")
        assert cand.matched_genres == tuple(sorted([g1, g2], key=str))
        assert cand.semantic_distance == 0.1425

        # Frozen immutability
        with pytest.raises(ValidationError):
            cand.semantic_distance = 0.5  # type: ignore[misc]

    def test_metadata_only_candidate_with_none_semantic_distance(self):
        wid = uuid.uuid4()
        s1 = uuid.uuid4()
        cand = CandidateBook(
            work_id=wid,
            retrieval_sources=("inferred_subject",),
            semantic_distance=None,
            matched_subjects=(s1,),
            eligibility=_eligible_info(),
            work_metadata=_sample_metadata(),
        )
        assert cand.semantic_distance is None
        assert cand.retrieval_sources == ("inferred_subject",)

    def test_duplicate_provenance_and_matched_ids_rejected(self):
        wid = uuid.uuid4()
        g1 = uuid.uuid4()

        with pytest.raises(ValidationError, match="duplicate"):
            CandidateBook(
                work_id=wid,
                retrieval_sources=("semantic", "semantic"),
                semantic_distance=0.2,
                eligibility=_eligible_info(),
                work_metadata=_sample_metadata(),
            )

        with pytest.raises(ValidationError, match="duplicate"):
            CandidateBook(
                work_id=wid,
                retrieval_sources=("explicit_genre",),
                matched_genres=(g1, g1),
                eligibility=_eligible_info(),
                work_metadata=_sample_metadata(),
            )

        with pytest.raises(ValidationError, match="duplicate"):
            CandidateBook(
                work_id=wid,
                retrieval_sources=("inferred_subject",),
                matched_subjects=(g1, g1),
                eligibility=_eligible_info(),
                work_metadata=_sample_metadata(),
            )

        with pytest.raises(ValidationError, match="duplicate"):
            CandidateBook(
                work_id=wid,
                retrieval_sources=("explicit_author",),
                matched_authors=(g1, g1),
                eligibility=_eligible_info(),
                work_metadata=_sample_metadata(),
            )

    @pytest.mark.parametrize("bad_distance", [-0.001, 2.001, float("nan"), float("inf"), float("-inf"), True])
    def test_invalid_semantic_distances_rejected(self, bad_distance):
        with pytest.raises(ValidationError):
            CandidateBook(
                work_id=uuid.uuid4(),
                retrieval_sources=("semantic",),
                semantic_distance=bad_distance,
                eligibility=_eligible_info(),
                work_metadata=_sample_metadata(),
            )

    def test_inconsistent_provenance_states_rejected(self):
        wid = uuid.uuid4()
        g1 = uuid.uuid4()

        # Empty retrieval_sources rejected
        with pytest.raises(ValidationError):
            CandidateBook(
                work_id=wid,
                retrieval_sources=(),
                eligibility=_eligible_info(),
                work_metadata=_sample_metadata(),
            )

        # semantic source without semantic_distance
        with pytest.raises(ValidationError, match="semantic_distance"):
            CandidateBook(
                work_id=wid,
                retrieval_sources=("semantic",),
                semantic_distance=None,
                eligibility=_eligible_info(),
                work_metadata=_sample_metadata(),
            )

        # semantic_distance without semantic source
        with pytest.raises(ValidationError, match="semantic"):
            CandidateBook(
                work_id=wid,
                retrieval_sources=("explicit_genre",),
                semantic_distance=0.25,
                matched_genres=(g1,),
                eligibility=_eligible_info(),
                work_metadata=_sample_metadata(),
            )

        # explicit_genre without matched_genres
        with pytest.raises(ValidationError, match="matched_genres"):
            CandidateBook(
                work_id=wid,
                retrieval_sources=("explicit_genre",),
                eligibility=_eligible_info(),
                work_metadata=_sample_metadata(),
            )

        # explicit_subject without matched_subjects
        with pytest.raises(ValidationError, match="matched_subjects"):
            CandidateBook(
                work_id=wid,
                retrieval_sources=("inferred_subject",),
                eligibility=_eligible_info(),
                work_metadata=_sample_metadata(),
            )

        # explicit_author without matched_authors
        with pytest.raises(ValidationError, match="matched_authors"):
            CandidateBook(
                work_id=wid,
                retrieval_sources=("explicit_author",),
                eligibility=_eligible_info(),
                work_metadata=_sample_metadata(),
            )


# ==============================================================================
# 4. EligibilityPolicy & RetrievalRequestContext Tests
# ==============================================================================

class TestEligibilityPolicyAndRequestContext:
    """Verify approved default product policies and request validation."""

    def test_approved_default_policies_by_request_type(self):
        next_read = EligibilityPolicy.default_for_request_type("next_read")
        assert next_read.include_tbr is True
        assert next_read.require_tbr_only is False
        assert next_read.include_completed is False
        assert next_read.include_in_progress is False
        assert next_read.include_paused is False
        assert next_read.include_abandoned is False
        assert next_read.dismissal_cooldown_days == 90

        discover = EligibilityPolicy.default_for_request_type("discover")
        assert discover.include_tbr is False
        assert discover.require_tbr_only is False
        assert discover.include_paused is False
        assert discover.include_abandoned is False

        tbr = EligibilityPolicy.default_for_request_type("tbr_priority")
        assert tbr.include_tbr is True
        assert tbr.require_tbr_only is True

        new_author = EligibilityPolicy.default_for_request_type("new_author")
        assert new_author.exclude_known_authors is True

    def test_invalid_policy_combinations_rejected(self):
        with pytest.raises(ValidationError, match="require_tbr_only"):
            EligibilityPolicy(request_type="tbr_priority", require_tbr_only=False)

        with pytest.raises(ValidationError, match="include_tbr=True"):
            EligibilityPolicy(request_type="discover", require_tbr_only=True, include_tbr=False)

        with pytest.raises(ValidationError, match="min_pages"):
            EligibilityPolicy(request_type="next_read", min_pages=300, max_pages=200)

    def test_request_context_auto_resolves_policy_and_normalizes_utc(self):
        offset_tz = timezone(timedelta(hours=5, minutes=30))
        local_dt = datetime(2026, 11, 10, 15, 30, 0, tzinfo=offset_tz)

        ctx = RetrievalRequestContext(
            user_id=uuid.uuid4(),
            request_type="discover",
            language_filters=("en", "fr"),
            max_pages=400,
            reference_time=local_dt,
        )
        assert ctx.target_count == 100
        assert ctx.eligibility_policy is not None
        assert ctx.eligibility_policy.include_tbr is False
        assert ctx.eligibility_policy.allowed_languages == ("en", "fr")
        assert ctx.eligibility_policy.max_pages == 400
        assert ctx.reference_time.tzinfo == timezone.utc
        assert ctx.reference_time.hour == 10

        # Mismatched policy request_type rejected
        with pytest.raises(ValidationError, match="must match request_type"):
            RetrievalRequestContext(
                user_id=uuid.uuid4(),
                request_type="discover",
                eligibility_policy=EligibilityPolicy.default_for_request_type("next_read"),
            )


# ==============================================================================
# 5. CandidatePoolResult & JSON Serialization Tests
# ==============================================================================

class TestCandidatePoolResultAndSerialization:
    """Verify candidate pool invariants, sparse pool honesty, and JSON round-trip."""

    def test_candidate_pool_target_reached_and_sparse_pool(self):
        uid = uuid.uuid4()
        c1 = CandidateBook(
            work_id=uuid.uuid4(),
            retrieval_sources=("cold_start_baseline",),
            eligibility=_eligible_info(),
            work_metadata=_sample_metadata(),
        )
        c2 = CandidateBook(
            work_id=uuid.uuid4(),
            retrieval_sources=("semantic",),
            semantic_distance=0.1,
            eligibility=_eligible_info(),
            work_metadata=_sample_metadata(),
        )

        # Target 2, actual 2 -> target_reached=True
        pool_full = CandidatePoolResult(
            user_id=uid,
            request_type="next_read",
            candidates=(c1, c2),
            target_count=2,
            actual_count=2,
            target_reached=True,
            semantic_status="executed",
        )
        assert pool_full.actual_count == 2

        # Sparse catalogue: target 100, actual 1 -> target_reached=False
        pool_sparse = CandidatePoolResult(
            user_id=uid,
            request_type="discover",
            candidates=(c1,),
            target_count=100,
            actual_count=1,
            target_reached=False,
            semantic_status="cold_start_no_vector",
            cold_start_mode=True,
            expansion_steps_executed=3,
            total_raw_retrieved=5,
            filtered_out_count=4,
        )
        assert pool_sparse.target_reached is False

        # JSON round-trip serialization
        json_str = pool_sparse.model_dump_json()
        restored = CandidatePoolResult.model_validate_json(json_str)
        assert restored == pool_sparse

    def test_candidate_pool_rejects_inconsistent_counts_duplicates_or_ineligible(self):
        uid = uuid.uuid4()
        wid = uuid.uuid4()
        c1 = CandidateBook(
            work_id=wid,
            retrieval_sources=("cold_start_baseline",),
            eligibility=_eligible_info(),
            work_metadata=_sample_metadata(),
        )

        # Mismatched actual_count
        with pytest.raises(ValidationError, match="actual_count"):
            CandidatePoolResult(
                user_id=uid,
                request_type="next_read",
                candidates=(c1,),
                target_count=100,
                actual_count=2,
                target_reached=False,
                semantic_status="skipped",
            )

        # Dishonest target_reached=True when actual_count < target_count
        with pytest.raises(ValidationError, match="target_reached"):
            CandidatePoolResult(
                user_id=uid,
                request_type="next_read",
                candidates=(c1,),
                target_count=100,
                actual_count=1,
                target_reached=True,
                semantic_status="skipped",
            )

        # Duplicate work_id in pool
        c_dup = CandidateBook(
            work_id=wid,
            retrieval_sources=("semantic",),
            semantic_distance=0.2,
            eligibility=_eligible_info(),
            work_metadata=_sample_metadata(),
        )
        with pytest.raises(ValidationError, match="Duplicate work_id"):
            CandidatePoolResult(
                user_id=uid,
                request_type="next_read",
                candidates=(c1, c_dup),
                target_count=100,
                actual_count=2,
                target_reached=False,
                semantic_status="executed",
            )

        # Ineligible candidate in eligible pool
        c_bad = CandidateBook(
            work_id=uuid.uuid4(),
            retrieval_sources=("cold_start_baseline",),
            eligibility=CandidateEligibilityInfo(is_eligible=False, exclusion_reasons=("completed",)),
            work_metadata=_sample_metadata(),
        )
        with pytest.raises(ValidationError, match="Ineligible candidate"):
            CandidatePoolResult(
                user_id=uid,
                request_type="next_read",
                candidates=(c_bad,),
                target_count=100,
                actual_count=1,
                target_reached=False,
                semantic_status="skipped",
            )


# ==============================================================================
# 6. Service Protocol & Intermediate Batch Contract Tests
# ==============================================================================

class TestRetrievalServiceInterfaces:
    """Verify service package protocol definitions and batch DTO contracts."""

    def test_intermediate_batch_contracts(self):
        wid = uuid.uuid4()
        hit = SemanticCandidateHit(work_id=wid, semantic_distance=0.12)
        sem_batch = SemanticRetrievalBatch(
            hits=(hit,),
            status="executed",
            model_name="sentence-transformers/all-MiniLM-L6-v2",
            model_revision="rev-1",
        )
        assert sem_batch.hits[0].work_id == wid

        meta_hit = MetadataCandidateHit(work_id=wid, retrieval_sources=("cold_start_baseline",))
        meta_batch = MetadataRetrievalBatch(hits=(meta_hit,), used_cold_start_baseline=True)
        assert meta_batch.used_cold_start_baseline is True

        filt_batch = EligibilityFilterBatch(eligible=(), filtered_out=(), filtered_out_count=0)
        assert filt_batch.filtered_out_count == 0

    def test_protocol_structural_conformance(self):
        class DummySemantic:
            async def retrieve(self, profile, limit, exclude_work_ids=()):
                return SemanticRetrievalBatch(status="skipped", model_name="m")

        class DummyMetadata:
            async def retrieve(self, user_id, profile, context, limit):
                return MetadataRetrievalBatch()

        class DummyMerger:
            def merge(self, semantic_hits, metadata_hits, work_metadata, default_eligibility=None):
                return ()

        class DummyFilter:
            async def filter_candidates(self, user_id, candidates, policy, reference_time):
                return EligibilityFilterBatch()

        class DummyOrchestrator:
            async def retrieve_candidates(self, context):
                raise NotImplementedError

        assert isinstance(DummySemantic(), SemanticRetrieverProtocol)
        assert isinstance(DummyMetadata(), MetadataRetrieverProtocol)
        assert isinstance(DummyMerger(), CandidateMergerProtocol)
        assert isinstance(DummyFilter(), EligibilityFilterProtocol)
        assert isinstance(DummyOrchestrator(), CandidateRetrievalServiceProtocol)
