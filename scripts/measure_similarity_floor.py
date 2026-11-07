"""Should the vector arm be allowed to vote when it is not confident?

Fusion has no similarity floor: every hit the vector arm returns earns
``1/(60 + rank)``, so a passage the arm returns at rank 5 because it is merely
the nearest of a bad set is worth ``1/65``, and two of those outvote a passage
BM25 put first (``1/61``). That is not hypothetical — `unbundling` in production
fuses bm25 1st / vector 5th to **3rd**, and the two passages above it are
``matched_by: both``, i.e. mediocre agreements beating an outright best answer.

The question is whether a threshold can tell "the arm found it" from "the arm
returned its window anyway". Earlier probes said the distances barely separate:
0.56 when a target is found, 0.70–0.85 when it is missed, 0.74 for the nearest
wrong chunk. That is a thin gap, so this measures it properly before anything is
changed:

  subject family   ~120 articles addressed by their own subject line. The arm
                   *should* succeed at these, so they supply the distribution of
                   "found" distances.
  gate family      the 4 controls and the 5 owner-confirmed paraphrases, i.e.
                   the cases where the arm is known to succeed and to fail.

For every query both arms are computed once, then every candidate policy is
replayed over the same cached ranks — no re-embedding per policy, no re-querying
the corpus.

Policies are of two kinds, because they answer different questions:

  absolute   keep vector hits with distance <= F. The only kind that can silence
             a wholly bad set, which is the case that causes the demotion.
  relative   keep hits within M of the arm's own best. Scale-free, but it keeps
             a bad query's whole window — a confident wrong answer looks exactly
             like a confident right one.

    ssh nick@ryzen 'docker exec -i stratechery-search python -' < scripts/measure_similarity_floor.py
"""

from __future__ import annotations

import json
import re
import sqlite3

from stratechery_search import api, embed, fusion, vector_store
from stratechery_search.filters import FilterSet

INDEX = "/data/index/search.sqlite"
STORE = "/data/index/lancedb"
#: The production first-page window (`fusion.window(0, 20)`), because the
#: demotion this script exists to explain depends on it: a wider window admits
#: more vector-only noise, and the effect is not window-invariant.
LIMIT = 50
SUBJECT_SAMPLE = 120
SUBJECT_STRIDE = 11

CONTROLS = [
    ("smiling curve", "%Smiling Curve%"),
    ("TSMC", "%TSMC%"),
    ("aggregation theory", "%Aggregation Theory%"),
    ("unbundling", "%Unbundling%"),
]
PARAPHRASES = [
    (
        "aggregation-power",
        "who holds the cards once reaching customers is trivial",
        "%Aggregation Theory%",
    ),
    (
        "commoditized",
        "what happens when a product can be copied by anyone",
        "%MongoDB%",
    ),
    (
        "self-disruption",
        "why good products get abandoned by the people who made them",
        "%Self-Disruption%",
    ),
    ("modularity", "splitting a business into interchangeable parts", "%Modularity%"),
    ("bundling", "selling several things together for one price", "%Sports Bundle%"),
]

#: (label, absolute floor or None, relative margin or None, depth cap or None)
#:
#: The cap is the third idea and the one the distances actually support: if a
#: confident arm's head is good and its tail is noise, cut the tail rather than
#: threshold the head. It also cannot ampute a correct answer that sits at 0.66.
POLICIES = [
    ("none", None, None, None),
    ("abs 0.60", 0.60, None, None),
    ("abs 0.65", 0.65, None, None),
    ("abs 0.70", 0.70, None, None),
    ("abs 0.75", 0.75, None, None),
    ("rel 0.03", None, 0.03, None),
    ("rel 0.06", None, 0.06, None),
    ("rel 0.10", None, 0.10, None),
    ("0.70+rel 0.05", 0.70, 0.05, None),
    ("0.75+rel 0.05", 0.75, 0.05, None),
    ("vec top 5", None, None, 5),
    ("vec top 10", None, None, 10),
    ("vec top 25", None, None, 25),
]


def subject_queries(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT message_id, subject FROM articles"
        " WHERE is_transactional = 0 AND subject IS NOT NULL AND subject != ''"
        " AND message_id % ? = 0 ORDER BY message_id LIMIT ?",
        (SUBJECT_STRIDE, SUBJECT_SAMPLE),
    ).fetchall()
    out = []
    for message_id, subject in rows:
        topic = re.sub(r"\s*\(Stratechery[^)]*\)\s*$", "", subject).strip()
        out.append({"query": topic, "pattern": None, "message_id": message_id})
    return out


def targets_for(conn: sqlite3.Connection, row: dict) -> set[str]:
    if row["pattern"] is not None:
        rows = conn.execute(
            "SELECT c.chunk_id FROM chunks c JOIN articles a ON a.message_id = c.message_id"
            " WHERE a.subject LIKE ? AND a.is_transactional = 0",
            (row["pattern"],),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT chunk_id FROM chunks WHERE message_id = ?", (row["message_id"],)
        ).fetchall()
    return {r[0] for r in rows}


def measure(conn, table, row: dict) -> dict:
    phrase = row["query"]
    bm25_ids = [
        hit["chunk_id"]
        for hit in api.search(
            conn,
            api.parse_search_params(
                {"q": [phrase], "limit": [str(LIMIT)], "hybrid": ["0"]}
            ),
            {},
        )["results"]
    ]
    hits = vector_store.search(
        table,
        embed.embed([phrase], input_type="search_query")[0],
        filters=FilterSet(),
        limit=LIMIT,
    )
    wanted = targets_for(conn, row)
    return {
        "query": phrase,
        "wanted": wanted,
        "bm25": bm25_ids,
        "vector": [(hit.chunk_id, hit.distance) for hit in hits],
        "bm25_target_rank": first_rank(bm25_ids, wanted),
    }


def admit(sample: dict, floor: float | None, margin: float | None) -> list[str]:
    hits = sample["vector"]
    if not hits:
        return []
    best = hits[0][1]
    kept = []
    for chunk_id, distance in hits:
        if floor is not None and distance > floor:
            continue
        if margin is not None and distance > best + margin:
            continue
        kept.append(chunk_id)
    return kept


def first_rank(ids: list[str], wanted: set[str]) -> int | None:
    return next(
        (i for i, chunk_id in enumerate(ids, start=1) if chunk_id in wanted), None
    )


def main() -> int:
    conn = sqlite3.connect(f"file:{INDEX}?mode=ro", uri=True)
    table = vector_store.open_store(STORE)

    rows = [{"query": q, "pattern": p, "family": "control"} for q, p in CONTROLS]
    rows += [
        {"query": q, "pattern": p, "family": "paraphrase"} for _, q, p in PARAPHRASES
    ]
    rows += [
        {
            "query": row["query"],
            "pattern": None,
            "family": "subject",
            "message_id": row["message_id"],
        }
        for row in subject_queries(conn)
    ]
    print(json.dumps({"queries": len(rows)}))

    samples = []
    for index, row in enumerate(rows, start=1):
        sample = measure(conn, table, row)
        sample["family"] = row["family"]
        samples.append(sample)
        if index % 25 == 0:
            print(json.dumps({"measured": index}), flush=True)

    found = [s for s in samples if s["family"] == "subject"]
    hit_dists = [
        min((d for c, d in s["vector"] if c in s["wanted"]), default=None)
        for s in found
    ]
    print(
        json.dumps(
            {
                "subject_hit_distances": {
                    "found": sum(1 for d in hit_dists if d is not None),
                    "of": len(found),
                    "p50": round(
                        sorted(d for d in hit_dists if d is not None)[
                            len(hit_dists) // 2
                        ],
                        4,
                    ),
                    "worst": round(max(d for d in hit_dists if d is not None), 4),
                },
                "subject_top1_distance": {
                    "p50": round(
                        sorted(s["vector"][0][1] for s in found)[len(found) // 2], 4
                    ),
                    "worst": round(max(s["vector"][0][1] for s in found), 4),
                },
            }
        )
    )

    subject_rows = [s for s in samples if s["family"] == "subject"]
    none_fused_top1 = sum(
        1
        for s in subject_rows
        if first_rank(
            [
                chunk_id
                for chunk_id, _ in fusion.fuse([s["bm25"], [c for c, _ in s["vector"]]])
            ],
            s["wanted"],
        )
        == 1
    )
    print(
        json.dumps(
            {
                "baselines": {
                    "bm25_only_top1": round(
                        sum(1 for s in subject_rows if s["bm25_target_rank"] == 1)
                        / len(subject_rows),
                        3,
                    ),
                    "vector_only_top1": round(
                        sum(
                            1
                            for s in subject_rows
                            if first_rank([c for c, _ in s["vector"]], s["wanted"]) == 1
                        )
                        / len(subject_rows),
                        3,
                    ),
                    "no_floor_top1": round(none_fused_top1 / len(subject_rows), 3),
                }
            }
        )
    )

    for label, floor, margin, cap in POLICIES:
        subject_top1 = subject_top5 = 0
        control_top2 = 0
        para_top5 = 0
        kept = 0
        demoted = 0
        control_ranks = {}
        for sample in samples:
            vector_ids = admit(sample, floor, margin)
            if cap is not None:
                vector_ids = vector_ids[:cap]
            kept += len(vector_ids)
            fused = [
                chunk_id for chunk_id, _ in fusion.fuse([sample["bm25"], vector_ids])
            ]
            rank = first_rank(fused, sample["wanted"])
            if sample["bm25"]:
                demoted += first_rank(fused, {sample["bm25"][0]}) != 1
            if sample["family"] == "subject":
                subject_top1 += rank == 1
                subject_top5 += rank is not None and rank <= 5
            elif sample["family"] == "control":
                control_top2 += rank is not None and rank <= 2
                control_ranks[sample["query"]] = rank
            else:
                para_top5 += rank is not None and rank <= 5
        total_subject = sum(1 for s in samples if s["family"] == "subject")
        print(
            json.dumps(
                {
                    "policy": label,
                    "subject_top1": round(subject_top1 / total_subject, 3),
                    "subject_top5": round(subject_top5 / total_subject, 3),
                    "control_top2": f"{control_top2}/4",
                    "control_ranks": control_ranks,
                    "paraphrase_top5": f"{para_top5}/5",
                    "bm25_top1_demoted": f"{demoted}/{len(samples)}",
                    "mean_vector_kept": round(kept / len(samples), 1),
                }
            ),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
