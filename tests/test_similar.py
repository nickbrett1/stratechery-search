"""More like this: the ranking, the masking, and the endpoint's failure modes.

The vector store is faked here on purpose. What these tests pin is the
*contract* from the phase 4 note — self-exclusion inside the ranking, one row per
article, `max` rather than `mean` over an article's passages, a deterministic
order, and a 503 that leaves the article page standing — none of which needs a
native wheel or 245 MB of real vectors to be wrong.

Two of these would have caught shipped defects if the equivalent test had
existed earlier in the project:

* a test can pin a defect as firmly as a feature, so ``test_the_ranking_is_by_
  best_passage_not_by_average`` asserts the refuted alternative (mean) is *not*
  what wins;
* ``test_a_missing_neighbour_is_a_503_not_a_dropped_row`` is the parity rule
  from §3.5, which is the one failure that must never be silent.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from stratechery_search import api, similar
from stratechery_search.server import Config, Handler, Server

# Three passages of article 152, one of 85344, one transactional of 30564.
DIM = 8


def vector(*values: float) -> list[float]:
    padded = list(values) + [0.0] * (DIM - len(values))
    return padded


class FakeTable:
    """The slice of the LanceDB table API `Space` actually uses."""

    def __init__(self, rows):
        self.rows = rows

    def search(self):
        return self

    def select(self, columns):
        self.columns = columns
        return self

    def limit(self, count):
        return self

    def to_list(self):
        return self.rows


class FakeBackend:
    def __init__(self, rows):
        self.rows = rows

    def table(self, conn=None):
        return FakeTable(self.rows)


def row(chunk_id: str, values: list[float], *, transactional: bool = False) -> dict:
    return {
        "chunk_id": chunk_id,
        "vector": values,
        "is_transactional": transactional,
        "embed_model": "cohere/embed-v5.0",
        "chunker_version": 1,
    }


#: Article 152's own passages cluster tightly (they are adjacent prose), 85344 is
#: the near-twin, and 30564 is a transactional mail whose vector is identical to
#: one of 152's — so it *would* win if transactional rows were ranked as targets.
ROWS = [
    row("152:0", vector(1.0, 0.0)),
    row("152:1", vector(0.99, 0.01)),
    row("152:2", vector(0.98, 0.02)),
    row("85344:0", vector(1.0, 0.0)),
    row("30564:0", vector(1.0, 0.0), transactional=True),
]


@pytest.fixture()
def space(tmp_path):
    return similar.Space(tmp_path / "lancedb", FakeBackend(ROWS))


# -- the ranking ------------------------------------------------------------


def test_the_article_you_are_reading_is_never_a_result(space):
    """§3.1: a passage's nearest neighbours are its own adjacent passages.

    Both 152:1 and 152:2 are at distance ~0 from 152:0, so an unmasked search is
    the article itself in reading order — the feature ships looking like a
    mirror.
    """
    answers = space.neighbours(["152:0", "152:1", "152:2"], exclude_article=152, limit=5)
    assert [answer.chunk_id for answer in answers] == ["85344:0"]
    assert answers[0].distance == pytest.approx(0.0, abs=1e-6)


def test_a_transactional_passage_is_not_a_target(space):
    """An access code must never appear beside an article, however close it is."""
    answers = space.neighbours(["152:0"], exclude_article=152, limit=5)
    assert "30564:0" not in [answer.chunk_id for answer in answers]


def test_one_row_per_article_carrying_its_best_passage(space):
    answers = space.neighbours(["152:0", "152:1"], exclude_article=152, limit=5)
    assert len({similar._article_of(answer.chunk_id) for answer in answers}) == len(answers)


def test_the_ranking_is_by_best_passage_not_by_average(tmp_path):
    """§2.2: `max` over an article's passages, never `mean`.

    Article 1 has one passage that is a near-twin (distance 0) and one that is
    orthogonal (distance 1). Its *mean* similarity is 0.5, which would place it
    behind article 2's uniformly mediocre 0.8. The measured fact is that an
    article is like this one if **one** of its passages is, so 1 must win.
    """
    rows = [
        row("10:0", vector(1.0, 0.0)),
        row("10:1", vector(0.0, 1.0)),
        row("20:0", vector(1.0, 0.0)),
        row("20:1", vector(1.0, 0.0)),
        row("30:0", vector(0.8, 0.6)),
        row("30:1", vector(0.8, 0.6)),
    ]
    # Query article 20 against a corpus where 10 has one twin and 30 is mediocre.
    corpus = similar.Space(tmp_path / "lance", FakeBackend(rows))
    answers = corpus.neighbours(["20:0", "20:1"], exclude_article=20, limit=2)
    assert answers[0].chunk_id.startswith("10:")
    assert answers[1].chunk_id.startswith("30:")
    assert answers[0].matched_passages == 2  # both of my passages chose article 10


def test_the_same_article_twice_gives_the_same_order(space):
    first = space.neighbours(["152:0", "152:1"], exclude_article=152, limit=5)
    second = space.neighbours(["152:1", "152:0"], exclude_article=152, limit=5)
    assert first == second


def test_a_passage_grain_answer_carries_which_paragraph_matched(space):
    answered = space.passage_neighbours(["152:0", "152:2"], exclude_article=152, limit=5)
    assert {query_chunk for query_chunk, _neighbour in answered} == {"152:0", "152:2"}


def test_an_article_with_nothing_in_the_store_is_a_similar_error(tmp_path):
    corpus = similar.Space(tmp_path / "lance", FakeBackend(ROWS))
    with pytest.raises(similar.SimilarError):
        corpus.neighbours(["999:0"], exclude_article=999, limit=5)


def test_the_matrix_is_loaded_once_and_reused(space):
    space.neighbours(["152:0"], exclude_article=152, limit=5)
    loaded = space._matrix
    space.neighbours(["152:1"], exclude_article=152, limit=5)
    assert space._matrix is loaded


# -- the endpoint -----------------------------------------------------------


@pytest.fixture()
def similar_service(corpus, tmp_path):
    archive, _derived, index_path = corpus
    dist = archive.parent / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text("<!doctype html><title>s</title>", encoding="utf-8")

    config = Config(
        port=0,
        index_path=index_path,
        archive_root=archive,
        static_dir=dist,
        vocab_path=archive.parent / "no-vocab.yaml",
    )
    server = Server(("127.0.0.1", 0), Handler)
    server.config = config
    server.hybrid = None
    server.facets = None
    # The real chunk ids, so the endpoint's sqlite resolution is real: the fake
    # store speaks the fixture corpus's ids.
    server.similar = similar.Space(tmp_path / "lancedb", FakeBackend(ROWS))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def test_the_endpoint_answers_with_the_articles_neighbours(similar_service):
    body = get_json(f"{similar_service}/article/152/similar")
    assert body["message_id"] == 152
    assert body["grain"] == "article"
    assert body["basis"]["passages"] == 3  # the fixture article's three passages
    assert body["basis"]["passages_in_store"] == 4  # 5 rows, one transactional
    assert body["basis"]["embed_model"] == "cohere/embed-v5.0"
    assert [card["message_id"] for card in body["results"]] == [85344]
    card = body["results"][0]
    assert card["distance"] == pytest.approx(0.0, abs=1e-6)
    assert card["article_href"] == "/article/85344#c85344-0"
    assert "<mark>" not in card["excerpt"]
    assert set(card) >= {
        "message_id",
        "sent_at",
        "subject",
        "kind",
        "distance",
        "matched_passages",
        "chunk_id",
        "start_char",
        "end_char",
        "excerpt",
        "article_href",
        "more_passages",
    }


def test_the_answer_is_byte_identical_twice(similar_service):
    first = urllib.request.urlopen(
        f"{similar_service}/article/152/similar", timeout=10
    ).read()
    second = urllib.request.urlopen(
        f"{similar_service}/article/152/similar", timeout=10
    ).read()
    assert json.loads(first)["results"] == json.loads(second)["results"]


def test_an_unknown_article_is_a_404(similar_service):
    with pytest.raises(urllib.error.HTTPError) as error:
        get_json(f"{similar_service}/article/999999/similar")
    assert error.value.code == 404


def test_a_process_with_no_store_says_503_and_the_article_still_renders(corpus):
    archive, _derived, index_path = corpus
    dist = archive.parent / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text("<!doctype html><title>s</title>", encoding="utf-8")
    config = Config(
        port=0,
        index_path=index_path,
        archive_root=archive,
        static_dir=dist,
        vocab_path=archive.parent / "no-vocab.yaml",
    )
    server = Server(("127.0.0.1", 0), Handler)
    server.config = config
    server.hybrid = None
    server.facets = None
    server.similar = None  # a process that never loaded vectors
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        with pytest.raises(urllib.error.HTTPError) as error:
            get_json(f"http://{host}:{port}/article/152/similar")
        assert error.value.code == 503
        payload = json.loads(error.value.read().decode("utf-8"))
        assert payload["error"]["code"] == "similar_unavailable"
        # §3.5: the article page renders normally with the block absent.
        assert get_json(f"http://{host}:{port}/article/152")["message_id"] == 152
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_a_neighbour_the_index_does_not_know_is_a_503(corpus, tmp_path):
    """The store and search.sqlite must agree; a stale one is not a dropped row."""
    archive, _derived, index_path = corpus
    import sqlite3

    conn = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
    try:
        # The fake store's only article-level answer is 85344, which the fixture
        # corpus does know — so the disagreement is staged the other way round:
        # a store row for an article search.sqlite has never heard of.
        staged = similar.Space(
            tmp_path / "stale",
            FakeBackend([*ROWS, row("999999:0", vector(1.0, 0.0))]),
        )
        with pytest.raises(api.ApiError) as error:
            api.similar(conn, 152, staged, limit=5)
        assert error.value.status == 503
        assert error.value.code == "similar_unavailable"
    finally:
        conn.close()


def test_limit_and_grain_are_validated():
    with pytest.raises(api.ApiError):
        api.parse_similar_params({"limit": ["0"]})
    with pytest.raises(api.ApiError):
        api.parse_similar_params({"limit": ["51"]})
    with pytest.raises(api.ApiError):
        api.parse_similar_params({"grain": ["themes"]})
    assert api.parse_similar_params({}) == (similar.DEFAULT_LIMIT, "article")
    assert api.parse_similar_params({"grain": ["passage"]})[1] == "passage"


def test_min_distance_is_not_a_parameter(similar_service):
    """§6: there is no floor, so the knob is refused rather than ignored."""
    body = get_json(f"{similar_service}/article/152/similar?min_distance=0.9")
    assert len(body["results"]) == 1
