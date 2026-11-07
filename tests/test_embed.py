"""The gateway client, and the two ways it could be silently wrong.

A network client is usually tested by mocking the network and asserting the
mock was called. That proves nothing about the thing that actually breaks here,
so these tests are aimed at the two silent failures instead:

1. **``input_type`` omitted.** Byte-identical to ``search_document`` (measured),
   so the corpus would look right and only queries would degrade.
2. **A timeout that fires on a slow moment.** Below ~800 ms a normal gateway
   hiccup becomes a BM25-only answer, and nobody sees an error.

Everything below runs without a network: ``embed._request`` is the one function
that touches it.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from stratechery_search import embed as embed_module
from stratechery_search.embed import (
    DEFAULT_TIMEOUT_MS,
    EMBED_DIM,
    MAX_BATCH,
    TIMEOUT_CEILING_MS,
    TIMEOUT_FLOOR_MS,
    EmbedConfig,
    EmbedError,
    embed,
)


@pytest.fixture
def sent(monkeypatch):
    """Capture the request body, and answer with correctly shaped vectors."""
    captured: list[dict] = []

    def fake_request(config, body):
        captured.append(body)
        return {
            "data": [
                {"index": i, "embedding": [0.1] * EMBED_DIM}
                for i in range(len(body["input"]))
            ]
        }

    monkeypatch.setattr(embed_module, "_request", fake_request)
    return captured


CONFIG = EmbedConfig(base_url="http://gateway:4000", api_key="k")


def test_input_type_is_required_not_defaulted():
    """The one omission that returns plausible results instead of an error."""
    with pytest.raises(TypeError):
        embed(["aggregation theory"], config=CONFIG)  # type: ignore[call-arg]


def test_input_type_reaches_the_gateway_and_is_validated(sent):
    embed(["TSMC"], input_type="search_query", config=CONFIG)
    assert sent[0]["input_type"] == "search_query"
    assert sent[0]["input"] == ["TSMC"]
    assert sent[0]["model"] == "cohere/embed-v5.0"

    with pytest.raises(EmbedError):
        embed(["TSMC"], input_type="SEARCH_QUERY", config=CONFIG)
    assert len(sent) == 1, "an invalid input_type must not reach the gateway"


def test_the_two_passes_are_distinct_requests(sent):
    embed(["aggregation theory"], input_type="search_document", config=CONFIG)
    embed(["aggregation theory"], input_type="search_query", config=CONFIG)
    assert sent[0]["input_type"] != sent[1]["input_type"]


def test_a_batch_over_the_cohere_cap_is_refused_before_the_call(sent):
    """97 answers HTTP 400 from Cohere — so it must not be sent at all."""
    with pytest.raises(EmbedError) as error:
        embed(["x"] * (MAX_BATCH + 1), input_type="search_document", config=CONFIG)
    assert error.value.code == "embed_config"
    assert sent == []
    embed(["x"] * MAX_BATCH, input_type="search_document", config=CONFIG)
    assert len(sent[0]["input"]) == MAX_BATCH


def test_vectors_come_back_in_input_order_not_response_order(monkeypatch):
    """Row alignment: a re-ordered response must not shift vectors onto texts."""
    def shuffled(config, body):
        size = len(body["input"])
        return {
            "data": [
                {"index": i, "embedding": [float(i)] * EMBED_DIM}
                for i in reversed(range(size))
            ]
        }

    monkeypatch.setattr(embed_module, "_request", shuffled)
    vectors = embed(["a", "b", "c"], input_type="search_document", config=CONFIG)
    assert [vector[0] for vector in vectors] == [0.0, 1.0, 2.0]


def test_a_narrower_vector_is_an_error_not_a_truncation(monkeypatch):
    """The schema is written for 2,048; a 1,024-dim model must not half-work."""
    monkeypatch.setattr(
        embed_module,
        "_request",
        lambda config, body: {"data": [{"index": 0, "embedding": [0.1] * 1024}]},
    )
    with pytest.raises(EmbedError) as error:
        embed(["TSMC"], input_type="search_query", config=CONFIG)
    assert "2048" in str(error.value)


def _failing_urlopen(monkeypatch, error):
    def raise_it(*args, **kwargs):
        raise error

    monkeypatch.setattr(embed_module.urllib.request, "urlopen", raise_it)


def test_gateway_down_is_a_degradable_error_not_a_validation_error(monkeypatch):
    import urllib.error

    _failing_urlopen(monkeypatch, urllib.error.URLError("connection refused"))
    with pytest.raises(EmbedError) as error:
        embed(["TSMC"], input_type="search_query", config=CONFIG)
    assert error.value.code == "embed_unreachable"


def test_a_400_is_our_bug_and_a_500_is_the_gateways(monkeypatch):
    import io
    import urllib.error

    def code_for(status):
        _failing_urlopen(
            monkeypatch,
            urllib.error.HTTPError(
                "http://gateway:4000/v1/embeddings", status, "nope", {}, io.BytesIO(b"detail")
            ),
        )
        with pytest.raises(EmbedError) as error:
            embed(["TSMC"], input_type="search_query", config=CONFIG)
        return error.value.code

    assert code_for(400) == "embed_rejected"
    assert code_for(500) == "embed_failed"


def test_timeout_is_clamped_so_it_cannot_fire_on_a_slow_moment():
    assert EmbedConfig.from_env({"EMBED_TIMEOUT_MS": "120"}).timeout_ms == TIMEOUT_FLOOR_MS
    assert EmbedConfig.from_env({"EMBED_TIMEOUT_MS": "99999"}).timeout_ms == TIMEOUT_CEILING_MS
    assert EmbedConfig.from_env({"EMBED_TIMEOUT_MS": "1500"}).timeout_ms == 1500
    assert EmbedConfig.from_env({}).timeout_ms == DEFAULT_TIMEOUT_MS


def test_the_configured_timeout_is_the_one_used(monkeypatch):
    seen = {}

    def fake_request(config, body):
        seen["timeout"] = config.timeout_s
        return {"data": [{"index": 0, "embedding": [0.1] * EMBED_DIM}]}

    monkeypatch.setattr(embed_module, "_request", fake_request)
    embed(
        ["TSMC"],
        input_type="search_query",
        config=EmbedConfig(base_url="http://gateway:4000", timeout_ms=1200),
    )
    assert seen["timeout"] == 1.2


def test_the_gateway_is_read_from_the_names_the_host_already_has():
    config = EmbedConfig.from_env(
        {"LITELLM_BASE_URL": "http://192.168.1.69:4000/", "LITELLM_API_KEY": "k"}
    )
    assert config.base_url == "http://192.168.1.69:4000"
    assert config.api_key == "k"
    # Explicit EMBED_* wins, so a host can point one service elsewhere.
    assert EmbedConfig.from_env(
        {"EMBED_BASE_URL": "http://other:4000", "LITELLM_BASE_URL": "http://x:4000"}
    ).base_url == "http://other:4000"


def test_no_gateway_is_a_configuration_error_not_a_search_error():
    with pytest.raises(EmbedError) as error:
        embed(["TSMC"], input_type="search_query", config=EmbedConfig(base_url=""))
    assert error.value.code == "embed_config"


def test_importing_the_service_does_not_import_lancedb():
    """Design §4.6: a broken Lance install must cost the flag, not the service.

    Run in a subprocess so this file's own imports cannot mask it, and asserted
    in *both* directions: absent after importing the app, and present only when
    the vector code is asked for. ``lancedb`` is not installed in every
    environment this suite runs in, so absence-of-import is the half that always
    holds; the import half is skipped when the wheel is missing.
    """
    probe = (
        "import sys;"
        "import stratechery_search, stratechery_search.server, stratechery_search.api;"
        "print('lancedb' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False", "lancedb imported at module scope"


def test_lancedb_is_pinned_exactly():
    """An exact pin, no range: the wheel wraps a Rust core whose API moves."""
    root = Path(__file__).resolve().parents[1]
    with open(root / "pyproject.toml", "rb") as handle:
        dependencies = tomllib.load(handle)["project"]["dependencies"]
    pins = [item for item in dependencies if item.startswith("lancedb")]
    assert pins == ["lancedb==0.40.0"], pins
