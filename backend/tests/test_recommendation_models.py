import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.db.base import Base
from backend.db.models import (
    BookEmbedding,
    BookWork,
    ReadingGoal,
    RecommendationItem,
    RecommendationRequest,
    User,
    UserInteraction,
    UserInterestProfile,
)


@pytest.fixture
def db_session():
    """Create an in-memory SQLite database and session for recommendation model testing."""
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def sample_user(db_session: Session) -> User:
    user = User(email="learner@example.com", display_name="Curious Reader")
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def sample_work(db_session: Session) -> BookWork:
    work = BookWork(canonical_title="Thinking, Fast and Slow", first_published_year=2011)
    db_session.add(work)
    db_session.flush()
    return work


class TestRecommendationMetadataAndDefinitions:
    """Verify metadata registration, column definitions, and constraints for Milestone 4A."""

    def test_milestone_4a_models_registered_in_metadata(self):
        expected_tables = {
            "reading_goals",
            "book_embeddings",
            "user_interest_profiles",
            "recommendation_requests",
            "recommendation_items",
        }
        assert expected_tables.issubset(set(Base.metadata.tables.keys()))

    def test_reading_goals_columns_and_constraints(self):
        table = Base.metadata.tables["reading_goals"]
        col_names = {c.name for c in table.columns}
        expected = {
            "id",
            "user_id",
            "title",
            "description",
            "goal_type",
            "target_date",
            "status",
            "created_at",
            "updated_at",
        }
        assert col_names == expected
        assert table.columns["id"].primary_key is True
        assert table.columns["user_id"].nullable is False
        assert table.columns["title"].nullable is False
        assert table.columns["description"].nullable is True
        assert table.columns["goal_type"].nullable is False
        assert table.columns["target_date"].nullable is True
        assert table.columns["status"].nullable is False

        # Foreign key with CASCADE
        fks = list(table.columns["user_id"].foreign_keys)
        assert len(fks) == 1
        assert fks[0].target_fullname == "users.id"
        assert fks[0].ondelete == "CASCADE"

        # Unique constraint on (id, user_id) for composite FK journey isolation
        unique_constraints = [
            tuple(col.name for col in uc.columns)
            for uc in table.constraints
            if hasattr(uc, "columns") and uc != table.primary_key
        ]
        assert ("id", "user_id") in unique_constraints

        # Check constraints
        ck_names = {ck.name for ck in table.constraints if hasattr(ck, "sqltext")}
        assert "ck_reading_goals_goal_type" in ck_names
        assert "ck_reading_goals_status" in ck_names

    def test_book_embeddings_columns_and_constraints(self):
        table = Base.metadata.tables["book_embeddings"]
        col_names = {c.name for c in table.columns}
        expected = {
            "id",
            "work_id",
            "model_name",
            "model_revision",
            "embedding",
            "source_text_hash",
            "is_active",
            "created_at",
        }
        assert col_names == expected
        assert table.columns["id"].primary_key is True
        assert table.columns["work_id"].nullable is False
        assert table.columns["model_name"].nullable is False
        assert table.columns["model_revision"].nullable is False
        assert table.columns["embedding"].nullable is False
        assert table.columns["source_text_hash"].nullable is False
        assert table.columns["is_active"].nullable is False

        # Foreign key with CASCADE
        fks = list(table.columns["work_id"].foreign_keys)
        assert len(fks) == 1
        assert fks[0].target_fullname == "book_works.id"
        assert fks[0].ondelete == "CASCADE"

        # Unique constraint on (work_id, model_name, model_revision, source_text_hash)
        unique_constraints = [
            tuple(col.name for col in uc.columns)
            for uc in table.constraints
            if hasattr(uc, "columns") and uc != table.primary_key
        ]
        assert (
            "work_id",
            "model_name",
            "model_revision",
            "source_text_hash",
        ) in unique_constraints

        # Indexes
        idx_names = {idx.name for idx in table.indexes}
        assert "uq_book_embeddings_active_work" in idx_names
        assert "ix_book_embeddings_hnsw" in idx_names

    def test_user_interest_profiles_columns_and_constraints(self):
        table = Base.metadata.tables["user_interest_profiles"]
        col_names = {c.name for c in table.columns}
        expected = {
            "id",
            "user_id",
            "model_name",
            "model_revision",
            "interest_vector",
            "inferred_topics",
            "interaction_count",
            "last_calculated_at",
            "created_at",
            "updated_at",
        }
        assert col_names == expected
        assert table.columns["id"].primary_key is True
        assert table.columns["user_id"].nullable is False
        assert table.columns["model_name"].nullable is False
        assert table.columns["model_revision"].nullable is False
        assert table.columns["interest_vector"].nullable is True
        assert table.columns["inferred_topics"].nullable is True
        assert table.columns["interaction_count"].nullable is False

        # Foreign key with CASCADE
        fks = list(table.columns["user_id"].foreign_keys)
        assert len(fks) == 1
        assert fks[0].target_fullname == "users.id"
        assert fks[0].ondelete == "CASCADE"

        # Check constraint on interaction_count >= 0
        ck_names = {ck.name for ck in table.constraints if hasattr(ck, "sqltext")}
        assert "ck_user_interest_profiles_interaction_count" in ck_names

    def test_recommendation_requests_columns_and_constraints(self):
        table = Base.metadata.tables["recommendation_requests"]
        col_names = {c.name for c in table.columns}
        expected = {
            "id",
            "user_id",
            "request_type",
            "request_context",
            "algorithm_version",
            "created_at",
        }
        assert col_names == expected
        assert table.columns["id"].primary_key is True
        assert table.columns["user_id"].nullable is False
        assert table.columns["request_type"].nullable is False
        assert table.columns["request_context"].nullable is True
        assert table.columns["algorithm_version"].nullable is False

        # Foreign key
        fks = list(table.columns["user_id"].foreign_keys)
        assert len(fks) == 1
        assert fks[0].target_fullname == "users.id"
        assert fks[0].ondelete == "CASCADE"

        # Check constraint on request_type
        ck_names = {ck.name for ck in table.constraints if hasattr(ck, "sqltext")}
        assert "ck_recommendation_requests_type" in ck_names

    def test_recommendation_items_columns_and_constraints(self):
        table = Base.metadata.tables["recommendation_items"]
        col_names = {c.name for c in table.columns}
        expected = {
            "id",
            "request_id",
            "work_id",
            "rank_position",
            "score",
            "score_breakdown",
            "reason_codes",
            "feedback_type",
            "feedback_reason",
            "feedback_at",
            "created_at",
        }
        assert col_names == expected
        assert table.columns["id"].primary_key is True
        assert table.columns["request_id"].nullable is False
        assert table.columns["work_id"].nullable is False
        assert table.columns["rank_position"].nullable is False
        assert table.columns["score"].nullable is False
        assert table.columns["score_breakdown"].nullable is True
        assert table.columns["reason_codes"].nullable is True
        assert table.columns["feedback_type"].nullable is True
        assert table.columns["feedback_reason"].nullable is True
        assert table.columns["feedback_at"].nullable is True

        # Foreign keys with CASCADE
        fks = {fk.target_fullname: fk for fk in table.foreign_keys}
        assert fks["recommendation_requests.id"].ondelete == "CASCADE"
        assert fks["book_works.id"].ondelete == "CASCADE"

        # Unique constraints
        unique_constraints = [
            tuple(col.name for col in uc.columns)
            for uc in table.constraints
            if hasattr(uc, "columns") and uc != table.primary_key
        ]
        assert ("request_id", "work_id") in unique_constraints
        assert ("request_id", "rank_position") in unique_constraints

        # Check constraints
        ck_names = {ck.name for ck in table.constraints if hasattr(ck, "sqltext")}
        assert "ck_recommendation_items_rank_position" in ck_names
        assert "ck_recommendation_items_score_range" in ck_names
        assert "ck_recommendation_items_feedback_type" in ck_names


class TestRecommendationORMAndOperations:
    """Verify ORM operations, persistence, and cascades."""

    def test_reading_goal_crud_and_cascade(
        self, db_session: Session, sample_user: User
    ):
        goal = ReadingGoal(
            user_id=sample_user.id,
            title="Master Behavioral Economics",
            description="Read the foundational books in judgment and decision making.",
            goal_type="learn_subject",
            target_date=date(2026, 12, 31),
            status="active",
        )
        db_session.add(goal)
        db_session.commit()

        assert isinstance(goal.id, uuid.UUID)
        assert goal.user is sample_user
        assert goal in sample_user.goals

        # Cascade delete when user is deleted
        goal_id = goal.id
        db_session.delete(sample_user)
        db_session.commit()
        assert db_session.get(ReadingGoal, goal_id) is None

    def test_book_embedding_version_lifecycle(
        self, db_session: Session, sample_work: BookWork
    ):
        # Create initial active embedding (v1)
        emb1 = BookEmbedding(
            work_id=sample_work.id,
            model_name="all-MiniLM-L6-v2",
            model_revision="commit-hash-v1",
            embedding=[0.1] * 384,
            source_text_hash="hash-initial-text",
            is_active=True,
        )
        db_session.add(emb1)
        db_session.commit()

        assert emb1.is_active is True
        assert emb1 in sample_work.embeddings

        # Deactivate v1 and create v2
        emb1.is_active = False
        emb2 = BookEmbedding(
            work_id=sample_work.id,
            model_name="all-MiniLM-L6-v2",
            model_revision="commit-hash-v1",
            embedding=[0.2] * 384,
            source_text_hash="hash-enriched-text",
            is_active=True,
        )
        db_session.add(emb2)
        db_session.commit()

        reloaded_emb1 = db_session.get(BookEmbedding, emb1.id)
        reloaded_emb2 = db_session.get(BookEmbedding, emb2.id)
        assert reloaded_emb1.is_active is False
        assert reloaded_emb2.is_active is True

    def test_duplicate_active_embedding_rejected(
        self, db_session: Session, sample_work: BookWork
    ):
        emb1 = BookEmbedding(
            work_id=sample_work.id,
            model_name="all-MiniLM-L6-v2",
            model_revision="commit-hash-v1",
            embedding=[0.1] * 384,
            source_text_hash="hash-1",
            is_active=True,
        )
        db_session.add(emb1)
        db_session.commit()

        # Adding a second active embedding for same work_id and model revision should violate partial unique index
        engine = db_session.bind
        with Session(engine) as session2:
            emb2 = BookEmbedding(
                work_id=sample_work.id,
                model_name="all-MiniLM-L6-v2",
                model_revision="commit-hash-v1",
                embedding=[0.2] * 384,
                source_text_hash="hash-2",
                is_active=True,
            )
            session2.add(emb2)
            with pytest.raises(IntegrityError):
                session2.commit()

    def test_user_interest_profile_crud(
        self, db_session: Session, sample_user: User
    ):
        profile = UserInterestProfile(
            user_id=sample_user.id,
            model_name="all-MiniLM-L6-v2",
            model_revision="rev-1",
            interest_vector=[0.05] * 384,
            inferred_topics={"behavioral_economics": 0.92, "psychology": 0.75},
            interaction_count=12,
            last_calculated_at=datetime.now(timezone.utc),
        )
        db_session.add(profile)
        db_session.commit()

        assert sample_user.interest_profile is profile
        assert profile.interaction_count == 12

    def test_duplicate_user_interest_profile_rejected(
        self, db_session: Session, sample_user: User
    ):
        p1 = UserInterestProfile(
            user_id=sample_user.id,
            model_name="all-MiniLM-L6-v2",
            model_revision="rev-1",
            interest_vector=None,
            interaction_count=0,
        )
        db_session.add(p1)
        db_session.commit()

        engine = db_session.bind
        with Session(engine) as session2:
            p2 = UserInterestProfile(
                user_id=sample_user.id,
                model_name="all-MiniLM-L6-v2",
                model_revision="rev-1",
                interest_vector=None,
                interaction_count=0,
            )
            session2.add(p2)
            with pytest.raises(IntegrityError):
                session2.commit()

    def test_recommendation_request_and_items_lifecycle(
        self, db_session: Session, sample_user: User, sample_work: BookWork
    ):
        work2 = BookWork(canonical_title="Nudge", first_published_year=2008)
        db_session.add(work2)
        db_session.flush()

        req = RecommendationRequest(
            user_id=sample_user.id,
            request_type="next_read",
            request_context={"mood": "intellectual", "max_pages": 500},
            algorithm_version="hybrid-v1.0",
        )
        db_session.add(req)
        db_session.flush()

        item1 = RecommendationItem(
            request_id=req.id,
            work_id=sample_work.id,
            rank_position=1,
            score=Decimal("0.892500"),
            score_breakdown={"semantic": 0.95, "topic": 0.88, "difficulty": 0.85},
            reason_codes=["MATCHES_CURRENT_GOAL", "BEGINNER_FRIENDLY"],
        )
        item2 = RecommendationItem(
            request_id=req.id,
            work_id=work2.id,
            rank_position=2,
            score=Decimal("0.841000"),
            score_breakdown={"semantic": 0.89, "topic": 0.82, "difficulty": 0.81},
            reason_codes=["TOPIC_RELEVANCE"],
        )
        db_session.add_all([item1, item2])
        db_session.commit()

        reloaded_req = db_session.get(RecommendationRequest, req.id)
        assert len(reloaded_req.items) == 2
        assert [i.rank_position for i in reloaded_req.items] == [1, 2]

        # Submit feedback on item 1
        item1.feedback_type = "helpful"
        item1.feedback_reason = "Spot on recommendation!"
        item1.feedback_at = datetime.now(timezone.utc)
        db_session.commit()

        reloaded_item = db_session.get(RecommendationItem, item1.id)
        assert reloaded_item.feedback_type == "helpful"
        assert reloaded_item.feedback_reason == "Spot on recommendation!"

        # Deleting request cascades to items
        req_id = req.id
        item1_id = item1.id
        db_session.delete(reloaded_req)
        db_session.commit()
        assert db_session.get(RecommendationItem, item1_id) is None


class TestRecommendationCheckConstraints:
    """Verify check constraints prevent invalid data."""

    def test_all_approved_goal_types_accepted(
        self, db_session: Session, sample_user: User
    ):
        approved_types = [
            "learn_subject",
            "read_more",
            "explore_genre",
            "career_development",
            "skill_depth",
            "custom",
        ]
        for idx, gtype in enumerate(approved_types):
            goal = ReadingGoal(
                user_id=sample_user.id,
                title=f"Goal {idx}",
                goal_type=gtype,
            )
            db_session.add(goal)
        db_session.commit()

    def test_invalid_goal_type_rejected(
        self, db_session: Session, sample_user: User
    ):
        goal = ReadingGoal(
            user_id=sample_user.id,
            title="Invalid Goal",
            goal_type="unauthorized_goal_type",
        )
        db_session.add(goal)
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_invalid_goal_status_rejected(
        self, db_session: Session, sample_user: User
    ):
        goal = ReadingGoal(
            user_id=sample_user.id,
            title="Invalid Status",
            goal_type="learn_subject",
            status="unknown_status",
        )
        db_session.add(goal)
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_recommendation_item_score_bounds(
        self, db_session: Session, sample_user: User, sample_work: BookWork
    ):
        req = RecommendationRequest(
            user_id=sample_user.id,
            request_type="discover",
            algorithm_version="v1",
        )
        db_session.add(req)
        db_session.flush()

        # Score > 1.0 must be rejected
        item = RecommendationItem(
            request_id=req.id,
            work_id=sample_work.id,
            rank_position=1,
            score=Decimal("1.000001"),
        )
        db_session.add(item)
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_recommendation_item_rank_must_be_positive(
        self, db_session: Session, sample_user: User, sample_work: BookWork
    ):
        req = RecommendationRequest(
            user_id=sample_user.id,
            request_type="discover",
            algorithm_version="v1",
        )
        db_session.add(req)
        db_session.flush()

        # Rank 0 is invalid
        item = RecommendationItem(
            request_id=req.id,
            work_id=sample_work.id,
            rank_position=0,
            score=Decimal("0.500000"),
        )
        db_session.add(item)
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_recommendation_feedback_type_whitelist(
        self, db_session: Session, sample_user: User, sample_work: BookWork
    ):
        req = RecommendationRequest(
            user_id=sample_user.id,
            request_type="discover",
            algorithm_version="v1",
        )
        db_session.add(req)
        db_session.flush()

        item = RecommendationItem(
            request_id=req.id,
            work_id=sample_work.id,
            rank_position=1,
            score=Decimal("0.500000"),
            feedback_type="unauthorized_feedback",
        )
        db_session.add(item)
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_extended_user_interaction_whitelist(
        self, db_session: Session, sample_user: User, sample_work: BookWork
    ):
        # All 12 interaction types (10 original + 2 recommendation feedback) should be accepted
        all_types = [
            "view",
            "click",
            "save",
            "dismiss",
            "rate",
            "start",
            "progress",
            "complete",
            "abandon",
            "recommendation_click",
            "recommendation_helpful",
            "recommendation_not_helpful",
        ]
        interactions = [
            UserInteraction(
                user_id=sample_user.id,
                work_id=sample_work.id,
                interaction_type=itype,
            )
            for itype in all_types
        ]
        db_session.add_all(interactions)
        db_session.commit()
        for item in interactions:
            assert item.id is not None

        # Invalid type rejected
        invalid = UserInteraction(
            user_id=sample_user.id,
            work_id=sample_work.id,
            interaction_type="invalid_action",
        )
        db_session.add(invalid)
        with pytest.raises(IntegrityError):
            db_session.commit()


class TestRecommendationSecurityReprs:
    """Verify __repr__ implementations do not expose private user text, scores, or prompt contexts."""

    def test_reading_goal_repr(self):
        g = ReadingGoal(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            title="Secret Career Goal: Leave my job",
            description="Highly private career plans",
            goal_type="career_development",
            status="active",
        )
        r = repr(g)
        assert "career_development" in r
        assert "active" in r
        assert "Leave my job" not in r
        assert "private career plans" not in r

    def test_book_embedding_repr(self):
        emb = BookEmbedding(
            id=uuid.uuid4(),
            work_id=uuid.uuid4(),
            model_name="all-MiniLM-L6-v2",
            model_revision="rev-123",
            embedding=[0.1] * 384,
            source_text_hash="hash123",
            is_active=True,
        )
        r = repr(emb)
        assert "all-MiniLM-L6-v2" in r
        assert "is_active=True" in r
        assert "0.1" not in r  # Does not dump 384 floats into logs

    def test_user_interest_profile_repr(self):
        p = UserInterestProfile(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            model_name="all-MiniLM-L6-v2",
            model_revision="rev-1",
            inferred_topics={"private_interest": 0.99},
            interaction_count=42,
        )
        r = repr(p)
        assert "interaction_count=42" in r
        assert "all-MiniLM-L6-v2" in r
        assert "private_interest" not in r

    def test_recommendation_request_repr(self):
        req = RecommendationRequest(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            request_type="mood_based",
            request_context={"confidential_mood": "feeling depressed and lonely"},
            algorithm_version="v1.0",
        )
        r = repr(req)
        assert "mood_based" in r
        assert "v1.0" in r
        assert "depressed" not in r

    def test_recommendation_item_repr(self):
        item = RecommendationItem(
            id=uuid.uuid4(),
            request_id=uuid.uuid4(),
            work_id=uuid.uuid4(),
            rank_position=1,
            score=Decimal("0.910000"),
            score_breakdown={"secret_weight": 0.99},
            reason_codes=["TOPIC_RELEVANCE"],
            feedback_type="helpful",
            feedback_reason="Private reason why I liked it",
        )
        r = repr(item)
        assert "rank=1" in r
        assert "score=0.910000" in r
        assert "secret_weight" not in r
        assert "Private reason" not in r
