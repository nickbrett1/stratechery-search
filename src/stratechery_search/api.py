"""Query layer behind the phase-1 HTTP contract.

Everything here reads ``search.sqlite`` and nothing writes it: the index is
built wholesale by ``build_index`` and is read-only at runtime, so the service
needs no write path, no lock discipline and no cache invalidation.

The response shapes are fixed by phase-1 scope contract §3 and asserted by
``tests/test_api.py``; they are the contract the Svelte app is written against,
so changing them is a breaking change rather than an implementation detail.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SNIPPET_OPEN = "<mark>"
SNIPPET_CLOSE = "</mark>"
SNIPPET_ELLIPSIS = "…"
SNIPPET_TOKENS = 12

MAX_QUERY_CHARS = 200
DEFAULT_LIMIT = 20
MAX_LIMIT = 100
# A grouped result carries the article's other matching chunks inline; a very
# long interview can match twenty or more, and past a screenful the reader is
# better served by the article page than by an ever-growing card.
MAX_MORE_PASSAGES = 20

KINDS = ("update", "daily", "weekly", "interview", "other")

# The chunk projection and its joins live here rather than inline: the grouped
# path runs them twice (once ranked per chunk, once for the page) and a copy that
# drifted would make the two disagree about what "this article's passages" means.
CHUNK_FIELDS = (
    "SELECT c.chunk_id, c.message_id, c.start_char, c.end_char, a.sent_at,"
    " a.subject, a.kind, a.sender, bm25(chunks_fts, 10.0, 1.0) AS rank,"
    " snippet(chunks_fts, 0, ?, ?, ?, ?) AS snip"
)
CHUNK_JOINS = (
    " FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid"
    " JOIN articles a ON a.message_id = c.message_id"
)
SNIPPET_ARGS = [SNIPPET_OPEN, SNIPPET_CLOSE, SNIPPET_ELLIPSIS, SNIPPET_TOKENS]


class ApiError(Exception):
    """An error the HTTP layer renders as ``{"error": {...}}``."""

    def __init__(
        self, status: int, code: str, message: str, detail: Any = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.detail = detail or {}

    def as_body(self) -> dict:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "detail": self.detail,
            }
        }


@dataclass(frozen=True)
class SearchParams:
    q: str
    start: str | None = None
    end: str | None = None
    kinds: tuple[str, ...] = ()
    senders: tuple[str, ...] = ()
    has_images: bool | None = None
    limit: int = DEFAULT_LIMIT
    offset: int = 0
    # "passage" is the contract's original shape: one row per matching chunk,
    # ranked by bm25. "article" folds an article's chunks under its best-ranked
    # one and pages by article, which is what a reader wants — three passages of
    # one 2018 update in the top four results reads as repetition, not relevance.
    group: str = "passage"


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_bool(value: str | None, *, name: str) -> bool | None:
    if value is None or value == "":
        return None
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise ApiError(400, "bad_request", f"{name} must be a boolean", {"value": value})


def _check_date(value: str | None, name: str) -> str | None:
    if value is None or value == "":
        return None
    if not _DATE_RE.match(value):
        raise ApiError(
            400, "bad_request", f"{name} must be YYYY-MM-DD", {"value": value}
        )
    return value


def parse_search_params(query: dict[str, list[str]]) -> SearchParams:
    """Turn raw querystring pairs into validated parameters."""
    raw_q = (query.get("q") or [""])[0].strip()
    if not raw_q:
        # An empty q is 400, not "browse everything": browsing is /stats' job.
        raise ApiError(400, "bad_request", "q is required", {"param": "q"})
    if len(raw_q) > MAX_QUERY_CHARS:
        raise ApiError(
            400,
            "bad_request",
            f"q must be at most {MAX_QUERY_CHARS} characters",
            {"length": len(raw_q)},
        )

    kinds = tuple(
        value
        for value in (
            part.strip() for value in query.get("kind", []) for part in value.split(",")
        )
        if value
    )
    unknown = [kind for kind in kinds if kind not in KINDS]
    if unknown:
        raise ApiError(400, "bad_request", "unknown kind", {"kind": unknown})

    try:
        limit = int((query.get("limit") or [DEFAULT_LIMIT])[0])
        offset = int((query.get("offset") or [0])[0])
    except (TypeError, ValueError) as exc:
        raise ApiError(400, "bad_request", "limit and offset must be integers") from exc
    if limit < 1 or limit > MAX_LIMIT:
        raise ApiError(
            400, "bad_request", f"limit must be 1–{MAX_LIMIT}", {"limit": limit}
        )
    if offset < 0:
        raise ApiError(400, "bad_request", "offset must be >= 0", {"offset": offset})

    group = ((query.get("group") or [""])[0] or "passage").strip()
    if group not in {"passage", "article"}:
        raise ApiError(
            400, "bad_request", "group must be passage or article", {"group": group}
        )

    return SearchParams(
        q=raw_q,
        start=_check_date((query.get("from") or [None])[0], "from"),
        end=_check_date((query.get("to") or [None])[0], "to"),
        kinds=kinds,
        senders=tuple(
            value
            for value in (
                part.strip()
                for value in query.get("sender", [])
                for part in value.split(",")
            )
            if value
        ),
        has_images=parse_bool(
            (query.get("has_images") or [None])[0], name="has_images"
        ),
        limit=limit,
        offset=offset,
        group=group,
    )


_WORD_RE = re.compile(r"[^\W_]+(?:[-'][^\W_]+)*", re.UNICODE)


def fts_query(raw: str) -> str:
    """Quote each word so user punctuation can never be FTS5 syntax.

    FTS5 treats ``-``, ``:``, ``*``, ``"`` and ``AND``/``OR``/``NOT`` as
    operators; a reader typing ``aggregator economics`` must not have to know
    that. Words are quoted and implicitly ANDed (the default operator).
    """
    words = _WORD_RE.findall(raw)
    if not words:
        raise ApiError(400, "bad_request", "q contains no searchable words", {"q": raw})
    return " ".join(f'"{word}"' for word in words)


def load_vocab(path: Path | None) -> dict[str, dict]:
    """Read the query-time synonym table, tolerating its absence.

    Phase 1 ships ``vocab.yaml`` optionally (§6 step 6 keeps or drops it on
    judgement), so a missing file means "no expansions", not an error. The file
    is parsed by the small loader in :mod:`stratechery_search.vocab`.
    """
    if path is None or not path.exists():
        return {}
    from .vocab import load_vocabulary

    return load_vocabulary(path)


def expand_query(raw: str, vocab: dict[str, dict]) -> tuple[str, list[dict]]:
    """Return the rewritten FTS expression and the visible expansions.

    Expansions are always surfaced to the UI: a rewrite the reader cannot see
    is a rewrite that cannot be trusted or debugged (design §3.1).
    """
    words = _WORD_RE.findall(raw)
    if not words:
        raise ApiError(400, "bad_request", "q contains no searchable words", {"q": raw})
    lowered = [word.lower() for word in words]

    taken: set[str] = set()
    parts: list[str] = []
    expansions: list[dict] = []
    for entry in vocab.values():
        surfaces = [entry["canonical"], *entry["aliases"]]
        hit = None
        for surface in surfaces:
            surface_words = surface.lower().split()
            if surface_words and all(word in lowered for word in surface_words):
                hit = surface
                taken.update(surface_words)
                break
        if hit is None:
            continue
        phrases = sorted({entry["canonical"], *entry["aliases"]})
        parts.append("(" + " OR ".join(f'"{phrase}"' for phrase in phrases) + ")")
        expansions.append(
            {
                "matched": hit,
                "canonical": entry["canonical"],
                "aliases": sorted(entry["aliases"]),
                "source": "vocab.yaml",
            }
        )

    parts.extend(f'"{word}"' for word in words if word.lower() not in taken)
    return (" AND ".join(parts) if parts else fts_query(raw)), expansions


def _filters(params: SearchParams) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    values: list[Any] = []
    if params.start:
        clauses.append("a.sent_at >= ?")
        values.append(f"{params.start}T00:00:00Z")
    if params.end:
        clauses.append("a.sent_at <= ?")
        values.append(f"{params.end}T23:59:59Z")
    if params.kinds:
        clauses.append("a.kind IN (" + ",".join("?" * len(params.kinds)) + ")")
        values.extend(params.kinds)
    if params.senders:
        clauses.append("a.sender IN (" + ",".join("?" * len(params.senders)) + ")")
        values.extend(params.senders)
    if params.has_images is not None:
        clauses.append("a.n_images > 0" if params.has_images else "a.n_images = 0")
    # Transaction mail (access codes, receipts, renewals) is in the corpus but is
    # never searchable: there is no query for which it is the right answer, so
    # this is a clause rather than a parameter. api.stats still counts it, so the
    # 1,691 → 1,647 gap on the corpus page stays explained.
    clauses.append("a.is_transactional = 0")
    return (" AND ".join(clauses), values)


def search(
    conn: sqlite3.Connection, params: SearchParams, vocab: dict[str, dict]
) -> dict:
    import time

    started = time.perf_counter()
    if vocab:
        match, expansions = expand_query(params.q, vocab)
    else:
        match, expansions = fts_query(params.q), []
    rewritten = match

    where, values = _filters(params)
    where_sql = f" AND {where}" if where else ""

    total = conn.execute(
        "SELECT count(*) FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid"
        " JOIN articles a ON a.message_id = c.message_id"
        f" WHERE chunks_fts MATCH ?{where_sql}",
        [match, *values],
    ).fetchone()[0]

    extra: dict[str, Any] = {}
    if params.group == "article":
        results, article_total = _grouped(conn, params, match, where_sql, values)
        extra["article_total"] = article_total
    else:
        rows = conn.execute(
            f"{CHUNK_FIELDS}{CHUNK_JOINS} WHERE chunks_fts MATCH ?{where_sql}"
            " ORDER BY rank, c.id LIMIT ? OFFSET ?",
            [*SNIPPET_ARGS, match, *values, params.limit, params.offset],
        ).fetchall()
        results = [_result(row) for row in rows]

    facets = _facets(conn, match, where, values)
    return {
        "query": {
            "raw": params.q,
            "rewritten": rewritten,
            "expansions": expansions,
        },
        "total": total,
        "limit": params.limit,
        "offset": params.offset,
        "took_ms": int((time.perf_counter() - started) * 1000),
        "results": results,
        "facets": facets,
        **extra,
    }


def _result(row: tuple) -> dict:
    (
        chunk_id,
        message_id,
        start_char,
        end_char,
        sent_at,
        subject,
        kind,
        sender,
        rank,
        snippet,
    ) = row
    return {
        "chunk_id": chunk_id,
        "message_id": message_id,
        "sent_at": sent_at,
        "subject": subject,
        "kind": kind,
        "sender": sender,
        "score": round(-float(rank), 6),
        "snippet": snippet,
        "start_char": start_char,
        "end_char": end_char,
        "article_href": f"/article/{message_id}#c{chunk_id.replace(':', '-')}",
    }


def _grouped(
    conn: sqlite3.Connection,
    params: SearchParams,
    match: str,
    where_sql: str,
    values: list[Any],
) -> tuple[list[dict], int]:
    """One row per article, its other matching chunks folded under the best one.

    The ranking is a page of *articles*: an article takes the rank of its best
    chunk, articles are ordered by that rank, and ``limit``/``offset`` count
    articles. Paging on passages instead would show the same article again on the
    next page — on "smiling curve" four of page two's fourteen articles were on
    page one, and page three was almost entirely repeats.
    """
    where = f" WHERE chunks_fts MATCH ?{where_sql}"
    # bm25() may be used only in a query that carries the MATCH — never inside an
    # aggregate, and not in an ordinary subquery either ("unable to use function
    # bm25 in the requested context", because it is evaluated in the outer
    # context). MATERIALIZED is what makes the CTE a real boundary: SQLite then
    # computes the rank per chunk inside the MATCH query, and the aggregate reads
    # a plain column. It costs a full pass over the matching chunks — 40 ms on the
    # 1,691-article index for the worst case, the word "the" — against 1 ms for
    # the ungrouped query, which is the price of ordering articles by relevance.
    page = conn.execute(
        "WITH ranked AS MATERIALIZED ("
        "SELECT c.message_id AS message_id, bm25(chunks_fts, 10.0, 1.0) AS rank"
        f"{CHUNK_JOINS}{where}"
        ") SELECT message_id, min(rank) AS rank, count(*) AS matched FROM ranked"
        " GROUP BY message_id ORDER BY rank, message_id LIMIT ? OFFSET ?",
        [match, *values, params.limit, params.offset],
    ).fetchall()
    article_total = conn.execute(
        f"SELECT count(DISTINCT a.message_id){CHUNK_JOINS}{where}", [match, *values]
    ).fetchone()[0]
    if not page:
        return [], article_total

    ids = [message_id for message_id, _, _ in page]
    rows = conn.execute(
        f"{CHUNK_FIELDS}{CHUNK_JOINS}{where}"
        f" AND a.message_id IN ({','.join('?' * len(ids))}) ORDER BY rank, c.id",
        [*SNIPPET_ARGS, match, *values, *ids],
    ).fetchall()

    by_article: dict[int, list[dict]] = {}
    for row in rows:
        by_article.setdefault(row[1], []).append(_result(row))

    results = []
    for message_id, _, matched in page:
        chunks = by_article.get(message_id, [])
        if not chunks:
            continue
        lead, *rest = chunks
        results.append(
            {
                **lead,
                # How many passages of this article matched at all, and how many
                # of them travelled with the row: the card says "3 more passages"
                # without a second round trip.
                "matched_passages": matched,
                "more_total": len(rest),
                "more_passages": rest[:MAX_MORE_PASSAGES],
            }
        )
    return results, article_total


def _facets(
    conn: sqlite3.Connection, match: str, where: str, values: list[Any]
) -> dict:
    """Facet counts under the current filter, from the same statement family.

    Shipping facets inside ``/search`` is deliberate (§3 NEW): a second
    ``/facets`` call renders counts that lag the result set by one round trip,
    which is the classic faceted-search bug.
    """
    where_sql = f" AND {where}" if where else ""
    base = (
        " FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid"
        " JOIN articles a ON a.message_id = c.message_id"
        f" WHERE chunks_fts MATCH ?{where_sql}"
    )

    def grouped(expression: str) -> list[tuple]:
        return conn.execute(
            f"SELECT {expression} AS bucket, count(DISTINCT c.message_id), count(*)"
            f"{base} GROUP BY bucket ORDER BY bucket",
            [match, *values],
        ).fetchall()

    by_year = [
        {"year": int(year), "articles": articles, "count": count}
        for year, articles, count in grouped("substr(a.sent_at, 1, 4)")
        if year is not None
    ]
    by_kind = [
        {"kind": kind, "articles": articles, "count": count}
        for kind, articles, count in conn.execute(
            f"SELECT a.kind, count(DISTINCT c.message_id), count(*){base}"
            " GROUP BY a.kind ORDER BY a.kind",
            [match, *values],
        ).fetchall()
    ]
    by_sender = [
        {"sender": sender or "", "articles": articles, "count": count}
        for sender, articles, count in conn.execute(
            f"SELECT a.sender, count(DISTINCT c.message_id), count(*){base}"
            " GROUP BY a.sender ORDER BY a.sender",
            [match, *values],
        ).fetchall()
    ]
    images = {"true": 0, "false": 0}
    for flag, articles in conn.execute(
        "SELECT CASE WHEN a.n_images > 0 THEN 'true' ELSE 'false' END AS bucket,"
        f" count(DISTINCT c.message_id){base} GROUP BY bucket",
        [match, *values],
    ).fetchall():
        images[flag] = articles
    return {
        "by_year": by_year,
        "by_kind": by_kind,
        "by_sender": by_sender,
        "has_images": images,
    }


def article(conn: sqlite3.Connection, message_id: int) -> dict:
    row = conn.execute(
        "SELECT message_id, sent_at, subject, kind, sender, from_header, chars, words,"
        " est_tokens, is_transactional, n_images FROM articles WHERE message_id = ?",
        (message_id,),
    ).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "no such article", {"message_id": message_id})

    (
        message_id,
        sent_at,
        subject,
        kind,
        sender,
        from_header,
        chars,
        words,
        est_tokens,
        is_transactional,
        n_images,
    ) = row

    chunk_rows = conn.execute(
        "SELECT chunk_id, ordinal, start_char, end_char, text, chunker_version, text_sha256"
        " FROM chunks WHERE message_id = ? ORDER BY ordinal",
        (message_id,),
    ).fetchall()

    # Reassemble the body by placing each slice at its recorded offset: the
    # chunks overlap by design (150 characters), so concatenating them duplicates
    # prose at every seam. What has been placed so far ends at `covered`, so the
    # part of this chunk that is new is everything past `covered - start_char` —
    # which is a different question from how long the pieces are, and the two
    # differ by exactly the overlap.
    pieces: list[str] = []
    covered = 0
    for _chunk_id, _ordinal, start_char, end_char, text, _chunker, _hash in chunk_rows:
        skip = max(0, covered - start_char)
        pieces.append(text[skip:])
        covered = max(covered, end_char)
    body = "".join(pieces)

    images = [
        {
            "url_path": url_path,
            "asset": asset,
            "sha256": sha256,
            "bytes": size,
            "href": f"/asset/{Path(asset).name}" if asset else None,
        }
        for url_path, asset, sha256, size in conn.execute(
            "SELECT url_path, asset, sha256, bytes FROM article_images"
            " WHERE message_id = ? ORDER BY ordinal",
            (message_id,),
        ).fetchall()
    ]

    derived_row = conn.execute(
        "SELECT s.flattener_version, a.normalizer_version, a.text_sha256"
        " FROM articles a, corpus_stats s WHERE a.message_id = ? AND s.singleton = 1",
        (message_id,),
    ).fetchone()
    archive_row = conn.execute(
        "SELECT archive_sha256, archive_bytes FROM articles WHERE message_id = ?",
        (message_id,),
    ).fetchone()

    return {
        "message_id": message_id,
        "sent_at": sent_at,
        "subject": subject,
        "kind": kind,
        "sender": sender,
        "from_header": from_header,
        "chars": chars,
        "words": words,
        "est_tokens": est_tokens,
        "is_transactional": bool(is_transactional),
        "derived": {
            "flattener_version": derived_row[0],
            "normalizer_version": derived_row[1],
            "text_sha256": derived_row[2],
        },
        "archive": {
            "sha256": archive_row[0],
            "bytes": archive_row[1],
            "path": f"raw/{message_id}.eml",
        },
        "chunks": [
            {
                "chunk_id": chunk_id,
                "ordinal": ordinal,
                "start_char": start_char,
                "end_char": end_char,
            }
            for chunk_id, ordinal, start_char, end_char, _text, _c, _h in chunk_rows
        ],
        "text": body,
        "images": images,
        "n_images": n_images,
    }


def stats(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT articles, searchable_articles, chunks, first_sent_at, last_sent_at,"
        " transactional, articles_with_images, images_distinct, built_at,"
        " flattener_version, chunker_version FROM corpus_stats WHERE singleton = 1"
    ).fetchone()
    if row is None:
        raise ApiError(503, "index_unbuilt", "corpus_stats is missing")
    (
        articles,
        searchable_articles,
        chunks,
        first_sent_at,
        last_sent_at,
        transactional,
        articles_with_images,
        images_distinct,
        built_at,
        flattener_version,
        chunker_version,
    ) = row

    return {
        "articles": articles,
        "searchable_articles": searchable_articles,
        "chunks": chunks,
        "first": first_sent_at,
        "last": last_sent_at,
        "by_year": [
            {"year": year, "articles": count, "chars": chars}
            for year, count, chars in conn.execute(
                "SELECT year, articles, chars FROM stats_by_year ORDER BY year"
            )
        ],
        "by_kind": [
            {"kind": kind, "articles": count}
            for kind, count in conn.execute(
                "SELECT kind, articles FROM stats_by_kind ORDER BY articles DESC"
            )
        ],
        "by_sender": [
            {"sender": sender, "articles": count}
            for sender, count in conn.execute(
                "SELECT sender, articles FROM stats_by_sender ORDER BY articles DESC"
            )
        ],
        "transactional": transactional,
        "images": {
            "distinct_assets": images_distinct,
            "articles_with_images": articles_with_images,
        },
        "notes": [
            {
                "code": code,
                "year": year,
                "articles": count,
                "neighbouring_mean": mean,
                "message": message,
            }
            for code, year, count, mean, message in conn.execute(
                "SELECT code, year, articles, neighbouring_mean, message FROM stats_notes"
                " ORDER BY code"
            )
        ],
        "index": {
            "flattener_version": flattener_version,
            "chunker_version": chunker_version,
            "built_at": built_at,
        },
    }


def health(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT articles, chunks, built_at FROM corpus_stats WHERE singleton = 1"
    ).fetchone()
    if row is None:
        raise ApiError(503, "index_degraded", "corpus_stats is empty")
    return {"status": "ok", "articles": row[0], "chunks": row[1], "built_at": row[2]}
