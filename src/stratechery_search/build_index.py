"""Build ``search.sqlite`` — the phase-1 index — from the derived text layer.

Usage::

    python -m stratechery_search.build_index \
        --derived data/derived \
        --archive /srv/datasets/stratechery-archive \
        --out data/index/search.sqlite

The build follows phase-1 scope contract §2.2 **in order**, and the order is
the deliverable: in particular step 4, ``INSERT INTO chunks_fts(chunks_fts)
VALUES('rebuild')``, is required rather than optional. An external-content FTS5
table does not index itself, so skipping it produces a schema-clean, completely
empty index that fails at query time as "search returns nothing".

Every §2.3 gate is evaluated here and written to ``index-report.json`` beside
the database. A failed gate exits non-zero and the database is not installed:
the temp file is discarded, so a half-built index never appears at the path the
service reads.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from .chunk import CHUNKER_VERSION, chunk_article, sha256_text
from .flatten import read_index

SCHEMA_VERSION = "1"

#: §2.3 gate values.
EXPECTED = {
    "articles": 1691,
    "searchable_articles": 1647,
    "chunks_min": 20_000,
    "chunks_max": 32_000,
    "images_distinct": 2415,
    "images_null": 2,
    "null_asset_ids": (77349, 95082),
    "chunk_end_tolerance": 150,
}

CONTROL_QUERY_TERM = "the"

SCHEMA = """
CREATE TABLE articles (
  message_id INTEGER PRIMARY KEY,
  sent_at TEXT NOT NULL,
  subject TEXT,
  from_header TEXT,
  kind TEXT,
  sender TEXT,
  chars INTEGER, words INTEGER, est_tokens INTEGER,
  n_images INTEGER, has_tables INTEGER,
  is_transactional INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE chunks (
  id INTEGER PRIMARY KEY,
  chunk_id TEXT NOT NULL UNIQUE,
  message_id INTEGER NOT NULL REFERENCES articles(message_id),
  ordinal INTEGER NOT NULL,
  start_char INTEGER, end_char INTEGER,
  text TEXT NOT NULL,
  subject TEXT NOT NULL,
  embed_model TEXT,
  chunker_version TEXT NOT NULL, text_sha256 TEXT NOT NULL
);

CREATE VIRTUAL TABLE chunks_fts USING fts5(
  text, subject,
  content='chunks', content_rowid='id',
  tokenize="porter unicode61 remove_diacritics 2",
  prefix='2 3'
);

CREATE TABLE article_images (
  message_id INTEGER NOT NULL REFERENCES articles(message_id),
  ordinal INTEGER NOT NULL,
  url_path TEXT NOT NULL,
  asset TEXT,
  sha256 TEXT,
  bytes INTEGER,
  PRIMARY KEY (message_id, ordinal)
);

CREATE TABLE corpus_stats (
  singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
  articles INTEGER NOT NULL,
  searchable_articles INTEGER NOT NULL,
  chunks INTEGER NOT NULL,
  first_sent_at TEXT, last_sent_at TEXT,
  transactional INTEGER NOT NULL,
  articles_with_images INTEGER NOT NULL,
  images_distinct INTEGER NOT NULL,
  built_at TEXT NOT NULL,
  flattener_version TEXT NOT NULL, chunker_version TEXT NOT NULL
);

CREATE TABLE stats_by_year  (year   INTEGER PRIMARY KEY, articles INTEGER NOT NULL, chars INTEGER NOT NULL);
CREATE TABLE stats_by_kind  (kind   TEXT    PRIMARY KEY, articles INTEGER NOT NULL, chunks INTEGER NOT NULL);
CREATE TABLE stats_by_sender(sender TEXT PRIMARY KEY, articles INTEGER NOT NULL);

CREATE TABLE stats_notes (
  code TEXT PRIMARY KEY,
  year INTEGER, articles INTEGER, neighbouring_mean INTEGER,
  message TEXT NOT NULL
);
"""


def _gate(code: str, expected, observed, ok: bool, note: str = "") -> dict:
    return {
        "gate": code,
        "expected": expected,
        "observed": observed,
        "pass": bool(ok),
        "note": note,
    }


def load_metas(derived: Path) -> list[dict]:
    """Read every ``*.meta.json`` under ``derived``, ordered by message_id."""
    metas = []
    for path in sorted(derived.glob("*.meta.json")):
        meta = json.loads(path.read_text(encoding="utf-8"))
        meta["_path"] = path
        metas.append(meta)
    metas.sort(key=lambda m: int(m["message_id"]))
    return metas


def _image_rows(row: dict) -> list[dict]:
    """Turn an ``index.jsonl`` ``images[]`` array into ``article_images`` rows.

    ``asset`` / ``sha256`` / ``bytes`` stay ``None`` for the two url-only
    references (message_ids 77349, 95082) — design §1 records them, and the
    ``/article`` contract returns ``asset: null`` rather than pretending.
    """
    out = []
    for ordinal, img in enumerate(row.get("images") or []):
        out.append(
            {
                "ordinal": ordinal,
                "url_path": img.get("url_path") or img.get("url") or "",
                "asset": img.get("asset"),
                "sha256": img.get("sha256"),
                "bytes": img.get("bytes"),
            }
        )
    return out


def _stats_notes(by_year: dict[int, int]) -> list[dict]:
    """Flag years that hold less than half the mean of their neighbours.

    This is the §3 ``notes[]`` machinery carrying design §1's honesty
    requirement: 2017 is a source-sampling artefact, not a quiet publishing
    year, and a chart that implies otherwise has to be contradicted in words.
    """
    notes = []
    years = sorted(by_year)
    for index, year in enumerate(years):
        if index == 0 or index == len(years) - 1:
            continue
        neighbours = [by_year[years[index - 1]], by_year[years[index + 1]]]
        mean = int(sum(neighbours) / 2)
        if by_year[year] < mean / 2:
            notes.append(
                {
                    "code": f"under_represented_year_{year}",
                    "year": year,
                    "articles": by_year[year],
                    "neighbouring_mean": mean,
                    "message": (
                        f"{year} holds {by_year[year]} messages against ~{mean} in "
                        "neighbouring years. A source sampling artefact, not a change "
                        "in publishing volume."
                    ),
                }
            )
    return notes


def build(
    derived: Path,
    archive: Path,
    out_path: Path,
    verify_idempotent: bool = True,
) -> dict:
    """Build the index at ``out_path`` and return the gate report."""
    metas = load_metas(derived)
    if not metas:
        raise SystemExit(f"no derived meta files under {derived}")

    index_rows: dict[int, dict] = {}
    if (archive / "index.jsonl").exists():
        index_rows = {int(row["message_id"]): row for row in read_index(archive)}

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_name(f".{out_path.name}.building")
    if tmp_path.exists():
        tmp_path.unlink()

    built_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    texts: dict[int, str] = {}
    for meta in metas:
        message_id = int(meta["message_id"])
        texts[message_id] = (derived / f"{message_id}.txt").read_text(encoding="utf-8")

    conn = sqlite3.connect(tmp_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)

        # Step 2 — articles.
        conn.executemany(
            "INSERT INTO articles (message_id, sent_at, subject, from_header, kind,"
            " sender, chars, words, est_tokens, n_images, has_tables,"
            " is_transactional) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    int(m["message_id"]),
                    m["sent_at"],
                    m.get("subject"),
                    m.get("from"),
                    m.get("kind"),
                    m.get("sender"),
                    int(m.get("chars") or 0),
                    int(m.get("words") or 0),
                    int(m.get("est_tokens") or 0),
                    int(m.get("n_images") or 0),
                    1 if m.get("has_tables") else 0,
                    1 if m.get("is_transactional") else 0,
                )
                for m in metas
            ],
        )

        # Step 3 — chunks, ordered by (message_id, ordinal) so `id` reproduces.
        chunk_rows: list[tuple] = []
        for meta in metas:
            message_id = int(meta["message_id"])
            rows = chunk_article(message_id, texts[message_id], meta.get("subject") or "")
            for row in rows:
                chunk_rows.append(
                    (
                        row["chunk_id"],
                        row["message_id"],
                        row["ordinal"],
                        row["start_char"],
                        row["end_char"],
                        row["text"],
                        row["subject"],
                        row["embed_model"],
                        row["chunker_version"],
                        row["text_sha256"],
                    )
                )
        conn.executemany(
            "INSERT INTO chunks (chunk_id, message_id, ordinal, start_char, end_char,"
            " text, subject, embed_model, chunker_version, text_sha256)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            chunk_rows,
        )

        # Step 4 — the required external-content rebuild.
        conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")

        # Step 5 — images, `asset` nullable on purpose.
        image_rows = []
        for meta in metas:
            row = index_rows.get(int(meta["message_id"]), {})
            for img in _image_rows(row):
                image_rows.append(
                    (
                        int(meta["message_id"]),
                        img["ordinal"],
                        img["url_path"],
                        img["asset"],
                        img["sha256"],
                        img["bytes"],
                    )
                )
        conn.executemany(
            "INSERT INTO article_images (message_id, ordinal, url_path, asset, sha256,"
            " bytes) VALUES (?,?,?,?,?,?)",
            image_rows,
        )

        # Step 6 — stats, in the same transaction as the load.
        by_year_articles: Counter[int] = Counter()
        by_year_chars: Counter[int] = Counter()
        by_kind_articles: Counter[str] = Counter()
        by_kind_chunks: Counter[str] = Counter()
        by_sender: Counter[str] = Counter()
        chunks_per_article: Counter[int] = Counter()
        for row in chunk_rows:
            chunks_per_article[row[1]] += 1
        for meta in metas:
            message_id = int(meta["message_id"])
            year = int(str(meta["sent_at"])[:4])
            by_year_articles[year] += 1
            by_year_chars[year] += int(meta.get("chars") or 0)
            by_kind_articles[meta.get("kind") or "other"] += 1
            by_kind_chunks[meta.get("kind") or "other"] += chunks_per_article[message_id]
            by_sender[meta.get("sender") or ""] += 1

        transactional = sum(1 for m in metas if m.get("is_transactional"))
        distinct_assets = {
            img["asset"] for row in index_rows.values() for img in _image_rows(row)
            if img["asset"]
        }
        articles_with_images = sum(1 for m in metas if int(m.get("n_images") or 0) > 0)
        sent_ats = sorted(str(m["sent_at"]) for m in metas)

        conn.execute(
            "INSERT INTO corpus_stats (singleton, articles, searchable_articles, chunks,"
            " first_sent_at, last_sent_at, transactional, articles_with_images,"
            " images_distinct, built_at, flattener_version, chunker_version)"
            " VALUES (1,?,?,?,?,?,?,?,?,?,?,?)",
            (
                len(metas),
                len(metas) - transactional,
                len(chunk_rows),
                sent_ats[0],
                sent_ats[-1],
                transactional,
                articles_with_images,
                len(distinct_assets),
                built_at,
                str(metas[0].get("flattener_version") or ""),
                CHUNKER_VERSION,
            ),
        )
        conn.executemany(
            "INSERT INTO stats_by_year (year, articles, chars) VALUES (?,?,?)",
            sorted((y, by_year_articles[y], by_year_chars[y]) for y in by_year_articles),
        )
        conn.executemany(
            "INSERT INTO stats_by_kind (kind, articles, chunks) VALUES (?,?,?)",
            sorted((k, by_kind_articles[k], by_kind_chunks[k]) for k in by_kind_articles),
        )
        conn.executemany(
            "INSERT INTO stats_by_sender (sender, articles) VALUES (?,?)",
            sorted(by_sender.items()),
        )
        conn.executemany(
            "INSERT INTO stats_notes (code, year, articles, neighbouring_mean, message)"
            " VALUES (?,?,?,?,?)",
            [
                (
                    n["code"],
                    n["year"],
                    n["articles"],
                    n["neighbouring_mean"],
                    n["message"],
                )
                for n in _stats_notes(dict(by_year_articles))
            ],
        )

        conn.commit()

        # Step 7 — optimize, then install atomically.
        conn.execute("PRAGMA optimize")
        conn.commit()

        report = _evaluate(conn, metas, texts, index_rows, derived)
        report["build"] = {
            "built_at": built_at,
            "schema_version": SCHEMA_VERSION,
            "chunker_version": CHUNKER_VERSION,
            "flattener_version": str(metas[0].get("flattener_version") or ""),
            "derived_dir": str(derived),
            "archive": str(archive),
        }

        pending = [g for g in report["gates"] if not g["pass"]]
        if pending:
            # Do not install a failing index: the service reads `out_path` only.
            report["installed"] = False
            conn.close()
            tmp_path.unlink(missing_ok=True)
            return report

        vacuum_target = out_path.with_name(f".{out_path.name}.vacuum")
        vacuum_target.unlink(missing_ok=True)
        conn.execute("VACUUM INTO '" + str(vacuum_target).replace("'", "''") + "'")
    finally:
        conn.close()

    vacuumed = out_path.with_name(f".{out_path.name}.vacuum")
    if vacuumed.exists():
        vacuumed.replace(out_path)
    tmp_path.unlink(missing_ok=True)

    report["installed"] = True
    if verify_idempotent:
        report["gates"].append(_idempotency_gate(derived, archive, out_path, metas))
    return report


def _idempotency_gate(derived: Path, archive: Path, out_path: Path, metas) -> dict:
    """Rebuild once more from the same ``derived/`` and compare the chunk set.

    Cheap insurance: ``id`` is an insertion-order rowid, so this gate is what
    proves the FTS join key is reproducible rather than incidental.
    """
    second = out_path.with_name(f".{out_path.name}.rebuild")
    second.unlink(missing_ok=True)
    report = build(derived, archive, second, verify_idempotent=False)
    if not report.get("installed"):
        second.unlink(missing_ok=True)
        return _gate("build_idempotent", "rebuild matches", "rebuild failed", False)

    def digest(path: Path) -> list[tuple]:
        conn = sqlite3.connect(path)
        try:
            return conn.execute(
                "SELECT id, chunk_id, text_sha256 FROM chunks ORDER BY id"
            ).fetchall()
        finally:
            conn.close()

    first_rows, second_rows = digest(out_path), digest(second)
    second.unlink(missing_ok=True)
    return _gate(
        "build_idempotent",
        "identical id/chunk_id/text_sha256",
        f"{len(first_rows)} rows, match={first_rows == second_rows}",
        first_rows == second_rows,
    )


def _evaluate(
    conn: sqlite3.Connection,
    metas: list[dict],
    texts: dict[int, str],
    index_rows: dict[int, dict],
    derived: Path,
) -> dict:
    gates: list[dict] = []
    cur = conn.cursor()

    articles = cur.execute("SELECT count(*) FROM articles").fetchone()[0]
    gates.append(
        _gate("article_rows", EXPECTED["articles"], articles, articles == EXPECTED["articles"])
    )

    transactional = cur.execute(
        "SELECT count(*) FROM articles WHERE is_transactional = 1"
    ).fetchone()[0]
    searchable = cur.execute(
        "SELECT searchable_articles FROM corpus_stats"
    ).fetchone()[0]
    gates.append(
        _gate(
            "searchable_articles",
            {"value": EXPECTED["searchable_articles"], "equals": "articles - transactional"},
            {"value": searchable, "computed": articles - transactional},
            searchable == EXPECTED["searchable_articles"] and searchable == articles - transactional,
        )
    )

    chunk_count = cur.execute("SELECT count(*) FROM chunks").fetchone()[0]
    gates.append(
        _gate(
            "chunk_rows",
            f"{EXPECTED['chunks_min']}-{EXPECTED['chunks_max']}",
            chunk_count,
            EXPECTED["chunks_min"] <= chunk_count <= EXPECTED["chunks_max"],
        )
    )

    empty = cur.execute("SELECT count(*) FROM chunks WHERE length(trim(text)) = 0").fetchone()[0]
    gates.append(_gate("no_empty_chunks", 0, empty, empty == 0))

    bad_tail = []
    for message_id, chars in cur.execute(
        "SELECT message_id, articles.chars FROM chunks JOIN articles USING (message_id)"
        " GROUP BY message_id HAVING abs(max(end_char) - articles.chars) > ?",
        (EXPECTED["chunk_end_tolerance"],),
    ).fetchall():
        bad_tail.append({"message_id": message_id, "chars": chars})
    gates.append(
        _gate(
            "chunk_coverage",
            f"max(end_char) = chars ± {EXPECTED['chunk_end_tolerance']}",
            bad_tail[:10] or "ok",
            not bad_tail,
        )
    )

    subject_mismatch = cur.execute(
        "SELECT count(*) FROM chunks JOIN articles USING (message_id)"
        " WHERE chunks.subject = '' OR chunks.subject IS NULL"
        " OR chunks.subject != coalesce(articles.subject, '')"
    ).fetchone()[0]
    gates.append(
        _gate("subject_denormalised", 0, subject_mismatch, subject_mismatch == 0)
    )

    fts_count = cur.execute("SELECT count(*) FROM chunks_fts").fetchone()[0]
    control = cur.execute(
        "SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH ?", (CONTROL_QUERY_TERM,)
    ).fetchone()[0]
    gates.append(
        _gate(
            "fts_parity_and_control",
            {"parity": chunk_count, "control_min": 1},
            {"fts_rows": fts_count, "control_hits": control},
            fts_count == chunk_count and control >= 1,
            note="parity alone passes on an empty index; the control query is the real gate",
        )
    )

    snippet = cur.execute(
        "SELECT snippet(chunks_fts, 0, '<mark>', '</mark>', '…', 8) FROM chunks_fts"
        " WHERE chunks_fts MATCH ? LIMIT 1",
        (CONTROL_QUERY_TERM,),
    ).fetchone()
    gates.append(
        _gate(
            "fts_content_resolution",
            "non-null snippet",
            "ok" if snippet and snippet[0] else None,
            bool(snippet and snippet[0]),
            note="catches the subject-not-in-chunks defect, which breaks snippet()/bm25()",
        )
    )

    image_rows = cur.execute("SELECT count(*) FROM article_images").fetchone()[0]
    expected_image_rows = cur.execute("SELECT coalesce(sum(n_images), 0) FROM articles").fetchone()[0]
    distinct_assets = cur.execute(
        "SELECT count(DISTINCT asset) FROM article_images WHERE asset IS NOT NULL"
    ).fetchone()[0]
    null_ids = [
        row[0]
        for row in cur.execute(
            "SELECT message_id FROM article_images WHERE asset IS NULL ORDER BY message_id"
        ).fetchall()
    ]
    gates.append(
        _gate(
            "images",
            {
                "rows": expected_image_rows,
                "distinct_assets": EXPECTED["images_distinct"],
                "null_assets": EXPECTED["images_null"],
            },
            {
                "rows": image_rows,
                "distinct_assets": distinct_assets,
                "null_assets": len(null_ids),
                "null_ids": null_ids,
            },
            image_rows == expected_image_rows
            and distinct_assets == EXPECTED["images_distinct"]
            and tuple(null_ids) == tuple(EXPECTED["null_asset_ids"]),
        )
    )

    singleton = cur.execute("SELECT count(*) FROM corpus_stats").fetchone()[0]
    year_sum = cur.execute("SELECT coalesce(sum(articles), 0) FROM stats_by_year").fetchone()[0]
    stats_transactional = cur.execute(
        "SELECT transactional FROM corpus_stats"
    ).fetchone()[0]
    gates.append(
        _gate(
            "stats_present",
            {"singleton": 1, "sum_by_year": EXPECTED["articles"],
             "transactional": transactional},
            {"singleton": singleton, "sum_by_year": year_sum,
             "transactional": stats_transactional},
            singleton == 1 and year_sum == articles and stats_transactional == transactional,
        )
    )

    bad_provenance = cur.execute(
        "SELECT count(*) FROM chunks WHERE chunker_version IS NULL OR chunker_version = ''"
        " OR text_sha256 IS NULL OR text_sha256 = ''"
    ).fetchone()[0]
    embedded = cur.execute(
        "SELECT count(*) FROM chunks WHERE embed_model IS NOT NULL"
    ).fetchone()[0]
    gates.append(
        _gate(
            "provenance",
            {"bad_provenance": 0, "embed_model_null": chunk_count},
            {"bad_provenance": bad_provenance, "embedded": embedded},
            bad_provenance == 0 and embedded == 0,
            note="embed_model stays NULL in phase 1; the column is not permission to fill it",
        )
    )

    # Chunk text must still hash to what it claims, and the chunk set must add
    # up to the derived text it came from.
    hash_mismatch = 0
    for (text, claimed) in cur.execute("SELECT text, text_sha256 FROM chunks"):
        if sha256_text(text) != claimed:
            hash_mismatch += 1
    gates.append(_gate("chunk_hashes", 0, hash_mismatch, hash_mismatch == 0))

    return {"gates": gates, "observed": _observed(cur)}


def _observed(cur: sqlite3.Cursor) -> dict:
    per_year = {
        str(year): {"articles": articles, "chars": chars}
        for year, articles, chars in cur.execute(
            "SELECT year, articles, chars FROM stats_by_year ORDER BY year"
        )
    }
    by_kind = {
        kind: {"articles": articles, "chunks": chunks}
        for kind, articles, chunks in cur.execute(
            "SELECT kind, articles, chunks FROM stats_by_kind ORDER BY articles DESC"
        )
    }
    by_sender = {
        sender or "(empty)": articles
        for sender, articles in cur.execute(
            "SELECT sender, articles FROM stats_by_sender ORDER BY articles DESC"
        )
    }
    chars = [row[0] for row in cur.execute("SELECT chars FROM articles ORDER BY chars")]
    chunk_sizes = [row[0] for row in cur.execute("SELECT length(text) FROM chunks")]
    return {
        "per_year": per_year,
        "by_kind": by_kind,
        "by_sender": by_sender,
        "chars": {
            "min": chars[0],
            "median": int(statistics.median(chars)),
            "max": chars[-1],
            "total": sum(chars),
        },
        "chunk_chars": {
            "min": min(chunk_sizes),
            "median": int(statistics.median(chunk_sizes)),
            "max": max(chunk_sizes),
        },
        "stats_notes": [
            {"code": code, "year": year, "message": message}
            for code, year, message in cur.execute(
                "SELECT code, year, message FROM stats_notes ORDER BY code"
            )
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--derived", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="report path (default: alongside the database)",
    )
    parser.add_argument(
        "--no-verify-idempotent",
        action="store_true",
        help="skip the second build that proves the chunk set reproduces",
    )
    args = parser.parse_args(argv)

    report = build(
        args.derived,
        args.archive,
        args.out,
        verify_idempotent=not args.no_verify_idempotent,
    )
    report_path = args.report or args.out.parent / "index-report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
        encoding="utf-8",
    )

    failed = [gate for gate in report["gates"] if not gate["pass"]]
    for gate in report["gates"]:
        print(f"{'PASS' if gate['pass'] else 'FAIL'} {gate['gate']}: {gate['observed']}")
    print(f"installed: {report.get('installed')} -> {args.out if report.get('installed') else '(not installed)'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
