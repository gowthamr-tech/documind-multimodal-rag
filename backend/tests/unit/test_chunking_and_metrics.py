import math

import pytest

from app.chunking.base import Block, BlockKind, ChunkType, count_tokens, embedding_text
from app.chunking.strategies import STRATEGIES, get_chunker
from app.evaluation.metrics.retrieval import mrr, ndcg_at_k, precision_at_k, recall_at_k
from app.retrieval.retriever import ContextItem, assemble_context, resolve_citation
from app.retrieval.search import Hit, RetrievedChunk, rrf


class HashEmbedder:
    """Deterministic toy embedder for strategy tests (bag of hashed words)."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * 32
            for w in t.lower().split():
                v[hash(w) % 32] += 1
            n = math.sqrt(sum(x * x for x in v)) or 1
            out.append([x / n for x in v])
        return out


def _doc() -> list[Block]:
    para = " ".join(f"Revenue sentence {i} grew strongly in the cloud segment." for i in range(40))
    return [
        Block(
            BlockKind.HEADING, "Revenue", 1, (0.1, 0.1, 0.5, 0.12), ["Report", "Revenue"], "#/t/0"
        ),
        Block(BlockKind.TEXT, para, 1, (0.1, 0.15, 0.9, 0.5), ["Report", "Revenue"], "#/t/1"),
        Block(
            BlockKind.HEADING, "Expenses", 2, (0.1, 0.1, 0.5, 0.12), ["Report", "Expenses"], "#/t/2"
        ),
        Block(
            BlockKind.TEXT,
            "Marketing costs rose. Research costs fell.",
            2,
            (0.1, 0.15, 0.9, 0.2),
            ["Report", "Expenses"],
            "#/t/3",
        ),
        Block(
            BlockKind.TABLE,
            "Table 2\n| Category | 2024 |\n|---|---|\n"
            + "\n".join(f"| Cat{i} | {i}.0 |" for i in range(30)),
            2,
            (0.2, 0.3, 0.8, 0.6),
            ["Report", "Expenses"],
            "#/tables/0",
            table_header="Category | 2024",
            table_rows=[f"Cat{i} | 2024: {i}.0" for i in range(30)],
            extra={"caption": "Table 2"},
        ),
        Block(
            BlockKind.FIGURE,
            "Figure 1\n[chart: bar_chart]",
            3,
            (0.1, 0.4, 0.9, 0.7),
            ["Report", "Outlook"],
            "#/pictures/0",
            extra={"figure_type": "chart", "values_are_estimated": True},
        ),
    ]


@pytest.mark.parametrize("name", STRATEGIES)
def test_every_strategy_preserves_provenance(name: str) -> None:
    drafts = get_chunker(name, embedder=HashEmbedder()).chunk(_doc())
    assert drafts
    for d in drafts:
        assert d.content.strip()
        assert d.page_start is not None
        assert d.element_refs
        assert d.bboxes and all(0 <= v <= 1 for b in d.bboxes for v in b["bbox"])
        assert d.section_path[:1] == ["Report"]


def test_size_bounded_strategies_respect_max_tokens() -> None:
    for name in ("recursive", "structure", "semantic"):
        for d in get_chunker(name, embedder=HashEmbedder()).chunk(_doc()):
            if d.chunk_type in {ChunkType.TEXT, ChunkType.SECTION}:
                assert d.token_count <= 350 + 60, (name, d.token_count)


def test_table_aware_never_splits_rows_and_repeats_header() -> None:
    drafts = get_chunker("table_aware").chunk(_doc())
    groups = [
        d for d in drafts if d.chunk_type is ChunkType.TABLE_ROWS or d.chunk_type is ChunkType.TABLE
    ]
    assert groups
    for g in groups:
        if g.chunk_type is ChunkType.TABLE_ROWS:
            assert g.content.splitlines()[1] == "Category | 2024"  # header repeated
    rows = [line for g in groups for line in g.content.splitlines() if line.startswith("Cat")]
    assert len(rows) == len(set(rows))  # no row duplicated or cut


def test_parent_child_links_children_to_parents() -> None:
    drafts = get_chunker("parent_child").chunk(_doc())
    parents = [i for i, d in enumerate(drafts) if d.metadata.get("role") == "parent"]
    children = [d for d in drafts if d.metadata.get("role") == "child"]
    assert parents
    assert children
    assert all(c.parent_index in parents for c in children)
    assert all(c.token_count <= 120 + 40 for c in children if c.chunk_type is ChunkType.TEXT)
    assert any(d.chunk_type is ChunkType.CHART for d in drafts)


def test_page_chunker_one_chunk_per_page() -> None:
    drafts = get_chunker("page").chunk(_doc())
    assert sorted(d.page_start for d in drafts) == [1, 2, 3]


def test_embedding_text_has_contextual_header() -> None:
    d = get_chunker("structure").chunk(_doc())[0]
    assert embedding_text(d, "Annual Report").startswith(
        "[Annual Report > Report > Revenue (p. 1)]"
    )


def test_unknown_and_misconfigured_strategies() -> None:
    with pytest.raises(ValueError, match="unknown"):
        get_chunker("magic")
    with pytest.raises(ValueError, match="embedder"):
        get_chunker("semantic")


def test_count_tokens_deterministic() -> None:
    assert count_tokens("Revenue grew 12.5%.") == 7


def test_ir_metrics_known_values() -> None:
    ranked = ["a", "b", "c", "d"]
    rel = {"b", "d"}
    assert recall_at_k(ranked, rel, 2) == 0.5
    assert precision_at_k(ranked, rel, 2) == 0.5
    assert mrr(ranked, rel) == 0.5
    assert math.isclose(
        ndcg_at_k(ranked, rel, 4), (1 / math.log2(3) + 1 / math.log2(5)) / (1 + 1 / math.log2(3))
    )
    assert ndcg_at_k(["x"], set(), 1) == 0.0


def test_rrf_rewards_agreement() -> None:
    import uuid

    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    fused = rrf([Hit(a, 0.9, 1), Hit(b, 0.8, 2)], [Hit(b, 5.0, 1), Hit(c, 4.0, 2)])
    assert fused[0][0] == b  # ranked well by both lists


def _rc(content: str, parent: str | None = None, pid=None):  # type: ignore[no-untyped-def]
    import uuid

    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        document_title="Doc",
        version_id=uuid.uuid4(),
        chunk_type="text",
        content=content,
        page_start=2,
        page_end=2,
        bboxes=[{"page": 2, "bbox": [0.1, 0.2, 0.3, 0.4]}],
        section_path=["S"],
        parent_chunk_id=pid,
        metadata={},
        parent_content=parent,
    )


def test_context_expands_parent_once_and_numbers_sources() -> None:
    import uuid

    pid = uuid.uuid4()
    items = assemble_context(
        [_rc("child one", "PARENT TEXT", pid), _rc("child two", "PARENT TEXT", pid), _rc("other")]
    )
    assert [i.source_id for i in items] == ["S1", "S2"]
    assert items[0].text == "PARENT TEXT"


def test_citation_quote_kept_only_if_verbatim() -> None:
    item = ContextItem(
        "S1",
        _rc("Total revenue was USD 1,578.7 million."),
        "Total revenue was USD 1,578.7 million.",
    )
    ok = resolve_citation(item, "USD 1,578.7 million")
    bad = resolve_citation(item, "USD 9,999 million")
    assert ok["quote_is_verbatim"] is True
    assert ok["page"] == 2
    assert ok["bbox"] == [0.1, 0.2, 0.3, 0.4]
    assert bad["quote_is_verbatim"] is False
    assert "9,999" not in bad["quote"]  # fabricated quote replaced by real source text
