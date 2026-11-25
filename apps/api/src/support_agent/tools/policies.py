"""search_policies - tier-aware retrieval over the knowledge base."""

from __future__ import annotations

from pydantic import Field

from support_agent.rag.gate import GateBand, gate_band
from support_agent.tools.base import Tool, ToolContext, ToolInput, ToolOutput


class SearchPoliciesInput(ToolInput):
    query: str = Field(
        min_length=3,
        max_length=300,
        description="A focused search query, e.g. 'refund window for monthly Pro charges'.",
    )


class PolicyExcerpt(ToolOutput):
    chunk_id: str
    doc_id: str
    section: str  # breadcrumb, starts with the document title
    content: str
    similarity: float


class SearchPoliciesOutput(ToolOutput):
    audience: str  # whose policies were searched: "general" or a tier
    confidence: GateBand
    top_similarity: float
    excerpts: list[PolicyExcerpt]
    guidance: str


_GUIDANCE: dict[GateBand, str] = {
    "confident": "Answer only from these excerpts and cite the section names.",
    "uncertain": (
        "The match is weak. Answer only if an excerpt directly addresses the question; otherwise "
        "say you are not sure and offer to escalate."
    ),
    "reject": (
        "Nothing relevant was found. Do not answer from general knowledge. Tell the customer this "
        "is outside what you can help with, or escalate if it is a Driftwood account question."
    ),
}


class SearchPolicies(Tool[SearchPoliciesInput, SearchPoliciesOutput]):
    name = "search_policies"
    label = "Searching knowledge base..."
    description = (
        "Search Driftwood's support policies and troubleshooting guides. Use it for any question "
        "about refunds, billing, plans, contracts, SLAs, security, data or troubleshooting - never "
        "answer those from memory. Results are automatically limited to the verified customer's "
        "plan (or general policies if not verified). Returns excerpts plus a confidence band."
    )
    input_model = SearchPoliciesInput
    output_model = SearchPoliciesOutput
    timeout_s = 6.0

    async def run(self, args: SearchPoliciesInput, ctx: ToolContext) -> SearchPoliciesOutput:
        tier = ctx.auth.tier  # from the verified session, never from model arguments
        settings = ctx.deps.settings
        result = await ctx.deps.retriever.search(args.query, tier, top_k=settings.retrieval_top_k)
        band = gate_band(result.top_similarity, settings)
        excerpts = (
            []
            if band == "reject"
            else [
                PolicyExcerpt(
                    chunk_id=c.chunk_id,
                    doc_id=c.doc_id,
                    section=c.section_path,
                    content=c.content,
                    similarity=round(c.similarity, 3),
                )
                for c in result.chunks
            ]
        )
        return SearchPoliciesOutput(
            audience=tier.value if tier else "general",
            confidence=band,
            top_similarity=round(result.top_similarity, 3),
            excerpts=excerpts,
            guidance=_GUIDANCE[band],
        )
