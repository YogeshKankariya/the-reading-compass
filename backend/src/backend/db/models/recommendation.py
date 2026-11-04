from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
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


class UserInterestProfile(Base):
    __tablename__ = "user_interest_profiles"

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
    model_name: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
    )
    model_revision: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
    )
    interest_vector: Mapped[list[float] | None] = mapped_column(
        Vector(384),
        nullable=True,
    )
    inferred_topics: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
    )
    interaction_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    last_calculated_at: Mapped[datetime | None] = mapped_column(
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
        CheckConstraint("interaction_count >= 0", name="ck_user_interest_profiles_interaction_count"),
        UniqueConstraint("user_id", name="uq_user_interest_profiles_user_id"),
    )

    # Relationships
    user: Mapped[User] = relationship("User", back_populates="interest_profile")

    def __repr__(self) -> str:
        return f"<UserInterestProfile id={self.id} user_id={self.user_id} model={self.model_name!r} interaction_count={self.interaction_count}>"


class RecommendationRequest(Base):
    __tablename__ = "recommendation_requests"

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
    request_type: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
    )
    request_context: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
    )
    algorithm_version: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "request_type IN ('next_read', 'discover', 'tbr_priority', 'goal_based', 'mood_based', 'new_author')",
            name="ck_recommendation_requests_type",
        ),
        Index("ix_recommendation_requests_user_created", "user_id", text("created_at DESC")),
    )

    # Relationships
    user: Mapped[User] = relationship("User", back_populates="recommendation_requests")
    items: Mapped[list[RecommendationItem]] = relationship(
        "RecommendationItem",
        back_populates="request",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="RecommendationItem.rank_position",
    )

    def __repr__(self) -> str:
        return f"<RecommendationRequest id={self.id} user_id={self.user_id} type={self.request_type!r} version={self.algorithm_version!r}>"


class RecommendationItem(Base):
    __tablename__ = "recommendation_items"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    request_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("recommendation_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    work_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("book_works.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rank_position: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    score: Mapped[Decimal] = mapped_column(
        Numeric(8, 6),
        nullable=False,
    )
    score_breakdown: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
    )
    reason_codes: Mapped[list[str] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
    )
    feedback_type: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )
    feedback_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    feedback_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint("rank_position >= 1", name="ck_recommendation_items_rank_position"),
        CheckConstraint("score >= 0.000000 AND score <= 1.000000", name="ck_recommendation_items_score_range"),
        CheckConstraint(
            "feedback_type IS NULL OR feedback_type IN ('helpful', 'not_helpful', 'save', 'dismiss')",
            name="ck_recommendation_items_feedback_type",
        ),
        UniqueConstraint("request_id", "work_id", name="uq_recommendation_items_request_work"),
        UniqueConstraint("request_id", "rank_position", name="uq_recommendation_items_request_rank"),
    )

    # Relationships
    request: Mapped[RecommendationRequest] = relationship("RecommendationRequest", back_populates="items")
    work: Mapped[BookWork] = relationship("BookWork", back_populates="recommendation_items")

    def __repr__(self) -> str:
        return f"<RecommendationItem id={self.id} request_id={self.request_id} work_id={self.work_id} rank={self.rank_position} score={self.score}>"
