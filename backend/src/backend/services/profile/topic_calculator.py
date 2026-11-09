from __future__ import annotations

import math
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import Settings, settings as default_settings
from backend.db.models.genre import BookWorkGenre
from backend.db.models.subject import BookWorkSubject
from backend.schemas.profile import (
    InferredTopicsPayload,
    ResolvedWorkSignal,
    TopicAffinityEntry,
)
from backend.services.profile.signal_extractor import ExtractedProfileSignals
from backend.services.profile.vector_builder import calculate_recency_decay


def _ensure_utc(dt: datetime) -> datetime:
    """Ensure datetime is timezone-aware and normalized to UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _categorize_negative_signals(
    signals: Sequence[ResolvedWorkSignal],
    max_per_category: int = 50,
    max_combined: int = 100,
) -> dict[str, list[str]]:
    """Group, deduplicate, and bound negative work signals by category.

    Categories:
        - dismissed_work_ids: explicit work dismissals
        - abandoned_work_ids: abandoned reading lifecycle events
        - low_rated_work_ids: 1-star and 2-star ratings
        - not_helpful_work_ids: unhelpful recommendation feedback
        - all_negative_work_ids: unified deduplicated negative work IDs

    Rules:
        - Max 50 distinct work IDs per category.
        - Max 100 distinct work IDs in the combined list.
        - Sorted by latest interaction timestamp descending, with work ID string tie-break.
        - Negative signals never generate positive affinity and never penalize genres/subjects.
    """
    category_works: dict[str, dict[uuid.UUID, datetime]] = {
        "dismissed_work_ids": {},
        "abandoned_work_ids": {},
        "low_rated_work_ids": {},
        "not_helpful_work_ids": {},
        "all_negative_work_ids": {},
    }

    for sig in signals:
        if sig.polarity != "negative" or sig.effective_weight >= 0:
            continue

        ts = _ensure_utc(sig.signal_timestamp)
        wid = sig.work_id

        # Always record in combined list (keep latest timestamp per work)
        curr_all = category_works["all_negative_work_ids"].get(wid)
        if curr_all is None or ts > curr_all:
            category_works["all_negative_work_ids"][wid] = ts

        # 1. Dismissals
        is_dismissal = (
            sig.effective_source in ("dismiss", "dismissal")
            or (sig.metadata is not None and sig.metadata.get("feedback_type") == "dismiss")
            or (sig.metadata is not None and sig.metadata.get("interaction_type") == "dismiss")
        )
        if is_dismissal:
            curr = category_works["dismissed_work_ids"].get(wid)
            if curr is None or ts > curr:
                category_works["dismissed_work_ids"][wid] = ts

        # 2. Abandonments
        is_abandon = (
            sig.effective_source == "abandon"
            or (
                sig.effective_source == "lifecycle"
                and sig.metadata is not None
                and sig.metadata.get("lifecycle_status") == "abandoned"
            )
            or (
                sig.metadata is not None
                and sig.metadata.get("interaction_type") == "abandon"
            )
        )
        if is_abandon:
            curr = category_works["abandoned_work_ids"].get(wid)
            if curr is None or ts > curr:
                category_works["abandoned_work_ids"][wid] = ts

        # 3. Low ratings (1 or 2 stars)
        is_low_rate = (
            sig.effective_source in ("rate", "rating")
            or (
                sig.metadata is not None
                and sig.metadata.get("rating_value") in (1, 2)
            )
            or (
                sig.metadata is not None
                and sig.metadata.get("interaction_type") == "rate"
            )
        )
        if is_low_rate:
            curr = category_works["low_rated_work_ids"].get(wid)
            if curr is None or ts > curr:
                category_works["low_rated_work_ids"][wid] = ts

        # 4. Not helpful recommendation feedback
        is_not_helpful = (
            sig.effective_source == "recommendation_not_helpful"
            or (
                sig.effective_source == "recommendation_feedback"
                and sig.metadata is not None
                and sig.metadata.get("feedback_type") == "not_helpful"
            )
            or (
                sig.metadata is not None
                and sig.metadata.get("interaction_type") == "recommendation_not_helpful"
            )
        )
        if is_not_helpful:
            curr = category_works["not_helpful_work_ids"].get(wid)
            if curr is None or ts > curr:
                category_works["not_helpful_work_ids"][wid] = ts

    # Sort each category deterministically: timestamp desc, then str(uuid) asc
    result: dict[str, list[str]] = {}
    for cat_name, work_dict in category_works.items():
        limit = max_combined if cat_name == "all_negative_work_ids" else max_per_category
        sorted_works = sorted(
            work_dict.items(),
            key=lambda item: (-item[1].timestamp(), str(item[0])),
        )
        result[cat_name] = [str(item[0]) for item in sorted_works[:limit]]

    return result


def calculate_topic_affinities(
    positive_signals: Sequence[ResolvedWorkSignal],
    work_genres: Mapping[uuid.UUID, Sequence[uuid.UUID]],
    work_subjects: Mapping[uuid.UUID, Sequence[tuple[uuid.UUID, float | Decimal | None] | uuid.UUID]],
    neutral_signals: Sequence[ResolvedWorkSignal] | None = None,
    negative_signals: Sequence[ResolvedWorkSignal] | None = None,
    calculation_time: datetime | None = None,
    half_life_days: float = 180.0,
    min_epsilon: float = 1e-6,
) -> InferredTopicsPayload:
    """Calculate normalized genre and subject affinities from resolved user signals.

    Pure mathematical function without database access or external IO.

    Selection & Aggregation rules:
        1. For each distinct work, select its primary non-neutral positive signal when available.
        2. Otherwise, use its neutral signal (e.g. 3-star rating at +0.10) for topic exposure.
        3. Never count both primary and neutral signals for the same work.
        4. Recency decay is applied exactly once per distinct work using `calculate_recency_decay`.
        5. Genre contribution = decayed_work_weight * 1.0.
        6. Subject contribution = decayed_work_weight * relevance_weight (defaulting to 1.0 if null).
        7. Raw scores are summed across distinct works for each topic.
        8. Affinities are normalized via max-score: affinity = raw_score / max_score in [0.0, 1.0].
        9. If max_score < min_epsilon, all affinities evaluate safely to 0.0.
        10. Negative signals are independently extracted, bounded, and preserved in `negative_signals`.

    Args:
        positive_signals: Sequence of resolved work signals.
        work_genres: Mapping of work UUID to sequence of associated genre UUIDs.
        work_subjects: Mapping of work UUID to sequence of associated subject tuples (UUID, relevance_weight).
        neutral_signals: Optional sequence of neutral work signals set aside during signal extraction.
        negative_signals: Optional sequence of negative work signals.
        calculation_time: Reference timestamp for recency decay (defaults to UTC now).
        half_life_days: Half-life parameter in days (must be positive finite float).
        min_epsilon: Near-zero threshold below which normalized affinity evaluates to 0.0.

    Returns:
        Validated InferredTopicsPayload with populated genres, subjects, negative_signals, and meta.
    """
    if half_life_days <= 0 or not math.isfinite(half_life_days):
        raise ValueError("half_life_days must be a positive finite float")
    if min_epsilon <= 0 or not math.isfinite(min_epsilon):
        raise ValueError("min_epsilon must be a positive finite float")

    calc_time = _ensure_utc(calculation_time or datetime.now(timezone.utc))

    # --------------------------------------------------------------------------
    # 1. Resolve eligible positive/neutral signals per work (deduplicated)
    # --------------------------------------------------------------------------
    eligible_work_signals: dict[uuid.UUID, ResolvedWorkSignal] = {}

    # Step 1A: Primary positive signals strictly take priority
    for sig in positive_signals:
        if sig.polarity == "positive" and sig.effective_weight > 0:
            if sig.work_id not in eligible_work_signals:
                eligible_work_signals[sig.work_id] = sig
            elif sig.effective_weight > eligible_work_signals[sig.work_id].effective_weight:
                # If duplicate positive signals for the same work exist, keep strongest
                eligible_work_signals[sig.work_id] = sig

    # Step 1B: Neutral signals contribute topic exposure only if no positive signal exists for the work
    for sig in positive_signals:
        if sig.polarity == "neutral" and sig.effective_weight > 0:
            if sig.work_id not in eligible_work_signals:
                eligible_work_signals[sig.work_id] = sig

    if neutral_signals:
        for sig in neutral_signals:
            if sig.effective_weight > 0:
                if sig.work_id not in eligible_work_signals:
                    eligible_work_signals[sig.work_id] = sig

    # --------------------------------------------------------------------------
    # 2. Apply recency decay once per distinct eligible work
    # --------------------------------------------------------------------------
    decayed_weights: dict[uuid.UUID, float] = {}
    for wid, sig in eligible_work_signals.items():
        decayed = calculate_recency_decay(
            base_weight=sig.effective_weight,
            signal_timestamp=sig.signal_timestamp,
            calculation_time=calc_time,
            half_life_days=half_life_days,
        )
        decayed_weights[wid] = max(0.0, float(decayed))

    # --------------------------------------------------------------------------
    # 3. Aggregate Genre Affinities
    # --------------------------------------------------------------------------
    raw_genre_scores: dict[uuid.UUID, float] = defaultdict(float)
    genre_contributing_works: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    genre_last_interacted: dict[uuid.UUID, datetime] = {}

    for wid, sig in eligible_work_signals.items():
        dw = decayed_weights[wid]
        if dw <= 0:
            continue

        genres_for_work = work_genres.get(wid, ())
        # Defensive deduplication so a work contributes at most once per genre
        unique_genre_ids = set(genres_for_work)
        sig_ts = _ensure_utc(sig.signal_timestamp)

        for gid in unique_genre_ids:
            raw_genre_scores[gid] += dw
            genre_contributing_works[gid].add(wid)
            curr_ts = genre_last_interacted.get(gid)
            if curr_ts is None or sig_ts > curr_ts:
                genre_last_interacted[gid] = sig_ts

    # --------------------------------------------------------------------------
    # 4. Aggregate Subject Affinities
    # --------------------------------------------------------------------------
    raw_subject_scores: dict[uuid.UUID, float] = defaultdict(float)
    subject_contributing_works: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    subject_last_interacted: dict[uuid.UUID, datetime] = {}

    for wid, sig in eligible_work_signals.items():
        dw = decayed_weights[wid]
        if dw <= 0:
            continue

        subjects_for_work = work_subjects.get(wid, ())

        # Defensive deduplication per subject for this work:
        # If the same subject is mapped multiple times to this work, take the max relevance weight
        best_subject_weights: dict[uuid.UUID, float] = {}
        for item in subjects_for_work:
            if isinstance(item, (tuple, list)) and len(item) >= 2:
                sid, rel = item[0], item[1]
            elif isinstance(item, uuid.UUID):
                sid, rel = item, 1.0
            else:
                continue

            if rel is None:
                r = 1.0
            else:
                if isinstance(rel, bool):
                    raise ValueError(f"Subject relevance weight cannot be a boolean: {rel!r}")
                try:
                    r = float(rel)
                except (TypeError, ValueError) as err:
                    raise ValueError(
                        f"Subject relevance weight could not be converted to float: {rel!r}"
                    ) from err

                if not math.isfinite(r):
                    raise ValueError(
                        f"Subject relevance weight must be a finite number: {rel!r}"
                    )
                if not (0.0 <= r <= 1.0):
                    raise ValueError(
                        f"Subject relevance weight must be between 0.0 and 1.0 inclusive, got {r}"
                    )

            curr_r = best_subject_weights.get(sid)
            if curr_r is None or r > curr_r:
                best_subject_weights[sid] = r

        sig_ts = _ensure_utc(sig.signal_timestamp)
        for sid, r in best_subject_weights.items():
            sub_contrib = dw * r
            raw_subject_scores[sid] += sub_contrib
            subject_contributing_works[sid].add(wid)
            curr_ts = subject_last_interacted.get(sid)
            if curr_ts is None or sig_ts > curr_ts:
                subject_last_interacted[sid] = sig_ts

    # --------------------------------------------------------------------------
    # 5. Max-score Normalization to [0.0, 1.0]
    # --------------------------------------------------------------------------
    # Genres normalization
    inferred_genres: dict[str, TopicAffinityEntry] = {}
    max_genre_score = max(raw_genre_scores.values()) if raw_genre_scores else 0.0

    sorted_genre_ids = sorted(
        raw_genre_scores.keys(),
        key=lambda gid: (-raw_genre_scores[gid], -len(genre_contributing_works[gid]), str(gid)),
    )

    for gid in sorted_genre_ids:
        raw_score = raw_genre_scores[gid]
        if max_genre_score < min_epsilon:
            affinity = 0.0
        else:
            affinity = min(1.0, max(0.0, raw_score / max_genre_score))

        inferred_genres[str(gid)] = TopicAffinityEntry(
            affinity=affinity,
            interaction_count=len(genre_contributing_works[gid]),
            last_interacted_at=genre_last_interacted[gid],
        )

    # Subjects normalization
    inferred_subjects: dict[str, TopicAffinityEntry] = {}
    max_subject_score = max(raw_subject_scores.values()) if raw_subject_scores else 0.0

    sorted_subject_ids = sorted(
        raw_subject_scores.keys(),
        key=lambda sid: (-raw_subject_scores[sid], -len(subject_contributing_works[sid]), str(sid)),
    )

    for sid in sorted_subject_ids:
        raw_score = raw_subject_scores[sid]
        if max_subject_score < min_epsilon:
            affinity = 0.0
        else:
            affinity = min(1.0, max(0.0, raw_score / max_subject_score))

        inferred_subjects[str(sid)] = TopicAffinityEntry(
            affinity=affinity,
            interaction_count=len(subject_contributing_works[sid]),
            last_interacted_at=subject_last_interacted[sid],
        )

    # --------------------------------------------------------------------------
    # 6. Extract & Categorize Negative Signals
    # --------------------------------------------------------------------------
    all_neg_candidates: list[ResolvedWorkSignal] = [
        s for s in positive_signals if s.polarity == "negative"
    ]
    if negative_signals:
        all_neg_candidates.extend(
            [s for s in negative_signals if s.polarity == "negative"]
        )

    categorized_negatives = _categorize_negative_signals(
        signals=all_neg_candidates,
        max_per_category=50,
        max_combined=100,
    )

    meta: dict[str, Any] = {
        "calculated_at": calc_time.isoformat(),
        "half_life_days": half_life_days,
        "distinct_positive_works": len(eligible_work_signals),
        "distinct_genres_inferred": len(inferred_genres),
        "distinct_subjects_inferred": len(inferred_subjects),
        "max_raw_genre_score": max_genre_score,
        "max_raw_subject_score": max_subject_score,
    }

    return InferredTopicsPayload(
        genres=inferred_genres,
        subjects=inferred_subjects,
        negative_signals=categorized_negatives,
        meta=meta,
    )


class ProfileTopicCalculator:
    """Orchestrates database retrieval of catalogue associations and topic affinity calculation."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or default_settings

    async def calculate(
        self,
        extracted_signals: ExtractedProfileSignals,
        calculation_time: datetime | None = None,
    ) -> InferredTopicsPayload:
        """Retrieve catalogue genre/subject mappings in two batched queries and calculate affinities.

        Args:
            extracted_signals: Output from Milestone 5.1.3 ProfileSignalExtractor.
            calculation_time: Optional reference timestamp (defaults to extracted_signals.extracted_at).

        Returns:
            Validated InferredTopicsPayload.
        """
        calc_time = calculation_time or extracted_signals.extracted_at

        # Identify all candidate works that could generate positive topic exposure
        candidate_work_ids: set[uuid.UUID] = set()
        for sig in extracted_signals.resolved_signals:
            if sig.polarity in ("positive", "neutral") and sig.effective_weight > 0:
                candidate_work_ids.add(sig.work_id)

        for sig in extracted_signals.neutral_signals:
            if sig.effective_weight > 0:
                candidate_work_ids.add(sig.work_id)

        work_id_list = list(candidate_work_ids)

        work_genres: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
        work_subjects: dict[uuid.UUID, list[tuple[uuid.UUID, float | None]]] = defaultdict(list)

        # Execute at most two batched queries only if candidate works exist
        if work_id_list:
            # Query 1: Genres
            stmt_genres = (
                select(BookWorkGenre.work_id, BookWorkGenre.genre_id)
                .where(BookWorkGenre.work_id.in_(work_id_list))
            )
            res_genres = await self.session.execute(stmt_genres)
            for row in res_genres.all():
                work_genres[row.work_id].append(row.genre_id)

            # Query 2: Subjects
            stmt_subjects = (
                select(
                    BookWorkSubject.work_id,
                    BookWorkSubject.subject_id,
                    BookWorkSubject.relevance_weight,
                )
                .where(BookWorkSubject.work_id.in_(work_id_list))
            )
            res_subjects = await self.session.execute(stmt_subjects)
            for row in res_subjects.all():
                rel = float(row.relevance_weight) if row.relevance_weight is not None else None
                work_subjects[row.work_id].append((row.subject_id, rel))

        # Call pure calculation function
        return calculate_topic_affinities(
            positive_signals=extracted_signals.resolved_signals,
            work_genres=work_genres,
            work_subjects=work_subjects,
            neutral_signals=extracted_signals.neutral_signals,
            negative_signals=[
                s for s in extracted_signals.resolved_signals if s.polarity == "negative"
            ],
            calculation_time=calc_time,
            half_life_days=self.settings.PROFILE_RECENCY_HALF_LIFE_DAYS,
            min_epsilon=self.settings.PROFILE_NEAR_ZERO_EPSILON,
        )
