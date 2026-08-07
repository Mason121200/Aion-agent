# Aion Agent 数据同步协议 v1（草案）

> 状态：草案 · 当前实现版本 `schema: "1.0"` · 参考实现：`aion_agent/sync/bundle.py`

## 1. 目标与原则

本协议定义 Aion Agent 的**数据交换格式**与**传输方式**，用于：
- 跨设备（电脑 / 手机 App / 未来任何客户端）同步认知、会话、任务、学习等数据；
- 让数据归用户所有：**随时可导出、可带走、可迁移**，用户不被任何单端实现绑定；
- 让任何实现只要遵守本协议即可互操作，构建 skill 生态。

核心原则：
- **存储私有、交换标准**：各端可以使用自己的存储引擎（JSON / SQLite / 内存），
  但对外交换一律使用本协议的 Bundle 格式；
- **主数据与派生物分离**：认知向量索引、日志等属于派生数据，不参与同步，可重建；
- **合并幂等**：同一 Bundle 重复导入结果一致，不产生重复数据。

## 2. 数据模型

### 2.1 Bundle 顶层结构

```json
{
  "schema": "1.0",
  "device_id": "device_xxxxxxxx",
  "exported_at": "2026-08-07T17:00:00",
  "files": {
    "cognitive.json": { ... },
    "chat.json": { ... },
    "plans.json": { ... },
    "study.json": { ... },
    "skills.json": { ... },
    "tool_policy.json": { ... }
  }
}
```

| 字段 | 说明 |
|---|---|
| `schema` | 协议版本，当前 `"1.0"`，用于未来兼容判断 |
| `device_id` | 导出设备的唯一标识（首次生成后持久化在 `device.json`） |
| `exported_at` | 导出时间（ISO 8601） |
| `files` | 参与同步的数据文件快照，键为文件名 |

不参与同步的数据：执行日志（`execution_log.jsonl`）、设备标识（`device.json`）、
向量索引（`embeddings.npz` / `ids.json` / `metadatas.json` / `documents.json`）。

### 2.2 cognitive.json —— 认知记忆（大脑皮层）

```json
{
  "saved_at": "2026-08-07T17:00:00",
  "triples": [ { "rel_id": "rel_xxxxxxxx", "subject": "用户", "predicate": "名字是",
                 "object": "小王", "dimension": "user", "user_id": "chat_user",
                 "confidence": 0.95, "usage_count": 0, "is_active": true,
                 "is_confirmed_by_user": false, "source": "chat",
                 "created_at": "...", "updated_at": null, "expires_at": null } ],
  "states": [ { "state_id": "state_xxxxxxxx", "user_id": "chat_user",
                "state_type": "user", "state_name": "task_plan",
                "description": "长期任务：...", "is_active": true,
                "priority": 5, "expires_at": null } ],
  "notes": [ { "note_id": "note_xxxxxxxx", "note_type": "task", "title": "...",
               "content": "...", "created_at": "..." } ],
  "correction_log": [ { "operation": "delete", "rel_id": "rel_xxxxxxxx",
                        "detail": {}, "created_at": "..." } ]
}
```

- `dimension` 取值：`user | self | env | world | state`（五维认知）。
- `is_active=false` 表示已失效（停止的任务、被修正的记忆），不参与注入与检索。
- `correction_log` 是审计/错题本，只追加。

### 2.3 chat.json —— 会话历史

```json
{
  "session_xxxxxxxx": {
    "user_id": "chat_user",
    "created_at": "...",
    "messages": [
      { "id": "msg_xxxxxxxx", "session_id": "session_xxxxxxxx", "role": "user",
        "content": "你好", "reasoning": null, "tool_call_id": null,
        "created_at": "2026-08-07T14:24:10" }
    ]
  }
}
```

`role` 取值：`user | assistant | tool | system`。

### 2.4 plans.json —— 长期任务

```json
{ "plans": {
  "task_xxxxxxxx": {
    "plan_id": "task_xxxxxxxx", "title": "三个月完成项目上线",
    "goal": "...", "why": "...", "tags": [], "start_date": "...",
    "end_date": "...", "daily_minutes": 0, "priority": "normal",
    "status": "active", "progress": 0, "current_status": "...",
    "next_steps": [],
    "plan_text": "完整规划方案（Markdown）",
    "acceptance_criteria": ["功能完整", "通过验收测试"],
    "milestones": [
      { "milestone_id": "ms_xxxxxxxx", "title": "阶段一", "due_date": "...",
        "steps": [], "output": "", "acceptance": "", "done": false,
        "done_at": null }
    ],
    "decision_log": [ { "ts": "...", "text": "创建任务" } ],
    "state_id": "state_xxxxxxxx", "rel_id": "rel_xxxxxxxx",
    "created_at": "...", "updated_at": "..."
  }
}}
```

- `status` 取值：`active | paused | completed | archived`。
- `state_id` / `rel_id` 是任务与认知层（状态、记忆）的关联键，实现方应保持联动。

### 2.5 study.json —— 学习计划

```json
{
  "plans": { "plan_xxxxxxxx": { "plan_id": "...", "title": "...", "subject": "...",
                                "goal": "...", "why": "", "cadence": "...",
                                "status": "active", "progress": 0,
                                "milestones": [], "decision_log": [] } },
  "materials": [],
  "sessions": [],
  "reminders": []
}
```

### 2.6 skills.json —— 技能启停状态

```json
{ "study": true, "planner": true }
```

键为技能名，值为是否启用。仅同步**启停状态**；技能包本体见 2.8。

### 2.7 tool_policy.json —— 工具权限

```json
{ "blocked": [], "confirm": [] }
```

- `blocked`：禁止执行的工具名列表；
- `confirm`：需要用户确认后才执行的工具名列表。

### 2.8 Skill Manifest —— 技能可分发格式

Skill 是生态的能力单元。每个 Skill 可导出为一份 manifest：

```json
{
  "manifest_version": "1.0",
  "name": "study",
  "version": "1.0.0",
  "description": "学习场景：学习计划 / 资料 / 学习记录 / 提醒",
  "level": "skill",
  "tools": ["plan_create", "plan_list", "add_study_material", "..."]
}
```

- `level` 取值：`system`（框架契约，不可禁用）/ `builtin`（随框架固化）/ `skill`（可启停扩展）。
- `tools` 为技能注册的工具名列表，工具定义遵循 OpenAI function calling 格式
  （`type=function, name, description, parameters`）—— 该格式即为生态的工具标准。
- 未来 v2：`entry`（打包入口）、`author` / `license` / `dependencies`，支持第三方 skill 分发。

## 3. 传输协议（HTTP）

所有端点基于 HTTP JSON。实现方可自行选择 Web 框架，但路径与语义保持一致。

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| GET | `/api/sync/export` | - | Bundle（见 2.1） |
| POST | `/api/sync/import` | `{"bundle": Bundle}` | `{"merged": {统计}}` |
| POST | `/api/sync/pull` | `{"url": "http://host:port"}` | `{"merged": {统计}}` |
| GET | `/api/sync/status` | - | `{"device_id": "...", "files": {name: size}}` |

### 3.1 拉取（pull）规范

- 请求体 `url` 为对端地址，**必须是 `http://` 或 `https://` 开头**；
- 实现方自动补全路径：若地址不以 `/api/sync/export` 结尾，则追加；
- 请求对端时携带 `User-Agent: AionAgent-Sync/1.0`；
- 对端返回必须是含 `files` 字段的 JSON，否则视为无效数据包。

### 3.2 错误处理

- 非法地址（非 http/https）→ HTTP 400，detail 说明原因；
- 对端不可达 / 返回异常 → HTTP 400，detail 以「拉取失败: ...」开头；
- 导入缺少 `bundle` 参数 → HTTP 400。

## 4. 合并规则

导入 / 拉取后，按文件类型执行幂等合并：

### 4.1 字典文件（chat / plans / study / skills / tool_policy）

按**顶层键**（session_id / plan_id / skill 名等）合并：
- 本地不存在的键 → 直接新增；
- 双方都有且值为字典 → 比较 `updated_at` / `created_at`，**新者覆盖**；
- 双方都有且值为列表（如消息）→ 见 4.2；
- 简单值（无时间戳）→ 远端覆盖本地。

### 4.2 消息列表（chat）

按消息 `id` 去重，保留时间顺序（按 `created_at` 升序）。

### 4.3 认知列表（cognitive）

各 section 按 id 字段去重追加：
- `triples` → `rel_id`；
- `states` → `state_id`；
- `notes` → `note_id`；
- `correction_log` → 无稳定 id，按 `(content, ts)` 去重（仅追加）。

### 4.4 冲突策略

**时间新者优先**：以记录的 `updated_at`（无则 `created_at`）比较，新的覆盖旧的。
本协议 v1 不提供字段级合并或三方冲突检测，属于 v2 演进方向。

## 5. 版本与扩展策略

- v1 冻结现有字段；新增字段**只允许向后兼容**（增加可选字段，不改语义）；
- 实现方读取 `schema` 字段：不认识的更高版本应拒绝导入并提示升级；
- v2 规划（不改变 v1 数据语义）：
  - 增量同步（按记录 diff，而非全量 Bundle）；
  - 同步令牌认证 + 传输加密；
  - 数据签名（防篡改）与跨设备用户身份。

## 6. 对接端实现清单

一个最小对接端需要实现：
1. 6 类数据模型（见第 2 节）的读写；
2. `GET /api/sync/export` 与 `POST /api/sync/import`（或本地文件导入）；
3. 第 4 节的合并规则；
4. Skill manifest 的读写（第 2.8 节）。

## 7. 附：当前实现参考

- `aion_agent/sync/bundle.py`：Bundle 构建 / 合并 / 拉取
- `aion_agent/server/app.py`：HTTP 端点
- `aion_agent/skills/base.py`：Skill 与 manifest
