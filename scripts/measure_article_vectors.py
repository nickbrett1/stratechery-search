"""Is a concept an article or a passage? Test the unit, not the model.

Every gate in this project measures *articles* while the arm retrieves
*passages*, and the depth profile says the cost of that mismatch is large: the
right article's best passage sits at rank 2, 3, 7, 69, 323 and 385 of 29,918
(`scripts/measure_vector_recall_depth.py`). Two of those are unreachable by any
window this service can afford.

The owner's argument, and the hypothesis here: **a concept is made over an
article, not over a passage.** 1,500 characters is one turn of an argument; the
argument is the article. If that is right, an article-level vector should rank
the target article far better than the best of its passage vectors does, and the
fix is a second table rather than a different model.

Method: embed **every searchable article once** — subject, then the body
truncated to :data:`ARTICLE_CHARS` — and rank all articles for each query. Then
rank the *same* articles the way the passage arm sees them (each article's best
passage) so both columns answer the identical question: "where is the article
among the 1,647 articles?" Ranks are then comparable, which they would not be
against 29,918 passages.

Cost: ~1,650 articles × ~1,400 tokens ≈ 2.3M tokens ≈ $0.28, plus one query
embed per query. Nothing is written to the store — this is a measurement, and if
it wins, building the table is a separate, deliberate change.

    ssh nick@ryzen 'docker exec -i stratechery-search python -' < scripts/measure_article_vectors.py

Cost knobs: ``ARTICLE_CHARS`` (0 = whole article, ~$1.00), ``EMBED_BATCH``
(documents per request), ``VECTORS_OUT`` (keep the article space as ``.npy``).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3

import numpy as np

from stratechery_search import embed, vector_store

INDEX = "/data/index/search.sqlite"
STORE = "/data/index/lancedb"

#: How much of the body goes into the article vector. Cohere accepts 128k, so
#: this is a cost decision, not a limit: 6,000 characters keeps the whole
#: experiment under thirty cents, and the lede, the claim and the first turn of
#: the argument all live inside it. ``ARTICLE_CHARS=0`` means the whole article
#: (~8M tokens, ~$1.00) — the only version that can answer the question for a
#: multi-topic daily update, where the concept is in a section, not the lede.
ARTICLE_CHARS = int(os.environ.get("ARTICLE_CHARS", "6000")) or None

#: Documents per embedding request. The 96 cap is per-request *count*, and a
#: whole article is ~20k characters, so a full batch would be 2M characters in
#: one call. Drop it to keep each request to a few tens of thousands of tokens.
BATCH = int(os.environ.get("EMBED_BATCH", str(embed.MAX_BATCH)))

SUBJECT_SAMPLE = 120
SUBJECT_STRIDE = 11

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
    (
        "paraphrase",
        "foundries",
        "the factory that makes chips for everyone else",
        "%TSMC%",
    ),
    ("control", "smiling-curve", "smiling curve", "%Smiling Curve%"),
    ("control", "tsmc", "TSMC", "%TSMC%"),
    ("control", "aggregation-theory", "aggregation theory", "%Aggregation Theory%"),
    ("control", "unbundling", "unbundling", "%Unbundling%"),
]


def normalise(matrix: np.ndarray) -> np.ndarray:
    return matrix / np.linalg.norm(matrix, axis=1, keepdims=True)


def article_bodies(conn: sqlite3.Connection) -> dict[int, tuple[str, str]]:
    """``message_id -> (subject, full body)`` for every searchable article."""
    bodies: dict[int, list[str]] = {}
    subjects: dict[int, str] = {}
    for message_id, subject, text in conn.execute(
        "SELECT c.message_id, a.subject, c.text FROM chunks c"
        " JOIN articles a ON a.message_id = c.message_id"
        " WHERE a.is_transactional = 0 ORDER BY c.message_id, c.id"
    ):
        bodies.setdefault(message_id, []).append(text or "")
        subjects[message_id] = subject or ""
    return {mid: (subjects[mid], "".join(parts)) for mid, parts in bodies.items()}


def target_articles(conn: sqlite3.Connection, pattern: str) -> set[int]:
    rows = conn.execute(
        "SELECT message_id FROM articles WHERE subject LIKE ? AND is_transactional = 0",
        (pattern,),
    ).fetchall()
    return {row[0] for row in rows}


def main() -> int:
    conn = sqlite3.connect(f"file:{INDEX}?mode=ro", uri=True)
    corpus = article_bodies(conn)
    ids = sorted(corpus)
    print(
        json.dumps(
            {"articles": len(ids), "article_chars": ARTICLE_CHARS, "batch": BATCH}
        )
    )

    documents = [f"{corpus[mid][0]}\n\n{corpus[mid][1][:ARTICLE_CHARS]}" for mid in ids]
    config = embed.EmbedConfig.for_corpus()
    vectors: list[list[float]] = []
    for start in range(0, len(documents), BATCH):
        batch = documents[start : start + BATCH]
        vectors.extend(embed.embed(batch, input_type="search_document", config=config))
        print(json.dumps({"embedded": len(vectors)}), flush=True)
    article_space = normalise(np.asarray(vectors, dtype=np.float32))
    if dump := os.environ.get("VECTORS_OUT"):
        # Embedding 1,647 articles costs real money; keep the space so a second
        # question about the same vectors does not pay for it twice.
        np.save(dump, article_space)
        np.save(dump + ".ids.npy", np.asarray(ids, dtype=np.int64))
        print(json.dumps({"saved": dump}), flush=True)

    # The passage arm's view of the same articles: each article's nearest
    # passage. Computed over the full store, so the comparison is of retrieval
    # units and not of window sizes.
    stored = vector_store.open_store(STORE).to_arrow()
    passage_ids = stored["chunk_id"].to_pylist()
    passage_vectors = normalise(
        np.asarray(stored["vector"].to_pylist(), dtype=np.float32)
    )
    passage_articles = np.asarray(
        [int(chunk_id.split(":")[0]) for chunk_id in passage_ids], dtype=np.int64
    )

    rows = [{"query": q, "pattern": p, "kind": k, "name": n} for k, n, q, p in QUERIES]
    rows += [
        {
            "query": re.sub(r"\s*\(Stratechery[^)]*\)\s*$", "", subject).strip(),
            "pattern": None,
            "kind": "subject",
            "name": f"subject:{message_id}",
            "message_id": message_id,
        }
        for message_id, subject in conn.execute(
            "SELECT message_id, subject FROM articles"
            " WHERE is_transactional = 0 AND subject IS NOT NULL AND subject != ''"
            " AND message_id % ? = 0 ORDER BY message_id LIMIT ?",
            (SUBJECT_STRIDE, SUBJECT_SAMPLE),
        ).fetchall()
    ]

    for row in rows:
        if row["pattern"] is not None:
            wanted = target_articles(conn, row["pattern"])
        else:
            wanted = {row["message_id"]}
        query = normalise(
            np.asarray(
                [embed.embed([row["query"]], input_type="search_query")[0]],
                dtype=np.float32,
            )
        )[0]

        article_similarity = article_space @ query
        article_order = np.argsort(-article_similarity)
        article_rank = next(
            (
                i
                for i, index in enumerate(article_order, start=1)
                if ids[index] in wanted
            ),
            None,
        )

        best: dict[int, float] = {}
        for message_id, similarity in zip(
            passage_articles, passage_vectors @ query, strict=True
        ):
            message_id = int(message_id)
            if similarity > best.get(message_id, -2.0):
                best[message_id] = float(similarity)
        shared = wanted & set(best)
        passage_rank = (
            1 + sum(1 for v in best.values() if v > max(best[m] for m in shared))
            if shared
            else None
        )

        row.update(
            {
                "article_rank": article_rank,
                "passage_rank": passage_rank,
                "article_distance": round(
                    1 - float(article_similarity[article_order[0]]), 4
                ),
                "article_target_distance": round(
                    1
                    - float(
                        max(
                            article_similarity[ids.index(m)] for m in wanted if m in ids
                        )
                    ),
                    4,
                )
                if wanted & set(ids)
                else None,
            }
        )
        print(json.dumps(row), flush=True)

    for kind in ("control", "paraphrase", "subject"):
        family = [row for row in rows if row["kind"] == kind]
        print(
            json.dumps(
                {
                    "family": kind,
                    "of": len(family),
                    "article_recall@1": sum(
                        1 for r in family if r["article_rank"] == 1
                    ),
                    "article_recall@5": sum(
                        1
                        for r in family
                        if r["article_rank"] and r["article_rank"] <= 5
                    ),
                    "article_recall@10": sum(
                        1
                        for r in family
                        if r["article_rank"] and r["article_rank"] <= 10
                    ),
                    "passage_recall@1": sum(
                        1 for r in family if r["passage_rank"] == 1
                    ),
                    "passage_recall@5": sum(
                        1
                        for r in family
                        if r["passage_rank"] and r["passage_rank"] <= 5
                    ),
                    "passage_recall@10": sum(
                        1
                        for r in family
                        if r["passage_rank"] and r["passage_rank"] <= 10
                    ),
                    "article_ranks": [r["article_rank"] for r in family],
                    "passage_ranks": [r["passage_rank"] for r in family],
                }
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
