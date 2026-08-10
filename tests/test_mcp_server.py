# -*- coding: utf-8 -*-
"""MCP Server 协议级测试：initialize / tools / resources / 错误处理"""
import asyncio
import json

import pytest

from aion_agent.mcp_server import McpServer, build_repo
from aion_agent.storage.in_memory_cognitive_repo import InMemoryCognitiveRepo
from aion_agent.llm.embedding import build_embedder


@pytest.fixture()
def server(tmp_path) -> McpServer:
    repo = InMemoryCognitiveRepo(embedder=build_embedder(), persist_dir=tmp_path)
    return McpServer(repo)


def _call(server: McpServer, method: str, params=None, req_id=1):
    msg = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        msg["params"] = params
    return asyncio.run(server._dispatch(msg))


def test_initialize_handshake(server):
    resp = _call(server, "initialize", {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0"},
    })
    result = resp["result"]
    assert result["serverInfo"]["name"] == "aion-agent-memory"
    assert result["protocolVersion"] == "2025-11-25"
    assert "tools" in result["capabilities"]


def test_ping(server):
    resp = _call(server, "ping")
    assert resp["result"] == {}


def test_tools_list(server):
    resp = _call(server, "tools/list")
    names = {t["name"] for t in resp["result"]["tools"]}
    assert {
        "recall_context", "search_cognition", "remember",
        "search_notes", "get_active_states", "memory_stats",
    } <= names


def test_remember_and_search(server):
    r1 = _call(server, "tools/call", {
        "name": "remember",
        "arguments": {"subject": "用户", "predicate": "名字是", "object": "小王"},
    })
    payload = json.loads(r1["result"]["content"][0]["text"])
    assert payload.get("saved") is True
    assert not r1["result"]["isError"]

    r2 = _call(server, "tools/call", {
        "name": "search_cognition",
        "arguments": {"query": "小王", "top_k": 5},
    })
    results = json.loads(r2["result"]["content"][0]["text"])["results"]
    assert len(results) >= 1
    assert results[0]["object"] == "小王"


def test_recall_context_and_stats(server):
    _call(server, "tools/call", {
        "name": "remember",
        "arguments": {"subject": "用户", "predicate": "城市是", "object": "深圳"},
    })
    ctx = json.loads(_call(server, "tools/call", {
        "name": "recall_context", "arguments": {},
    })["result"]["content"][0]["text"])
    assert len(ctx["triples"]) >= 1

    stats = json.loads(_call(server, "tools/call", {
        "name": "memory_stats", "arguments": {},
    })["result"]["content"][0]["text"])
    assert stats["triples"] >= 1


def test_tool_call_validation_error(server):
    resp = _call(server, "tools/call", {
        "name": "remember",
        "arguments": {"subject": "", "predicate": "", "object": ""},
    })
    assert resp["result"]["isError"] is True


def test_resources_list_and_read(server):
    resp = _call(server, "resources/list")
    uris = [r["uri"] for r in resp["result"]["resources"]]
    assert "memory://cognitive" in uris and "memory://notes" in uris

    read = _call(server, "resources/read", {"uri": "memory://cognitive"})
    contents = read["result"]["contents"]
    assert contents[0]["uri"] == "memory://cognitive"
    assert contents[0]["text"]


def test_method_not_found(server):
    resp = _call(server, "no/such", {}, req_id=99)
    assert resp["error"]["code"] == -32601
    assert resp["id"] == 99