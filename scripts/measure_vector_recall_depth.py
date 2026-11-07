"""Is the vector arm's paraphrase failure a retrieval problem or a ranking one?

Everything tried so far has measured ranks inside a small pool. That answers
"which is better, A or B" but not the question that decides whether the arm can
be improved at all:

  if the right passage is *somewhere deep in the full-corpus ranking* — say rank
  30 of 29,918 — then candidate generation is working and the fix is a better
  judge over a wider window (a cross-encoder rerank, a stronger fusion, a query
  rewrite): real headroom, and the distance to close it is one component.

  if it is at rank 4,000 — then no reranker inside any affordable window will
  ever see it, the representation itself does not contain the answer, and the
  honest conclusion is that this model on 1,500-character passages cannot pull
  these concepts out. That is a design boundary, not a bug.

So this measures the depth profile of the full corpus, per query, and reports
recall@k for k = 1, 5, 10, 25, 50, 100, 200, 500, 1000. The arm is the only
thing being measured: one embed and one brute-force scan per query, no fusion,
no BM25.

    ssh nick@ryzen 'docker exec -i stratechery-search python -' < scripts/measure_vector_recall_depth.py
"""

from __future__ import annotations

import json
import sqlite3

from stratechery_search import embed, vector_store
from stratechery_search.filters import FilterSet

INDEX = "/data/index/search.sqlite"
STORE = "/data/index/lancedb"
DEPTHS = (1, 5, 10, 25, 50, 100, 200, 500, 1000, 2000)
LIMIT = 2000

QUERIES = [
    (
        "paraphrase",
        "aggregation-power",
        "who holds the cards once reaching customers is trivial",
        "%Aggregation Theory%",
    ),
    (
        "paraphrase",
        "commoditized",
        "what happens when a product can be copied by anyone",
        "%MongoDB%",
    ),
    (
        "paraphrase",
        "self-disruption",
        "why good products get abandoned by the people who made them",
        "%Self-Disruption%",
    ),
    (
        "paraphrase",
        "modularity",
        "splitting a business into interchangeable parts",
        "%Modularity%",
    ),
    (
        "paraphrase",
        "bundling",
        "selling several things together for one price",
        "%Sports Bundle%",
    ),
    ("control", "smiling-curve", "smiling curve", "%Smiling Curve%"),
    ("control", "tsmc", "TSMC", "%TSMC%"),
    ("control", "aggregation-theory", "aggregation theory", "%Aggregation Theory%"),
    ("control", "unbundling", "unbundling", "%Unbundling%"),
    # A paraphrase the ARM has been seen to get right (found the foundry/TSMC
    # passages at zero shared keywords). It is the ceiling example: if a query
    # this good lands at rank 3 and a bad one at rank 4,000, the spread is the
    # representation, not the plumbing.
    (
        "paraphrase",
        "foundries",
        "the factory that makes chips for everyone else",
        "%TSMC%",
    ),
]


def targets(conn: sqlite3.Connection, pattern: str) -> set[str]:
    rows = conn.execute(
        "SELECT c.chunk_id FROM chunks c JOIN articles a ON a.message_id = c.message_id"
        " WHERE a.subject LIKE ? AND a.is_transactional = 0",
        (pattern,),
    ).fetchall()
    return {row[0] for row in rows}


def main() -> int:
    conn = sqlite3.connect(f"file:{INDEX}?mode=ro", uri=True)
    table = vector_store.open_store(STORE)

    rows = []
    for kind, name, query, pattern in QUERIES:
        wanted = targets(conn, pattern)
        hits = vector_store.search(
            table,
            embed.embed([query], input_type="search_query")[0],
            filters=FilterSet(),
            limit=LIMIT,
        )
        rank = next(
            (i for i, hit in enumerate(hits, start=1) if hit.chunk_id in wanted), None
        )
        target_distance = hits[rank - 1].distance if rank else None
        rows.append(
            {
                "kind": kind,
                "query": name,
                "text": query,
                "target_rank": rank,
                "top1_distance": round(hits[0].distance, 4),
                "target_distance": round(target_distance, 4)
                if target_distance
                else None,
                "searched": len(hits),
            }
        )
        print(json.dumps(rows[-1]), flush=True)

    print()
    for kind in ("control", "paraphrase"):
        family = [row for row in rows if row["kind"] == kind]
        profile = {
            f"recall@{depth}": sum(
                1
                for row in family
                if row["target_rank"] and row["target_rank"] <= depth
            )
            for depth in DEPTHS
        }
        print(json.dumps({"family": kind, "of": len(family), **profile}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
