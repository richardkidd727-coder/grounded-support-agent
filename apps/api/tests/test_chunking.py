from pathlib import Path

import pytest

from support_agent.config import API_ROOT
from support_agent.rag.chunking import ChunkerConfig, chunk_document, parse_sections
from support_agent.rag.documents import DocumentError, load_documents, parse_document
from support_agent.rag.tokens import estimate_tokens


def make_doc(body: str, audience: str = "[all]") -> str:
    return (
        f"---\ndoc_id: test-doc\ntitle: Test Doc\ndoc_type: policy\n"
        f"audience: {audience}\nversion: 1\n---\n\n# Test Doc\n\n{body}"
    )


def chunks_of(body: str, audience: str = "[all]", **config: int) -> list:  # type: ignore[type-arg]
    doc = parse_document(make_doc(body, audience), "test.md")
    return chunk_document(doc, ChunkerConfig(**config))


def test_breadcrumbs_follow_heading_hierarchy() -> None:
    chunks = chunks_of("Intro text.\n\n## Billing\n\nB.\n\n### Taxes\n\nT.\n\n## Support\n\nS.")
    assert [c.section_path for c in chunks] == [
        "Test Doc",
        "Test Doc > Billing",
        "Test Doc > Billing > Taxes",
        "Test Doc > Support",
    ]
    assert chunks[2].embed_text.startswith("Test Doc > Billing > Taxes\n\n")


def test_audience_comment_applies_and_is_inherited() -> None:
    body = (
        "## Pro plan\n<!-- audience: pro -->\n\nP.\n\n### Annual\n\nA.\n\n"
        "## Everyone\n\nE.\n\n## Big\n<!-- audience: pro, enterprise -->\n\nB."
    )
    by_path = {c.section_path: c.audience for c in chunks_of(body)}
    assert by_path["Test Doc > Pro plan"] == ("pro",)
    assert by_path["Test Doc > Pro plan > Annual"] == ("pro",)  # inherited
    assert by_path["Test Doc > Everyone"] == ("all",)
    assert by_path["Test Doc > Big"] == ("pro", "enterprise")
    assert all("audience" not in c.content for c in chunks_of(body))  # comment not in content


def test_section_audience_cannot_widen_a_restricted_document() -> None:
    with pytest.raises(DocumentError, match="wider"):
        chunks_of("## S\n<!-- audience: free -->\n\nx", audience="[enterprise]")


def test_unknown_audience_rejected() -> None:
    with pytest.raises(DocumentError, match="unknown audience"):
        chunks_of("## S\n<!-- audience: vip -->\n\nx")


def test_lead_in_sentence_stays_with_its_list() -> None:
    chunks = chunks_of("## Rules\n\nThese rules apply:\n\n- one\n- two\n\nAfter.")
    assert len(chunks) == 1
    assert "These rules apply:\n\n- one\n- two" in chunks[0].content


def test_list_items_and_tables_are_never_split_when_they_fit() -> None:
    items = "\n".join(f"- rule number {i} with some words" for i in range(10))
    table = "| a | b |\n|---|---|\n" + "\n".join(f"| r{i} | v{i} |" for i in range(10))
    for chunk in chunks_of(f"## L\n\n{items}\n\n## T\n\n{table}", max_tokens=200):
        assert chunk.content in (items, table)


def test_oversize_table_is_split_by_row_with_header_repeated() -> None:
    rows = "\n".join(f"| row {i} | value number {i} with padding words |" for i in range(40))
    table = f"| Name | Value |\n|---|---|\n{rows}"
    chunks = chunks_of(f"## Big table\n\n{table}", max_tokens=120, overlap_tokens=10)
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.content.startswith("| Name | Value |\n|---|---|\n")
    all_rows = [line for c in chunks for line in c.content.split("\n")[2:]]
    assert all_rows == rows.split("\n")  # every row exactly once, in order


def test_oversize_list_is_split_by_item_not_mid_item() -> None:
    items = [f"- item {i}\n  continued detail line for item {i}" for i in range(30)]
    chunks = chunks_of("## Long list\n\n" + "\n".join(items), max_tokens=100, overlap_tokens=0)
    assert len(chunks) > 1
    rebuilt = "\n".join(c.content for c in chunks)
    assert rebuilt == "\n".join(items)


def test_small_trailing_block_is_carried_as_overlap() -> None:
    long_para = " ".join(["word"] * 120) + "."
    body = f"## S\n\n{long_para}\n\nShort rule.\n\n{long_para}"
    # Room for one paragraph plus the short rule, but not two paragraphs.
    budget = estimate_tokens(long_para) + estimate_tokens("Short rule.") + 5
    chunks = chunks_of(body, max_tokens=budget, overlap_tokens=20)
    assert len(chunks) == 2
    assert chunks[0].content.endswith("Short rule.")
    assert chunks[1].content.startswith("Short rule.")


def test_code_fence_is_atomic_and_headings_inside_it_are_ignored() -> None:
    chunks = chunks_of("## Example\n\n```\n# not a heading\nkey: value\n```")
    assert len(chunks) == 1
    assert "# not a heading" in chunks[0].content


def test_chunk_ids_are_stable_and_sequential() -> None:
    first = chunks_of("## A\n\nx\n\n## B\n\ny")
    second = chunks_of("## A\n\nx\n\n## B\n\ny")
    assert [c.chunk_id for c in first] == ["test-doc:000", "test-doc:001"]
    assert [c.content_hash for c in first] == [c.content_hash for c in second]


def test_empty_sections_produce_no_chunks() -> None:
    sections = parse_sections(parse_document(make_doc("## Parent\n\n### Child\n\ntext"), "t.md"))
    assert [s.heading for s in sections] == ["Child"]


# --- the real corpus ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def corpus_dir() -> Path:
    return API_ROOT / "knowledge_base"


def test_real_corpus_chunks_within_limits(corpus_dir: Path) -> None:
    config = ChunkerConfig()
    docs = load_documents(corpus_dir)
    assert len(docs) >= 12
    for doc in docs:
        chunks = chunk_document(doc, config)
        assert chunks, doc.doc_id
        for c in chunks:
            # Overlap may push a chunk slightly past max_tokens, never past the model's 512.
            assert estimate_tokens(c.embed_text) <= config.max_tokens + config.overlap_tokens
            assert c.content.strip()


def test_real_corpus_tier_sections_are_tagged(corpus_dir: Path) -> None:
    doc = next(d for d in load_documents(corpus_dir) if d.doc_id == "refund-policy")
    audience = {c.section_path.split(" > ")[-1]: c.audience for c in chunk_document(doc)}
    assert audience["Monthly Pro subscriptions"] == ("pro",)
    assert audience["Enterprise plan"] == ("enterprise",)
    assert audience["General rules"] == ("all",)


def test_real_tokenizer_never_exceeds_model_limit(corpus_dir: Path) -> None:
    """Our estimate must over-count WordPiece tokens (skipped if the model isn't cached)."""
    tokenizers = pytest.importorskip("tokenizers")
    tokenizer_files = list((API_ROOT / ".cache" / "fastembed").rglob("tokenizer.json"))
    if not tokenizer_files:
        pytest.skip("embedding model not downloaded (run `uv run ingest` once)")
    tok = tokenizers.Tokenizer.from_file(str(tokenizer_files[0]))
    for doc in load_documents(corpus_dir):
        for c in chunk_document(doc):
            real = len(tok.encode(c.embed_text).ids)
            assert real <= 512
            assert real <= estimate_tokens(c.embed_text), (c.chunk_id, real)
