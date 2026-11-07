"""Is cohere/embed-v5.0 simply the wrong model for this corpus?

The gate (`scripts/vector_gate.py`) found 2 of 5 owner-confirmed paraphrases in
the top five. The chunk-length experiment (`scripts/measure_chunk_length.py`)
then ruled out the cheapest explanation: cutting passages to 1,024 or 400
characters made the ranking *worse*, not better, so the dilution story is dead.
What is left is the more expensive kind of question — is the model a poor fit?

That question has a cheap answer as long as the corpus is not re-embedded. This
takes the **same candidate pool** as the chunk-length run (every 60th passage,
plus every passage of the five target articles — 639 rows), embeds it with a
second model, and ranks the same nine queries in both spaces. Same texts, same
queries, same targets: only the embedding model changes.

The comparison is like-for-like because the cohere vectors come out of the
store (free, already paid for) and the pool is small enough that the second
model costs about four cents rather than the ~$1 of a corpus pass. If the
second model separates the targets where cohere does not, the corpus pass is
worth planning. If it does not either, the embedding arm is being asked for
something no model does with 1,500-character passages, and the next move is a
retrieval design change, not a re-embed.

Model: `gemini-embedding-001` (3,072 dims, retrieval task types, $0.15/1M
tokens). Chosen because the gateway already holds a Gemini key and because the
task-type parameter is the closest analogue to the `input_type` lesson already
learned here — a query embedded as a document cost 0.54 cosine against 0.53 for
the correct pair.

    ssh nick@ryzen 'docker exec -e GEMINI_API_KEY="$(docker exec litellm printenv GEMINI_API_KEY)" -i stratechery-search python -' < scripts/measure_embed_model.py

Never hard-code the key; it is read from the environment, and the shell above
pulls it from the gateway container without printing it.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.error
import urllib.request

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from stratechery_search import embed, vector_store

INDEX = "/data/index/search.sqlite"
STORE = "/data/index/lancedb"

#: Same pool rules as the chunk-length experiment, so a row can be read across
#: both scripts.
POOL_TARGET = 500
STRIDE = 60

MODEL = "gemini-embedding-001"
ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents"
)
#: Requests per batch call, not texts per request: the API batches one text per
#: request object and caps the batch at 100. Sixteen passages of ~400 tokens
#: keeps each call well under the 20,000-token batch limit.
BATCH = 16

#: The nine rows of `scripts/vector_gate.py`, unchanged and in the same order.
#: Kept here rather than imported because the scripts are piped into the
#: container's stdin and are not a package — they must be kept in step by hand.
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
    ("control", "control-smiling-curve", "smiling curve", "%Smiling Curve%"),
    ("control", "control-tsmc", "TSMC", "%TSMC%"),
    (
        "control",
        "control-aggregation-theory",
        "aggregation theory",
        "%Aggregation Theory%",
    ),
    ("control", "control-unbundling", "unbundling", "%Unbundling%"),
]


def _post(body: dict, *, attempts: int = 6) -> dict:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise SystemExit(
            "GEMINI_API_KEY is not set; see the docstring for the invocation"
        )
    url = ENDPOINT.format(model=MODEL)
    payload = json.dumps(body).encode()
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json", "x-goog-api-key": key},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode()[:400]
            if error.code in (429, 500, 503) and attempt < attempts:
                time.sleep(min(2**attempt, 30))
                continue
            raise SystemExit(f"gemini {error.code}: {detail}") from error
        except urllib.error.URLError as error:
            if attempt < attempts:
                time.sleep(min(2**attempt, 30))
                continue
            raise SystemExit(f"gemini unreachable: {error}") from error
    raise SystemExit("gemini: retries exhausted")


def embed_texts(texts: list[str], task_type: str) -> np.ndarray:
    vectors: list[list[float]] = []
    for start in range(0, len(texts), BATCH):
        chunk = texts[start : start + BATCH]
        body = {
            "requests": [
                {
                    "model": f"models/{MODEL}",
                    "content": {"parts": [{"text": text}]},
                    "taskType": task_type,
                }
                for text in chunk
            ]
        }
        embeddings = _post(body)["embeddings"]
        if len(embeddings) != len(chunk):
            raise SystemExit(
                f"asked for {len(chunk)} embeddings, received {len(embeddings)}"
            )
        vectors.extend(item["values"] for item in embeddings)
        print(json.dumps({"embedded": len(vectors)}), flush=True)
    matrix = np.asarray(vectors, dtype=np.float32)
    return matrix / np.linalg.norm(matrix, axis=1, keepdims=True)


def _normalise(matrix: np.ndarray) -> np.ndarray:
    return matrix / np.linalg.norm(matrix, axis=1, keepdims=True)


def ranks(
    query: np.ndarray, ids: list[str], space: np.ndarray, wanted: set[str]
) -> dict:
    similarity = space @ query
    order = np.argsort(-similarity)
    ranked = [ids[index] for index in order]
    best = next(
        (rank for rank, chunk_id in enumerate(ranked, start=1) if chunk_id in wanted),
        None,
    )
    target_best = max(
        (float(similarity[i]) for i, chunk_id in enumerate(ids) if chunk_id in wanted),
        default=None,
    )
    return {
        "rank": best,
        "target_similarity": round(target_best, 4) if target_best is not None else None,
        "top1_similarity": round(float(similarity[order[0]]), 4),
        "margin": round(float(similarity[order[0]]) - target_best, 4)
        if target_best is not None
        else None,
    }


def main() -> int:
    conn = sqlite3.connect(f"file:{INDEX}?mode=ro", uri=True)
    pool = dict(
        conn.execute(
            "SELECT c.chunk_id, c.text FROM chunks c JOIN articles a ON a.message_id = c.message_id"
            " WHERE a.is_transactional = 0 AND c.id % ? = 0 LIMIT ?",
            (STRIDE, POOL_TARGET),
        ).fetchall()
    )
    targets: dict[str, set[str]] = {}
    for _, _, _, pattern in QUERIES:
        if pattern in targets:
            continue
        rows = conn.execute(
            "SELECT c.chunk_id, c.text FROM chunks c JOIN articles a ON a.message_id = c.message_id"
            " WHERE a.subject LIKE ? AND a.is_transactional = 0",
            (pattern,),
        ).fetchall()
        targets[pattern] = {row[0] for row in rows}
        pool.update(dict(rows))

    ids = list(pool)
    texts = [pool[chunk_id] for chunk_id in ids]
    print(
        json.dumps(
            {
                "pool": len(ids),
                "targets": {p: len(v) for p, v in targets.items()},
                "model": MODEL,
            }
        )
    )

    # cohere's side is already paid for: read it, do not re-embed it.
    table = vector_store.open_store(STORE)
    arrow = table.to_arrow()
    sub = arrow.filter(pc.is_in(arrow["chunk_id"], value_set=pa.array(ids)))
    stored = {
        chunk_id: np.asarray(vector, dtype=np.float32)
        for chunk_id, vector in zip(
            sub["chunk_id"].to_pylist(), sub["vector"].to_pylist(), strict=False
        )
    }
    missing = [chunk_id for chunk_id in ids if chunk_id not in stored]
    if missing:
        print(
            json.dumps(
                {"warning": "pool ids missing from the store", "missing": len(missing)}
            )
        )
        ids = [chunk_id for chunk_id in ids if chunk_id in stored]
        texts = [pool[chunk_id] for chunk_id in ids]
    cohere_space = _normalise(np.stack([stored[chunk_id] for chunk_id in ids]))

    gemini_docs = embed_texts(texts, "RETRIEVAL_DOCUMENT")
    if gemini_docs.shape[0] != len(ids):
        raise SystemExit("gemini returned a different number of document vectors")

    report = []
    for kind, name, query, pattern in QUERIES:
        wanted = targets[pattern] & set(ids)
        if not wanted:
            print(json.dumps({"query": name, "skipped": "no target in the pool"}))
            continue
        # The query is one short text: the gateway path is what production uses,
        # and it is already budgeted for a single query, unlike a document batch.
        cohere_query = _normalise(
            np.asarray(
                [embed.embed([query], input_type="search_query")[0]], dtype=np.float32
            )
        )[0]
        gemini_query = embed_texts([query], "RETRIEVAL_QUERY")[0]
        row = {
            "kind": kind,
            "query": name,
            "text": query,
            "targets_in_pool": len(wanted),
            "cohere": ranks(cohere_query, ids, cohere_space, wanted),
            "gemini": ranks(gemini_query, ids, gemini_docs, wanted),
        }
        report.append(row)
        print(json.dumps(row), flush=True)

    def summary(kind: str, key: str, cutoff: int) -> dict:
        rows = [row for row in report if row["kind"] == kind]
        hits = [row for row in rows if (row[key]["rank"] or 10**9) <= cutoff]
        return {
            "hits": len(hits),
            "of": len(rows),
            "ranks": [row[key]["rank"] for row in rows],
        }

    print(
        json.dumps(
            {
                "summary": {
                    "paraphrase_recall@5": {
                        "cohere": summary("paraphrase", "cohere", 5),
                        "gemini": summary("paraphrase", "gemini", 5),
                    },
                    "control_recall@2": {
                        "cohere": summary("control", "cohere", 2),
                        "gemini": summary("control", "gemini", 2),
                    },
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
