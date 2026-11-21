"""Chunking primitives shared by all strategies.

Input is a list of provenance-carrying `Block`s (built from the persisted Document Graph);
output is `ChunkDraft`s that keep document/page/bbox/section/element provenance no matter how
the text was split — that provenance is what citations resolve to.

Token counts use a deterministic word-piece approximation (`count_tokens`), not a specific
model's tokenizer, so chunk boundaries are reproducible across embedding providers.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

_TOKEN_RE = re.compile(r"\w+|[^\w\s]")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\"'])")


def count_tokens(text: str) -> int:
    return len(_TOKEN_RE.findall(text))


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_RE.split(text) if s.strip()]


class BlockKind(enum.StrEnum):
    TEXT = "text"
    HEADING = "heading"
    TABLE = "table"
    FIGURE = "figure"


class ChunkType(enum.StrEnum):
    TEXT = "text"
    SECTION = "section"
    PAGE = "page"
    TABLE = "table"
    TABLE_ROWS = "table_rows"
    CHART = "chart"
    IMAGE = "image"


@dataclass(slots=True)
class Block:
    kind: BlockKind
    text: str
    page_no: int | None
    bbox: tuple[float, float, float, float] | list[float] | None
    section_path: list[str]
    element_ref: str
    # tables: header text + per-row flattened strings; figures: type/estimated flag
    table_header: str | None = None
    table_rows: list[str] = field(default_factory=list)
    page_end: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ChunkDraft:
    content: str
    chunk_type: ChunkType
    blocks: list[Block]
    parent_index: int | None = None  # index into the same draft list
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def page_start(self) -> int | None:
        pages = [b.page_no for b in self.blocks if b.page_no is not None]
        return min(pages) if pages else None

    @property
    def page_end(self) -> int | None:
        pages = [(b.page_end or b.page_no) for b in self.blocks if b.page_no is not None]
        return max(p for p in pages if p is not None) if pages else None

    @property
    def section_path(self) -> list[str]:
        # deepest common section path of the blocks
        paths = [b.section_path for b in self.blocks if b.section_path]
        if not paths:
            return []
        common = paths[0]
        for p in paths[1:]:
            n = 0
            while n < min(len(common), len(p)) and common[n] == p[n]:
                n += 1
            common = common[:n]
        return list(common) or list(paths[0][:1])

    @property
    def bboxes(self) -> list[dict[str, Any]]:
        out, seen = [], set()
        for b in self.blocks:
            if b.page_no is None or b.bbox is None:
                continue
            key = (b.page_no, tuple(b.bbox))
            if key not in seen:
                seen.add(key)
                out.append({"page": b.page_no, "bbox": [round(float(v), 4) for v in b.bbox]})
        return out

    @property
    def element_refs(self) -> list[str]:
        return list(dict.fromkeys(b.element_ref for b in self.blocks))

    @property
    def token_count(self) -> int:
        return count_tokens(self.content)


def embedding_text(draft: ChunkDraft, doc_title: str) -> str:
    """Contextual header + content: retrieval sees *where* text sits, not just the text."""
    path = (
        " > ".join([doc_title, *draft.section_path])
        if doc_title
        else " > ".join(draft.section_path)
    )
    page = f" (p. {draft.page_start})" if draft.page_start else ""
    return f"[{path}{page}]\n{draft.content}" if path else draft.content


class Embedder(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


class Chunker(Protocol):
    name: str

    def chunk(self, blocks: list[Block]) -> list[ChunkDraft]: ...


@dataclass(slots=True)
class ChunkingConfig:
    max_tokens: int = 350
    overlap_tokens: int = 50
    child_tokens: int = 120
    table_rows_per_chunk: int = 12
    semantic_breakpoint_percentile: float = 90.0
