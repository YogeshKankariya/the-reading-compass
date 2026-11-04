from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
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
    from backend.db.models.goal import ReadingGoal
    from backend.db.models.interaction import UserInteraction
    from backend.db.models.library import BookRating, ReadingList, UserBook
    from backend.db.models.recommendation import (
        RecommendationRequest,
        UserInterestProfile,
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    email: Mapped[str] = mapped_column(
        String(320),
        unique=True,
        nullable=False,
        index=True,
    )
    display_name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
    )
    password_hash: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
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

    # Relationships
    profile: Mapped[UserProfile | None] = relationship(
        "UserProfile",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    auth_identities: Mapped[list[UserAuthIdentity]] = relationship(
        "UserAuthIdentity",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    preferences: Mapped[list[UserPreference]] = relationship(
        "UserPreference",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    user_books: Mapped[list[UserBook]] = relationship(
        "UserBook",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    ratings: Mapped[list[BookRating]] = relationship(
        "BookRating",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    reading_lists: Mapped[list[ReadingList]] = relationship(
        "ReadingList",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    interactions: Mapped[list[UserInteraction]] = relationship(
        "UserInteraction",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    goals: Mapped[list[ReadingGoal]] = relationship(
        "ReadingGoal",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    interest_profile: Mapped[UserInterestProfile | None] = relationship(
        "UserInterestProfile",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    recommendation_requests: Mapped[list[RecommendationRequest]] = relationship(
        "RecommendationRequest",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} status={self.status!r}>"


class UserAuthIdentity(Base):
    __tablename__ = "user_auth_identities"

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
        index=True,
    )
    provider: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )
    provider_subject: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    provider_email: Mapped[str | None] = mapped_column(
        String(320),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "provider",
            "provider_subject",
            name="uq_user_auth_identities_provider_subject",
        ),
    )

    # Relationships
    user: Mapped[User] = relationship(
        "User",
        back_populates="auth_identities",
    )

    def __repr__(self) -> str:
        return (
            f"<UserAuthIdentity id={self.id} user_id={self.user_id} "
            f"provider={self.provider!r}>"
        )


class UserProfile(Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    reader_level: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="casual",
        server_default=text("'casual'"),
    )
    preferred_language: Mapped[str] = mapped_column(
        String(12),
        nullable=False,
        default="en",
        server_default=text("'en'"),
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
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    user: Mapped[User] = relationship(
        "User",
        back_populates="profile",
    )

    def __repr__(self) -> str:
        return (
            f"<UserProfile user_id={self.user_id} "
            f"reader_level={self.reader_level!r} preferred_language={self.preferred_language!r}>"
        )


class UserPreference(Base):
    __tablename__ = "user_preferences"

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
        index=True,
    )
    preference_type: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
    )
    preference_value: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    preference_weight: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 3),
        nullable=True,
    )
    source: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="onboarding",
        server_default=text("'onboarding'"),
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
        UniqueConstraint(
            "user_id",
            "preference_type",
            "preference_value",
            name="uq_user_preferences_user_type_value",
        ),
        CheckConstraint(
            "preference_weight >= 0 AND preference_weight <= 1",
            name="ck_user_preferences_preference_weight",
        ),
    )

    # Relationships
    user: Mapped[User] = relationship(
        "User",
        back_populates="preferences",
    )

    def __repr__(self) -> str:
        return (
            f"<UserPreference id={self.id} user_id={self.user_id} "
            f"type={self.preference_type!r}>"
        )
