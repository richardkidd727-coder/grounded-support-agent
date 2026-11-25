"""Structure-aware markdown chunking for policy documents.

Policy text is made of *rules*: a sentence plus the list or table that qualifies it. Fixed-size
splitting cuts those apart ("Refunds are allowed within" | "30 days, up to $100"), which is the
main source of wrong-but-confident answers. So this chunker:

1. Splits on headings first. A chunk never spans two sections, and every chunk carries its
   breadcrumb ("Refund Policy > Pro plan > Monthly Pro subscriptions"), which is also prepended
   to the text that gets embedded.
2. Treats lists, tables and code fences as atomic blocks, and glues a lead-in sentence ending in
   ":" to the block it introduces.
3. Packs blocks greedily up to `max_tokens`. Only an oversize block is split - lists by item,
   tables by row with the header repeated, prose by sentence.
4. Overlaps consecutive chunks of the same section by carrying the previous chunk's last block
   when it is small, so a rule at a boundary appears whole in at least one chunk.
5. Resolves each section's audience (`<!-- audience: pro -->`, inherited by sub-sections), which
   becomes a hard retrieval filter.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Literal

from support_agent.domain import AUDIENCE_VALUES
from support_agent.rag.documents import DocumentError, SourceDocument
from support_agent.rag.tokens import estimate_tokens

CHUNKER_VERSION = "structure-v2"  # bump to force re-indexing when the algorithm changes

_HEADING = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<text>.+?)\s*#*\s*$")
_AUDIENCE = re.compile(r"^\s*<!--\s*audience:\s*(?P<values>[\w,\s]+?)\s*-->\s*$")
_COMMENT = re.compile(r"^\s*<!--.*-->\s*$")
_LIST_ITEM = re.compile(r"^(?P<indent>\s*)(?:[-*+]|\d+[.)])\s+")
_FENCE = re.compile(r"^\s*(```|~~~)")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")

BlockKind = Literal["paragraph", "list", "table", "code"]


@dataclass(frozen=True, slots=True)
class ChunkerConfig:
    max_tokens: int = 350
    overlap_tokens: int = 60

    @property
    def signature(self) -> str:
        return f"{CHUNKER_VERSION}:{self.max_tokens}:{self.overlap_tokens}"


@dataclass(frozen=True, slots=True)
class Block:
    kind: BlockKind
    text: str

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


@dataclass(slots=True)
class Section:
    level: int
    heading: str
    path: tuple[str, ...]  # headings below the document title, outermost first
    audience: tuple[str, ...]
    blocks: list[Block] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Chunk:
    chunk_id: str
    doc_id: str
    ordinal: int
    title: str
    section_path: str  # "Title > Section > Sub-section"
    content: str
    audience: tuple[str, ...]
    token_count: int

    @property
    def embed_text(self) -> str:
        """Breadcrumb + content: gives short chunks the context their section heading implies."""
        return f"{self.section_path}\n\n{self.content}"

    @property
    def content_hash(self) -> str:
        payload = f"{self.embed_text}\n{','.join(self.audience)}".encode()
        return hashlib.sha256(payload).hexdigest()


# --- parsing --------------------------------------------------------------------------------


def parse_sections(doc: SourceDocument) -> list[Section]:
    """Split a document body into sections, each holding its own top-level blocks."""
    lines = doc.body.split("\n")
    root = Section(level=0, heading=doc.title, path=(), audience=doc.audience)
    sections = [root]
    stack: list[Section] = []  # open headings, outermost first
    current = root
    i = 0

    while i < len(lines):
        line = lines[i]
        heading = _HEADING.match(line)

        if heading:
            level = len(heading["hashes"])
            text = heading["text"].strip()
            while stack and stack[-1].level >= level:
                stack.pop()
            inherited = stack[-1].audience if stack else doc.audience

            audience, i = _read_audience(lines, i + 1, doc, inherited)
            # The H1 is the document title: it opens the root, it is not part of the breadcrumb.
            path = () if level == 1 else (*(s.heading for s in stack if s.level > 1), text)
            current = Section(level=level, heading=text, path=path, audience=audience)
            stack.append(current)
            sections.append(current)
            continue

        if not line.strip() or (_COMMENT.match(line) and not _FENCE.match(line)):
            i += 1
            continue

        block, i = _read_block(lines, i)
        current.blocks.append(block)

    return [s for s in sections if s.blocks]


def _read_audience(
    lines: list[str], start: int, doc: SourceDocument, inherited: tuple[str, ...]
) -> tuple[tuple[str, ...], int]:
    """Consume an optional `<!-- audience: ... -->` line right after a heading."""
    j = start
    while j < len(lines) and not lines[j].strip():
        j += 1
    match = _AUDIENCE.match(lines[j]) if j < len(lines) else None
    if not match:
        return inherited, start

    values = tuple(v.strip() for v in match["values"].split(",") if v.strip())
    unknown = set(values) - AUDIENCE_VALUES
    if unknown:
        raise DocumentError(f"{doc.source_path}: unknown audience {sorted(unknown)}")
    if "all" not in doc.audience and not set(values) <= set(doc.audience):
        raise DocumentError(
            f"{doc.source_path}: section audience {values} is wider than document {doc.audience}"
        )
    return values, j + 1


def _read_block(lines: list[str], i: int) -> tuple[Block, int]:
    line = lines[i]
    n = len(lines)

    if _FENCE.match(line):
        fence = _FENCE.match(line)
        assert fence is not None
        j = i + 1
        while j < n and not lines[j].lstrip().startswith(fence.group(1)):
            j += 1
        return Block("code", "\n".join(lines[i : j + 1]).strip()), j + 1

    if line.lstrip().startswith("|"):
        j = i
        while j < n and lines[j].lstrip().startswith("|"):
            j += 1
        return Block("table", "\n".join(lines[i:j]).strip()), j

    if _LIST_ITEM.match(line):
        j = i
        while j < n:
            cur = lines[j]
            if _LIST_ITEM.match(cur) or (cur.startswith((" ", "\t")) and cur.strip()):
                j += 1
            elif not cur.strip() and j + 1 < n and _LIST_ITEM.match(lines[j + 1]):
                j += 1  # blank line between items of the same list
            else:
                break
        return Block("list", "\n".join(lines[i:j]).strip()), j

    j = i
    while j < n and lines[j].strip() and not _starts_construct(lines[j]):
        j += 1
    j = max(j, i + 1)
    return Block("paragraph", " ".join(s.strip() for s in lines[i:j])), j


def _starts_construct(line: str) -> bool:
    return bool(
        _HEADING.match(line)
        or _LIST_ITEM.match(line)
        or _FENCE.match(line)
        or line.lstrip().startswith("|")
        or _COMMENT.match(line)
    )


# --- packing --------------------------------------------------------------------------------


def chunk_document(doc: SourceDocument, config: ChunkerConfig | None = None) -> list[Chunk]:
    config = config or ChunkerConfig()
    chunks: list[Chunk] = []
    for section in parse_sections(doc):
        section_path = " > ".join((doc.title, *section.path))
        for blocks in _pack(_glue_lead_ins(section.blocks, config), config):
            content = "\n\n".join(b.text for b in blocks)
            ordinal = len(chunks)
            chunks.append(
                Chunk(
                    chunk_id=f"{doc.doc_id}:{ordinal:03d}",
                    doc_id=doc.doc_id,
                    ordinal=ordinal,
                    title=doc.title,
                    section_path=section_path,
                    content=content,
                    audience=section.audience,
                    token_count=estimate_tokens(f"{section_path}\n\n{content}"),
                )
            )
    return chunks


def _glue_lead_ins(blocks: list[Block], config: ChunkerConfig) -> list[Block]:
    """Merge "These rules apply:" with the list/table it introduces, when they fit together."""
    out: list[Block] = []
    for block in blocks:
        prev = out[-1] if out else None
        if (
            prev is not None
            and prev.kind == "paragraph"
            and prev.text.rstrip().endswith(":")
            and block.kind in ("list", "table")
            and prev.tokens + block.tokens <= config.max_tokens
        ):
            out[-1] = Block(block.kind, f"{prev.text}\n\n{block.text}")
        else:
            out.append(block)
    return out


def _pack(blocks: list[Block], config: ChunkerConfig) -> list[list[Block]]:
    units = [unit for block in blocks for unit in _split_oversize(block, config.max_tokens)]
    groups: list[list[Block]] = []
    current: list[Block] = []
    size = 0

    for unit in units:
        if current and size + unit.tokens > config.max_tokens:
            groups.append(current)
            carry = current[-1]
            if carry.tokens <= config.overlap_tokens and carry.tokens + unit.tokens <= (
                config.max_tokens
            ):
                current, size = [carry], carry.tokens
            else:
                current, size = [], 0
        current.append(unit)
        size += unit.tokens

    if current:
        groups.append(current)
    return groups


def _split_oversize(block: Block, max_tokens: int) -> list[Block]:
    if block.tokens <= max_tokens:
        return [block]
    if block.kind == "table":
        return _split_table(block, max_tokens)
    if block.kind == "list":
        parts = _list_items(block.text)
        joiner = "\n"
    else:
        parts = _SENTENCE_END.split(block.text)
        joiner = " "
    return [Block(block.kind, text) for text in _greedy_join(parts, joiner, max_tokens)]


def _split_table(block: Block, max_tokens: int) -> list[Block]:
    lines = block.text.split("\n")
    header, rows = lines[:2], lines[2:]  # header row + |---| separator, repeated in each part
    head = "\n".join(header)
    budget = max(max_tokens - estimate_tokens(head), 1)
    return [Block("table", f"{head}\n{part}") for part in _greedy_join(rows, "\n", budget)]


def _list_items(text: str) -> list[str]:
    """Top-level list items, each with its nested lines."""
    items: list[str] = []
    for line in text.split("\n"):
        m = _LIST_ITEM.match(line)
        if m and not m["indent"]:
            items.append(line)
        elif items:
            items[-1] = f"{items[-1]}\n{line}"
        else:
            items.append(line)
    return items


def _greedy_join(parts: list[str], joiner: str, max_tokens: int) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    for part in parts:
        candidate = joiner.join([*buf, part])
        if buf and estimate_tokens(candidate) > max_tokens:
            out.append(joiner.join(buf))
            buf = [part]
        else:
            buf.append(part)
    if buf:
        out.append(joiner.join(buf))
    return out
