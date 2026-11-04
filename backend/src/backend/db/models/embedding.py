from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base

if TYPE_CHECKING:
    from backend.db.models.book import BookWork


class BookEmbedding(Base):
    __tablename__ = "book_embeddings"

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
    model_name: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
    )
    model_revision: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
    )
    embedding: Mapped[list[float]] = mapped_column(
        Vector(384),
        nullable=False,
    )
    source_text_hash: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "work_id",
            "model_name",
            "model_revision",
            "source_text_hash",
            name="uq_book_embeddings_work_model_hash",
        ),
        Index(
            "uq_book_embeddings_active_work",
            "work_id",
            "model_name",
            "model_revision",
            unique=True,
            postgresql_where=text("is_active = true"),
            sqlite_where=text("is_active = 1"),
        ),
        Index(
            "ix_book_embeddings_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    # Relationships
    work: Mapped[BookWork] = relationship("BookWork", back_populates="embeddings")

    def __repr__(self) -> str:
        return f"<BookEmbedding id={self.id} work_id={self.work_id} model={self.model_name!r} is_active={self.is_active}>"
