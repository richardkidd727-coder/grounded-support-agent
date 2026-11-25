"""Load knowledge-base markdown files with validated YAML front matter."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from support_agent.domain import AUDIENCE_VALUES

_FRONT_MATTER = re.compile(r"\A---\s*\n(?P<meta>.*?)\n---\s*\n(?P<body>.*)\Z", re.DOTALL)
SKIPPED_FILES = frozenset({"README.md"})


class DocumentError(ValueError):
    """A knowledge-base file is malformed. Raised at ingest time, never at query time."""


class FrontMatter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    title: str = Field(min_length=1)
    doc_type: Literal["policy", "guide", "troubleshooting", "reference"]
    audience: list[str] = Field(min_length=1)
    version: str

    @field_validator("version", mode="before")
    @classmethod
    def _version_as_str(cls, v: object) -> str:
        return str(v)  # YAML reads `2026-09` style values as non-strings

    @field_validator("audience")
    @classmethod
    def _known_audiences(cls, v: list[str]) -> list[str]:
        unknown = set(v) - AUDIENCE_VALUES
        if unknown:
            raise ValueError(f"unknown audience(s): {sorted(unknown)}")
        return v


@dataclass(frozen=True, slots=True)
class SourceDocument:
    doc_id: str
    title: str
    doc_type: str
    audience: tuple[str, ...]
    version: str
    body: str
    source_path: str
    content_hash: str


def parse_document(text: str, source_path: str) -> SourceDocument:
    match = _FRONT_MATTER.match(text.replace("\r\n", "\n"))
    if not match:
        raise DocumentError(f"{source_path}: missing YAML front matter")
    try:
        raw = yaml.safe_load(match["meta"])
        meta = FrontMatter.model_validate(raw)
    except (yaml.YAMLError, ValidationError) as exc:
        raise DocumentError(f"{source_path}: invalid front matter: {exc}") from exc

    return SourceDocument(
        doc_id=meta.doc_id,
        title=meta.title,
        doc_type=meta.doc_type,
        audience=tuple(meta.audience),
        version=meta.version,
        body=match["body"],
        source_path=source_path,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def load_documents(directory: Path) -> list[SourceDocument]:
    docs: list[SourceDocument] = []
    for path in sorted(directory.glob("*.md")):
        if path.name in SKIPPED_FILES:
            continue
        docs.append(parse_document(path.read_text(encoding="utf-8"), path.name))

    seen: dict[str, str] = {}
    for doc in docs:
        if doc.doc_id in seen:
            raise DocumentError(
                f"duplicate doc_id {doc.doc_id!r} in {seen[doc.doc_id]} and {doc.source_path}"
            )
        seen[doc.doc_id] = doc.source_path
    return docs
