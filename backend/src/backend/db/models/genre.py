from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    ForeignKey,
    String,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base

if TYPE_CHECKING:
    from backend.db.models.book import BookWork


class Genre(Base):
    __tablename__ = "genres"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
    )
    slug: Mapped[str] = mapped_column(
        String(120),
        unique=True,
        nullable=False,
    )

    # Relationships
    work_genres: Mapped[list[BookWorkGenre]] = relationship(
        "BookWorkGenre",
        back_populates="genre",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    works: Mapped[list[BookWork]] = relationship(
        "BookWork",
        secondary="book_work_genres",
        back_populates="genres",
        viewonly=True,
    )

    def __repr__(self) -> str:
        return f"<Genre id={self.id} name={self.name!r} slug={self.slug!r}>"


class BookWorkGenre(Base):
    __tablename__ = "book_work_genres"

    work_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("book_works.id", ondelete="CASCADE"),
        primary_key=True,
    )
    genre_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("genres.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    source: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="provider",
        server_default=text("'provider'"),
    )

    # Relationships
    work: Mapped[BookWork] = relationship(
        "BookWork",
        back_populates="work_genres",
    )
    genre: Mapped[Genre] = relationship(
        "Genre",
        back_populates="work_genres",
    )

    def __repr__(self) -> str:
        return f"<BookWorkGenre work_id={self.work_id} genre_id={self.genre_id}>"
