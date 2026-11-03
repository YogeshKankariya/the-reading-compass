import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.db.base import Base
from backend.db.models import (
    BookEdition,
    BookRating,
    BookWork,
    ReadingList,
    ReadingListBook,
    ReadingProgress,
    User,
    UserBook,
    UserInteraction,
)


@pytest.fixture
def db_session():
    """Create an in-memory SQLite database and session for library model testing."""
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
    user = User(email="reader@example.com", display_name="Test Reader")
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def sample_work(db_session: Session) -> BookWork:
    work = BookWork(canonical_title="Dune", first_published_year=1965)
    db_session.add(work)
    db_session.flush()
    return work


@pytest.fixture
def sample_edition(db_session: Session, sample_work: BookWork) -> BookEdition:
    edition = BookEdition(
        work_id=sample_work.id,
        isbn_13="9780441172719",
        page_count=688,
    )
    db_session.add(edition)
    db_session.flush()
    return edition


class TestLibraryMetadataAndDefinitions:
    """Verify metadata registration, column definitions, and constraints for library models."""

    def test_library_models_registered_in_metadata(self):
        expected_tables = {
            "user_books",
            "reading_progress",
            "book_ratings",
            "reading_lists",
            "reading_list_books",
            "user_interactions",
        }
        assert expected_tables.issubset(set(Base.metadata.tables.keys()))

    def test_user_books_columns_and_constraints(self):
        table = Base.metadata.tables["user_books"]
        col_names = {c.name for c in table.columns}
        expected = {
            "id",
            "user_id",
            "work_id",
            "edition_id",
            "status",
            "notes",
            "created_at",
            "updated_at",
        }
        assert col_names == expected
        assert table.columns["id"].primary_key is True
        assert table.columns["user_id"].nullable is False
        assert table.columns["work_id"].nullable is False
        assert table.columns["edition_id"].nullable is True
        assert table.columns["status"].nullable is False
        assert table.columns["notes"].nullable is True
        assert table.columns["created_at"].nullable is False
        assert table.columns["updated_at"].nullable is False

        # Foreign keys
        fks = {fk.target_fullname: fk for fk in table.foreign_keys}
        assert fks["users.id"].ondelete == "CASCADE"
        assert fks["book_works.id"].ondelete == "CASCADE"
        assert fks["book_editions.id"].ondelete == "SET NULL"

        # Unique constraint on (user_id, work_id)
        unique_constraints = [
            tuple(col.name for col in uc.columns)
            for uc in table.constraints
            if hasattr(uc, "columns") and uc != table.primary_key
        ]
        assert ("user_id", "work_id") in unique_constraints

        # Check constraint on status
        ck_names = {ck.name for ck in table.constraints if hasattr(ck, "sqltext")}
        assert "ck_user_books_status" in ck_names

    def test_reading_progress_columns_and_constraints(self):
        table = Base.metadata.tables["reading_progress"]
        col_names = {c.name for c in table.columns}
        expected = {
            "id",
            "user_book_id",
            "current_page",
            "total_pages",
            "progress_percent",
            "started_at",
            "completed_at",
            "last_read_at",
            "created_at",
            "updated_at",
        }
        assert col_names == expected
        assert table.columns["id"].primary_key is True
        assert table.columns["user_book_id"].nullable is False
        assert table.columns["current_page"].nullable is True
        assert table.columns["total_pages"].nullable is True
        assert table.columns["progress_percent"].nullable is False
        assert table.columns["started_at"].nullable is True
        assert table.columns["completed_at"].nullable is True
        assert table.columns["last_read_at"].nullable is True

        # Foreign key with CASCADE
        fks = list(table.columns["user_book_id"].foreign_keys)
        assert len(fks) == 1
        assert fks[0].target_fullname == "user_books.id"
        assert fks[0].ondelete == "CASCADE"

        # Unique constraint on user_book_id for 1:1 relationship
        unique_constraints = [
            tuple(col.name for col in uc.columns)
            for uc in table.constraints
            if hasattr(uc, "columns") and uc != table.primary_key
        ]
        assert ("user_book_id",) in unique_constraints

        # Check constraints
        ck_names = {ck.name for ck in table.constraints if hasattr(ck, "sqltext")}
        assert "ck_reading_progress_total_pages" in ck_names
        assert "ck_reading_progress_current_page" in ck_names
        assert "ck_reading_progress_percent" in ck_names
        assert "ck_reading_progress_page_bounds" in ck_names

    def test_book_ratings_columns_and_constraints(self):
        table = Base.metadata.tables["book_ratings"]
        col_names = {c.name for c in table.columns}
        expected = {
            "id",
            "user_id",
            "work_id",
            "rating",
            "review_text",
            "is_spoiler",
            "created_at",
            "updated_at",
        }
        assert col_names == expected
        assert table.columns["id"].primary_key is True
        assert table.columns["user_id"].nullable is False
        assert table.columns["work_id"].nullable is False
        assert table.columns["rating"].nullable is False
        assert table.columns["review_text"].nullable is True
        assert table.columns["is_spoiler"].nullable is False

        # Foreign keys with CASCADE
        fks = {fk.target_fullname: fk for fk in table.foreign_keys}
        assert fks["users.id"].ondelete == "CASCADE"
        assert fks["book_works.id"].ondelete == "CASCADE"

        # Unique constraint on (user_id, work_id)
        unique_constraints = [
            tuple(col.name for col in uc.columns)
            for uc in table.constraints
            if hasattr(uc, "columns") and uc != table.primary_key
        ]
        assert ("user_id", "work_id") in unique_constraints

        # Check constraint on rating
        ck_names = {ck.name for ck in table.constraints if hasattr(ck, "sqltext")}
        assert "ck_book_ratings_rating" in ck_names

    def test_reading_lists_columns_and_constraints(self):
        table = Base.metadata.tables["reading_lists"]
        col_names = {c.name for c in table.columns}
        expected = {
            "id",
            "user_id",
            "name",
            "description",
            "is_public",
            "created_at",
            "updated_at",
        }
        assert col_names == expected
        assert table.columns["id"].primary_key is True
        assert table.columns["user_id"].nullable is False
        assert table.columns["name"].nullable is False
        assert table.columns["description"].nullable is True
        assert table.columns["is_public"].nullable is False

        # Foreign key to users.id with CASCADE
        fks = list(table.columns["user_id"].foreign_keys)
        assert len(fks) == 1
        assert fks[0].target_fullname == "users.id"
        assert fks[0].ondelete == "CASCADE"

        # Case-insensitive unique index on (user_id, lower(name))
        idx_names = {idx.name for idx in table.indexes}
        assert "uq_reading_lists_user_lower_name" in idx_names

    def test_reading_list_books_columns_and_constraints(self):
        table = Base.metadata.tables["reading_list_books"]
        col_names = {c.name for c in table.columns}
        expected = {"list_id", "work_id", "position", "notes", "added_at"}
        assert col_names == expected

        # Composite primary key on (list_id, work_id)
        pk_cols = {c.name for c in table.primary_key.columns}
        assert pk_cols == {"list_id", "work_id"}

        assert table.columns["position"].nullable is False
        assert table.columns["notes"].nullable is True
        assert table.columns["added_at"].nullable is False

        # Foreign keys with CASCADE
        fks = {fk.target_fullname: fk for fk in table.foreign_keys}
        assert fks["reading_lists.id"].ondelete == "CASCADE"
        assert fks["book_works.id"].ondelete == "CASCADE"

        # Unique constraint on (list_id, position)
        unique_constraints = [
            tuple(col.name for col in uc.columns)
            for uc in table.constraints
            if hasattr(uc, "columns") and uc != table.primary_key
        ]
        assert ("list_id", "position") in unique_constraints

        # Check constraint on position >= 1
        ck_names = {ck.name for ck in table.constraints if hasattr(ck, "sqltext")}
        assert "ck_reading_list_books_position" in ck_names

    def test_user_interactions_columns_and_constraints(self):
        table = Base.metadata.tables["user_interactions"]
        col_names = {c.name for c in table.columns}
        expected = {
            "id",
            "user_id",
            "work_id",
            "interaction_type",
            "rating_value",
            "context",
            "created_at",
        }
        assert col_names == expected
        assert table.columns["id"].primary_key is True
        assert table.columns["user_id"].nullable is False
        assert table.columns["work_id"].nullable is True
        assert table.columns["interaction_type"].nullable is False
        assert table.columns["rating_value"].nullable is True
        assert table.columns["context"].nullable is True
        assert table.columns["created_at"].nullable is False

        # Foreign keys
        fks = {fk.target_fullname: fk for fk in table.foreign_keys}
        assert fks["users.id"].ondelete == "CASCADE"
        assert fks["book_works.id"].ondelete == "CASCADE"

        # Check constraints
        ck_names = {ck.name for ck in table.constraints if hasattr(ck, "sqltext")}
        assert "ck_user_interactions_type" in ck_names
        assert "ck_user_interactions_rating_value" in ck_names


class TestLibraryORMAndOperations:
    """Verify ORM persistence, relationships, cascades, and constraints."""

    def test_user_book_and_reading_progress_one_to_one(
        self, db_session: Session, sample_user: User, sample_work: BookWork, sample_edition: BookEdition
    ):
        user_book = UserBook(
            user_id=sample_user.id,
            work_id=sample_work.id,
            edition_id=sample_edition.id,
            status="currently_reading",
            notes="Loving the world-building.",
        )
        progress = ReadingProgress(
            user_book=user_book,
            current_page=120,
            total_pages=688,
            progress_percent=Decimal("17.44"),
            started_at=date(2026, 3, 1),
        )
        db_session.add(user_book)
        db_session.commit()

        assert isinstance(user_book.id, uuid.UUID)
        assert isinstance(progress.id, uuid.UUID)
        assert progress.user_book_id == user_book.id
        assert user_book.progress is progress
        assert progress.user_book is user_book

        # Cascade delete from UserBook to ReadingProgress
        progress_id = progress.id
        db_session.delete(user_book)
        db_session.commit()

        assert db_session.get(ReadingProgress, progress_id) is None

    def test_percentage_only_reading_progress(
        self, db_session: Session, sample_user: User, sample_work: BookWork
    ):
        """Audiobook or page-unknown scenario where only progress_percent is tracked."""
        user_book = UserBook(
            user_id=sample_user.id,
            work_id=sample_work.id,
            status="currently_reading",
        )
        progress = ReadingProgress(
            user_book=user_book,
            current_page=None,
            total_pages=None,
            progress_percent=Decimal("45.50"),
        )
        db_session.add(user_book)
        db_session.commit()

        reloaded = db_session.get(ReadingProgress, progress.id)
        assert reloaded.current_page is None
        assert reloaded.total_pages is None
        assert reloaded.progress_percent == Decimal("45.50")

    def test_duplicate_user_book_rejected(
        self, db_session: Session, sample_user: User, sample_work: BookWork
    ):
        ub1 = UserBook(user_id=sample_user.id, work_id=sample_work.id, status="want_to_read")
        db_session.add(ub1)
        db_session.commit()

        engine = db_session.bind
        with Session(engine) as session2:
            ub2 = UserBook(user_id=sample_user.id, work_id=sample_work.id, status="completed")
            session2.add(ub2)
            with pytest.raises(IntegrityError):
                session2.commit()

    def test_book_rating_persistence_and_uniqueness(
        self, db_session: Session, sample_user: User, sample_work: BookWork
    ):
        rating = BookRating(
            user_id=sample_user.id,
            work_id=sample_work.id,
            rating=5,
            review_text="One of the greatest sci-fi novels of all time.",
            is_spoiler=False,
        )
        db_session.add(rating)
        db_session.commit()

        assert isinstance(rating.id, uuid.UUID)
        assert rating.rating == 5

        # Duplicate rating by same user for same work is rejected
        engine = db_session.bind
        with Session(engine) as session2:
            rating2 = BookRating(
                user_id=sample_user.id,
                work_id=sample_work.id,
                rating=4,
            )
            session2.add(rating2)
            with pytest.raises(IntegrityError):
                session2.commit()

    def test_user_book_deletion_does_not_delete_book_rating(
        self, db_session: Session, sample_user: User, sample_work: BookWork
    ):
        """Ratings exist independently of whether the book is in the user's active library."""
        user_book = UserBook(
            user_id=sample_user.id,
            work_id=sample_work.id,
            status="completed",
        )
        rating = BookRating(
            user_id=sample_user.id,
            work_id=sample_work.id,
            rating=5,
            review_text="Superb",
        )
        db_session.add_all([user_book, rating])
        db_session.commit()

        # Delete user_book
        rating_id = rating.id
        db_session.delete(user_book)
        db_session.commit()

        # Rating must still exist!
        assert db_session.get(BookRating, rating_id) is not None

    def test_reading_list_and_books_ordering(
        self, db_session: Session, sample_user: User, sample_work: BookWork
    ):
        work2 = BookWork(canonical_title="Neuromancer", first_published_year=1984)
        db_session.add(work2)
        db_session.flush()

        rlist = ReadingList(
            user_id=sample_user.id,
            name="Sci-Fi Classics",
            description="Essential science fiction books.",
            is_public=True,
        )
        db_session.add(rlist)
        db_session.flush()

        item1 = ReadingListBook(list_id=rlist.id, work_id=sample_work.id, position=1)
        item2 = ReadingListBook(list_id=rlist.id, work_id=work2.id, position=2)
        db_session.add_all([item1, item2])
        db_session.commit()

        reloaded_list = db_session.get(ReadingList, rlist.id)
        assert len(reloaded_list.items) == 2
        assert [item.position for item in reloaded_list.items] == [1, 2]

        # ReadingListBook references BookWork directly
        assert reloaded_list.items[0].work.canonical_title in {"Dune", "Neuromancer"}

    def test_user_book_deletion_does_not_delete_reading_list_book(
        self, db_session: Session, sample_user: User, sample_work: BookWork
    ):
        """Reading lists reference BookWork directly, so deleting a UserBook has no effect on list items."""
        user_book = UserBook(
            user_id=sample_user.id,
            work_id=sample_work.id,
            status="want_to_read",
        )
        rlist = ReadingList(user_id=sample_user.id, name="Favorites")
        db_session.add_all([user_book, rlist])
        db_session.flush()

        item = ReadingListBook(list_id=rlist.id, work_id=sample_work.id, position=1)
        db_session.add(item)
        db_session.commit()

        db_session.delete(user_book)
        db_session.commit()

        # Item still in reading list
        assert db_session.get(ReadingListBook, (rlist.id, sample_work.id)) is not None

    def test_duplicate_position_rejected_in_reading_list(
        self, db_session: Session, sample_user: User, sample_work: BookWork
    ):
        work2 = BookWork(canonical_title="Foundation", first_published_year=1951)
        rlist = ReadingList(user_id=sample_user.id, name="Hugo Winners")
        db_session.add_all([work2, rlist])
        db_session.flush()

        item1 = ReadingListBook(list_id=rlist.id, work_id=sample_work.id, position=1)
        db_session.add(item1)
        db_session.commit()

        engine = db_session.bind
        with Session(engine) as session2:
            item2 = ReadingListBook(list_id=rlist.id, work_id=work2.id, position=1)
            session2.add(item2)
            with pytest.raises(IntegrityError):
                session2.commit()

    def test_reading_list_reordering_with_temporary_position(
        self, db_session: Session, sample_user: User, sample_work: BookWork
    ):
        """Test transactional reordering algorithm using temporary high position."""
        work2 = BookWork(canonical_title="Solaris", first_published_year=1961)
        rlist = ReadingList(user_id=sample_user.id, name="Space Explorations")
        db_session.add_all([work2, rlist])
        db_session.flush()

        item1 = ReadingListBook(list_id=rlist.id, work_id=sample_work.id, position=1)
        item2 = ReadingListBook(list_id=rlist.id, work_id=work2.id, position=2)
        db_session.add_all([item1, item2])
        db_session.commit()

        # Swap positions: item1 becomes 2, item2 becomes 1
        # Step 1: move item1 to temp position above max
        item1.position = 1000
        db_session.flush()

        # Step 2: move item2 to position 1
        item2.position = 1
        db_session.flush()

        # Step 3: move item1 to position 2
        item1.position = 2
        db_session.commit()

        assert db_session.get(ReadingListBook, (rlist.id, sample_work.id)).position == 2
        assert db_session.get(ReadingListBook, (rlist.id, work2.id)).position == 1

    def test_user_interaction_logging(
        self, db_session: Session, sample_user: User, sample_work: BookWork
    ):
        interaction = UserInteraction(
            user_id=sample_user.id,
            work_id=sample_work.id,
            interaction_type="recommendation_click",
            rating_value=None,
            context={"surface": "home_shelf", "rank": 2, "model": "rec_v1"},
        )
        db_session.add(interaction)
        db_session.commit()

        assert isinstance(interaction.id, uuid.UUID)
        reloaded = db_session.get(UserInteraction, interaction.id)
        assert reloaded.interaction_type == "recommendation_click"
        assert reloaded.context == {"surface": "home_shelf", "rank": 2, "model": "rec_v1"}

    def test_edition_deletion_sets_null_on_user_book(
        self, db_session: Session, sample_user: User, sample_work: BookWork, sample_edition: BookEdition
    ):
        user_book = UserBook(
            user_id=sample_user.id,
            work_id=sample_work.id,
            edition_id=sample_edition.id,
            status="want_to_read",
        )
        db_session.add(user_book)
        db_session.commit()

        # Delete edition
        db_session.delete(sample_edition)
        db_session.commit()

        reloaded_ub = db_session.get(UserBook, user_book.id)
        assert reloaded_ub is not None
        assert reloaded_ub.edition_id is None


class TestLibraryCheckConstraints:
    """Verify check constraints prevent invalid data."""

    def test_reading_progress_total_pages_must_be_positive(
        self, db_session: Session, sample_user: User, sample_work: BookWork
    ):
        user_book = UserBook(user_id=sample_user.id, work_id=sample_work.id)
        progress = ReadingProgress(
            user_book=user_book,
            current_page=0,
            total_pages=0,  # Invalid: must be > 0
            progress_percent=Decimal("0.00"),
        )
        db_session.add_all([user_book, progress])
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_reading_progress_current_page_cannot_be_negative(
        self, db_session: Session, sample_user: User, sample_work: BookWork
    ):
        user_book = UserBook(user_id=sample_user.id, work_id=sample_work.id)
        progress = ReadingProgress(
            user_book=user_book,
            current_page=-1,  # Invalid: must be >= 0
            total_pages=100,
            progress_percent=Decimal("0.00"),
        )
        db_session.add_all([user_book, progress])
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_reading_progress_current_page_cannot_exceed_total(
        self, db_session: Session, sample_user: User, sample_work: BookWork
    ):
        user_book = UserBook(user_id=sample_user.id, work_id=sample_work.id)
        progress = ReadingProgress(
            user_book=user_book,
            current_page=350,
            total_pages=300,  # Invalid: current > total
            progress_percent=Decimal("100.00"),
        )
        db_session.add_all([user_book, progress])
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_reading_progress_percent_bounds(
        self, db_session: Session, sample_user: User, sample_work: BookWork
    ):
        user_book = UserBook(user_id=sample_user.id, work_id=sample_work.id)
        progress = ReadingProgress(
            user_book=user_book,
            progress_percent=Decimal("101.50"),  # Invalid: > 100.00
        )
        db_session.add_all([user_book, progress])
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_book_rating_must_be_between_1_and_5(
        self, db_session: Session, sample_user: User, sample_work: BookWork
    ):
        rating_zero = BookRating(
            user_id=sample_user.id,
            work_id=sample_work.id,
            rating=0,  # Invalid: must be 1-5
        )
        db_session.add(rating_zero)
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_reading_list_book_position_must_be_at_least_1(
        self, db_session: Session, sample_user: User, sample_work: BookWork
    ):
        rlist = ReadingList(user_id=sample_user.id, name="Test List")
        db_session.add(rlist)
        db_session.flush()

        item = ReadingListBook(list_id=rlist.id, work_id=sample_work.id, position=0)
        db_session.add(item)
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_user_interaction_invalid_type_rejected(
        self, db_session: Session, sample_user: User, sample_work: BookWork
    ):
        interaction = UserInteraction(
            user_id=sample_user.id,
            work_id=sample_work.id,
            interaction_type="unauthorized_event_type",
        )
        db_session.add(interaction)
        with pytest.raises(IntegrityError):
            db_session.commit()


class TestLibrarySecurityAndPrivacyReprs:
    """Verify __repr__ implementations do not leak private user notes, reviews, context, or list titles."""

    def test_user_book_repr(self):
        ub = UserBook(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            work_id=uuid.uuid4(),
            status="currently_reading",
            notes="Very private diary notes about my feelings on this book.",
        )
        ub_repr = repr(ub)
        assert "currently_reading" in ub_repr
        assert "diary notes" not in ub_repr
        assert "feelings" not in ub_repr

    def test_reading_progress_repr(self):
        rp = ReadingProgress(
            id=uuid.uuid4(),
            user_book_id=uuid.uuid4(),
            current_page=150,
            total_pages=300,
            progress_percent=Decimal("50.00"),
        )
        rp_repr = repr(rp)
        assert "50.00" in rp_repr

    def test_book_rating_repr(self):
        rating = BookRating(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            work_id=uuid.uuid4(),
            rating=4,
            review_text="This is an extensive private review with personal thoughts.",
            is_spoiler=True,
        )
        r_repr = repr(rating)
        assert "rating=4" in r_repr
        assert "personal thoughts" not in r_repr

    def test_reading_list_repr(self):
        rl = ReadingList(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            name="Confidential Secret Book List",
            description="Private description about the user",
            is_public=False,
        )
        rl_repr = repr(rl)
        assert "is_public=False" in rl_repr
        assert "Confidential Secret Book List" not in rl_repr
        assert "Private description" not in rl_repr

    def test_reading_list_book_repr(self):
        rlb = ReadingListBook(
            list_id=uuid.uuid4(),
            work_id=uuid.uuid4(),
            position=3,
        )
        rlb_repr = repr(rlb)
        assert "position=3" in rlb_repr

    def test_user_interaction_repr(self):
        ui = UserInteraction(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            work_id=uuid.uuid4(),
            interaction_type="recommendation_click",
            context={"secret_search_term": "how to survive", "ip": "1.2.3.4"},
        )
        ui_repr = repr(ui)
        assert "interaction_type='recommendation_click'" in ui_repr
        assert "how to survive" not in ui_repr
        assert "1.2.3.4" not in ui_repr
