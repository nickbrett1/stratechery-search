"""Embeddings via the LiteLLM gateway — one model, two input types.

LanceDB stores vectors, not text (design §4.4). So the vector arm has two
halves, and they are not the same call:

    corpus (once):    29,918 passages --input_type=search_document--> store
    query  (per call): q                --input_type=search_query---> kNN

There is no path from query text to a kNN call that skips this module, and the
corpus is never re-embedded to make one possible. "Embed once" is true of the
corpus and false of queries; keeping those apart is the whole point.

**The one omission this module exists to prevent.** Measured on the live
gateway 2026-11-07: omitting ``input_type`` is *byte-identical* to sending
``search_document`` (cosine 1.000, same bytes). That inverts the danger — the
corpus pass would be accidentally correct if the field were dropped, and only
the query pass would silently degrade into comparing a document-shaped query
against document-shaped passages. It still returns plausible results, with no
error and no log line. So ``input_type`` is a required keyword argument here
rather than a default: there is no correct way to call ``embed()`` and forget it.

Everything is stdlib (``urllib``), like the rest of the service: the gateway is
one JSON POST, and an HTTP client library would be the largest dependency the
project has for the least code.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, replace

#: The alias that resolves to Cohere's ``embed-v5.0-pro`` (design §4.7). The
#: dimension is 2,048 — **not** 1,024, which is the older v3.0 size; a schema
#: written for 1,024 does not match and the mistake is invisible until search.
DEFAULT_MODEL = "cohere/embed-v5.0"
EMBED_DIM = 2048

#: Cohere's own cap, not the gateway's: 96 texts answer 200, 97 answers HTTP 400
#: from Cohere's validation ("total number of texts must be at most 96"),
#: re-measured 2026-11-07. At 96 that is ``ceil(29918/96) = 312`` requests for
#: one full corpus pass.
MAX_BATCH = 96

#: Accept the gateway's ``input_type`` values, and nothing else. A typo here
#: would be worse than an error: the API accepts unknown strings by quietly
#: treating them as the default, which is the failure this module exists to stop.
INPUT_TYPES = ("search_document", "search_query")

#: Query-embed timeout, measured rather than chosen (design §4.4, memo §0.3).
#:
#: Twenty-five sequential calls on a realistic query string, 2026-11-07, from
#: this network to the gateway: **p50 115.9 ms, p95 120.8 ms** (min 107.2, max
#: 188.4). The rule is ``2 x p95`` clamped to [800, 3000] ms, so 2 x 120.8 =
#: 242 ms is raised to the floor: a timeout of 242 ms would fire on an ordinary
#: slow moment and turn a search into a BM25-only answer for no reason. 800 ms
#: is ~6.6x the measured p95 — generous enough to absorb a cold connection or a
#: busy gateway, and bounded so that "degrade to BM25" (§4) cannot mean every
#: search waits four seconds for the gateway to give up first.
DEFAULT_TIMEOUT_MS = 800
TIMEOUT_FLOOR_MS = 800
TIMEOUT_CEILING_MS = 3000

#: Corpus-embed timeout — the same question asked about a different call, with
#: the opposite answer.
#:
#: A *query* embed that runs long degrades a search, so it is bounded tightly.
#: A *document* batch that runs long aborts one step of a resumable job, so the
#: only thing to bound is "does not hang": being slow costs a batch, being cut
#: off costs the batch it was about to finish. Measured on the live gateway,
#: 2026-11-07, one real 96-document batch, three attempts: **1.26 s / 0.96 s /
#: 1.03 s**. Rule: 4 x the slowest observation = 5,040 ms, set to 5,000 — the
#: headroom is for a colder gateway and a slower batch, not for a slow query.
#:
#: This exists because the corpus pass first inherited ``DEFAULT_TIMEOUT_MS``
#: and died on batch 1 (memo stage 2.1): 800 ms was derived from *query* p95 and
#: has no argument to make about a 96-document request.
CORPUS_TIMEOUT_MS = 5000
CORPUS_TIMEOUT_FLOOR_MS = 1000
CORPUS_TIMEOUT_CEILING_MS = 30000


class EmbedError(Exception):
    """A gateway failure the caller may degrade on, never a 503.

    ``code`` is stable and machine-readable so the search path can decide what
    to do without parsing a message: an unreachable gateway is an operational
    condition (degrade to BM25), whereas a broken response shape is not.
    """

    def __init__(self, code: str, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class EmbedConfig:
    """Where the gateway is and how long we will wait for it."""

    base_url: str
    api_key: str = ""
    model: str = DEFAULT_MODEL
    timeout_ms: int = DEFAULT_TIMEOUT_MS

    @property
    def timeout_s(self) -> float:
        return self.timeout_ms / 1000

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> EmbedConfig:
        """Read the gateway from the environment it already has.

        ``EMBED_BASE_URL``/``EMBED_API_KEY`` are the explicit names;
        ``LITELLM_BASE_URL``/``LITELLM_API_KEY`` are the ones already set on the
        host and in CI, so an unset pair does not silently mean "no gateway".
        """
        source = os.environ if env is None else env
        base_url = (
            source.get("EMBED_BASE_URL") or source.get("LITELLM_BASE_URL") or ""
        ).rstrip("/")
        api_key = source.get("EMBED_API_KEY") or source.get("LITELLM_API_KEY") or ""
        model = source.get("EMBED_MODEL") or DEFAULT_MODEL
        return cls(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_ms=_clamp_timeout(source.get("EMBED_TIMEOUT_MS")),
        )

    @classmethod
    def for_corpus(cls, env: dict[str, str] | None = None) -> EmbedConfig:
        """The same gateway, budgeted for a document batch instead of a query.

        ``EMBED_CORPUS_TIMEOUT_MS`` is deliberately *not* ``EMBED_TIMEOUT_MS``:
        inheriting the query budget is the defect this method exists to close,
        and a config that could silently fall back to 800 ms would bring it
        back the first time someone tuned the query side.
        """
        source = os.environ if env is None else env
        return replace(
            cls.from_env(source),
            timeout_ms=_clamp_corpus_timeout(source.get("EMBED_CORPUS_TIMEOUT_MS")),
        )


def _clamp_timeout(raw: str | None) -> int:
    """Bound the configured timeout, because an unclamped one fails silently.

    The clamp is the memo's, applied in code rather than trusted to an operator:
    a timeout below the floor fires on an ordinary slow moment, and one above
    the ceiling makes every degraded search wait seconds before falling back.
    """
    try:
        value = int(raw) if raw not in (None, "") else DEFAULT_TIMEOUT_MS
    except ValueError as exc:
        raise EmbedError(
            "embed_config", f"EMBED_TIMEOUT_MS is not an integer: {raw!r}"
        ) from exc
    return max(TIMEOUT_FLOOR_MS, min(TIMEOUT_CEILING_MS, value))


def _clamp_corpus_timeout(raw: str | None) -> int:
    """Bound the corpus timeout too — a hang is still a hang, just a cheaper one.

    The bounds are wide because the shape of the risk is different: a request
    that legitimately takes two seconds is an ordinary document batch, so the
    floor refuses a value that would cut one off, and the ceiling only stops a
    typo from parking the pass for an hour.
    """
    try:
        value = int(raw) if raw not in (None, "") else CORPUS_TIMEOUT_MS
    except ValueError as exc:
        raise EmbedError(
            "embed_config", f"EMBED_CORPUS_TIMEOUT_MS is not an integer: {raw!r}"
        ) from exc
    return max(CORPUS_TIMEOUT_FLOOR_MS, min(CORPUS_TIMEOUT_CEILING_MS, value))


def _request(config: EmbedConfig, body: dict) -> dict:
    """The only function here that touches the network. Patched in tests."""
    request = urllib.request.Request(
        f"{config.base_url}/v1/embeddings",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=config.timeout_s) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read()[:300].decode("utf-8", "replace")
        # A 4xx is our request being wrong (a batch over the cap, a bad model
        # name) and must not be mistaken for the gateway being down: only the
        # latter may degrade a search to BM25.
        code = "embed_rejected" if error.code < 500 else "embed_failed"
        raise EmbedError(code, f"gateway HTTP {error.code}: {detail}", error.code) from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise EmbedError("embed_unreachable", f"gateway unreachable: {error}") from error
    except json.JSONDecodeError as error:
        raise EmbedError("embed_failed", "gateway returned a non-JSON body") from error


def embed(
    texts: list[str],
    *,
    input_type: str,
    config: EmbedConfig | None = None,
) -> list[list[float]]:
    """Embed ``texts`` in one request. Returns vectors in input order.

    ``input_type`` is required and validated: see the module docstring for why
    a default would be a silent degradation rather than a convenience.
    """
    if input_type not in INPUT_TYPES:
        raise EmbedError("embed_config", f"input_type must be one of {INPUT_TYPES}")
    if not texts:
        raise EmbedError("embed_config", "nothing to embed")
    if len(texts) > MAX_BATCH:
        raise EmbedError(
            "embed_config",
            f"{len(texts)} texts exceeds the {MAX_BATCH} cap (Cohere answers 400 above it)",
        )
    if any(not (text or "").strip() for text in texts):
        raise EmbedError("embed_config", "an empty text has no embedding")

    config = config or EmbedConfig.from_env()
    if not config.base_url:
        raise EmbedError(
            "embed_config",
            "no gateway configured: set EMBED_BASE_URL (or LITELLM_BASE_URL)",
        )

    body = {"model": config.model, "input": list(texts), "input_type": input_type}
    payload = _request(config, body)

    data = payload.get("data")
    if not isinstance(data, list) or len(data) != len(texts):
        raise EmbedError(
            "embed_failed",
            f"expected {len(texts)} embeddings, got {len(data) if isinstance(data, list) else 'none'}",
        )
    # Cohere returns an explicit `index`; relying on list order instead would be
    # the row-alignment bug the memo's stage 1.3 checks by re-fetching a sha.
    ordered = sorted(data, key=lambda item: item.get("index", 0))
    vectors = [item["embedding"] for item in ordered]
    for vector in vectors:
        if len(vector) != EMBED_DIM:
            raise EmbedError(
                "embed_failed",
                f"expected {EMBED_DIM}-dim vectors, got {len(vector)} — the schema is written for {EMBED_DIM}",
            )
    return vectors
