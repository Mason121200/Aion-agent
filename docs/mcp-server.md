# Aion Agent MCP Server（记忆层开放接口）

> 状态：可用 · 实现：`aion_agent/mcp_server.py` · 协议：MCP（Model Context Protocol），JSON-RPC 2.0 over stdio
> 核心依赖：**零**（纯标准库实现，符合本项目「核心依赖仅 numpy」的风格）

## 1. 这是什么

把 Aion Agent 的**认知记忆层**暴露为标准 MCP 工具，任何 MCP 客户端
（Claude Code / Cursor / Codex / 自研 Agent 等）都可以读写这份记忆：

- **读**：检索五维认知三元组（RAG）、笔记、活跃状态、记忆统计；
- **写**：通过 `remember` 沉淀新记忆（自动进入向量索引）；
- **资源**：`memory://cognitive`、`memory://notes` 导出全量数据（数据主权兑现）。

对应生态定位：**存储私有、交换标准** —— 记忆留在本地，交换走标准协议，
任何 Agent 引擎只要实现 MCP 即可读写同一份记忆，用户不被任何实现绑定。

## 2. 运行

```bash
# 直接运行（stdio，供 MCP 客户端拉起）
python -m aion_agent.mcp_server

# 指定数据目录（默认 $AION_DATA_DIR/server 或 ~/.aion_agent/server）
python -m aion_agent.mcp_server --data-dir /path/to/data

# 或通过 CLI
aion mcp [--data-dir PATH]
```

## 3. 工具清单

| 工具 | 说明 |
|------|------|
| `recall_context` | 返回可注入的认知上下文（triples + active states + notes），用于长期记忆注入 |
| `search_cognition` | 向量/关键词检索认知三元组（`query` / `top_k` / `user_id`） |
| `remember` | 写入认知三元组（`subject` / `predicate` / `object` / `dimension` / `confidence`） |
| `search_notes` | 笔记检索（标题/内容/标签关键词） |
| `get_active_states` | 当前活跃状态（如正在执行的任务） |
| `memory_stats` | 记忆统计：条数 / 维度分布 / 修正日志审计 |

工具定义遵循 OpenAI function calling 格式（`inputSchema`），可被 MCP 客户端自动发现。

## 4. 资源

| URI | 说明 |
|-----|------|
| `memory://cognitive` | 全部认知数据（三元组 + 状态），JSON |
| `memory://notes` | 全部笔记，JSON |

## 5. 客户端对接示例

Claude Code `~/.claude.json` 或项目 `.mcp.json`：

```json
{
  "mcpServers": {
    "aion-memory": {
      "command": "python",
      "args": ["-m", "aion_agent.mcp_server", "--data-dir", "E:/aion_agent/.local_data/server"],
      "env": { "PYTHONPATH": "E:/aion_agent" }
    }
  }
}
```

## 6. 与 benchmark 配套

记忆检索质量 / 去重 / 注入节省可量化：

```bash
aion benchmark          # 或 python -m aion_agent.benchmark
```

输出示例（HashEmbedder，离线）：

```json
{
  "recall": { "recall_at_1": 1.0, "recall_at_3": 1.0, "recall_at_5": 1.0 },
  "dedup_exact": { "attempts": 5, "unique_ids": 1 },
  "dedup_semantic": { "input": 9, "after_dedup": 6 },
  "inject_saving": { "total_triples": 21, "injected_triples": 8, "saving_ratio": 0.601 },
  "latency": { "write_avg_ms": 5.41, "retrieve_avg_ms": 0.13 }
}
```

## 7. 协议实现说明（零依赖）

- 传输：stdio，每行一个 JSON-RPC 2.0 消息（newline-delimited）；
- 握手：`initialize`（`protocolVersion: 2025-11-25`）→ `notifications/initialized`；
- 工具：`tools/list` / `tools/call`；资源：`resources/list` / `resources/read`；
- 错误：解析错误 `-32700`、方法不存在 `-32601`、参数错误 `-32602`、内部错误 `-32603`；
- 全部工具处理器复用 `InMemoryCognitiveRepo` 的异步接口，无额外状态。