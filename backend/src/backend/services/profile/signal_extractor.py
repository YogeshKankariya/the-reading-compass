from __future__ import annotations

import math
import uuid
from datetime import datetime, time, timezone
from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.core.config import Settings, settings as default_settings
from backend.db.models.embedding import BookEmbedding
from backend.db.models.interaction import UserInteraction
from backend.db.models.library import BookRating, ReadingProgress, UserBook
from backend.db.models.recommendation import RecommendationItem, RecommendationRequest
from backend.db.models.user import UserPreference
from backend.schemas.profile import ExplicitPreferenceInput, ResolvedWorkSignal


# ==============================================================================
# Constants & Configuration Defaults
# ==============================================================================

# Rolling window within which consecutive passive events (view/click) on the same
# work are considered a single eligible event for watermark calculation.
PASSIVE_THROTTLING_WINDOW_SECONDS: float = 3600.0  # 1 hour

APPROVED_INTERACTION_TYPES: frozenset[str] = frozenset(
    {
        "view",
        "click",
        "save",
        "dismiss",
        "rate",
        "start",
        "progress",
        "complete",
        "abandon",
        "recommendation_click",
        "recommendation_helpful",
        "recommendation_not_helpful",
    }
)


def _ensure_utc(dt: datetime) -> datetime:
    """Ensure datetime is timezone-aware and normalized to UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ==============================================================================
# Result Containers
# ==============================================================================

class ExtractedProfileSignals(BaseModel):
    """Result payload produced by profile signal extraction."""

    model_config = ConfigDict(frozen=True)

    user_id: uuid.UUID
    resolved_signals: list[ResolvedWorkSignal]
    neutral_signals: list[ResolvedWorkSignal] = Field(default_factory=list)
    explicit_preferences: list[ExplicitPreferenceInput] = Field(default_factory=list)
    eligible_interaction_count: int = Field(ge=0)
    work_embeddings: dict[uuid.UUID, list[float]] = Field(default_factory=dict)
    embedding_status: Literal["available", "revision_unspecified", "no_compatible_embeddings"] = "available"
    model_name: str
    model_revision: str | None = None
    extracted_at: datetime


# ==============================================================================
# Signal Extraction Service
# ==============================================================================

class ProfileSignalExtractor:
    """Extracts, resolves, and validates profile interaction signals and compatible embeddings.

    Applies approved Milestone 5.1.3 precedence hierarchy:
        Rating > Reading Lifecycle > Recommendation Feedback > Library Save (TBR) > Passive Browsing

    Rules enforced:
        1. Deterministic priority selection: each work yields at most one resolved signal.
        2. Neutral-rating exception: a 3-star rating (+0.10) is preserved as neutral/topic-only
           and does not suppress non-neutral lifecycle, feedback, or save signals.
        3. Model revision filtering: unpinned/unset revisions never match via wildcards.
        4. Passive ceiling: only the strongest passive signal is selected; views/clicks are never summed.
        5. Watermark consistency: event-level eligibility filter excludes null work_ids,
           unsupported types, and authoritative duplicates with 1-hour passive throttling.
    """

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or default_settings

    # --------------------------------------------------------------------------
    # Public API
    # --------------------------------------------------------------------------

    async def get_eligible_interaction_count(self, user_id: uuid.UUID) -> int:
        """Count eligible interaction events for the watermark threshold.

        Executes the same eligibility rules as full profile extraction without
        performing work-level signal resolution.
        """
        # Fetch ratings for deduplication
        ratings_stmt = select(BookRating.work_id).where(BookRating.user_id == user_id)
        ratings_res = await self.session.execute(ratings_stmt)
        active_rated_work_ids = {row[0] for row in ratings_res.all()}

        # Fetch active TBR saves for deduplication
        user_books_stmt = select(UserBook.work_id, UserBook.status).where(UserBook.user_id == user_id)
        ub_res = await self.session.execute(user_books_stmt)
        active_tbr_work_ids = {row[0] for row in ub_res.all() if row[1] == "want_to_read"}

        # Fetch interactions ordered chronologically
        interactions_stmt = (
            select(UserInteraction)
            .where(UserInteraction.user_id == user_id)
            .order_by(UserInteraction.created_at.asc(), UserInteraction.id.asc())
        )
        interactions_res = await self.session.execute(interactions_stmt)
        interactions = list(interactions_res.scalars().all())

        return self._count_eligible_events(
            interactions=interactions,
            active_rated_work_ids=active_rated_work_ids,
            active_tbr_work_ids=active_tbr_work_ids,
        )

    async def get_compatible_embeddings(
        self,
        work_ids: Sequence[uuid.UUID],
        model_revision: str | None = None,
    ) -> tuple[dict[uuid.UUID, list[float]], Literal["available", "revision_unspecified", "no_compatible_embeddings"], str | None]:
        """Retrieve compatible active embeddings for given work IDs.

        Requirements:
            - Model name must match settings.PROFILE_EMBEDDING_MODEL_NAME.
            - Model revision must match the explicitly passed revision or settings.PROFILE_EMBEDDING_MODEL_REVISION.
            - If revision is None/unset, no query is run and 'revision_unspecified' is returned.
            - Only active embeddings (is_active = true) are queried.
            - Returned embeddings must have expected dimension.
        """
        effective_revision = model_revision if model_revision is not None else self.settings.PROFILE_EMBEDDING_MODEL_REVISION

        if not effective_revision or not effective_revision.strip():
            return {}, "revision_unspecified", None

        effective_revision = effective_revision.strip()

        if not work_ids:
            return {}, "no_compatible_embeddings", effective_revision

        stmt = (
            select(BookEmbedding)
            .where(
                BookEmbedding.work_id.in_(list(work_ids)),
                BookEmbedding.is_active.is_(True),
                BookEmbedding.model_name == self.settings.PROFILE_EMBEDDING_MODEL_NAME,
                BookEmbedding.model_revision == effective_revision,
            )
        )
        res = await self.session.execute(stmt)
        embeddings = res.scalars().all()

        embeddings_map: dict[uuid.UUID, list[float]] = {}
        expected_dim = self.settings.PROFILE_EMBEDDING_DIMENSION

        for emb in embeddings:
            raw_vec = emb.embedding
            if raw_vec is not None:
                vec_list = list(raw_vec)
                if len(vec_list) == expected_dim and all(math.isfinite(x) for x in vec_list):
                    # Partial unique index guarantees at most one active embedding per work
                    embeddings_map[emb.work_id] = vec_list

        status: Literal["available", "no_compatible_embeddings"] = (
            "available" if embeddings_map else "no_compatible_embeddings"
        )
        return embeddings_map, status, effective_revision

    async def extract_profile_signals(
        self,
        user_id: uuid.UUID,
        model_revision: str | None = None,
        calculation_time: datetime | None = None,
    ) -> ExtractedProfileSignals:
        """Extract and resolve all user signals and compatible active embeddings.

        Accepts optional model_revision override (e.g. for testing) and calculation_time.
        """
        now_utc = _ensure_utc(calculation_time or datetime.now(timezone.utc))
        weights = self.settings.get_signal_weights()

        # 1. Fetch ratings
        ratings_stmt = select(BookRating).where(BookRating.user_id == user_id)
        ratings_res = await self.session.execute(ratings_stmt)
        ratings = list(ratings_res.scalars().all())

        # 2. Fetch user books with reading progress
        user_books_stmt = (
            select(UserBook)
            .options(selectinload(UserBook.progress))
            .where(UserBook.user_id == user_id)
        )
        ub_res = await self.session.execute(user_books_stmt)
        user_books = list(ub_res.scalars().all())

        # 3. Fetch recommendation items with feedback
        rec_items_stmt = (
            select(RecommendationItem)
            .join(RecommendationRequest, RecommendationItem.request_id == RecommendationRequest.id)
            .where(
                RecommendationRequest.user_id == user_id,
                RecommendationItem.feedback_type.is_not(None),
            )
            .order_by(RecommendationItem.feedback_at.desc().nullslast(), RecommendationItem.created_at.desc())
        )
        rec_res = await self.session.execute(rec_items_stmt)
        rec_items = list(rec_res.scalars().all())

        # 4. Fetch interactions
        interactions_stmt = (
            select(UserInteraction)
            .where(UserInteraction.user_id == user_id)
            .order_by(UserInteraction.created_at.asc(), UserInteraction.id.asc())
        )
        int_res = await self.session.execute(interactions_stmt)
        interactions = list(int_res.scalars().all())

        # 5. Fetch explicit user preferences
        prefs_stmt = select(UserPreference).where(UserPreference.user_id == user_id)
        prefs_res = await self.session.execute(prefs_stmt)
        user_preferences = list(prefs_res.scalars().all())

        explicit_prefs: list[ExplicitPreferenceInput] = []
        for p in user_preferences:
            w = float(p.preference_weight) if p.preference_weight is not None else 1.0
            explicit_prefs.append(
                ExplicitPreferenceInput(
                    preference_type=p.preference_type,
                    preference_value=p.preference_value,
                    preference_weight=w,
                    source=p.source,
                )
            )

        # Compute eligible interaction count for watermark
        active_rated_work_ids = {r.work_id for r in ratings}
        active_tbr_work_ids = {ub.work_id for ub in user_books if ub.status == "want_to_read"}
        eligible_count = self._count_eligible_events(
            interactions=interactions,
            active_rated_work_ids=active_rated_work_ids,
            active_tbr_work_ids=active_tbr_work_ids,
        )

        # Resolve signals per work
        resolved_signals, neutral_signals = self._resolve_work_signals(
            ratings=ratings,
            user_books=user_books,
            rec_items=rec_items,
            interactions=interactions,
            weights=weights,
        )

        # Retrieve compatible embeddings
        all_work_ids = [s.work_id for s in resolved_signals]
        embeddings_map, emb_status, effective_rev = await self.get_compatible_embeddings(
            work_ids=all_work_ids,
            model_revision=model_revision,
        )

        return ExtractedProfileSignals(
            user_id=user_id,
            resolved_signals=resolved_signals,
            neutral_signals=neutral_signals,
            explicit_preferences=explicit_prefs,
            eligible_interaction_count=eligible_count,
            work_embeddings=embeddings_map,
            embedding_status=emb_status,
            model_name=self.settings.PROFILE_EMBEDDING_MODEL_NAME,
            model_revision=effective_rev,
            extracted_at=now_utc,
        )

    # --------------------------------------------------------------------------
    # Internal Resolution Logic
    # --------------------------------------------------------------------------

    def _count_eligible_events(
        self,
        interactions: list[UserInteraction],
        active_rated_work_ids: set[uuid.UUID],
        active_tbr_work_ids: set[uuid.UUID],
    ) -> int:
        """Count eligible interaction events with passive throttling and duplicate suppression."""
        count = 0
        last_passive_time: dict[uuid.UUID, datetime] = {}
        seen_rate_works: set[uuid.UUID] = set()
        seen_save_works: set[uuid.UUID] = set()

        for event in interactions:
            # 1. Non-null work_id
            if event.work_id is None:
                continue

            # 2. Supported interaction type
            if event.interaction_type not in APPROVED_INTERACTION_TYPES:
                continue

            event_time = _ensure_utc(event.created_at)

            # 3. Deduplicate historical rate interactions that duplicate active ratings
            if event.interaction_type == "rate":
                if event.work_id in active_rated_work_ids:
                    if event.work_id in seen_rate_works:
                        # Multiple rate events for same active rating: count only the first
                        continue
                    seen_rate_works.add(event.work_id)
                else:
                    # Rating was deleted from authoritative table; ignore orphan event
                    continue

            # 4. Deduplicate historical save interactions for active TBR
            elif event.interaction_type == "save":
                if event.work_id in active_tbr_work_ids:
                    if event.work_id in seen_save_works:
                        continue
                    seen_save_works.add(event.work_id)

            # 5. Passive event throttling (1-hour window per work)
            elif event.interaction_type in ("view", "click", "recommendation_click"):
                last_time = last_passive_time.get(event.work_id)
                if last_time is not None:
                    elapsed = (event_time - last_time).total_seconds()
                    if elapsed < PASSIVE_THROTTLING_WINDOW_SECONDS:
                        continue
                last_passive_time[event.work_id] = event_time

            # 6. Lifecycle events (start, progress, complete, abandon) are distinct eligible events
            # Count accepted event
            count += 1

        return count

    def _resolve_work_signals(
        self,
        ratings: list[BookRating],
        user_books: list[UserBook],
        rec_items: list[RecommendationItem],
        interactions: list[UserInteraction],
        weights: dict[str, float],
    ) -> tuple[list[ResolvedWorkSignal], list[ResolvedWorkSignal]]:
        """Resolve all extracted data into one ResolvedWorkSignal per work plus neutral signals."""
        # Group data by work_id
        ratings_by_work: dict[uuid.UUID, BookRating] = {r.work_id: r for r in ratings}
        user_books_by_work: dict[uuid.UUID, UserBook] = {ub.work_id: ub for ub in user_books}

        rec_items_by_work: dict[uuid.UUID, list[RecommendationItem]] = {}
        for item in rec_items:
            rec_items_by_work.setdefault(item.work_id, []).append(item)

        interactions_by_work: dict[uuid.UUID, list[UserInteraction]] = {}
        for event in interactions:
            if event.work_id is not None and event.interaction_type in APPROVED_INTERACTION_TYPES:
                interactions_by_work.setdefault(event.work_id, []).append(event)

        all_work_ids = (
            set(ratings_by_work.keys())
            | set(user_books_by_work.keys())
            | set(rec_items_by_work.keys())
            | set(interactions_by_work.keys())
        )

        resolved_signals: list[ResolvedWorkSignal] = []
        neutral_signals: list[ResolvedWorkSignal] = []

        for work_id in sorted(all_work_ids, key=lambda w: str(w)):
            res_sig, neu_sig = self._resolve_single_work(
                work_id=work_id,
                rating=ratings_by_work.get(work_id),
                user_book=user_books_by_work.get(work_id),
                rec_items=rec_items_by_work.get(work_id, []),
                interactions=interactions_by_work.get(work_id, []),
                weights=weights,
            )
            if res_sig is not None:
                resolved_signals.append(res_sig)
            if neu_sig is not None:
                neutral_signals.append(neu_sig)

        return resolved_signals, neutral_signals

    def _resolve_single_work(
        self,
        work_id: uuid.UUID,
        rating: BookRating | None,
        user_book: UserBook | None,
        rec_items: list[RecommendationItem],
        interactions: list[UserInteraction],
        weights: dict[str, float],
    ) -> tuple[ResolvedWorkSignal | None, ResolvedWorkSignal | None]:
        """Resolve a single work's signals according to the approved priority hierarchy."""
        # 1. Evaluate Rating Candidate
        rating_candidate: ResolvedWorkSignal | None = None
        is_neutral_rating = False
        if rating is not None:
            r_val = rating.rating
            r_weight = weights.get(f"rate_{r_val}", 0.0)
            r_time = _ensure_utc(rating.updated_at or rating.created_at)
            r_polarity: Literal["positive", "negative", "neutral"] = (
                "neutral" if r_val == 3 else ("positive" if r_weight > 0 else "negative")
            )
            is_neutral_rating = (r_val == 3)
            rating_candidate = ResolvedWorkSignal(
                work_id=work_id,
                effective_weight=r_weight,
                polarity=r_polarity,
                effective_source="rating",
                signal_timestamp=r_time,
                contributing_interaction_ids=[rating.id],
                metadata={"rating_value": r_val, "is_spoiler": rating.is_spoiler},
            )

        # 2. Evaluate Lifecycle Candidate
        lifecycle_candidate: ResolvedWorkSignal | None = None
        lifecycle_events = [e for e in interactions if e.interaction_type in ("start", "progress", "complete", "abandon")]

        if user_book is not None and user_book.status != "want_to_read":
            # Active lifecycle status on user_book
            ub_time = _ensure_utc(user_book.updated_at or user_book.created_at)
            progress = user_book.progress

            if user_book.status == "completed":
                # Terminal completion dominance
                comp_time = ub_time
                if progress and progress.completed_at:
                    comp_time = _ensure_utc(datetime.combine(progress.completed_at, time.min))
                lifecycle_candidate = ResolvedWorkSignal(
                    work_id=work_id,
                    effective_weight=weights["complete"],
                    polarity="positive",
                    effective_source="lifecycle",
                    signal_timestamp=comp_time,
                    contributing_interaction_ids=[user_book.id] + [e.id for e in lifecycle_events],
                    metadata={"lifecycle_status": "completed"},
                )
            elif user_book.status in ("currently_reading", "paused"):
                # Active in-progress
                last_read = ub_time
                if progress and progress.last_read_at:
                    last_read = _ensure_utc(progress.last_read_at)
                lifecycle_candidate = ResolvedWorkSignal(
                    work_id=work_id,
                    effective_weight=weights["start"],
                    polarity="positive",
                    effective_source="lifecycle",
                    signal_timestamp=last_read,
                    contributing_interaction_ids=[user_book.id] + [e.id for e in lifecycle_events],
                    metadata={"lifecycle_status": user_book.status},
                )
            elif user_book.status == "abandoned":
                # Abandonment: check if superseded by a later restart interaction
                latest_restart = max(
                    (e for e in lifecycle_events if e.interaction_type in ("start", "progress", "complete")),
                    key=lambda e: _ensure_utc(e.created_at),
                    default=None,
                )
                if latest_restart is not None and _ensure_utc(latest_restart.created_at) > ub_time:
                    # Restarted after abandonment
                    w_key = "complete" if latest_restart.interaction_type == "complete" else "start"
                    lifecycle_candidate = ResolvedWorkSignal(
                        work_id=work_id,
                        effective_weight=weights[w_key],
                        polarity="positive",
                        effective_source="lifecycle",
                        signal_timestamp=_ensure_utc(latest_restart.created_at),
                        contributing_interaction_ids=[latest_restart.id],
                        metadata={"lifecycle_status": "restarted"},
                    )
                else:
                    lifecycle_candidate = ResolvedWorkSignal(
                        work_id=work_id,
                        effective_weight=weights["abandon"],
                        polarity="negative",
                        effective_source="lifecycle",
                        signal_timestamp=ub_time,
                        contributing_interaction_ids=[user_book.id],
                        metadata={"lifecycle_status": "abandoned"},
                    )

        elif lifecycle_events:
            # Lifecycle derived purely from historical events
            sorted_events = sorted(lifecycle_events, key=lambda e: (_ensure_utc(e.created_at), str(e.id)))
            latest_event = sorted_events[-1]
            latest_time = _ensure_utc(latest_event.created_at)

            # Check if completed
            completed_event = next((e for e in reversed(sorted_events) if e.interaction_type == "complete"), None)
            if completed_event is not None:
                lifecycle_candidate = ResolvedWorkSignal(
                    work_id=work_id,
                    effective_weight=weights["complete"],
                    polarity="positive",
                    effective_source="lifecycle",
                    signal_timestamp=_ensure_utc(completed_event.created_at),
                    contributing_interaction_ids=[e.id for e in lifecycle_events],
                    metadata={"lifecycle_status": "completed"},
                )
            elif latest_event.interaction_type == "abandon":
                # Check for subsequent restart
                restart_after = next(
                    (e for e in sorted_events if _ensure_utc(e.created_at) > latest_time and e.interaction_type in ("start", "progress")),
                    None,
                )
                if restart_after is not None:
                    lifecycle_candidate = ResolvedWorkSignal(
                        work_id=work_id,
                        effective_weight=weights["start"],
                        polarity="positive",
                        effective_source="lifecycle",
                        signal_timestamp=_ensure_utc(restart_after.created_at),
                        contributing_interaction_ids=[e.id for e in lifecycle_events],
                        metadata={"lifecycle_status": "restarted"},
                    )
                else:
                    lifecycle_candidate = ResolvedWorkSignal(
                        work_id=work_id,
                        effective_weight=weights["abandon"],
                        polarity="negative",
                        effective_source="lifecycle",
                        signal_timestamp=latest_time,
                        contributing_interaction_ids=[e.id for e in lifecycle_events],
                        metadata={"lifecycle_status": "abandoned"},
                    )
            else:
                # start or progress
                w_key = "start" if latest_event.interaction_type == "start" else "progress"
                lifecycle_candidate = ResolvedWorkSignal(
                    work_id=work_id,
                    effective_weight=weights[w_key],
                    polarity="positive",
                    effective_source="lifecycle",
                    signal_timestamp=latest_time,
                    contributing_interaction_ids=[e.id for e in lifecycle_events],
                    metadata={"lifecycle_status": latest_event.interaction_type},
                )

        # 3. Evaluate Recommendation Feedback Candidate
        rec_feedback_candidate: ResolvedWorkSignal | None = None
        if rec_items:
            # Sort by feedback_at descending
            latest_rec = max(
                rec_items,
                key=lambda item: (_ensure_utc(item.feedback_at or item.created_at), str(item.id)),
            )
            fb_type = latest_rec.feedback_type
            fb_time = _ensure_utc(latest_rec.feedback_at or latest_rec.created_at)

            if fb_type == "helpful":
                rec_feedback_candidate = ResolvedWorkSignal(
                    work_id=work_id,
                    effective_weight=weights["recommendation_helpful"],
                    polarity="positive",
                    effective_source="recommendation_feedback",
                    signal_timestamp=fb_time,
                    contributing_interaction_ids=[latest_rec.id],
                    metadata={"feedback_type": "helpful"},
                )
            elif fb_type == "not_helpful":
                rec_feedback_candidate = ResolvedWorkSignal(
                    work_id=work_id,
                    effective_weight=weights["recommendation_not_helpful"],
                    polarity="negative",
                    effective_source="recommendation_feedback",
                    signal_timestamp=fb_time,
                    contributing_interaction_ids=[latest_rec.id],
                    metadata={"feedback_type": "not_helpful"},
                )
            elif fb_type == "save":
                rec_feedback_candidate = ResolvedWorkSignal(
                    work_id=work_id,
                    effective_weight=weights["save"],
                    polarity="positive",
                    effective_source="save",
                    signal_timestamp=fb_time,
                    contributing_interaction_ids=[latest_rec.id],
                    metadata={"feedback_type": "save"},
                )
            elif fb_type == "dismiss":
                rec_feedback_candidate = ResolvedWorkSignal(
                    work_id=work_id,
                    effective_weight=weights["dismiss"],
                    polarity="negative",
                    effective_source="dismissal",
                    signal_timestamp=fb_time,
                    contributing_interaction_ids=[latest_rec.id],
                    metadata={"feedback_type": "dismiss"},
                )

        # 4. Evaluate Library Save (TBR) Candidate
        save_candidate: ResolvedWorkSignal | None = None
        if user_book is not None and user_book.status == "want_to_read":
            save_time = _ensure_utc(user_book.updated_at or user_book.created_at)
            save_candidate = ResolvedWorkSignal(
                work_id=work_id,
                effective_weight=weights["save"],
                polarity="positive",
                effective_source="save",
                signal_timestamp=save_time,
                contributing_interaction_ids=[user_book.id],
                metadata={"library_status": "want_to_read"},
            )
        elif rec_feedback_candidate is not None and rec_feedback_candidate.effective_source == "save":
            save_candidate = rec_feedback_candidate
            rec_feedback_candidate = None

        # 5. Evaluate Dismissal Candidate
        dismiss_candidate: ResolvedWorkSignal | None = None
        dismiss_events = [e for e in interactions if e.interaction_type == "dismiss"]
        if dismiss_events:
            latest_dismiss = max(dismiss_events, key=lambda e: (_ensure_utc(e.created_at), str(e.id)))
            dismiss_candidate = ResolvedWorkSignal(
                work_id=work_id,
                effective_weight=weights["dismiss"],
                polarity="negative",
                effective_source="dismissal",
                signal_timestamp=_ensure_utc(latest_dismiss.created_at),
                contributing_interaction_ids=[e.id for e in dismiss_events],
            )
        elif rec_feedback_candidate is not None and rec_feedback_candidate.effective_source == "dismissal":
            dismiss_candidate = rec_feedback_candidate
            rec_feedback_candidate = None

        # Check dismissal supersession by later explicit positive action
        if dismiss_candidate is not None:
            # Positive actions: save, lifecycle start/progress/complete, rating >= 4
            candidate_times: list[datetime] = []
            if save_candidate is not None:
                candidate_times.append(save_candidate.signal_timestamp)
            if lifecycle_candidate is not None and lifecycle_candidate.polarity == "positive":
                candidate_times.append(lifecycle_candidate.signal_timestamp)
            if rating_candidate is not None and rating_candidate.polarity == "positive":
                candidate_times.append(rating_candidate.signal_timestamp)

            if any(t > dismiss_candidate.signal_timestamp for t in candidate_times):
                # Dismissal was superseded by a later positive action
                dismiss_candidate = None

        # 6. Evaluate Passive Browsing Candidate
        passive_candidate: ResolvedWorkSignal | None = None
        passive_events = [e for e in interactions if e.interaction_type in ("recommendation_click", "click", "view")]
        if passive_events:
            # Select strongest passive signal
            strongest_event = max(
                passive_events,
                key=lambda e: (weights.get(e.interaction_type, 0.0), _ensure_utc(e.created_at), str(e.id)),
            )
            p_weight = weights.get(strongest_event.interaction_type, 0.05)
            p_time = _ensure_utc(strongest_event.created_at)
            passive_candidate = ResolvedWorkSignal(
                work_id=work_id,
                effective_weight=p_weight,
                polarity="positive",
                effective_source="passive",
                signal_timestamp=p_time,
                contributing_interaction_ids=[e.id for e in passive_events],
                metadata={"interaction_type": strongest_event.interaction_type},
            )

        # ----------------------------------------------------------------------
        # Apply Priority Hierarchy:
        # Rating > Lifecycle > Rec Feedback > Save (TBR) > Dismissal > Passive
        # With Neutral Rating Exception
        # ----------------------------------------------------------------------
        resolved_primary: ResolvedWorkSignal | None = None
        neutral_sig: ResolvedWorkSignal | None = None

        if rating_candidate is not None:
            if not is_neutral_rating:
                # Non-neutral rating strictly takes precedence over all lower families
                resolved_primary = rating_candidate
            else:
                # Neutral 3-star rating: preserve as neutral signal
                neutral_sig = rating_candidate
                # Exception: it must NOT suppress a non-neutral lifecycle or recommendation or save signal
                if lifecycle_candidate is not None and lifecycle_candidate.polarity != "neutral":
                    resolved_primary = lifecycle_candidate
                elif rec_feedback_candidate is not None and rec_feedback_candidate.polarity != "neutral":
                    resolved_primary = rec_feedback_candidate
                elif save_candidate is not None:
                    resolved_primary = save_candidate
                elif dismiss_candidate is not None:
                    resolved_primary = dismiss_candidate
                else:
                    # No non-neutral explicit signal from lower families: 3-star rating is primary
                    resolved_primary = rating_candidate

        if resolved_primary is None:
            if lifecycle_candidate is not None:
                resolved_primary = lifecycle_candidate
            elif rec_feedback_candidate is not None:
                resolved_primary = rec_feedback_candidate
            elif save_candidate is not None:
                resolved_primary = save_candidate
            elif dismiss_candidate is not None:
                resolved_primary = dismiss_candidate
            elif passive_candidate is not None:
                resolved_primary = passive_candidate

        return resolved_primary, neutral_sig
