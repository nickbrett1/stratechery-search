"""Is the vector arm weak because each chunk is too long to have a centre?

The gate (`scripts/vector_gate.py`) says the arm finds 2 of 5 paraphrases. One
candidate cause is cheap to test and one is expensive:

  cheap     a 1,500-character passage is an average of several arguments, so its
            vector sits near the middle of the space and answers a paraphrase
            with "whichever chunk averages nearest". Shorten the text and the
            ordering should sharpen.
  expensive the model is wrong for this corpus, which means a decision about
            what to embed with, not just how much.

This measures the cheap one: **it does not touch the store.** It takes a spread
of real chunks, embeds each at three lengths (full is read from the store, so
only the truncated versions cost anything), and asks whether the known answer
ranks better when its text is cut to 1,024 or 400 characters. Same candidate
pool, same queries — only the document length changes.

Cost: ~12 requests over ~1,000 texts, about $0.0005. If shortness were the fix,
a re-embed of the corpus is the follow-up; if it is not, no re-embed would have
helped and the dollar stays unspent.

    ssh nick@ryzen 'docker exec -i stratechery-search python -' < scripts/measure_chunk_length.py
"""

from __future__ import annotations

import json
import sqlite3

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from stratechery_search import embed, vector_store

INDEX = "/data/index/search.sqlite"
STORE = "/data/index/lancedb"
POOL_TARGET = 500
#: Every 60th chunk, plus every chunk of the five target articles. Deterministic
#: on purpose: a random pool would make two runs incomparable.
STRIDE = 60

TARGETS = [
    "%Aggregation Theory%",
    "%MongoDB%",
    "%Self-Disruption%",
    "%Modularity%",
    "%Sports Bundle%",
]

QUERIES = [
    ("aggregation-power", "who holds the cards once reaching customers is trivial", "%Aggregation Theory%"),
    ("commoditized", "what happens when a product can be copied by anyone", "%MongoDB%"),
    ("self-disruption", "why good products get abandoned by the people who made them", "%Self-Disruption%"),
    ("modularity", "splitting a business into interchangeable parts", "%Modularity%"),
    ("bundling", "selling several things together for one price", "%Sports Bundle%"),
]

#: A document batch is not a query: the query budget (800 ms, derived from
#: query p95) aborts a 96-text request, and inheriting it here was the first
#: thing this script did wrong.
CORPUS = embed.EmbedConfig.for_corpus()

LENGTHS = (1024, 400)


def main() -> int:
    conn = sqlite3.connect(f"file:{INDEX}?mode=ro", uri=True)
    pool = dict(
        conn.execute(
            "SELECT c.chunk_id, c.text FROM chunks c JOIN articles a ON a.message_id = c.message_id"
            " WHERE a.is_transactional = 0 AND c.id % ? = 0 LIMIT ?",
            (STRIDE, POOL_TARGET),
        ).fetchall()
    )
    target_ids: dict[str, set[str]] = {}
    for pattern in TARGETS:
        rows = conn.execute(
            "SELECT c.chunk_id, c.text FROM chunks c JOIN articles a ON a.message_id = c.message_id"
            " WHERE a.subject LIKE ? AND a.is_transactional = 0",
            (pattern,),
        ).fetchall()
        target_ids[pattern] = {row[0] for row in rows}
        pool.update(dict(rows))

    ids = list(pool)
    texts = [pool[chunk_id] for chunk_id in ids]
    print(json.dumps({"pool": len(ids), "targets": {p: len(v) for p, v in target_ids.items()}}))

    # Full-chunk vectors come out of the store: nothing to pay for.
    table = vector_store.open_store(STORE)
    arrow = table.to_arrow()
    sub = arrow.filter(pc.is_in(arrow["chunk_id"], value_set=pa.array(ids)))
    full = {
        chunk_id: np.asarray(vector, dtype=np.float32)
        for chunk_id, vector in zip(sub["chunk_id"].to_pylist(), sub["vector"].to_pylist(), strict=False)
    }
    if len(full) != len(ids):
        print(json.dumps({"warning": "some pool ids are missing from the store", "missing": len(ids) - len(full)}))

    # Only the truncations cost money.
    variants: dict[int, dict[str, np.ndarray]] = {}
    for length in LENGTHS:
        vectors = []
        for start in range(0, len(texts), embed.MAX_BATCH):
            batch = [text[:length] for text in texts[start : start + embed.MAX_BATCH]]
            vectors.extend(
                embed.embed(batch, input_type="search_document", config=CORPUS)
            )
        variants[length] = {
            chunk_id: np.asarray(vector, dtype=np.float32)
            for chunk_id, vector in zip(ids, vectors, strict=False)
        }

    report = []
    for name, query, pattern in QUERIES:
        q = np.asarray(embed.embed([query], input_type="search_query")[0], dtype=np.float32)
        wanted = target_ids[pattern]
        row = {"query": name}
        for label, space in [("full", full), *[(str(length), variants[length]) for length in LENGTHS]]:
            distances = []
            for chunk_id, vector in space.items():
                if chunk_id in full:  # keep the same candidate set in every condition
                    distances.append((float(1.0 - np.dot(q, vector)), chunk_id))
            distances.sort()
            best = next((rank for rank, (_, chunk_id) in enumerate(distances, start=1) if chunk_id in wanted), None)
            target_best = min((d for d, chunk_id in distances if chunk_id in wanted), default=None)
            row[label] = {
                "rank": best,
                "target_distance": round(target_best, 4) if target_best is not None else None,
                "top1_distance": round(distances[0][0], 4) if distances else None,
            }
        report.append(row)
        print(json.dumps(row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
