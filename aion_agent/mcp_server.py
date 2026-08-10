# -*- coding: utf-8 -*-
"""Aion Agent MCP Server —— 零依赖 stdio 实现

把 aion_agent 的认知记忆层（五维三元组 / 状态 / 笔记）暴露为标准 MCP 工具，
任何 MCP 客户端（Claude Code / Cursor / Codex 等）都可以读写这份记忆。

协议：Model Context Protocol，JSON-RPC 2.0 over stdio（每行一个 JSON 消息）。
无需安装 mcp SDK，纯标准库实现，符合本项目「核心依赖仅 numpy」的风格。

运行：
    python -m aion_agent.mcp_server [--data-dir PATH]

工具：
    recall_context     返回可注入的认知上下文（triples + active states + notes）
    search_cognition   向量/关键词检索认知三元组（RAG）
    remember           写入一条认知三元组
    search_notes       笔记检索
    get_active_states  当前活跃状态
    memory_stats       记忆统计（条数 / 维度分布 / 修正日志）

资源：
    memory://cognitive 全部认知数据（JSON）
    memory://notes     全部笔记（JSON）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from aion_agent.core.entities.agent_state import AgentState
from aion_agent.core.entities.cognitive_triple import CognitiveTriple, Dimension
from aion_agent.core.entities.note import Note
from aion_agent.llm.embedding import build_embedder
from aion_agent.storage.in_memory_cognitive_repo import InMemoryCognitiveRepo

PROTOCOL_VERSION = "2025-11-25"
SERVER_NAME = "aion-agent-memory"
SERVER_VERSION = "0.1.0"

DEFAULT_USER = "chat_user"


def _default_data_dir() -> Path:
    override = os.environ.get("AION_DATA_DIR")
    if override:
        return Path(override).expanduser() / "server"
    return Path.home() / ".aion_agent" / "server"


def _iso(dt) -> Optional[str]:
    return dt.isoformat() if dt else None


def _triple_to_dict(t: CognitiveTriple) -> dict:
    return {
        "rel_id": t.rel_id,
        "subject": t.subject,
        "predicate": t.predicate,
        "object": t.object,
        "dimension": t.dimension.value,
        "confidence": t.confidence,
        "usage_count": t.usage_count,
        "is_confirmed": t.is_confirmed_by_user,
        "created_at": _iso(t.created_at),
        "expires_at": _iso(t.expires_at),
    }


def _state_to_dict(s: AgentState) -> dict:
    return {
        "state_id": s.state_id,
        "state_type": s.state_type,
        "state_name": s.state_name,
        "description": s.description,
        "priority": s.priority,
        "expires_at": _iso(s.expires_at),
    }


def _note_to_dict(n: Note) -> dict:
    return {
        "note_id": n.note_id,
        "note_type": n.note_type.value,
        "title": n.title,
        "content": n.content,
        "summary": n.summary,
        "tags": list(n.tags),
        "created_at": _iso(n.created_at),
        "archived": n.is_archived(),
    }


# ==================== 工具定义（OpenAI function calling 风格 schema） ====================

def _tool(name: str, description: str, properties: dict, required: List[str]) -> dict:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


TOOLS: List[dict] = [
    _tool(
        "recall_context",
        "获取用户当前可注入的认知上下文：活跃三元组 + 状态 + 笔记，供 Agent 作为长期记忆注入。",
        {
            "user_id": {"type": "string", "description": "用户标识，默认 chat_user"},
        },
        [],
    ),
    _tool(
        "search_cognition",
        "检索认知记忆（五维三元组）。支持向量语义检索与关键词检索，返回按相关性排序的结果。",
        {
            "query": {"type": "string", "description": "查询语句"},
            "top_k": {"type": "integer", "description": "返回条数，默认 5"},
            "user_id": {"type": "string", "description": "用户标识，默认 chat_user"},
        },
        ["query"],
    ),
    _tool(
        "remember",
        "写入一条认知记忆三元组 (主语, 谓语, 宾语)，并自动进入向量索引。",
        {
            "subject": {"type": "string", "description": "主语，如：用户"},
            "predicate": {"type": "string", "description": "谓语，如：名字是"},
            "object": {"type": "string", "description": "宾语，如：小王"},
            "dimension": {
                "type": "string",
                "enum": ["user", "self", "env", "world", "state"],
                "description": "认知维度",
            },
            "confidence": {"type": "number", "description": "置信度 0-1，默认 0.8"},
            "user_id": {"type": "string", "description": "用户标识，默认 chat_user"},
        },
        ["subject", "predicate", "object"],
    ),
    _tool(
        "search_notes",
        "检索笔记（标题/内容/标签关键词匹配）。",
        {
            "query": {"type": "string", "description": "关键词，空则返回最近笔记"},
            "top_k": {"type": "integer", "description": "返回条数，默认 5"},
            "user_id": {"type": "string", "description": "用户标识，默认 chat_user"},
        },
        [],
    ),
    _tool(
        "get_active_states",
        "获取用户当前活跃的临时状态（如正在执行的任务）。",
        {
            "user_id": {"type": "string", "description": "用户标识，默认 chat_user"},
        },
        [],
    ),
    _tool(
        "memory_stats",
        "记忆统计：三元组条数、维度分布、状态数、笔记数、修正日志审计。",
        {
            "user_id": {"type": "string", "description": "用户标识，默认 chat_user"},
        },
        [],
    ),
]


def _resources() -> List[dict]:
    return [
        {
            "uri": "memory://cognitive",
            "name": "认知记忆（三元组 + 状态）",
            "description": "全部认知数据 JSON",
            "mimeType": "application/json",
        },
        {
            "uri": "memory://notes",
            "name": "笔记",
            "description": "全部笔记 JSON",
            "mimeType": "application/json",
        },
    ]


# ==================== 服务端实现 ====================

class McpServer:
    """标准输入输出 JSON-RPC 2.0 MCP server"""

    def __init__(self, repo: InMemoryCognitiveRepo):
        self._repo = repo
        self._initialized = False

    # ---------- 工具处理器 ----------

    async def _handle_tool(self, name: str, args: dict) -> dict:
        user_id = str(args.get("user_id") or DEFAULT_USER)
        if name == "recall_context":
            triples = []
            for dim in (Dimension.USER, Dimension.SELF, Dimension.WORLD, Dimension.ENV):
                for t in await self._repo.list_triples_by_dimension(user_id, dim):
                    triples.append(_triple_to_dict(t))
            states = await self._repo.get_active_states(user_id)
            notes = await self._repo.get_notes_for_injection(user_id, top_k=100)
            return {
                "triples": triples,
                "states": [_state_to_dict(s) for s in states],
                "notes": [_note_to_dict(n) for n in notes],
            }
        if name == "search_cognition":
            query = str(args.get("query") or "")
            top_k = int(args.get("top_k") or 5)
            results = await self._repo.retrieve(
                user_id, query=query, top_k=max(1, min(top_k, 50))
            )
            return {"results": [_triple_to_dict(t) for t in results]}
        if name == "remember":
            dimension = str(args.get("dimension") or "user")
            try:
                dim = Dimension(dimension)
            except ValueError:
                dim = Dimension.USER
            try:
                confidence = float(args.get("confidence") or 0.8)
            except (TypeError, ValueError):
                confidence = 0.8
            triple = CognitiveTriple(
                subject=str(args.get("subject") or "").strip(),
                predicate=str(args.get("predicate") or "").strip(),
                object=str(args.get("object") or "").strip(),
                dimension=dim,
                confidence=max(0.0, min(1.0, confidence)),
                user_id=user_id,
                source="mcp",
            )
            if not triple.subject or not triple.predicate or not triple.object:
                raise ValueError("subject/predicate/object 均不能为空")
            rel_id = await self._repo.save_triple(triple)
            return {"rel_id": rel_id, "saved": True}
        if name == "search_notes":
            query = str(args.get("query") or "")
            top_k = int(args.get("top_k") or 5)
            notes = await self._repo.search_notes(
                user_id, query=query, top_k=max(1, min(top_k, 50))
            )
            return {"results": [_note_to_dict(n) for n in notes]}
        if name == "get_active_states":
            states = await self._repo.get_active_states(user_id)
            return {"states": [_state_to_dict(s) for s in states]}
        if name == "memory_stats":
            triples = await self._repo.list_triples_by_dimension(user_id, Dimension.USER)
            all_triples = []
            for dim in Dimension:
                all_triples.extend(
                    await self._repo.list_triples_by_dimension(user_id, dim)
                )
            by_dim: Dict[str, int] = {}
            for t in all_triples:
                by_dim[t.dimension.value] = by_dim.get(t.dimension.value, 0) + 1
            states = await self._repo.get_active_states(user_id)
            notes = await self._repo.get_notes_for_injection(user_id, top_k=1000)
            correction = await self._repo.get_correction_stats()
            return {
                "triples": len(all_triples),
                "triples_by_dimension": by_dim,
                "active_states": len(states),
                "notes": len(notes),
                "correction": correction,
            }
        raise ValueError(f"未知工具: {name}")

    # ---------- 资源处理器 ----------

    async def _handle_resource_read(self, uri: str) -> dict:
        if uri == "memory://cognitive":
            triples = []
            for dim in Dimension:
                for t in await self._repo.list_triples_by_dimension(DEFAULT_USER, dim):
                    triples.append(_triple_to_dict(t))
            states = await self._repo.get_active_states(DEFAULT_USER)
            payload = {
                "triples": triples,
                "states": [_state_to_dict(s) for s in states],
            }
            return {"text": json.dumps(payload, ensure_ascii=False, indent=1)}
        if uri == "memory://notes":
            notes = await self._repo.get_notes_for_injection(DEFAULT_USER, top_k=1000)
            return {"text": json.dumps(
                {"notes": [_note_to_dict(n) for n in notes]},
                ensure_ascii=False, indent=1,
            )}
        raise ValueError(f"未知资源: {uri}")

    # ---------- JSON-RPC 分发 ----------

    def _error(self, req_id, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

    def _result(self, req_id, result: Any) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    async def _dispatch(self, msg: dict) -> Optional[dict]:
        req_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params") or {}

        if method == "initialize":
            self._initialized = True
            return self._result(req_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {
                    "tools": {},
                    "resources": {},
                },
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            })
        if method in ("notifications/initialized", "notifications/cancelled"):
            return None
        if method == "ping":
            return self._result(req_id, {})
        if method == "tools/list":
            return self._result(req_id, {"tools": TOOLS})
        if method == "tools/call":
            name = str(params.get("name") or "")
            arguments = params.get("arguments") or {}
            try:
                data = await self._handle_tool(name, arguments)
                return self._result(req_id, {
                    "content": [{"type": "text", "text": json.dumps(
                        data, ensure_ascii=False, indent=1,
                    )}],
                    "isError": False,
                })
            except Exception as e:  # noqa: BLE001
                return self._result(req_id, {
                    "content": [{"type": "text", "text": f"错误: {e}"}],
                    "isError": True,
                })
        if method == "resources/list":
            return self._result(req_id, {"resources": _resources()})
        if method == "resources/read":
            uri = str(params.get("uri") or "")
            try:
                content = await self._handle_resource_read(uri)
                return self._result(req_id, {"contents": [
                    {"uri": uri, "mimeType": "application/json", **content}
                ]})
            except Exception as e:  # noqa: BLE001
                return self._error(req_id, -32602, f"资源读取失败: {e}")
        if req_id is None:
            return None
        return self._error(req_id, -32601, f"Method not found: {method}")

    def serve(self) -> None:
        """阻塞式 stdio 主循环"""
        stdin = sys.stdin.buffer
        stdout = sys.stdout.buffer
        for raw in stdin:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                stdout.write((json.dumps(
                    self._error(None, -32700, "Parse error"), ensure_ascii=False
                ) + "\n").encode("utf-8"))
                stdout.flush()
                continue
            if not isinstance(msg, dict):
                continue
            try:
                response = asyncio.run(self._dispatch(msg))
            except Exception as e:  # noqa: BLE001
                response = self._error(msg.get("id"), -32603, f"Internal error: {e}")
            if response is not None:
                stdout.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
                stdout.flush()


def build_repo(data_dir: Optional[Path] = None) -> InMemoryCognitiveRepo:
    base = Path(data_dir) if data_dir else _default_data_dir()
    return InMemoryCognitiveRepo(embedder=build_embedder(), persist_dir=base)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Aion Agent MCP Server（stdio）")
    parser.add_argument(
        "--data-dir",
        help="数据目录（默认 $AION_DATA_DIR/server 或 ~/.aion_agent/server）",
    )
    args = parser.parse_args(argv)
    repo = build_repo(Path(args.data_dir) if args.data_dir else None)
    McpServer(repo).serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())