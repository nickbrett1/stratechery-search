"""Contract tests for the phase-1 API (§3).

These assert the response *shapes* the Svelte app is written against, field by
field, plus the failure modes: an unbuilt index is 503, bad parameters are 400,
and the service has no write path. A shape change here is a contract change.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest


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
        vocab_path=None,
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
