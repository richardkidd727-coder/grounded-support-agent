import math

import pytest

from support_agent.config import Settings
from support_agent.domain import Tier, allowed_audiences
from support_agent.rag.embeddings import HashingEmbedder, to_pgvector
from support_agent.rag.evaluation import (
    CaseResult,
    EvalCase,
    choose_threshold,
    gate_bands,
    summarise,
    sweep_thresholds,
)
from support_agent.rag.gate import gate_band
from support_agent.rag.retriever import RetrievedChunk, fuse, reciprocal_rank_fusion
from support_agent.rag.store import CandidateRow


def row(cid: str, sim: float, v: int | None, k: int | None) -> CandidateRow:
    return CandidateRow(cid, "doc", "T", "T > S", "c", ("all",), sim, v, k, None)


def test_rrf_rewards_agreement_between_retrievers() -> None:
    both = reciprocal_rank_fusion([2, 2])
    vector_only = reciprocal_rank_fusion([1, None])
    assert both > vector_only
    assert reciprocal_rank_fusion([None, None]) == 0


def test_fuse_orders_by_rrf_then_similarity_and_truncates() -> None:
    fused = fuse(
        [
            row("a", 0.9, 1, None),
            row("b", 0.5, 2, 1),
            row("c", 0.6, 1, None),
            row("d", 0.1, 30, None),
        ],
        top_k=3,
    )
    assert [c.chunk_id for c in fused] == ["b", "a", "c"]  # a/c tie on RRF; a has higher sim


def test_allowed_audiences() -> None:
    assert allowed_audiences(None) == ["all"]
    assert allowed_audiences(Tier.PRO) == ["all", "pro"]


def test_gate_band_boundaries() -> None:
    s = Settings(_env_file=None, retrieval_reject_below=0.5, retrieval_accept_above=0.7)  # type: ignore[call-arg]
    assert gate_band(0.49, s) == "reject"
    assert gate_band(0.50, s) == "uncertain"
    assert gate_band(0.69, s) == "uncertain"
    assert gate_band(0.70, s) == "confident"


def test_gate_bands_keep_every_observed_case_on_the_correct_side() -> None:
    bands = gate_bands(in_scope=[0.62, 0.75, 0.9], out_of_scope=[0.3, 0.55, 0.68])
    assert bands.reject_below == 0.62
    assert bands.accept_above == 0.69
    assert bands.in_scope_to_judge == pytest.approx(1 / 3)
    assert bands.out_of_scope_rejected_outright == pytest.approx(2 / 3)


def test_threshold_sweep_picks_middle_of_best_plateau() -> None:
    sweep = sweep_thresholds(in_scope=[0.8, 0.9], out_of_scope=[0.4, 0.5])
    choice = choose_threshold(sweep)
    assert choice.balanced_accuracy == 1.0
    assert 0.5 < choice.threshold <= 0.8
    assert math.isclose(choice.threshold, 0.65, abs_tol=0.02)  # middle of the 0.51-0.80 plateau


def test_summary_metrics() -> None:
    def res(case_id: str, docs: list[str], rank: int | None, sim: float) -> CaseResult:
        return CaseResult(EvalCase(id=case_id, query="q", docs=docs), rank, sim, "-")

    s = summarise(
        [
            res("a", ["d"], 1, 0.8),
            res("b", ["d"], 3, 0.7),
            res("c", ["d"], None, 0.6),
            res("o", [], None, 0.4),
        ]
    )
    assert s.hit_at == {1: pytest.approx(1 / 3), 3: pytest.approx(2 / 3), 5: pytest.approx(2 / 3)}
    assert s.mrr == pytest.approx((1 + 1 / 3) / 3)


def test_eval_case_section_matching() -> None:
    case = EvalCase(id="x", query="q", docs=["refund-policy"], section="Monthly Pro")
    chunk = RetrievedChunk(
        "refund-policy:003",
        "refund-policy",
        "Refund Policy",
        "Refund Policy > Pro plan > Monthly Pro subscriptions",
        "c",
        ("pro",),
        0.8,
        0.03,
        1,
        1,
    )
    assert case.matches(chunk)
    assert not EvalCase(id="y", query="q", docs=["other"]).matches(chunk)


async def test_hashing_embedder_is_normalised_and_lexical() -> None:
    e = HashingEmbedder(64)
    a, b, c = await e.embed_documents(["refund my plan", "refund my plan please", "sync error"])
    dot = lambda x, y: sum(i * j for i, j in zip(x, y, strict=True))  # noqa: E731
    assert dot(a, a) == pytest.approx(1.0)
    assert dot(a, b) > dot(a, c)


def test_pgvector_literal() -> None:
    assert to_pgvector([0.5, -1.0]) == "[0.5000000,-1.0000000]"
