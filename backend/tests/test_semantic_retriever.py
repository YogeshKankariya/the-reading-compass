import logging
import math
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from backend.core.config import Settings, settings
from backend.db.models.book import BookWork
from backend.db.models.embedding import BookEmbedding
from backend.db.models.recommendation import UserInterestProfile
from backend.db.models.user import User
from backend.services.retrieval import SemanticRetriever, SemanticRetrieverProtocol

test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)


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


def make_unit_vector(dim: int = 384, components: dict[int, float] | None = None) -> list[float]:
    """Create a normalized 384-dim unit vector for deterministic cosine-distance testing."""
    vec = [0.0] * dim
    if components is None:
        vec[0] = 1.0
    else:
        for idx, val in components.items():
            vec[idx] = val
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec]


def make_settings(**overrides) -> Settings:
    base = {
        "DATABASE_URL": settings.DATABASE_URL,
        "PROFILE_EMBEDDING_MODEL_NAME": "sentence-transformers/all-MiniLM-L6-v2",
        "PROFILE_EMBEDDING_MODEL_REVISION": "rev-test-1",
    }
    base.update(overrides)
    return Settings(**base)


def make_profile(
    user_id: uuid.UUID | None = None,
    vector: list[float] | None = None,
    model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
    model_revision: str = "rev-test-1",
) -> UserInterestProfile:
    return UserInterestProfile(
        user_id=user_id or uuid.uuid4(),
        model_name=model_name,
        model_revision=model_revision,
        interest_vector=vector,
        interaction_count=0,
    )


# ==============================================================================
# 1. Unit & Compatibility Validation Tests (No DB Query Executed)
# ==============================================================================

@pytest.mark.asyncio
class TestSemanticRetrieverCompatibilityAndValidation:
    """Verify profile/config guards, strict vector validation, and safe error handling."""

    async def test_implements_semantic_retriever_protocol(self):
        mock_session = AsyncMock(spec=AsyncSession)
        retriever = SemanticRetriever(session=mock_session, settings=make_settings())
        assert isinstance(retriever, SemanticRetrieverProtocol)

    async def test_missing_profile_returns_cold_start_without_query(self):
        mock_session = AsyncMock(spec=AsyncSession)
        retriever = SemanticRetriever(session=mock_session, settings=make_settings())

        batch = await retriever.retrieve(profile=None, limit=50)
        assert batch.status == "cold_start_no_vector"
        assert batch.hits == ()
        mock_session.execute.assert_not_called()

    async def test_null_interest_vector_returns_cold_start_without_query(self):
        mock_session = AsyncMock(spec=AsyncSession)
        retriever = SemanticRetriever(session=mock_session, settings=make_settings())
        profile = make_profile(vector=None)

        batch = await retriever.retrieve(profile=profile, limit=50)
        assert batch.status == "cold_start_no_vector"
        assert batch.hits == ()
        mock_session.execute.assert_not_called()

    async def test_unset_configured_revision_returns_revision_unspecified(self):
        mock_session = AsyncMock(spec=AsyncSession)
        cfg = make_settings(PROFILE_EMBEDDING_MODEL_REVISION=None)
        retriever = SemanticRetriever(session=mock_session, settings=cfg)
        profile = make_profile(vector=make_unit_vector())

        batch = await retriever.retrieve(profile=profile, limit=50)
        assert batch.status == "revision_unspecified"
        assert batch.model_revision is None
        assert batch.hits == ()
        mock_session.execute.assert_not_called()

    async def test_model_name_and_revision_mismatch_return_model_mismatch(self):
        mock_session = AsyncMock(spec=AsyncSession)
        retriever = SemanticRetriever(session=mock_session, settings=make_settings())
        vec = make_unit_vector()

        wrong_model = make_profile(vector=vec, model_name="other-model", model_revision="rev-test-1")
        batch_m = await retriever.retrieve(profile=wrong_model, limit=50)
        assert batch_m.status == "model_mismatch"
        assert batch_m.hits == ()

        wrong_rev = make_profile(vector=vec, model_revision="rev-old-0")
        batch_r = await retriever.retrieve(profile=wrong_rev, limit=50)
        assert batch_r.status == "model_mismatch"
        assert batch_r.hits == ()

        unset_rev = make_profile(vector=vec, model_revision="unset")
        batch_u = await retriever.retrieve(profile=unset_rev, limit=50)
        assert batch_u.status == "model_mismatch"
        assert batch_u.hits == ()

        mock_session.execute.assert_not_called()

    @pytest.mark.parametrize("bad_limit", [0, -1, -100, True, False])
    async def test_invalid_limit_rejected(self, bad_limit):
        mock_session = AsyncMock(spec=AsyncSession)
        retriever = SemanticRetriever(session=mock_session, settings=make_settings())
        profile = make_profile(vector=make_unit_vector())

        with pytest.raises(ValueError, match="limit"):
            await retriever.retrieve(profile=profile, limit=bad_limit)
        mock_session.execute.assert_not_called()

    async def test_invalid_dimensions_rejected_without_logging_raw_vector(self, caplog):
        mock_session = AsyncMock(spec=AsyncSession)
        retriever = SemanticRetriever(session=mock_session, settings=make_settings())

        short_vec = [0.123456] * 10
        profile = make_profile(vector=short_vec)

        with caplog.at_level(logging.WARNING):
            with pytest.raises(ValueError, match="dimension mismatch"):
                await retriever.retrieve(profile=profile, limit=10)

        mock_session.execute.assert_not_called()
        assert "0.123456" not in caplog.text

    @pytest.mark.parametrize(
        "bad_element",
        [True, False, float("nan"), float("inf"), float("-inf"), "bad", None],
    )
    async def test_invalid_vector_components_rejected(self, bad_element):
        mock_session = AsyncMock(spec=AsyncSession)
        retriever = SemanticRetriever(session=mock_session, settings=make_settings())

        bad_vec = [0.1] * 383 + [bad_element]
        profile = make_profile(vector=bad_vec)

        with pytest.raises(ValueError):
            await retriever.retrieve(profile=profile, limit=10)
        mock_session.execute.assert_not_called()

    async def test_near_zero_vector_rejected(self):
        mock_session = AsyncMock(spec=AsyncSession)
        retriever = SemanticRetriever(session=mock_session, settings=make_settings())
        zero_vec = [0.0] * 384
        profile = make_profile(vector=zero_vec)

        with pytest.raises(ValueError, match="near-zero"):
            await retriever.retrieve(profile=profile, limit=10)
        mock_session.execute.assert_not_called()

    async def test_database_error_logged_safely_and_propagated(self, caplog):
        mock_session = AsyncMock(spec=AsyncSession)
        mock_session.execute.side_effect = OperationalError("stmt", {}, Exception("conn lost"))

        retriever = SemanticRetriever(session=mock_session, settings=make_settings())
        vec = make_unit_vector(components={0: 0.987654, 1: 0.15665})
        profile = make_profile(vector=vec)

        with caplog.at_level(logging.ERROR):
            with pytest.raises(OperationalError):
                await retriever.retrieve(profile=profile, limit=10)

        # Ensure raw vector contents are never exposed in logs
        assert "0.987654" not in caplog.text
        assert "Database error executing pgvector semantic retrieval" in caplog.text


# ==============================================================================
# 2. PostgreSQL + pgvector Integration Tests
# ==============================================================================

@pytest.mark.asyncio
class TestSemanticRetrieverPostgresIntegration:
    """End-to-end pgvector cosine-distance retrieval against real PostgreSQL."""

    async def test_valid_semantic_retrieval_cosine_distance_and_ordering(self):
        async with get_test_session() as session:
            cfg = make_settings(PROFILE_EMBEDDING_MODEL_REVISION="rev-pg-1")
            retriever = SemanticRetriever(session=session, settings=cfg)

            user = User(email=f"sem_{uuid.uuid4().hex[:8]}@example.com", display_name="Sem User")
            session.add(user)
            await session.flush()

            # Create works
            w_exact = BookWork(canonical_title="Exact Match")
            w_close = BookWork(canonical_title="Close Match")
            w_ortho = BookWork(canonical_title="Orthogonal")
            w_opp = BookWork(canonical_title="Opposite")
            session.add_all([w_exact, w_close, w_ortho, w_opp])
            await session.flush()

            v_profile = make_unit_vector(components={0: 1.0})
            v_close = make_unit_vector(components={0: 0.8, 1: 0.6})     # cos sim = 0.8, dist = 0.2
            v_ortho = make_unit_vector(components={1: 1.0})              # cos sim = 0.0, dist = 1.0
            v_opp = make_unit_vector(components={0: -1.0})               # cos sim = -1.0, dist = 2.0

            for work, emb in [
                (w_exact, v_profile),
                (w_close, v_close),
                (w_ortho, v_ortho),
                (w_opp, v_opp),
            ]:
                session.add(
                    BookEmbedding(
                        work_id=work.id,
                        model_name=cfg.PROFILE_EMBEDDING_MODEL_NAME,
                        model_revision="rev-pg-1",
                        embedding=emb,
                        source_text_hash=f"hash-{work.id}",
                        is_active=True,
                    )
                )

            profile = make_profile(
                user_id=user.id,
                vector=v_profile,
                model_name=cfg.PROFILE_EMBEDDING_MODEL_NAME,
                model_revision="rev-pg-1",
            )
            session.add(profile)
            await session.flush()

            # Run retrieve_for_user with limit=10
            batch = await retriever.retrieve_for_user(user_id=user.id, limit=10)
            assert batch.status == "executed"
            assert batch.model_name == cfg.PROFILE_EMBEDDING_MODEL_NAME
            assert batch.model_revision == "rev-pg-1"
            assert [h.work_id for h in batch.hits] == [w_exact.id, w_close.id, w_ortho.id, w_opp.id]

            assert batch.hits[0].semantic_distance == pytest.approx(0.0, abs=1e-5)
            assert batch.hits[1].semantic_distance == pytest.approx(0.2, abs=1e-4)
            assert batch.hits[2].semantic_distance == pytest.approx(1.0, abs=1e-5)
            assert batch.hits[3].semantic_distance == pytest.approx(2.0, abs=1e-5)

    async def test_filters_inactive_wrong_model_and_wrong_revision_embeddings(self):
        async with get_test_session() as session:
            rev = f"rev-{uuid.uuid4().hex[:6]}"
            cfg = make_settings(PROFILE_EMBEDDING_MODEL_REVISION=rev)
            retriever = SemanticRetriever(session=session, settings=cfg)

            w_valid = BookWork(canonical_title="Valid Active")
            w_inactive = BookWork(canonical_title="Inactive")
            w_wrong_model = BookWork(canonical_title="Wrong Model")
            w_wrong_rev = BookWork(canonical_title="Wrong Revision")
            session.add_all([w_valid, w_inactive, w_wrong_model, w_wrong_rev])
            await session.flush()

            v = make_unit_vector(components={0: 1.0})
            v_far = make_unit_vector(components={1: 1.0})

            session.add_all([
                BookEmbedding(
                    work_id=w_valid.id,
                    model_name=cfg.PROFILE_EMBEDDING_MODEL_NAME,
                    model_revision=rev,
                    embedding=v_far,
                    source_text_hash="h-valid",
                    is_active=True,
                ),
                BookEmbedding(
                    work_id=w_inactive.id,
                    model_name=cfg.PROFILE_EMBEDDING_MODEL_NAME,
                    model_revision=rev,
                    embedding=v,
                    source_text_hash="h-inactive",
                    is_active=False,
                ),
                BookEmbedding(
                    work_id=w_wrong_model.id,
                    model_name="other-model",
                    model_revision=rev,
                    embedding=v,
                    source_text_hash="h-model",
                    is_active=True,
                ),
                BookEmbedding(
                    work_id=w_wrong_rev.id,
                    model_name=cfg.PROFILE_EMBEDDING_MODEL_NAME,
                    model_revision="old-rev",
                    embedding=v,
                    source_text_hash="h-rev",
                    is_active=True,
                ),
            ])
            await session.flush()

            profile = make_profile(
                vector=v,
                model_name=cfg.PROFILE_EMBEDDING_MODEL_NAME,
                model_revision=rev,
            )
            batch = await retriever.retrieve(profile=profile, limit=50)
            assert batch.status == "executed"
            assert [h.work_id for h in batch.hits] == [w_valid.id]

    async def test_deterministic_tie_breaking_limit_and_exclusions(self):
        async with get_test_session() as session:
            rev = f"rev-tie-{uuid.uuid4().hex[:6]}"
            cfg = make_settings(PROFILE_EMBEDDING_MODEL_REVISION=rev)
            retriever = SemanticRetriever(session=session, settings=cfg)

            works = [BookWork(canonical_title=f"Tie Work {i}") for i in range(4)]
            session.add_all(works)
            await session.flush()

            # All 4 works have the exact same embedding vector -> exact tie in distance
            v = make_unit_vector(components={2: 1.0})
            for w in works:
                session.add(
                    BookEmbedding(
                        work_id=w.id,
                        model_name=cfg.PROFILE_EMBEDDING_MODEL_NAME,
                        model_revision=rev,
                        embedding=v,
                        source_text_hash=f"h-{w.id}",
                        is_active=True,
                    )
                )
            await session.flush()

            sorted_ids = sorted(w.id for w in works)
            profile = make_profile(
                vector=v,
                model_name=cfg.PROFILE_EMBEDDING_MODEL_NAME,
                model_revision=rev,
            )

            # Respects limit and deterministic work_id ASC tie-break
            batch_limit = await retriever.retrieve(profile=profile, limit=2)
            assert batch_limit.status == "executed"
            assert [h.work_id for h in batch_limit.hits] == sorted_ids[:2]

            # Respects exclude_work_ids
            batch_excl = await retriever.retrieve(
                profile=profile,
                limit=10,
                exclude_work_ids=[sorted_ids[0], sorted_ids[2]],
            )
            assert [h.work_id for h in batch_excl.hits] == [sorted_ids[1], sorted_ids[3]]

    async def test_empty_result_set_returns_executed_empty(self):
        async with get_test_session() as session:
            rev = f"rev-empty-{uuid.uuid4().hex[:8]}"
            cfg = make_settings(PROFILE_EMBEDDING_MODEL_REVISION=rev)
            retriever = SemanticRetriever(session=session, settings=cfg)

            profile = make_profile(
                vector=make_unit_vector(),
                model_name=cfg.PROFILE_EMBEDDING_MODEL_NAME,
                model_revision=rev,
            )
            batch = await retriever.retrieve(profile=profile, limit=20)
            assert batch.status == "executed"
            assert batch.hits == ()

    async def test_limit_capped_at_max_expansion_limit_and_no_payload_selected(self):
        async with get_test_session() as session:
            rev = f"rev-sql-{uuid.uuid4().hex[:6]}"
            cfg = make_settings(
                PROFILE_EMBEDDING_MODEL_REVISION=rev,
                RETRIEVAL_SEMANTIC_INITIAL_LIMIT=3,
                RETRIEVAL_METADATA_INITIAL_LIMIT=3,
                CANDIDATE_TARGET_COUNT=3,
                RETRIEVAL_MAX_EXPANSION_LIMIT=5,
            )
            retriever = SemanticRetriever(session=session, settings=cfg)

            works = [BookWork(canonical_title=f"Cap Work {i}") for i in range(7)]
            session.add_all(works)
            await session.flush()

            v = make_unit_vector(components={3: 1.0})
            for w in works:
                session.add(
                    BookEmbedding(
                        work_id=w.id,
                        model_name=cfg.PROFILE_EMBEDDING_MODEL_NAME,
                        model_revision=rev,
                        embedding=v,
                        source_text_hash=f"h-{w.id}",
                        is_active=True,
                    )
                )
            await session.flush()

            executed_sql: list[str] = []

            def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
                executed_sql.append(statement)

            sync_conn = await session.connection()
            event.listen(sync_conn.sync_connection, "before_cursor_execute", before_cursor_execute)
            try:
                profile = make_profile(
                    vector=v,
                    model_name=cfg.PROFILE_EMBEDDING_MODEL_NAME,
                    model_revision=rev,
                )
                # Caller passes limit=100, should clamp to RETRIEVAL_MAX_EXPANSION_LIMIT=5
                batch = await retriever.retrieve(profile=profile, limit=100)
            finally:
                event.remove(sync_conn.sync_connection, "before_cursor_execute", before_cursor_execute)

            # Exactly 1 query executed (no N+1)
            assert len(executed_sql) == 1
            sql = executed_sql[0]

            # Clamped to max expansion limit (5)
            assert len(batch.hits) == 5

            # Selects only work_id + cosine distance, never materializing description or raw vector column in SELECT projection
            select_clause = sql.split("FROM")[0]
            assert "work_id" in select_clause
            assert "semantic_distance" in select_clause
            assert "description" not in select_clause
            assert "canonical_title" not in select_clause
            assert "<=>" in sql

    async def test_retrieve_for_user_missing_profile_in_db(self):
        async with get_test_session() as session:
            cfg = make_settings(PROFILE_EMBEDDING_MODEL_REVISION="rev-missing-prof")
            retriever = SemanticRetriever(session=session, settings=cfg)
            batch = await retriever.retrieve_for_user(user_id=uuid.uuid4())
            assert batch.status == "cold_start_no_vector"
            assert batch.hits == ()
