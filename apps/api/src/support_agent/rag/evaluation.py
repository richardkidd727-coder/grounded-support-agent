"""Retrieval evaluation: ranking quality + calibration of the confidence (similarity) gate."""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from support_agent.domain import Tier
from support_agent.rag.retriever import HybridRetriever, RetrievedChunk


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    query: str
    tier: Tier | None = None
    docs: list[str] = Field(default_factory=list)
    section: str | None = None

    @property
    def in_scope(self) -> bool:
        return bool(self.docs)

    def matches(self, chunk: RetrievedChunk) -> bool:
        if chunk.doc_id not in self.docs:
            return False
        return self.section is None or self.section.lower() in chunk.section_path.lower()


class EvalSet(BaseModel):
    cases: list[EvalCase]


def load_cases(path: Path) -> list[EvalCase]:
    return EvalSet.model_validate(yaml.safe_load(path.read_text(encoding="utf-8"))).cases


@dataclass(frozen=True, slots=True)
class CaseResult:
    case: EvalCase
    rank: int | None  # 1-based rank of the first matching chunk (in-scope only)
    top_similarity: float
    top_section: str


@dataclass(frozen=True, slots=True)
class ThresholdChoice:
    threshold: float
    in_scope_accepted: float  # recall of the gate on answerable questions
    out_of_scope_rejected: float
    balanced_accuracy: float


@dataclass(frozen=True, slots=True)
class GateBands:
    """Three-band gate. Similarity alone cannot separate near-misses ("refund my gym membership")
    from real questions, so only the clear cases are decided by similarity:

    * below `reject_below`  -> out of scope, no LLM call (every answerable case scored above it)
    * above `accept_above`  -> confident retrieval (every out-of-scope case scored below it)
    * in between            -> uncertain; the grounding judge decides
    """

    reject_below: float
    accept_above: float
    in_scope_to_judge: float  # share of answerable questions that need the judge
    out_of_scope_to_judge: float
    out_of_scope_rejected_outright: float


@dataclass(frozen=True, slots=True)
class EvalSummary:
    results: list[CaseResult]
    hit_at: dict[int, float]
    mrr: float
    threshold: ThresholdChoice
    sweep: list[ThresholdChoice]
    bands: GateBands


async def run_eval(
    retriever: HybridRetriever, cases: Sequence[EvalCase], top_k: int = 5
) -> EvalSummary:
    results: list[CaseResult] = []
    for case in cases:
        res = await retriever.search(case.query, case.tier, top_k=top_k)
        rank = next((i for i, c in enumerate(res.chunks, 1) if case.matches(c)), None)
        results.append(
            CaseResult(
                case=case,
                rank=rank if case.in_scope else None,
                top_similarity=res.top_similarity,
                top_section=res.chunks[0].section_path if res.chunks else "-",
            )
        )
    return summarise(results)


def summarise(results: list[CaseResult]) -> EvalSummary:
    answerable = [r for r in results if r.case.in_scope]
    n = len(answerable) or 1
    hit_at = {k: sum(1 for r in answerable if r.rank and r.rank <= k) / n for k in (1, 3, 5)}
    mrr = sum(1 / r.rank for r in answerable if r.rank) / n
    in_scores = [r.top_similarity for r in answerable]
    out_scores = [r.top_similarity for r in results if not r.case.in_scope]
    sweep = sweep_thresholds(in_scores, out_scores)
    return EvalSummary(
        results, hit_at, mrr, choose_threshold(sweep), sweep, gate_bands(in_scores, out_scores)
    )


def gate_bands(in_scope: Sequence[float], out_of_scope: Sequence[float]) -> GateBands:
    # Two-decimal grid. reject_below <= every answerable score; accept_above > every
    # out-of-scope score. The epsilon absorbs float noise (0.58 * 100 == 57.999...).
    reject_below = math.floor(min(in_scope, default=0.0) * 100 + 1e-9) / 100
    accept_above = round(math.floor(max(out_of_scope, default=0.99) * 100 + 1e-9) / 100 + 0.01, 2)
    accept_above = max(accept_above, reject_below)

    def share(values: Sequence[float], pred: Callable[[float], bool]) -> float:
        return sum(1 for v in values if pred(v)) / max(len(values), 1)

    def in_band(v: float) -> bool:
        return reject_below <= v < accept_above

    return GateBands(
        reject_below=reject_below,
        accept_above=accept_above,
        in_scope_to_judge=share(in_scope, in_band),
        out_of_scope_to_judge=share(out_of_scope, in_band),
        out_of_scope_rejected_outright=share(out_of_scope, lambda v: v < reject_below),
    )


def sweep_thresholds(
    in_scope: Sequence[float], out_of_scope: Sequence[float], lo: float = 0.30, hi: float = 0.95
) -> list[ThresholdChoice]:
    out: list[ThresholdChoice] = []
    steps = round((hi - lo) / 0.01)
    for i in range(steps + 1):
        t = round(lo + i * 0.01, 2)
        tpr = sum(s >= t for s in in_scope) / max(len(in_scope), 1)
        tnr = sum(s < t for s in out_of_scope) / max(len(out_of_scope), 1)
        out.append(ThresholdChoice(t, tpr, tnr, (tpr + tnr) / 2))
    return out


def choose_threshold(sweep: Sequence[ThresholdChoice]) -> ThresholdChoice:
    """Best balanced accuracy; among ties take the middle of the plateau (largest margin)."""
    best = max(c.balanced_accuracy for c in sweep)
    plateau = [c for c in sweep if abs(c.balanced_accuracy - best) < 1e-9]
    return plateau[len(plateau) // 2]


def render_markdown(summary: EvalSummary, config_lines: Sequence[str]) -> str:
    answerable = [r for r in summary.results if r.case.in_scope]
    rejected = [r for r in summary.results if not r.case.in_scope]
    t = summary.threshold
    b = summary.bands

    def dist(values: list[float]) -> str:
        if not values:
            return "-"
        return (
            f"min {min(values):.3f} / median {statistics.median(values):.3f} "
            f"/ max {max(values):.3f}"
        )

    lines = [
        "# Retrieval evaluation",
        "",
        *[f"- {c}" for c in config_lines],
        f"- Cases: {len(answerable)} in scope, {len(rejected)} out of scope",
        "",
        "## Ranking (in-scope cases)",
        "",
        "| Hit@1 | Hit@3 | Hit@5 | MRR |",
        "|---|---|---|---|",
        f"| {summary.hit_at[1]:.0%} | {summary.hit_at[3]:.0%} | {summary.hit_at[5]:.0%} "
        f"| {summary.mrr:.3f} |",
        "",
        "## Confidence gate (top cosine similarity)",
        "",
        f"- In scope: {dist([r.top_similarity for r in answerable])}",
        f"- Out of scope: {dist([r.top_similarity for r in rejected])}",
        f"- Best *single* threshold: {t.threshold:.2f} - accepts {t.in_scope_accepted:.0%} of "
        f"answerable questions, rejects {t.out_of_scope_rejected:.0%} of out-of-scope ones "
        f"(balanced accuracy {t.balanced_accuracy:.0%}). The two distributions overlap, so a "
        f"single cut-off must trade wrong answers against needless escalations.",
        "",
        "### Three-band gate (used by the agent)",
        "",
        "| Band | Similarity | Action |",
        "|---|---|---|",
        f"| Reject | < {b.reject_below:.2f} | Out of scope, no LLM call |",
        f"| Uncertain | {b.reject_below:.2f} - {b.accept_above:.2f} | Grounding judge decides |",
        f"| Confident | >= {b.accept_above:.2f} | Answer, grounded on retrieved chunks |",
        "",
        f"- Answerable questions sent to the judge: {b.in_scope_to_judge:.0%} "
        f"(none rejected outright)",
        f"- Out-of-scope questions rejected outright: {b.out_of_scope_rejected_outright:.0%}; "
        f"sent to the judge: {b.out_of_scope_to_judge:.0%} (none accepted outright)",
        "",
        "## Per case",
        "",
        "| Case | Tier | In scope | Rank | Top sim | Gate band | Top section |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in summary.results:
        s = r.top_similarity
        band = "reject" if s < b.reject_below else "confident" if s >= b.accept_above else "judge"
        wrong = (band == "reject" and r.case.in_scope) or (
            band == "confident" and not r.case.in_scope
        )
        gate = band + (" ✗" if wrong else "")
        rank = "-" if not r.case.in_scope else (str(r.rank) if r.rank else "miss ✗")
        tier = r.case.tier.value if r.case.tier else "unverified"
        lines.append(
            f"| {r.case.id} | {tier} | {'yes' if r.case.in_scope else 'no'} | {rank} "
            f"| {r.top_similarity:.3f} | {gate} | {r.top_section} |"
        )
    return "\n".join(lines) + "\n"
