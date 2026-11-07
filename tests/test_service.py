"""Contract tests for the phase-1 API (§3).

These assert the response *shapes* the Svelte app is written against, field by
field, plus the failure modes: an unbuilt index is 503, bad parameters are 400,
and the service has no write path. A shape change here is a contract change.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from stratechery_search import api


def get(url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_healthz_reports_the_index(service):
    status, body = get(f"{service}/healthz")
    assert status == 200
    assert body["status"] == "ok"
    assert body["articles"] == 3
    assert body["chunks"] >= 3
    assert body["built_at"].endswith("Z")


def test_livez_is_independent_of_the_index(service):
    status, body = get(f"{service}/livez")
    assert status == 200
    assert body == {"status": "ok"}


def test_search_shape_is_the_contract(service):
    status, body = get(f"{service}/search?q=aggregation")
    assert status == 200

    assert set(body) == {
        "query",
        "total",
        "limit",
        "offset",
        "took_ms",
        "results",
        "facets",
    }
    assert body["query"]["raw"] == "aggregation"
    assert body["query"]["rewritten"] == '"aggregation"'
    assert body["query"]["expansions"] == []
    assert body["limit"] == 20 and body["offset"] == 0
    assert body["total"] >= 1

    result = body["results"][0]
    assert set(result) == {
        "chunk_id",
        "message_id",
        "sent_at",
        "subject",
        "kind",
        "sender",
        "score",
        "snippet",
        "start_char",
        "end_char",
        "article_href",
    }
    assert result["message_id"] == 152
    assert result["kind"] == "update"
    assert "<mark>aggregation</mark>" in result["snippet"]
    assert result["article_href"].startswith("/article/152#c152-")
    assert result["score"] > 0

    facets = body["facets"]
    assert set(facets) == {"by_year", "by_kind", "by_sender", "has_images"}
    assert facets["by_year"][0]["year"] == 2026
    assert set(facets["by_year"][0]) == {"year", "articles", "count"}
    assert set(facets["by_kind"][0]) == {"kind", "articles", "count"}
    assert set(facets["by_sender"][0]) == {"sender", "articles", "count"}
    assert set(facets["has_images"]) == {"true", "false"}


def test_transactional_mail_is_never_searchable(service):
    # Access codes, receipts and renewals are in the corpus — api.stats counts
    # them, so the articles/searchable gap on the corpus page stays explained —
    # but there is no query for which they are the right answer. That makes the
    # exclusion a clause in the WHERE, not a parameter, and the old
    # include_transactional escape hatch is gone: passing it must change
    # nothing rather than quietly bring the mails back.
    _, body = get(f"{service}/search?q=access%20code")
    assert body["total"] == 0

    _, ignored = get(f"{service}/search?q=access%20code&include_transactional=true")
    assert ignored["total"] == 0
    assert ignored["results"] == []

    _, stats = get(f"{service}/stats")
    assert stats["transactional"] == 1
    assert stats["searchable_articles"] == stats["articles"] - 1


def test_filters_and_facets_narrow_together(service):
    _, body = get(f"{service}/search?q=the&kind=update&from=2026-01-01")
    assert body["total"] >= 1
    assert all(result["kind"] == "update" for result in body["results"])
    assert [facet["year"] for facet in body["facets"]["by_year"]] == [2026]

    _, empty = get(f"{service}/search?q=the&kind=interview")
    assert empty["total"] == 0
    assert empty["results"] == []


def test_grouped_search_folds_an_articles_passages_under_one_row(service):
    # The fixture's first article repeats one paragraph, so it matches in several
    # chunks: ungrouped, "aggregation" returns those chunks as separate results.
    _, passages = get(f"{service}/search?q=aggregation")
    assert len(passages["results"]) > 1
    assert len({result["message_id"] for result in passages["results"]}) == 1

    _, body = get(f"{service}/search?q=aggregation&group=article")
    assert body["total"] == passages["total"]
    assert body["article_total"] == 1
    assert len(body["results"]) == 1

    lead = body["results"][0]
    assert lead["message_id"] == 152
    assert lead["matched_passages"] == len(passages["results"])
    assert (
        lead["more_total"] == len(lead["more_passages"]) == len(passages["results"]) - 1
    )
    assert all(chunk["message_id"] == 152 for chunk in lead["more_passages"])
    assert lead["chunk_id"] not in {
        chunk["chunk_id"] for chunk in lead["more_passages"]
    }
    # The lead is the best-ranked chunk of the article, not merely the first one.
    assert lead["score"] >= max(chunk["score"] for chunk in lead["more_passages"])


def test_grouped_paging_counts_articles_not_passages(service):
    # Passage paging would hand back another chunk of the same article at offset
    # 1, which is how page two came to repeat page one in the SPA.
    _, first = get(f"{service}/search?q=aggregation&group=article&limit=1&offset=0")
    _, second = get(f"{service}/search?q=aggregation&group=article&limit=1&offset=1")
    assert len(first["results"]) == 1
    assert first["article_total"] == 1
    assert second["results"] == []


def test_group_must_be_a_known_mode(service):
    status, body = get(f"{service}/search?q=the&group=author")
    assert status == 400
    assert body["error"]["code"] == "bad_request"


def test_browsing_a_period_lists_its_articles_newest_first(service):
    # An empty q with a filter is a question a reader asks — "2016 onwards" — not
    # a request for everything. Nothing matched, so nothing is ranked either.
    status, body = get(f"{service}/search?from=2016-01-01&group=article")
    assert status == 200
    assert body["browse"] is True
    assert body["query"] == {
        "raw": "",
        "rewritten": "",
        "expansions": [],
        "resolved": [],
    }
    assert body["article_total"] == 2
    # The transaction mail is in 2024, so it is never in a browsed page.
    assert [result["message_id"] for result in body["results"]] == [152, 85344]
    assert body["results"][0]["sent_at"] > body["results"][1]["sent_at"]

    result = body["results"][0]
    # The card shows how the article opens, since there is no match to excerpt.
    assert result["snippet"].startswith("The aggregation theory")
    assert "<mark>" not in result["snippet"]
    # Nothing "matched", so nothing is folded underneath.
    assert result["more_total"] == 0
    assert result["more_passages"] == []


def test_a_browse_snippet_does_not_repeat_the_title():
    # Several issues open by repeating the subject line, which the card shows an
    # inch above the snippet; the archived subject carries a parenthetical tail the
    # body copy does not, so the comparison cannot be a plain startswith().
    from stratechery_search.api import _opening

    subject = "IBM Misses, IBM's Mainframe Moat (Stratechery Update 7-15-2026)"
    text = (
        "IBM Misses, IBM's Mainframe Moat\n\nWednesday, July 15, 2026\n\nGood morning,"
    )
    assert _opening(text, subject) == "Wednesday, July 15, 2026 Good morning,"
    # A dek before the title is the best possible preview and is left alone.
    dek = "IBM announced preliminary results that spooked the software market."
    assert _opening(dek + "\n\n" + text, subject).startswith("IBM announced")


def test_browsing_pages_by_article_and_reports_the_filtered_total(service):
    _, first = get(f"{service}/search?from=2016-01-01&group=article&limit=1&offset=0")
    _, second = get(f"{service}/search?from=2016-01-01&group=article&limit=1&offset=1")
    assert [result["message_id"] for result in first["results"]] == [152]
    assert [result["message_id"] for result in second["results"]] == [85344]
    # `total` counts passages and `article_total` counts articles, exactly as in
    # the searched shape, so the pager needs no special case.
    assert first["total"] > first["article_total"] == 2


def test_browsing_facets_describe_the_browsed_set(service):
    _, body = get(f"{service}/search?from=2016-01-01&group=article")
    years = {bucket["year"]: bucket["articles"] for bucket in body["facets"]["by_year"]}
    assert years == {2018: 1, 2026: 1}
    kinds = {bucket["kind"] for bucket in body["facets"]["by_kind"]}
    assert kinds == {"update"}


def test_browsing_still_excludes_transactional_mail(service):
    # 30564 is the only article in 2024 and it is an access code.
    _, body = get(f"{service}/search?from=2024-01-01&to=2024-12-31&group=article")
    assert body["results"] == []
    assert body["article_total"] == 0


def test_an_empty_q_with_nothing_to_narrow_it_is_still_400(service):
    status, body = get(f"{service}/search?q=&group=article")
    assert status == 400
    assert body["error"]["message"] == "q is required"


def test_pagination_is_reported(service):
    _, body = get(f"{service}/search?q=the&limit=1&offset=1")
    assert body["limit"] == 1
    assert body["offset"] == 1
    assert len(body["results"]) <= 1


@pytest.mark.parametrize(
    "query",
    [
        "",
        "q=",
        "q=the&kind=nonsense",
        "q=the&limit=0",
        "q=the&limit=101",
        "q=the&offset=-1",
        "q=the&from=July",
        "q=the&has_images=maybe",
    ],
)
def test_bad_parameters_are_400(service, query):
    status, body = get(f"{service}/search?{query}")
    assert status == 400
    assert set(body) == {"error"}
    assert body["error"]["code"] == "bad_request"
    assert body["error"]["message"]


def test_a_half_typed_word_finds_nothing_without_partial(service):
    # The measurement the whole feature rests on: `aggregation` indexes as
    # `aggreg` (Porter), so a reader who has typed eight of its eleven characters
    # is asking for a term longer than the only one stored, and gets nothing.
    status, body = get(f"{service}/search?q=aggregat")
    assert status == 200
    assert body["total"] == 0
    assert body["query"]["rewritten"] == '"aggregat"'
    assert body["query"]["resolved"] == []


def test_partial_makes_a_half_typed_word_find_the_word_it_is_becoming(service):
    status, body = get(f"{service}/search?q=aggregat&partial=1")
    assert status == 200
    assert body["total"] == 3
    # The spelling of "quoted token, prefixed" is an FTS5 grammar question, so it
    # is pinned here rather than assumed: the wrong form fails as a syntax error.
    assert body["query"]["rewritten"] == '("aggregat"* OR "aggregation")'
    # The rewrite is visible, for the same reason expansions are.
    assert body["query"]["resolved"] == ["aggregation"]


def test_only_the_unfinished_token_is_prefixed_and_expanded(service):
    # Completed words keep their meaning; prefixing them would widen the result
    # set and flatten the ranking for no reader benefit (§4.2).
    status, body = get(f"{service}/search?q=aggregation+integr&partial=1")
    assert status == 200
    assert (
        body["query"]["rewritten"] == '"aggregation" AND ("integr"* OR "integration")'
    )
    assert body["query"]["resolved"] == ["integration"]


def test_one_character_is_not_a_prefix(service):
    # A single character matches a large share of the vocabulary and ranks
    # arbitrarily: below the floor the token is quoted as ordinary search does.
    status, body = get(f"{service}/search?q=a&partial=1")
    assert status == 200
    assert body["query"]["rewritten"] == '"a"'
    assert body["query"]["resolved"] == []


def test_a_one_character_word_does_not_empty_the_page(service):
    # Measured on a phone, 2026-11-07: typing "smiling c" on the way to "smiling
    # curve" rendered "No passages match" for one keystroke, because a
    # one-character tail was quoted whole (`"smiling" AND "c"`) and matched
    # nothing. A word that has been started but not finished must not constrain
    # the query yet — the answer stays the one the completed words gave.
    _, whole = get(f"{service}/search?q=aggregation")
    status, body = get(f"{service}/search?q=aggregation+t&partial=1")
    assert status == 200
    assert body["query"]["rewritten"] == '"aggregation"'
    assert body["query"]["resolved"] == []
    assert body["total"] == whole["total"] > 0


def test_partial_works_on_the_grouped_path_the_ui_asks_for(service):
    # The browser sends group=article, which ranks articles rather than passages;
    # the expression is the same one, so the fold must not lose the match.
    status, body = get(f"{service}/search?q=aggregat&partial=1&group=article")
    assert status == 200
    assert body["article_total"] == 1
    assert body["total"] == 3


@pytest.mark.parametrize(
    "raw",
    ['agg"', "aggregat*", "agg-r", "don't", "OR", "(agg", "aggregat ", "-agg", ":"],
)
def test_partial_never_becomes_fts_syntax(service, raw):
    # Claim 5: no keystroke produces an error state. A query with no words in it
    # is a 400 by contract — the UI's job is not to send it — but FTS5 syntax
    # must never escape, which would be a 500 rather than a refusal.
    status, body = get(f"{service}/search?partial=1&q={urllib.parse.quote(raw)}")
    assert status in {200, 400}
    if status == 400:
        assert body["error"]["code"] == "bad_request"
        assert "no searchable words" in body["error"]["message"]


def test_partial_must_be_a_boolean(service):
    status, body = get(f"{service}/search?q=the&partial=maybe")
    assert status == 400
    assert body["error"]["code"] == "bad_request"


def test_vocab_defaults_to_the_repo_file_not_the_data_root(monkeypatch):
    # The synonym table is reviewed like code, so its default is the checkout's
    # own vocab.yaml — not a file in the writable appdata volume.
    from stratechery_search.server import Config

    monkeypatch.delenv("VOCAB_PATH", raising=False)
    assert Config().vocab_path == Path("vocab.yaml")
    # The default is cwd-relative, like STATIC_DIR, and does not follow DATA_ROOT.
    monkeypatch.setenv("DATA_ROOT", "/tmp/somewhere")
    assert Config().vocab_path == Path("vocab.yaml")


def test_vocab_path_env_overrides_the_default(monkeypatch, tmp_path):
    from stratechery_search.server import Config

    monkeypatch.setenv("VOCAB_PATH", str(tmp_path / "v.yaml"))
    assert Config().vocab_path == tmp_path / "v.yaml"
    # An empty value must not become Path("."), a directory that read_text()
    # refuses on every search.
    monkeypatch.setenv("VOCAB_PATH", "")
    assert Config().vocab_path == Path("vocab.yaml")


def test_a_real_vocabulary_file_drives_the_rewrite(tmp_path):
    from stratechery_search.server import Config

    path = tmp_path / "vocab.yaml"
    path.write_text(
        "terms:\n"
        "  - canonical: aggregation theory\n"
        "    aliases: [aggregation, aggregators]\n",
        encoding="utf-8",
    )
    vocab = api.load_vocab(Config(vocab_path=path).vocab_path)
    rewritten, expansions = api.expand_query("aggregation theory", vocab)
    assert rewritten == '("aggregation" OR "aggregation theory" OR "aggregators")'
    assert expansions[0]["canonical"] == "aggregation theory"
    assert expansions[0]["source"] == "vocab.yaml"


def test_a_malformed_vocabulary_file_is_an_answered_503(corpus, tmp_path):
    """A typo in vocab.yaml must not drop the connection.

    ``load_vocab()`` runs inside the /search handler, and ``VocabError`` is a
    ``ValueError`` rather than an ``ApiError``: before the guard in
    ``api.load_vocab``, one stray line propagated out of ``do_GET`` and
    ``BaseHTTPRequestHandler`` closed the socket — on every search, until the file
    was fixed or deleted. The reader got a dead connection instead of the
    contract's ``{"error": {...}}``. This asserts the answered 503, which is also
    the assertion that the socket was not dropped: a dropped connection raises
    ``URLError``/``RemoteDisconnected`` out of ``get()`` rather than returning.
    """
    import threading

    from stratechery_search.server import Config, Handler, Server

    archive, _derived, index_path = corpus
    bad_vocab = tmp_path / "vocab.yaml"
    bad_vocab.write_text(
        "terms:\n"
        "  - canonical: Digital Markets Act\n"
        "    aliases: [DMA]\n"
        "  - canonical: Broken\n"
        "    this line is not part of the accepted shape\n",
        encoding="utf-8",
    )
    config = Config(
        port=0,
        index_path=index_path,
        archive_root=archive,
        static_dir=tmp_path / "dist",
        vocab_path=bad_vocab,
    )
    server = Server(("127.0.0.1", 0), Handler)
    server.config = config
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        status, body = get(f"http://{host}:{port}/search?q=DMA")
        assert status == 503
        assert body["error"]["code"] == "vocab_invalid"
        # The file's own line number, so the typo can be found without guessing.
        assert ":5:" in body["error"]["detail"]["detail"]
        # And the next request is answered the same way, not refused at the
        # socket: the handler recovered rather than leaving the service dead.
        status, body = get(f"http://{host}:{port}/search?q=aggregation")
        assert status == 503
        assert body["error"]["code"] == "vocab_invalid"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_a_missing_resolver_degrades_instead_of_failing(corpus):
    # A new image against an index built before `terms` existed is a real window:
    # the two deploy independently. Type-ahead must then behave like the search it
    # replaced, not 500 on every keystroke.
    _archive, _derived, index_path = corpus
    conn = sqlite3.connect(index_path)
    try:
        conn.execute("DROP TABLE terms")
        conn.commit()
        body = api.search(conn, api.SearchParams(q="aggregat", partial=True), {})
        assert body["total"] == 0
        assert body["query"]["resolved"] == []
        assert body["query"]["rewritten"] == '("aggregat"*)'
    finally:
        conn.close()


def test_punctuation_never_becomes_fts_syntax(service):
    status, body = get(f"{service}/search?q=%22quoted%22%20AND%20-or%20*the*")
    assert status == 200
    # Operators and wildcards are read as words, never as FTS5 syntax.
    assert body["query"]["rewritten"] == '"quoted" "AND" "or" "the"'


def test_article_shape_is_the_contract(service):
    status, body = get(f"{service}/article/152")
    assert status == 200
    assert set(body) == {
        "message_id",
        "sent_at",
        "subject",
        "kind",
        "sender",
        "from_header",
        "chars",
        "words",
        "est_tokens",
        "is_transactional",
        "derived",
        "archive",
        "chunks",
        "text",
        "images",
        "n_images",
    }
    assert body["message_id"] == 152
    assert body["is_transactional"] is False
    assert set(body["derived"]) == {
        "flattener_version",
        "normalizer_version",
        "text_sha256",
    }
    assert set(body["archive"]) == {"sha256", "bytes", "path"}
    assert body["archive"]["path"] == "raw/152.eml"
    assert body["text"].startswith("The aggregation theory")
    assert body["text"].rstrip().endswith("value chain.")
    assert set(body["chunks"][0]) == {"chunk_id", "ordinal", "start_char", "end_char"}
    assert body["chunks"][0]["chunk_id"] == "152:0"

    image = body["images"][0]
    assert set(image) == {"url_path", "asset", "sha256", "bytes", "href"}
    assert image["href"] == "/asset/aaaa0000.png"


def test_article_text_is_the_derived_body_not_concatenated_chunks(service, corpus):
    _archive, derived, _index = corpus
    _, body = get(f"{service}/article/152")
    assert body["text"] == (derived / "152.txt").read_text(encoding="utf-8")


def test_url_only_image_has_null_asset_and_null_href(service):
    _, body = get(f"{service}/article/85344")
    image = body["images"][0]
    assert image["asset"] is None
    assert image["href"] is None
    assert image["url_path"].startswith("https://")


def test_unknown_article_is_404(service):
    status, body = get(f"{service}/article/999999")
    assert status == 404
    assert body["error"]["code"] == "not_found"


def test_non_integer_article_id_is_400(service):
    status, body = get(f"{service}/article/abc")
    assert status == 400
    assert body["error"]["code"] == "bad_request"


def test_stats_shape_is_the_contract(service):
    status, body = get(f"{service}/stats")
    assert status == 200
    assert body["articles"] == 3
    assert body["searchable_articles"] == 2
    assert body["transactional"] == 1
    assert set(body["images"]) == {"distinct_assets", "articles_with_images"}
    assert sum(year["articles"] for year in body["by_year"]) == body["articles"]
    assert set(body["index"]) == {"flattener_version", "chunker_version", "built_at"}
    assert isinstance(body["notes"], list)
    # notes[] is computed at request time from the same counts this response
    # carries, not replayed from the build-time snapshot: a snapshot can only go
    # stale, and did — it kept describing a 2017 gap the page had stopped
    # drawing.
    from stratechery_search.notes import stats_notes

    daily_from = body["searchable"]["daily_from"]
    assert body["notes"] == stats_notes(
        {bucket["year"]: bucket["articles"] for bucket in body["by_year"]},
        int(daily_from[:4]) if daily_from else None,
    )


def test_stats_offers_the_searchable_slices_the_landing_screen_builds_chips_from(
    service,
):
    # by_year/by_kind describe the whole archive; the landing screen's chips
    # must describe what a chip can actually reach, or 2024 (this corpus's only
    # 2024 article is an access code) becomes a chip that returns nothing.
    _status, body = get(f"{service}/stats")
    searchable = body["searchable"]
    assert [bucket["year"] for bucket in searchable["by_year"]] == ["2018", "2026"]
    assert sum(bucket["articles"] for bucket in searchable["by_year"]) == 2
    assert [bucket["kind"] for bucket in searchable["by_kind"]] == ["update"]
    assert sum(bucket["articles"] for bucket in searchable["by_kind"]) == 2
    # The corpus page states the subscription boundary beside its per-year
    # chart: a short bar before it means "not subscribed yet", not "published
    # less". This fixture is two updates eleven years apart, so it cannot claim
    # a boundary at all — and says so rather than inventing one.
    assert searchable["daily_from"] is None


def test_asset_is_served_with_an_image_content_type(service):
    with urllib.request.urlopen(
        f"{service}/asset/aaaa0000.png", timeout=10
    ) as response:
        assert response.status == 200
        assert response.headers["Content-Type"] == "image/png"
        assert response.read() == bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 16


@pytest.mark.parametrize(
    "path",
    ["/asset/..%2Fsecret.png", "/asset/secret.txt", "/asset/nope.png", "/asset/"],
)
def test_asset_rejects_escapes_and_unknown_types(service, path):
    status, body = get(f"{service}{path}")
    assert status in {400, 404}
    assert body["error"]["code"] in {"bad_request", "not_found"}


def test_unbuilt_index_is_503(tmp_path):
    """A missing index is a degraded product, not a dead process."""
    import threading

    from stratechery_search.server import Config, Handler, Server

    config = Config(
        port=0,
        index_path=tmp_path / "missing.sqlite",
        archive_root=tmp_path,
        static_dir=tmp_path / "dist",
        # None now means the repo's own vocab.yaml; absence has to be said out
        # loud, or a committed synonym table would silently rewrite what these
        # tests assert.
        vocab_path=tmp_path / "no-vocab.yaml",
    )
    server = Server(("127.0.0.1", 0), Handler)
    server.config = config
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        status, body = get(f"http://{host}:{port}/healthz")
        assert status == 503
        assert body["error"]["code"] == "index_unbuilt"
        status, body = get(f"http://{host}:{port}/search?q=the")
        assert status == 503
        # /livez still answers: the process is alive, the index is not.
        status, _body = get(f"http://{host}:{port}/livez")
        assert status == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_the_service_has_no_write_path(service):
    request = urllib.request.Request(
        f"{service}/search?q=the", method="POST", data=b"{}"
    )
    try:
        urllib.request.urlopen(request, timeout=10)
    except urllib.error.HTTPError as error:
        assert error.code == 405
        body = json.loads(error.read())
        assert body["error"]["code"] == "method_not_allowed"
    else:  # pragma: no cover - a 2xx here is the failure
        raise AssertionError("POST /search was accepted")


def test_browser_navigation_negotiates_index_html(service):
    # A browser reloading or directly navigating to /stats or /article/{id} sends
    # text/html in Accept, which should serve index.html rather than raw JSON.
    for path in ("/stats", "/article/152"):
        request = urllib.request.Request(
            f"{service}{path}",
            headers={"Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.status == 200
            assert "text/html" in response.headers.get("Content-Type", "")
            body = response.read().decode("utf-8")
            assert '<div id="app"></div>' in body

    # An API caller (e.g. Svelte api.js or curl) requests application/json and receives JSON.
    for path in ("/stats", "/article/152"):
        request = urllib.request.Request(
            f"{service}{path}",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.status == 200
            assert "application/json" in response.headers.get("Content-Type", "")
            data = json.loads(response.read())
            assert isinstance(data, dict)


def test_app_icon_and_manifest_are_served_with_real_content_types(service):
    # A browser asks for the icon and the manifest before any of this app's JS
    # runs, and a wrong Content-Type makes it ignore both (a manifest served as
    # text/plain will not install, an SVG served as octet-stream will not render).
    for path, expected in (
        ("/favicon.svg", "image/svg+xml"),
        ("/site.webmanifest", "application/manifest+json"),
    ):
        with urllib.request.urlopen(f"{service}{path}", timeout=10) as response:
            assert response.status == 200
            assert response.headers.get("Content-Type", "") == expected, path


def test_index_html_names_the_app_in_full(service):
    # The product is "Stratechery Search", capital S — not the lowercase
    # "stratechery/search" wordmark the header used to draw, and not the
    # sentence-case title the first build shipped.
    with urllib.request.urlopen(f"{service}/", timeout=10) as response:
        body = response.read().decode("utf-8")
    assert '<div id="app"></div>' in body
    assert "<title>Stratechery Search</title>" in body
    assert "Stratechery search" not in body


def test_the_json_a_keystroke_asks_for_is_compressed(service):
    # Measured 2026-11-07 on a phone over the tailnet: one type-ahead response was
    # 73 KB, uncompressed, against 11 KB gzipped — thirty times the query's own
    # cost. Compressing it is the cheapest latency there is.
    request = urllib.request.Request(
        f"{service}/search?q=aggregation", headers={"Accept-Encoding": "gzip"}
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.headers.get("Content-Encoding") == "gzip"
        # urllib does not decompress; gzip.decompress on the raw bytes is proof
        # that what was sent is the body, not a header promise.
        body = json.loads(gzip.decompress(response.read()))
        # Vary is what stops a cache from handing the identity form to a client
        # that asked for gzip.
        assert "Accept-Encoding" in response.headers.get("Vary", "")
    assert body["total"] > 0


def test_a_client_that_did_not_ask_gets_plain_bytes(service):
    with urllib.request.urlopen(f"{service}/search?q=aggregation", timeout=10) as response:
        assert response.headers.get("Content-Encoding") is None
        assert json.loads(response.read())["total"] > 0


def test_head_answers_instead_of_501(service):
    # `curl -I` and every uptime monitor use HEAD. It used to be 501 — which
    # reads as "the site is broken", not as "unsupported method".
    request = urllib.request.Request(f"{service}/healthz", method="HEAD")
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.status == 200
        assert response.read() == b""
        assert int(response.headers["Content-Length"]) > 0
