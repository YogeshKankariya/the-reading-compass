from __future__ import annotations

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Numeric,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base

if TYPE_CHECKING:
    from backend.db.models.book import BookWork


class Subject(Base):
    __tablename__ = "subjects"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(
        String(150),
        unique=True,
        nullable=False,
    )
    slug: Mapped[str] = mapped_column(
        String(170),
        unique=True,
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    parent_subject_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("subjects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Self-referencing relationship
    parent: Mapped[Subject | None] = relationship(
        "Subject",
        remote_side=lambda: [Subject.id],
        back_populates="children",
    )
    children: Mapped[list[Subject]] = relationship(
        "Subject",
        back_populates="parent",
    )

    # Relationships to work
    work_subjects: Mapped[list[BookWorkSubject]] = relationship(
        "BookWorkSubject",
        back_populates="subject",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    works: Mapped[list[BookWork]] = relationship(
        "BookWork",
        secondary="book_work_subjects",
        back_populates="subjects",
        viewonly=True,
    )

    def __repr__(self) -> str:
        return f"<Subject id={self.id} name={self.name!r} slug={self.slug!r}>"


class BookWorkSubject(Base):
    __tablename__ = "book_work_subjects"

    work_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("book_works.id", ondelete="CASCADE"),
        primary_key=True,
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("subjects.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    relevance_weight: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 3),
        nullable=True,
    )
    source: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="provider",
        server_default=text("'provider'"),
    )

    __table_args__ = (
        CheckConstraint(
            "relevance_weight >= 0 AND relevance_weight <= 1",
            name="ck_book_work_subjects_relevance_weight",
        ),
    )

    # Relationships
    work: Mapped[BookWork] = relationship(
        "BookWork",
        back_populates="work_subjects",
    )
    subject: Mapped[Subject] = relationship(
        "Subject",
        back_populates="work_subjects",
    )

    def __repr__(self) -> str:
        return f"<BookWorkSubject work_id={self.work_id} subject_id={self.subject_id}>"
