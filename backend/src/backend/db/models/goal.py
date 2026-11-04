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
    from backend.db.models.user import User


class ReadingGoal(Base):
    __tablename__ = "reading_goals"

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
    title: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    goal_type: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
    )
    target_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="active",
        server_default=text("'active'"),
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
        CheckConstraint(
            "goal_type IN ('learn_subject', 'read_more', 'explore_genre', 'career_development', 'skill_depth', 'custom')",
            name="ck_reading_goals_goal_type",
        ),
        CheckConstraint(
            "status IN ('active', 'completed', 'paused', 'abandoned')",
            name="ck_reading_goals_status",
        ),
        UniqueConstraint("id", "user_id", name="uq_reading_goals_id_user"),
        Index("ix_reading_goals_user_status", "user_id", "status"),
    )

    # Relationships
    user: Mapped[User] = relationship("User", back_populates="goals")

    def __repr__(self) -> str:
        return f"<ReadingGoal id={self.id} user_id={self.user_id} goal_type={self.goal_type!r} status={self.status!r}>"
