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
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .notes import daily_from_month, stats_notes

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
    # Type-ahead (design note §7): the last token is treated as half-typed and
    # matched by prefix. Off by default, so the MCP adapter, a bookmarked URL and
    # `curl` keep meaning exactly what they meant — a complete question.
    partial: bool = False


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


def _has_filter(params: SearchParams) -> bool:
    """Whether anything narrows the corpus without a query.

    This is what separates "browse 2019" from "give me everything", and it is the
    condition under which an empty ``q`` is legitimate (see ``_browse``).
    """
    return bool(
        params.start
        or params.end
        or params.kinds
        or params.senders
        or params.has_images is not None
    )


def parse_search_params(query: dict[str, list[str]]) -> SearchParams:
    """Turn raw querystring pairs into validated parameters."""
    raw_q = (query.get("q") or [""])[0].strip()
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

    params = SearchParams(
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
        partial=bool(parse_bool((query.get("partial") or [None])[0], name="partial")),
    )
    if not params.q and not _has_filter(params):
        # An empty q is 400 unless a filter narrows the corpus. "Everything" is
        # not a query — /stats answers it in aggregate — but "2019" and "every
        # interview" are questions a reader asks, and the UI's year and kind
        # chips ask them before anyone has typed anything.
        raise ApiError(400, "bad_request", "q is required", {"param": "q"})
    return params


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


#: A reader typing one character is not asking a question yet: a 1-character
#: prefix matches a large slice of the vocabulary and ranks arbitrarily. Two is
#: the floor, and below it the UI has not started rather than showing zero.
MIN_PREFIX_CHARS = 2

#: How many unstemmed terms a half-typed token may expand to. The group is ORed
#: into the porter index, so this is also the width of the ranking done per
#: keystroke; 20 was enough for every prefix tried by hand and keeps the
#: expression small.
MAX_RESOLVED_TERMS = 20


def resolve_prefix(conn: sqlite3.Connection, token: str) -> list[str]:
    """Unstemmed terms beginning with ``token``, most frequent first.

    This is the resolver from design note §4.4, reduced to its cheapest form:
    ``terms`` holds the corpus vocabulary as ``unicode61`` tokenised it (no
    stemming), so "which real words start with ``aggregat``" is an index range
    scan, not a ranking pass over a second index. The terms are handed back to
    ``chunks_fts``, which stems them — one definition of a result, one ranker.

    The upper bound is the prefix with the largest code point appended, which is
    the standard way to say "starts with" to a bytewise TEXT index without giving
    the index up to ``LIKE``.
    """
    keyword = token.casefold()
    if len(keyword) < MIN_PREFIX_CHARS:
        return []
    try:
        rows = conn.execute(
            "SELECT term FROM terms WHERE term >= ? AND term < ?"
            " ORDER BY doc DESC, term LIMIT ?",
            (keyword, keyword + "\U0010ffff", MAX_RESOLVED_TERMS),
        ).fetchall()
    except sqlite3.OperationalError:
        # An index built before `terms` existed. The image and the index are
        # deployed independently — a new image against yesterday's file is a real
        # window, not a hypothetical — so this degrades to whole-token matching
        # (the prefix on its own) instead of 500ing on every keystroke.
        return []
    return [row[0] for row in rows]


def _join(head_expression: str, tail_expression: str) -> str:
    return (
        f"{head_expression} AND {tail_expression}"
        if head_expression
        else tail_expression
    )


def partial_expression(
    conn: sqlite3.Connection, raw: str, vocab: dict[str, dict]
) -> tuple[str, list[dict], list[str]]:
    """The expression for a half-typed query, plus what it was taken to mean.

    The shape is *completed tokens AND (prefix OR resolved terms)*::

        "commoditize" "your" AND ("comp"* OR "compete" OR "complement" ...)

    Both halves of that group are needed. The porter prefix still works whenever
    the typed text is short enough to be a stem prefix (``"aggr"*`` → 2,253
    chunks), and the resolved terms are what work when it is not
    (``"aggregat"*`` → nothing at all, because the index holds ``aggreg``).

    Only the last token is prefixed: earlier tokens are words the reader has
    finished, and turning them into prefixes would widen the result set and
    flatten the ranking for no reader benefit.

    Expansions are computed over the completed tokens only (design note §4.5): an
    unfinished word is not a word, and expanding it would make the result set jump
    between one keystroke and the next.

    A one-character tail constrains nothing (see below): the results are those of
    the completed words, so the reader never sees a mid-word zero.
    """
    words = _WORD_RE.findall(raw)
    if not words:
        raise ApiError(400, "bad_request", "q contains no searchable words", {"q": raw})
    head, tail = words[:-1], words[-1]

    if head:
        head_text = " ".join(head)
        if vocab:
            head_expression, expansions = expand_query(head_text, vocab)
        else:
            head_expression = " ".join(f'"{word}"' for word in head)
            expansions = []
    else:
        head_expression, expansions = "", []

    if len(tail) < MIN_PREFIX_CHARS:
        # A single character is not yet a word, and both obvious readings of it
        # are wrong. Prefixing it is unaffordable — the index's `prefix='2 3 4'`
        # cannot serve one character, so it degenerates to a scan — and quoting it
        # whole searches for the literal letter, which is what made typing
        # "smiling c" on the way to "smiling curve" return **0** and read as "the
        # archive has nothing" (measured on a phone, 2026-11-07: the one
        # zero-flicker the gate recorded). So it does not constrain anything yet:
        # the answer is the one the completed words already gave, which is what
        # was on screen a keystroke ago. A query that is *only* this letter has
        # nothing to fall back to, so it is searched as the word it is.
        if not head_expression:
            return f'"{tail}"', expansions, []
        return head_expression, expansions, []

    resolved = resolve_prefix(conn, tail)
    alternatives = [f'"{tail}"*']
    alternatives.extend(f'"{term}"' for term in resolved if term != tail.casefold())
    return (
        _join(head_expression, "(" + " OR ".join(alternatives) + ")"),
        expansions,
        resolved,
    )


def load_vocab(path: Path | None) -> dict[str, dict]:
    """Read the query-time synonym table, tolerating its absence.

    Phase 1 ships ``vocab.yaml`` optionally (§6 step 6 keeps or drops it on
    judgement), so a missing file means "no expansions", not an error. The file
    is parsed by the small loader in :mod:`stratechery_search.vocab`.
    """
    if path is None or not path.exists():
        return {}
    from .vocab import VocabError, load_vocabulary

    try:
        return load_vocabulary(path)
    except VocabError as exc:
        raise ApiError(
            503,
            "vocab_invalid",
            "vocab.yaml is not in the accepted shape",
            {"path": str(path), "detail": str(exc)},
        ) from exc


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

    if not expansions:
        # Nothing in the table fired. Return the plain expression byte for byte
        # rather than the ``AND``-joined equivalent below: ``rewritten`` is part
        # of the response and the UI shows it, so "a query that matches no entry
        # behaves as before" has to mean identical output, not merely an
        # expression that ranks the same.
        return fts_query(raw), []

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


BROWSE_SNIPPET_CHARS = 240


_SUBJECT_TAIL = re.compile(r"\s*\([^()]*\)\s*$")


def _opening(text: str | None, subject: str | None) -> str:
    """How an article opens, for a card that has no match to excerpt.

    Some issues begin by repeating the subject line, which the card already shows
    an inch above the snippet, so that repeat is dropped when it is exact. The rest
    is collapsed to one line: ``snippet()`` leaves newlines in, and the browser
    would collapse them anyway.
    """
    body = (text or "").lstrip()
    # The archived subject carries the "(Stratechery Update 7-15-2026)" tail that
    # the card strips for display, while the title inside the body does not, so the
    # comparison has to be against the untailed title or it never matches.
    title = _SUBJECT_TAIL.sub("", subject or "").strip()
    if title and body.startswith(title):
        body = body[len(title) :].lstrip(" \n:-—")
    return " ".join(body.split())[:BROWSE_SNIPPET_CHARS]


def _browse(conn: sqlite3.Connection, params: SearchParams, started: float) -> dict:
    """The corpus newest-first under a filter, with no query and no ranking.

    An empty ``q`` only reaches here when ``_has_filter`` is true, so this answers
    "2019" or "every interview" rather than "everything". There is nothing to rank
    against — and ``bm25()`` cannot be computed without a MATCH anyway — so the
    order is the archive's own: newest first, one row per article under
    ``group=article`` (each row shows how the article opens), one row per passage
    otherwise.

    Facets are still computed, because a reader who has chosen 2019 wants to see
    that 2019 is what they chose and how the other years compare.
    """
    where, values = _filters(params)
    total, article_total = conn.execute(
        "SELECT count(*), count(DISTINCT c.message_id)"
        " FROM chunks c JOIN articles a ON a.message_id = c.message_id"
        f" WHERE {where}",
        values,
    ).fetchone()

    if params.group != "article":
        rows = conn.execute(
            "SELECT c.chunk_id, c.message_id, c.start_char, c.end_char, a.sent_at,"
            " a.subject, a.kind, a.sender, 0.0 AS rank,"
            " c.text AS snip"
            " FROM chunks c JOIN articles a ON a.message_id = c.message_id"
            f" WHERE {where}"
            " ORDER BY a.sent_at DESC, c.message_id DESC, c.ordinal"
            " LIMIT ? OFFSET ?",
            [*values, params.limit, params.offset],
        ).fetchall()
        results = [_result((*row[:9], _opening(row[9], row[5]))) for row in rows]
    else:
        page = [
            row[0]
            for row in conn.execute(
                "SELECT a.message_id FROM articles a"
                f" WHERE {where} ORDER BY a.sent_at DESC, a.message_id DESC"
                " LIMIT ? OFFSET ?",
                [*values, params.limit, params.offset],
            )
        ]
        # Every filter lives on `articles`, so the page is chosen without touching
        # chunks; the lead chunk of each chosen article is fetched afterwards.
        # Deliberately not `WHERE c.ordinal = 0`: the row is the article's first
        # passage because it is the lowest ordinal in the rows that came back, not
        # because the chunker promised to start at zero.
        results = []
        if page:
            rows = conn.execute(
                "SELECT c.chunk_id, c.message_id, c.start_char, c.end_char,"
                " a.sent_at, a.subject, a.kind, a.sender, 0.0 AS rank,"
                " c.text AS snip"
                " FROM chunks c JOIN articles a ON a.message_id = c.message_id"
                f" WHERE c.message_id IN ({','.join('?' * len(page))})"
                " ORDER BY c.message_id, c.ordinal",
                page,
            ).fetchall()
            first: dict[int, tuple] = {}
            for row in rows:
                first.setdefault(row[1], row)
            for message_id in page:
                row = first.get(message_id)
                if row is None:
                    continue
                results.append(
                    {
                        **_result((*row[:9], _opening(row[9], row[5]))),
                        # Nothing "matched", so nothing is folded underneath: the
                        # count is what the card needs to not claim otherwise.
                        "matched_passages": 1,
                        "more_total": 0,
                        "more_passages": [],
                    }
                )

    return {
        "query": {"raw": "", "rewritten": "", "expansions": [], "resolved": []},
        "browse": True,
        "total": total,
        "article_total": article_total,
        "limit": params.limit,
        "offset": params.offset,
        "took_ms": int((time.perf_counter() - started) * 1000),
        "results": results,
        "facets": _facets(conn, None, where, values),
    }


def search(
    conn: sqlite3.Connection, params: SearchParams, vocab: dict[str, dict]
) -> dict:
    started = time.perf_counter()
    if not params.q:
        return _browse(conn, params, started)
    if params.partial:
        match, expansions, resolved = partial_expression(conn, params.q, vocab)
    elif vocab:
        match, expansions = expand_query(params.q, vocab)
        resolved = []
    else:
        match, expansions = fts_query(params.q), []
        resolved = []
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
            # What a half-typed last token was taken to mean (design note §4.4).
            # Surfaced for the same reason expansions are: a rewrite the reader
            # cannot see is a rewrite that cannot be debugged.
            "resolved": resolved,
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
    conn: sqlite3.Connection, match: str | None, where: str, values: list[Any]
) -> dict:
    """Facet counts under the current filter, from the same statement family.

    Shipping facets inside ``/search`` is deliberate (§3 NEW): a second
    ``/facets`` call renders counts that lag the result set by one round trip,
    which is the classic faceted-search bug.
    """
    where_sql = f" AND {where}" if where else ""
    if match is None:
        # Browse mode: no MATCH, so the same counts run over the filtered corpus.
        base = (
            " FROM chunks c JOIN articles a ON a.message_id = c.message_id"
            f" WHERE {where or '1 = 1'}"
        )
        args: list[Any] = list(values)
    else:
        base = (
            " FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid"
            " JOIN articles a ON a.message_id = c.message_id"
            f" WHERE chunks_fts MATCH ?{where_sql}"
        )
        args = [match, *values]

    def grouped(expression: str) -> list[tuple]:
        return conn.execute(
            f"SELECT {expression} AS bucket, count(DISTINCT c.message_id), count(*)"
            f"{base} GROUP BY bucket ORDER BY bucket",
            args,
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
            args,
        ).fetchall()
    ]
    by_sender = [
        {"sender": sender or "", "articles": articles, "count": count}
        for sender, articles, count in conn.execute(
            f"SELECT a.sender, count(DISTINCT c.message_id), count(*){base}"
            " GROUP BY a.sender ORDER BY a.sender",
            args,
        ).fetchall()
    ]
    images = {"true": 0, "false": 0}
    for flag, articles in conn.execute(
        "SELECT CASE WHEN a.n_images > 0 THEN 'true' ELSE 'false' END AS bucket,"
        f" count(DISTINCT c.message_id){base} GROUP BY bucket",
        args,
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

    by_year = [
        {"year": year, "articles": count, "chars": chars}
        for year, count, chars in conn.execute(
            "SELECT year, articles, chars FROM stats_by_year ORDER BY year"
        )
    ]
    searchable_by_year = [
        {"year": year, "articles": count}
        for year, count in conn.execute(
            "SELECT substr(sent_at, 1, 4) AS year, count(*) FROM articles"
            " WHERE is_transactional = 0 GROUP BY year ORDER BY year"
        )
    ]
    searchable_by_kind = [
        {"kind": kind, "articles": count}
        for kind, count in conn.execute(
            "SELECT kind, count(*) FROM articles WHERE is_transactional = 0"
            " GROUP BY kind ORDER BY count(*) DESC, kind"
        )
    ]
    # When sustained daily coverage starts. The corpus page states it beside the
    # per-year chart, because before that boundary a short bar means "not
    # subscribed yet", not "published less". Not the *first* daily update: two
    # free sample updates in 2017 would put that in September 2017, fourteen
    # months early.
    daily_from = daily_from_month(
        {
            month
            for (month,) in conn.execute(
                "SELECT DISTINCT substr(sent_at, 1, 7) FROM articles"
                " WHERE is_transactional = 0 AND kind IN ('daily', 'update')"
            )
        }
    )

    return {
        "articles": articles,
        "searchable_articles": searchable_articles,
        "chunks": chunks,
        "first": first_sent_at,
        "last": last_sent_at,
        "by_year": by_year,
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
        # The landing screen offers chips, and a chip is a question the API has
        # to be able to answer. by_year/by_kind above describe the whole archive,
        # transaction mail included, but search and browse only reach the
        # searchable part — so a chip built from those would promise a year that
        # returns nothing (the fixture corpus's only 2024 article is an access
        # code). These slices are the same query restricted to what the reader
        # can actually reach.
        "searchable": {
            "by_year": searchable_by_year,
            "by_kind": searchable_by_kind,
            "daily_from": daily_from,
        },
        "transactional": transactional,
        "images": {
            "distinct_assets": images_distinct,
            "articles_with_images": articles_with_images,
        },
        # Recomputed here rather than read from the build-time `stats_notes`
        # snapshot: the note is a pure function of the yearly counts (which this
        # response already carries) and the date the daily update starts, so a
        # snapshot can only ever be stale — it kept describing a 2017 that the
        # page had stopped drawing long after the chart caption explained it.
        "notes": stats_notes(
            {bucket["year"]: bucket["articles"] for bucket in by_year},
            int(daily_from[:4]) if daily_from else None,
        ),
        "index": {
            "flattener_version": flattener_version,
            "chunker_version": chunker_version,
            "built_at": built_at,
        },
    }


def health(
    conn: sqlite3.Connection,
    vocab_path: Path | None = None,
    vocab: dict[str, dict] | None = None,
) -> dict:
    """The index's own counts, plus whether the synonym table is loaded.

    ``vocab`` is reported because "is the rewrite firing?" was otherwise
    unanswerable from a running service: a file the image never copied in looks
    exactly like a healthy one, except that it expands nothing. ``terms: 0`` is
    therefore the honest answer, and the path says which file produced it.
    """
    row = conn.execute(
        "SELECT articles, chunks, built_at FROM corpus_stats WHERE singleton = 1"
    ).fetchone()
    if row is None:
        raise ApiError(503, "index_degraded", "corpus_stats is empty")
    return {
        "status": "ok",
        "articles": row[0],
        "chunks": row[1],
        "built_at": row[2],
        "vocab": {
            "path": None if vocab_path is None else str(vocab_path),
            "terms": len(vocab or {}),
        },
    }
