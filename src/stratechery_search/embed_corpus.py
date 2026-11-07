"""Embed the corpus into the Lance store — once, resumably, and provably no-ops on re-run.

    python -m stratechery_search.embed_corpus --check     # parity, no spend
    python -m stratechery_search.embed_corpus --limit 96  # the sample (~$0.003)
    python -m stratechery_search.embed_corpus             # the full pass (~$1)

It budgets its own timeout (``EMBED_CORPUS_TIMEOUT_MS``, default 5 s, see
:data:`stratechery_search.embed.CORPUS_TIMEOUT_MS`): a real 96-document batch
takes ~1 s, so the query budget would abort the pass on its first batch.

Three properties are the reason this is a module rather than a script:

**Resumable.** Each batch's rows are written as it succeeds, and a chunk already
in the store with the same ``text_sha256`` is skipped. A failure at request 200
resumes rather than re-spending, and a half-finished store is visibly partial
because row count is checked against ``search.sqlite`` rather than assumed.

**Keyed by ``chunk_id``.** The store's only stable label, and the only thing the
two arms can join on. ``chunks.id`` is a SQLite detail and would make the two
indexes agree by accident today and disagree silently tomorrow.

**A second run embeds nothing.** That is not an intention, it is the one check
worth most here (design §4.1.3, and the ``reader`` precedent where a reset-to-
needs-embedding bug turned a $1 pass into a recurring $5): run it twice and the
second pass reports ``embedded: 0``. Provenance is what makes a chunker change a
deliberate re-embed instead of silent drift.

Provenance lives in the store, not in ``search.sqlite``: the index is read-only
at runtime by design, and adding a write path to it to record "this row has been
embedded" would trade a phase-1 invariant for a column the store already has.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

from . import embed as embed_module
from . import vector_store

#: Chunks per request. Cohere's cap is 96 and the gateway does not raise it
#: (97 answers HTTP 400), so this is a ceiling rather than a tuning knob:
#: ``ceil(29918/96) = 312`` requests for the whole corpus.
BATCH = 96

CHUNK_QUERY = (
    "SELECT c.chunk_id, c.chunker_version, c.text_sha256, c.text,"
    " a.sent_at, a.kind, a.sender, a.n_images, a.is_transactional"
    " FROM chunks c JOIN articles a ON a.message_id = c.message_id"
    " ORDER BY c.chunk_id"
)


def _existing(table) -> dict[str, str]:
    """``chunk_id -> text_sha256`` already in the store."""
    rows = (
        table.search()
        .select(["chunk_id", "text_sha256"])
        .limit(None)
        .to_list()
    )
    return {row["chunk_id"]: row["text_sha256"] for row in rows}


def _pending(conn: sqlite3.Connection, existing: dict[str, str]) -> list[tuple]:
    """Chunks with no row, or a row whose text has changed since it was embedded."""
    pending = []
    for row in conn.execute(CHUNK_QUERY):
        chunk_id, _, text_sha256 = row[0], row[1], row[2]
        if existing.get(chunk_id) == text_sha256:
            continue
        pending.append(row)
    return pending


def _row(values: tuple, vector: list[float], model: str) -> dict:
    (
        chunk_id,
        chunker_version,
        text_sha256,
        _text,
        sent_at,
        kind,
        sender,
        n_images,
        is_transactional,
    ) = values
    return {
        "chunk_id": chunk_id,
        "vector": vector,
        "embed_model": model,
        "chunker_version": int(chunker_version or 0),
        "text_sha256": text_sha256,
        "sent_at": sent_at or "",
        "kind": kind or "",
        "sender": sender or "",
        "has_images": bool(n_images),
        "is_transactional": bool(is_transactional),
    }


def run(
    *,
    index_path: Path,
    store_path: Path,
    limit: int | None = None,
    batch: int = BATCH,
    check_only: bool = False,
) -> dict:
    if batch > embed_module.MAX_BATCH:
        raise SystemExit(f"--batch cannot exceed {embed_module.MAX_BATCH} (Cohere answers 400 above it)")

    conn = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
    try:
        if check_only:
            table = vector_store.open_store(store_path)
            return {
                "check": True,
                "parity": vector_store.assert_parity(table, conn),
                **vector_store.describe(table),
            }

        table = vector_store.open_store(store_path, create=True)
        existing = _existing(table)
        pending = _pending(conn, existing)
        if limit is not None:
            pending = pending[:limit]

        config = embed_module.EmbedConfig.for_corpus()
        summary = {
            "index": str(index_path),
            "store": str(store_path),
            "model": config.model,
            # Recorded because it is the number that made the pass possible: the
            # query budget (800 ms) aborts a 96-document batch, and a run record
            # without this field cannot tell which budget produced the rows.
            "timeout_ms": config.timeout_ms,
            "already_stored": len(existing),
            "pending": len(pending),
            "embedded": 0,
            "batches": 0,
        }
        if not pending:
            # The no-op re-run. Printed loudly because it is the check, not a
            # quiet success: it is what makes a chunker change cheap.
            summary["note"] = "nothing to embed — the store already matches the index"
            summary.update(vector_store.describe(table))
            return summary

        started = time.perf_counter()
        for offset in range(0, len(pending), batch):
            chunk = pending[offset : offset + batch]
            vectors = embed_module.embed(
                [row[3] for row in chunk],
                input_type="search_document",
                config=config,
            )
            written = vector_store.add_rows(
                table,
                [_row(values, vector, config.model) for values, vector in zip(chunk, vectors, strict=True)],
            )
            summary["embedded"] += written
            summary["batches"] += 1
            print(
                json.dumps(
                    {
                        "batch": summary["batches"],
                        "embedded": summary["embedded"],
                        "of": len(pending),
                        "elapsed_s": round(time.perf_counter() - started, 1),
                    }
                ),
                flush=True,
            )

        summary["elapsed_s"] = round(time.perf_counter() - started, 1)
        # A partial run is a *deliberate* state (the 96-row sample), so parity is
        # reported rather than raised here: what must not happen is the service
        # concluding a partial store is fine. `hybrid=1` still fails loudly, and
        # `--check` is where an operator gets the exception.
        try:
            summary["parity"] = {"ok": True, **vector_store.assert_parity(table, conn)}
        except vector_store.StoreError as error:
            summary["parity"] = {"ok": False, "code": error.code, "message": str(error)}
            print(
                f"parity NOT satisfied: {error} — the store is incomplete, so hybrid=1 will 500",
                file=sys.stderr,
                flush=True,
            )
        return summary
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--index",
        type=Path,
        default=Path(_env("INDEX_PATH", Path(_env("DATA_ROOT", "/data")) / "index" / "search.sqlite")),
    )
    parser.add_argument(
        "--store",
        type=Path,
        default=Path(_env("LANCEDB_PATH", Path(_env("DATA_ROOT", "/data")) / "index" / "lancedb")),
    )
    parser.add_argument("--limit", type=int, default=None, help="stop after N chunks (the 96-row sample)")
    parser.add_argument("--batch", type=int, default=BATCH)
    parser.add_argument("--check", action="store_true", help="assert parity and describe, embed nothing")
    args = parser.parse_args(argv)

    summary = run(
        index_path=args.index,
        store_path=args.store,
        limit=args.limit,
        batch=args.batch,
        check_only=args.check,
    )
    print(json.dumps(summary, indent=2))
    return 0


def _env(name: str, default):
    import os

    return os.environ.get(name, default)


if __name__ == "__main__":
    sys.exit(main())
