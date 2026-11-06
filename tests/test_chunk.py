"""Tests for the char-bounded chunker."""

from __future__ import annotations

from stratechery_search.chunk import chunk_article, chunk_text


def _prose(paragraphs: int = 12, words: int = 40) -> str:
    sentence = "The aggregation theory says integration is a choice. " * 6
    block = " ".join([sentence] * max(1, words // 60))
    return "\n\n".join(f"{block} Paragraph {index}." for index in range(paragraphs))


def test_slices_cover_the_text_without_gaps():
    text = _prose()
    slices = chunk_text(text)
    assert slices[0][0] == 0
    assert slices[-1][1] == len(text)
    for previous, nxt in zip(slices, slices[1:], strict=False):
        # Overlapping chunks: the next slice starts at or before the last one ended.
        assert nxt[0] <= previous[1]
        assert nxt[0] > previous[0]
    for _, _, slice_text in slices:
        assert slice_text.strip()


def test_chunks_respect_the_budget():
    text = _prose(paragraphs=20)
    for start, end, _ in chunk_text(text, target=1500):
        assert end - start <= 1600, (start, end)


def test_breaks_prefer_paragraph_boundaries():
    text = "one two three\n\nfour five six\n\nseven eight nine"
    slices = chunk_text(text, target=20, overlap=5)
    # The paragraph break at offset 15 wins over the space at offset 13, and the
    # separator belongs to the chunk it terminates.
    assert slices[0][2] == "one two three\n\n"
    assert "".join(part for _, _, part in slices).replace("\n\n", " ") != ""


def test_short_tail_is_merged_into_the_previous_chunk():
    text = "a" * 1400 + " " + "b" * 40
    slices = chunk_text(text, target=1400, overlap=100)
    assert len(slices) == 1
    assert slices[0][2] == text


def test_chunk_ids_are_ordinal_and_stable():
    text = _prose(paragraphs=8)
    first = chunk_article(152, text, "Subject (Stratechery Update 7-15-2026)")
    second = chunk_article(152, text, "Subject (Stratechery Update 7-15-2026)")
    assert [row["chunk_id"] for row in first] == [
        f"152:{index}" for index in range(len(first))
    ]
    assert [row["text_sha256"] for row in first] == [
        row["text_sha256"] for row in second
    ]
    assert all(row["subject"] for row in first)
    assert all(row["embed_model"] is None for row in first)


def test_empty_text_has_no_chunks():
    assert chunk_text("") == []
