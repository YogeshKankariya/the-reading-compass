from __future__ import annotations

import logging
import math
import re
import unicodedata
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Mapping

from sqlalchemy import case, distinct, func, or_, select
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import Settings, settings as default_settings
from backend.db.models.author import Author, BookWorkAuthor
from backend.db.models.book import BookWork
from backend.db.models.genre import BookWorkGenre, Genre
from backend.db.models.recommendation import UserInterestProfile
from backend.db.models.subject import BookWorkSubject, Subject
from backend.db.models.user import UserPreference
from backend.schemas.profile import TopicAffinityEntry
from backend.schemas.retrieval import RetrievalRequestContext, RetrievalSource
from backend.services.retrieval.interfaces import (
    MetadataCandidateHit,
    MetadataRetrievalBatch,
)

logger = logging.getLogger(__name__)


def normalize_author_name(name: str) -> str:
    """Normalize an author name consistently with Author.normalized_name conventions.

    Lowercases, NFKC-normalizes Unicode, replaces punctuation/underscores with spaces,
    and collapses repeated whitespace (e.g., "Robert C. Martin" -> "robert c martin").
    """
    normalized = unicodedata.normalize("NFKC", name).lower()
    cleaned = re.sub(r"[\W_]+", " ", normalized, flags=re.UNICODE)
    return " ".join(cleaned.split())


def _try_parse_uuid(value: str) -> uuid.UUID | None:
    """Attempt strict UUID parsing; return None if not a valid UUID string."""
    try:
        return uuid.UUID(value.strip())
    except (ValueError, AttributeError, TypeError):
        return None


def _to_python_uuid(value: Any) -> uuid.UUID:
    """Convert native or asyncpg pgproto UUID values cleanly to stdlib uuid.UUID."""
    if type(value) is uuid.UUID:
        return value
    return uuid.UUID(str(value))


@dataclass
class _CandidateAccumulator:
    """Internal per-work accumulator for metadata channel hits before DTO construction."""

    work_id: uuid.UUID
    sources: set[RetrievalSource] = field(default_factory=set)
    matched_genres: set[uuid.UUID] = field(default_factory=set)
    matched_subjects: set[uuid.UUID] = field(default_factory=set)
    matched_authors: set[uuid.UUID] = field(default_factory=set)


class MetadataRetriever:
    """Deterministic explicit, inferred-topic, and cold-start candidate retriever (Milestone 5.2.3).

    Conforms to MetadataRetrieverProtocol. Does not perform eligibility filtering,
    TBR/dismissal checks, candidate merging with semantic hits, or ranking/scoring.
    """

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or default_settings

    async def retrieve_for_user(
        self,
        user_id: uuid.UUID,
        context: RetrievalRequestContext | None = None,
        limit: int | None = None,
    ) -> MetadataRetrievalBatch:
        """Load the user's interest profile and execute deterministic metadata retrieval."""
        effective_limit = (
            limit if limit is not None else self.settings.RETRIEVAL_METADATA_INITIAL_LIMIT
        )
        effective_context = context or RetrievalRequestContext(
            user_id=user_id,
            target_count=self.settings.CANDIDATE_TARGET_COUNT,
        )

        stmt = select(UserInterestProfile).where(UserInterestProfile.user_id == user_id)
        try:
            res = await self.session.execute(stmt)
            profile = res.scalar_one_or_none()
        except SQLAlchemyError:
            logger.exception(
                "Database error loading UserInterestProfile for user %s during metadata retrieval",
                user_id,
            )
            raise

        return await self.retrieve(
            user_id=user_id,
            profile=profile,
            context=effective_context,
            limit=effective_limit,
        )

    async def retrieve(
        self,
        user_id: uuid.UUID,
        profile: UserInterestProfile | None,
        context: RetrievalRequestContext,
        limit: int,
    ) -> MetadataRetrievalBatch:
        """Retrieve deterministic candidate works via explicit preferences, inferred topics, or cold-start baseline."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError(f"Metadata retrieval limit must be a positive integer >= 1, got {limit!r}")
        if context.user_id != user_id:
            raise ValueError(
                f"context.user_id ({context.user_id}) does not match user_id ({user_id})"
            )

        effective_limit = min(limit, self.settings.RETRIEVAL_MAX_EXPANSION_LIMIT)

        # 1. Extract & validate inferred topic affinities (strictly fail on malformed data)
        inferred_genres, inferred_subjects = self._extract_inferred_topics(profile)

        # 2. Load & resolve user's explicit preferences
        (
            explicit_genre_weights,
            explicit_subject_weights,
            explicit_author_weights,
        ) = await self._resolve_explicit_preferences(user_id)

        has_usable_preferences = bool(
            explicit_genre_weights
            or explicit_subject_weights
            or explicit_author_weights
            or inferred_genres
            or inferred_subjects
        )

        # 3. Cold-start baseline when no usable explicit or inferred preferences exist
        if not has_usable_preferences:
            baseline_ids = await self._query_cold_start_baseline(effective_limit)
            baseline_hits = tuple(
                MetadataCandidateHit(
                    work_id=wid,
                    retrieval_sources=("cold_start_baseline",),
                )
                for wid in baseline_ids
            )
            return MetadataRetrievalBatch(
                hits=baseline_hits,
                used_cold_start_baseline=True,
            )

        # 4. Query active channels independently in fixed order and preserve ordered queues
        accumulators: dict[uuid.UUID, _CandidateAccumulator] = {}
        channel_queues: list[deque[uuid.UUID]] = []

        if explicit_genre_weights:
            rows = await self._query_genre_candidates(explicit_genre_weights, effective_limit)
            channel_queues.append(
                self._accumulate_channel(
                    accumulators=accumulators,
                    rows=rows,
                    source="explicit_genre",
                    target_attr="matched_genres",
                )
            )

        if explicit_subject_weights:
            rows = await self._query_subject_candidates(explicit_subject_weights, effective_limit)
            channel_queues.append(
                self._accumulate_channel(
                    accumulators=accumulators,
                    rows=rows,
                    source="explicit_subject",
                    target_attr="matched_subjects",
                )
            )

        if explicit_author_weights:
            rows = await self._query_author_candidates(explicit_author_weights, effective_limit)
            channel_queues.append(
                self._accumulate_channel(
                    accumulators=accumulators,
                    rows=rows,
                    source="explicit_author",
                    target_attr="matched_authors",
                )
            )

        if inferred_genres:
            rows = await self._query_genre_candidates(inferred_genres, effective_limit)
            channel_queues.append(
                self._accumulate_channel(
                    accumulators=accumulators,
                    rows=rows,
                    source="inferred_genre",
                    target_attr="matched_genres",
                )
            )

        if inferred_subjects:
            rows = await self._query_subject_candidates(inferred_subjects, effective_limit)
            channel_queues.append(
                self._accumulate_channel(
                    accumulators=accumulators,
                    rows=rows,
                    source="inferred_subject",
                    target_attr="matched_subjects",
                )
            )

        # 5. Deterministic channel-balanced round-robin selection up to effective_limit
        selected_work_ids = self._round_robin_select(
            channel_queues=channel_queues,
            limit=effective_limit,
        )

        hits = tuple(
            MetadataCandidateHit(
                work_id=wid,
                retrieval_sources=tuple(sorted(accumulators[wid].sources)),  # type: ignore[arg-type]
                matched_genres=tuple(sorted(accumulators[wid].matched_genres, key=str)),
                matched_subjects=tuple(sorted(accumulators[wid].matched_subjects, key=str)),
                matched_authors=tuple(sorted(accumulators[wid].matched_authors, key=str)),
            )
            for wid in selected_work_ids
        )

        return MetadataRetrievalBatch(
            hits=hits,
            used_cold_start_baseline=False,
        )

    # --------------------------------------------------------------------------
    # Explicit Preference Resolution
    # --------------------------------------------------------------------------

    async def _resolve_explicit_preferences(
        self,
        user_id: uuid.UUID,
    ) -> tuple[dict[uuid.UUID, float], dict[uuid.UUID, float], dict[uuid.UUID, float]]:
        """Load UserPreference records for user and resolve genre, subject, and author IDs."""
        stmt = select(
            UserPreference.preference_type,
            UserPreference.preference_value,
            UserPreference.preference_weight,
        ).where(UserPreference.user_id == user_id)

        try:
            res = await self.session.execute(stmt)
            pref_rows = res.all()
        except SQLAlchemyError:
            logger.exception(
                "Database error loading UserPreference rows for user %s",
                user_id,
            )
            raise

        genre_items: list[tuple[str, float]] = []
        subject_items: list[tuple[str, float]] = []
        author_items: list[tuple[str, float]] = []

        for row in pref_rows:
            p_type = str(row.preference_type).strip().lower()
            p_val = str(row.preference_value).strip()
            if not p_val:
                continue

            weight = self._validate_preference_weight(row.preference_weight)
            if weight <= 0.0:
                continue

            if p_type == "genre":
                genre_items.append((p_val, weight))
            elif p_type == "subject":
                subject_items.append((p_val, weight))
            elif p_type == "author":
                author_items.append((p_val, weight))

        genre_weights = await self._match_taxonomy_ids(
            model=Genre,
            items=genre_items,
            user_id=user_id,
        )
        subject_weights = await self._match_taxonomy_ids(
            model=Subject,
            items=subject_items,
            user_id=user_id,
        )
        author_weights = await self._match_author_ids(
            items=author_items,
            user_id=user_id,
        )

        return genre_weights, subject_weights, author_weights

    async def _match_taxonomy_ids(
        self,
        model: type[Genre] | type[Subject],
        items: list[tuple[str, float]],
        user_id: uuid.UUID,
    ) -> dict[uuid.UUID, float]:
        """Resolve genre or subject preferences by UUID, exact slug, or case-insensitive name."""
        if not items:
            return {}

        uuid_map: dict[uuid.UUID, float] = {}
        label_weights: dict[str, float] = {}

        for raw_val, weight in items:
            parsed_uuid = _try_parse_uuid(raw_val)
            if parsed_uuid is not None:
                uuid_map[parsed_uuid] = max(uuid_map.get(parsed_uuid, 0.0), weight)
            else:
                norm_label = raw_val.strip().lower()
                label_weights[norm_label] = max(label_weights.get(norm_label, 0.0), weight)

        conditions = []
        if uuid_map:
            conditions.append(model.id.in_(list(uuid_map.keys())))
        if label_weights:
            labels = list(label_weights.keys())
            conditions.append(func.lower(model.slug).in_(labels))
            conditions.append(func.lower(model.name).in_(labels))

        stmt = select(model.id, model.slug, model.name).where(or_(*conditions))
        try:
            res = await self.session.execute(stmt)
            rows = res.all()
        except SQLAlchemyError:
            logger.exception(
                "Database error resolving explicit %s preferences for user %s",
                model.__tablename__,
                user_id,
            )
            raise

        resolved: dict[uuid.UUID, float] = {}
        slug_matches: dict[str, list[uuid.UUID]] = defaultdict(list)
        name_matches: dict[str, list[uuid.UUID]] = defaultdict(list)

        for row in rows:
            tid = _to_python_uuid(row.id)
            if tid in uuid_map:
                resolved[tid] = max(resolved.get(tid, 0.0), uuid_map[tid])
            slug_matches[str(row.slug).lower()].append(tid)
            name_matches[str(row.name).lower()].append(tid)

        for label, weight in label_weights.items():
            matched = slug_matches.get(label) or name_matches.get(label) or []
            if len(matched) == 1:
                tid = matched[0]
                resolved[tid] = max(resolved.get(tid, 0.0), weight)
            elif len(matched) > 1:
                logger.warning(
                    "Ignored ambiguous %s preference for user %s (matched %d rows)",
                    model.__tablename__,
                    user_id,
                    len(matched),
                )

        return resolved

    async def _match_author_ids(
        self,
        items: list[tuple[str, float]],
        user_id: uuid.UUID,
    ) -> dict[uuid.UUID, float]:
        """Resolve explicit author preferences by UUID or exact normalized_name without fuzziness."""
        if not items:
            return {}

        uuid_map: dict[uuid.UUID, float] = {}
        norm_weights: dict[str, float] = {}

        for raw_val, weight in items:
            parsed_uuid = _try_parse_uuid(raw_val)
            if parsed_uuid is not None:
                uuid_map[parsed_uuid] = max(uuid_map.get(parsed_uuid, 0.0), weight)
            else:
                norm_name = normalize_author_name(raw_val)
                if norm_name:
                    norm_weights[norm_name] = max(norm_weights.get(norm_name, 0.0), weight)

        if not uuid_map and not norm_weights:
            return {}

        conditions = []
        if uuid_map:
            conditions.append(Author.id.in_(list(uuid_map.keys())))
        if norm_weights:
            conditions.append(Author.normalized_name.in_(list(norm_weights.keys())))

        stmt = select(Author.id, Author.normalized_name).where(or_(*conditions))
        try:
            res = await self.session.execute(stmt)
            rows = res.all()
        except SQLAlchemyError:
            logger.exception(
                "Database error resolving explicit author preferences for user %s",
                user_id,
            )
            raise

        resolved: dict[uuid.UUID, float] = {}
        authors_by_norm: dict[str, list[uuid.UUID]] = defaultdict(list)

        for row in rows:
            aid = _to_python_uuid(row.id)
            if aid in uuid_map:
                resolved[aid] = max(resolved.get(aid, 0.0), uuid_map[aid])
            authors_by_norm[str(row.normalized_name)].append(aid)

        for norm_name, weight in norm_weights.items():
            matched_authors = authors_by_norm.get(norm_name, [])
            if len(matched_authors) == 1:
                aid = matched_authors[0]
                resolved[aid] = max(resolved.get(aid, 0.0), weight)
            elif len(matched_authors) > 1:
                logger.warning(
                    "Ignored ambiguous author preference for user %s (matched %d authors)",
                    user_id,
                    len(matched_authors),
                )

        return resolved

    # --------------------------------------------------------------------------
    # Inferred Topics Parsing & Validation
    # --------------------------------------------------------------------------

    def _extract_inferred_topics(
        self,
        profile: UserInterestProfile | None,
    ) -> tuple[dict[uuid.UUID, float], dict[uuid.UUID, float]]:
        """Validate and extract top-N inferred genres and subjects above threshold."""
        if profile is None or profile.inferred_topics is None:
            return {}, {}

        payload = profile.inferred_topics
        if not isinstance(payload, Mapping):
            raise ValueError(
                f"Profile inferred_topics must be a mapping, got {type(payload).__name__}"
            )

        raw_genres = payload.get("genres", {})
        raw_subjects = payload.get("subjects", {})

        parsed_genres = self._parse_topic_mapping(raw_genres, "genres")
        parsed_subjects = self._parse_topic_mapping(raw_subjects, "subjects")
        return parsed_genres, parsed_subjects

    def _parse_topic_mapping(
        self,
        raw_map: Any,
        topic_kind: str,
    ) -> dict[uuid.UUID, float]:
        """Strictly validate inferred topic entries, filter by minimum affinity, and slice top-N."""
        if raw_map is None:
            return {}
        if not isinstance(raw_map, Mapping):
            raise ValueError(
                f"inferred_topics['{topic_kind}'] must be a mapping, got {type(raw_map).__name__}"
            )

        min_affinity = self.settings.RETRIEVAL_MIN_INFERRED_TOPIC_AFFINITY
        top_n = self.settings.RETRIEVAL_METADATA_TOPIC_TOP_N

        eligible: list[tuple[uuid.UUID, float]] = []
        for key, entry in raw_map.items():
            try:
                topic_id = uuid.UUID(str(key).strip())
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError(
                    f"Invalid UUID key in inferred_topics['{topic_kind}']: {key!r}"
                ) from exc

            raw_aff: Any
            if isinstance(entry, TopicAffinityEntry):
                raw_aff = entry.affinity
            elif isinstance(entry, Mapping):
                if "affinity" not in entry:
                    raise ValueError(
                        f"Missing 'affinity' field for {topic_kind} entry {topic_id}"
                    )
                raw_aff = entry["affinity"]
            else:
                raise ValueError(
                    f"Invalid entry type for inferred_topics['{topic_kind}'][{topic_id}]: "
                    f"expected mapping, got {type(entry).__name__}"
                )

            if isinstance(raw_aff, bool):
                raise ValueError(
                    f"Affinity for {topic_kind} {topic_id} cannot be a boolean: {raw_aff!r}"
                )
            try:
                affinity = float(raw_aff)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Non-numeric affinity for {topic_kind} {topic_id}: {raw_aff!r}"
                ) from exc

            if not math.isfinite(affinity) or not (0.0 <= affinity <= 1.0):
                raise ValueError(
                    f"Affinity for {topic_kind} {topic_id} must be finite in [0.0, 1.0], got {affinity}"
                )

            if affinity >= min_affinity and affinity > 0.0:
                eligible.append((topic_id, affinity))

        # Sort descending by affinity, tie-break deterministically by topic UUID string ascending
        eligible.sort(key=lambda item: (-item[1], str(item[0])))
        return {tid: aff for tid, aff in eligible[:top_n]}

    @staticmethod
    def _validate_preference_weight(raw_weight: Any) -> float:
        if raw_weight is None:
            return 1.0
        if isinstance(raw_weight, bool):
            raise ValueError(f"preference_weight cannot be boolean: {raw_weight!r}")
        val = float(raw_weight)
        if not math.isfinite(val) or not (0.0 <= val <= 1.0):
            raise ValueError(f"preference_weight must be finite in [0.0, 1.0], got {val}")
        return val

    # --------------------------------------------------------------------------
    # Bounded SQL Channel Queries
    # --------------------------------------------------------------------------

    async def _query_genre_candidates(
        self,
        genre_weights: dict[uuid.UUID, float],
        limit: int,
    ) -> list[tuple[uuid.UUID, list[uuid.UUID], float]]:
        genre_ids = list(genre_weights.keys())
        weight_expr = case(
            *[(BookWorkGenre.genre_id == gid, w) for gid, w in genre_weights.items()],
            else_=0.0,
        )
        score_expr = func.sum(weight_expr)

        stmt = (
            select(
                BookWorkGenre.work_id.label("work_id"),
                func.array_agg(BookWorkGenre.genre_id).label("matched_ids"),
                score_expr.label("match_strength"),
            )
            .where(BookWorkGenre.genre_id.in_(genre_ids))
            .group_by(BookWorkGenre.work_id)
            .order_by(score_expr.desc(), BookWorkGenre.work_id.asc())
            .limit(limit)
        )

        try:
            res = await self.session.execute(stmt)
            return [
                (
                    _to_python_uuid(r.work_id),
                    [_to_python_uuid(gid) for gid in r.matched_ids],
                    float(r.match_strength),
                )
                for r in res.all()
            ]
        except SQLAlchemyError:
            logger.exception("Database error querying BookWorkGenre candidates")
            raise

    async def _query_subject_candidates(
        self,
        subject_weights: dict[uuid.UUID, float],
        limit: int,
    ) -> list[tuple[uuid.UUID, list[uuid.UUID], float]]:
        subject_ids = list(subject_weights.keys())
        weight_expr = case(
            *[(BookWorkSubject.subject_id == sid, w) for sid, w in subject_weights.items()],
            else_=0.0,
        )
        effective_relevance = func.coalesce(BookWorkSubject.relevance_weight, 1.0)
        score_expr = func.sum(weight_expr * effective_relevance)

        stmt = (
            select(
                BookWorkSubject.work_id.label("work_id"),
                func.array_agg(BookWorkSubject.subject_id).label("matched_ids"),
                score_expr.label("match_strength"),
            )
            .where(
                BookWorkSubject.subject_id.in_(subject_ids),
                or_(
                    BookWorkSubject.relevance_weight.is_(None),
                    BookWorkSubject.relevance_weight > 0,
                ),
            )
            .group_by(BookWorkSubject.work_id)
            .order_by(score_expr.desc(), BookWorkSubject.work_id.asc())
            .limit(limit)
        )

        try:
            res = await self.session.execute(stmt)
            return [
                (
                    _to_python_uuid(r.work_id),
                    [_to_python_uuid(sid) for sid in r.matched_ids],
                    float(r.match_strength),
                )
                for r in res.all()
            ]
        except SQLAlchemyError:
            logger.exception("Database error querying BookWorkSubject candidates")
            raise

    async def _query_author_candidates(
        self,
        author_weights: dict[uuid.UUID, float],
        limit: int,
    ) -> list[tuple[uuid.UUID, list[uuid.UUID], float]]:
        author_ids = list(author_weights.keys())
        weight_expr = case(
            *[(BookWorkAuthor.author_id == aid, w) for aid, w in author_weights.items()],
            else_=0.0,
        )
        # An author can hold multiple roles on one work; max avoids double-counting one author
        score_expr = func.max(weight_expr)

        stmt = (
            select(
                BookWorkAuthor.work_id.label("work_id"),
                func.array_agg(
                    aggregate_order_by(
                        distinct(BookWorkAuthor.author_id),
                        BookWorkAuthor.author_id.asc(),
                    )
                ).label("matched_ids"),
                score_expr.label("match_strength"),
            )
            .where(BookWorkAuthor.author_id.in_(author_ids))
            .group_by(BookWorkAuthor.work_id)
            .order_by(score_expr.desc(), BookWorkAuthor.work_id.asc())
            .limit(limit)
        )

        try:
            res = await self.session.execute(stmt)
            return [
                (
                    _to_python_uuid(r.work_id),
                    [_to_python_uuid(aid) for aid in r.matched_ids],
                    float(r.match_strength),
                )
                for r in res.all()
            ]
        except SQLAlchemyError:
            logger.exception("Database error querying BookWorkAuthor candidates")
            raise

    async def _query_cold_start_baseline(self, limit: int) -> list[uuid.UUID]:
        """Deterministic verified-prioritized baseline over BookWork without popularity or randomness."""
        verified_rank = case((BookWork.metadata_status == "verified", 0), else_=1)
        stmt = (
            select(BookWork.id)
            .order_by(verified_rank.asc(), BookWork.created_at.desc(), BookWork.id.asc())
            .limit(limit)
        )

        try:
            res = await self.session.execute(stmt)
            return [_to_python_uuid(wid) for wid in res.scalars().all()]
        except SQLAlchemyError:
            logger.exception("Database error querying cold-start baseline candidates")
            raise

    @staticmethod
    def _accumulate_channel(
        accumulators: dict[uuid.UUID, _CandidateAccumulator],
        rows: list[tuple[uuid.UUID, list[uuid.UUID], float]],
        source: RetrievalSource,
        target_attr: str,
    ) -> deque[uuid.UUID]:
        queue: deque[uuid.UUID] = deque()
        for work_id, matched_ids, _strength in rows:
            acc = accumulators.get(work_id)
            if acc is None:
                acc = _CandidateAccumulator(work_id=work_id)
                accumulators[work_id] = acc

            acc.sources.add(source)
            getattr(acc, target_attr).update(matched_ids)
            queue.append(work_id)
        return queue

    @staticmethod
    def _round_robin_select(
        channel_queues: list[deque[uuid.UUID]],
        limit: int,
    ) -> list[uuid.UUID]:
        """Select up to `limit` unique work_ids deterministically round-robin across channels."""
        selected: list[uuid.UUID] = []
        seen: set[uuid.UUID] = set()

        while len(selected) < limit and any(channel_queues):
            for queue in channel_queues:
                while queue and queue[0] in seen:
                    queue.popleft()
                if queue:
                    wid = queue.popleft()
                    seen.add(wid)
                    selected.append(wid)
                    if len(selected) >= limit:
                        break

        return selected
