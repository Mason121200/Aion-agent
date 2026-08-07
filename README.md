# Aion Agent

> Aion Agent —— 完整 Agent 的精简版（MVP 是相对 zero_code 而言）。
> zero_code 是原型验证项目；Aion Agent 是正式开源项目。
> 当前已落地「ReAct 循环 + 工具执行 + 对话记忆 + 通用工具安全层 + Skill 技能体系 + 长期任务规划 + 跨设备同步」闭环。
> 目标：成为可依赖的通用 Agent 底座，以数据传输服务 + Skill 构建生态。

认知流水线（无 LLM 时可离线演示；接入 LLM 后对话自动沉淀记忆）：

```
流式文本（含 COGNITION 标记）
   │
   ▼
① MarkdownParseFilter    提取 <!--COGNITION_START/END--> 认知块（流式状态机）
② JsonParseFilter        解析 JSON + 容错修复（尾部逗号/单引号）
③ SemanticDedupFilter    精确 + 语义去重（两两余弦相似度 > 0.85）
④ DimensionSplitFilter   分流：triple / state / note / skip（含快照过滤）
⑤ StorageFilter          持久化到认知存储（去重合并）
   │
   ▼
认知存储 InMemoryCognitiveRepo
   ├─ CognitiveTriple  五维长期记忆（user/self/env/world/state）
   ├─ AgentState       临时状态（有生命周期，可释放）
   ├─ Note             长文笔记
   └─ NumpyVectorStore 纯 numpy 余弦检索（可离线，可持久化）
   │
   ▼
CognitionInjector  RAG 注入（token 预算裁剪）→ System Prompt
```

## 认知层（完整实现）

对话中沉淀的记忆不只是「存下来」，agent 还能**主动回忆**与**修正**：

- **主动回忆（4 个搜索工具）**：`search_cognition`（关键词，OR 匹配）、
  `search_by_relation`（按谓词）、`search_entity`（按实体）、
  `search_notes`（笔记检索）——ReAct 需要回忆深层记忆时自行调用。
- **认知写入**：`create_cognition` 显式写三元组，与 COGNITION 标记块互补。
- **认知修正（4 个修正工具）**：`update_cognition` / `delete_cognition`
  （软删除）/ `merge_cognition`（合并重复）/ `confirm_cognition`
  （用户确认 → 置信度 1.0）；所有修改写入**错题本**（correction_log），
  可通过 `get_correction_stats` 审计。
- **检索语义**：默认 `HashEmbedder`（离线 n-gram 哈希，零依赖演示）；
  设置 `AION_EMBEDDING=llm` 后切换为 OpenAI 兼容 `/embeddings` 接口
  （如 `text-embedding-3-small`），语义去重与向量检索变为真实语义。
- **语义去重**：`SemanticDedupFilter` 在存储前对认知条目做两两余弦去重
  （阈值 0.85），与存储层的精确去重（subject/predicate/object）互补。

## 快速开始

```bash
# ✨ 与 LLM 对话：ReAct 循环 + 记忆自动沉淀（体验模式，推荐）
#    首次使用先配置 .env（见下文「对话体验模式」）
python -m aion_agent chat

# 关闭工具调用，仅保留「对话 + 记忆」闭环
python -m aion_agent chat --no-tools

# 离线全链路演示（无 LLM 也能跑，验证管道正确性）
python -m aion_agent demo

# 从文本文件提取认知块并入库
python -m aion_agent extract <file.txt>

# 启动本地服务（Web UI / PWA，手机同局域网访问）
python -m aion_agent serve

# 运行测试
python -m pytest tests -q
```

核心依赖仅 `numpy`；LLM 客户端用标准库实现，实体层用 `dataclass`。

## 通用工具与安全层

内置 7 个通用工具，覆盖 ReAct「环境反馈」场景：

| 工具 | 说明 |
|------|------|
| `get_current_time` / `calculator` | 时间 / 数学计算（AST 白名单求值，杜绝 eval） |
| `read_file` / `file_write` / `file_list` | 本地文件读写与目录列表（限时限量） |
| `web_fetch` | 网页文本抓取（仅 http/https，限时限量） |
| `shell` | 只读命令白名单（默认需用户确认） |

安全边界（`aion_agent/security/guard.py`）：
- `PathGuard`：敏感路径黑名单（`.env` / `.pem` / `.git` / 密钥等）+ 系统目录拦截 + 可配置允许写根；
- `CommandWhitelist`：只允许只读命令（`pwd/ls/dir/echo/whoami/hostname/date`），
  危险模式（管道 / 重定向 / 递归删除 / 关机格式化）一律拒绝；
- 权限策略：`auto`（自动执行）/ `confirm`（需用户确认）/ `blocked`（禁用），
  可在 Web UI 调整并持久化到 `tool_policy.json`。

## Skill 技能体系

能力以 Skill 打包接入，**任何新能力 = 一个技能包**：

- 三级固化：`system`（框架契约，不可禁用）/ `builtin`（随框架固化，不可覆盖）/
  `skill`（可启停扩展）；
- 默认技能：`builtin`（通用工具）/ `cognition`（认知层工具）/
  `planner`（任务规划）/ `study`（学习场景）；
- 每个 Skill 可导出 manifest（`name/version/description/level/tools`），
  工具定义遵循 OpenAI function calling 格式，作为生态互操作的统一标准
  （见 `docs/sync-protocol.md` 2.8 节）。

## 长期任务与状态联动

`planner/` 提供完整任务闭环：`task_create → task_update / task_checkin → task_archive`，
支持里程碑、进度追溯、动态调整。

任务与认知层自动联动（同一数据，两处视图）：
- 创建任务 → 写入「正在执行长期任务」记忆 + 任务状态（state）；
- 停止 / 归档 / 完成 → 释放状态、记忆自动失效，不再被注入；
- 修改标题 / 截止 / 进度 → 记忆与状态同步更新；
- 检视打卡 → 追加决策日志、刷新活跃时间。

完整规划方案落盘：`plan_text`（完整方案）+ `acceptance_criteria`（验收标准）+
里程碑步骤（`steps / output / acceptance`），作为 agent 后续追踪进度的数据基础。

## 跨设备数据同步

设备间点对点直连（P2P），无中心服务器：

- 导出：`GET /api/sync/export` → 全量 Bundle（认知 / 会话 / 任务 / 学习 / 配置 6 类数据）；
- 导入 / 拉取：`POST /api/sync/import`（JSON 数据包）或 `POST /api/sync/pull`（对端地址）；
- 合并规则：按记录 id 去重、时间新者优先、消息按 id 合并；执行日志与设备标识不同步；
- Web UI 右上角「🔗 同步」：导出复制 / 局域网拉取 / JSON 导入三种方式。

协议规范见 `docs/sync-protocol.md`（数据模型、传输端点、合并规则、对接端实现清单）。

## ReAct 循环层（Think → Act → Observe → Reflect）

`use_cases/react/react_loop.py` 移植自 zero_code 的 `ReActLoop`（MVP 简化版）：

```
用户消息 → 注入记忆 → ┌─ Think（LLM 流式推理）
                       ├─ Act（解析工具调用 → 执行工具）
                       ├─ Observe（摘要结果 + 错误分类）
                       └─ Reflect（规则式 / LLM 反思）→ 继续 or 停止
```

- **Think**：流式输出实时透出，思考链（reasoning）与正文分开；
  回复中的 `<!--COGNITION-->` 块被流式状态机剥离，自动解析、分流、落库。
- **Act**：模型声明工具调用 → 内置工具执行（`get_current_time` /
  `calculator` / `read_file` + 9 个认知工具：主动搜索 / 写入 / 修正），
  结果作为 tool 消息喂回下一轮。
- **Observe**：`observe.py` 摘要工具结果（>2000 字符截断），失败时
  按错误类型（FileNotFoundError / PermissionError / TimeoutError …）给建议。
- **Reflect**：`reflect.py` 规则式快路径（无失败→继续，全成功→继续，
  有失败→纠偏注入修正指令）；`reflect_with_llm` 在失败时调用 LLM 反思
  （Reflexion 化），解析失败自动回退规则式，保证主链路稳定。

### 上下文窗口管理

`use_cases/react/context_window.py` 是教材第8章「上下文学习」的工程落地：

- **历史窗口**：`max_context_messages`（默认 20）——只保留最近 N 条消息；
- **Token 预算**：`max_tokens_budget`（默认 8000）——按估算 token 裁剪历史，
  超预算丢弃最旧的非 system 消息；全循环累计 token 达到预算即提前收尾；
- **本轮保护**：最新的用户消息永不丢弃；system（认知规则/动态上下文）永不裁剪；
- **步数感知**：每轮注入剩余步数，接近上限时提示模型尽快收尾。

事件协议（`session.react_stream()` 逐个产出 dict）：
`reasoning / token / cognition / tool_call / tool_result / reflect /
context / budget_exhausted / error / final / session`

## 对话体验模式（chat）

这是 Aion Agent 的核心体验：**你和 LLM 正常聊天，模型可以调用工具获取
环境反馈（时间/计算/读文件），重要信息自动沉淀为记忆，下一轮提问时
LLM 会「想起来」**，认知块 JSON 全程对你不可见。

1. 在项目根目录创建 `.env`（参考 `.env.example`）：

```ini
# 只需配置 API Key（Base URL 与模型已内置 DeepSeek 默认值）
AION_LLM_API_KEY=sk-xxx
# 可选覆盖：
# AION_LLM_BASE_URL=https://api.deepseek.com/v1
# AION_LLM_MODEL=deepseek-v4-flash
```

兼容 OpenAI / DeepSeek 等任意 `/chat/completions` 接口。

2. 启动：

```bash
python -m aion_agent chat
```

3. 体验一段对话：

```text
你 > 我叫小杨，喜欢中文
助手 > 你好小杨！我记住了，你偏好中文。
  🧠 记忆沉淀：+2 三元组
你 > 现在几点了？
助手 > 让我查一下。
  🛠️ 调用工具: get_current_time()
  ✅ 观察结果: 当前时间：2026年08月06日 14:30:00（Thursday）
  🤔 反思: 工具执行成功，继续下一轮推理
现在时间是 14:30。
你 > 我叫什么？
助手 > 你叫小杨（从我的记忆里查到的）。
```

每一轮 = 注入记忆 → ReAct 循环（可调用工具）→ 自动提取认知块 → 去重保存。

## 认知块协议

LLM 在回复末尾输出一个认知块，管道自动提取并处理：

```html
<!--COGNITION_START-->
[
  {"type": "triple", "subject": "小杨", "predicate": "偏好语言", "object": "中文", "dimension": "user", "confidence": 0.95},
  {"type": "triple", "subject": "学习进度", "predicate": "处于", "object": "第8章", "dimension": "state", "confidence": 0.8, "expires_in": 7},
  {"type": "state", "state_name": "学习中", "state_type": "task", "description": "阅读复杂推理", "priority": 3, "expires_in": 7},
  {"type": "note", "title": "第8章笔记", "content": "ReAct 强调推理轨迹与行动交错……", "tags": ["llm"]}
]
<!--COGNITION_END-->
```

### 五维认知分类

| 维度 | 含义 | 存储去向 |
|------|------|----------|
| `user` | 用户画像、身份、偏好 | CognitiveTriple（长期） |
| `self` | Agent 自身认知、能力清单 | CognitiveTriple（长期） |
| `env` | 环境认知（配置级） | CognitiveTriple（长期） |
| `world` | 客观知识、规则、共识 | CognitiveTriple（长期） |
| `state` | 情绪、进度、待办（有生命周期） | CognitiveTriple / AgentState（临时） |

### 分流规则（DimensionSplitFilter）

- `type=triple` + user/self/world → 长期记忆；`type=triple` + env（配置级）→ 长期记忆
- `type=triple` + env（**快照级**）→ 跳过（Git 状态、行数、测试结果等噪声，关键词 + 数值模式识别）
- `type=state` → AgentState（缺 `state_name` 跳过）
- `type=note` → Note（占位/空泛内容跳过，标题自动生成）
- `object > 200 字符` → 自动降级为 Note
- 无 `type` 旧格式 → 兼容为 triple

## 离线向量检索

MVP 内置 `HashEmbedder`（字符 n-gram 哈希 → 128 维向量，`zlib.crc32` 确定性哈希），
让 `NumpyVectorStore` 在**无 LLM / 无 embedding 模型**时也能演示完整的
「文本 → 向量 → 余弦检索」链路。生产环境替换为真实 embedding 服务即可：

```python
class MyEmbedder:
    def embed(self, text: str) -> list[float]:
        return model.encode(text)  # 例如 OpenAI text-embedding-3-small
```

## RAG 注入（token 预算裁剪）

`CognitionInjector` 按预算注入认知卡片：

- `world` 80% / `state` 10% / user+self+env 从 world 预算中匀出
- 超出预算按顺序截断；`state` 注入带「反锚定」提示（防止情绪状态抢占语义权重）
- 静态规则模板（认知提取规范）固定注入，不参与预算裁剪，可命中前缀缓存

## 与 zero_code 的关系

- **zero_code**：原型验证项目，包含完整的 Agent 循环（ReAct 循环、Reflexion 反思、
  任务编排、执行单元验证、双文档协作、Web UI），认知功能是其子集。
- **Aion Agent**：正式开源项目，重新设计为简洁、可学习、可扩展的 MVP；
  认知逻辑与 ReAct 循环均与 zero_code 一一对应，便于对照学习。

### MVP 裁剪清单

| zero_code | Aion Agent | 说明 |
|-----------|-----------|------|
| pydantic 实体 | dataclass | 减少依赖，逻辑不变 |
| SemanticDedupFilter（需 embedding） | HashEmbedder + 存储层精确去重 | 离线可用 |
| SQLite + Chroma 持久化 | JSON + numpy `.npz` 落盘（重启不丢） | 轻量可读，无第三方依赖 |
| 事件总线 + SSE | 直接返回事件字典 | 简化调用面 |
| ReActLoop（Hub + 自评 + 效率日志） | ✅ ReActLoop（核心闭环） | 裁掉 Hub/自评/指标，保留 Think→Act→Observe→Reflect |
| 上下文窗口管理 | ✅ context_window.py（历史窗口 + Token 预算） | 每轮请求严格扩展上一轮，可命中前缀缓存 |
| 工具执行（注册表/执行器/内置工具） | ✅ tools/（7 个内置工具 + 安全层） | 超时熔断 + 权限策略（auto/confirm/blocked） |
| Reflexion（LLM 反思） | ✅ reflect_with_llm | 失败时调用，解析失败回退规则式 |
| 任务编排（长期规划） | ✅ planner/（任务/里程碑/进度/动态调整） | 任务-状态-记忆联动 + 完整方案落盘 |
| 双文档协作 / 用户确认 | 暂未移植 | 后续目标 |
| 冲突解决 / 合并 / 用户确认 | 暂未移植 | 高级认知操作 |

## 应用化（桌面 + 手机，可直接安装使用）

无需上架应用商店即可分发给他人安装：

- **本地服务（Web UI / PWA）**：`python -m aion_agent serve`
  - 启动 FastAPI 服务 + 响应式界面，手机与电脑同一 Wi-Fi 下
    访问 `http://<电脑IP>:8000` 即可使用；Android/iOS 浏览器
    「添加到主屏幕」可全屏使用（PWA）。
  - 提供 SSE 流式对话、会话历史、认知记忆查看/删除。
- **桌面应用**：`python -m aion_agent app`
  - 后台启动本地服务 + pywebview 原生窗口（缺依赖时自动回退浏览器）。
- **Windows 单文件 exe**：`aion.spec` + `packaging/launcher.py`
  - `python -m PyInstaller --clean --noconfirm aion.spec`
  - 产物 `dist/aion.exe`，双击即用；无参数启动进入交互菜单
    （1 对话 / 2 演示 / 3 本地服务 / 4 退出）。
- **Android APK（侧载安装）**：`android/` 为 WebView 壳工程
  - 推送 `android/**` 后由 GitHub Actions（`.github/workflows/build-apk.yml`）
    自动构建，在 Actions 页签下载 `aion-agent-apk` 直接安装；
    打开 App 点右上角「服务器」输入电脑局域网地址即可连接。
- **数据目录**：默认 `~/.aion_agent`（可用 `AION_DATA_DIR` 覆盖）。
- **LLM 配置发现**：服务/应用启动时自动从「exe 同目录 / 当前目录 / `~/.aion_agent`」
  三处查找 `.env`（`AION_LLM_API_KEY` 必填；可选 `AION_LLM_BASE_URL` / `AION_LLM_MODEL`）。
- **会话管理**：Web UI 顶栏可新建/切换/删除会话，历史与记忆按用户持久化。

### 打包产物与安全策略（重要）

- 重新打包：`python -m PyInstaller --clean --noconfirm aion.spec`，产物 `dist/aion.exe`。
- exe 未做代码签名。若目标电脑开启了 **Smart App Control（Windows 11 新机默认强制）**，
  首次运行未签名新 exe 会被拦截（报「应用程序控制策略已阻止此文件」）。
  处理方式：
  1. 关闭 Smart App Control（设置 → 隐私和安全性 → Windows 安全中心 →
     应用和浏览器控制 → Smart App Control → 关闭；注意关闭不可逆）；
  2. 或购买代码签名证书对 exe 签名后再分发；
  3. 或直接源码运行（`python -m aion_agent app`），不受影响。

## 项目结构

```
aion_agent/
├── core/                # 实体与端口（Message / ToolCall / 认知实体 / 端口接口）
├── pipeline/            # 管道-过滤器：提取 → 解析 → 分流 → 存储
├── planner/             # 长期任务规划（JsonPlanRepo：任务/里程碑/决策日志）
├── security/            # 安全守卫（PathGuard / CommandWhitelist）
├── skills/              # Skill 技能体系（注册 / 启停 / 目录 / manifest）
├── storage/             # InMemoryCognitiveRepo / JsonChatRepo / NumpyVectorStore
├── study/               # 学习场景（计划 / 资料 / 记录 / 提醒）
├── sync/                # 跨设备同步（Bundle 导出 / 合并 / 拉取）
├── llm/                 # OpenAI 兼容客户端（stdlib，流式/非流式/工具调用）
├── server/              # FastAPI 服务 + Web UI（PWA）+ 同步 API
├── tools/               # 工具注册表 / 执行器 / 内置工具（含安全守卫）
└── use_cases/
    ├── react/           # ReAct 循环：react_loop / observe / reflect / verify / context_window
    ├── react_chat_session.py  # ReAct 对话会话（记忆注入 + 窗口管理）
    └── cognition_*.py   # 认知用例（旧 chat 兼容）
android/                 # Android WebView 壳（GitHub Actions 云构建 APK）
examples/sample_cognition.txt  # 练习用认知块样例
tests/                   # 单元测试
docs/learning_map.md     # 教材第8章「复杂推理」学习对照
```

## 路线图

1. ✅ LLM 对话 + 认知自动沉淀（chat 模式，OpenAI 兼容）
2. ✅ 记忆持久化（cognitive.json + 向量索引，重启不丢）
3. ✅ ReAct 循环层（观察 → 思考 → 行动 → 认知归档）
4. ✅ 工具执行 + 环境反馈（内置时间/计算/读文件，超时熔断）
5. ✅ Reflexion 反思闭环（失败时 LLM 反思，回退规则式）
6. ✅ 任务编排 / 长期规划（planner：任务/里程碑/进度/动态调整 + 认知联动）
7. ✅ Skill 技能体系（三级固化 + manifest 可分发）
8. ✅ 跨设备同步（Bundle 协议：导出/导入/拉取，见 docs/sync-protocol.md）
9. ✅ 应用化：本地服务（Web UI/PWA） + 桌面壳 + Android APK 云构建
10. ⏳ 认知冲突解决 / 合并 / 用户确认（高级认知操作）
11. ⏳ 同步 v2：增量 diff / 认证加密 / 数据签名
12. ⏳ 记忆层 MCP server（让任何 agent 可读写记忆）

## License

[Apache-2.0](./LICENSE)