"""Reflect 阶段 —— 评估工具执行结果，决定继续/停止/纠偏

移植自 zero_code 的 src/use_cases/react/reflect.py：
- reflect()：规则式快路径（无失败→继续；全成功→继续；无工具调用→停止）
- reflect_with_llm()：Reflexion 化——失败时调用 LLM 分析原因生成修正，
  解析失败或调用异常自动回退规则式，保证主链路稳定。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from aion_agent.use_cases.react.prompts import REFLECTION_PROMPT

logger = logging.getLogger(__name__)

_MAX_FAILURES_SHOWN = 3
_MAX_REASON_PREVIEW = 200
_MAX_CORRECTION_LEN = 500
_MAX_ASSISTANT_PREVIEW = 600
_MAX_ERROR_PREVIEW = 400


def reflect(
    tool_results: List[Dict[str, Any]],
    turn: int,
    messages: Optional[List] = None,
) -> Dict[str, Any]:
    """规则式 Reflect：评估结果，决定下一步动作

    Returns:
        {"action": "stop" | "continue" | "fallback", "reason": str, "correction": str}
    """
    results = tool_results or []

    if results:
        failures = [r for r in results if not r.get("success", False)]
        if failures:
            correction = "以下工具执行失败，请使用其他方式或修正参数重试：\n"
            for f in failures:
                error = f.get("error", "未知错误")
                tool_name = f.get("tool_call_id", "unknown")
                correction += f"- {tool_name}: {error}\n"
            correction += "\n请尝试替代方案。"
            return {
                "action": "fallback",
                "reason": f"{len(failures)} 个工具失败，尝试纠偏",
                "correction": correction,
            }
        return {
            "action": "continue",
            "reason": "工具执行成功，继续下一轮推理",
        }

    return {
        "action": "stop",
        "reason": "无工具调用，任务完成",
    }


def _format_reflection_input(
    tool_results: List[Dict[str, Any]],
    turn: int,
    messages: Optional[List] = None,
) -> str:
    """构造 LLM 反思输入：轮次 + 失败明细（含上一轮推理预览）"""
    failures = [r for r in (tool_results or []) if not r.get("success", False)]
    parts = [
        f"当前轮次：第 {turn + 1} 轮",
        f"工具执行失败数：{len(failures)}",
    ]
    if messages:
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                text = str(msg.get("content") or "").strip()
                if text:
                    parts.append(f"最近推理：\n{text[:_MAX_ASSISTANT_PREVIEW]}")
                break
    parts.append("失败明细：")
    for f in failures[:_MAX_FAILURES_SHOWN]:
        tool_id = f.get("tool_call_id", "unknown")
        error = str(f.get("error") or f.get("content") or "未知错误")
        parts.append(f"- tool_call_id={tool_id}: {error[:_MAX_ERROR_PREVIEW]}")
    return "\n".join(parts)


def _parse_reflection_json(content: str) -> Optional[Dict[str, Any]]:
    """解析反思 JSON：容忍围栏/前后缀，action 必须在白名单内"""
    if not content:
        return None
    raw = str(content).strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"```\s*$", "", raw).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        decision = json.loads(raw[start:end + 1])
    except Exception:
        return None
    if not isinstance(decision, dict):
        return None
    action = decision.get("action")
    if action not in ("fallback", "stop", "continue"):
        return None
    return {
        "action": action,
        "reason": str(decision.get("reason") or "")[:_MAX_REASON_PREVIEW],
        "correction": str(decision.get("correction") or "")[:_MAX_CORRECTION_LEN],
    }


async def reflect_with_llm(
    llm_client: Any,
    tool_results: List[Dict[str, Any]],
    turn: int,
    messages: Optional[List] = None,
) -> Dict[str, Any]:
    """LLM 反思版 Reflect（Reflexion 化）

    无失败走规则式快路径（不产生额外 LLM 调用）；有失败时调用 LLM
    分析原因并生成修正指令。任何异常都回退规则式 reflect()。
    """
    base = reflect(tool_results, turn, messages)
    if base["action"] != "fallback":
        return base
    if llm_client is None:
        return base
    try:
        response = await llm_client.complete(
            messages=[
                {"role": "system", "content": REFLECTION_PROMPT},
                {"role": "user", "content": _format_reflection_input(
                    tool_results, turn, messages,
                )},
            ],
            temperature=0.2,
        )
        content = (
            response.content
            if hasattr(response, "content")
            else response.get("content", "")
        )
        decision = _parse_reflection_json(content)
        if decision:
            decision["reflected"] = True
            return decision
        logger.warning("[Reflect] LLM 反思输出无法解析，回退规则式")
    except Exception as exc:
        logger.warning("[Reflect] LLM 反思调用失败，回退规则式: %s", exc)
    return base