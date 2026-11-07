"""Ranking weights: what the two FTS columns are worth.

The subject line is the article's own name for what it is about, and every chunk
of an article carries it. Weighting it low makes an article that *is* about a
topic compete with every paragraph that merely mentions it: measured on the live
index, `aggregation theory` put the two articles named for it 8th, because 308
chunks matched both words and the top three scores sat within half a percent of
each other.

These tests pin the decision rather than the numbers — a subject match outranks a
body match — and pin that the weights are written once, not copied into two SQL
strings that can drift apart.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from corpus import ARTICLE_BODY, SECOND_BODY
from corpus import build as build_corpus
from stratechery_search import api

#: The default article body with one mention, near the front, of a word that
#: belongs in another article's subject. One mention is deliberate: it is the
#: smallest honest case, and it is the one where the old 10:1 weighting against
#: the subject put the *wrong* article first (measured: 152 first at 10/1,
#: 85344 first at 2/10).
NOISY = "Amazon is a company. " + ARTICLE_BODY


def test_a_subject_match_outranks_a_body_match(tmp_path):
    _archive, _derived, index_path = build_corpus(tmp_path, bodies={152: NOISY})
    conn = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
    results = api.search(conn, api.parse_search_params({"q": ["amazon"]}), {})
    # The Amazon article says the word once, in its subject; the IBM article
    # says it once, in its body. Same term frequency, so only the column
    # weights can decide — and a reader typing `amazon` is naming a topic.
    assert results["results"][0]["subject"].startswith("More on Amazon")
    # The body match is not thrown away, it is outranked.
    assert {row["message_id"] for row in results["results"]} == {152, 85344}


def test_the_body_still_answers_a_query_no_subject_covers(tmp_path):
    _archive, _derived, index_path = build_corpus(tmp_path, bodies={152: NOISY})
    conn = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
    results = api.search(
        conn, api.parse_search_params({"q": ["services", "revenue"]}), {}
    )
    assert [row["message_id"] for row in results["results"]] == [85344]
    assert "services" in results["results"][0]["snippet"].lower()
    assert SECOND_BODY.split(" ")[0] in results["results"][0]["snippet"]


def test_the_weights_are_written_once_and_the_subject_is_the_heavier_column():
    assert api.BM25_SUBJECT_WEIGHT > api.BM25_TEXT_WEIGHT
    source = Path(api.__file__).read_text(encoding="utf-8")
    # Both the paged projection and the grouped CTE rank with the same weights;
    # two copies of a literal is how the two paths come to disagree.
    assert source.count("bm25(chunks_fts, {BM25_TEXT_WEIGHT}") == 2
