# 学习对照：《大语言模型》第8章「复杂推理」

> 本文档把教材第8章的概念与 Aion Agent 的代码一一对应，供学习时「以代码反推理论、以理论重读代码」。

## 1. 你最初的问题：规划器 / 执行器 / 环境反馈，还是 ReAct 原型？

**结论：ReAct 是一种提示范式（理论），zero_code/Aion Agent 是它的工程化实现。**

- **ReAct 理论**：让模型交替输出「思考（Thought）/ 行动（Action）/ 观察（Observation）」
  的推理轨迹，把外部工具当作环境，用观察结果喂回推理。这是提示层面的方法，不涉及代码架构。
- **工程实现**：`use_cases/react/react_loop.py` 把 ReAct 落成「循环 + 事件」的运行时——
  **执行器**（工具注册表/执行器 `tools/`）、**环境反馈**（`observe.py` 观察 + 工具结果）、
  **反思**（`reflect.py` Reflexion）都是该循环的组成部件；认知管道是循环的「记忆器官」。
- **规划器**：MVP 阶段规划能力体现为循环内部的「步数感知 + 反思决策」——
  复杂任务规划（任务笔记、执行单元拆分）属于 zero_code 的 TaskOrchestrator，
  已列入路线图第 6 步，是本项目与完整 Agent 的下一个差距点。

## 2. 章节概念 → 代码映射

| 第8章概念 | Aion Agent 中的位置 | 一句话理解 |
|-----------|--------------------|-----------|
| 上下文学习 / 提示工程 | `use_cases/cognition_injector.py` | 把记忆拼进 System Prompt，靠提示而非微调 |
| ReAct（推理+行动交替） | `use_cases/react/react_loop.py` | Think→Act→Observe→Reflect 四段式循环运行时 |
| 上下文窗口 / 长度受限 | `use_cases/react/context_window.py` | 历史窗口 + Token 预算裁剪，保护最新消息与 system |
| Reflexion（反思纠错） | `use_cases/react/reflect.py` | 工具失败时 LLM 反思生成修正，失败回退规则式 |
| 工具使用 | `tools/`（注册表/执行器/内置工具） | get_current_time / calculator / read_file，超时熔断 |
| 环境反馈 | `use_cases/react/observe.py` | 工具结果摘要 + 错误分类 + 建议，喂回下一轮推理 |
| 记忆 / 长短期记忆 | `core/entities/` + `storage/` | 三元组=长期记忆，AgentState=工作记忆，Note=笔记本 |
| RAG（检索增强） | `storage/numpy_vector_store.py` + injector | 向量检索 + token 预算裁剪注入 |
| 状态追踪 | `core/entities/agent_state.py` | 有生命周期、可释放、可过期 |
| 置信度 / 遗忘 | `cognitive_triple.py`（confidence/expires_at） | 记忆的「权重」与「时效」 |

## 3. 认知架构设计的三个关键决策

1. **双层记忆**：大脑皮层（三元组，RAG 可检索）≠ 笔记本（Note/状态，按需读取）。
   类比教材中「工作记忆 vs 长期记忆」的区分。
2. **提取与推理分离**：LLM 输出认知块（结构化 JSON），管道用**规则**而非模型做分流，
   保证「提取质量」可测、可修。
3. **注入预算**：记忆再多也不能撑爆上下文 → 按维度分配 token 预算，超预算截断。
   教材中 RAG 的「检索后重排/裁剪」思想在此落地。

## 4. ReAct 循环的四个阶段（新）

- **Think**：`react_loop.py` 调用 `llm.stream()` 流式推理，正文/思考链/认知块
  在流上分离；无工具调用即视为任务完成（退出判定）。
- **Act**：解析流式工具调用 → `ToolExecutor.execute()` 执行 → 结果作为
  `role=tool` 消息追加，严格扩展上一轮请求（可命中 DeepSeek 前缀缓存）。
- **Observe**：`observe.py` 把工具结果压缩到 2000 字符内，失败时按错误类型给建议。
- **Reflect**：规则式快路径 + `reflect_with_llm`（Reflexion）；失败注入
  「系统提示: 修正指令」后继续下一轮。

## 5. 建议的阅读顺序

1. `core/entities/cognitive_triple.py` —— 什么是「一条认知」
2. `pipeline/markdown_parse_filter.py` —— 流式状态机如何从文本流中抓标记
3. `pipeline/dimension_split_filter.py` —— 什么值得记、什么该扔掉
4. `storage/in_memory_cognitive_repo.py` —— 记忆如何存取、去重
5. `use_cases/cognition_injector.py` —— 记忆如何回到提示里
6. `use_cases/react/context_window.py` —— 上下文窗口与 token 预算怎么算
7. `use_cases/react/react_loop.py` —— ReAct 循环运行时怎么转
8. `use_cases/react/observe.py` + `reflect.py` —— 环境反馈与反思纠错
9. `tools/builtin.py` —— 一个工具如何注册、执行、被观察
10. `python -m aion_agent demo` / `python -m aion_agent chat` —— 跑通全链路，对照输出重读上述代码