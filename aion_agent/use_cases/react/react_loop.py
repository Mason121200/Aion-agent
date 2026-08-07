"""ReActLoop —— 核心推理循环（Think → Act → Observe → Reflect）

移植自 zero_code 的 src/use_cases/react/react_loop.py，MVP 简化：
- 去掉 Hub / 事件总线 / 自评（self_eval）/ 效率日志 / TaskContext 编排
- 保留核心闭环：思考（流式）→ 行动（工具调用）→ 观察（Observe）→ 反思（Reflect）
- 认知块处理委托给 CognitionPipeline（feed/flush 剥离标记 + process_block 落库）
- 新增真正的「上下文窗口管理」：
    * max_context_messages：历史窗口裁剪
    * max_tokens_budget：全循环 token 预算（超预算提前收尾）
    * 每轮注入剩余步数感知

事件协议（异步生成器逐个产出 dict）：
    reasoning / token / cognition / context / tool_call / tool_result /
    reflect / budget_exhausted / error / final / session
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from aion_agent.core.entities.message import Message
from aion_agent.core.ports.i_llm_client import ILLMClient
from aion_agent.core.ports.i_tool_executor import IToolExecutor, ToolResult
from aion_agent.core.ports.i_tool_registry import IToolRegistry
from aion_agent.use_cases.react.context_window import (
    estimate_tokens,
    trim_history,
    trim_messages_by_tokens,
)
from aion_agent.use_cases.react.observe import observe
from aion_agent.use_cases.react.reflect import reflect, reflect_with_llm
from aion_agent.use_cases.react.verify import format_correction, verify_with_llm

logger = logging.getLogger(__name__)


class PipelineSplitter:
    """把 CognitionPipeline 的 markdown 状态机包装成 ReActLoop 需要的接口

    约定：feed(chunk) -> (visible_text, cognition_block_or_None)；
    flush() -> (visible_text, cognition_block_or_None)。
    """

    def __init__(self, pipeline):
        self._pipeline = pipeline

    def reset(self) -> None:
        self._pipeline.reset_markdown()

    def feed(self, chunk: str):
        return self._pipeline.feed_markdown(chunk)

    def flush(self):
        return self._pipeline.flush_markdown()


class ReActLoop:
    """ReAct 循环执行器（纯逻辑，不包含持久化状态）"""

    def __init__(
        self,
        llm_client: ILLMClient,
        history: List[Message],
        user_id: str,
        session_id: str,
        system_prompt: str,
        dynamic_context: Optional[str] = None,
        pipeline=None,
        tool_registry: Optional[IToolRegistry] = None,
        tool_executor: Optional[IToolExecutor] = None,
        max_steps: int = 8,
        max_tokens_budget: int = 8000,
        max_context_messages: int = 20,
        tool_timeout_seconds: int = 30,
        llm_reflect_enabled: bool = True,
        verify_enabled: bool = True,
        execution_log=None,
    ):
        self._llm = llm_client
        self._user_id = user_id
        self._session_id = session_id
        self._system_prompt = system_prompt
        self._dynamic_context = dynamic_context
        self._history = history or []
        self._pipeline = pipeline
        self._splitter = PipelineSplitter(pipeline) if pipeline is not None else None
        self._tool_registry = tool_registry
        self._tool_executor = tool_executor
        self._tools = tool_registry.list_tools() if tool_registry is not None else []
        self._max_steps = max_steps
        self._max_tokens_budget = max_tokens_budget
        self._max_context_messages = max_context_messages
        self._tool_timeout_seconds = tool_timeout_seconds
        self._llm_reflect_enabled = llm_reflect_enabled
        self._verify_enabled = verify_enabled
        self._execution_log = execution_log
        self._all_tool_results: List[Dict[str, Any]] = []

        self._step_count = 0
        self._total_tokens = 0
        self._total_latency_ms = 0.0

    # ==================== 主循环 ====================

    async def run(self) -> AsyncGenerator[Dict[str, Any], None]:
        """执行 ReAct 循环，产出事件流"""
        history = trim_history(self._history, self._max_context_messages)

        # ---- 初始化消息：system + 历史 + 动态上下文 + 高亮指令 ----
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt},
        ]
        for msg in history:
            if msg is None:
                continue
            messages.append({"role": msg.role, "content": msg.content})

        if self._dynamic_context:
            messages.append({
                "role": "system",
                "content": f"【动态上下文】\n{self._dynamic_context}",
            })

        if history and history[-1] and history[-1].role == "user":
            recent_msg = history[-1].content
            truncated = recent_msg[:200] + ("..." if len(recent_msg) > 200 else "")
            messages.append({
                "role": "system",
                "content": (
                    f"⚠️ 以上对话历史仅供参考。用户最新消息是：「{truncated}」"
                    f"请直接回应这条消息，不要延续历史中的分析话题，"
                    f"除非用户明确要求继续。"
                ),
            })

        # ---- 上下文窗口：按 token 预算裁剪历史（保护最新用户消息） ----
        prompt_budget = max(self._max_tokens_budget // 2, 3000)
        messages, dropped = trim_messages_by_tokens(
            messages, budget=prompt_budget, protected_tail=1,
        )
        if dropped:
            yield {
                "type": "context",
                "note": f"上下文窗口裁剪：丢弃 {dropped} 条历史消息（token 预算保护）",
            }

        if self._splitter is not None:
            self._splitter.reset()

        full_visible = ""
        full_reasoning = ""
        final_content = ""
        cognition_totals: Dict[str, int] = {
            "triples": 0, "states": 0, "notes": 0, "skipped": 0, "total": 0,
        }
        cognition_records: List[str] = []
        loop_exhausted = False

        # ---- 主循环 ----
        for turn in range(self._max_steps):
            self._step_count += 1
            logger.info(f"[ReAct] 轮次 {self._step_count}/{self._max_steps}")

            # 预算检查：累计 token 已超预算 → 提前收尾
            if self._total_tokens >= self._max_tokens_budget:
                yield {
                    "type": "budget_exhausted",
                    "note": (
                        f"token 预算已用尽（{self._total_tokens}/"
                        f"{self._max_tokens_budget}），提前结束"
                    ),
                }
                break

            # 剩余步数感知
            self._inject_step_awareness(messages, turn)

            assistant_content = ""
            current_tool_calls: List[Dict[str, Any]] = []
            step_tokens = 0
            step_start_ts = time.monotonic()

            # ---- Think（流式） ----
            try:
                async for chunk in self._llm.stream(
                    messages=messages,
                    tools=self._tools or [],
                    temperature=0.7,
                    max_tokens=4096,
                ):
                    if chunk.reasoning:
                        full_reasoning += chunk.reasoning
                        yield {"type": "reasoning", "content": chunk.reasoning}
                        if self._splitter is not None:
                            try:
                                _, cog = self._splitter.feed(chunk.reasoning)
                            except Exception as e:
                                logger.error(f"[ReAct] 认知分割(推理)异常: {e}")
                                cog = None
                            if cog:
                                summary = await self._handle_cognition_block(cog)
                                if summary:
                                    self._merge_totals(cognition_totals, summary)
                                    cognition_records.extend(summary.get("records", []))
                                    yield {"type": "cognition", **summary}

                    if chunk.content:
                        try:
                            visible, cog = self._splitter.feed(chunk.content)
                        except Exception as e:
                            logger.error(f"[ReAct] 认知分割(内容)异常: {e}")
                            visible, cog = chunk.content, None
                        if visible:
                            full_visible += visible
                            assistant_content += visible
                            yield {"type": "token", "content": visible}
                        if cog:
                            summary = await self._handle_cognition_block(cog)
                            if summary:
                                self._merge_totals(cognition_totals, summary)
                                cognition_records.extend(summary.get("records", []))
                                yield {"type": "cognition", **summary}

                    if chunk.tool_calls:
                        current_tool_calls.extend(chunk.tool_calls)

                    if chunk.is_final:
                        if chunk.usage and isinstance(chunk.usage, dict):
                            # 预算按本轮新增输出 token 计，输入上下文不计入
                            # （避免大上下文把预算快速耗尽，打断多步工具流程）
                            step_tokens = int(
                                chunk.usage.get("completion_tokens")
                                or chunk.usage.get("total_tokens", 0)
                                or 0
                            )
                            self._total_tokens += step_tokens
                            if self._execution_log is not None:
                                self._execution_log.append(
                                    session_id=self._session_id,
                                    event_type="llm_call",
                                    content=(full_visible or assistant_content)[-300:],
                                    meta={
                                        "turn": self._step_count,
                                        "completion_tokens": step_tokens,
                                        "total_tokens": self._total_tokens,
                                        "tool_calls": len(current_tool_calls),
                                    },
                                )
                        break
            except Exception as e:
                logger.error(f"[ReAct] LLM 调用失败: {e}", exc_info=True)
                yield {"type": "error", "error": f"LLM 调用失败: {str(e)}"}
                return
            finally:
                self._total_latency_ms += (
                    time.monotonic() - step_start_ts
                ) * 1000.0

            # 冲刷认知状态机残留
            if self._splitter is not None:
                try:
                    final_visible, final_cog = self._splitter.flush()
                except Exception as e:
                    logger.error(f"[ReAct] flush 异常: {e}")
                    final_visible, final_cog = "", None
                if final_visible:
                    full_visible += final_visible
                    assistant_content += final_visible
                    yield {"type": "token", "content": final_visible}
                if final_cog:
                    summary = await self._handle_cognition_block(final_cog)
                    if summary:
                        self._merge_totals(cognition_totals, summary)
                        cognition_records.extend(summary.get("records", []))
                        yield {"type": "cognition", **summary}

            # ---- 无工具调用 → 完成 ----
            if not current_tool_calls:
                final_content = full_visible or assistant_content
                messages.append({"role": "assistant", "content": final_content})
                break

            # ---- Act：保存 assistant 消息（含工具调用）并执行 ----
            messages.append({
                "role": "assistant",
                "content": assistant_content or "",
                "tool_calls": [
                    self._normalize_tool_call(tc) for tc in current_tool_calls
                ],
            })

            tool_results_for_turn: List[Dict[str, Any]] = []
            for tc in current_tool_calls:
                func = tc.get("function") or {}
                tool_name = func.get("name", "")
                tool_args = func.get("arguments", {}) or {}
                tool_call_id = tc.get("id") or (
                    f"call_{self._session_id}_{self._step_count}"
                )

                if not tool_name:
                    continue

                yield {"type": "tool_call", "name": tool_name, "args": tool_args}

                exec_result = await self._execute_tool(tool_name, tool_args)
                obs = observe(exec_result)

                yield {
                    "type": "tool_result",
                    "tool_call": {
                        "id": tool_call_id,
                        "name": tool_name,
                        "success": exec_result.success,
                        "data": exec_result.data if exec_result.success else None,
                        "error": exec_result.error if not exec_result.success else None,
                    },
                }

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": obs["content"],
                })
                tool_results_for_turn.append({
                    "tool_call_id": tool_call_id,
                    "content": obs["content"],
                    "success": exec_result.success,
                    "error": exec_result.error if not exec_result.success else None,
                })
                self._all_tool_results.append(tool_results_for_turn[-1])
                if self._execution_log is not None:
                    self._execution_log.append(
                        session_id=self._session_id,
                        event_type="tool_call",
                        content=tool_name,
                        meta={"args": tool_args, "tool_call_id": tool_call_id},
                    )
                    self._execution_log.append(
                        session_id=self._session_id,
                        event_type="tool_result",
                        content=(
                            obs["content"] if exec_result.success
                            else str(exec_result.error)
                        )[:500],
                        meta={
                            "tool": tool_name,
                            "success": exec_result.success,
                            "tool_call_id": tool_call_id,
                        },
                    )

            # ---- Reflect ----
            if self._llm_reflect_enabled:
                try:
                    reflect_result = await reflect_with_llm(
                        self._llm, tool_results_for_turn, turn, messages,
                    )
                except Exception as e:
                    logger.warning(f"[ReAct] LLM 反思异常，回退规则式: {e}")
                    reflect_result = reflect(tool_results_for_turn, turn)
            else:
                reflect_result = reflect(tool_results_for_turn, turn)

            if reflect_result.get("reflected"):
                yield {
                    "type": "reflect",
                    "action": reflect_result["action"],
                    "reason": reflect_result.get("reason", ""),
                    "correction": reflect_result.get("correction", ""),
                }
                if self._execution_log is not None:
                    self._execution_log.append(
                        session_id=self._session_id,
                        event_type="reflect",
                        content=reflect_result.get("reason", ""),
                        meta={"action": reflect_result["action"]},
                    )

            action = reflect_result["action"]
            if action == "stop":
                logger.info(f"[ReAct] ✅ 任务完成: {reflect_result.get('reason', '')}")
                final_content = full_visible
                break
            if action == "continue":
                logger.info(f"[ReAct] 🔄 继续推理: {reflect_result.get('reason', '')}")
                continue
            if action == "fallback":
                logger.info(f"[ReAct] 🔧 纠偏: {reflect_result.get('reason', '')}")
                correction = reflect_result.get("correction", "")
                if correction:
                    messages.append({
                        "role": "user",
                        "content": f"系统提示: {correction}\n请根据以上信息调整下一步操作。",
                    })
                continue

        # ---- 循环耗尽兜底 ----
        else:
            loop_exhausted = True

        if not final_content:
            if full_visible:
                final_content = (
                    "（已达到最大步数，以下为执行过程中已产生的进展）\n\n"
                    + full_visible
                    + "\n\n---\n本次任务在步数上限内未能完全完成。"
                    "如需继续执行，请直接告诉我。"
                )
            else:
                final_content = (
                    "（已达到最大步数，未产生可用的中间结果，任务未能完成。"
                    "如需继续，请告诉我。）"
                )

        if (
            self._verify_enabled
            and self._llm_reflect_enabled
            and self._all_tool_results
            and self._total_tokens < self._max_tokens_budget
        ):
            try:
                verify_result = await verify_with_llm(
                    self._llm,
                    tool_results=self._all_tool_results,
                    final_reply=final_content,
                    turn=max(self._step_count - 1, 0),
                )
                if not verify_result.get("verified") and verify_result.get("correction"):
                    correction_text = format_correction(
                        "", verify_result["correction"]
                    )
                    final_content = format_correction(
                        final_content, verify_result["correction"]
                    )
                    # 更正也作为 token 流出，确保 UI 可见且写入持久化回复
                    yield {"type": "token", "content": correction_text}
                if self._execution_log is not None:
                    self._execution_log.append(
                        session_id=self._session_id,
                        event_type="verify",
                        content=verify_result.get("issues", ""),
                        meta={
                            "verified": verify_result.get("verified"),
                            "correction": verify_result.get("correction", ""),
                        },
                    )
                yield {"type": "verify", **verify_result}
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[ReAct] 验收环节异常，跳过: {e}")

        yield {"type": "final", "content": final_content}
        yield {
            "type": "session",
            "session_id": self._session_id,
            "steps": self._step_count,
            "tokens": self._total_tokens,
            "latency_ms": round(self._total_latency_ms, 1),
            "exhausted": loop_exhausted,
            "cognition": dict(cognition_totals),
        }

    # ==================== 内部工具方法 ====================

    @staticmethod
    def _merge_totals(totals: Dict[str, int], summary: Dict[str, int]) -> None:
        for key in ("triples", "states", "notes", "skipped", "total"):
            totals[key] = totals.get(key, 0) + int(summary.get(key, 0) or 0)

    async def _handle_cognition_block(
        self, block: str
    ) -> Optional[Dict[str, int]]:
        """处理认知块：解析 → 分流 → 落库，返回统计摘要"""
        if self._pipeline is None:
            return None
        try:
            result = await self._pipeline.process_block(block, self._user_id)
            records = []
            for td in result.triples:
                if isinstance(td, dict):
                    text = (
                        f"{td.get('subject', '')}{td.get('predicate', '')}"
                        f"{td.get('object', '')}"
                    ).strip()
                    if text:
                        records.append(text)
            for sd in result.states:
                if isinstance(sd, dict) and sd.get("state_name"):
                    records.append(f"[状态] {sd.get('state_name')}")
            for nd in result.notes:
                if isinstance(nd, dict) and nd.get("title"):
                    records.append(f"[笔记] {nd.get('title')}")
            return {
                "triples": len(result.triples),
                "states": len(result.states),
                "notes": len(result.notes),
                "skipped": result.skipped,
                "total": (
                    len(result.triples) + len(result.states) + len(result.notes)
                ),
                "records": records,
            }
        except Exception as e:
            logger.warning(f"[ReAct] 认知处理失败: {e}")
            return None

    async def _execute_tool(
        self, tool_name: str, tool_args: Dict[str, Any]
    ) -> ToolResult:
        if self._tool_executor is None:
            logger.warning(f"[ReAct] 工具执行器未配置，跳过 {tool_name}")
            return ToolResult(success=False, error="工具执行器未配置")
        try:
            return await self._tool_executor.execute(
                tool_name=tool_name,
                args=tool_args,
                timeout_seconds=self._tool_timeout_seconds,
            )
        except Exception as e:
            logger.error(f"[ReAct] 工具执行异常: {e}", exc_info=True)
            return ToolResult(success=False, error=str(e))

    @staticmethod
    def _normalize_tool_call(tc: Dict[str, Any]) -> Dict[str, Any]:
        """把流式解析出的工具调用规范化为 OpenAI wire 格式（arguments 为 JSON 字符串）"""
        func = tc.get("function") or {}
        args = func.get("arguments") or {}
        if isinstance(args, str):
            args_str = args
        else:
            args_str = json.dumps(args, ensure_ascii=False)
        return {
            "id": tc.get("id") or "call_unknown",
            "type": "function",
            "function": {
                "name": func.get("name"),
                "arguments": args_str,
            },
        }

    def _inject_step_awareness(
        self, messages: List[Dict[str, Any]], turn: int
    ) -> None:
        """每轮注入剩余步数感知（接近上限时提示尽快收尾）"""
        remaining = self._max_steps - (turn + 1)
        if remaining <= 0:
            return
        note = f"当前已用 {turn + 1}/{self._max_steps} 步，剩余 {remaining} 步。"
        if remaining <= max(1, int(self._max_steps * 0.2)):
            note += (
                "剩余步数已不足。若无法在剩余步数内完成，"
                "请尽快总结已取得的进展，不要开启新分支。"
            )
        else:
            note += "请继续执行。"
        messages.append({"role": "system", "content": note})