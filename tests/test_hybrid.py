"""Fusion, the shared filter predicate, and the hybrid query path.

The hybrid path is tested without LanceDB and without a gateway: the arm is a
fake that returns chunk ids, and the embedder is patched. That is deliberate —
what these tests are for is the *contract* (what the reader sees, what happens
when an arm fails), not that kNN works. The store's own tests need the native
wheel and skip without it.
"""

from __future__ import annotations

import sqlite3

import pytest

from stratechery_search import api, fusion
from stratechery_search import embed as embed_module
from stratechery_search.filters import FilterSet
from stratechery_search.vector_store import StoredVector, StoreError

# -- fusion -----------------------------------------------------------------


def test_consensus_beats_a_single_arm_top_rank():
    """The worked example from design §4.5: both arms beat one arm's rank 1."""
    fused = dict(fusion.fuse([["A", "B"], ["B", "A"]]))
    # A: fts 1 + vec 2 = 1/61 + 1/62; B: fts 2 + vec 1 = the same. A tie, broken
    # by chunk_id — which is the point of the tie-break.
    assert fused["A"] == pytest.approx(fused["B"])
    assert [cid for cid, _ in fusion.fuse([["A", "B"], ["B", "A"]])] == ["A", "B"]

    ranked = fusion.fuse([["B", "A", "C"], ["A"]])
    assert ranked[0][0] == "A", "a passage both arms found outranks one arm's first"


def test_a_single_arm_hit_is_not_dropped():
    assert dict(fusion.fuse([["A"], ["B"]]))["B"] > 0


def test_the_tie_break_is_chunk_id_so_the_same_query_gives_the_same_order():
    arms = [["b:0", "a:0"], ["a:0", "b:0"]]
    assert [cid for cid, _ in fusion.fuse(arms)] == [cid for cid, _ in fusion.fuse(arms)]
    assert [cid for cid, _ in fusion.fuse(arms)] == ["a:0", "b:0"]


def test_the_window_grows_with_offset_and_is_capped():
    assert fusion.window(0, 20) == 50
    assert fusion.window(0, 100) == 100
    assert fusion.window(180, 20) == 200
    assert fusion.window(1000, 20) == fusion.MAX_WINDOW


def test_offset_is_applied_once_after_fusion():
    fused = [cid for cid, _ in fusion.fuse([["a", "b", "c", "d"]])]
    assert fused[2:] == ["c", "d"]


def test_matched_by_labels_each_passage():
    labels = fusion.matched_by(["a", "b"], ["b", "c"])
    assert labels == {"a": "bm25", "b": "both", "c": "vector"}


# -- one predicate, two renderings ------------------------------------------


def test_the_two_renderings_say_the_same_thing():
    filters = FilterSet(
        start="2023-01-01", end="2023-12-31", kinds=("interview", "update"),
        senders=("ben@stratechery.com",), has_images=False,
    )
    sql, values = filters.sql()
    lance = filters.lance()
    assert "a.sent_at >= ?" in sql and "a.sent_at <= ?" in sql
    assert "a.kind IN (?,?)" in sql and "a.n_images = 0" in sql
    assert "a.is_transactional = 0" in sql

    assert "sent_at >= '2023-01-01T00:00:00Z'" in lance
    assert "sent_at <= '2023-12-31T23:59:59Z'" in lance
    assert "kind IN ('interview', 'update')" in lance
    assert "has_images = false" in lance
    assert "is_transactional = false" in lance
    assert values == [
        "2023-01-01T00:00:00Z",
        "2023-12-31T23:59:59Z",
        "interview",
        "update",
        "ben@stratechery.com",
    ]


def test_transactional_mail_is_excluded_when_nothing_else_is_filtered():
    """Not a default: there is no parameter that turns it off."""
    assert "is_transactional = 0" in FilterSet().sql()[0]
    assert "is_transactional = false" in FilterSet().lance()


def test_a_sender_with_a_quote_cannot_break_the_lance_expression():
    assert FilterSet(senders=("o'brien",)).lance().endswith("is_transactional = false")
    assert "('o''brien')" in FilterSet(senders=("o'brien",)).lance()


# -- the hybrid query path --------------------------------------------------


class FakeBackend:
    """The vector arm, reduced to what the query layer uses."""

    def __init__(self, chunk_ids, distances=None):
        self.chunk_ids = list(chunk_ids)
        self.distances = distances or {}
        self.calls = []

    def search(self, vector, *, conn=None, filters, limit):
        self.calls.append({"limit": limit, "vector": vector, "filters": filters, "conn": conn})
        return [
            StoredVector(chunk_id=cid, distance=self.distances.get(cid, 0.1))
            for cid in self.chunk_ids[:limit]
        ]


@pytest.fixture
def conn(corpus):
    _, _, index_path = corpus
    connection = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
    yield connection
    connection.close()


@pytest.fixture
def embedded(monkeypatch):
    """A query embedding that costs nothing, and records that it was asked for."""
    calls = []

    def fake_embed(texts, *, input_type, config=None):
        calls.append({"texts": texts, "input_type": input_type})
        return [[0.0] * embed_module.EMBED_DIM for _ in texts]

    monkeypatch.setattr(embed_module, "embed", fake_embed)
    return calls


def search(conn, **kwargs):
    params = api.parse_search_params({key: [str(value)] for key, value in kwargs.items()})
    return api.search(conn, params, {}, hybrid=kwargs.pop("_backend", None))


def test_hybrid_is_the_default_once_a_store_is_wired(conn, embedded):
    """2026-11-07: the flag stopped being a flag because the store exists."""
    backend = FakeBackend(["152:0"])
    body = api.search(conn, api.parse_search_params({"q": ["aggregation"]}), {}, hybrid=backend)
    assert body["hybrid"]["enabled"] is True and body["hybrid"]["degraded"] is False
    assert [call["input_type"] for call in embedded] == ["search_query"]


def test_hybrid_can_still_be_turned_off_explicitly(conn, embedded):
    backend = FakeBackend(["152:0"])
    params = api.parse_search_params({"q": ["aggregation"], "hybrid": ["0"]})
    body = api.search(conn, params, {}, hybrid=backend)
    assert "hybrid" not in body or body["hybrid"] is None
    assert embedded == [] and backend.calls == [], "hybrid=0 must not touch the arm"
    assert body["total"] == 3


def test_a_server_with_no_store_still_answers(conn, embedded):
    """The default cannot be a 500: with no store the default search is BM25.

    An *explicit* `hybrid=1` against the same server fails loudly — see
    ``test_hybrid_without_a_store_is_a_500``; the difference is whether the
    reader asked for something that cannot be honoured.
    """
    body = api.search(conn, api.parse_search_params({"q": ["aggregation"]}), {})
    assert "hybrid" not in body or body["hybrid"] is None
    assert body["results"] and embedded == []


def test_total_is_the_match_count_not_the_window(conn, embedded):
    """The tally has to mean the same thing on both paths, or the UI lies.

    TSMC reports 1,131 passages on BM25 and reported 86 with hybrid's window
    count; a reader cannot tell a corpus of 1,131 from a window of 50+50-14.
    """
    backend = FakeBackend(["152:0"])
    params = api.parse_search_params({"q": ["aggregation"]})
    bm25 = api.search(conn, api.parse_search_params({"q": ["aggregation"], "hybrid": ["0"]}), {})
    hybrid = api.search(conn, params, {}, hybrid=backend)
    assert hybrid["total"] == bm25["total"]
    assert hybrid["hybrid"]["fused"] <= hybrid["hybrid"]["window"]


def test_a_process_with_no_store_never_embeds(conn, embedded):
    body = api.search(conn, api.parse_search_params({"q": ["aggregation"]}), {})
    assert "hybrid" not in body or body.get("hybrid") is None
    assert embedded == [], "no store means no gateway call, default on or not"


def test_a_vector_only_hit_gets_an_excerpt_and_no_invented_highlight(conn, embedded):
    backend = FakeBackend(["85344:0", "152:0"])
    params = api.parse_search_params({"q": ["aggregation"], "hybrid": ["1"]})
    body = api.search(conn, params, {}, hybrid=backend)

    vector_only = [row for row in body["results"] if row["matched_by"] == "vector"]
    assert vector_only, "the vector arm's unique hit should be in the results"
    card = vector_only[0]
    assert card["chunk_id"] == "85344:0"
    assert "<mark>" not in card["snippet"], "a passage with no matching term has nothing to mark"
    assert card["score"] is None, "there is no BM25 score for a vector-only row"
    assert card["fused_score"] is not None
    assert body["hybrid"]["vector_hits"] == 1 + len(
        [row for row in body["results"] if row["matched_by"] == "both"]
    )
    assert body["hybrid"]["top1_distance"] == pytest.approx(0.1)


def test_both_arms_agreeing_is_labelled_and_keeps_the_real_snippet(conn, embedded):
    backend = FakeBackend(["152:0"])
    params = api.parse_search_params({"q": ["aggregation"], "hybrid": ["1"]})
    body = api.search(conn, params, {}, hybrid=backend)
    both = [row for row in body["results"] if row["chunk_id"] == "152:0"]
    assert both and both[0]["matched_by"] == "both"
    assert "<mark>" in both[0]["snippet"]
    assert both[0]["score"] is not None


def test_the_query_connection_reaches_the_vector_arm(conn, embedded):
    """The first hybrid request after a process start used to 500 here.

    ``assert_parity`` compares the store against ``search.sqlite``, so it needs
    the connection; ``Backend`` was built with ``conn=None`` and nothing passed
    one through, so a cold process raised AttributeError on the very first
    hybrid request. Warm processes hid it.
    """
    backend = FakeBackend(["152:0"])
    params = api.parse_search_params({"q": ["aggregation"], "hybrid": ["1"]})
    api.search(conn, params, {}, hybrid=backend)
    assert backend.calls, "the arm should have been asked"
    assert all(call["conn"] is conn for call in backend.calls)


def test_the_embed_uses_search_query_never_search_document(conn, embedded):
    backend = FakeBackend([])
    params = api.parse_search_params({"q": ["aggregation"], "hybrid": ["1"]})
    api.search(conn, params, {}, hybrid=backend)
    assert [call["input_type"] for call in embedded] == ["search_query"]


def test_type_ahead_never_pays_for_an_embed(conn, embedded):
    backend = FakeBackend(["152:0"])
    params = api.parse_search_params({"q": ["aggreg"], "partial": ["1"], "hybrid": ["1"]})
    api.search(conn, params, {}, hybrid=backend)
    assert embedded == []
    assert backend.calls == []


def test_a_gateway_failure_degrades_to_bm25_and_says_so(conn, monkeypatch):
    def down(texts, *, input_type, config=None):
        raise embed_module.EmbedError("embed_unreachable", "gateway unreachable")

    monkeypatch.setattr(embed_module, "embed", down)
    plain = api.search(conn, api.parse_search_params({"q": ["aggregation"]}), {})
    params = api.parse_search_params({"q": ["aggregation"], "hybrid": ["1"]})
    body = api.search(conn, params, {}, hybrid=FakeBackend(["85344:0"]))

    assert body["hybrid"] == {
        "enabled": False,
        "degraded": True,
        "reason": "embed_unreachable",
    }
    assert [row["chunk_id"] for row in body["results"]] == [
        row["chunk_id"] for row in plain["results"]
    ]


def test_a_store_that_disagrees_with_the_index_is_a_500_not_a_degradation(conn, embedded):
    class Mismatched(FakeBackend):
        def search(self, vector, *, conn=None, filters, limit):
            raise StoreError("store_mismatch", "store has 4 passages, search.sqlite has 5")

    params = api.parse_search_params({"q": ["aggregation"], "hybrid": ["1"]})
    with pytest.raises(api.ApiError) as error:
        api.search(conn, params, {}, hybrid=Mismatched([]))
    assert error.value.status == 500
    assert error.value.code == "store_mismatch"


def test_hybrid_without_a_store_is_a_deployment_error(conn, embedded):
    params = api.parse_search_params({"q": ["aggregation"], "hybrid": ["1"]})
    with pytest.raises(api.ApiError) as error:
        api.search(conn, params, {}, hybrid=None)
    assert error.value.status == 500
    assert error.value.code == "store_missing"


def test_hybrid_pages_by_article_after_fusion(conn, embedded):
    backend = FakeBackend(["85344:0", "152:2", "152:1"])
    params = api.parse_search_params(
        {"q": ["aggregation"], "hybrid": ["1"], "group": ["article"]}
    )
    body = api.search(conn, params, {}, hybrid=backend)
    message_ids = [row["message_id"] for row in body["results"]]
    assert len(message_ids) == len(set(message_ids)), "one card per article"
    # `total` is passages matched by the text arm, `article_total` is articles
    # ranked after fusion — different questions, and the UI needs both. (The
    # per-card `matched_passages` counts fused passages, vector-only ones
    # included, so it is not the same number.)
    assert body["total"] == 3
    assert body["article_total"] == len(message_ids)
    assert body["hybrid"]["fused"] == 4  # 3 FTS ids + 1 vector-only, all fused
    for row in body["results"]:
        assert row["matched_passages"] >= 1
        assert row["more_total"] == len(row["more_passages"])


def test_the_filter_reaches_the_vector_arm_unchanged(conn, embedded):
    backend = FakeBackend(["152:0"])
    params = api.parse_search_params(
        {"q": ["aggregation"], "hybrid": ["1"], "kind": ["interview"], "from": ["2016-01-01"]}
    )
    api.search(conn, params, {}, hybrid=backend)
    filters = backend.calls[0]["filters"]
    assert filters.kinds == ("interview",)
    assert filters.start == "2016-01-01"
    assert "is_transactional = false" in filters.lance()


def test_hybrid_response_is_additive_and_keeps_the_phase1_shape(conn, embedded):
    params = api.parse_search_params({"q": ["aggregation"], "hybrid": ["1"]})
    body = api.search(conn, params, {}, hybrid=FakeBackend(["152:0"]))
    for field in (
        "query", "total", "limit", "offset", "took_ms", "results", "facets",
    ):
        assert field in body
    assert set(body["query"]) == {"raw", "rewritten", "expansions", "resolved"}
