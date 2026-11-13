"""Query layer behind the phase-1 HTTP contract.

Everything here reads ``search.sqlite`` and nothing writes it: the index is
built wholesale by ``build_index`` and is read-only at runtime, so the service
needs no write path, no lock discipline and no cache invalidation.

The response shapes are fixed by phase-1 scope contract §3 and asserted by
``tests/test_api.py``; they are the contract the Svelte app is written against,
so changing them is a breaking change rather than an implementation detail.
"""

from __future__ import annotations

import html
import re
import sqlite3
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from . import embed as embed_module
from . import fusion
from . import similar as similar_module
from .facets import FacetError
from .filters import FilterSet
from .notes import daily_from_month, stats_notes
from .vector_store import StoreError

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

#: bm25 column weights for ``chunks_fts``, in column order: (text, subject).
#:
#: The subject is the article's own name for what it is about, written by the
#: person who wrote the article, and it is carried by **every** chunk of that
#: article. A weight of 1 against the body's 10 therefore made a subject match
#: worth one occurrence in a paragraph the body outweighed ten to one, and the
#: effect was measurable: a query that *names an article* — `aggregation theory`
#: — put that article 8th, because 308 chunks matched both words and the top
#: three scores sat within half a percent of each other, broken by document
#: length rather than relevance (`scripts/measure_bm25_weights.py`).
#:
#: 2.0 / 10.0 is the measured choice, not a round number. Over 192 articles
#: addressed by their own subject line, weighting the subject higher moves the
#: article to first place for 97.9% of them against 90.6% shipped; over 200
#: sampled passages searched with an 8-word window from their own middle, the
#: same change costs 1.5 points of top-1 (74.5% -> 73.0%). Going past 5:1
#: stops buying subject accuracy and keeps costing passage accuracy; going all
#: the way to 0/10 — the design note's original weights — costs 7.5 points of
#: top-1, which is why the body still counts.
BM25_TEXT_WEIGHT = 2.0
BM25_SUBJECT_WEIGHT = 10.0

# The chunk projection and its joins live here rather than inline: the grouped
# path runs them twice (once ranked per chunk, once for the page) and a copy that
# drifted would make the two disagree about what "this article's passages" means.
CHUNK_FIELDS = (
    "SELECT c.chunk_id, c.message_id, c.start_char, c.end_char, a.sent_at,"
    f" a.subject, a.kind, a.sender, bm25(chunks_fts, {BM25_TEXT_WEIGHT},"
    f" {BM25_SUBJECT_WEIGHT}) AS rank,"
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
    # 3-pre: the entity facet. A *closed* vocabulary — the ids come from
    # entities.yaml via the built facet index — so an unknown id is a 400 and not
    # an empty result that reads as "no articles". Resolution to article ids
    # happens once per request (see `_entity_articles`) and is then handed to both
    # arms as one predicate, which is the same discipline `FilterSet` exists for.
    entities: tuple[str, ...] = ()
    # Phase 2: fuse the BM25 arm with a vector arm over LanceDB. **On by
    # default since 2026-11-07** (`hybrid=0` turns it off), because the corpus
    # pass is complete, parity is asserted, and the flag stops being a flag once
    # the thing it guards is deployed — a feature nobody switches on is not a
    # feature. Three paths do not reach it: `partial=1` (type-ahead fires a
    # request per keystroke and a live embed call on each is a latency question,
    # not a money one, §4.2), an empty `q` (nothing to embed), and a server with
    # no store — that degrades to BM25 rather than taking search down, because a
    # default that can 500 every search is not a default. `hybrid_explicit`
    # separates "asked for and cannot be honoured" (a 500) from "would have
    # happened anyway" (a degraded 200).
    hybrid: bool = True
    hybrid_explicit: bool = False


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
        or params.entities
        or params.has_images is not None
    )


def parse_search_params(query: dict[str, list[str]]) -> SearchParams:
    """Turn raw querystring pairs into validated parameters."""
    raw_q = (query.get("q") or [""])[0].strip()
    hybrid_raw = parse_bool((query.get("hybrid") or [None])[0], name="hybrid")
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

    entities = tuple(
        dict.fromkeys(
            value
            for value in (
                part.strip()
                for raw in query.get("entity", [])
                for part in raw.split(",")
            )
            if value
        )
    )

    params = SearchParams(
        q=raw_q,
        entities=entities,
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
        hybrid=True if hybrid_raw is None else hybrid_raw,
        hybrid_explicit=hybrid_raw is not None,
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


#: A reader typing is not asking a question yet, and the floor is three, not two.
#: A 2-character prefix was measured on a phone (2026-11-07) at **211–218 ms of
#: server time** and 20 KB gzipped — `co` alone touches 29,274 chunks and ORs 20
#: resolved terms — against 12–75 ms for everything else. That is clause 4 of the
#: type-ahead gate ("took_ms on a 2-char prefix is not pathological; if it is
#: slow, raise the min length to 3 and say so"), and it was slow. The cost is
#: that `co` finds nothing until it is `com`; the three-character floor is
#: quoted in ordinary search either way.
MIN_PREFIX_CHARS = 3

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
        # Below the floor, the unfinished word constrains nothing yet. Both
        # obvious readings of it are wrong: prefixing is unaffordable (a
        # 2-character prefix cost 211–218 ms of server time and 20 KB gzipped on
        # a phone, 2026-11-07), and quoting it whole searches for a word that has
        # not been finished — which is what made typing "smiling c" on the way to
        # "smiling curve" return **0** and read as "the archive has nothing", the
        # one zero-flicker the gate recorded. So the answer is the one the
        # completed words already gave, which is what was on screen a keystroke
        # ago. A query that is *only* this fragment has nothing to fall back to,
        # so it is searched as the word it is.
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


def _filters(
    params: SearchParams, article_ids: tuple[int, ...] | None = None
) -> tuple[str, list[Any]]:
    """The SQL rendering of the one eligibility predicate.

    The predicate itself lives in :mod:`.filters` because the vector arm needs
    the same question asked of LanceDB, and two hand-written predicates is how
    the two arms come to disagree about what "2023, interviews, no transactions"
    means — a drift that looks like a ranking problem forever.

    ``article_ids`` is the entity facet, already resolved to article ids. It is
    passed rather than looked up here because resolving it needs the facet
    index, which is not a property of the request.
    """
    return FilterSet.from_params(params, article_ids=article_ids).sql()


def _sideways(
    params: SearchParams, article_ids: tuple[int, ...] | None = None
) -> dict[str, tuple[str, list[Any]]]:
    """Per-dimension predicates with *that dimension's own selection removed*.

    The rule the entity facet already followed (§9), applied to the dimensions
    that shipped in phase 1: a count is only useful if the reader can still see
    what picking a *different* value would give them. Computed under the full
    filter, choosing "daily update" removes every other kind from the panel — not
    because the corpus lost them, but because the count asked the wrong question.
    Reported from the phone: "I pick one and then I can't select the other".
    """
    return {
        "year": _filters(replace(params, start=None, end=None), article_ids),
        "kind": _filters(replace(params, kinds=()), article_ids),
        "images": _filters(replace(params, has_images=None), article_ids),
    }


def _entity_articles(params: SearchParams, facet_index: Any) -> tuple[int, ...] | None:
    """Resolve ``entity=`` to article ids, once, for both arms.

    ``None`` means "no entity filter". An empty tuple is a real answer — entities
    that never co-occur — and must not be confused with it: rendering ``None`` as
    "everything" and ``()`` as "everything" is the same bug as an unmatched query
    returning the corpus.
    """
    if not params.entities:
        return None
    if facet_index is None:
        raise ApiError(
            400,
            "facet_unbuilt",
            "entity filtering needs the facet index;"
            " run `python -m stratechery_search.build_facets`",
            {"entity": list(params.entities)},
        )
    try:
        return tuple(sorted(facet_index.articles_for(params.entities)))
    except FacetError as exc:
        raise ApiError(
            400, "unknown_entity", str(exc), {"entity": list(params.entities)}
        ) from exc


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


def _browse(
    conn: sqlite3.Connection,
    params: SearchParams,
    started: float,
    *,
    article_ids: tuple[int, ...] | None = None,
    facet_index: Any = None,
) -> dict:
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
    where, values = _filters(params, article_ids)
    side_where, side_values = _filters(params)
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

    facets = _facets(
        conn, None, where, values, sideways=_sideways(params, article_ids)
    )
    _add_entity_facets(
        facets, conn, None, side_where, side_values, facet_index=facet_index
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
        "facets": facets,
    }


def search(
    conn: sqlite3.Connection,
    params: SearchParams,
    vocab: dict[str, dict],
    hybrid: Any = None,
    facet_index: Any = None,
) -> dict:
    """The one search entry point: BM25 alone, or BM25 fused with the vector arm.

    Degradation is scoped to the *gateway*: if the embedding call fails or times
    out, the reader gets the BM25 answer with a ``hybrid.degraded`` note rather
    than a 503, because search must not become dependent on a metered API. A
    store that disagrees with the index is **not** a degradation — that is a
    deployment mistake and raises (§4.5c), scoped to the flag so `hybrid=0`
    keeps serving.
    """
    started = time.perf_counter()
    article_ids = _entity_articles(params, facet_index)
    if params.hybrid and not params.partial:
        if hybrid is None and not params.hybrid_explicit:
            # No store wired into this process and nobody asked for one (tests,
            # the MCP adapter, a bare `python -m` run). Hybrid is the default, so
            # this has to be an ordinary BM25 answer: a default that can 500
            # every search is not a default. An *explicit* `hybrid=1` still
            # fails loudly below.
            return _bm25_search(
                conn,
                params,
                vocab,
                started,
                article_ids=article_ids,
                facet_index=facet_index,
            )
        try:
            return _hybrid_search(
                conn,
                params,
                vocab,
                hybrid,
                started,
                article_ids=article_ids,
                facet_index=facet_index,
            )
        except embed_module.EmbedError as error:
            degraded = _bm25_search(
                conn,
                params,
                vocab,
                started,
                article_ids=article_ids,
                facet_index=facet_index,
            )
            degraded["hybrid"] = {
                "enabled": False,
                "degraded": True,
                "reason": error.code,
            }
            return degraded
        except StoreError as error:
            # A store that disagrees with the index is a deployment mistake, not
            # an operational condition: it must look like a failure (§4.5c).
            raise ApiError(500, error.code, str(error), error.detail) from error
    return _bm25_search(
        conn, params, vocab, started, article_ids=article_ids, facet_index=facet_index
    )


def _bm25_search(
    conn: sqlite3.Connection,
    params: SearchParams,
    vocab: dict[str, dict],
    started: float,
    *,
    article_ids: tuple[int, ...] | None = None,
    facet_index: Any = None,
) -> dict:
    if not params.q:
        return _browse(
            conn, params, started, article_ids=article_ids, facet_index=facet_index
        )
    if params.partial:
        match, expansions, resolved = partial_expression(conn, params.q, vocab)
    elif vocab:
        match, expansions = expand_query(params.q, vocab)
        resolved = []
    else:
        match, expansions = fts_query(params.q), []
        resolved = []
    rewritten = match

    where, values = _filters(params, article_ids)
    where_sql = f" AND {where}" if where else ""
    # The side-view for the entity facet: every filter *except* the entity
    # selection, so a reader who has chosen Apple still sees how many articles
    # each other company would give them. Naive intersection reads zero
    # everywhere the moment one chip is clicked, which is the standard
    # faceted-search defect (design note §4.4).
    side_where, side_values = _filters(params)

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

    facets = _facets(
        conn, match, where, values, sideways=_sideways(params, article_ids)
    )
    _add_entity_facets(
        facets, conn, match, side_where, side_values, facet_index=facet_index
    )
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


#: How much of a passage a vector-only hit shows. It gets a *leading excerpt*,
#: not a snippet: FTS5's ``snippet()`` needs a MATCH context and knows where the
#: query terms were, and a passage the vector arm found *because it contains none
#: of them* has nothing to mark. Fabricating a highlight would be a lie about why
#: the result is there (§4.5d).
HYBRID_EXCERPT_CHARS = 240


def _vector_only_result(row: tuple, distance: float) -> dict:
    (
        chunk_id,
        message_id,
        start_char,
        end_char,
        sent_at,
        subject,
        kind,
        sender,
        text,
    ) = row
    excerpt = (text or "").strip().replace("\n", " ")
    return {
        "chunk_id": chunk_id,
        "message_id": message_id,
        "sent_at": sent_at,
        "subject": subject,
        "kind": kind,
        "sender": sender,
        # A fused score is a different scale from -bm25, so it never overwrites
        # ``score``: there is no BM25 value for this row, and a null says so.
        "score": None,
        "snippet": html.escape(excerpt[:HYBRID_EXCERPT_CHARS]),
        "start_char": start_char,
        "end_char": end_char,
        "article_href": f"/article/{message_id}#c{chunk_id.replace(':', '-')}",
        "matched_by": "vector",
        "fused_score": None,
        "vector_distance": round(distance, 6),
    }


def _fused_rows(
    conn: sqlite3.Connection,
    match: str,
    where_sql: str,
    values: list[Any],
    ids: list[str],
    labels: dict[str, str],
    distances: dict[str, float],
) -> dict[str, dict]:
    """Fetch the card for every fused id, from whichever arm can describe it."""
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    rows: dict[str, dict] = {}

    # Ids the FTS arm returned get the real snippet, marks and all — unchanged.
    for row in conn.execute(
        f"{CHUNK_FIELDS}{CHUNK_JOINS} WHERE chunks_fts MATCH ?{where_sql}"
        f" AND c.chunk_id IN ({placeholders}) ORDER BY rank, c.id",
        [*SNIPPET_ARGS, match, *values, *ids],
    ):
        if row[0] in labels and labels[row[0]] in {"bm25", "both"}:
            rows[row[0]] = _result(row)

    # Ids only the vector arm found have no MATCH context to excerpt from, so
    # they get the opening of the passage instead of a highlight.
    missing = [chunk_id for chunk_id in ids if chunk_id not in rows]
    if missing:
        for row in conn.execute(
            "SELECT c.chunk_id, c.message_id, c.start_char, c.end_char, a.sent_at,"
            " a.subject, a.kind, a.sender, substr(c.text, 1, ?)"
            " FROM chunks c JOIN articles a ON a.message_id = c.message_id"
            f" WHERE c.chunk_id IN ({','.join('?' * len(missing))})",
            [HYBRID_EXCERPT_CHARS, *missing],
        ):
            rows[row[0]] = _vector_only_result(row, distances.get(row[0], 0.0))

    for chunk_id, result in rows.items():
        result["matched_by"] = labels.get(chunk_id, "vector")
    return rows


def _hybrid_search(
    conn: sqlite3.Connection,
    params: SearchParams,
    vocab: dict[str, dict],
    backend: Any,
    started: float,
    *,
    article_ids: tuple[int, ...] | None = None,
    facet_index: Any = None,
) -> dict:
    """BM25 fused with a vector arm, by reciprocal rank (design §4.5)."""
    if backend is None:
        raise ApiError(
            500,
            "store_missing",
            "hybrid=1 needs a vector store; run `python -m stratechery_search.embed_corpus`",
            {},
        )
    if not params.q:
        # Browse is a filter over everything; there is no text to embed.
        return _bm25_search(
            conn,
            params,
            vocab,
            started,
            article_ids=article_ids,
            facet_index=facet_index,
        )

    span = fusion.window(params.offset, params.limit)

    # The FTS arm keeps the whole existing rewrite path: vocab expansion, and —
    # under `partial=1`, which hybrid never sees — the resolver. The asymmetry
    # this leaves (FTS searches rewritten terms, the vector arm embeds the
    # original text) is a property to observe, not a bug to smooth over (§4.2).
    if vocab:
        match, expansions = expand_query(params.q, vocab)
    else:
        match, expansions = fts_query(params.q), []
    filters = FilterSet.from_params(params, article_ids=article_ids)
    where, values = filters.sql()
    where_sql = f" AND {where}"
    side_where, side_values = _filters(params)

    fts_ids = [
        row[0]
        for row in conn.execute(
            "SELECT c.chunk_id"
            f"{CHUNK_JOINS} WHERE chunks_fts MATCH ?{where_sql}"
            " ORDER BY rank, c.id LIMIT ?",
            [match, *values, span],
        )
    ]

    vector = embed_module.embed([params.q], input_type="search_query")[0]
    hits = backend.search(vector, conn=conn, filters=filters, limit=span)
    vector_ids = [hit.chunk_id for hit in hits]
    distances = {hit.chunk_id: hit.distance for hit in hits}

    fused = fusion.fuse([fts_ids, vector_ids])
    labels = fusion.matched_by(fts_ids, vector_ids)
    rows = _fused_rows(
        conn,
        match,
        where_sql,
        values,
        [chunk_id for chunk_id, _ in fused],
        labels,
        distances,
    )

    # `total` is the number of passages that matched — the same number BM25
    # reports for the same query, because it is the same expression run against
    # the same arm. The *fused* set is what can be ranked, and the fusion window
    # caps it at 200, so it ships separately in the `hybrid` block. Without both,
    # "86" and "1,131" read as a disagreement about the corpus rather than a
    # statement about the window.
    matched_total = conn.execute(
        "SELECT count(*) FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid"
        " JOIN articles a ON a.message_id = c.message_id"
        f" WHERE chunks_fts MATCH ?{where_sql}",
        [match, *values],
    ).fetchone()[0]

    if params.group == "article":
        results, article_total = _fused_grouped(params, fused, rows)
    else:
        page = fused[params.offset : params.offset + params.limit]
        results = []
        for chunk_id, score in page:
            card = rows.get(chunk_id)
            if card is None:  # an id the store has and SQLite does not: see parity
                continue
            results.append({**card, "fused_score": round(score, 8)})

    return {
        "query": {
            "raw": params.q,
            "rewritten": match,
            "expansions": expansions,
            "resolved": [],
        },
        "total": matched_total,
        **({"article_total": article_total} if params.group == "article" else {}),
        "limit": params.limit,
        "offset": params.offset,
        "took_ms": int((time.perf_counter() - started) * 1000),
        "results": results,
        "facets": _hybrid_facets(
            conn,
            params,
            article_ids,
            match,
            where,
            values,
            side_where,
            side_values,
            facet_index,
        ),
        # `total` now means the same thing on both paths — passages matched — so
        # `fused` is what makes the difference legible: it is how many of them
        # this answer actually ranked, capped by the fusion window.
        "hybrid": {
            "enabled": True,
            "degraded": False,
            "window": span,
            "fused": len(fused),
            "fts_hits": len(fts_ids),
            "vector_hits": len(vector_ids),
            "both": sum(1 for label in labels.values() if label == "both"),
            # §4.2: the number the similarity floor will be set from in week one,
            # logged rather than guessed.
            "top1_distance": round(min(distances.values()), 6) if distances else None,
        },
    }


def _fused_grouped(
    params: SearchParams, fused: list[tuple[str, float]], rows: dict[str, dict]
) -> tuple[list[dict], int]:
    """Fold the fused passage list into articles, then page by article.

    Fusion happens at passage level and the fold happens after it (§2): the
    article takes the rank of its best *fused* passage, and ``limit``/``offset``
    count articles, so page two does not repeat page one's articles.
    """
    order: list[Any] = []
    by_article: dict[Any, list[dict]] = {}
    for chunk_id, score in fused:
        card = rows.get(chunk_id)
        if card is None:
            continue
        message_id = card["message_id"]
        if message_id not in by_article:
            by_article[message_id] = []
            order.append(message_id)
        by_article[message_id].append({**card, "fused_score": round(score, 8)})

    results = []
    for message_id in order[params.offset : params.offset + params.limit]:
        lead, *rest = by_article[message_id]
        results.append(
            {
                **lead,
                "matched_passages": len(by_article[message_id]),
                "more_total": len(rest),
                "more_passages": rest[:MAX_MORE_PASSAGES],
            }
        )
    return results, len(order)


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
        "SELECT c.message_id AS message_id,"
        f" bm25(chunks_fts, {BM25_TEXT_WEIGHT}, {BM25_SUBJECT_WEIGHT}) AS rank"
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


def _matched_articles(
    conn: sqlite3.Connection, match: str | None, where: str, values: list[Any]
) -> set[int]:
    """The distinct articles the current query matches, under ``where``.

    Article grain on purpose: the entity facet counts articles, so the set it
    counts over is the article set, not the passage set. Doing this with
    ``count(DISTINCT ...)`` per entity would be 47 statements; the set is at most
    1,647 ids, so it is fetched once and counted in memory.
    """
    where_sql = f" AND {where}" if where else ""
    if match is None:
        return {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT c.message_id"
                " FROM chunks c JOIN articles a ON a.message_id = c.message_id"
                f" WHERE {where or '1 = 1'}",
                list(values),
            )
        }
    return {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT c.message_id"
            " FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid"
            " JOIN articles a ON a.message_id = c.message_id"
            f" WHERE chunks_fts MATCH ?{where_sql}",
            [match, *values],
        )
    }


def _add_entity_facets(
    payload: dict,
    conn: sqlite3.Connection,
    match: str | None,
    side_where: str,
    side_values: list[Any],
    *,
    facet_index: Any,
) -> None:
    """Attach ``facets.by_entity`` computed *sideways*.

    ``side_where`` is every filter except the entity selection, so the counts
    answer "how many articles would each company give me" rather than "how many
    of these Apple articles are also Apple". With no facet index the key is
    absent rather than empty: a service with no gazetteer has no entity
    dimension, and `[]` would claim it had one and found nothing.
    """
    if facet_index is None:
        return
    matched = _matched_articles(conn, match, side_where, side_values)
    payload["by_entity"] = facet_index.counts(matched)


def _hybrid_facets(
    conn: sqlite3.Connection,
    params: SearchParams,
    article_ids: tuple[int, ...] | None,
    match: str,
    where: str,
    values: list[Any],
    side_where: str,
    side_values: list[Any],
    facet_index: Any,
) -> dict:
    payload = _facets(
        conn, match, where, values, sideways=_sideways(params, article_ids)
    )
    _add_entity_facets(
        payload, conn, match, side_where, side_values, facet_index=facet_index
    )
    return payload


def _facets(
    conn: sqlite3.Connection,
    match: str | None,
    where: str,
    values: list[Any],
    sideways: dict[str, tuple[str, list[Any]]] | None = None,
) -> dict:
    """Facet counts, each dimension counted with its own selection removed.

    Shipping facets inside ``/search`` is deliberate (§3 NEW): a second
    ``/facets`` call renders counts that lag the result set by one round trip,
    which is the classic faceted-search bug. The other half of that bug is the
    one this function had until 2026-11-08: counting a dimension *under* its own
    filter hides every value the reader did not pick, so a chip bar becomes a
    one-way door — pick "daily update" and "weekly" vanishes.

    ``sideways`` maps a dimension to the predicate that applies every filter
    *except* that dimension's own. A dimension with no entry falls back to the
    full predicate, which is right for one with no selection to remove.
    """
    sideways = sideways or {}

    def base(clause: tuple[str, list[Any]]) -> tuple[str, list[Any]]:
        clause_where, clause_values = clause
        if match is None:
            # Browse mode: no MATCH, so the same counts run over the filtered
            # corpus.
            return (
                " FROM chunks c JOIN articles a ON a.message_id = c.message_id"
                f" WHERE {clause_where or '1 = 1'}",
                list(clause_values),
            )
        where_sql = f" AND {clause_where}" if clause_where else ""
        return (
            " FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid"
            " JOIN articles a ON a.message_id = c.message_id"
            f" WHERE chunks_fts MATCH ?{where_sql}",
            [match, *clause_values],
        )

    def grouped(
        dimension: str | None, expression: str, order: str = "bucket"
    ) -> list[tuple]:
        clause = sideways.get(dimension, (where, values)) if dimension else (where, values)
        sql, args = base(clause)
        return conn.execute(
            f"SELECT {expression} AS bucket, count(DISTINCT c.message_id), count(*)"
            f"{sql} GROUP BY bucket ORDER BY {order}",
            args,
        ).fetchall()

    by_year = [
        {"year": int(year), "articles": articles, "count": count}
        for year, articles, count in grouped("year", "substr(a.sent_at, 1, 4)")
        if year is not None
    ]
    by_kind = [
        {"kind": kind, "articles": articles, "count": count}
        for kind, articles, count in grouped("kind", "a.kind")
    ]
    # No Sender facet in the UI (three addresses, all @stratechery.com), so there
    # is no selection to remove: the full predicate is the honest answer.
    by_sender = [
        {"sender": sender or "", "articles": articles, "count": count}
        for sender, articles, count in grouped(None, "a.sender")
    ]
    images = {"true": 0, "false": 0}
    for flag, articles, _count in grouped(
        "images", "CASE WHEN a.n_images > 0 THEN 'true' ELSE 'false' END"
    ):
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


def parse_similar_params(query: dict[str, list[str]]) -> tuple[int, str]:
    """``limit`` and ``grain`` for ``/article/{id}/similar`` (phase 4 §6).

    ``min_distance`` is not accepted as a parameter at all: the similarity floor
    was measured and refuted over 129 queries ([[similarity-floor-test]] — on
    ``unbundling`` the right article sits at 0.6384 while two wrong ones sit at
    0.5750), so an accepted-but-ignored knob would be the design note's own
    "a floor set before seeing any data is a guess" in API form.
    """
    raw_limit = (query.get("limit") or [""])[0]
    if raw_limit == "":
        limit = similar_module.DEFAULT_LIMIT
    else:
        try:
            limit = int(raw_limit)
        except ValueError as error:
            raise ApiError(
                400, "bad_request", "limit must be an integer", {"limit": raw_limit}
            ) from error
        if not 1 <= limit <= similar_module.MAX_LIMIT:
            raise ApiError(
                400,
                "bad_request",
                f"limit must be between 1 and {similar_module.MAX_LIMIT}",
                {"limit": limit},
            )
    grain = (query.get("grain") or ["article"])[0] or "article"
    if grain not in {"article", "passage"}:
        raise ApiError(
            400, "bad_request", "grain must be article or passage", {"grain": grain}
        )
    return limit, grain


#: How much of a neighbouring passage the similar list shows. Reuses the
#: vector-only-hit rule: a leading excerpt, escaped, and **no marks** — nothing
#: matched a query here, so there is nothing to highlight (§4.5d).
SIMILAR_EXCERPT_CHARS = HYBRID_EXCERPT_CHARS


def _year_of(sent_at: str | None) -> int | None:
    try:
        return int(str(sent_at)[:4])
    except (TypeError, ValueError):
        return None


def similar(
    conn: sqlite3.Connection,
    message_id: int,
    space: Any,
    *,
    limit: int = similar_module.DEFAULT_LIMIT,
    grain: str = "article",
) -> dict:
    """``GET /article/{message_id}/similar`` — see the phase 4 note §6.

    Two properties are load-bearing and neither is an optimisation:

    * **Self-exclusion is inside :mod:`.similar`**, before ranking, by article.
      A passage's nearest neighbours are its own adjacent passages, so an
      unmasked version ships looking like a mirror (§3.1).
    * **A neighbour that will not resolve is a 503, never a dropped row or a
      500.** The Lance store and ``search.sqlite`` must agree; a list built from
      chunk ids that no longer resolve is a lie about the archive (§3.5).
    """
    started = time.perf_counter()
    if space is None:
        raise ApiError(
            503,
            similar_module.SimilarError.code,
            "this process has no vector store loaded",
        )

    row = conn.execute(
        "SELECT sent_at, subject FROM articles WHERE message_id = ?", (message_id,)
    ).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "no such article", {"message_id": message_id})
    query_sent_at, query_subject = row
    query_year = _year_of(query_sent_at)

    chunk_ids = [
        chunk_id
        for (chunk_id,) in conn.execute(
            "SELECT chunk_id FROM chunks WHERE message_id = ? ORDER BY ordinal",
            (message_id,),
        )
    ]
    if not chunk_ids:
        # An article with no passages cannot be asked about. That is the index
        # disagreeing with itself, not a reader error.
        raise ApiError(
            503,
            similar_module.SimilarError.code,
            "this article has no passages in the index",
            {"message_id": message_id},
        )

    try:
        if grain == "passage":
            answered = space.passage_neighbours(
                chunk_ids, exclude_article=message_id, limit=limit, conn=conn
            )
            ordered = []
            from_query = {}
            for query_chunk, neighbour in answered:
                # Two of my passages can choose the same target passage. At this
                # grain that is one row, keyed to the passage that matched it
                # best — the list is already sorted by distance.
                if neighbour.chunk_id in from_query:
                    continue
                from_query[neighbour.chunk_id] = query_chunk
                ordered.append(neighbour)
        else:
            ordered = space.neighbours(
                chunk_ids, exclude_article=message_id, limit=limit, conn=conn
            )
            from_query = {}
        basis = space.basis(len(chunk_ids))
    except similar_module.SimilarError as error:
        raise ApiError(503, error.code, str(error)) from error

    if not ordered:
        neighbours: list[dict] = []
    else:
        placeholders = ",".join("?" * len(ordered))
        found = {
            row[0]: row[1:]
            for row in conn.execute(
                "SELECT c.chunk_id, c.message_id, c.start_char, c.end_char, a.sent_at,"
                " a.subject, a.kind, a.sender, c.text, c.text_sha256"
                " FROM chunks c JOIN articles a ON a.message_id = c.message_id"
                f" WHERE c.chunk_id IN ({placeholders})",
                [neighbour.chunk_id for neighbour in ordered],
            )
        }
        missing = [
            neighbour.chunk_id for neighbour in ordered if neighbour.chunk_id not in found
        ]
        if missing:
            raise ApiError(
                503,
                similar_module.SimilarError.code,
                "the store and search.sqlite disagree about which passages exist",
                {"missing": missing[:5], "missing_count": len(missing)},
            )
        neighbours = []
        for neighbour in ordered:
            (
                found_message,
                start_char,
                end_char,
                sent_at,
                subject,
                kind,
                sender,
                text,
                _text_sha256,
            ) = found[neighbour.chunk_id]
            neighbour_year = _year_of(sent_at)
            card = {
                "message_id": found_message,
                "sent_at": sent_at,
                "subject": subject,
                "kind": kind,
                "sender": sender,
                # Cosine distance, lower is closer — the same convention and the
                # same direction as `hybrid.top1_distance`. There is deliberately
                # no `similarity` field: two vocabularies for one number is how a
                # UI ends up inverting a sort.
                "distance": neighbour.distance,
                "matched_passages": neighbour.matched_passages,
                "chunk_id": neighbour.chunk_id,
                "start_char": start_char,
                "end_char": end_char,
                "excerpt": html.escape(
                    (text or "").strip().replace("\n", " ")[:SIMILAR_EXCERPT_CHARS]
                ),
                "article_href": f"/article/{found_message}#c{neighbour.chunk_id.replace(':', '-')}",
                # `group=article`'s own meaning one level down: the other
                # passages of this article that also chose it, not a score.
                "more_passages": max(0, neighbour.matched_passages - 1),
                # §3.4: neighbouring articles drift forward, and the honest way to
                # publish that is to show it rather than to name it a theme.
                "year_delta": (
                    neighbour_year - query_year
                    if neighbour_year is not None and query_year is not None
                    else None
                ),
            }
            if grain == "passage":
                card["query_chunk_id"] = from_query.get(neighbour.chunk_id)
            neighbours.append(card)

    return {
        "message_id": message_id,
        "subject": query_subject,
        "grain": grain,
        "basis": basis,
        "results": neighbours,
        "limit": limit,
        "took_ms": int((time.perf_counter() - started) * 1000),
    }


#: How many of the newest articles the landing screen offers as a way in.
#: Five: enough to be worth scanning (a single headline is a teaser that reads
#: as stale the moment the archive moves on) and few enough that the list stays a
#: row of links rather than a third of the screen — the complaint that produced
#: it was that the landing screen was hard to navigate, so it must not become
#: another block to scroll past.
LANDING_RECENT = 5


def recent_articles(conn: sqlite3.Connection, limit: int = LANDING_RECENT) -> list:
    """The newest searchable articles, newest first.

    Ordered the way ``_browse`` orders: ``sent_at`` descending, then
    ``message_id``, because two updates can share a date and the id makes the
    tie-break deterministic instead of whatever SQLite happened to return.

    ``is_transactional = 0`` for the same reason search and browse exclude those
    rows: an access code is mail, not something to offer as reading.
    """
    return [
        {
            "message_id": message_id,
            "subject": subject,
            "sent_at": sent_at,
            "kind": kind,
            "article_href": f"/article/{message_id}",
        }
        for message_id, subject, sent_at, kind in conn.execute(
            "SELECT message_id, subject, sent_at, kind FROM articles"
            " WHERE is_transactional = 0"
            " ORDER BY sent_at DESC, message_id DESC"
            " LIMIT ?",
            (limit,),
        )
    ]


def stats(conn: sqlite3.Connection, facet_index: Any = None) -> dict:
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
        # The way in that was missing: "what is new" is a question a reader asks
        # before they know what to search for. It rides on /stats rather than
        # owning an endpoint because the landing screen already fetches this
        # document — a second request would be a second round trip on a phone for
        # five rows of text the same connection can read for free.
        "recent": recent_articles(conn),
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
        # The landing screen's way in to the company facet. Absent when there is
        # no gazetteer, for the same reason `facets.by_entity` is: an empty list
        # would claim a dimension and report that nobody is in it.
        **(
            {
                "companies": _landing_entities(facet_index, "company"),
                "people": _landing_entities(facet_index, "person"),
            }
            if facet_index
            else {}
        ),
    }


def _landing_entities(facet_index: Any, kind: str) -> list[dict]:
    """Every entity of one kind in the gazetteer, largest first.

    All of them, not a top-N: the first version cut at 12 by article count, which
    silently hid Anthropic (190 articles, rank 13) behind incumbents that simply
    have more archive. A cut that hides a company a reader is looking for is a
    worse bug than a long chip row, and the order the UI shows them in is a
    rendering decision, not the API's.

    Companies and people are separate keys rather than one list with a `kind`
    field, because they are separate rows on the landing screen — the same split
    the facet panel needed after it listed Elon Musk under "Company".
    """
    counts = [
        {
            "entity_id": entity_id,
            "name": entity.name,
            "articles": len(facet_index.articles_by_entity.get(entity_id, ())),
        }
        for entity_id, entity in facet_index.entities.items()
        if entity.kind == kind
    ]
    counts.sort(key=lambda row: (-row["articles"], row["entity_id"]))
    return counts


def health(
    conn: sqlite3.Connection,
    vocab_path: Path | None = None,
    vocab: dict[str, dict] | None = None,
    facets_path: Path | None = None,
    facet_index: Any = None,
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
        # The same "is the file firing?" question as `vocab`. `entities: 0` with a
        # path is the honest answer for a service whose facet index was never
        # built, and it is the state the UI must tolerate.
        "facets": {
            "path": None if facets_path is None else str(facets_path),
            "entities": 0 if facet_index is None else len(facet_index.entities),
            "version": 0 if facet_index is None else facet_index.version,
            "articles": 0 if facet_index is None else len(facet_index.by_article),
        },
    }
