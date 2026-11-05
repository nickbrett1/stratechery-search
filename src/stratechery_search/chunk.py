"""Char-bounded chunking of normalised article bodies.

Phase-1 scope contract §2.1 fixes the chunk shape: a chunk is a slice of the
*normalised* text, labelled ``<message_id>:<ordinal>``, carrying its
``start_char`` / ``end_char`` so the article view can scroll to it. The chunker
never rewrites prose — the slices concatenate back to the body that was
flattened, which is what makes ``text_sha256`` a meaningful provenance field.

The budget is deliberately character-based, not token-based: phase 1 has no
tokenizer, and ``est_tokens = chars / 4`` is the only token figure allowed to
exist (see §8). Chunks are broken at a paragraph, then a line, then a sentence,
then a space — in that order — so a slice almost never ends mid-word.
"""

from __future__ import annotations

import hashlib

#: Bumped whenever the slicing changes: every chunk row records it.
CHUNKER_VERSION = "1"

TARGET_CHARS = 1500
OVERLAP_CHARS = 150
#: A final chunk shorter than this is merged into its predecessor instead of
#: shipping a three-word tail as its own row.
MIN_TAIL_CHARS = 200

_BREAKS: tuple[tuple[str, int], ...] = (("\n\n", 2), ("\n", 1), (". ", 2), (" ", 1))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _find_break(text: str, start: int, limit: int) -> int:
    """Return the end offset of the last natural break in ``[start, limit)``.

    Falls back to ``limit`` when no break exists in the window, which keeps the
    function total: chunking never loses text to a failed search for a boundary.
    """
    floor = start + (limit - start) // 2
    if limit <= floor:
        return limit
    best = None
    for token, width in _BREAKS:
        index = text.rfind(token, floor, limit)
        if index != -1:
            candidate = index + width
            if best is None or candidate > best:
                best = candidate
        if best is not None:
            # Paragraph/line/sentence preference: the first token that matches
            # at a usable position wins, even if a later token would extend.
            return best
    return limit


def _snap_overlap(text: str, start: int, end: int) -> int:
    """Move an overlap start forward to the next space so no word is split."""
    if start <= 0:
        return 0
    space = text.find(" ", start, end)
    return space + 1 if space != -1 else start


def chunk_text(
    text: str,
    target: int = TARGET_CHARS,
    overlap: int = OVERLAP_CHARS,
) -> list[tuple[int, int, str]]:
    """Slice ``text`` into ``(start, end, slice)`` triples, deterministically."""
    if target <= 0:
        raise ValueError("target must be positive")
    if overlap < 0 or overlap >= target:
        raise ValueError("overlap must be in [0, target)")

    total = len(text)
    if total == 0:
        return []

    slices: list[tuple[int, int, str]] = []
    start = 0
    while start < total:
        limit = min(start + target, total)
        end = _find_break(text, start, limit) if limit < total else total
        if end <= start:  # pathological break placement; fail forward
            end = limit
        slices.append((start, end, text[start:end]))
        if end >= total:
            break
        start = _snap_overlap(text, max(end - overlap, start + 1), end)

    if len(slices) > 1 and len(slices[-1][2]) < MIN_TAIL_CHARS:
        prev_start = slices[-2][0]
        slices[-2:] = [(prev_start, total, text[prev_start:total])]
    return slices


def chunk_article(
    message_id: int,
    text: str,
    subject: str,
) -> list[dict]:
    """Build the ``chunks`` rows for one article, insertion-order stable."""
    rows = []
    for ordinal, (start, end, slice_text) in enumerate(chunk_text(text)):
        rows.append(
            {
                "chunk_id": f"{message_id}:{ordinal}",
                "message_id": message_id,
                "ordinal": ordinal,
                "start_char": start,
                "end_char": end,
                "text": slice_text,
                "subject": subject,
                "embed_model": None,
                "chunker_version": CHUNKER_VERSION,
                "text_sha256": sha256_text(slice_text),
            }
        )
    return rows
