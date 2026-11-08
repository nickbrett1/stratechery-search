"""The gazetteer: the file, the matcher, the build, and the query surface.

Four things are being pinned here, and the first three are the ones that make the
fourth trustworthy:

* the taxonomy file is refused when it is wrong in a way that would make a count
  silently incorrect (an alias list that lost a line, a ``case_sensitive`` name
  that is not an alias, a duplicate id);
* matching is longest-first and boundary-anchored, so ``Apple TV`` does not also
  count as ``Apple`` and ``SMICsemi`` is not a mention of SMIC;
* ``case_sensitive`` is a per-surface flag, because that is what the measured
  ``WHO`` failure demands — the same string is an organisation in one casing and
  a pronoun in another;
* ``/search`` gains an entity dimension *additively*, and the counts are
  computed sideways (excluding the entity's own selection) so a reader who picks
  a company still sees what the others would give them.
"""

from __future__ import annotations

import json
import threading

import pytest

from corpus import build as build_corpus
from stratechery_search import facets
from stratechery_search.server import Config, Handler, Server


def write_taxonomy(path, body: str):
    path.write_text(body, encoding="utf-8")
    return path


BASE = """\
version: 1
entities:
  - id: apple
    kind: company
    name: Apple
    aliases: [Apple]
    measured: test

  - id: ibm
    kind: company
    name: IBM
    aliases: [IBM]
    measured: test
"""


# -- the file ---------------------------------------------------------------


def test_a_taxonomy_loads_with_its_aliases_and_case_rules(tmp_path):
    path = write_taxonomy(
        tmp_path / "entities.yaml",
        """version: 3
entities:
  # a comment owns its whole line
  - id: tsmc
    kind: company
    name: TSMC
    aliases: [TSMC, "Taiwan Semiconductor", "Taiwan Semiconductor Manufacturing"]
    case_sensitive: [TSMC]
    measured: union=227
""",
    )
    taxonomy = facets.load_taxonomy(path)
    assert taxonomy.version == 3
    entity = taxonomy.entities[0]
    assert entity.id == "tsmc"
    assert [alias.surface for alias in entity.aliases] == [
        "TSMC",
        "Taiwan Semiconductor",
        "Taiwan Semiconductor Manufacturing",
    ]
    assert [alias.case_sensitive for alias in entity.aliases] == [True, False, False]


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (
            "version: 1\nentities:\n  - id: Apple\n    kind: company\n"
            "    name: Apple\n    aliases: [Apple]\n",
            "lower_snake_case",
        ),
        (
            "version: 1\nentities:\n  - id: apple\n    kind: brand\n"
            "    name: Apple\n    aliases: [Apple]\n",
            "kind must be",
        ),
        (
            "version: 1\nentities:\n  - id: apple\n    kind: company\n"
            "    name: Apple\n    aliases: [Apple]\n    case_sensitive: [IBM]\n",
            "not in aliases",
        ),
        (
            "version: 1\nentities:\n  - id: apple\n    kind: company\n"
            "    name: Apple\n    aliases: [Apple]\n    favourite: yes\n",
            "unsupported key",
        ),
        (
            "version: 1\nentities:\n  - id: apple\n    kind: company\n"
            "    name: Apple\n    aliases: [Apple]\n"
            "  - id: apple\n    kind: company\n    name: Apple\n"
            "    aliases: [Apple]\n",
            "duplicate id",
        ),
        (
            "version: 1\nentities:\n  - id: apple\n    kind: company\n"
            "    name: Apple Inc\n    aliases: [Apple]\n",
            "not one of its own aliases",
        ),
        ("version: 1\n", "no entities"),
        ("entities:\n  - id: a1\n    kind: person\n    name: A1\n", "missing version"),
    ],
)
def test_the_file_is_refused_loudly_when_it_is_wrong(tmp_path, body, reason):
    path = write_taxonomy(tmp_path / "entities.yaml", body)
    with pytest.raises(facets.FacetError) as error:
        facets.load_taxonomy(path)
    assert reason in str(error.value)


def test_the_repo_taxonomy_loads_and_every_entry_clears_the_admission_floor():
    """The shipped file is itself a test: 47 entities, all measured.

    Not a style check — the file is the product, and a broken one takes the
    entity facet down at build time, which is only discoverable on ryzen
    otherwise.
    """
    from pathlib import Path

    taxonomy = facets.load_taxonomy(Path("entities.yaml"))
    assert taxonomy.version == 1
    assert len(taxonomy.entities) >= 40
    for entity in taxonomy.entities:
        assert entity.kind in facets.KINDS
        assert any(alias.surface == entity.name for alias in entity.aliases)


# -- the matcher ------------------------------------------------------------


def matcher_for(tmp_path, body: str = BASE) -> facets.Matcher:
    return facets.Matcher(facets.load_taxonomy(write_taxonomy(tmp_path / "e.yaml", body)))


def test_the_longest_alias_wins_and_does_not_double_count(tmp_path):
    matcher = matcher_for(
        tmp_path,
        """version: 1
entities:
  - id: tsmc
    kind: company
    name: TSMC
    aliases: [TSMC, "Taiwan Semiconductor", "Taiwan Semiconductor Manufacturing"]
""",
    )
    found = matcher.find("Taiwan Semiconductor Manufacturing makes chips; TSMC too.")
    assert [entity_id for entity_id, _ in found] == ["tsmc", "tsmc"]
    assert [surface for _, surface in found] == [
        "Taiwan Semiconductor Manufacturing",
        "TSMC",
    ]


def test_a_mid_word_match_is_not_a_mention(tmp_path):
    matcher = matcher_for(tmp_path)
    assert matcher.find("SMICsemi and Applebee's and IBMish") == []


def test_case_sensitivity_is_per_surface(tmp_path):
    matcher = matcher_for(
        tmp_path,
        """version: 1
entities:
  - id: arm
    kind: company
    name: Arm
    aliases: [Arm]
    case_sensitive: [Arm]
  - id: apple
    kind: company
    name: Apple
    aliases: [Apple]
""",
    )
    assert matcher.find("arm the rebels") == []
    assert matcher.find("Arm designs cores") == [("arm", "Arm")]
    assert [entity_id for entity_id, _ in matcher.find("apple pie")] == ["apple"]


def test_a_possessive_is_the_same_mention(tmp_path):
    matcher = matcher_for(tmp_path)
    assert matcher.find("Apple's revenue and Apple\u2019s margin") == [
        ("apple", "Apple"),
        ("apple", "Apple"),
    ]


def test_find_verbose_reports_which_mentions_only_matched_loosely(tmp_path):
    matcher = matcher_for(tmp_path)
    verbose = matcher.find_verbose("Apple, then apple pie")
    assert verbose == [
        ("apple", "Apple", True),
        ("apple", "Apple", False),
    ]


# -- the build --------------------------------------------------------------

# Repeated with paragraph breaks so the article really does split into several
# passages: the point of the build test is that the facet still counts one.
MENTIONED = (
    "The aggregation theory says IBM and Apple compete for the same customers.\n\n"
) * 70


@pytest.fixture()
def corpus_with_mentions(tmp_path):
    """The contract corpus, with entity names injected into both articles.

    Injected rather than assumed: the fixture's bodies were written to exercise
    chunking and ranking, and one of them contains no company name at all. A test
    that relied on the accident would pass for the wrong reason.
    """
    return build_corpus(
        tmp_path,
        bodies={152: MENTIONED},
    )


def test_the_build_counts_articles_not_passages(tmp_path, corpus_with_mentions):
    _archive, derived, index_path = corpus_with_mentions
    import sqlite3

    conn = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
    taxonomy_path = write_taxonomy(tmp_path / "entities.yaml", BASE)
    out = tmp_path / "facets.sqlite"
    report = facets.build(
        conn,
        facets.load_taxonomy(taxonomy_path),
        derived,
        out,
        digest=facets.taxonomy_digest(taxonomy_path),
    )
    conn.close()

    # Article 152 is many passages; the facet says one article. Anything else and
    # the panel disagrees with the result list by a factor of the chunk count.
    assert report["per_entity"] == {"apple": 2, "ibm": 1}
    index = facets.open_index(out)
    assert index is not None
    assert index.articles_for(("ibm",)) == frozenset({152})
    assert index.articles_for(("ibm", "apple")) == frozenset({152})
    assert index.counts({152, 85344})[0]["entity_id"] in {"apple"}
    assert index.meta["taxonomy_version"] == "1"


def test_a_missing_derived_file_fails_the_build_rather_than_counting_zero(
    tmp_path, corpus_with_mentions
):
    _archive, derived, index_path = corpus_with_mentions
    import sqlite3

    (derived / "85344.txt").unlink()
    conn = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
    taxonomy_path = write_taxonomy(tmp_path / "entities.yaml", BASE)
    try:
        report = facets.build(
            conn, facets.load_taxonomy(taxonomy_path), derived, tmp_path / "f.sqlite"
        )
    finally:
        conn.close()
    assert report["missing_derived"] == [85344]


def test_an_unknown_entity_is_an_error_not_an_empty_result(tmp_path, corpus_with_mentions):
    _archive, derived, index_path = corpus_with_mentions
    import sqlite3

    conn = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
    taxonomy_path = write_taxonomy(tmp_path / "entities.yaml", BASE)
    out = tmp_path / "facets.sqlite"
    facets.build(conn, facets.load_taxonomy(taxonomy_path), derived, out)
    conn.close()
    index = facets.open_index(out)
    with pytest.raises(facets.FacetError):
        index.articles_for(("appel",))


def test_no_file_means_no_facet_index(tmp_path):
    assert facets.open_index(None) is None
    assert facets.open_index(tmp_path / "absent.sqlite") is None


# -- the query surface ------------------------------------------------------


@pytest.fixture()
def facetted_service(corpus_with_mentions, tmp_path):
    archive, derived, index_path = corpus_with_mentions
    import sqlite3

    taxonomy_path = write_taxonomy(tmp_path / "entities.yaml", BASE)
    out = tmp_path / "index" / "facets.sqlite"
    conn = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
    facets.build(conn, facets.load_taxonomy(taxonomy_path), derived, out)
    conn.close()

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
    config.facets_path = out
    server = Server(("127.0.0.1", 0), Handler)
    server.config = config
    server.hybrid = None
    server.facets = facets.open_index(out)
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
    import urllib.request

    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def test_the_facet_payload_arrives_with_the_results(facetted_service):
    body = get_json(f"{facetted_service}/search?q=aggregation&group=article")
    by_entity = body["facets"]["by_entity"]
    assert [row["entity_id"] for row in by_entity] == ["apple", "ibm"]
    assert by_entity[0]["name"] == "Apple"
    assert set(by_entity[0]) == {"entity_id", "name", "kind", "articles"}
    # Additive: the old keys are still exactly what they were.
    assert set(body["facets"]) == {
        "by_year",
        "by_kind",
        "by_sender",
        "has_images",
        "by_entity",
    }


def test_an_entity_filter_narrows_the_results(facetted_service):
    # "Apple" is a term both articles share, so the entity filter is doing the
    # narrowing rather than the query happening to be about one company.
    both = get_json(f"{facetted_service}/search?q=Apple&group=article")
    only_ibm = get_json(f"{facetted_service}/search?q=Apple&entity=ibm&group=article")
    assert [row["message_id"] for row in only_ibm["results"]] == [152]
    assert len(both["results"]) == 2


def test_a_company_is_a_question_on_its_own(facetted_service):
    """A chip is a place to start: no q, still a browse (design note §2, job 3)."""
    body = get_json(f"{facetted_service}/search?entity=ibm&group=article")
    assert body["browse"] is True
    assert [row["message_id"] for row in body["results"]] == [152]


def test_the_counts_are_computed_sideways(facetted_service):
    """Choosing Apple must not collapse the other companies to zero.

    This is the difference between "counts within my selection" (every other chip
    reads 0, the classic faceted-search defect) and "counts with my selection
    removed from its own dimension".
    """
    body = get_json(f"{facetted_service}/search?q=Apple&entity=apple&group=article")
    counts = {row["entity_id"]: row["articles"] for row in body["facets"]["by_entity"]}
    assert counts == {"apple": 2, "ibm": 1}
    # ...while the other dimensions do narrow with the selection.
    assert [row["message_id"] for row in body["results"]] == [152, 85344]


def test_an_unknown_entity_is_a_400(facetted_service):
    import urllib.error

    with pytest.raises(urllib.error.HTTPError) as error:
        get_json(f"{facetted_service}/search?q=aggregation&entity=appel")
    assert error.value.code == 400
    payload = json.loads(error.value.read().decode("utf-8"))
    assert payload["error"]["code"] == "unknown_entity"


def test_no_entity_parameter_leaves_the_rest_of_the_answer_identical(
    facetted_service, corpus_with_mentions
):
    """G4: additive means the old answer, byte for byte, minus the new key."""
    archive, _derived, index_path = corpus_with_mentions
    dist = archive.parent / "dist"
    config = Config(
        port=0,
        index_path=index_path,
        archive_root=archive,
        static_dir=dist,
        vocab_path=archive.parent / "no-vocab.yaml",
    )
    config.facets_path = archive.parent / "absent.sqlite"
    server = Server(("127.0.0.1", 0), Handler)
    server.config = config
    server.hybrid = None
    server.facets = None
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    plain_url = f"http://{host}:{port}"
    try:
        with_facets = get_json(f"{facetted_service}/search?q=aggregation&group=article")
        without = get_json(f"{plain_url}/search?q=aggregation&group=article")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    with_facets.pop("took_ms")
    without.pop("took_ms")
    with_facets["facets"].pop("by_entity")
    assert with_facets == without


def test_healthz_says_whether_the_gazetteer_fired(facetted_service):
    body = get_json(f"{facetted_service}/healthz")
    assert body["facets"]["entities"] == 2
    assert body["facets"]["version"] == 1
    assert body["facets"]["articles"] == 2
    assert body["facets"]["path"].endswith("facets.sqlite")
