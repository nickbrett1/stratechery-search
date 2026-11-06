"""Tests for the MCP adapter: same bodies as HTTP, and no second implementation."""

from __future__ import annotations

import json

from stratechery_search import mcp_adapter


def test_tools_are_listed_without_a_nas_prefix():
    names = [tool["name"] for tool in mcp_adapter.TOOLS]
    assert names == ["search_corpus", "get_article"]
    assert not any(name.startswith("nas-") or name.startswith("nas_") for name in names)


def test_search_corpus_returns_the_http_body(service):
    result = mcp_adapter.call_tool("search_corpus", {"q": "aggregation"}, service)
    assert result["total"] >= 1
    first = result["results"][0]
    assert first["message_id"] == 152
    assert {"chunk_id", "snippet", "article_href"} <= set(first)
    assert result["query"]["raw"] == "aggregation"


def test_get_article_matches_the_endpoint(service):
    article = mcp_adapter.call_tool("get_article", {"message_id": 152}, service)
    assert article["message_id"] == 152
    assert article["text"].startswith("The aggregation theory")


def test_initialize_and_tools_list_are_mcp_shaped(service):
    initialized = mcp_adapter.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"}, service
    )
    assert initialized["result"]["protocolVersion"]
    assert initialized["result"]["capabilities"] == {"tools": {}}

    listed = mcp_adapter.handle(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, service
    )
    assert [tool["name"] for tool in listed["result"]["tools"]] == [
        "search_corpus",
        "get_article",
    ]

    call = mcp_adapter.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "search_corpus", "arguments": {"q": "aggregation"}},
        },
        service,
    )
    content = call["result"]["content"][0]
    assert content["type"] == "text"
    assert json.loads(content["text"])["total"] >= 1


def test_upstream_error_is_reported_not_swallowed(service):
    call = mcp_adapter.handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "get_article", "arguments": {"message_id": 999999}},
        },
        service,
    )
    assert call["result"]["isError"] is True
    assert "not_found" in call["result"]["content"][0]["text"]


def test_notifications_get_no_response(service):
    assert (
        mcp_adapter.handle(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}, service
        )
        is None
    )
