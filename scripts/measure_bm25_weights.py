"""Why does the literal query `aggregation theory` rank the right article 8th?

Two arms answer that question and only one of them is suspect. The vector arm
puts `aggregation theory` first; BM25 puts the two articles whose *subject* is
"Aggregation Theory" eighth. So the ranking is not confused about what the words
mean — it is confused about where they matter.

The mechanism, measured rather than assumed: `chunks_fts` has two columns,
``text`` and ``subject``, and the shipped weights are ``bm25(chunks_fts, 10.0,
1.0)``. Every chunk of an article carries that article's subject, so a subject
match is worth one occurrence in a column the text outweighs ten to one. For
`aggregation theory` that means:

  308 chunks match both words, and the top 93 sit inside 10% of the score span
  between the best and worst match — a two-term AND over a 29,918-chunk corpus
  is nearly a tie. Inside that tie, an article *named* for the query gets the
  same treatment as a paragraph that mentions it in passing.

This script asks whether that is fixable by weights, and what a change costs. It
runs two suites against the live index, read-only, for a grid of (text, subject)
weights:

  passage queries  200 chunks sampled by stride, each searched with an 8-word
                   window taken from its own middle. The target is the article
                   the window came from, so this measures one thing only: does
                   the change break "find the article that says this"?
                   Reported as top-1, top-5 and MRR over the article's best
                   chunk. This suite exists to catch a regression, not to win.

  topic queries    the nine rows of `scripts/vector_gate.py`, split into the four
                   literal controls and the five paraphrases. Only the controls
                   are ground truth a reader would recognise, and only they are
                   reported as pass/fail; the paraphrases are printed because
                   BM25 is known to fail them and a weight change should not be
                   credited with fixing that.

The passage suite is deliberately biased *towards* text: the query is body
words, so raising the subject weight can only cost it. That is the point — it is
the hurt test. If a subject-heavy weight survives 200 passage queries and fixes
the controls, the change is defensible.

    ssh nick@ryzen 'docker exec -i stratechery-search python -' < scripts/measure_bm25_weights.py
"""

from __future__ import annotations

import json
import sqlite3

INDEX = "/data/index/search.sqlite"

#: Deterministic sample. Every 137th chunk is arbitrary but stable, so two runs
#: of this script are comparable and a change can be attributed.
STRIDE = 137
SAMPLE = 200
#: Words taken from the middle of the passage. The opening lines often echo the
#: subject line, which would make the passage suite another subject test.
WINDOW = 8
WINDOW_SKIP = 4

WEIGHTS = [
    (10.0, 1.0),  # shipped
    (10.0, 3.0),
    (10.0, 10.0),
    (5.0, 10.0),
    (1.0, 10.0),
    (1.0, 5.0),
    (0.0, 10.0),
]

CONTROLS = [
    ("smiling curve", "%Smiling Curve%", "smiling curve"),
    ("TSMC", "%TSMC%", "TSMC"),
    ("aggregation theory", "%Aggregation Theory%", "aggregation theory"),
    ("unbundling", "%Unbundling%", "unbundling"),
]

PARAPHRASES = [
    (
        "aggregation-power",
        "%Aggregation Theory%",
        "who holds the cards once reaching customers is trivial",
    ),
    (
        "commoditized",
        "%MongoDB%",
        "what happens when a product can be copied by anyone",
    ),
    (
        "self-disruption",
        "%Self-Disruption%",
        "why good products get abandoned by the people who made them",
    ),
    ("modularity", "%Modularity%", "splitting a business into interchangeable parts"),
    ("bundling", "%Sports Bundle%", "selling several things together for one price"),
]


def quote(words: list[str]) -> str:
    return " ".join(f'"{word}"' for word in words)


def targets(conn: sqlite3.Connection, pattern: str) -> set[str]:
    rows = conn.execute(
        "SELECT c.chunk_id FROM chunks c JOIN articles a ON a.message_id = c.message_id"
        " WHERE a.subject LIKE ? AND a.is_transactional = 0",
        (pattern,),
    ).fetchall()
    return {row[0] for row in rows}


def best_rank(
    conn: sqlite3.Connection,
    expression: str,
    weight: tuple[float, float],
    wanted: set[str],
) -> int | None:
    """Rank of the first chunk belonging to a wanted article, 1-based."""
    rows = conn.execute(
        "SELECT c.chunk_id FROM chunks_fts"
        " JOIN chunks c ON c.id = chunks_fts.rowid"
        " JOIN articles a ON a.message_id = c.message_id"
        f" WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts, {weight[0]}, {weight[1]})",
        (expression,),
    )
    for rank, (chunk_id,) in enumerate(rows, start=1):
        if chunk_id in wanted:
            return rank
    return None


def passage_sample(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT c.chunk_id, c.text FROM chunks c"
        " JOIN articles a ON a.message_id = c.message_id"
        " WHERE a.is_transactional = 0 AND c.id % ? = 0"
        " ORDER BY c.id LIMIT ?",
        (STRIDE, SAMPLE),
    ).fetchall()
    out = []
    for chunk_id, text in rows:
        words = text.split()
        if len(words) < WINDOW + WINDOW_SKIP + 4:
            continue
        out.append(
            {
                "chunk_id": chunk_id,
                "expression": quote(words[WINDOW_SKIP : WINDOW_SKIP + WINDOW]),
            }
        )
    return out


def main() -> int:
    conn = sqlite3.connect(f"file:{INDEX}?mode=ro", uri=True)

    sample = passage_sample(conn)
    print(json.dumps({"passage_queries": len(sample), "weights": WEIGHTS}))

    suite = []
    for row in sample:
        owner = conn.execute(
            "SELECT message_id FROM chunks WHERE chunk_id = ?", (row["chunk_id"],)
        ).fetchone()[0]
        suite.append({**row, "owner": owner})

    for weight in WEIGHTS:
        top1 = top5 = 0
        reciprocal = 0.0
        for row in suite:
            rows = conn.execute(
                "SELECT c.message_id FROM chunks_fts"
                " JOIN chunks c ON c.id = chunks_fts.rowid"
                f" WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts, {weight[0]}, {weight[1]})",
                (row["expression"],),
            )
            rank = next(
                (
                    i
                    for i, (message_id,) in enumerate(rows, start=1)
                    if message_id == row["owner"]
                ),
                None,
            )
            if rank is None:
                continue
            top1 += rank == 1
            top5 += rank <= 5
            reciprocal += 1 / rank
        total = len(suite)
        print(
            json.dumps(
                {
                    "suite": "passage",
                    "weights": weight,
                    "top1": round(top1 / total, 3),
                    "top5": round(top5 / total, 3),
                    "mrr": round(reciprocal / total, 3),
                }
            ),
            flush=True,
        )

    for label, group in (("control", CONTROLS), ("paraphrase", PARAPHRASES)):
        for name, pattern, query in group:
            wanted = targets(conn, pattern)
            row = {"suite": label, "query": name, "ranks": {}}
            for weight in WEIGHTS:
                key = f"{weight[0]:g}/{weight[1]:g}"
                row["ranks"][key] = best_rank(
                    conn, quote(query.split()), weight, wanted
                )
            print(json.dumps(row), flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
