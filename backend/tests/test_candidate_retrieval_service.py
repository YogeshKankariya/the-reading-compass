import logging
import math
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from backend.api.dependencies import get_candidate_retrieval_service
from backend.core.config import Settings, get_settings, settings as global_settings
from backend.db.models.author import Author, BookWorkAuthor
from backend.db.models.book import BookEdition, BookWork
from backend.db.models.embedding import BookEmbedding
from backend.db.models.genre import BookWorkGenre, Genre
from backend.db.models.library import UserBook
from backend.db.models.recommendation import (
    RecommendationItem,
    RecommendationRequest,
    UserInterestProfile,
)
from backend.db.models.subject import BookWorkSubject, Subject
from backend.db.models.user import User, UserPreference
from backend.db.session import get_db
from backend.main import app as main_app
from backend.schemas.retrieval import (
    CandidateBook,
    CandidatePoolResult,
    CandidateWorkMetadata,
    EligibilityPolicy,
    RetrievalRequestContext,
)
from backend.services.retrieval import (
    CandidateMerger,
    CandidateRetrievalService,
    CandidateRetrievalServiceProtocol,
    EligibilityFilter,
    EligibilityFilterBatch,
    MetadataCandidateHit,
    MetadataRetrievalBatch,
    SemanticCandidateHit,
    SemanticRetrievalBatch,
)

test_engine = create_async_engine(global_settings.DATABASE_URL, poolclass=NullPool)


@asynccontextmanager
async def get_test_session():
    """Yield an isolated AsyncSession inside a rolled-back PostgreSQL transaction."""
    async with test_engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()


def make_settings(**overrides) -> Settings:
    base = {
        "DATABASE_URL": global_settings.DATABASE_URL,
        "PROFILE_EMBEDDING_MODEL_NAME": "sentence-transformers/all-MiniLM-L6-v2",
        "PROFILE_EMBEDDING_MODEL_REVISION": "rev-test-1",
    }
    base.update(overrides)
    return Settings(**base)


def make_unit_vector(dim: int = 384, components: dict[int, float] | None = None) -> list[float]:
    vec = [0.0] * dim
    if components is None:
        vec[0] = 1.0
    else:
        for idx, val in components.items():
            vec[idx] = val
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec]


def make_work_metadata(title: str = "Test Work") -> CandidateWorkMetadata:
    return CandidateWorkMetadata(
        canonical_title=title,
        original_language="en",
        metadata_status="verified",
        edition_page_counts=(250,),
    )


def make_profile(
    user_id: uuid.UUID,
    vector: list[float] | None = None,
    model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
    model_revision: str = "rev-test-1",
) -> UserInterestProfile:
    return UserInterestProfile(
        user_id=user_id,
        model_name=model_name,
        model_revision=model_revision,
        interest_vector=vector,
        interaction_count=0,
    )


# ==============================================================================
# 1. Service Orchestration & Validation Unit Tests
# ==============================================================================


@pytest.mark.asyncio
class TestCandidateRetrievalOrchestration:
    """Verify orchestration wiring, context propagation, user isolation, and cold start."""

    async def test_implements_protocol(self):
        mock_session = AsyncMock(spec=AsyncSession)
        service = CandidateRetrievalService(session=mock_session, settings=make_settings())
        assert isinstance(service, CandidateRetrievalServiceProtocol)

    async def test_orchestrates_both_channels_merger_and_eligibility_with_initial_limits(self):
        uid = uuid.uuid4()
        w_sem = uuid.uuid4()
        w_meta = uuid.uuid4()
        w_both = uuid.uuid4()
        g_id = uuid.uuid4()
        ref_time = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)

        cfg = make_settings(
            CANDIDATE_TARGET_COUNT=2,
            RETRIEVAL_SEMANTIC_INITIAL_LIMIT=200,
            RETRIEVAL_METADATA_INITIAL_LIMIT=200,
        )
        profile = make_profile(uid, vector=make_unit_vector())

        mock_profile_service = AsyncMock()
        mock_profile_service.get_profile.return_value = profile

        mock_sem = AsyncMock()
        mock_sem.retrieve.return_value = SemanticRetrievalBatch(
            hits=(
                SemanticCandidateHit(work_id=w_sem, semantic_distance=0.12),
                SemanticCandidateHit(work_id=w_both, semantic_distance=0.25),
            ),
            status="executed",
            model_name=cfg.PROFILE_EMBEDDING_MODEL_NAME,
            model_revision=cfg.PROFILE_EMBEDDING_MODEL_REVISION,
        )

        mock_meta = AsyncMock()
        mock_meta.retrieve.return_value = MetadataRetrievalBatch(
            hits=(
                MetadataCandidateHit(
                    work_id=w_meta,
                    retrieval_sources=("explicit_genre",),
                    matched_genres=(g_id,),
                ),
                MetadataCandidateHit(
                    work_id=w_both,
                    retrieval_sources=("explicit_genre",),
                    matched_genres=(g_id,),
                ),
            ),
            used_cold_start_baseline=False,
        )

        real_merger = CandidateMerger()
        spy_merger = MagicMock(wraps=real_merger)

        # Mark w_meta as ineligible; w_sem and w_both remain eligible -> target=2 reached!
        async def fake_filter(user_id, candidates, policy, reference_time):
            assert user_id == uid
            assert reference_time == ref_time
            assert policy.request_type == "discover"
            assert policy.include_tbr is False
            eligible = tuple(c for c in candidates if c.work_id != w_meta)
            ineligible = tuple(
                c.model_copy(
                    update={
                        "eligibility": c.eligibility.model_copy(
                            update={"is_eligible": False, "exclusion_reasons": ("excluded_tbr",)}
                        )
                    }
                )
                for c in candidates
                if c.work_id == w_meta
            )
            return EligibilityFilterBatch(
                eligible=eligible,
                filtered_out=ineligible,
                filtered_out_count=len(ineligible),
            )

        mock_filter = AsyncMock()
        mock_filter.filter_candidates.side_effect = fake_filter

        async def fake_meta_loader(work_ids):
            return {wid: make_work_metadata(f"Book {wid}") for wid in work_ids}

        service = CandidateRetrievalService(
            session=AsyncMock(spec=AsyncSession),
            settings=cfg,
            profile_service=mock_profile_service,
            semantic_retriever=mock_sem,
            metadata_retriever=mock_meta,
            candidate_merger=spy_merger,
            eligibility_filter=mock_filter,
            work_metadata_loader=fake_meta_loader,
        )

        ctx = RetrievalRequestContext(
            user_id=uid,
            request_type="discover",
            reference_time=ref_time,
        )
        result = await service.retrieve_candidates(ctx)

        assert isinstance(result, CandidatePoolResult)
        assert result.user_id == uid
        assert result.request_type == "discover"
        assert result.target_count == 2
        assert result.actual_count == 2
        assert result.target_reached is True
        assert result.expansion_steps_executed == 0
        assert result.semantic_status == "executed"
        assert result.cold_start_mode is False
        assert result.total_raw_retrieved == 3
        assert result.filtered_out_count == 1
        assert result.retrieved_at == ref_time

        mock_profile_service.get_profile.assert_awaited_once_with(uid)
        mock_sem.retrieve.assert_awaited_once_with(profile=profile, limit=200)
        assert mock_meta.retrieve.await_count == 1
        assert mock_meta.retrieve.await_args.kwargs["limit"] == 200
        assert mock_meta.retrieve.await_args.kwargs["user_id"] == uid
        assert mock_meta.retrieve.await_args.kwargs["profile"] is profile
        assert spy_merger.merge.call_count == 1
        assert mock_filter.filter_candidates.await_count == 1

        by_id = {c.work_id: c for c in result.candidates}
        assert set(by_id.keys()) == {w_sem, w_both}
        assert by_id[w_both].retrieval_sources == ("explicit_genre", "semantic")
        assert by_id[w_both].semantic_distance == 0.25
        assert by_id[w_both].matched_genres == (g_id,)

    async def test_user_id_mismatch_and_invalid_inputs_rejected(self):
        service = CandidateRetrievalService(
            session=AsyncMock(spec=AsyncSession),
            settings=make_settings(),
        )
        ctx = RetrievalRequestContext(user_id=uuid.uuid4())
        other_user = uuid.uuid4()

        with pytest.raises(ValueError, match="does not match"):
            await service.retrieve_candidates(ctx, expected_user_id=other_user)

        with pytest.raises(ValueError, match="does not match"):
            await service.retrieve_for_user(user_id=other_user, context=ctx)

        with pytest.raises(ValueError, match="valid UUID"):
            await service.retrieve_for_user(user_id="not-a-uuid")  # type: ignore[arg-type]

        with pytest.raises(ValueError, match="RetrievalRequestContext"):
            await service.retrieve_candidates("bad-context")  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "case_name,semantic_status,metadata_used_baseline,expected_cold_start,profile_factory",
        [
            # 1. Genuine vector cold start: user has no UserInterestProfile record
            (
                "missing_profile_explicit_metadata",
                "cold_start_no_vector",
                False,
                True,
                lambda uid, cfg: None,
            ),
            # 2. Genuine vector cold start: UserInterestProfile exists but interest_vector is None
            (
                "null_vector_explicit_metadata",
                "cold_start_no_vector",
                False,
                True,
                lambda uid, cfg: make_profile(uid, vector=None),
            ),
            # 3. Genuine vector cold start: missing profile and zero preferences (catalogue baseline fallback)
            (
                "missing_profile_baseline_metadata",
                "cold_start_no_vector",
                True,
                True,
                lambda uid, cfg: None,
            ),
            # 4. Vector exists, revision_unspecified on server, personalized via explicit metadata
            (
                "revision_unspecified_explicit_metadata",
                "revision_unspecified",
                False,
                False,
                lambda uid, cfg: make_profile(
                    uid,
                    vector=make_unit_vector(),
                    model_name=cfg.PROFILE_EMBEDDING_MODEL_NAME,
                    model_revision="rev-test-1",
                ),
            ),
            # 5. Vector exists, revision_unspecified on server, but user has zero preferences -> catalogue baseline
            (
                "revision_unspecified_baseline_metadata",
                "revision_unspecified",
                True,
                True,
                lambda uid, cfg: make_profile(
                    uid,
                    vector=make_unit_vector(),
                    model_name=cfg.PROFILE_EMBEDDING_MODEL_NAME,
                    model_revision="rev-test-1",
                ),
            ),
            # 6. Vector exists, model_mismatch, personalized via explicit metadata
            (
                "model_mismatch_explicit_metadata",
                "model_mismatch",
                False,
                False,
                lambda uid, cfg: make_profile(
                    uid,
                    vector=make_unit_vector(),
                    model_name="different-model",
                    model_revision="different-rev",
                ),
            ),
            # 7. Vector exists, model_mismatch, but user has zero preferences -> catalogue baseline
            (
                "model_mismatch_baseline_metadata",
                "model_mismatch",
                True,
                True,
                lambda uid, cfg: make_profile(
                    uid,
                    vector=make_unit_vector(),
                    model_name="different-model",
                    model_revision="different-rev",
                ),
            ),
        ],
    )
    async def test_semantic_unavailable_or_missing_profile_still_executes_metadata(
        self,
        case_name,
        semantic_status,
        metadata_used_baseline,
        expected_cold_start,
        profile_factory,
    ):
        uid = uuid.uuid4()
        w1 = uuid.uuid4()
        cfg = make_settings(CANDIDATE_TARGET_COUNT=1)

        profile = profile_factory(uid, cfg)
        mock_profile_service = AsyncMock()
        mock_profile_service.get_profile.return_value = profile

        mock_sem = AsyncMock()
        mock_sem.retrieve.return_value = SemanticRetrievalBatch(
            hits=(),
            status=semantic_status,
            model_name=cfg.PROFILE_EMBEDDING_MODEL_NAME,
            model_revision=cfg.PROFILE_EMBEDDING_MODEL_REVISION if semantic_status != "revision_unspecified" else None,
        )

        sources = ("cold_start_baseline",) if metadata_used_baseline else ("explicit_author",)
        mock_meta = AsyncMock()
        mock_meta.retrieve.return_value = MetadataRetrievalBatch(
            hits=(
                MetadataCandidateHit(
                    work_id=w1,
                    retrieval_sources=sources,
                    matched_authors=(uuid.uuid4(),) if not metadata_used_baseline else (),
                ),
            ),
            used_cold_start_baseline=metadata_used_baseline,
        )

        async def fake_filter(user_id, candidates, policy, reference_time):
            return EligibilityFilterBatch(eligible=tuple(candidates), filtered_out=(), filtered_out_count=0)

        service = CandidateRetrievalService(
            session=AsyncMock(spec=AsyncSession),
            settings=cfg,
            profile_service=mock_profile_service,
            semantic_retriever=mock_sem,
            metadata_retriever=mock_meta,
            candidate_merger=CandidateMerger(),
            eligibility_filter=AsyncMock(filter_candidates=AsyncMock(side_effect=fake_filter)),
            work_metadata_loader=AsyncMock(return_value={w1: make_work_metadata("Candidate Work")}),
        )

        result = await service.retrieve_for_user(user_id=uid)
        assert result.actual_count == 1
        assert result.target_reached is True
        assert result.semantic_status == semantic_status
        assert result.cold_start_mode is expected_cold_start
        assert result.candidates[0].work_id == w1
        assert result.candidates[0].semantic_distance is None
        assert result.candidates[0].retrieval_sources == sources


# ==============================================================================
# 2. Bounded Expansion & Provenance Retention Tests
# ==============================================================================


@pytest.mark.asyncio
class TestBoundedCandidateExpansion:
    """Verify exact 200 -> 400 -> 800 progression, caps, exhaustion, and provenance retention."""

    async def test_no_expansion_when_target_reached_initially(self):
        uid = uuid.uuid4()
        works = [uuid.uuid4() for _ in range(100)]
        cfg = make_settings(CANDIDATE_TARGET_COUNT=100)

        mock_sem = AsyncMock()
        mock_sem.retrieve.return_value = SemanticRetrievalBatch(
            hits=tuple(SemanticCandidateHit(work_id=w, semantic_distance=0.1) for w in works),
            status="executed",
            model_name=cfg.PROFILE_EMBEDDING_MODEL_NAME,
            model_revision=cfg.PROFILE_EMBEDDING_MODEL_REVISION,
        )
        mock_meta = AsyncMock()
        mock_meta.retrieve.return_value = MetadataRetrievalBatch(hits=(), used_cold_start_baseline=False)

        async def allow_all(user_id, candidates, policy, reference_time):
            return EligibilityFilterBatch(eligible=tuple(candidates), filtered_out=(), filtered_out_count=0)

        service = CandidateRetrievalService(
            session=AsyncMock(spec=AsyncSession),
            settings=cfg,
            profile_service=AsyncMock(get_profile=AsyncMock(return_value=make_profile(uid, make_unit_vector()))),
            semantic_retriever=mock_sem,
            metadata_retriever=mock_meta,
            candidate_merger=CandidateMerger(),
            eligibility_filter=AsyncMock(filter_candidates=AsyncMock(side_effect=allow_all)),
            work_metadata_loader=AsyncMock(return_value={w: make_work_metadata() for w in works}),
        )

        result = await service.retrieve_candidates(RetrievalRequestContext(user_id=uid))
        assert result.actual_count == 100
        assert result.target_reached is True
        assert result.expansion_steps_executed == 0
        assert mock_sem.retrieve.await_count == 1
        assert mock_meta.retrieve.await_count == 1

    async def test_default_expansion_progression_200_400_800_and_stops_at_800(self):
        uid = uuid.uuid4()
        cfg = make_settings()  # target=100, initial=200/200, mult=2.0, max_limit=800, max_steps=3

        # Construct 800 semantic works & 800 metadata works; only ~35 eligible per band
        # Step 0 (200) -> 40 eligible; Step 1 (400) -> 80 eligible; Step 2 (800) -> 110 eligible!
        sem_works = [uuid.uuid4() for _ in range(800)]
        meta_works = [uuid.uuid4() for _ in range(800)]
        g_id = uuid.uuid4()

        # Make first 150 excluded, remaining partially eligible so target=100 hits at step 2 (limit=800)
        eligible_set = set(sem_works[170:210]) | set(sem_works[350:390]) | set(meta_works[740:785])
        assert len(eligible_set) >= 100

        sem_limits: list[int] = []
        meta_limits: list[int] = []

        async def sem_side_effect(profile, limit):
            sem_limits.append(limit)
            return SemanticRetrievalBatch(
                hits=tuple(
                    SemanticCandidateHit(work_id=w, semantic_distance=0.2)
                    for w in sem_works[:limit]
                ),
                status="executed",
                model_name=cfg.PROFILE_EMBEDDING_MODEL_NAME,
                model_revision=cfg.PROFILE_EMBEDDING_MODEL_REVISION,
            )

        async def meta_side_effect(user_id, profile, context, limit):
            meta_limits.append(limit)
            return MetadataRetrievalBatch(
                hits=tuple(
                    MetadataCandidateHit(
                        work_id=w,
                        retrieval_sources=("explicit_genre",),
                        matched_genres=(g_id,),
                    )
                    for w in meta_works[:limit]
                ),
                used_cold_start_baseline=False,
            )

        async def filter_side_effect(user_id, candidates, policy, reference_time):
            elig = tuple(c for c in candidates if c.work_id in eligible_set)
            inelig = tuple(
                c.model_copy(
                    update={
                        "eligibility": c.eligibility.model_copy(
                            update={"is_eligible": False, "exclusion_reasons": ("completed",)}
                        )
                    }
                )
                for c in candidates
                if c.work_id not in eligible_set
            )
            return EligibilityFilterBatch(
                eligible=elig,
                filtered_out=inelig,
                filtered_out_count=len(inelig),
            )

        meta_map = {w: make_work_metadata() for w in (sem_works + meta_works)}

        service = CandidateRetrievalService(
            session=AsyncMock(spec=AsyncSession),
            settings=cfg,
            profile_service=AsyncMock(get_profile=AsyncMock(return_value=make_profile(uid, make_unit_vector()))),
            semantic_retriever=AsyncMock(retrieve=AsyncMock(side_effect=sem_side_effect)),
            metadata_retriever=AsyncMock(retrieve=AsyncMock(side_effect=meta_side_effect)),
            candidate_merger=CandidateMerger(),
            eligibility_filter=AsyncMock(filter_candidates=AsyncMock(side_effect=filter_side_effect)),
            work_metadata_loader=AsyncMock(side_effect=lambda ids: {i: meta_map[i] for i in ids}),
        )

        result = await service.retrieve_candidates(RetrievalRequestContext(user_id=uid))

        assert sem_limits == [200, 400, 800]
        assert meta_limits == [200, 400, 800]
        assert max(sem_limits) <= 800
        assert max(meta_limits) <= 800
        assert result.expansion_steps_executed == 2
        assert result.target_reached is True
        assert result.actual_count == len(result.candidates) == len(eligible_set)
        assert all(c.eligibility.is_eligible for c in result.candidates)

    async def test_stops_at_800_without_repeating_800_when_target_unreached(self):
        uid = uuid.uuid4()
        cfg = make_settings()  # max_limit=800, max_steps=3

        sem_works = [uuid.uuid4() for _ in range(900)]
        sem_limits: list[int] = []

        async def sem_side_effect(profile, limit):
            sem_limits.append(limit)
            return SemanticRetrievalBatch(
                hits=tuple(SemanticCandidateHit(work_id=w, semantic_distance=0.1) for w in sem_works[:limit]),
                status="executed",
                model_name=cfg.PROFILE_EMBEDDING_MODEL_NAME,
                model_revision=cfg.PROFILE_EMBEDDING_MODEL_REVISION,
            )

        async def none_eligible(user_id, candidates, policy, reference_time):
            inelig = tuple(
                c.model_copy(
                    update={
                        "eligibility": c.eligibility.model_copy(
                            update={"is_eligible": False, "exclusion_reasons": ("completed",)}
                        )
                    }
                )
                for c in candidates
            )
            return EligibilityFilterBatch(eligible=(), filtered_out=inelig, filtered_out_count=len(inelig))

        service = CandidateRetrievalService(
            session=AsyncMock(spec=AsyncSession),
            settings=cfg,
            profile_service=AsyncMock(get_profile=AsyncMock(return_value=make_profile(uid, make_unit_vector()))),
            semantic_retriever=AsyncMock(retrieve=AsyncMock(side_effect=sem_side_effect)),
            metadata_retriever=AsyncMock(
                retrieve=AsyncMock(return_value=MetadataRetrievalBatch(hits=(), used_cold_start_baseline=False))
            ),
            candidate_merger=CandidateMerger(),
            eligibility_filter=AsyncMock(filter_candidates=AsyncMock(side_effect=none_eligible)),
            work_metadata_loader=AsyncMock(side_effect=lambda ids: {i: make_work_metadata() for i in ids}),
        )

        result = await service.retrieve_candidates(RetrievalRequestContext(user_id=uid))
        # Progression hits 200 -> 400 -> 800, then stops cleanly without running a duplicate 800 query
        assert sem_limits == [200, 400, 800]
        assert result.expansion_steps_executed == 2
        assert result.actual_count == 0
        assert result.target_reached is False
        assert result.filtered_out_count == 800
        assert result.total_raw_retrieved == 800

    async def test_stops_at_max_expansion_steps_when_max_limit_higher(self):
        uid = uuid.uuid4()
        cfg_3 = make_settings(
            CANDIDATE_TARGET_COUNT=100,
            RETRIEVAL_SEMANTIC_INITIAL_LIMIT=200,
            RETRIEVAL_METADATA_INITIAL_LIMIT=200,
            RETRIEVAL_MAX_EXPANSION_LIMIT=2000,
            RETRIEVAL_MAX_EXPANSION_STEPS=3,
        )
        works = [uuid.uuid4() for _ in range(2000)]
        sem_limits: list[int] = []

        async def sem_side_effect(profile, limit):
            sem_limits.append(limit)
            return SemanticRetrievalBatch(
                hits=tuple(SemanticCandidateHit(work_id=w, semantic_distance=0.3) for w in works[:limit]),
                status="executed",
                model_name=cfg_3.PROFILE_EMBEDDING_MODEL_NAME,
                model_revision=cfg_3.PROFILE_EMBEDDING_MODEL_REVISION,
            )

        async def only_10_eligible(user_id, candidates, policy, reference_time):
            elig = tuple(candidates[:10])
            inelig = tuple(
                c.model_copy(
                    update={
                        "eligibility": c.eligibility.model_copy(
                            update={"is_eligible": False, "exclusion_reasons": ("completed",)}
                        )
                    }
                )
                for c in candidates[10:]
            )
            return EligibilityFilterBatch(eligible=elig, filtered_out=inelig, filtered_out_count=len(inelig))

        service = CandidateRetrievalService(
            session=AsyncMock(spec=AsyncSession),
            settings=cfg_3,
            profile_service=AsyncMock(get_profile=AsyncMock(return_value=make_profile(uid, make_unit_vector()))),
            semantic_retriever=AsyncMock(retrieve=AsyncMock(side_effect=sem_side_effect)),
            metadata_retriever=AsyncMock(
                retrieve=AsyncMock(return_value=MetadataRetrievalBatch(hits=(), used_cold_start_baseline=False))
            ),
            candidate_merger=CandidateMerger(),
            eligibility_filter=AsyncMock(filter_candidates=AsyncMock(side_effect=only_10_eligible)),
            work_metadata_loader=AsyncMock(side_effect=lambda ids: {i: make_work_metadata() for i in ids}),
        )

        result = await service.retrieve_candidates(RetrievalRequestContext(user_id=uid))
        assert sem_limits == [200, 400, 800, 1600]
        assert result.expansion_steps_executed == 3
        assert result.actual_count == 10
        assert result.target_reached is False

        # Also test RETRIEVAL_MAX_EXPANSION_STEPS=1 stops after 1 expansion (400)
        sem_limits.clear()
        cfg_1 = make_settings(
            CANDIDATE_TARGET_COUNT=100,
            RETRIEVAL_SEMANTIC_INITIAL_LIMIT=200,
            RETRIEVAL_METADATA_INITIAL_LIMIT=200,
            RETRIEVAL_MAX_EXPANSION_LIMIT=800,
            RETRIEVAL_MAX_EXPANSION_STEPS=1,
        )
        service.settings = cfg_1
        result_1 = await service.retrieve_candidates(RetrievalRequestContext(user_id=uid))
        assert sem_limits == [200, 400]
        assert result_1.expansion_steps_executed == 1

    async def test_retains_earlier_candidates_and_provenance_without_re_querying_exhausted_channels(self):
        uid = uuid.uuid4()
        w_early = uuid.uuid4()
        w_later = uuid.uuid4()
        g_id = uuid.uuid4()
        s_id = uuid.uuid4()

        cfg = make_settings(
            CANDIDATE_TARGET_COUNT=2,
            RETRIEVAL_SEMANTIC_INITIAL_LIMIT=2,
            RETRIEVAL_METADATA_INITIAL_LIMIT=2,
            RETRIEVAL_MAX_EXPANSION_LIMIT=8,
            RETRIEVAL_MAX_EXPANSION_STEPS=3,
        )

        # Metadata step 0 returns w_early (explicit_genre) ONLY (1 < limit=2 -> exhausted!)
        # Step 1 will NOT re-query metadata because metadata is already exhausted!
        # Semantic step 0 returns w_early (dist=0.4) + dummy ineligible w_x
        # Semantic step 1 returns ONLY w_later (dropping w_early!) -> earlier w_early & provenance MUST be retained!
        w_x = uuid.uuid4()
        sem_calls: list[int] = []
        meta_calls: list[int] = []

        async def sem_side_effect(profile, limit):
            sem_calls.append(limit)
            if limit == 2:
                return SemanticRetrievalBatch(
                    hits=(
                        SemanticCandidateHit(work_id=w_early, semantic_distance=0.4),
                        SemanticCandidateHit(work_id=w_x, semantic_distance=0.1),
                    ),
                    status="executed",
                    model_name=cfg.PROFILE_EMBEDDING_MODEL_NAME,
                    model_revision=cfg.PROFILE_EMBEDDING_MODEL_REVISION,
                )
            return SemanticRetrievalBatch(
                hits=(SemanticCandidateHit(work_id=w_later, semantic_distance=0.2),),
                status="executed",
                model_name=cfg.PROFILE_EMBEDDING_MODEL_NAME,
                model_revision=cfg.PROFILE_EMBEDDING_MODEL_REVISION,
            )

        async def meta_side_effect(user_id, profile, context, limit):
            meta_calls.append(limit)
            return MetadataRetrievalBatch(
                hits=(
                    MetadataCandidateHit(
                        work_id=w_early,
                        retrieval_sources=("explicit_genre", "inferred_subject"),
                        matched_genres=(g_id,),
                        matched_subjects=(s_id,),
                    ),
                ),
                used_cold_start_baseline=False,
            )

        async def filter_side_effect(user_id, candidates, policy, reference_time):
            elig = tuple(c for c in candidates if c.work_id != w_x)
            inelig = tuple(
                c.model_copy(
                    update={
                        "eligibility": c.eligibility.model_copy(
                            update={"is_eligible": False, "exclusion_reasons": ("completed",)}
                        )
                    }
                )
                for c in candidates
                if c.work_id == w_x
            )
            return EligibilityFilterBatch(eligible=elig, filtered_out=inelig, filtered_out_count=len(inelig))

        service = CandidateRetrievalService(
            session=AsyncMock(spec=AsyncSession),
            settings=cfg,
            profile_service=AsyncMock(get_profile=AsyncMock(return_value=make_profile(uid, make_unit_vector()))),
            semantic_retriever=AsyncMock(retrieve=AsyncMock(side_effect=sem_side_effect)),
            metadata_retriever=AsyncMock(retrieve=AsyncMock(side_effect=meta_side_effect)),
            candidate_merger=CandidateMerger(),
            eligibility_filter=AsyncMock(filter_candidates=AsyncMock(side_effect=filter_side_effect)),
            work_metadata_loader=AsyncMock(
                side_effect=lambda ids: {i: make_work_metadata(f"Title {i}") for i in ids}
            ),
        )

        result = await service.retrieve_candidates(RetrievalRequestContext(user_id=uid))

        assert meta_calls == [2]  # Not re-queried at 4!
        assert sem_calls == [2, 4]
        assert result.expansion_steps_executed == 1
        assert result.actual_count == 2
        assert result.target_reached is True
        by_id = {c.work_id: c for c in result.candidates}
        assert set(by_id.keys()) == {w_early, w_later}
        assert by_id[w_early].retrieval_sources == ("explicit_genre", "inferred_subject", "semantic")
        assert by_id[w_early].semantic_distance == 0.4
        assert by_id[w_early].matched_genres == (g_id,)
        assert by_id[w_early].matched_subjects == (s_id,)

    async def test_cold_start_metadata_only_expansion_without_repeated_semantic_calls(self):
        uid = uuid.uuid4()
        cfg = make_settings(
            CANDIDATE_TARGET_COUNT=100,
            RETRIEVAL_SEMANTIC_INITIAL_LIMIT=200,
            RETRIEVAL_METADATA_INITIAL_LIMIT=200,
            RETRIEVAL_MAX_EXPANSION_LIMIT=800,
            RETRIEVAL_MAX_EXPANSION_STEPS=3,
        )
        works = [uuid.uuid4() for _ in range(400)]
        eligible_works = set(works[150:260])  # 110 eligible reached at limit=400

        mock_sem = AsyncMock()
        mock_sem.retrieve.return_value = SemanticRetrievalBatch(
            hits=(),
            status="cold_start_no_vector",
            model_name=cfg.PROFILE_EMBEDDING_MODEL_NAME,
            model_revision=cfg.PROFILE_EMBEDDING_MODEL_REVISION,
        )

        meta_limits: list[int] = []

        async def meta_side_effect(user_id, profile, context, limit):
            meta_limits.append(limit)
            return MetadataRetrievalBatch(
                hits=tuple(
                    MetadataCandidateHit(work_id=w, retrieval_sources=("cold_start_baseline",))
                    for w in works[:limit]
                ),
                used_cold_start_baseline=True,
            )

        async def filter_side_effect(user_id, candidates, policy, reference_time):
            elig = tuple(c for c in candidates if c.work_id in eligible_works)
            inelig = tuple(
                c.model_copy(
                    update={
                        "eligibility": c.eligibility.model_copy(
                            update={"is_eligible": False, "exclusion_reasons": ("excluded_tbr",)}
                        )
                    }
                )
                for c in candidates
                if c.work_id not in eligible_works
            )
            return EligibilityFilterBatch(eligible=elig, filtered_out=inelig, filtered_out_count=len(inelig))

        service = CandidateRetrievalService(
            session=AsyncMock(spec=AsyncSession),
            settings=cfg,
            profile_service=AsyncMock(get_profile=AsyncMock(return_value=None)),
            semantic_retriever=mock_sem,
            metadata_retriever=AsyncMock(retrieve=AsyncMock(side_effect=meta_side_effect)),
            candidate_merger=CandidateMerger(),
            eligibility_filter=AsyncMock(filter_candidates=AsyncMock(side_effect=filter_side_effect)),
            work_metadata_loader=AsyncMock(side_effect=lambda ids: {i: make_work_metadata() for i in ids}),
        )

        result = await service.retrieve_candidates(RetrievalRequestContext(user_id=uid))

        assert mock_sem.retrieve.await_count == 1  # Never re-queried on expansion!
        assert meta_limits == [200, 400]
        assert result.expansion_steps_executed == 1
        assert result.semantic_status == "cold_start_no_vector"
        assert result.cold_start_mode is True  # missing vector + cold_start_baseline used
        assert result.actual_count == 110
        assert result.target_reached is True

    async def test_non_cold_start_expansion_when_semantic_unavailable(self):
        """A user with an established vector whose revision is unspecified is not in cold start."""
        uid = uuid.uuid4()
        cfg = make_settings(
            CANDIDATE_TARGET_COUNT=100,
            RETRIEVAL_SEMANTIC_INITIAL_LIMIT=200,
            RETRIEVAL_METADATA_INITIAL_LIMIT=200,
            RETRIEVAL_MAX_EXPANSION_LIMIT=800,
            RETRIEVAL_MAX_EXPANSION_STEPS=3,
        )
        works = [uuid.uuid4() for _ in range(400)]
        eligible_works = set(works[150:260])
        g_id = uuid.uuid4()
        profile = make_profile(
            uid,
            vector=make_unit_vector(),
            model_name=cfg.PROFILE_EMBEDDING_MODEL_NAME,
            model_revision="rev-test-1",
        )

        mock_sem = AsyncMock()
        mock_sem.retrieve.return_value = SemanticRetrievalBatch(
            hits=(),
            status="revision_unspecified",
            model_name=cfg.PROFILE_EMBEDDING_MODEL_NAME,
            model_revision=None,
        )

        meta_limits: list[int] = []

        async def meta_side_effect(user_id, profile, context, limit):
            meta_limits.append(limit)
            return MetadataRetrievalBatch(
                hits=tuple(
                    MetadataCandidateHit(
                        work_id=w,
                        retrieval_sources=("explicit_genre",),
                        matched_genres=(g_id,),
                    )
                    for w in works[:limit]
                ),
                used_cold_start_baseline=False,
            )

        async def filter_side_effect(user_id, candidates, policy, reference_time):
            elig = tuple(c for c in candidates if c.work_id in eligible_works)
            inelig = tuple(
                c.model_copy(
                    update={
                        "eligibility": c.eligibility.model_copy(
                            update={"is_eligible": False, "exclusion_reasons": ("excluded_tbr",)}
                        )
                    }
                )
                for c in candidates
                if c.work_id not in eligible_works
            )
            return EligibilityFilterBatch(eligible=elig, filtered_out=inelig, filtered_out_count=len(inelig))

        service = CandidateRetrievalService(
            session=AsyncMock(spec=AsyncSession),
            settings=cfg,
            profile_service=AsyncMock(get_profile=AsyncMock(return_value=profile)),
            semantic_retriever=mock_sem,
            metadata_retriever=AsyncMock(retrieve=AsyncMock(side_effect=meta_side_effect)),
            candidate_merger=CandidateMerger(),
            eligibility_filter=AsyncMock(filter_candidates=AsyncMock(side_effect=filter_side_effect)),
            work_metadata_loader=AsyncMock(side_effect=lambda ids: {i: make_work_metadata() for i in ids}),
        )

        result = await service.retrieve_candidates(RetrievalRequestContext(user_id=uid))

        assert mock_sem.retrieve.await_count == 1  # Bypassed channel not repeated
        assert meta_limits == [200, 400]
        assert result.expansion_steps_executed == 1
        assert result.semantic_status == "revision_unspecified"
        assert result.cold_start_mode is False  # Vector exists, explicit metadata used
        assert result.actual_count == 110
        assert result.target_reached is True

    async def test_duplicate_hits_across_steps_never_inflate_actual_count(self):
        uid = uuid.uuid4()
        w1 = uuid.uuid4()
        cfg = make_settings(
            CANDIDATE_TARGET_COUNT=2,
            RETRIEVAL_SEMANTIC_INITIAL_LIMIT=2,
            RETRIEVAL_METADATA_INITIAL_LIMIT=2,
            RETRIEVAL_MAX_EXPANSION_LIMIT=8,
        )

        async def sem_side_effect(profile, limit):
            # Repeatedly returns same single work w1 -> step 1 sees 0 new semantic works -> exhausted
            return SemanticRetrievalBatch(
                hits=(SemanticCandidateHit(work_id=w1, semantic_distance=0.15),),
                status="executed",
                model_name=cfg.PROFILE_EMBEDDING_MODEL_NAME,
                model_revision=cfg.PROFILE_EMBEDDING_MODEL_REVISION,
            )

        async def meta_side_effect(user_id, profile, context, limit):
            return MetadataRetrievalBatch(
                hits=(MetadataCandidateHit(work_id=w1, retrieval_sources=("cold_start_baseline",)),),
                used_cold_start_baseline=True,
            )

        async def allow_all(user_id, candidates, policy, reference_time):
            return EligibilityFilterBatch(eligible=tuple(candidates), filtered_out=(), filtered_out_count=0)

        sem_mock = AsyncMock(retrieve=AsyncMock(side_effect=sem_side_effect))
        meta_mock = AsyncMock(retrieve=AsyncMock(side_effect=meta_side_effect))

        service = CandidateRetrievalService(
            session=AsyncMock(spec=AsyncSession),
            settings=cfg,
            profile_service=AsyncMock(get_profile=AsyncMock(return_value=make_profile(uid, make_unit_vector()))),
            semantic_retriever=sem_mock,
            metadata_retriever=meta_mock,
            candidate_merger=CandidateMerger(),
            eligibility_filter=AsyncMock(filter_candidates=AsyncMock(side_effect=allow_all)),
            work_metadata_loader=AsyncMock(return_value={w1: make_work_metadata()}),
        )

        result = await service.retrieve_candidates(RetrievalRequestContext(user_id=uid))
        assert result.actual_count == 1
        assert result.total_raw_retrieved == 1
        assert result.target_reached is False
        assert sem_mock.retrieve.await_count == 2  # initial(2), expand(4 -> 0 new -> exhausted!)
        assert meta_mock.retrieve.await_count == 1  # exhausted at initial(2) since len=1 < 2


# ==============================================================================
# 3. Error Handling, Logging Safety & FastAPI Dependency Integration
# ==============================================================================


@pytest.mark.asyncio
class TestErrorsLoggingAndDependencyIntegration:
    """Verify DB errors propagate, logs hide vectors, and DI reuses session/settings cleanly."""

    async def test_database_error_propagates_without_swallowing(self):
        uid = uuid.uuid4()
        mock_profile_service = AsyncMock()
        mock_profile_service.get_profile.side_effect = OperationalError(
            "SELECT 1", {}, Exception("connection reset")
        )
        service = CandidateRetrievalService(
            session=AsyncMock(spec=AsyncSession),
            settings=make_settings(),
            profile_service=mock_profile_service,
        )

        with pytest.raises(OperationalError):
            await service.retrieve_candidates(RetrievalRequestContext(user_id=uid))

    async def test_logging_records_metrics_without_raw_vectors(self, caplog):
        uid = uuid.uuid4()
        w1 = uuid.uuid4()
        secret_vec = make_unit_vector(components={0: 0.7777, 1: 0.3333})
        profile = make_profile(uid, vector=secret_vec)
        cfg = make_settings(CANDIDATE_TARGET_COUNT=1)

        mock_sem = AsyncMock()
        mock_sem.retrieve.return_value = SemanticRetrievalBatch(
            hits=(SemanticCandidateHit(work_id=w1, semantic_distance=0.1),),
            status="executed",
            model_name=cfg.PROFILE_EMBEDDING_MODEL_NAME,
            model_revision=cfg.PROFILE_EMBEDDING_MODEL_REVISION,
        )
        mock_meta = AsyncMock()
        mock_meta.retrieve.return_value = MetadataRetrievalBatch(hits=(), used_cold_start_baseline=False)

        async def allow_all(user_id, candidates, policy, reference_time):
            return EligibilityFilterBatch(eligible=tuple(candidates), filtered_out=(), filtered_out_count=0)

        service = CandidateRetrievalService(
            session=AsyncMock(spec=AsyncSession),
            settings=cfg,
            profile_service=AsyncMock(get_profile=AsyncMock(return_value=profile)),
            semantic_retriever=mock_sem,
            metadata_retriever=mock_meta,
            candidate_merger=CandidateMerger(),
            eligibility_filter=AsyncMock(filter_candidates=AsyncMock(side_effect=allow_all)),
            work_metadata_loader=AsyncMock(return_value={w1: make_work_metadata()}),
        )

        with caplog.at_level(logging.DEBUG, logger="backend.services.retrieval.candidate_retrieval_service"):
            await service.retrieve_candidates(RetrievalRequestContext(user_id=uid))

        full_log = "\n".join(r.getMessage() for r in caplog.records)
        assert "stage=initial" in full_log
        assert "semantic_status=executed" in full_log
        assert "eligible_count=1" in full_log
        assert "0.7777" not in full_log
        assert str(secret_vec[0]) not in full_log
        assert cfg.DATABASE_URL not in full_log

    async def test_fastapi_dependency_provider_reuses_request_session_and_no_public_routes(self):
        # Verify main FastAPI app exposes NO recommendation/candidate routes yet
        route_paths = {getattr(route, "path", None) for route in main_app.routes}
        assert not any(path and "recommend" in path for path in route_paths)
        assert not any(path and "candidate" in path for path in route_paths)

        # Verify get_candidate_retrieval_service wires exact injected session & settings
        fake_session = AsyncMock(spec=AsyncSession)
        custom_settings = make_settings(CANDIDATE_TARGET_COUNT=42)

        async def override_db():
            yield fake_session

        def override_settings():
            return custom_settings

        test_app = FastAPI()
        test_app.dependency_overrides[get_db] = override_db
        test_app.dependency_overrides[get_settings] = override_settings

        captured: dict[str, object] = {}

        @test_app.get("/_internal-di-verify")
        async def _verify_di(
            svc: CandidateRetrievalService = Depends(get_candidate_retrieval_service),
        ):
            captured["service"] = svc
            return {
                "target": svc.settings.CANDIDATE_TARGET_COUNT,
                "same_session": svc.session is fake_session,
                "same_settings": svc.settings is custom_settings,
            }

        client = TestClient(test_app)
        resp = client.get("/_internal-di-verify")
        assert resp.status_code == 200
        assert resp.json() == {
            "target": 42,
            "same_session": True,
            "same_settings": True,
        }
        svc = captured["service"]
        assert isinstance(svc, CandidateRetrievalService)
        assert isinstance(svc, CandidateRetrievalServiceProtocol)
        assert svc.semantic_retriever.session is fake_session  # type: ignore[attr-defined]
        assert svc.metadata_retriever.session is fake_session  # type: ignore[attr-defined]
        assert svc.eligibility_filter.session is fake_session  # type: ignore[attr-defined]
        assert svc.profile_service.session is fake_session


# ==============================================================================
# 4. PostgreSQL + pgvector End-to-End Integration Tests
# ==============================================================================


@pytest.mark.asyncio
class TestCandidateRetrievalPostgreSQLIntegration:
    """Verify full retrieval pipeline against live PostgreSQL + pgvector inside rolled-back tx."""

    async def test_end_to_end_hybrid_retrieval_expansion_filtering_and_metadata_hydration(self):
        async with get_test_session() as session:
            cfg = make_settings(
                CANDIDATE_TARGET_COUNT=3,
                RETRIEVAL_SEMANTIC_INITIAL_LIMIT=2,
                RETRIEVAL_METADATA_INITIAL_LIMIT=2,
                RETRIEVAL_EXPANSION_MULTIPLIER=2.0,
                RETRIEVAL_MAX_EXPANSION_LIMIT=8,
                RETRIEVAL_MAX_EXPANSION_STEPS=3,
            )

            user = User(email=f"orch_{uuid.uuid4().hex[:8]}@example.com", display_name="Orch Reader")
            other_user = User(email=f"other_{uuid.uuid4().hex[:8]}@example.com", display_name="Other")
            session.add_all([user, other_user])
            await session.flush()

            genre = Genre(name=f"SciFi-{uuid.uuid4().hex[:6]}", slug=f"scifi-{uuid.uuid4().hex[:6]}")
            subject = Subject(name=f"Systems-{uuid.uuid4().hex[:6]}", slug=f"systems-{uuid.uuid4().hex[:6]}")
            author = Author(name="Ada Lovelace", normalized_name="ada lovelace")
            session.add_all([genre, subject, author])
            await session.flush()

            # Create 5 works:
            # w1: semantic #1 (closest), completed by user -> EXCLUDED
            # w2: semantic #2 & genre match, dismissed 10 days ago -> EXCLUDED
            # w3: semantic #3, unverified metadata, eligible!
            # w4: semantic #4 & author match, eligible!
            # w5: metadata genre + subject match (no embedding!), eligible!
            w1 = BookWork(canonical_title="Excluded Completed", original_language="en", metadata_status="verified")
            w2 = BookWork(canonical_title="Excluded Dismissed", original_language="en", metadata_status="verified")
            w3 = BookWork(canonical_title="Unverified Eligible", original_language="en", metadata_status="unverified")
            w4 = BookWork(canonical_title="Semantic & Author Match", original_language="en", metadata_status="verified")
            w5 = BookWork(canonical_title="Metadata Only Match", original_language="en", metadata_status="verified")
            session.add_all([w1, w2, w3, w4, w5])
            await session.flush()

            session.add_all([
                BookEdition(work_id=w3.id, page_count=310),
                BookEdition(work_id=w4.id, page_count=420),
                BookEdition(work_id=w5.id, page_count=260),
                BookWorkAuthor(work_id=w4.id, author_id=author.id, author_role="author", display_order=1),
                BookWorkGenre(work_id=w2.id, genre_id=genre.id),
                BookWorkGenre(work_id=w5.id, genre_id=genre.id),
                BookWorkSubject(work_id=w5.id, subject_id=subject.id),
            ])

            # Embeddings ordered monotonically away from [1, 0, 0, ...]
            session.add_all([
                BookEmbedding(
                    work_id=w1.id,
                    model_name=cfg.PROFILE_EMBEDDING_MODEL_NAME,
                    model_revision=cfg.PROFILE_EMBEDDING_MODEL_REVISION,
                    embedding=make_unit_vector(components={0: 1.0}),
                    source_text_hash="h1",
                    is_active=True,
                ),
                BookEmbedding(
                    work_id=w2.id,
                    model_name=cfg.PROFILE_EMBEDDING_MODEL_NAME,
                    model_revision=cfg.PROFILE_EMBEDDING_MODEL_REVISION,
                    embedding=make_unit_vector(components={0: 0.98, 1: 0.2}),
                    source_text_hash="h2",
                    is_active=True,
                ),
                BookEmbedding(
                    work_id=w3.id,
                    model_name=cfg.PROFILE_EMBEDDING_MODEL_NAME,
                    model_revision=cfg.PROFILE_EMBEDDING_MODEL_REVISION,
                    embedding=make_unit_vector(components={0: 0.90, 1: 0.4}),
                    source_text_hash="h3",
                    is_active=True,
                ),
                BookEmbedding(
                    work_id=w4.id,
                    model_name=cfg.PROFILE_EMBEDDING_MODEL_NAME,
                    model_revision=cfg.PROFILE_EMBEDDING_MODEL_REVISION,
                    embedding=make_unit_vector(components={0: 0.80, 1: 0.6}),
                    source_text_hash="h4",
                    is_active=True,
                ),
            ])

            # Profile + explicit genre/subject preferences
            session.add(
                UserInterestProfile(
                    user_id=user.id,
                    model_name=cfg.PROFILE_EMBEDDING_MODEL_NAME,
                    model_revision=cfg.PROFILE_EMBEDDING_MODEL_REVISION,
                    interest_vector=make_unit_vector(components={0: 1.0}),
                    interaction_count=5,
                )
            )
            session.add_all([
                UserPreference(user_id=user.id, preference_type="genre", preference_value=genre.slug, preference_weight=1.0),
                UserPreference(user_id=user.id, preference_type="subject", preference_value=subject.slug, preference_weight=0.9),
                UserPreference(user_id=user.id, preference_type="author", preference_value="Ada Lovelace", preference_weight=0.8),
            ])

            now = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
            session.add(UserBook(user_id=user.id, work_id=w1.id, status="completed"))
            # Ensure other_user library state does NOT leak into user's exclusions
            session.add(UserBook(user_id=other_user.id, work_id=w3.id, status="completed"))

            req = RecommendationRequest(
                user_id=user.id,
                request_type="next_read",
                algorithm_version="v1",
            )
            session.add(req)
            await session.flush()

            session.add(
                RecommendationItem(
                    request_id=req.id,
                    work_id=w2.id,
                    rank_position=1,
                    score=0.9,
                    score_breakdown={},
                    reason_codes=[],
                    feedback_type="dismiss",
                    feedback_at=now - timedelta(days=10),
                )
            )
            await session.flush()

            service = CandidateRetrievalService(session=session, settings=cfg)
            ctx = RetrievalRequestContext(
                user_id=user.id,
                request_type="next_read",
                reference_time=now,
            )
            result = await service.retrieve_candidates(ctx)

            # At step 0 (limit=2): sem={w1, w2}, meta={w2, w5} -> eligible={w5} (1 < target=3)
            # At step 1 (limit=4): sem={w1, w2, w3, w4}, meta={w2, w4, w5} -> eligible={w3, w4, w5} (3 == target=3)!
            assert result.target_count == 3
            assert result.actual_count == 3
            assert result.target_reached is True
            assert result.expansion_steps_executed == 1
            assert result.semantic_status == "executed"
            assert result.cold_start_mode is False
            assert result.filtered_out_count == 2  # w1 completed + w2 dismissed
            assert result.retrieved_at == now

            by_id = {c.work_id: c for c in result.candidates}
            assert set(by_id.keys()) == {w3.id, w4.id, w5.id}
            assert by_id[w3.id].work_metadata.metadata_status == "unverified"
            assert by_id[w3.id].work_metadata.edition_page_counts == (310,)
            assert by_id[w4.id].retrieval_sources == ("explicit_author", "semantic")
            assert by_id[w4.id].matched_authors == (author.id,)
            assert by_id[w4.id].work_metadata.author_names == ("Ada Lovelace",)
            assert by_id[w5.id].retrieval_sources == ("explicit_genre", "explicit_subject")
            assert by_id[w5.id].semantic_distance is None
            assert by_id[w5.id].work_metadata.genre_ids == (genre.id,)
            assert by_id[w5.id].work_metadata.subject_ids == (subject.id,)

    async def test_missing_profile_with_explicit_preferences_and_tbr_sparse_pool(self):
        async with get_test_session() as session:
            cfg = make_settings(CANDIDATE_TARGET_COUNT=100)
            user = User(email=f"cold_{uuid.uuid4().hex[:8]}@example.com", display_name="Cold User")
            session.add(user)
            await session.flush()

            genre = Genre(name=f"History-{uuid.uuid4().hex[:6]}", slug=f"history-{uuid.uuid4().hex[:6]}")
            session.add(genre)
            await session.flush()

            w_tbr = BookWork(canonical_title="Saved History Book")
            w_non_tbr = BookWork(canonical_title="Unsaved History Book")
            session.add_all([w_tbr, w_non_tbr])
            await session.flush()

            session.add_all([
                BookWorkGenre(work_id=w_tbr.id, genre_id=genre.id),
                BookWorkGenre(work_id=w_non_tbr.id, genre_id=genre.id),
                UserPreference(user_id=user.id, preference_type="genre", preference_value=genre.slug, preference_weight=1.0),
                UserBook(user_id=user.id, work_id=w_tbr.id, status="want_to_read"),
            ])
            await session.flush()

            service = CandidateRetrievalService(session=session, settings=cfg)

            # 1. next_read with missing UserInterestProfile row -> cold_start_no_vector, metadata explicit_genre works
            next_read_result = await service.retrieve_for_user(user_id=user.id, request_type="next_read")
            assert next_read_result.semantic_status == "cold_start_no_vector"
            assert next_read_result.cold_start_mode is True
            assert next_read_result.target_reached is False
            assert next_read_result.actual_count == 2
            assert {c.work_id for c in next_read_result.candidates} == {w_tbr.id, w_non_tbr.id}
            assert all("explicit_genre" in c.retrieval_sources for c in next_read_result.candidates)

            # 2. tbr_priority -> strictly excludes non-TBR even when sparse (1 < 100)
            tbr_result = await service.retrieve_for_user(user_id=user.id, request_type="tbr_priority")
            assert tbr_result.actual_count == 1
            assert tbr_result.target_reached is False
            assert [c.work_id for c in tbr_result.candidates] == [w_tbr.id]

    async def test_existing_profile_vector_with_model_mismatch_distinguishes_cold_start(self):
        """A user with an established vector is not in cold start unless metadata falls back to baseline."""
        async with get_test_session() as session:
            cfg = make_settings(CANDIDATE_TARGET_COUNT=100)
            user_with_prefs = User(email=f"mismatch_pref_{uuid.uuid4().hex[:8]}@example.com", display_name="Mismatch User 1")
            user_no_prefs = User(email=f"mismatch_nopref_{uuid.uuid4().hex[:8]}@example.com", display_name="Mismatch User 2")
            session.add_all([user_with_prefs, user_no_prefs])
            await session.flush()

            genre = Genre(name=f"Drama-{uuid.uuid4().hex[:6]}", slug=f"drama-{uuid.uuid4().hex[:6]}")
            session.add(genre)
            await session.flush()

            w1 = BookWork(canonical_title="Drama Book", metadata_status="verified")
            session.add(w1)
            await session.flush()

            session.add_all([
                BookWorkGenre(work_id=w1.id, genre_id=genre.id),
                # Both users have established profiles with non-null interest vectors, but under a mismatched model
                UserInterestProfile(
                    user_id=user_with_prefs.id,
                    model_name="legacy-embedding-model",
                    model_revision="legacy-v1",
                    interest_vector=make_unit_vector(),
                    interaction_count=10,
                ),
                UserInterestProfile(
                    user_id=user_no_prefs.id,
                    model_name="legacy-embedding-model",
                    model_revision="legacy-v1",
                    interest_vector=make_unit_vector(),
                    interaction_count=10,
                ),
                # user_with_prefs has explicit genre preferences
                UserPreference(
                    user_id=user_with_prefs.id,
                    preference_type="genre",
                    preference_value=genre.slug,
                    preference_weight=1.0,
                ),
            ])
            await session.flush()

            service = CandidateRetrievalService(session=session, settings=cfg)

            # Case A: user_with_prefs -> model_mismatch, but personalized via explicit genre -> cold_start_mode is False!
            result_prefs = await service.retrieve_for_user(user_id=user_with_prefs.id, request_type="next_read")
            assert result_prefs.semantic_status == "model_mismatch"
            assert result_prefs.cold_start_mode is False
            assert result_prefs.actual_count >= 1
            assert any(c.work_id == w1.id for c in result_prefs.candidates)
            assert any("explicit_genre" in c.retrieval_sources for c in result_prefs.candidates)

            # Case B: user_no_prefs -> model_mismatch, AND zero preferences -> catalogue baseline fallback -> cold_start_mode is True!
            result_no_prefs = await service.retrieve_for_user(user_id=user_no_prefs.id, request_type="next_read")
            assert result_no_prefs.semantic_status == "model_mismatch"
            assert result_no_prefs.cold_start_mode is True
            assert result_no_prefs.actual_count >= 1
            assert any("cold_start_baseline" in c.retrieval_sources for c in result_no_prefs.candidates)
