from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKey,
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
    from backend.db.models.book import BookWork


class Author(Base):
    __tablename__ = "authors"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    normalized_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    bio: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    # Relationships
    work_authors: Mapped[list[BookWorkAuthor]] = relationship(
        "BookWorkAuthor",
        back_populates="author",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    works: Mapped[list[BookWork]] = relationship(
        "BookWork",
        secondary="book_work_authors",
        back_populates="authors",
        viewonly=True,
    )

    def __repr__(self) -> str:
        return f"<Author id={self.id} name={self.name!r}>"


class BookWorkAuthor(Base):
    __tablename__ = "book_work_authors"

    work_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("book_works.id", ondelete="CASCADE"),
        primary_key=True,
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("authors.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    author_role: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default="author",
        server_default=text("'author'"),
    )
    display_order: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=1,
        server_default=text("1"),
    )

    # Relationships
    work: Mapped[BookWork] = relationship(
        "BookWork",
        back_populates="work_authors",
    )
    author: Mapped[Author] = relationship(
        "Author",
        back_populates="work_authors",
    )

    def __repr__(self) -> str:
        return (
            f"<BookWorkAuthor work_id={self.work_id} "
            f"author_id={self.author_id} role={self.author_role!r}>"
        )
