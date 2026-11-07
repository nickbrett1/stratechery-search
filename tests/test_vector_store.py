"""The store itself — skipped unless the native wheel is installed.

``lancedb`` is a real dependency of the image (pinned, design §4.6) but this
suite also runs on hosts without it, so these tests skip rather than fail: what
they assert is the store's behaviour, not that pip finished.

The highest-value test here is :func:`test_a_second_run_embeds_nothing` — the
design note's "an intended no-op is not a no-op" (§4.1.3), which is what keeps a
future chunker change cheap instead of a recurring spend.
"""

from __future__ import annotations

import sqlite3

import pytest

pytest.importorskip("lancedb")

from stratechery_search import embed as embed_module  # noqa: E402
from stratechery_search import embed_corpus, vector_store  # noqa: E402
from stratechery_search.embed import EMBED_DIM  # noqa: E402
from stratechery_search.filters import FilterSet  # noqa: E402


class FakeVectors:
    """Deterministic pseudo-vectors per text, so ordering is reproducible."""

    def __init__(self):
        self.calls = []

    def __call__(self, texts, *, input_type, config=None):
        self.calls.append({"n": len(texts), "input_type": input_type})
        vectors = []
        for text in texts:
            seed = sum(ord(char) for char in text) or 1
            vector = [0.0] * EMBED_DIM
            vector[seed % EMBED_DIM] = 1.0
            vectors.append(vector)
        return vectors


@pytest.fixture
def store(tmp_path, corpus):
    _, _, index_path = corpus
    conn = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
    yield tmp_path / "lancedb", conn, index_path
    conn.close()


@pytest.fixture
def fake_vectors(monkeypatch):
    fake = FakeVectors()
    monkeypatch.setattr(embed_module, "embed", fake)
    return fake


def build(tmp_path, index_path, *, limit=None, batch=2):
    return embed_corpus.run(
        index_path=index_path,
        store_path=tmp_path / "lancedb",
        limit=limit,
        batch=batch,
    )


def test_the_corpus_pass_budgets_its_own_timeout(store, fake_vectors, tmp_path, monkeypatch):
    """End to end: the pass reports the budget it actually spent, not the query one.

    Set the query timeout to its floor and the pass still runs at the corpus
    budget — the failure at 800 ms was *in the real pass*, so the assertion that
    matters is the one made through ``run()``, not about the constant.
    """
    monkeypatch.setenv("EMBED_TIMEOUT_MS", "800")
    summary = build(tmp_path, store[2], limit=2)
    assert summary["timeout_ms"] == embed_module.CORPUS_TIMEOUT_MS
    assert summary["embedded"] == 2


def test_the_full_fixture_corpus_round_trips(store, fake_vectors, tmp_path):
    store_path, conn, index_path = store
    summary = build(tmp_path, index_path)
    assert summary["embedded"] == 5
    assert summary["parity"] == {"ok": True, "count": 5, "chunker_versions": [1]}

    table = vector_store.open_store(store_path)
    assert vector_store.count(table) == 5
    assert vector_store.embed_models(table) == {"cohere/embed-v5.0"}
    assert vector_store.chunker_versions(table) == {1}

    # Five rows, four reachable: the predicate keeps the access code out.
    hits = vector_store.search(table, [1.0] + [0.0] * (EMBED_DIM - 1), filters=FilterSet(), limit=5)
    assert len(hits) == 4


def test_the_transactional_passage_is_in_the_store_but_never_retrievable(store, fake_vectors, tmp_path):
    """Parity counts every chunk; the predicate is what makes mail unreachable."""
    store_path, conn, index_path = store
    build(tmp_path, index_path)
    table = vector_store.open_store(store_path)
    rows = table.search().select(["chunk_id", "is_transactional"]).limit(None).to_list()
    assert any(row["is_transactional"] for row in rows), "the access code is embedded for parity"

    hits = vector_store.search(table, [1.0] + [0.0] * (EMBED_DIM - 1), filters=FilterSet(), limit=10)
    assert "30564:0" not in {hit.chunk_id for hit in hits}


def test_a_second_run_embeds_nothing(store, fake_vectors, tmp_path):
    store_path, conn, index_path = store
    build(tmp_path, index_path)
    fake_vectors.calls.clear()

    again = build(tmp_path, index_path)
    assert again["embedded"] == 0
    assert again["pending"] == 0
    assert fake_vectors.calls == [], "a re-run must not spend a single token"


def test_a_partial_store_fails_parity_loudly(store, fake_vectors, tmp_path):
    """Same version, fewer rows: the case that silently equals BM25."""
    store_path, conn, index_path = store
    summary = build(tmp_path, index_path, limit=2)
    assert summary["parity"]["ok"] is False
    assert summary["parity"]["code"] == "store_mismatch"
    table = vector_store.open_store(store_path)
    with pytest.raises(vector_store.StoreError) as error:
        vector_store.assert_parity(table, conn)
    assert error.value.code == "store_mismatch"
    assert "2 passages" in str(error.value)


def test_the_builder_resumes_where_it_stopped(store, fake_vectors, tmp_path):
    store_path, conn, index_path = store
    build(tmp_path, index_path, limit=2)
    resumed = build(tmp_path, index_path)
    assert resumed["already_stored"] == 2
    assert resumed["embedded"] == 3
    assert resumed["parity"]["ok"] is True
    assert vector_store.assert_parity(vector_store.open_store(store_path), conn)["count"] == 5


def test_a_1024_dim_vector_cannot_be_written(store, fake_vectors, tmp_path):
    store_path, conn, index_path = store
    table = vector_store.open_store(store_path, create=True)
    row = {
        "chunk_id": "x:0",
        "vector": [0.0] * 1024,
        "embed_model": "cohere/embed-v5.0",
        "chunker_version": 1,
        "text_sha256": "sha",
        "sent_at": "2024-01-01T00:00:00Z",
        "kind": "daily",
        "sender": "ben@stratechery.com",
        "has_images": False,
        "is_transactional": False,
    }
    with pytest.raises(vector_store.StoreError):
        vector_store.add_rows(table, [row])


def test_a_missing_store_is_an_error_the_message_can_fix(store):
    store_path, _, _ = store
    with pytest.raises(vector_store.StoreError) as error:
        vector_store.open_store(store_path)
    assert error.value.code == "store_missing"
    assert "embed_corpus" in str(error.value)
