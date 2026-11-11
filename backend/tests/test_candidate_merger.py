import logging
import uuid

import pytest

from backend.schemas.retrieval import (
    CandidateBook,
    CandidateEligibilityInfo,
    CandidateWorkMetadata,
)
from backend.services.retrieval import (
    CandidateMerger,
    CandidateMergerProtocol,
    MetadataCandidateHit,
    SemanticCandidateHit,
)


def _meta(title: str = "Book") -> CandidateWorkMetadata:
    return CandidateWorkMetadata(canonical_title=title)


class TestCandidateMerger:
    """Unit tests for deterministic multi-channel CandidateMerger (Milestone 5.2.4)."""

    def test_implements_candidate_merger_protocol(self):
        merger = CandidateMerger()
        assert isinstance(merger, CandidateMergerProtocol)

    def test_empty_input_batches(self):
        merger = CandidateMerger()
        assert merger.merge([], [], {}) == ()

    def test_semantic_only_and_metadata_only_candidates(self):
        merger = CandidateMerger()
        w_sem = uuid.UUID("00000000-0000-0000-0000-000000000001")
        w_meta = uuid.UUID("00000000-0000-0000-0000-000000000002")
        g1 = uuid.uuid4()

        merged = merger.merge(
            semantic_hits=[SemanticCandidateHit(work_id=w_sem, semantic_distance=0.18)],
            metadata_hits=[
                MetadataCandidateHit(
                    work_id=w_meta,
                    retrieval_sources=("explicit_genre",),
                    matched_genres=(g1,),
                )
            ],
            work_metadata={w_sem: _meta("Sem"), w_meta: _meta("Meta")},
        )

        assert len(merged) == 2
        assert all(isinstance(c, CandidateBook) for c in merged)

        c_sem, c_meta = merged[0], merged[1]
        assert c_sem.work_id == w_sem
        assert c_sem.retrieval_sources == ("semantic",)
        assert c_sem.semantic_distance == 0.18

        # Metadata-only candidate never fabricates semantic_distance
        assert c_meta.work_id == w_meta
        assert c_meta.retrieval_sources == ("explicit_genre",)
        assert c_meta.semantic_distance is None
        assert c_meta.matched_genres == (g1,)

    def test_same_work_in_both_sources_and_multiple_metadata_sources_unioned(self):
        merger = CandidateMerger()
        wid = uuid.uuid4()
        g1, g2 = uuid.uuid4(), uuid.uuid4()
        s1 = uuid.uuid4()
        a1 = uuid.uuid4()

        merged = merger.merge(
            semantic_hits=[SemanticCandidateHit(work_id=wid, semantic_distance=0.11)],
            metadata_hits=[
                MetadataCandidateHit(
                    work_id=wid,
                    retrieval_sources=("explicit_genre", "explicit_author"),
                    matched_genres=(g1,),
                    matched_authors=(a1,),
                ),
                MetadataCandidateHit(
                    work_id=wid,
                    retrieval_sources=("inferred_genre", "inferred_subject"),
                    matched_genres=(g2, g1),
                    matched_subjects=(s1,),
                ),
            ],
            work_metadata={wid: _meta("Multi")},
        )

        assert len(merged) == 1
        cand = merged[0]
        assert cand.work_id == wid
        assert cand.semantic_distance == 0.11
        assert cand.retrieval_sources == (
            "explicit_author",
            "explicit_genre",
            "inferred_genre",
            "inferred_subject",
            "semantic",
        )
        assert cand.matched_genres == tuple(sorted([g1, g2], key=str))
        assert cand.matched_subjects == (s1,)
        assert cand.matched_authors == (a1,)

    def test_duplicate_semantic_hits_keep_deterministic_minimum_and_warn_on_discrepancy(self, caplog):
        merger = CandidateMerger()
        wid = uuid.uuid4()

        # Identical duplicates -> no warning
        with caplog.at_level(logging.WARNING):
            merged_same = merger.merge(
                semantic_hits=[
                    SemanticCandidateHit(work_id=wid, semantic_distance=0.25),
                    SemanticCandidateHit(work_id=wid, semantic_distance=0.25),
                ],
                metadata_hits=[],
                work_metadata={wid: _meta("Dup Same")},
            )
        assert len(merged_same) == 1
        assert merged_same[0].semantic_distance == 0.25
        assert "Inconsistent duplicate semantic_distance" not in caplog.text

        # Conflicting duplicates -> deterministic min and warning logged
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            merged_diff = merger.merge(
                semantic_hits=[
                    SemanticCandidateHit(work_id=wid, semantic_distance=0.45),
                    SemanticCandidateHit(work_id=wid, semantic_distance=0.15),
                ],
                metadata_hits=[],
                work_metadata={wid: _meta("Dup Diff")},
            )
        assert len(merged_diff) == 1
        assert merged_diff[0].semantic_distance == 0.15
        assert "Inconsistent duplicate semantic_distance" in caplog.text

    def test_deterministic_canonical_ordering_without_ranking_or_source_priority(self):
        merger = CandidateMerger()
        w_low = uuid.UUID("00000000-0000-0000-0000-000000000001")
        w_mid = uuid.UUID("00000000-0000-0000-0000-000000000002")
        w_high = uuid.UUID("00000000-0000-0000-0000-000000000003")

        g1, s1 = uuid.uuid4(), uuid.uuid4()
        sem_hits = [
            SemanticCandidateHit(work_id=w_high, semantic_distance=0.01),
            SemanticCandidateHit(work_id=w_low, semantic_distance=0.95),
        ]
        meta_hits = [
            MetadataCandidateHit(
                work_id=w_mid,
                retrieval_sources=("explicit_genre", "inferred_subject"),
                matched_genres=(g1,),
                matched_subjects=(s1,),
            ),
            MetadataCandidateHit(
                work_id=w_high,
                retrieval_sources=("explicit_genre",),
                matched_genres=(g1,),
            ),
        ]
        work_meta = {w_low: _meta("L"), w_mid: _meta("M"), w_high: _meta("H")}

        res_forward = merger.merge(sem_hits, meta_hits, work_meta)
        res_reversed = merger.merge(list(reversed(sem_hits)), list(reversed(meta_hits)), work_meta)

        assert res_forward == res_reversed
        # Strictly ordered by work_id string, NOT by source count or semantic distance
        assert [c.work_id for c in res_forward] == [w_low, w_mid, w_high]

    def test_missing_work_metadata_raises_value_error(self):
        merger = CandidateMerger()
        wid = uuid.uuid4()
        with pytest.raises(ValueError, match="Missing CandidateWorkMetadata"):
            merger.merge(
                semantic_hits=[SemanticCandidateHit(work_id=wid, semantic_distance=0.1)],
                metadata_hits=[],
                work_metadata={},
            )

    def test_custom_default_eligibility_applied(self):
        merger = CandidateMerger()
        wid = uuid.uuid4()
        custom_elig = CandidateEligibilityInfo(
            is_eligible=False,
            exclusion_reasons=("pending_filter",),
        )
        merged = merger.merge(
            semantic_hits=[SemanticCandidateHit(work_id=wid, semantic_distance=0.2)],
            metadata_hits=[],
            work_metadata={wid: _meta("Pending")},
            default_eligibility=custom_elig,
        )
        assert merged[0].eligibility == custom_elig
