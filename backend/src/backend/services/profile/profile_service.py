from __future__ import annotations

import hashlib
import logging
import math
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Sequence

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.core.config import Settings, settings as default_settings
from backend.db.models.embedding import BookEmbedding
from backend.db.models.interaction import UserInteraction
from backend.db.models.library import BookRating, ReadingProgress, UserBook
from backend.db.models.recommendation import (
    RecommendationItem,
    RecommendationRequest,
    UserInterestProfile,
)
from backend.db.models.user import UserPreference
from backend.schemas.profile import (
    ExplicitPreferenceInput,
    ProfileRefreshResult,
)
from backend.services.profile.signal_extractor import ProfileSignalExtractor
from backend.services.profile.topic_calculator import ProfileTopicCalculator
from backend.services.profile.vector_builder import build_user_interest_profile_vector

logger = logging.getLogger(__name__)


def _ensure_utc(dt: datetime) -> datetime:
    """Ensure datetime is timezone-aware and normalized to UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def compute_explicit_preference_hash(
    preferences: Sequence[UserPreference | ExplicitPreferenceInput],
) -> str:
    """Compute a deterministic SHA-256 hash across normalized explicit user preferences.

    Canonical format per preference:
        `{preference_type}|{preference_value}|{preference_weight:.6f}|{source}`
    Sorted lexicographically by (preference_type, preference_value, source) to guarantee
    deterministic hashing independent of database query order.

    Detects additions, modifications, and deletions.
    """
    items: list[tuple[str, str, str, str]] = []
    for p in preferences:
        p_type = str(p.preference_type).strip()
        p_val = str(p.preference_value).strip()
        p_src = str(p.source).strip() if p.source is not None else "onboarding"
        raw_weight = p.preference_weight
        if raw_weight is None:
            w_float = 1.0
        else:
            w_float = float(raw_weight)
        w_str = f"{w_float:.6f}"
        items.append((p_type, p_val, w_str, p_src))

    items.sort(key=lambda x: (x[0], x[1], x[3], x[2]))
    canonical = "\n".join(f"{t}|{v}|{w}|{s}" for t, v, w, s in items)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_authoritative_state_fingerprint(
    ratings: Sequence[BookRating],
    user_books: Sequence[UserBook],
    rec_items: Sequence[RecommendationItem],
) -> str:
    """Compute a deterministic SHA-256 fingerprint over authoritative user state.

    Includes all semantically relevant state affecting profile calculation:
        1. Current BookRating records: work_id, rating value.
        2. Current UserBook lifecycle: work_id, status, reading progress (progress_percent, last_read_at).
        3. Current RecommendationItem feedback: work_id, feedback_type, feedback_at.

    Rules:
        - Sorted deterministically to be independent of query order.
        - Canonical string format per item.
        - Detects additions, updates, and deletions of ratings, shelf status, progress, and feedback.
        - Pure UTF-8 encoding hashed via standard SHA-256 (no Python hash()).
    """
    items: list[str] = []

    # 1. Book ratings
    for r in ratings:
        items.append(f"R|{r.work_id}|{r.rating}")

    # 2. User books & reading progress
    for ub in user_books:
        prog_part = ""
        if ub.progress is not None:
            pct_str = f"{float(ub.progress.progress_percent):.2f}"
            dt_str = ub.progress.last_read_at.isoformat() if ub.progress.last_read_at is not None else ""
            prog_part = f"{pct_str}|{dt_str}"
        items.append(f"B|{ub.work_id}|{ub.status}|{prog_part}")

    # 3. Recommendation items with feedback
    for item in rec_items:
        if item.feedback_type is not None:
            fb_dt_str = item.feedback_at.isoformat() if item.feedback_at is not None else ""
            items.append(f"F|{item.work_id}|{item.feedback_type}|{fb_dt_str}")

    items.sort()
    canonical = "\n".join(items)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def generate_user_profile_lock_key(user_id: uuid.UUID) -> int:
    """Derive a stable, deterministic signed 64-bit integer for pg_advisory_xact_lock.

    Uses SHA-256 with domain prefix b"profile_refresh_lock:" taking the first 8 bytes
    as a signed 64-bit integer.
    """
    digest = hashlib.sha256(b"profile_refresh_lock:" + user_id.bytes).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


class ProfileService:
    """Coordinates signal extraction, vector construction, topic calculation, and atomic profile persistence."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or default_settings
        self.signal_extractor = ProfileSignalExtractor(session=self.session, settings=self.settings)
        self.topic_calculator = ProfileTopicCalculator(session=self.session, settings=self.settings)

    async def get_profile(self, user_id: uuid.UUID) -> UserInterestProfile | None:
        """Fetch the current UserInterestProfile for a given user without locking."""
        stmt = select(UserInterestProfile).where(UserInterestProfile.user_id == user_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_authoritative_state_fingerprint(self, user_id: uuid.UUID) -> str:
        """Fetch current authoritative user records and compute their fingerprint."""
        ratings_stmt = select(BookRating).where(BookRating.user_id == user_id)
        ratings_res = await self.session.execute(ratings_stmt)
        ratings = list(ratings_res.scalars().all())

        user_books_stmt = (
            select(UserBook)
            .options(selectinload(UserBook.progress))
            .where(UserBook.user_id == user_id)
        )
        ub_res = await self.session.execute(user_books_stmt)
        user_books = list(ub_res.scalars().all())

        rec_items_stmt = (
            select(RecommendationItem)
            .join(RecommendationRequest, RecommendationItem.request_id == RecommendationRequest.id)
            .where(
                RecommendationRequest.user_id == user_id,
                RecommendationItem.feedback_type.is_not(None),
            )
        )
        rec_res = await self.session.execute(rec_items_stmt)
        rec_items = list(rec_res.scalars().all())

        return compute_authoritative_state_fingerprint(
            ratings=ratings,
            user_books=user_books,
            rec_items=rec_items,
        )

    async def should_refresh_profile(
        self,
        user_id: uuid.UUID,
        profile: UserInterestProfile | None = None,
        force: bool = False,
    ) -> tuple[
        bool,
        Literal[
            "initial_profile",
            "threshold_reached",
            "authoritative_state_change",
            "explicit_preference_change",
            "model_revision_mismatch",
            "forced",
            "none",
        ],
    ]:
        """Lightweight freshness evaluation testing the approved trigger conditions.

        Evaluation order:
            1. Forced refresh flag.
            2. Missing profile (initial profile).
            3. Model name or revision mismatch.
            4. Explicit preference addition, edit, or deletion (via hash comparison).
            5. Authoritative user state change (ratings, lifecycle status, progress, recommendation feedback).
            6. Eligible interaction count watermark threshold reached.
            7. New compatible active embeddings added to catalogue for relevant works.
            8. No changes (profile is fresh).
        """
        if force:
            return True, "forced"

        current_profile = profile
        if current_profile is None:
            current_profile = await self.get_profile(user_id)

        # 1. Initial profile generation
        if current_profile is None or current_profile.last_calculated_at is None:
            return True, "initial_profile"

        # 2. Model name or revision mismatch
        target_model = self.settings.PROFILE_EMBEDDING_MODEL_NAME
        target_revision = self.settings.PROFILE_EMBEDDING_MODEL_REVISION

        if current_profile.model_name != target_model:
            return True, "model_revision_mismatch"

        if target_revision is not None and current_profile.model_revision != target_revision:
            return True, "model_revision_mismatch"

        if target_revision is None and current_profile.model_revision != "unset":
            return True, "model_revision_mismatch"

        # 3. Explicit preference mutation or deletion
        prefs_stmt = select(UserPreference).where(UserPreference.user_id == user_id)
        prefs_res = await self.session.execute(prefs_stmt)
        current_prefs = list(prefs_res.scalars().all())
        current_pref_hash = compute_explicit_preference_hash(current_prefs)

        stored_meta = (
            current_profile.inferred_topics.get("meta", {})
            if isinstance(current_profile.inferred_topics, dict)
            else {}
        )
        stored_pref_hash = stored_meta.get("explicit_preference_hash")

        if stored_pref_hash != current_pref_hash:
            return True, "explicit_preference_change"

        # 4. Authoritative user state change (ratings, shelf status, reading progress, recommendation feedback)
        current_state_fingerprint = await self.get_authoritative_state_fingerprint(user_id)
        stored_state_fingerprint = stored_meta.get("authoritative_state_fingerprint")

        if stored_state_fingerprint != current_state_fingerprint:
            return True, "authoritative_state_change"

        # 5. Interaction threshold check relative to watermark
        current_eligible_count = await self.signal_extractor.get_eligible_interaction_count(user_id)
        if current_eligible_count - current_profile.interaction_count >= self.settings.PROFILE_REFRESH_THRESHOLD:
            return True, "threshold_reached"

        # 6. New compatible active embeddings added to catalogue
        if target_revision is not None and current_profile.last_calculated_at is not None:
            user_works_subq = (
                select(UserInteraction.work_id)
                .where(UserInteraction.user_id == user_id, UserInteraction.work_id.is_not(None))
                .union(
                    select(UserBook.work_id).where(UserBook.user_id == user_id)
                )
                .subquery()
            )
            emb_stmt = (
                select(BookEmbedding.id)
                .join(user_works_subq, BookEmbedding.work_id == user_works_subq.c.work_id)
                .where(
                    BookEmbedding.model_name == target_model,
                    BookEmbedding.model_revision == target_revision,
                    BookEmbedding.is_active.is_(True),
                    BookEmbedding.created_at > current_profile.last_calculated_at,
                )
                .limit(1)
            )
            emb_res = await self.session.execute(emb_stmt)
            if emb_res.scalar_one_or_none() is not None:
                return True, "threshold_reached"

        return False, "none"

    async def refresh_profile(
        self,
        user_id: uuid.UUID,
        force: bool = False,
        calculation_time: datetime | None = None,
        preference_embeddings: Mapping[str, Sequence[float]] | None = None,
    ) -> ProfileRefreshResult:
        """Atomically calculate and persist the user's interest profile under advisory lock.

        Ensures:
            1. Per-user transaction advisory locking (pg_advisory_xact_lock).
            2. Double-checked locking so concurrent callers for the same user reuse the fresh profile.
            3. Pure calculation coordinated across signal extractor, vector builder, and topic calculator.
            4. Atomic insertion or update preserving primary key and creation timestamps.
            5. Automatic rollback on calculation failure preserving existing profile state.
        """
        if self.session.in_transaction():
            return await self._execute_refresh(
                user_id=user_id,
                force=force,
                calculation_time=calculation_time,
                preference_embeddings=preference_embeddings,
            )
        else:
            async with self.session.begin():
                return await self._execute_refresh(
                    user_id=user_id,
                    force=force,
                    calculation_time=calculation_time,
                    preference_embeddings=preference_embeddings,
                )

    async def _execute_refresh(
        self,
        user_id: uuid.UUID,
        force: bool,
        calculation_time: datetime | None,
        preference_embeddings: Mapping[str, Sequence[float]] | None = None,
    ) -> ProfileRefreshResult:
        """Internal refresh implementation running inside an active database transaction."""
        lock_key = generate_user_profile_lock_key(user_id)

        # 1. Acquire transaction advisory lock on PostgreSQL
        bind = self.session.bind
        if bind is not None and getattr(bind.dialect, "name", "") == "postgresql":
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": lock_key},
            )

        # 2. Re-read profile inside lock for double-checked locking
        profile_stmt = select(UserInterestProfile).where(UserInterestProfile.user_id == user_id)
        profile_res = await self.session.execute(profile_stmt)
        existing_profile = profile_res.scalar_one_or_none()

        should_refresh, reason = await self.should_refresh_profile(
            user_id=user_id,
            profile=existing_profile,
            force=force,
        )

        if not should_refresh:
            prev_count = existing_profile.interaction_count if existing_profile else None
            return ProfileRefreshResult(
                refresh_required=False,
                refresh_completed=False,
                reason="none",
                previous_interaction_count=prev_count,
                current_interaction_count=prev_count,
                calculated_at=existing_profile.last_calculated_at if existing_profile else None,
            )

        # 3. Snapshot calculation reference instant and capture authoritative state fingerprint
        calc_time = _ensure_utc(calculation_time or datetime.now(timezone.utc))
        target_revision = self.settings.PROFILE_EMBEDDING_MODEL_REVISION

        # Capture authoritative-state fingerprint immediately before extraction as a conservative watermark
        calculation_fingerprint = await self.get_authoritative_state_fingerprint(user_id)

        # 4. Extract signals and embeddings
        extracted_signals = await self.signal_extractor.extract_profile_signals(
            user_id=user_id,
            model_revision=target_revision,
        )

        # 5. Build explicit and behavioral vectors
        # Pair explicit preferences with embeddings if available
        paired_explicit_prefs: list[tuple[ExplicitPreferenceInput, Sequence[float]]] = []
        if preference_embeddings:
            for p in extracted_signals.explicit_preferences:
                if p.preference_value in preference_embeddings:
                    paired_explicit_prefs.append((p, preference_embeddings[p.preference_value]))

        vector_result = build_user_interest_profile_vector(
            explicit_preferences=paired_explicit_prefs,
            resolved_signals=extracted_signals.resolved_signals,
            work_embeddings=extracted_signals.work_embeddings,
            calculation_time=calc_time,
            settings=self.settings,
        )

        # 6. Calculate topic affinities and negative work signals
        inferred_topics_payload = await self.topic_calculator.calculate(
            extracted_signals=extracted_signals,
            calculation_time=calc_time,
        )

        # 7. Record preference hash and authoritative state fingerprint in inferred topics metadata
        pref_hash = compute_explicit_preference_hash(extracted_signals.explicit_preferences)
        inferred_topics_payload.meta["explicit_preference_hash"] = pref_hash
        inferred_topics_payload.meta["explicit_preference_count"] = len(extracted_signals.explicit_preferences)
        inferred_topics_payload.meta["authoritative_state_fingerprint"] = calculation_fingerprint

        topics_dict = inferred_topics_payload.model_dump(mode="json")
        model_rev_to_store = extracted_signals.model_revision or "unset"
        prev_count = existing_profile.interaction_count if existing_profile else None
        new_count = extracted_signals.eligible_interaction_count

        # 8. Atomically insert or update profile record
        if existing_profile is None:
            new_profile = UserInterestProfile(
                user_id=user_id,
                model_name=extracted_signals.model_name,
                model_revision=model_rev_to_store,
                interest_vector=vector_result.interest_vector,
                inferred_topics=topics_dict,
                interaction_count=new_count,
                last_calculated_at=calc_time,
            )
            self.session.add(new_profile)
        else:
            existing_profile.model_name = extracted_signals.model_name
            existing_profile.model_revision = model_rev_to_store
            existing_profile.interest_vector = vector_result.interest_vector
            existing_profile.inferred_topics = topics_dict
            existing_profile.interaction_count = new_count
            existing_profile.last_calculated_at = calc_time
            existing_profile.updated_at = datetime.now(timezone.utc)

        await self.session.flush()

        return ProfileRefreshResult(
            refresh_required=True,
            refresh_completed=True,
            reason=reason,
            previous_interaction_count=prev_count,
            current_interaction_count=new_count,
            calculated_at=calc_time,
        )
