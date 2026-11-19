"""Deterministic, heading-aware chunking with source references (AC-B-01)."""

from hypothesis import given, settings
from hypothesis import strategies as st

from devteam_services.extractor import Block, chunk_blocks, chunk_id
from devteam_services.extractor import chunker as chunker_module

SHA = "a" * 64


def _chunks(blocks: list[Block], target: int = 1200, hard_max: int = 2000) -> list[chunker_module.Chunk]:
    return chunk_blocks(
        blocks, document_sha256=SHA, display_name="BRD.pdf", target_chars=target, max_chars=hard_max
    )


SAMPLE = [
    Block("heading", "1 Intro", page=1, level=1),
    Block("paragraph", "First paragraph.", page=1),
    Block("paragraph", "Second paragraph.", page=2),
    Block("heading", "1.1 Detail", page=2, level=2),
    Block("paragraph", "Third.", page=2),
    Block("table", "A | B\n1 | 2", page=3),
    Block("heading", "2 Next", page=4, level=1),
    Block("paragraph", "Fourth.", page=4),
]


def test_sections_paths_pages_and_paragraph_numbers() -> None:
    chunks = _chunks(SAMPLE)
    assert [c.kind for c in chunks] == ["text", "text", "table", "text"]
    first, detail, table, last = chunks
    assert first.text == "1 Intro\n\nFirst paragraph.\n\nSecond paragraph."
    assert (first.page_start, first.page_end, first.paragraph_start, first.paragraph_end) == (1, 2, 1, 2)
    assert first.reference == "BRD.pdf · p.1–2 · § 1 Intro · ¶1–2"  # noqa: RUF001 - the reference format uses an en dash for ranges
    assert detail.section_path == "1 Intro > 1.1 Detail"
    assert (detail.paragraph_start, detail.paragraph_end) == (3, 3)
    assert table.section_path == "1 Intro > 1.1 Detail" and table.paragraph_start == 4
    assert last.section_path == "2 Next"  # a level-1 heading closes the level-2 section
    assert last.reference == "BRD.pdf · p.4 · § 2 Next · ¶5"


def test_chunk_ids_are_deterministic_and_versioned(monkeypatch: object) -> None:
    first = [c.id for c in _chunks(SAMPLE)]
    assert first == [c.id for c in _chunks(SAMPLE)]
    assert first[0] == chunk_id(SHA, 0) and first[0].startswith("chk_") and len(first[0]) == 30
    other_doc = chunk_blocks(
        SAMPLE, document_sha256="b" * 64, display_name="x", target_chars=1200, max_chars=2000
    )
    assert set(first).isdisjoint(c.id for c in other_doc)
    monkeypatch.setattr(chunker_module, "CHUNKER_VERSION", "2")  # type: ignore[attr-defined]
    assert chunk_id(SHA, 0) != first[0]


def test_paragraphs_are_packed_up_to_the_target() -> None:
    blocks = [Block("paragraph", "x" * 400) for _ in range(5)]
    chunks = _chunks(blocks, target=1000)
    assert [(c.paragraph_start, c.paragraph_end) for c in chunks] == [(1, 2), (3, 4), (5, 5)]


def test_long_paragraphs_are_split_without_losing_text() -> None:
    sentence = "This sentence is part of a very long requirement paragraph. "
    text = (sentence * 80).strip()
    chunks = _chunks([Block("paragraph", text)], target=500, hard_max=800)
    assert len(chunks) > 1
    assert all(len(c.text) <= 800 for c in chunks)
    assert all(c.paragraph_start == c.paragraph_end == 1 for c in chunks)
    assert " ".join(c.text for c in chunks).split() == text.split()


def test_heading_without_body_still_produces_a_chunk() -> None:
    chunks = _chunks([Block("heading", "Empty section", level=1), Block("heading", "Next", level=1)])
    assert [c.text for c in chunks] == ["Empty section", "Next"]
    assert chunks[0].paragraph_start == 0 and "¶" not in chunks[0].reference


def test_documents_without_pages_omit_the_page_reference() -> None:
    chunks = chunk_blocks(
        [Block("paragraph", "Body")],
        document_sha256=SHA,
        display_name="a.docx",
        target_chars=100,
        max_chars=200,
    )
    assert chunks[0].reference == "a.docx · ¶1"
    assert chunks[0].page_start is None


_blocks = st.lists(
    st.one_of(
        st.builds(
            Block,
            kind=st.just("heading"),
            text=st.text(min_size=1, max_size=40).filter(str.strip),
            level=st.integers(1, 4),
        ),
        st.builds(
            Block,
            kind=st.just("paragraph"),
            text=st.text(min_size=1, max_size=3000).filter(str.strip),
            page=st.one_of(st.none(), st.integers(1, 50)),
        ),
        st.builds(Block, kind=st.just("table"), text=st.text(min_size=1, max_size=500).filter(str.strip)),
    ),
    max_size=40,
)


@settings(max_examples=150, deadline=None)
@given(_blocks)
def test_chunking_properties(blocks: list[Block]) -> None:
    chunks = _chunks(blocks, target=600, hard_max=1000)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    assert len({c.id for c in chunks}) == len(chunks)
    assert all(c.text for c in chunks)
    assert all(c.kind != "text" or len(c.text) <= 1000 + 2 + 40 for c in chunks)  # max + heading line
    paragraphs = sum(1 for b in blocks if b.kind != "heading")
    covered = {n for c in chunks for n in range(c.paragraph_start, c.paragraph_end + 1) if n}
    assert covered == set(range(1, paragraphs + 1))  # every paragraph/table is referenced
    assert chunks == _chunks(blocks, target=600, hard_max=1000)  # deterministic
