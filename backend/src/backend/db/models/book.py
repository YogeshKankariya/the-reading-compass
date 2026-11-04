from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base

if TYPE_CHECKING:
    from backend.db.models.author import Author, BookWorkAuthor
    from backend.db.models.embedding import BookEmbedding
    from backend.db.models.genre import BookWorkGenre, Genre
    from backend.db.models.interaction import UserInteraction
    from backend.db.models.library import (
        BookRating,
        ReadingListBook,
        UserBook,
    )
    from backend.db.models.recommendation import RecommendationItem
    from backend.db.models.subject import BookWorkSubject, Subject


class BookWork(Base):
    __tablename__ = "book_works"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    canonical_title: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )
    subtitle: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    original_language: Mapped[str | None] = mapped_column(
        String(12),
        nullable=True,
    )
    first_published_year: Mapped[int | None] = mapped_column(
        SmallInteger,
        nullable=True,
    )
    difficulty_level: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )
    estimated_reading_minutes: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    cover_image_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    metadata_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="unverified",
        server_default=text("'unverified'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    editions: Mapped[list[BookEdition]] = relationship(
        "BookEdition",
        back_populates="work",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    work_authors: Mapped[list[BookWorkAuthor]] = relationship(
        "BookWorkAuthor",
        back_populates="work",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    authors: Mapped[list[Author]] = relationship(
        "Author",
        secondary="book_work_authors",
        back_populates="works",
        viewonly=True,
    )
    work_genres: Mapped[list[BookWorkGenre]] = relationship(
        "BookWorkGenre",
        back_populates="work",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    genres: Mapped[list[Genre]] = relationship(
        "Genre",
        secondary="book_work_genres",
        back_populates="works",
        viewonly=True,
    )
    work_subjects: Mapped[list[BookWorkSubject]] = relationship(
        "BookWorkSubject",
        back_populates="work",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    subjects: Mapped[list[Subject]] = relationship(
        "Subject",
        secondary="book_work_subjects",
        back_populates="works",
        viewonly=True,
    )
    user_books: Mapped[list[UserBook]] = relationship(
        "UserBook",
        back_populates="work",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    ratings: Mapped[list[BookRating]] = relationship(
        "BookRating",
        back_populates="work",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    reading_list_items: Mapped[list[ReadingListBook]] = relationship(
        "ReadingListBook",
        back_populates="work",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    interactions: Mapped[list[UserInteraction]] = relationship(
        "UserInteraction",
        back_populates="work",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    embeddings: Mapped[list[BookEmbedding]] = relationship(
        "BookEmbedding",
        back_populates="work",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    recommendation_items: Mapped[list[RecommendationItem]] = relationship(
        "RecommendationItem",
        back_populates="work",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<BookWork id={self.id} canonical_title={self.canonical_title!r}>"


class BookEdition(Base):
    __tablename__ = "book_editions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    work_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("book_works.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    isbn_10: Mapped[str | None] = mapped_column(
        String(10),
        nullable=True,
    )
    isbn_13: Mapped[str | None] = mapped_column(
        String(13),
        nullable=True,
    )
    publisher: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    publication_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    page_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    format: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )
    language: Mapped[str | None] = mapped_column(
        String(12),
        nullable=True,
    )
    cover_image_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint("page_count >= 0", name="ck_book_editions_page_count_positive"),
        Index(
            "ix_book_editions_isbn_10",
            "isbn_10",
            unique=True,
            postgresql_where=text("isbn_10 IS NOT NULL"),
        ),
        Index(
            "ix_book_editions_isbn_13",
            "isbn_13",
            unique=True,
            postgresql_where=text("isbn_13 IS NOT NULL"),
        ),
    )

    # Relationships
    work: Mapped[BookWork] = relationship(
        "BookWork",
        back_populates="editions",
    )

    def __repr__(self) -> str:
        return f"<BookEdition id={self.id} work_id={self.work_id} isbn_13={self.isbn_13!r}>"
