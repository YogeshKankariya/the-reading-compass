import uuid
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.db.base import Base
from backend.db.models import (
    Author,
    BookEdition,
    BookWork,
    BookWorkAuthor,
    BookWorkGenre,
    BookWorkSubject,
    Genre,
    Subject,
)


@pytest.fixture
def db_session():
    """Create an in-memory SQLite database and session for model testing."""
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


class TestMetadataAndDefinitions:
    """Verify metadata registration, table names, and column definitions."""

    def test_all_models_registered_in_metadata(self):
        expected_tables = {
            "book_works",
            "book_editions",
            "authors",
            "book_work_authors",
            "genres",
            "book_work_genres",
            "subjects",
            "book_work_subjects",
        }
        assert expected_tables.issubset(set(Base.metadata.tables.keys()))

    def test_book_work_columns(self):
        table = Base.metadata.tables["book_works"]
        col_names = {c.name for c in table.columns}
        expected = {
            "id",
            "canonical_title",
            "subtitle",
            "description",
            "original_language",
            "first_published_year",
            "difficulty_level",
            "estimated_reading_minutes",
            "cover_image_url",
            "metadata_status",
            "created_at",
            "updated_at",
        }
        assert col_names == expected
        assert table.columns["canonical_title"].nullable is False
        assert table.columns["metadata_status"].nullable is False
        assert table.columns["created_at"].nullable is False
        assert table.columns["updated_at"].nullable is False
        assert table.columns["subtitle"].nullable is True
        assert table.columns["description"].nullable is True
        assert table.columns["id"].primary_key is True

    def test_book_edition_columns_and_constraints(self):
        table = Base.metadata.tables["book_editions"]
        col_names = {c.name for c in table.columns}
        expected = {
            "id",
            "work_id",
            "isbn_10",
            "isbn_13",
            "publisher",
            "publication_date",
            "page_count",
            "format",
            "language",
            "cover_image_url",
            "created_at",
        }
        assert col_names == expected
        assert table.columns["id"].primary_key is True
        assert table.columns["work_id"].nullable is False
        assert table.columns["created_at"].nullable is False

        # Check FK to book_works.id with ondelete CASCADE
        fks = list(table.columns["work_id"].foreign_keys)
        assert len(fks) == 1
        assert fks[0].target_fullname == "book_works.id"
        assert fks[0].ondelete == "CASCADE"

        # Check check constraints
        check_names = {ck.name for ck in table.constraints if hasattr(ck, "sqltext")}
        assert "ck_book_editions_page_count_positive" in check_names

        # Check partial indexes on isbn_10 and isbn_13
        index_names = {idx.name for idx in table.indexes}
        assert "ix_book_editions_isbn_10" in index_names
        assert "ix_book_editions_isbn_13" in index_names

    def test_author_columns(self):
        table = Base.metadata.tables["authors"]
        col_names = {c.name for c in table.columns}
        expected = {"id", "name", "normalized_name", "bio", "created_at"}
        assert col_names == expected
        assert table.columns["id"].primary_key is True
        assert table.columns["name"].nullable is False
        assert table.columns["normalized_name"].nullable is False
        assert table.columns["created_at"].nullable is False

    def test_composite_primary_keys(self):
        # BookWorkAuthor: (work_id, author_id, author_role)
        bwa_pk = [c.name for c in Base.metadata.tables["book_work_authors"].primary_key]
        assert bwa_pk == ["work_id", "author_id", "author_role"]

        # BookWorkGenre: (work_id, genre_id)
        bwg_pk = [c.name for c in Base.metadata.tables["book_work_genres"].primary_key]
        assert bwg_pk == ["work_id", "genre_id"]

        # BookWorkSubject: (work_id, subject_id)
        bws_pk = [c.name for c in Base.metadata.tables["book_work_subjects"].primary_key]
        assert bws_pk == ["work_id", "subject_id"]

    def test_subject_columns_and_self_reference(self):
        table = Base.metadata.tables["subjects"]
        col_names = {c.name for c in table.columns}
        expected = {"id", "name", "slug", "description", "parent_subject_id"}
        assert col_names == expected
        assert table.columns["parent_subject_id"].nullable is True

        fks = list(table.columns["parent_subject_id"].foreign_keys)
        assert len(fks) == 1
        assert fks[0].target_fullname == "subjects.id"
        assert fks[0].ondelete == "SET NULL"

    def test_uniqueness_constraints(self):
        genres_table = Base.metadata.tables["genres"]
        genre_unique_cols = {
            tuple(col.name for col in uc.columns)
            for uc in genres_table.constraints
            if hasattr(uc, "columns") and uc != genres_table.primary_key
        }
        assert ("name",) in genre_unique_cols
        assert ("slug",) in genre_unique_cols

        subjects_table = Base.metadata.tables["subjects"]
        subject_unique_cols = {
            tuple(col.name for col in uc.columns)
            for uc in subjects_table.constraints
            if hasattr(uc, "columns") and uc != subjects_table.primary_key
        }
        assert ("name",) in subject_unique_cols
        assert ("slug",) in subject_unique_cols


class TestModelInstancesAndRelationships:
    """Verify ORM instantiation, defaults, and relationships."""

    def test_uuid_primary_key_generation(self, db_session: Session):
        work = BookWork(canonical_title="Clean Code")
        author = Author(name="Robert C. Martin", normalized_name="robert c martin")
        genre = Genre(name="Software Engineering", slug="software-engineering")
        subject = Subject(name="Clean Architecture", slug="clean-architecture")

        db_session.add_all([work, author, genre, subject])
        db_session.flush()

        assert isinstance(work.id, uuid.UUID)
        assert isinstance(author.id, uuid.UUID)
        assert isinstance(genre.id, uuid.UUID)
        assert isinstance(subject.id, uuid.UUID)

        db_session.commit()

        assert work.id is not None
        assert author.id is not None
        assert genre.id is not None
        assert subject.id is not None

    def test_work_to_edition_relationship(self, db_session: Session):
        work = BookWork(canonical_title="The Hobbit")
        edition1 = BookEdition(
            work=work,
            isbn_10="0345339681",
            isbn_13="9780345339683",
            format="paperback",
            page_count=304,
        )
        edition2 = BookEdition(
            work=work,
            isbn_13="9780547928227",
            format="hardcover",
            page_count=320,
        )
        db_session.add(work)
        db_session.commit()

        reloaded_work = db_session.get(BookWork, work.id)
        assert len(reloaded_work.editions) == 2
        assert {ed.format for ed in reloaded_work.editions} == {"paperback", "hardcover"}
        assert edition1.work.id == work.id

        # Test cascade delete of editions when work is deleted
        edition1_id = edition1.id
        db_session.delete(reloaded_work)
        db_session.commit()

        assert db_session.get(BookEdition, edition1_id) is None

    def test_work_to_author_relationship(self, db_session: Session):
        work = BookWork(canonical_title="Good Omens")
        author1 = Author(name="Neil Gaiman", normalized_name="neil gaiman")
        author2 = Author(name="Terry Pratchett", normalized_name="terry pratchett")

        link1 = BookWorkAuthor(work=work, author=author1, author_role="author", display_order=1)
        link2 = BookWorkAuthor(work=work, author=author2, author_role="author", display_order=2)

        db_session.add_all([work, author1, author2, link1, link2])
        db_session.commit()

        reloaded_work = db_session.get(BookWork, work.id)
        assert len(reloaded_work.work_authors) == 2
        assert len(reloaded_work.authors) == 2
        author_names = {a.name for a in reloaded_work.authors}
        assert author_names == {"Neil Gaiman", "Terry Pratchett"}

        # Author can see their works
        reloaded_author1 = db_session.get(Author, author1.id)
        assert len(reloaded_author1.works) == 1
        assert reloaded_author1.works[0].canonical_title == "Good Omens"

        # Check composite PK behavior: same work and author with different role is valid
        link3 = BookWorkAuthor(work=work, author=author1, author_role="narrator", display_order=3)
        db_session.add(link3)
        db_session.commit()
        assert len(reloaded_work.work_authors) == 3

    def test_work_to_genre_relationship(self, db_session: Session):
        work = BookWork(canonical_title="Neuromancer")
        genre = Genre(name="Cyberpunk", slug="cyberpunk")
        link = BookWorkGenre(work=work, genre=genre, source="provider")

        db_session.add_all([work, genre, link])
        db_session.commit()

        reloaded_work = db_session.get(BookWork, work.id)
        assert len(reloaded_work.genres) == 1
        assert reloaded_work.genres[0].slug == "cyberpunk"
        assert len(genre.works) == 1

    def test_work_to_subject_relationship(self, db_session: Session):
        work = BookWork(canonical_title="Thinking, Fast and Slow")
        subject = Subject(name="Cognitive Bias", slug="cognitive-bias")
        link = BookWorkSubject(
            work=work,
            subject=subject,
            relevance_weight=Decimal("0.950"),
            source="curated",
        )

        db_session.add_all([work, subject, link])
        db_session.commit()

        reloaded_work = db_session.get(BookWork, work.id)
        assert len(reloaded_work.subjects) == 1
        assert reloaded_work.subjects[0].name == "Cognitive Bias"
        assert reloaded_work.work_subjects[0].relevance_weight == Decimal("0.950")

    def test_subject_self_referencing_relationship(self, db_session: Session):
        root = Subject(name="Science", slug="science")
        physics = Subject(name="Physics", slug="physics", parent=root)
        quantum = Subject(name="Quantum Mechanics", slug="quantum-mechanics", parent=physics)

        db_session.add_all([root, physics, quantum])
        db_session.commit()

        reloaded_quantum = db_session.get(Subject, quantum.id)
        assert reloaded_quantum.parent is not None
        assert reloaded_quantum.parent.name == "Physics"
        assert reloaded_quantum.parent.parent.name == "Science"

        reloaded_root = db_session.get(Subject, root.id)
        assert len(reloaded_root.children) == 1
        assert reloaded_root.children[0].name == "Physics"
        assert len(reloaded_root.children[0].children) == 1
        assert reloaded_root.children[0].children[0].name == "Quantum Mechanics"

    def test_subject_parent_nullable_and_set_null(self, db_session: Session):
        parent = Subject(name="History", slug="history")
        child = Subject(name="Ancient History", slug="ancient-history", parent=parent)
        db_session.add_all([parent, child])
        db_session.commit()

        # Child with null parent
        orphan = Subject(name="General Knowledge", slug="general-knowledge")
        db_session.add(orphan)
        db_session.commit()
        assert orphan.parent_subject_id is None
        assert orphan.parent is None

    def test_model_reprs(self):
        work_id = uuid.uuid4()
        work = BookWork(id=work_id, canonical_title="1984")
        assert "1984" in repr(work)

        author_id = uuid.uuid4()
        author = Author(id=author_id, name="George Orwell", normalized_name="george orwell")
        assert "George Orwell" in repr(author)

        genre_id = uuid.uuid4()
        genre = Genre(id=genre_id, name="Dystopian", slug="dystopian")
        assert "dystopian" in repr(genre)

        subject_id = uuid.uuid4()
        subject = Subject(id=subject_id, name="Surveillance", slug="surveillance")
        assert "surveillance" in repr(subject)

        edition = BookEdition(id=uuid.uuid4(), work_id=work_id, isbn_13="9780451524935")
        assert "9780451524935" in repr(edition)

        bwa = BookWorkAuthor(work_id=work_id, author_id=author_id, author_role="author")
        assert "author" in repr(bwa)

        bwg = BookWorkGenre(work_id=work_id, genre_id=genre_id)
        assert str(genre_id) in repr(bwg)

        bws = BookWorkSubject(work_id=work_id, subject_id=subject_id)
        assert str(subject_id) in repr(bws)

    def test_composite_pk_duplicate_rejected(self, db_session: Session):
        work = BookWork(canonical_title="Dune")
        author = Author(name="Frank Herbert", normalized_name="frank herbert")
        link1 = BookWorkAuthor(work=work, author=author, author_role="author", display_order=1)
        db_session.add_all([work, author, link1])
        db_session.commit()

        # In a separate session, inserting duplicate PK raises IntegrityError
        engine = db_session.bind
        with Session(engine) as session2:
            link2 = BookWorkAuthor(work_id=work.id, author_id=author.id, author_role="author", display_order=2)
            session2.add(link2)
            with pytest.raises(IntegrityError):
                session2.commit()

    def test_genre_and_subject_uniqueness_enforced(self, db_session: Session):
        g1 = Genre(name="Fantasy", slug="fantasy")
        db_session.add(g1)
        db_session.commit()

        # Duplicate genre name
        g2 = Genre(name="Fantasy", slug="fantasy-2")
        db_session.add(g2)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

        # Duplicate genre slug
        g3 = Genre(name="Epic Fantasy", slug="fantasy")
        db_session.add(g3)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

        s1 = Subject(name="Philosophy", slug="philosophy")
        db_session.add(s1)
        db_session.commit()

        # Duplicate subject name
        s2 = Subject(name="Philosophy", slug="philosophy-2")
        db_session.add(s2)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_cascade_delete_from_join_targets(self, db_session: Session):
        work = BookWork(canonical_title="Foundation")
        author = Author(name="Isaac Asimov", normalized_name="isaac asimov")
        genre = Genre(name="Sci-Fi", slug="sci-fi")
        subject = Subject(name="Psychohistory", slug="psychohistory")

        bwa = BookWorkAuthor(work=work, author=author, author_role="author")
        bwg = BookWorkGenre(work=work, genre=genre)
        bws = BookWorkSubject(work=work, subject=subject)

        db_session.add_all([work, author, genre, subject, bwa, bwg, bws])
        db_session.commit()

        work_id = work.id
        author_id = author.id

        # Deleting author cascades BookWorkAuthor, but work remains
        db_session.delete(author)
        db_session.commit()
        db_session.expire_all()

        assert db_session.get(Author, author_id) is None
        reloaded_work = db_session.get(BookWork, work_id)
        assert reloaded_work is not None
        assert len(reloaded_work.work_authors) == 0
        assert len(reloaded_work.work_genres) == 1
        assert len(reloaded_work.work_subjects) == 1

