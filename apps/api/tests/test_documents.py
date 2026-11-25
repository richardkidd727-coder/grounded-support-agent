from pathlib import Path

import pytest

from support_agent.rag.documents import DocumentError, load_documents, parse_document

VALID = (
    "---\ndoc_id: a-doc\ntitle: A\ndoc_type: guide\naudience: [pro]\nversion: 2026-09\n---\nBody"
)


def test_parses_front_matter_and_normalises_version() -> None:
    doc = parse_document(VALID, "a.md")
    assert (doc.doc_id, doc.title, doc.audience, doc.version) == ("a-doc", "A", ("pro",), "2026-09")
    assert doc.body == "Body"
    assert len(doc.content_hash) == 64


def test_windows_line_endings_are_accepted() -> None:
    assert parse_document(VALID.replace("\n", "\r\n"), "a.md").doc_id == "a-doc"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("no front matter", "missing YAML front matter"),
        (VALID.replace("doc_type: guide", "doc_type: memo"), "invalid front matter"),
        (VALID.replace("[pro]", "[vip]"), "invalid front matter"),
        (VALID.replace("doc_id: a-doc", "doc_id: Bad Id"), "invalid front matter"),
        (VALID.replace("title: A\n", "title: A\nextra: 1\n"), "invalid front matter"),
    ],
)
def test_invalid_documents_rejected(text: str, message: str) -> None:
    with pytest.raises(DocumentError, match=message):
        parse_document(text, "bad.md")


def test_duplicate_doc_ids_rejected(tmp_path: Path) -> None:
    (tmp_path / "one.md").write_text(VALID, encoding="utf-8")
    (tmp_path / "two.md").write_text(VALID, encoding="utf-8")
    with pytest.raises(DocumentError, match="duplicate doc_id"):
        load_documents(tmp_path)


def test_readme_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# not a policy", encoding="utf-8")
    (tmp_path / "one.md").write_text(VALID, encoding="utf-8")
    assert [d.doc_id for d in load_documents(tmp_path)] == ["a-doc"]
