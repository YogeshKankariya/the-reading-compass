from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base

if TYPE_CHECKING:
    from backend.db.models.book import BookEdition, BookWork
    from backend.db.models.user import User


class UserBook(Base):
    __tablename__ = "user_books"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    work_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("book_works.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    edition_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("book_editions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="want_to_read",
        server_default=text("'want_to_read'"),
    )
    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
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

    __table_args__ = (
        UniqueConstraint("user_id", "work_id", name="uq_user_books_user_work"),
        Index("ix_user_books_user_status", "user_id", "status"),
        CheckConstraint(
            "status IN ('want_to_read', 'currently_reading', 'completed', 'paused', 'abandoned')",
            name="ck_user_books_status",
        ),
    )

    # Relationships
    user: Mapped[User] = relationship("User", back_populates="user_books")
    work: Mapped[BookWork] = relationship("BookWork", back_populates="user_books")
    edition: Mapped[BookEdition | None] = relationship("BookEdition")
    progress: Mapped[ReadingProgress | None] = relationship(
        "ReadingProgress",
        back_populates="user_book",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<UserBook id={self.id} user_id={self.user_id} work_id={self.work_id} status={self.status!r}>"


class ReadingProgress(Base):
    __tablename__ = "reading_progress"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    user_book_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("user_books.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    current_page: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    total_pages: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    progress_percent: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default=text("0.00"),
    )
    started_at: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    completed_at: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    last_read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
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

    __table_args__ = (
        CheckConstraint("current_page IS NULL OR current_page >= 0", name="ck_reading_progress_current_page"),
        CheckConstraint("total_pages IS NULL OR total_pages > 0", name="ck_reading_progress_total_pages"),
        CheckConstraint("progress_percent >= 0.00 AND progress_percent <= 100.00", name="ck_reading_progress_percent"),
        CheckConstraint(
            "current_page IS NULL OR total_pages IS NULL OR current_page <= total_pages",
            name="ck_reading_progress_page_bounds",
        ),
    )

    # Relationships
    user_book: Mapped[UserBook] = relationship("UserBook", back_populates="progress")

    def __repr__(self) -> str:
        return f"<ReadingProgress id={self.id} user_book_id={self.user_book_id} progress_percent={self.progress_percent}>"


class BookRating(Base):
    __tablename__ = "book_ratings"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    work_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("book_works.id", ondelete="CASCADE"),
        nullable=False,
    )
    rating: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
    )
    review_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    is_spoiler: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
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

    __table_args__ = (
        UniqueConstraint("user_id", "work_id", name="uq_book_ratings_user_work"),
        CheckConstraint("rating >= 1 AND rating <= 5", name="ck_book_ratings_rating"),
        Index("ix_book_ratings_work_rating", "work_id", "rating"),
        Index("ix_book_ratings_user_rating", "user_id", "rating"),
    )

    # Relationships
    user: Mapped[User] = relationship("User", back_populates="ratings")
    work: Mapped[BookWork] = relationship("BookWork", back_populates="ratings")

    def __repr__(self) -> str:
        return f"<BookRating id={self.id} user_id={self.user_id} work_id={self.work_id} rating={self.rating}>"


class ReadingList(Base):
    __tablename__ = "reading_lists"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    is_public: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
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

    __table_args__ = (
        Index(
            "uq_reading_lists_user_lower_name",
            "user_id",
            func.lower(text("name")),
            unique=True,
        ),
        Index("ix_reading_lists_user_created", "user_id", text("created_at DESC")),
    )

    # Relationships
    user: Mapped[User] = relationship("User", back_populates="reading_lists")
    items: Mapped[list[ReadingListBook]] = relationship(
        "ReadingListBook",
        back_populates="reading_list",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ReadingListBook.position",
    )

    def __repr__(self) -> str:
        return f"<ReadingList id={self.id} user_id={self.user_id} is_public={self.is_public}>"


class ReadingListBook(Base):
    __tablename__ = "reading_list_books"

    list_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("reading_lists.id", ondelete="CASCADE"),
        primary_key=True,
    )
    work_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("book_works.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    position: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("list_id", "position", name="uq_reading_list_books_list_position"),
        CheckConstraint("position >= 1", name="ck_reading_list_books_position"),
    )

    # Relationships
    reading_list: Mapped[ReadingList] = relationship("ReadingList", back_populates="items")
    work: Mapped[BookWork] = relationship("BookWork", back_populates="reading_list_items")

    def __repr__(self) -> str:
        return f"<ReadingListBook list_id={self.list_id} work_id={self.work_id} position={self.position}>"
