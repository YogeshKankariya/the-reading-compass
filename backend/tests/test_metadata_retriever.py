import uuid
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from backend.core.config import Settings, settings
from backend.db.models.author import Author, BookWorkAuthor
from backend.db.models.book import BookWork
from backend.db.models.genre import BookWorkGenre, Genre
from backend.db.models.recommendation import UserInterestProfile
from backend.db.models.subject import BookWorkSubject, Subject
from backend.db.models.user import User, UserPreference
from backend.schemas.retrieval import RetrievalRequestContext
from backend.services.retrieval import (
    MetadataRetriever,
    MetadataRetrieverProtocol,
    normalize_author_name,
)

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


def make_settings(**overrides) -> Settings:
    base = {
        "DATABASE_URL": settings.DATABASE_URL,
        "RETRIEVAL_MIN_INFERRED_TOPIC_AFFINITY": 0.10,
        "RETRIEVAL_METADATA_TOPIC_TOP_N": 5,
    }
    base.update(overrides)
    return Settings(**base)


def make_context(user_id: uuid.UUID, **overrides) -> RetrievalRequestContext:
    return RetrievalRequestContext(user_id=user_id, **overrides)


# ==============================================================================
# 1. Unit Tests: Protocol, Normalization, Validation & Error Handling
# ==============================================================================

def test_author_name_normalization():
    assert normalize_author_name("Robert C. Martin") == "robert c martin"
    assert normalize_author_name("  robert   c.  martin ") == "robert c martin"
    assert normalize_author_name("J.R.R. Tolkien") == "j r r tolkien"
    assert normalize_author_name("Neil Gaiman") == "neil gaiman"
    assert normalize_author_name("Jean-Luc Picard") == "jean luc picard"
    assert normalize_author_name("   ...   ") == ""


@pytest.mark.asyncio
class TestMetadataRetrieverUnitAndValidation:
    """Verify protocol conformance, author normalization, affinity validation, and errors."""

    async def test_implements_metadata_retriever_protocol(self):
        mock_session = AsyncMock(spec=AsyncSession)
        retriever = MetadataRetriever(session=mock_session, settings=make_settings())
        assert isinstance(retriever, MetadataRetrieverProtocol)

    @pytest.mark.parametrize("bad_limit", [0, -5, True, False])
    async def test_invalid_limit_rejected(self, bad_limit):
        mock_session = AsyncMock(spec=AsyncSession)
        retriever = MetadataRetriever(session=mock_session, settings=make_settings())
        uid = uuid.uuid4()
        with pytest.raises(ValueError, match="limit"):
            await retriever.retrieve(
                user_id=uid,
                profile=None,
                context=make_context(uid),
                limit=bad_limit,
            )
        mock_session.execute.assert_not_called()

    async def test_mismatched_context_user_id_rejected(self):
        mock_session = AsyncMock(spec=AsyncSession)
        retriever = MetadataRetriever(session=mock_session, settings=make_settings())
        with pytest.raises(ValueError, match="does not match"):
            await retriever.retrieve(
                user_id=uuid.uuid4(),
                profile=None,
                context=make_context(uuid.uuid4()),
                limit=50,
            )

    @pytest.mark.parametrize(
        "bad_topics",
        [
            "not-a-dict",
            {"genres": ["invalid-list"]},
            {"genres": {"not-a-uuid": {"affinity": 0.5}}},
            {"genres": {str(uuid.uuid4()): "not-a-dict"}},
            {"genres": {str(uuid.uuid4()): {"interaction_count": 2}}},  # missing affinity
            {"genres": {str(uuid.uuid4()): {"affinity": True}}},
            {"subjects": {str(uuid.uuid4()): {"affinity": float("nan")}}},
            {"subjects": {str(uuid.uuid4()): {"affinity": float("inf")}}},
            {"subjects": {str(uuid.uuid4()): {"affinity": -0.1}}},
            {"subjects": {str(uuid.uuid4()): {"affinity": 1.2}}},
            {"subjects": {str(uuid.uuid4()): {"affinity": "abc"}}},
        ],
    )
    async def test_malformed_inferred_affinity_data_rejected(self, bad_topics):
        mock_session = AsyncMock(spec=AsyncSession)
        retriever = MetadataRetriever(session=mock_session, settings=make_settings())
        uid = uuid.uuid4()
        profile = UserInterestProfile(
            user_id=uid,
            model_name="m",
            model_revision="r",
            inferred_topics=bad_topics,
        )
        with pytest.raises(ValueError):
            await retriever.retrieve(
                user_id=uid,
                profile=profile,
                context=make_context(uid),
                limit=20,
            )
        mock_session.execute.assert_not_called()

    async def test_database_error_propagated_cleanly(self, caplog):
        mock_session = AsyncMock(spec=AsyncSession)
        mock_session.execute.side_effect = OperationalError("stmt", {}, Exception("db down"))
        retriever = MetadataRetriever(session=mock_session, settings=make_settings())
        uid = uuid.uuid4()

        with pytest.raises(OperationalError):
            await retriever.retrieve(
                user_id=uid,
                profile=None,
                context=make_context(uid),
                limit=20,
            )
        assert "Database error loading UserPreference" in caplog.text


# ==============================================================================
# 2. PostgreSQL Integration Tests: Explicit Preference Retrieval
# ==============================================================================

@pytest.mark.asyncio
class TestMetadataRetrieverExplicitPreferences:
    """Verify genre, subject, and strict author matching against real PostgreSQL."""

    async def test_explicit_genre_subject_and_multi_match_provenance(self):
        async with get_test_session() as session:
            retriever = MetadataRetriever(session=session, settings=make_settings())

            user = User(email=f"exp_{uuid.uuid4().hex[:8]}@example.com", display_name="Exp")
            session.add(user)
            await session.flush()

            g1 = Genre(name=f"SciFi-{uuid.uuid4().hex[:6]}", slug=f"scifi-{uuid.uuid4().hex[:6]}")
            s1 = Subject(name=f"Systems-{uuid.uuid4().hex[:6]}", slug=f"systems-{uuid.uuid4().hex[:6]}")
            a1 = Author(name="Donella Meadows", normalized_name="donella meadows")
            session.add_all([g1, s1, a1])
            await session.flush()

            w_all = BookWork(canonical_title="Matches Genre, Subject, Author")
            w_genre_only = BookWork(canonical_title="Matches Genre Only")
            w_unrelated = BookWork(canonical_title="Unrelated Work")
            session.add_all([w_all, w_genre_only, w_unrelated])
            await session.flush()

            session.add_all([
                BookWorkGenre(work_id=w_all.id, genre_id=g1.id),
                BookWorkSubject(work_id=w_all.id, subject_id=s1.id, relevance_weight=Decimal("0.900")),
                BookWorkAuthor(work_id=w_all.id, author_id=a1.id),
                BookWorkGenre(work_id=w_genre_only.id, genre_id=g1.id),
            ])

            # Store preferences by UUID strings for genre/subject/author
            session.add_all([
                UserPreference(user_id=user.id, preference_type="genre", preference_value=str(g1.id), preference_weight=Decimal("1.000")),
                UserPreference(user_id=user.id, preference_type="subject", preference_value=str(s1.id), preference_weight=Decimal("0.800")),
                UserPreference(user_id=user.id, preference_type="author", preference_value=str(a1.id), preference_weight=Decimal("0.900")),
            ])
            await session.flush()

            batch = await retriever.retrieve(
                user_id=user.id,
                profile=None,
                context=make_context(user.id),
                limit=50,
            )
            assert batch.used_cold_start_baseline is False

            # No duplicate work_ids; w_unrelated excluded
            hit_map = {h.work_id: h for h in batch.hits}
            assert len(hit_map) == len(batch.hits) == 2
            assert set(hit_map.keys()) == {w_all.id, w_genre_only.id}

            # Multi-channel candidate preserves all provenance and matched IDs
            hit_all = hit_map[w_all.id]
            assert hit_all.retrieval_sources == ("explicit_author", "explicit_genre", "explicit_subject")
            assert hit_all.matched_genres == (g1.id,)
            assert hit_all.matched_subjects == (s1.id,)
            assert hit_all.matched_authors == (a1.id,)

            hit_g = hit_map[w_genre_only.id]
            assert hit_g.retrieval_sources == ("explicit_genre",)
            assert hit_g.matched_genres == (g1.id,)
            assert hit_g.matched_subjects == ()
            assert hit_g.matched_authors == ()

    async def test_author_matching_uuid_exact_normalized_no_fuzzy_ambiguity_and_unresolved(self):
        async with get_test_session() as session:
            retriever = MetadataRetriever(session=session, settings=make_settings())

            user = User(email=f"auth_{uuid.uuid4().hex[:8]}@example.com", display_name="Auth User")
            session.add(user)
            await session.flush()

            uniq_tag = uuid.uuid4().hex[:6]
            # Exact author via normalized name
            a_exact = Author(name="Robert C. Martin", normalized_name=f"robert c martin {uniq_tag}")
            # Partial/similar name that should NOT be matched by fuzzy logic
            a_partial = Author(name="Robert Martin Jr", normalized_name=f"robert martin jr {uniq_tag}")
            # Ambiguous authors sharing identical normalized_name
            a_ambig_1 = Author(name="John Smith", normalized_name=f"john smith {uniq_tag}")
            a_ambig_2 = Author(name="John Smith", normalized_name=f"john smith {uniq_tag}")
            # Author matched by UUID directly
            a_uuid = Author(name="Ursula Le Guin", normalized_name=f"ursula le guin {uniq_tag}")

            session.add_all([a_exact, a_partial, a_ambig_1, a_ambig_2, a_uuid])
            await session.flush()

            w_exact = BookWork(canonical_title="Clean Code")
            w_partial = BookWork(canonical_title="Partial Should Not Match")
            w_ambig1 = BookWork(canonical_title="Ambig 1")
            w_ambig2 = BookWork(canonical_title="Ambig 2")
            w_uuid = BookWork(canonical_title="Earthsea")
            session.add_all([w_exact, w_partial, w_ambig1, w_ambig2, w_uuid])
            await session.flush()

            session.add_all([
                BookWorkAuthor(work_id=w_exact.id, author_id=a_exact.id),
                BookWorkAuthor(work_id=w_partial.id, author_id=a_partial.id),
                BookWorkAuthor(work_id=w_ambig1.id, author_id=a_ambig_1.id),
                BookWorkAuthor(work_id=w_ambig2.id, author_id=a_ambig_2.id),
                BookWorkAuthor(work_id=w_uuid.id, author_id=a_uuid.id),
            ])

            session.add_all([
                # Punctuated/uppercase input -> resolves cleanly to exact normalized_name
                UserPreference(user_id=user.id, preference_type="author", preference_value=f"  Robert C. Martin {uniq_tag} "),
                # Ambiguous normalized_name -> safely ignored
                UserPreference(user_id=user.id, preference_type="author", preference_value=f"John Smith {uniq_tag}"),
                # Completely unresolved -> safely ignored
                UserPreference(user_id=user.id, preference_type="author", preference_value=f"Nonexistent Author {uniq_tag}"),
                # Explicit UUID -> resolves cleanly
                UserPreference(user_id=user.id, preference_type="author", preference_value=str(a_uuid.id)),
            ])
            await session.flush()

            batch = await retriever.retrieve(
                user_id=user.id,
                profile=None,
                context=make_context(user.id),
                limit=50,
            )
            assert batch.used_cold_start_baseline is False
            matched_work_ids = {h.work_id for h in batch.hits}
            assert matched_work_ids == {w_exact.id, w_uuid.id}

            # Verify no fuzzy or ambiguous works slipped in
            assert w_partial.id not in matched_work_ids
            assert w_ambig1.id not in matched_work_ids
            assert w_ambig2.id not in matched_work_ids

    async def test_explicit_genre_and_subject_slug_and_case_insensitive_name_resolution(self):
        async with get_test_session() as session:
            retriever = MetadataRetriever(session=session, settings=make_settings())
            user = User(email=f"slug_{uuid.uuid4().hex[:8]}@example.com", display_name="Slug User")
            session.add(user)
            await session.flush()

            tag = uuid.uuid4().hex[:6]
            g = Genre(name=f"Science Fiction {tag}", slug=f"science-fiction-{tag}")
            s = Subject(name=f"Distributed Systems {tag}", slug=f"distributed-systems-{tag}")
            session.add_all([g, s])
            await session.flush()

            w_g = BookWork(canonical_title="Dune")
            w_s = BookWork(canonical_title="Designing Data-Intensive Applications")
            session.add_all([w_g, w_s])
            await session.flush()

            session.add_all([
                BookWorkGenre(work_id=w_g.id, genre_id=g.id),
                BookWorkSubject(work_id=w_s.id, subject_id=s.id),
            ])
            session.add_all([
                UserPreference(user_id=user.id, preference_type="genre", preference_value=f"science-fiction-{tag}"),
                UserPreference(user_id=user.id, preference_type="subject", preference_value=f"distributed systems {tag}"),
            ])
            await session.flush()

            batch = await retriever.retrieve(
                user_id=user.id,
                profile=None,
                context=make_context(user.id),
                limit=50,
            )
            hit_map = {h.work_id: h for h in batch.hits}
            assert set(hit_map.keys()) == {w_g.id, w_s.id}
            assert hit_map[w_g.id].matched_genres == (g.id,)
            assert hit_map[w_s.id].matched_subjects == (s.id,)


# ==============================================================================
# 3. PostgreSQL Integration Tests: Inferred Preferences & Separation
# ==============================================================================

@pytest.mark.asyncio
class TestMetadataRetrieverInferredTopics:
    """Verify threshold filtering, top-N, tie-breaking, relevance weights, and explicit separation."""

    async def test_inferred_thresholds_top_n_and_uuid_tie_breaking(self):
        async with get_test_session() as session:
            cfg = make_settings(
                RETRIEVAL_MIN_INFERRED_TOPIC_AFFINITY=0.20,
                RETRIEVAL_METADATA_TOPIC_TOP_N=2,
            )
            retriever = MetadataRetriever(session=session, settings=cfg)

            user = User(email=f"inf_{uuid.uuid4().hex[:8]}@example.com", display_name="Inf User")
            session.add(user)
            await session.flush()

            # Create 4 genres with deterministic UUID ordering for tie-breaking check
            g_ids = sorted([uuid.uuid4() for _ in range(4)], key=str)
            g_tie_low_uuid = Genre(id=g_ids[0], name=f"G0-{uuid.uuid4().hex[:6]}", slug=f"g0-{uuid.uuid4().hex[:6]}")
            g_tie_high_uuid = Genre(id=g_ids[1], name=f"G1-{uuid.uuid4().hex[:6]}", slug=f"g1-{uuid.uuid4().hex[:6]}")
            g_top = Genre(id=g_ids[2], name=f"G2-{uuid.uuid4().hex[:6]}", slug=f"g2-{uuid.uuid4().hex[:6]}")
            g_below_thresh = Genre(id=g_ids[3], name=f"G3-{uuid.uuid4().hex[:6]}", slug=f"g3-{uuid.uuid4().hex[:6]}")
            session.add_all([g_tie_low_uuid, g_tie_high_uuid, g_top, g_below_thresh])
            await session.flush()

            w_top = BookWork(canonical_title="Top Genre Book")
            w_tie_win = BookWork(canonical_title="Tie Win Genre Book")
            w_tie_lose = BookWork(canonical_title="Tie Lose Out of Top-N")
            w_below = BookWork(canonical_title="Below Threshold Book")
            session.add_all([w_top, w_tie_win, w_tie_lose, w_below])
            await session.flush()

            session.add_all([
                BookWorkGenre(work_id=w_top.id, genre_id=g_top.id),
                BookWorkGenre(work_id=w_tie_win.id, genre_id=g_tie_low_uuid.id),
                BookWorkGenre(work_id=w_tie_lose.id, genre_id=g_tie_high_uuid.id),
                BookWorkGenre(work_id=w_below.id, genre_id=g_below_thresh.id),
            ])
            await session.flush()

            # Top-N is 2: g_top (0.9) is #1; tie at 0.5 between g_ids[0] and g_ids[1] picks lower UUID g_ids[0] as #2!
            # g_below_thresh (0.15 < 0.20) is filtered by threshold.
            profile = UserInterestProfile(
                user_id=user.id,
                model_name="m",
                model_revision="r",
                inferred_topics={
                    "genres": {
                        str(g_tie_high_uuid.id): {"affinity": 0.50, "interaction_count": 1},
                        str(g_tie_low_uuid.id): {"affinity": 0.50, "interaction_count": 1},
                        str(g_top.id): {"affinity": 0.90, "interaction_count": 2},
                        str(g_below_thresh.id): {"affinity": 0.15, "interaction_count": 1},
                    },
                    "subjects": {},
                },
            )

            batch = await retriever.retrieve(
                user_id=user.id,
                profile=profile,
                context=make_context(user.id),
                limit=50,
            )
            assert [h.work_id for h in batch.hits] == [w_top.id, w_tie_win.id]
            assert all(h.retrieval_sources == ("inferred_genre",) for h in batch.hits)

    async def test_subject_relevance_weight_and_null_as_one_and_provenance_separation(self):
        async with get_test_session() as session:
            retriever = MetadataRetriever(session=session, settings=make_settings())

            user = User(email=f"rel_{uuid.uuid4().hex[:8]}@example.com", display_name="Rel User")
            session.add(user)
            await session.flush()

            s_exp = Subject(name=f"ExpSub-{uuid.uuid4().hex[:6]}", slug=f"expsub-{uuid.uuid4().hex[:6]}")
            s_inf = Subject(name=f"InfSub-{uuid.uuid4().hex[:6]}", slug=f"infsub-{uuid.uuid4().hex[:6]}")
            session.add_all([s_exp, s_inf])
            await session.flush()

            w_null_rel = BookWork(canonical_title="Null Rel Equals 1.0")
            w_low_rel = BookWork(canonical_title="Low Rel 0.25")
            w_zero_rel = BookWork(canonical_title="Zero Rel Excluded")
            w_shared = BookWork(canonical_title="Shared Explicit And Inferred")
            session.add_all([w_null_rel, w_low_rel, w_zero_rel, w_shared])
            await session.flush()

            session.add_all([
                BookWorkSubject(work_id=w_null_rel.id, subject_id=s_inf.id, relevance_weight=None),
                BookWorkSubject(work_id=w_low_rel.id, subject_id=s_inf.id, relevance_weight=Decimal("0.250")),
                BookWorkSubject(work_id=w_zero_rel.id, subject_id=s_inf.id, relevance_weight=Decimal("0.000")),
                BookWorkSubject(work_id=w_shared.id, subject_id=s_exp.id, relevance_weight=Decimal("1.000")),
                BookWorkSubject(work_id=w_shared.id, subject_id=s_inf.id, relevance_weight=Decimal("1.000")),
            ])

            # Add explicit subject preference for s_exp
            session.add(
                UserPreference(user_id=user.id, preference_type="subject", preference_value=str(s_exp.id), preference_weight=Decimal("0.900"))
            )
            await session.flush()

            profile = UserInterestProfile(
                user_id=user.id,
                model_name="m",
                model_revision="r",
                inferred_topics={
                    "genres": {},
                    "subjects": {
                        str(s_inf.id): {"affinity": 0.80, "interaction_count": 3},
                    },
                },
            )

            batch = await retriever.retrieve(
                user_id=user.id,
                profile=profile,
                context=make_context(user.id),
                limit=50,
            )
            hit_map = {h.work_id: h for h in batch.hits}

            # Zero relevance excluded
            assert w_zero_rel.id not in hit_map

            # w_shared keeps explicit_subject AND inferred_subject separate
            assert hit_map[w_shared.id].retrieval_sources == ("explicit_subject", "inferred_subject")
            assert hit_map[w_shared.id].matched_subjects == tuple(sorted([s_exp.id, s_inf.id], key=str))

            # Null relevance (1.0 * 0.8 = 0.8) ranks ahead of 0.25 (0.25 * 0.8 = 0.2)
            ordered_ids = [h.work_id for h in batch.hits]
            assert ordered_ids.index(w_null_rel.id) < ordered_ids.index(w_low_rel.id)


# ==============================================================================
# 4. Cold-Start, Baseline Ordering & Query Performance Tests
# ==============================================================================

@pytest.mark.asyncio
class TestMetadataRetrieverColdStartAndQueryBehavior:
    """Verify cold-start modes, deterministic baseline ordering, limits, and SQL efficiency."""

    async def test_cold_start_no_preferences_deterministic_baseline_ordering(self):
        async with get_test_session() as session:
            retriever = MetadataRetriever(session=session, settings=make_settings())
            uid = uuid.uuid4()

            t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
            t1 = t0 + timedelta(days=5)

            # Equal created_at verified works to verify UUID tie-break
            v_ids = sorted([uuid.uuid4() for _ in range(2)], key=str)
            w_ver_old = BookWork(id=v_ids[0], canonical_title="Verified Old", metadata_status="verified", created_at=t0)
            w_ver_new1 = BookWork(id=v_ids[1], canonical_title="Verified New", metadata_status="verified", created_at=t1)
            w_unver_new = BookWork(canonical_title="Unverified Newer", metadata_status="unverified", created_at=t1 + timedelta(days=1))
            session.add_all([w_unver_new, w_ver_old, w_ver_new1])
            await session.flush()

            batch1 = await retriever.retrieve(
                user_id=uid,
                profile=None,
                context=make_context(uid),
                limit=50,
            )
            batch2 = await retriever.retrieve(
                user_id=uid,
                profile=UserInterestProfile(user_id=uid, model_name="m", model_revision="r", interest_vector=None, inferred_topics=None),
                context=make_context(uid),
                limit=50,
            )

            assert batch1.used_cold_start_baseline is True
            assert batch1 == batch2
            # Verified works first (newer -> older), then unverified
            assert [h.work_id for h in batch1.hits] == [w_ver_new1.id, w_ver_old.id, w_unver_new.id]
            assert all(h.retrieval_sources == ("cold_start_baseline",) for h in batch1.hits)

    async def test_cold_start_null_vector_uses_explicit_or_inferred_without_baseline(self):
        async with get_test_session() as session:
            retriever = MetadataRetriever(session=session, settings=make_settings())
            user = User(email=f"cs_{uuid.uuid4().hex[:8]}@example.com", display_name="CS User")
            session.add(user)
            await session.flush()

            g = Genre(name=f"CS-Genre-{uuid.uuid4().hex[:6]}", slug=f"cs-genre-{uuid.uuid4().hex[:6]}")
            w_match = BookWork(canonical_title="Matched Unembedded")
            w_other = BookWork(canonical_title="Verified Other", metadata_status="verified")
            session.add_all([g, w_match, w_other])
            await session.flush()
            session.add(BookWorkGenre(work_id=w_match.id, genre_id=g.id))

            # Null vector, but inferred topic present
            profile = UserInterestProfile(
                user_id=user.id,
                model_name="m",
                model_revision="r",
                interest_vector=None,
                inferred_topics={"genres": {str(g.id): {"affinity": 0.85, "interaction_count": 1}}, "subjects": {}},
            )
            session.add(profile)
            await session.flush()

            batch = await retriever.retrieve_for_user(user_id=user.id, limit=20)
            assert batch.used_cold_start_baseline is False
            assert [h.work_id for h in batch.hits] == [w_match.id]
            assert batch.hits[0].retrieval_sources == ("inferred_genre",)

    async def test_empty_catalogue_cold_start_returns_empty_cleanly(self):
        async with get_test_session() as session:
            retriever = MetadataRetriever(session=session, settings=make_settings())
            uid = uuid.uuid4()
            batch = await retriever.retrieve_for_user(user_id=uid, limit=20)
            assert batch.used_cold_start_baseline is True
            assert batch.hits == ()

    async def test_limit_enforcement_bounded_queries_and_no_payload_materialization(self):
        async with get_test_session() as session:
            cfg = make_settings(
                CANDIDATE_TARGET_COUNT=3,
                RETRIEVAL_SEMANTIC_INITIAL_LIMIT=3,
                RETRIEVAL_METADATA_INITIAL_LIMIT=3,
                RETRIEVAL_MAX_EXPANSION_LIMIT=4,
            )
            retriever = MetadataRetriever(session=session, settings=cfg)

            user = User(email=f"sql_{uuid.uuid4().hex[:8]}@example.com", display_name="SQL User")
            session.add(user)
            await session.flush()

            g = Genre(name=f"SQL-G-{uuid.uuid4().hex[:6]}", slug=f"sql-g-{uuid.uuid4().hex[:6]}")
            session.add(g)
            works = [BookWork(canonical_title=f"SQL Book {i}", description="Heavy description") for i in range(7)]
            session.add_all(works)
            await session.flush()

            for w in works:
                session.add(BookWorkGenre(work_id=w.id, genre_id=g.id))

            session.add(
                UserPreference(user_id=user.id, preference_type="genre", preference_value=str(g.id))
            )
            await session.flush()

            executed_sql: list[str] = []

            def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
                executed_sql.append(statement)

            sync_conn = await session.connection()
            event.listen(sync_conn.sync_connection, "before_cursor_execute", before_cursor_execute)
            try:
                # Request limit=100 -> capped at RETRIEVAL_MAX_EXPANSION_LIMIT=4
                batch = await retriever.retrieve(
                    user_id=user.id,
                    profile=None,
                    context=make_context(user.id),
                    limit=100,
                )
            finally:
                event.remove(sync_conn.sync_connection, "before_cursor_execute", before_cursor_execute)

            assert len(batch.hits) == 4
            # Bounded query count: 1 (user_preferences) + 1 (genres resolve) + 1 (book_work_genres) = 3
            assert len(executed_sql) == 3

            for sql in executed_sql:
                select_clause = sql.split("FROM")[0]
                assert "description" not in select_clause
                assert "book_editions" not in sql
                assert "book_embeddings" not in sql


# ==============================================================================
# 5. Channel-Balanced Round-Robin & Duplicate Author Role Tests
# ==============================================================================

class TestRoundRobinSelectionEdgeCases:
    """Unit tests for deterministic round-robin queue rotation and deduplication."""

    def test_empty_and_unequal_queues_with_duplicates(self):
        w1, w2, w3, w4, w5 = [uuid.UUID(int=i) for i in range(1, 6)]

        # All empty queues
        assert MetadataRetriever._round_robin_select([], limit=10) == []
        assert MetadataRetriever._round_robin_select([deque(), deque()], limit=10) == []

        # Unequal lengths + duplicate work IDs across channels
        q_exp_genre = deque([w1, w2, w3])
        q_empty = deque()
        q_exp_author = deque([w1, w4])        # w1 already selected by genre -> turn advances to w4!
        q_inf_subject = deque([w2, w5])       # w2 already selected by genre -> turn advances to w5!

        selected = MetadataRetriever._round_robin_select(
            [q_exp_genre, q_empty, q_exp_author, q_inf_subject],
            limit=10,
        )
        # Rotation 1: w1 (genre), w4 (author skips duplicate w1), w5 (inferred_subj skips w2? Wait: w2 wasn't selected yet in R1!)
        # Trace R1: genre->w1, empty->skip, author->skips w1 pops w4, inf_subj->pops w2!
        # Trace R2: genre->skips w2 pops w3, author->empty, inf_subj->pops w5!
        assert selected == [w1, w4, w2, w3, w5]


@pytest.mark.asyncio
class TestChannelStarvationAndDistinctAuthorsIntegration:
    """PostgreSQL integration tests for channel-balanced round-robin and multi-role authors."""

    async def test_multi_channel_truncation_prevents_genre_starvation_of_author_and_inferred_channels(self):
        """Test A: Small limit (4) retains explicit-author and inferred candidates alongside multi-genre matches."""
        async with get_test_session() as session:
            retriever = MetadataRetriever(session=session, settings=make_settings())

            user = User(email=f"rr_{uuid.uuid4().hex[:8]}@example.com", display_name="RR User")
            session.add(user)
            await session.flush()

            tag = uuid.uuid4().hex[:6]
            g1 = Genre(name=f"RR-G1-{tag}", slug=f"rr-g1-{tag}")
            g2 = Genre(name=f"RR-G2-{tag}", slug=f"rr-g2-{tag}")
            author = Author(name="Author Priority", normalized_name=f"author priority {tag}")
            s_inf = Subject(name=f"RR-Sub-{tag}", slug=f"rr-sub-{tag}")
            session.add_all([g1, g2, author, s_inf])
            await session.flush()

            genre_works = [BookWork(canonical_title=f"Genre Book {i}") for i in range(4)]
            author_works = [BookWork(canonical_title=f"Author Book {i}") for i in range(2)]
            inf_work = BookWork(canonical_title="Inferred Subject Book")
            session.add_all(genre_works + author_works + [inf_work])
            await session.flush()

            # All 4 genre books match BOTH g1 and g2 (explicit strength = 2.0)
            for gw in genre_works:
                session.add_all([
                    BookWorkGenre(work_id=gw.id, genre_id=g1.id),
                    BookWorkGenre(work_id=gw.id, genre_id=g2.id),
                ])

            for aw in author_works:
                session.add(BookWorkAuthor(work_id=aw.id, author_id=author.id))

            session.add(BookWorkSubject(work_id=inf_work.id, subject_id=s_inf.id, relevance_weight=Decimal("1.000")))

            session.add_all([
                UserPreference(user_id=user.id, preference_type="genre", preference_value=str(g1.id), preference_weight=Decimal("1.000")),
                UserPreference(user_id=user.id, preference_type="genre", preference_value=str(g2.id), preference_weight=Decimal("1.000")),
                UserPreference(user_id=user.id, preference_type="author", preference_value=str(author.id), preference_weight=Decimal("1.000")),
            ])
            await session.flush()

            profile = UserInterestProfile(
                user_id=user.id,
                model_name="m",
                model_revision="r",
                inferred_topics={
                    "genres": {},
                    "subjects": {str(s_inf.id): {"affinity": 0.9, "interaction_count": 1}},
                },
            )

            # With limit=4: R1 takes 1 genre, 1 author, 1 inferred_subject; R2 takes 1 genre -> 4 total!
            batch1 = await retriever.retrieve(
                user_id=user.id,
                profile=profile,
                context=make_context(user.id),
                limit=4,
            )
            batch2 = await retriever.retrieve(
                user_id=user.id,
                profile=profile,
                context=make_context(user.id),
                limit=4,
            )

            assert batch1 == batch2
            assert len(batch1.hits) == 4

            selected_sources = {src for hit in batch1.hits for src in hit.retrieval_sources}
            assert "explicit_genre" in selected_sources
            assert "explicit_author" in selected_sources
            assert "inferred_subject" in selected_sources

            # Verify author candidates were NOT starved out by 4 multi-genre books
            author_hit_ids = {h.work_id for h in batch1.hits if "explicit_author" in h.retrieval_sources}
            assert len(author_hit_ids) == 1
            assert next(iter(author_hit_ids)) in {aw.id for aw in author_works}

    async def test_duplicate_author_roles_on_same_work_deduplicated_and_no_strength_inflation(self):
        """Test B: Author holding multiple roles on one work uses DISTINCT aggregation and MAX weight."""
        async with get_test_session() as session:
            retriever = MetadataRetriever(session=session, settings=make_settings())

            user = User(email=f"roles_{uuid.uuid4().hex[:8]}@example.com", display_name="Roles User")
            session.add(user)
            await session.flush()

            tag = uuid.uuid4().hex[:6]
            a_multi_role = Author(name="Multi Role Author", normalized_name=f"multi role {tag}")
            a_higher_weight = Author(name="Higher Weight Single", normalized_name=f"higher weight {tag}")
            session.add_all([a_multi_role, a_higher_weight])
            await session.flush()

            # w_multi has weight 0.6 * 2 roles; if wrongly summed it would be 1.2 > 0.9!
            # w_single has weight 0.9 * 1 role; with MAX it must order ahead of w_multi!
            w_multi = BookWork(canonical_title="Multi-Role Work")
            w_single = BookWork(canonical_title="Single-Role Higher Weight Work")
            session.add_all([w_multi, w_single])
            await session.flush()

            session.add_all([
                BookWorkAuthor(work_id=w_multi.id, author_id=a_multi_role.id, author_role="author", display_order=1),
                BookWorkAuthor(work_id=w_multi.id, author_id=a_multi_role.id, author_role="illustrator", display_order=2),
                BookWorkAuthor(work_id=w_single.id, author_id=a_higher_weight.id, author_role="author", display_order=1),
            ])

            session.add_all([
                UserPreference(user_id=user.id, preference_type="author", preference_value=str(a_multi_role.id), preference_weight=Decimal("0.600")),
                UserPreference(user_id=user.id, preference_type="author", preference_value=str(a_higher_weight.id), preference_weight=Decimal("0.900")),
            ])
            await session.flush()

            # Verify raw SQL query returns DISTINCT author_id once and non-inflated MAX weight
            raw_rows = await retriever._query_author_candidates(
                {a_multi_role.id: 0.6, a_higher_weight.id: 0.9},
                limit=10,
            )
            assert len(raw_rows) == 2
            assert raw_rows[0] == (w_single.id, [a_higher_weight.id], 0.9)
            assert raw_rows[1] == (w_multi.id, [a_multi_role.id], 0.6)

            # Verify public retrieve contract
            batch = await retriever.retrieve(
                user_id=user.id,
                profile=None,
                context=make_context(user.id),
                limit=10,
            )
            assert [h.work_id for h in batch.hits] == [w_single.id, w_multi.id]
            assert batch.hits[1].matched_authors == (a_multi_role.id,)
            assert batch.hits[1].retrieval_sources == ("explicit_author",)

    async def test_duplicate_work_across_channels_retains_complete_provenance_union(self):
        """A work overlapping genre, author, and inferred subject keeps all sources/IDs when selected."""
        async with get_test_session() as session:
            retriever = MetadataRetriever(session=session, settings=make_settings())

            user = User(email=f"union_{uuid.uuid4().hex[:8]}@example.com", display_name="Union User")
            session.add(user)
            await session.flush()

            tag = uuid.uuid4().hex[:6]
            g = Genre(name=f"UG-{tag}", slug=f"ug-{tag}")
            a = Author(name="Union Author", normalized_name=f"union author {tag}")
            s = Subject(name=f"US-{tag}", slug=f"us-{tag}")
            w_overlap = BookWork(canonical_title="Overlap Work")
            w_author_only = BookWork(canonical_title="Author Only Work")
            session.add_all([g, a, s, w_overlap, w_author_only])
            await session.flush()

            session.add_all([
                BookWorkGenre(work_id=w_overlap.id, genre_id=g.id),
                BookWorkAuthor(work_id=w_overlap.id, author_id=a.id),
                BookWorkSubject(work_id=w_overlap.id, subject_id=s.id),
                BookWorkAuthor(work_id=w_author_only.id, author_id=a.id),
            ])
            session.add_all([
                UserPreference(user_id=user.id, preference_type="genre", preference_value=str(g.id)),
                UserPreference(user_id=user.id, preference_type="author", preference_value=str(a.id)),
            ])
            await session.flush()

            profile = UserInterestProfile(
                user_id=user.id,
                model_name="m",
                model_revision="r",
                inferred_topics={"genres": {}, "subjects": {str(s.id): {"affinity": 0.8}}},
            )

            batch = await retriever.retrieve(
                user_id=user.id,
                profile=profile,
                context=make_context(user.id),
                limit=5,
            )
            hit_map = {h.work_id: h for h in batch.hits}
            assert set(hit_map.keys()) == {w_overlap.id, w_author_only.id}
            assert hit_map[w_overlap.id].retrieval_sources == (
                "explicit_author",
                "explicit_genre",
                "inferred_subject",
            )
            assert hit_map[w_overlap.id].matched_genres == (g.id,)
            assert hit_map[w_overlap.id].matched_authors == (a.id,)
            assert hit_map[w_overlap.id].matched_subjects == (s.id,)
