"""Does the vector arm earn its ~190 ms? Rank the known answer, three ways.

Run inside the serving container, where the store and the index live::

    ssh nick@ryzen 'docker exec -i stratechery-search python -' < scripts/vector_gate.py
    ssh nick@ryzen 'docker exec -i stratechery-search python -' < scripts/vector_gate.py --emit-json

The set was built with the owner on 2026-11-07 and the ground truth is his, not
mine: each query describes an idea, and the target is the article he says should
answer it. Two properties make a row worth having:

**BM25 should fail it.** A paraphrase shares few or no content words with the
passage that answers it, so an AND-of-terms search returns nothing relevant.
That is the whole test — if BM25 already finds the article, the row measures
nothing about embeddings.

**The words are checked, not assumed.** ``shared`` in the output is the overlap
between the query and the target article's text. A row that turns out to share
three words is weaker evidence than its author thought, so it is printed rather
than buried.

Rejected rows are recorded with their reasons instead of deleted: two of the
first eight were wrong in ways worth remembering (an unrelated article, and a
target picked by word frequency rather than meaning).

Pass criteria, agreed before the first run: paraphrase recall@5 >= 4/5 and no
control failure. A control failing means a bug, not weakness — the arm cannot be
judged while a literal query misses a known article.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

from stratechery_search import api, embed, vector_store
from stratechery_search.filters import FilterSet

STORE = Path("/data/index/lancedb")
INDEX = Path("/data/index/search.sqlite")

#: ``kind`` is "paraphrase" (the actual test) or "control" (a literal query the
#: arm has to get right, whatever else happens).
#:
#: ``subject_like`` resolves the target at run time rather than pinning a
#: message_id: the same set has to survive a rebuild, and a stale id would look
#: exactly like a retrieval failure.
QUERIES = [
    {
        "id": "aggregation-power",
        "kind": "paraphrase",
        "query": "who holds the cards once reaching customers is trivial",
        "subject_like": "%Aggregation Theory%",
        "note": "Reworded from 'demand is easy to reach' to drop two shared words.",
    },
    {
        "id": "commoditized",
        "kind": "paraphrase",
        "query": "what happens when a product can be copied by anyone",
        "subject_like": "%MongoDB%",
        "note": "Re-pointed by the owner from 'Email Addresses and Razor Blades'.",
    },
    {
        "id": "self-disruption",
        "kind": "paraphrase",
        "query": "why good products get abandoned by the people who made them",
        "subject_like": "%Self-Disruption%",
        "note": "Owner-confirmed. Shares the word 'products'.",
    },
    {
        "id": "modularity",
        "kind": "paraphrase",
        "query": "splitting a business into interchangeable parts",
        "subject_like": "%Modularity%",
        "note": "Owner-confirmed. No shared content words.",
    },
    {
        "id": "bundling",
        "kind": "paraphrase",
        "query": "selling several things together for one price",
        "subject_like": "%Sports Bundle%",
        "note": "Owner confirmed the Sports Bundle roundup is the target, not The Great Unbundling.",
    },
    {
        "id": "control-smiling-curve",
        "kind": "control",
        "query": "smiling curve",
        "subject_like": "%Smiling Curve%",
        "note": "Literal. Both arms must rank an article 1st or 2nd.",
    },
    {
        "id": "control-tsmc",
        "kind": "control",
        "query": "TSMC",
        "subject_like": "%TSMC%",
        "note": "Literal, high-frequency. The vocab expansion makes this easy for FTS.",
    },
    {
        "id": "control-aggregation-theory",
        "kind": "control",
        "query": "aggregation theory",
        "subject_like": "%Aggregation Theory%",
        "note": "The literal twin of aggregation-power: if this fails, nothing else means anything.",
    },
    {
        "id": "control-unbundling",
        "kind": "control",
        "query": "unbundling",
        "subject_like": "%Unbundling%",
        "note": "Literal twin of bundling.",
    },
]

#: Rows the owner rejected, kept so the next person does not re-derive them.
REJECTED = [
    {
        "query": "why the assembly step is where all the money leaks out",
        "subject_like": "%Smiling Curve%",
        "reason": "Owner: unrelated. The article picked (an AWS/re:Invent roundup) is not the smiling-curve essay.",
    },
    {
        "query": "a business buying up the stages it used to buy from others",
        "subject_like": "%Integration%",
        "reason": "Owner: incorrect target. Zillow/Aggregation/Integration was not the article meant.",
    },
    {
        "query": "doing the unattractive work nobody else wants to do",
        "subject_like": "%Strateg%",
        "reason": "Dropped: 'strategic' was a word-frequency pick, not a concept. The article was a deal roundup.",
    },
]

LIMIT = 100
STOP = {
    "what", "when", "why", "does", "with", "that", "they", "them", "their",
    "from", "into", "than", "have", "your", "this", "were", "will", "would",
}  # fmt: skip


def content_words(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z]{5,}", text.lower()) if word not in STOP}


def targets(conn: sqlite3.Connection, pattern: str) -> tuple[set[str], set[str]]:
    """``(chunk_ids, article_texts)`` for every article whose subject matches."""
    rows = conn.execute(
        "SELECT c.chunk_id, c.text FROM chunks c JOIN articles a ON a.message_id = c.message_id"
        " WHERE a.subject LIKE ? AND a.is_transactional = 0",
        (pattern,),
    ).fetchall()
    return {row[0] for row in rows}, {row[1] for row in rows}


def best_rank(ids: list[str], wanted: set[str]) -> int | None:
    for position, chunk_id in enumerate(ids, start=1):
        if chunk_id in wanted:
            return position
    return None


def run() -> dict:
    conn = sqlite3.connect(f"file:{INDEX}?mode=ro", uri=True)
    try:
        table = vector_store.open_store(STORE)
        backend = vector_store.Backend(STORE, conn)
        rows = []
        for item in QUERIES:
            chunk_ids, texts = targets(conn, item["subject_like"])
            if not chunk_ids:
                rows.append({**item, "error": "no article matches the subject pattern"})
                continue

            bm25 = api.search(
                conn,
                api.parse_search_params(
                    {"q": [item["query"]], "limit": [str(LIMIT)], "hybrid": ["0"]}
                ),
                {},
            )
            vector = [hit.chunk_id for hit in vector_store.search(
                table,
                embed.embed([item["query"]], input_type="search_query")[0],
                filters=FilterSet(),
                limit=LIMIT,
            )]
            fused = api.search(
                conn,
                api.parse_search_params(
                    {"q": [item["query"]], "limit": [str(LIMIT)], "group": ["article"]}
                ),
                {},
                hybrid=backend,
            )
            fused_ids = [row["chunk_id"] for row in fused["results"]]

            shared = sorted(set().union(*(content_words(text) for text in texts)) & content_words(item["query"]))
            rows.append(
                {
                    **item,
                    "articles": len({text for text in texts}),
                    "shared": shared,
                    "bm25_rank": best_rank([r["chunk_id"] for r in bm25["results"]], chunk_ids),
                    "vector_rank": best_rank(vector, chunk_ids),
                    "fused_rank": best_rank(fused_ids, chunk_ids),
                    "vector_distance": fused.get("hybrid", {}).get("top1_distance"),
                }
            )
        return {"rows": rows, "rejected": REJECTED}
    finally:
        conn.close()


def report(result: dict) -> int:
    def cell(value):
        return "—" if value is None else str(value)

    print(f"{'id':28} {'kind':10} {'bm25':>5} {'vector':>7} {'fused':>6}  shared")
    for row in result["rows"]:
        if "error" in row:
            print(f"{row['id']:28} {row['kind']:10} ERROR: {row['error']}")
            continue
        print(
            f"{row['id']:28} {row['kind']:10} {cell(row['bm25_rank']):>5}"
            f" {cell(row['vector_rank']):>7} {cell(row['fused_rank']):>6}"
            f"  {','.join(row['shared']) or '-'}"
        )

    paraphrases = [row for row in result["rows"] if row["kind"] == "paraphrase" and "error" not in row]
    controls = [row for row in result["rows"] if row["kind"] == "control" and "error" not in row]
    hit = lambda row: row["vector_rank"] is not None and row["vector_rank"] <= 5  # noqa: E731
    print()
    print(f"paraphrase vector recall@5: {sum(map(hit, paraphrases))}/{len(paraphrases)}")
    print(f"control   vector recall@2: "
          f"{sum(1 for r in controls if r['vector_rank'] and r['vector_rank'] <= 2)}/{len(controls)}")
    print(f"control   bm25   recall@2: "
          f"{sum(1 for r in controls if r['bm25_rank'] and r['bm25_rank'] <= 2)}/{len(controls)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--emit-json", action="store_true", help="print the set and exit, no spend")
    args = parser.parse_args(argv)
    if args.emit_json:
        print(json.dumps({"queries": QUERIES, "rejected": REJECTED}, indent=2))
        return 0
    return report(run())


if __name__ == "__main__":
    sys.exit(main())
