"""MCP adapter: two tools, both of them HTTP calls.

A thin client, not a second implementation. Every answer comes from the same
``/search`` and ``/article/{id}`` endpoints the UI uses, so the two surfaces
cannot disagree about what the corpus says. There is no local state, no index
handle and no cache: the service owns all of that, and this process only knows a
base URL.

Run it as a stdio MCP server::

    STRATECHERY_SEARCH_URL=http://127.0.0.1:8091 python -m stratechery_search.mcp_adapter

Naming: the tools carry no ``nas-``/``stratechery_search_`` prefix, because the
server prefix belongs to the host's tool naming rule, not to the tool.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE_URL = "http://127.0.0.1:8091"
PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "search_corpus",
        "description": (
            "Full-text search over the Stratechery archive (2016→now). Returns ranked "
            "passages with the article they belong to, plus the total hit count. "
            "Transaction mail (access codes, receipts, renewals) is not searchable."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "search terms (1–200 chars)"},
                "from": {"type": "string", "description": "earliest date, YYYY-MM-DD"},
                "to": {"type": "string", "description": "latest date, YYYY-MM-DD"},
                "kind": {
                    "type": "string",
                    "description": "one of update, daily, weekly, interview, other",
                },
                "limit": {"type": "integer", "description": "1–100, default 20"},
            },
            "required": ["q"],
        },
    },
    {
        "name": "get_article",
        "description": (
            "One article by message_id, exactly as the reader shows it: normalised "
            "text, metadata, chunk anchors and the image list (asset is null for "
            "images that were not mirrored)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"message_id": {"type": "integer"}},
            "required": ["message_id"],
        },
    },
]


class HttpError(RuntimeError):
    """The service answered with an error body, or did not answer at all."""

    def __init__(self, message: str, status: int = 0, code: str = "upstream_error") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def _get(base_url: str, path: str, params: dict | None = None) -> dict:
    url = base_url.rstrip("/") + path
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            body = json.loads(error.read())
            detail = body.get("error", {})
            raise HttpError(
                detail.get("message") or f"search service returned {error.code}",
                status=error.code,
                code=detail.get("code", "upstream_error"),
            ) from error
        except json.JSONDecodeError as decode_error:
            raise HttpError(f"search service returned {error.code}") from decode_error
    except urllib.error.URLError as error:
        raise HttpError(
            f"cannot reach the search service at {base_url}: {error.reason}",
            code="service_unreachable",
        ) from error


def search_corpus(
    base_url: str,
    q: str,
    start: str | None = None,
    end: str | None = None,
    kind: str | None = None,
    limit: int = 20,
) -> dict:
    params: dict[str, str] = {"q": q, "limit": str(limit)}
    if start:
        params["from"] = start
    if end:
        params["to"] = end
    if kind:
        params["kind"] = kind
    body = _get(base_url, "/search", params)
    return {
        "total": body["total"],
        "results": body["results"],
        "query": body["query"],
        "facets": body["facets"],
    }


def get_article(base_url: str, message_id: int) -> dict:
    return _get(base_url, f"/article/{int(message_id)}")


def call_tool(name: str, arguments: dict, base_url: str) -> dict:
    if name == "search_corpus":
        return search_corpus(
            base_url,
            q=arguments.get("q", ""),
            start=arguments.get("from"),
            end=arguments.get("to"),
            kind=arguments.get("kind"),
            limit=int(arguments.get("limit", 20)),
        )
    if name == "get_article":
        return get_article(base_url, arguments["message_id"])
    raise HttpError(f"unknown tool {name!r}", code="unknown_tool")


def _respond(request_id, result=None, error=None) -> None:
    payload = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def handle(message: dict, base_url: str) -> dict | None:
    """Handle one JSON-RPC message; return the response, or None for a notification."""
    method = message.get("method")
    request_id = message.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "stratechery-search", "version": "0.1"},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = message.get("params") or {}
        try:
            result = call_tool(params.get("name", ""), params.get("arguments") or {}, base_url)
        except HttpError as error:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": f"{error.code}: {error}"}],
                    "isError": True,
                },
            }
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                "isError": False,
            },
        }
    if request_id is None:
        return None  # a notification such as notifications/initialized
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def main() -> int:
    base_url = os.environ.get("STRATECHERY_SEARCH_URL", DEFAULT_BASE_URL)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            _respond(None, error={"code": -32700, "message": "parse error"})
            continue
        response = handle(message, base_url)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
