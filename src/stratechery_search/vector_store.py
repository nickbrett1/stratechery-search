"""The LanceDB store: vectors, provenance, and the parity check that cannot be skipped.

Shape (design §4.5a): one row per passage, keyed by ``chunk_id`` — never
``chunks.id INTEGER``, which is the external-content join key for ``chunks_fts``
and meaningful only inside ``search.sqlite``. The filter columns are
denormalised onto the row because LanceDB has no ``articles`` table and pushing
the predicate down is how the two arms come to agree (see :mod:`.filters`).

**``lancedb`` is imported inside functions, never at module scope.** The default
path (``hybrid=0``) must never depend on a large native wheel loading, so a
broken Lance install costs the flag, not the service — the same scoping the
parity check applies to a mismatched index. A subprocess test asserts the
import graph leaves it out.

**No ANN index.** 29,918 rows is brute-force range for this service, and
choosing IVF/PQ before measuring would be the design note's own "a floor set
before seeing any data is a guess" mistake in index form. The row count at which
that changes is :data:`ANN_ROWS`, recorded rather than acted on.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .embed import EMBED_DIM
from .filters import FilterSet

#: Above this, a flat scan stops being obviously cheap on a phone's budget and a
#: vector index becomes a measurement to take (not yet a decision to make).
ANN_ROWS = 250_000

TABLE = "passages"


class StoreError(Exception):
    """A problem with the store itself. Never a degradation — these are 500s."""

    def __init__(self, code: str, message: str, detail: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail or {}


@dataclass(frozen=True)
class StoredVector:
    chunk_id: str
    distance: float


def _lancedb():
    """The one place the native wheel is imported."""
    import lancedb  # noqa: PLC0415 - deliberate: see the module docstring

    return lancedb


def schema():
    """The Arrow schema, written for 2,048 dims and nothing else.

    ``pa.list_(pa.float32(), 2048)`` is a *fixed-size* list, which is what makes
    the width a build-time assertion rather than a runtime surprise: a 1,024-dim
    model cannot be written into this table at all.
    """
    import pyarrow as pa  # noqa: PLC0415

    return pa.schema(
        [
            pa.field("chunk_id", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), EMBED_DIM)),
            pa.field("embed_model", pa.string()),
            pa.field("chunker_version", pa.int64()),
            pa.field("text_sha256", pa.string()),
            # Denormalised for prefiltering (§4.5a). `has_images` is stored as the
            # answer, not the count, because the predicate is a question.
            pa.field("sent_at", pa.string()),
            pa.field("kind", pa.string()),
            pa.field("sender", pa.string()),
            pa.field("has_images", pa.bool_()),
            pa.field("is_transactional", pa.bool_()),
        ]
    )


def open_store(path: Path, *, create: bool = False):
    """Connect to (or create) the store directory and return the table."""
    lancedb = _lancedb()
    path = Path(path)
    if not create and not path.exists():
        raise StoreError(
            "store_missing",
            "the vector store does not exist; run `python -m stratechery_search.embed_corpus`",
            {"path": str(path)},
        )
    path.mkdir(parents=True, exist_ok=True)
    database = lancedb.connect(str(path))
    names = set(database.table_names())
    if TABLE not in names:
        if not create:
            raise StoreError(
                "store_missing", f"table {TABLE!r} is not in {path}", {"path": str(path)}
            )
        return database.create_table(TABLE, schema=schema())
    return database.open_table(TABLE)


def add_rows(table, rows: list[dict]) -> int:
    """Append rows, asserting the width against the schema before writing."""
    if not rows:
        return 0
    for row in rows:
        if len(row["vector"]) != EMBED_DIM:
            raise StoreError(
                "store_schema",
                f"row {row.get('chunk_id')} has {len(row['vector'])} dims, schema is {EMBED_DIM}",
            )
    table.add(rows)
    return len(rows)


def existing_chunk_ids(table) -> set[str]:
    """Which ``chunk_id``s are already embedded — the resumable-pass primitive."""
    return {row["chunk_id"] for row in table.search().select(["chunk_id"]).limit(None).to_list()}


def search(
    table,
    vector: list[float],
    *,
    filters: FilterSet,
    limit: int,
) -> list[StoredVector]:
    """kNN with the predicate pushed down as a prefilter.

    ``distance_type="cosine"``: the vectors are L2-normalised by the model, so
    cosine and dot-product agree, and cosine is the one whose numbers the design
    note's similarity log refers to.
    """
    if len(vector) != EMBED_DIM:
        raise StoreError(
            "store_schema", f"query vector is {len(vector)}-dim, schema is {EMBED_DIM}"
        )
    rows = (
        table.search(vector, vector_column_name="vector")
        .distance_type("cosine")
        .where(filters.lance())
        .limit(limit)
        .to_list()
    )
    return [
        StoredVector(chunk_id=row["chunk_id"], distance=float(row["_distance"]))
        for row in rows
    ]


def count(table) -> int:
    return table.count_rows()


def chunk_ids(table) -> set[str]:
    """Every ``chunk_id`` in the store — the only check that catches a corpus
    that is the right version but half-embedded."""
    return existing_chunk_ids(table)


def chunker_versions(table) -> set[int]:
    rows = table.search().select(["chunker_version"]).limit(None).to_list()
    return {int(row["chunker_version"]) for row in rows}


def embed_models(table) -> set[str]:
    rows = table.search().select(["embed_model"]).limit(None).to_list()
    return {row["embed_model"] for row in rows}


def assert_parity(table, conn: sqlite3.Connection, *, full: bool = True) -> dict:
    """A request whose store does not match ``search.sqlite`` fails loudly.

    Three checks, in cost order (design §4.5c): count, then the
    ``chunker_version`` set, then the ``chunk_id`` sets. The third is a full scan
    on either side, which is cheap at this corpus size and is the only check that
    catches a same-version-but-partial corpus — the case where the arms return
    disjoint ids, nothing joins, and hybrid silently equals BM25.

    A mismatch raises; it must never be degraded to BM25. An unreachable gateway
    is an operational condition, a mismatched index is a deployment mistake, and
    the difference is that one of them must look like a failure.
    """
    sqlite_count = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
    store_count = count(table)
    if sqlite_count != store_count:
        raise StoreError(
            "store_mismatch",
            f"store has {store_count} passages, search.sqlite has {sqlite_count}",
            {"store": store_count, "sqlite": sqlite_count},
        )

    sqlite_versions = {
        int(row[0]) for row in conn.execute("SELECT DISTINCT chunker_version FROM chunks")
    }
    store_versions = chunker_versions(table)
    if sqlite_versions != store_versions:
        raise StoreError(
            "store_mismatch",
            "chunker_version differs between the store and search.sqlite",
            {
                "store": sorted(store_versions),
                "sqlite": sorted(sqlite_versions),
            },
        )

    if full:
        sqlite_ids = {row[0] for row in conn.execute("SELECT chunk_id FROM chunks")}
        store_ids = chunk_ids(table)
        if sqlite_ids != store_ids:
            missing = sorted(sqlite_ids - store_ids)[:5]
            extra = sorted(store_ids - sqlite_ids)[:5]
            raise StoreError(
                "store_mismatch",
                f"chunk_id sets differ: {len(sqlite_ids - store_ids)} missing from the store,"
                f" {len(store_ids - sqlite_ids)} unknown to search.sqlite",
                {"missing_examples": missing, "extra_examples": extra},
            )

    return {"count": store_count, "chunker_versions": sorted(store_versions)}


def describe(table) -> dict:
    """A small JSON-safe description, for /healthz and for the build log."""
    return {
        "rows": count(table),
        "dim": EMBED_DIM,
        "embed_models": sorted(embed_models(table)),
        "chunker_versions": sorted(chunker_versions(table)),
        "ann_index": None,
        "ann_rows_threshold": ANN_ROWS,
    }


class Backend:
    """The hybrid arm as the query layer sees it: one search, lazily opened.

    The table handle and the parity verdict are held on the instance, so the
    first ``hybrid=1`` request pays the connect and the rest do not. Nothing
    here is touched by ``hybrid=0`` — the import, the connect and the parity scan
    all live behind :meth:`table`.
    """

    def __init__(self, path: Path, conn: sqlite3.Connection | None = None) -> None:
        self.path = Path(path)
        self._table = None
        self._parity: dict | None = None
        self._conn = conn
        self._stamp: int | None = None

    def _store_mtime(self) -> int:
        """A cheap "has the store moved on disk" probe.

        LanceDB writes each version as new files inside ``passages.lance``, so
        that directory's mtime changes exactly when a rebuild lands. Without
        this, a process that served one hybrid request before a corpus pass
        keeps the *old* snapshot for its whole life and answers confidently
        from 96 rows — which is what happened between stage A and stage B.
        """
        target = self.path / f"{TABLE}.lance"
        try:
            return (target if target.exists() else self.path).stat().st_mtime_ns
        except OSError:
            return 0

    def table(self, conn: sqlite3.Connection | None = None):
        stamp = self._store_mtime()
        if self._table is None or stamp != self._stamp:
            table = open_store(self.path)
            # Validate *before* caching. Cached first, a mismatch would 500 once
            # and then serve 200s for the life of the process — the exact
            # silence this check exists to prevent.
            parity = assert_parity(table, conn or self._conn)
            self._table, self._parity, self._stamp = table, parity, stamp
        return self._table

    def warm(self, index_path: Path) -> dict:
        """Pay the connect and the parity scan before a reader asks for one.

        Measured 2026-11-07 on the live box: the first hybrid request after a
        container start took **1,078 ms**; the same query warm, **205 ms**. The
        ~870 ms difference is opening LanceDB and comparing 29,918 chunk ids on
        each side, and nothing about it needs a reader to wait. ``serve()``
        calls this on a background thread at startup.

        Failure is returned, never raised: a store that cannot be warmed is one
        whose first real request fails the same way, and a warm-up that kills
        the server would be a worse failure than the slow search it prevents.
        """
        started = time.perf_counter()
        try:
            # sqlite3 connections are per-thread, so this one belongs to the
            # thread that warms -- not to the request threads that follow.
            conn = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
            try:
                self.table(conn)
            finally:
                conn.close()
        except Exception as error:  # noqa: BLE001 - startup must survive this
            return {
                "ok": False,
                "ms": round((time.perf_counter() - started) * 1000, 1),
                "code": getattr(error, "code", type(error).__name__),
                "message": str(error),
            }
        return {
            "ok": True,
            "ms": round((time.perf_counter() - started) * 1000, 1),
            "parity": self._parity,
        }

    def search(
        self,
        vector: list[float],
        *,
        conn: sqlite3.Connection | None = None,
        filters: FilterSet,
        limit: int,
    ):
        # The connection is what makes parity possible: without it
        # `assert_parity` has no index to compare against and the first hybrid
        # request after every process start raised AttributeError -> 500.
        return search(self.table(conn), vector, filters=filters, limit=limit)

    def describe(self, conn: sqlite3.Connection | None = None) -> dict:
        table = self.table(conn)
        return {**describe(table), "parity": self._parity, "path": str(self.path)}
