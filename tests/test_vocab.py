"""The committed synonym table: what it rewrites, and the property it rests on.

These tests are deliberately independent of the live index. The fixture below is
a hand-built database whose rows are chosen so that "the rewritten query returns
at least what the original did" is a statement about the *rewrite*, not about
which articles happen to exist. The four entries themselves are measured against
the real index elsewhere; here they are only asserted to be the four that were
decided, and to do what the measurement assumed.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from stratechery_search import api
from stratechery_search.build_index import SCHEMA
from stratechery_search.server import Config, Handler, Server

REPO_ROOT = Path(__file__).resolve().parents[1]
VOCAB_PATH = REPO_ROOT / "vocab.yaml"

#: ``canonical`` -> ``alias``, exactly the four shipped entries.
ENTRIES = {
    "Taiwan Semiconductor Manufacturing": "TSMC",
    "Semiconductor Manufacturing International": "SMIC",
    "Digital Markets Act": "DMA",
    "Child Sexual Abuse Material": "CSAM",
}

#: ``expand_query`` sorts the phrases in a group, so these are alphabetical, not
#: canonical-first. The alias is the query a reader actually types.
EXPECTED_EXPRESSIONS = {
    "TSMC": '("TSMC" OR "Taiwan Semiconductor Manufacturing")',
    "SMIC": '("SMIC" OR "Semiconductor Manufacturing International")',
    "DMA": '("DMA" OR "Digital Markets Act")',
    "CSAM": '("CSAM" OR "Child Sexual Abuse Material")',
}

#: One row per surface form, so each entry has something only the other side of
#: the rewrite can reach. ``message_id`` 1..8, one chunk each.
ROWS = [
    "TSMC is the largest foundry in the world.",
    "Taiwan Semiconductor Manufacturing Company reported quarterly results.",
    "SMIC is China's largest foundry.",
    "Semiconductor Manufacturing International is listed in Shanghai.",
    "The DMA came into force in March 2024.",
    "The Digital Markets Act names the gatekeepers.",
    "CSAM must be reported to the authorities.",
    "Child Sexual Abuse Material is a criminal offence.",
]


@pytest.fixture(scope="module")
def vocab() -> dict[str, dict]:
    return api.load_vocab(VOCAB_PATH)


def _index(texts: list[str], tmp_path: Path) -> sqlite3.Connection:
    """A real ``search.sqlite`` schema over the rows we choose."""
    path = tmp_path / "synthetic.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    for message_id, text in enumerate(texts, 1):
        conn.execute(
            "INSERT INTO articles (message_id, sent_at, subject, from_header, kind,"
            " sender, chars, words, est_tokens, n_images, has_tables,"
            " is_transactional, archive_sha256, archive_bytes, text_sha256,"
            " normalizer_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                message_id,
                f"2024-0{message_id}-01T00:00:00Z",
                f"Synthetic {message_id}",
                "email@stratechery.com",
                "update",
                "email@stratechery.com",
                len(text),
                len(text.split()),
                len(text) // 4,
                0,
                0,
                0,
                None,
                None,
                None,
                None,
            ),
        )
        conn.execute(
            "INSERT INTO chunks (id, chunk_id, message_id, ordinal, start_char,"
            " end_char, text, subject, embed_model, chunker_version, text_sha256)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                message_id,
                f"{message_id}:0",
                message_id,
                0,
                0,
                len(text),
                text,
                f"Synthetic {message_id}",
                None,
                "test",
                "0" * 64,
            ),
        )
    # ``chunks_fts`` is external-content: it does not index itself.
    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    conn.commit()
    return conn


def _chunk_ids(conn: sqlite3.Connection, raw: str, vocab: dict[str, dict]) -> set[str]:
    body = api.search(conn, api.SearchParams(q=raw, limit=100), vocab)
    return {result["chunk_id"] for result in body["results"]}


def test_the_shipped_file_has_exactly_the_four_decided_terms(vocab):
    # Four, not "at least four": each one has a measured `buys` figure behind it,
    # and an entry added without one is what the measurement existed to prevent.
    assert len(vocab) == 4
    assert set(vocab) == set(ENTRIES)
    for canonical, alias in ENTRIES.items():
        # The parser appends the canonical to its own aliases; the file does not
        # repeat it.
        assert vocab[canonical]["aliases"] == [alias, canonical]


@pytest.mark.parametrize("query,expected", sorted(EXPECTED_EXPRESSIONS.items()))
def test_each_entry_produces_its_fts_expression(vocab, query, expected):
    rewritten, expansions = api.expand_query(query, vocab)
    assert rewritten == expected
    assert len(expansions) == 1
    assert expansions[0]["matched"] == query
    assert expansions[0]["source"] == "vocab.yaml"


def test_a_query_matching_no_entry_is_byte_identical_to_before(vocab, tmp_path):
    # The rewrite is a recall move, never a rewrite of a question it does not
    # recognise: no file, an empty file, and a file whose entries simply do not
    # fire must all produce today's expression, byte for byte.
    absent = tmp_path / "absent.yaml"
    empty = tmp_path / "empty.yaml"
    empty.write_text("terms:\n", encoding="utf-8")

    assert api.load_vocab(absent) == {}
    assert api.load_vocab(empty) == {}

    for raw in ("aggregation theory", "the commoditization of choice", "TSM"):
        baseline = api.fts_query(raw)
        assert api.expand_query(raw, {})[0] == baseline
        assert api.expand_query(raw, api.load_vocab(empty))[0] == baseline
        assert api.expand_query(raw, vocab) == (baseline, [])


@pytest.mark.parametrize("alias", sorted(EXPECTED_EXPRESSIONS))
def test_the_rewrite_is_recall_only(vocab, tmp_path, alias):
    """The property the whole change rests on: a superset, never a different set.

    Measured over a corpus where the alias and the canonical each occur alone, so
    neither side of the rewrite can reach both rows without the other.
    """
    conn = _index(ROWS, tmp_path)
    try:
        unrewritten = _chunk_ids(conn, alias, {})
        rewritten = _chunk_ids(conn, alias, vocab)
        assert unrewritten, "the fixture must match the unrewritten query"
        assert unrewritten <= rewritten, (
            f"{alias}: the rewrite lost {sorted(unrewritten - rewritten)}"
        )
        # And it actually buys something, or the entry is not earning its place.
        assert rewritten - unrewritten

        canonical = next(c for c, a in ENTRIES.items() if a == alias)
        assert _chunk_ids(conn, canonical, {}) <= _chunk_ids(conn, canonical, vocab)
    finally:
        conn.close()


def test_a_multi_word_canonical_query_narrows_to_the_phrase(vocab, tmp_path):
    """Where recall-only does *not* hold, stated rather than discovered.

    Expanding the canonical replaces word-AND (``"Digital" "Markets" "Act"``)
    with a phrase OR (``("DMA" OR "Digital Markets Act")``). A row that holds all
    three words without holding them adjacently is therefore lost. That is the
    trade the rewrite makes; this test pins the behaviour so it cannot change
    silently.
    """
    rows = [
        *ROWS,
        "Taiwan's semiconductor industry is a manufacturing powerhouse.",
    ]
    conn = _index(rows, tmp_path)
    try:
        unrewritten = _chunk_ids(conn, "Taiwan Semiconductor Manufacturing", {})
        rewritten = _chunk_ids(conn, "Taiwan Semiconductor Manufacturing", vocab)
        assert unrewritten == {"2:0", "9:0"}
        assert rewritten == {"1:0", "2:0"}
        assert not unrewritten <= rewritten
    finally:
        conn.close()


def test_a_malformed_line_does_not_reach_expand_query(tmp_path):
    # Belt and braces for Task 1 at the loader level: the loader refuses rather
    # than dropping the bad line, so a typo can never be silently "ignored" into
    # a vocabulary that is subtly not what the file says.
    path = tmp_path / "vocab.yaml"
    path.write_text(
        "terms:\n  - canonical: Digital Markets Act\n    aliases: [DMA]\n    oops\n",
        encoding="utf-8",
    )
    with pytest.raises(api.ApiError) as raised:
        api.load_vocab(path)
    assert raised.value.status == 503
    assert raised.value.code == "vocab_invalid"
    assert ":4:" in raised.value.detail["detail"]


@contextlib.contextmanager
def _running(corpus, vocab_path: Path):
    archive, _derived, index_path = corpus
    dist = archive.parent / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text("<!doctype html>", encoding="utf-8")
    config = Config(
        port=0,
        index_path=index_path,
        archive_root=archive,
        static_dir=dist,
        vocab_path=vocab_path,
    )
    server = Server(("127.0.0.1", 0), Handler)
    server.config = config
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _get(url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_healthz_says_whether_the_file_is_firing(corpus):
    # The question that sat unanswerable for two days: /healthz now reports the
    # path it loaded and how many terms it found, so an image that never copied
    # the file in says `terms: 0` instead of looking healthy.
    with _running(corpus, VOCAB_PATH) as base:
        status, body = _get(f"{base}/healthz")
    assert status == 200
    assert body["vocab"] == {"path": str(VOCAB_PATH), "terms": 4}


def test_healthz_reports_zero_terms_when_the_file_is_absent(corpus, tmp_path):
    with _running(corpus, tmp_path / "no-vocab.yaml") as base:
        status, body = _get(f"{base}/healthz")
    assert status == 200
    assert body["vocab"] == {"path": str(tmp_path / "no-vocab.yaml"), "terms": 0}
