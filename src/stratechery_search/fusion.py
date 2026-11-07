"""Reciprocal rank fusion: two orderings in, one ordering out.

The two arms produce scores that cannot be compared — ``bm25(chunks_fts, 2.0,
10.0)`` is negative and unbounded, cosine distance is 0..2 — so fusion is done on
**ranks only**, which discards magnitudes and keeps each arm's intent:

    fused(d) = Σ_arms  1 / (k + rank_arm(d))        k = 60, rank is 1-based

A passage is scored only by the arms that returned it, and that is the whole
safety argument for adding an arm that always returns its full window
regardless of quality: a passage **both** arms agree on outranks a passage only
one arm ranked first. Worked example from the design note — FTS rank 3, vector
rank 40 → ``1/63 + 1/100 = 0.0259``, which beats FTS rank 1 alone (``1/61 =
0.0164``).

Determinism is not a nicety here. Sorting by fused score alone leaves ties in
whatever order the dict happened to be in, so the same query would give two
orders and every future comparison would be noise. The tie-break is
``chunk_id``, always, and it is applied in :func:`fuse`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

#: The standard RRF constant. 60 is from the original paper and is not tuned
#: here — with two arms it mostly controls how quickly a deep rank stops
#: contributing, and at 60 a rank-50 hit still scores ~1/110.
RRF_K = 60

#: Fusion window per arm, before both are fused. 50 at page 1 is ~2.5x the page
#: size; it grows with ``offset`` so deep pages are not empty, and is capped
#: because 200 is 0.7% of this corpus and a wider window would cost more than it
#: could possibly return.
MIN_WINDOW = 50
MAX_WINDOW = 200


def window(offset: int, limit: int) -> int:
    """How many rows each arm returns before fusion.

    ``max(MIN_WINDOW, offset + limit)``, capped at :data:`MAX_WINDOW`. A bare
    ``offset + limit`` would be wrong: a passage at FTS rank 50 and vector rank
    50 fuses into the top 10, so a window of exactly ``offset + limit`` would
    surface it on page 1 and lose it on page 3.
    """
    return min(MAX_WINDOW, max(MIN_WINDOW, offset + limit))


def fuse(arms: Sequence[Iterable[str]], *, offset: int = 0, limit: int | None = None) -> list[tuple[str, float]]:
    """Fuse ranked ``chunk_id`` lists into ``[(chunk_id, fused_score), ...]``.

    Arms are passed in a fixed order (FTS first, vector second) and each is
    already sorted best-first. Returns every fused id, sorted by score
    descending then ``chunk_id`` ascending, so a slice is a page.
    """
    scores: dict[str, float] = {}
    for arm in arms:
        for rank, chunk_id in enumerate(arm, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    if limit is None:
        return ranked[offset:]
    return ranked[offset : offset + limit]


def matched_by(fts: Iterable[str], vector: Iterable[str]) -> dict[str, str]:
    """``chunk_id -> "bm25" | "vector" | "both"``, for the additive field.

    The two arms search different strings — ``expand_query`` rewrites the query
    before FTS, while the vector arm embeds the original text — so which arm
    found a passage is the observable that keeps that asymmetry visible rather
    than something to be "fixed" into sameness.
    """
    in_fts = set(fts)
    in_vector = set(vector)
    labels: dict[str, str] = {}
    for chunk_id in in_fts | in_vector:
        if chunk_id in in_fts and chunk_id in in_vector:
            labels[chunk_id] = "both"
        elif chunk_id in in_fts:
            labels[chunk_id] = "bm25"
        else:
            labels[chunk_id] = "vector"
    return labels
