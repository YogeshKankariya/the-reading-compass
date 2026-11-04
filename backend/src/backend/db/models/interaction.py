from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    SmallInteger,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base

if TYPE_CHECKING:
    from backend.db.models.book import BookWork
    from backend.db.models.user import User


class UserInteraction(Base):
    __tablename__ = "user_interactions"

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
    work_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("book_works.id", ondelete="CASCADE"),
        nullable=True,
    )
    interaction_type: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
    )
    rating_value: Mapped[int | None] = mapped_column(
        SmallInteger,
        nullable=True,
    )
    context: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "interaction_type IN ('view', 'click', 'save', 'dismiss', 'rate', 'start', 'progress', 'complete', 'abandon', 'recommendation_click', 'recommendation_helpful', 'recommendation_not_helpful')",
            name="ck_user_interactions_type",
        ),
        CheckConstraint(
            "rating_value IS NULL OR (rating_value >= 1 AND rating_value <= 5)",
            name="ck_user_interactions_rating_value",
        ),
        Index("ix_user_interactions_user_created", "user_id", text("created_at DESC")),
        Index("ix_user_interactions_user_type_created", "user_id", "interaction_type", text("created_at DESC")),
        Index("ix_user_interactions_work_type", "work_id", "interaction_type"),
    )

    # Relationships
    user: Mapped[User] = relationship("User", back_populates="interactions")
    work: Mapped[BookWork | None] = relationship("BookWork", back_populates="interactions")

    def __repr__(self) -> str:
        return f"<UserInteraction id={self.id} user_id={self.user_id} interaction_type={self.interaction_type!r}>"
