#!/usr/bin/env python3
"""Reproduce the number that set ``EMBED_TIMEOUT_MS``.

Usage::

    python3 scripts/measure_embed_latency.py              # p50/p95 over 25 calls
    python3 scripts/measure_embed_latency.py --n 40       # slower, tighter
    python3 scripts/measure_embed_latency.py --probes     # also re-run the four probes

Timing is one sequential ``/v1/embeddings`` call per measurement with a fresh
connection each time — which is what the service actually does, since it is
stdlib ``urllib`` and holds no connection pool. A pooled client would report a
number the deployment never sees.

The rule this feeds (memo §0.3): ``EMBED_TIMEOUT_MS = 2 x p95``, clamped to
[800, 3000]. If this prints a p95 above 1500 ms, the clamp is the wrong shape
and the memo should be amended rather than the constant edited quietly.

Nothing here is part of the service, and it costs a few thousand tokens to run.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stratechery_search.embed import (  # noqa: E402
    TIMEOUT_CEILING_MS,
    TIMEOUT_FLOOR_MS,
    EmbedConfig,
    embed,
)

# Realistic typed queries, not a two-token probe: length changes both the token
# count and (slightly) the gateway's latency, and the number being set is the
# timeout for a *search*, so the sample should look like one.
QUERIES = [
    "aggregation theory",
    "the smiling curve",
    "why is TSMC's moat so durable",
    "commoditize your complement",
    "aggregation theory and the smiling curve",
    "what does Ben Thompson mean by integration",
    "Apple's services margin",
    "AI capex and the circular deal",
]


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * fraction
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (position - low) * (ordered[high] - ordered[low])


def measure(config: EmbedConfig, count: int) -> None:
    latencies = []
    for index in range(count):
        query = QUERIES[index % len(QUERIES)]
        started = time.perf_counter()
        embed([query], input_type="search_query", config=config)
        latencies.append((time.perf_counter() - started) * 1000)

    p50 = percentile(latencies, 0.5)
    p95 = percentile(latencies, 0.95)
    proposed = max(TIMEOUT_FLOOR_MS, min(TIMEOUT_CEILING_MS, round(2 * p95)))
    print(
        json.dumps(
            {
                "n": count,
                "p50_ms": round(p50, 1),
                "p95_ms": round(p95, 1),
                "min_ms": round(min(latencies), 1),
                "max_ms": round(max(latencies), 1),
                "rule_2x_p95_ms": round(2 * p95, 1),
                "clamped_to": proposed,
            },
            indent=2,
        )
    )


def _untyped(config: EmbedConfig, text: str) -> list[float]:
    """One raw request with no ``input_type`` — the omission being demonstrated."""
    import json as _json
    import urllib.request

    request = urllib.request.Request(
        f"{config.base_url}/v1/embeddings",
        data=_json.dumps({"model": config.model, "input": [text]}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=config.timeout_s) as response:
        payload = _json.loads(response.read())
    return payload["data"][0]["embedding"]


def probes(config: EmbedConfig) -> None:
    """The four gateway facts the build rests on (memo §0.4 / design §4.4, §4.7)."""
    text = "TSMC"

    def cosine(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=False)) / (
            (sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5)
        )

    query = embed([text], input_type="search_query", config=config)[0]
    document = embed([text], input_type="search_document", config=config)[0]
    query_again = embed([text], input_type="search_query", config=config)[0]
    # Deliberately *not* through embed(), which refuses to omit input_type: this
    # probe exists to show what the gateway does when it is omitted, and the
    # answer (identical to search_document) is the reason for that refusal.
    untyped = _untyped(config, text)
    print(
        json.dumps(
            {
                "dim": len(query),
                "l2_norm": round(sum(x * x for x in query) ** 0.5, 6),
                "search_query_vs_search_document": round(cosine(query, document), 4),
                "control_search_query_twice_identical": query == query_again,
                "no_input_type_is_search_document": untyped == document,
            },
            indent=2,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=25, help="sequential calls (default 25)")
    parser.add_argument(
        "--probes", action="store_true", help="also re-run the input_type probes"
    )
    args = parser.parse_args()

    config = EmbedConfig.from_env()
    if not config.base_url:
        print("no gateway: set EMBED_BASE_URL (or LITELLM_BASE_URL)", file=sys.stderr)
        return 2
    print(f"gateway {config.base_url} model {config.model}", file=sys.stderr)
    measure(config, args.n)
    if args.probes:
        probes(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
