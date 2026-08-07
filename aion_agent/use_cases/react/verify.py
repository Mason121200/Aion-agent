"""Verify 阶段 —— 最终答复验收（反思层的「验收」环节）

在 ReAct 循环结束、即将产出最终回复前，若本轮调用过工具：
让 LLM 核对「最终答复是否与工具结果一致、是否回答了用户问题」。
不一致时生成简短更正附在回复末尾，确保「基于真实情况回复用户」。
任何解析/调用异常都默认通过（验收是增强，不是阻断）。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from aion_agent.use_cases.react.prompts import VERIFY_PROMPT

logger = logging.getLogger(__name__)

_MAX_RESULTS_SHOWN = 5
_MAX_FAILURES_SHOWN = 3
_MAX_RESULT_PREVIEW = 200
_MAX_REPLY_PREVIEW = 1500
_MAX_ISSUES_LEN = 300
_MAX_CORRECTION_LEN = 400


def _format_verify_input(
    tool_results: List[Dict[str, Any]],
    final_reply: str,
    turn: int,
) -> str:
    """构建验收输入：工具结果摘要 + 最终答复"""
    successes = [r for r in tool_results if r.get("success", False)]
    failures = [r for r in tool_results if not r.get("success", False)]
    parts = [
        f"当前轮次：第 {turn + 1} 轮（已结束，准备产出最终答复）",
        f"工具执行成功数：{len(successes)}，失败数：{len(failures)}",
    ]
    for r in successes[:_MAX_RESULTS_SHOWN]:
        content = str(r.get("content") or "")[:_MAX_RESULT_PREVIEW]
        parts.append(f"- 成功 {r.get('tool_call_id', '?')}: {content}")
    for f in failures[:_MAX_FAILURES_SHOWN]:
        error = str(f.get("error") or "")[:_MAX_RESULT_PREVIEW]
        parts.append(f"- 失败 {f.get('tool_call_id', '?')}: {error}")
    parts.append(f"最终答复：\n{str(final_reply or '')[:_MAX_REPLY_PREVIEW]}")
    return "\n".join(parts)


def _parse_verify_json(content: str) -> Optional[Dict[str, Any]]:
    """解析验收 JSON：容忍围栏/前后缀，verified 缺省视为 True"""
    if not content:
        return None
    raw = str(content).strip()
    if raw.startswith("```"):
        raw = raw.replace("```json", "").replace("```", "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        decision = json.loads(raw[start:end + 1])
    except Exception:
        return None
    if not isinstance(decision, dict):
        return None
    return {
        "verified": bool(decision.get("verified", True)),
        "issues": str(decision.get("issues") or "")[:_MAX_ISSUES_LEN],
        "correction": str(decision.get("correction") or "")[:_MAX_CORRECTION_LEN],
    }


async def verify_with_llm(
    llm_client: Any,
    *,
    tool_results: List[Dict[str, Any]],
    final_reply: str,
    turn: int,
) -> Dict[str, Any]:
    """最终答复验收。返回 {"verified": bool, "issues": str, "correction": str, "skipped": bool}"""
    if not tool_results:
        return {"verified": True, "issues": "", "correction": "", "skipped": True}
    if not str(final_reply or "").strip():
        return {"verified": True, "issues": "", "correction": "", "skipped": True}
    if llm_client is None:
        return {"verified": True, "issues": "", "correction": "", "skipped": True}
    try:
        response = await llm_client.complete(
            messages=[
                {"role": "system", "content": VERIFY_PROMPT},
                {
                    "role": "user",
                    "content": _format_verify_input(
                        tool_results, final_reply, turn
                    ),
                },
            ]
        )
        decision = _parse_verify_json(getattr(response, "content", "") or "")
        if decision is None:
            logger.info("[Verify] 验收结果无法解析，默认通过")
            return {
                "verified": True, "issues": "", "correction": "",
                "skipped": False, "note": "验收结果无法解析，默认通过",
            }
        return {
            "verified": bool(decision.get("verified", True)),
            "issues": decision.get("issues", ""),
            "correction": decision.get("correction", ""),
            "skipped": False,
        }
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[Verify] 验收异常，默认通过: {e}")
        return {
            "verified": True, "issues": "", "correction": "",
            "skipped": False, "note": f"验收异常，默认通过: {e}",
        }


def format_correction(final_reply: str, correction: str) -> str:
    """把验收更正附到最终答复末尾（保持流式输出体验）"""
    correction = str(correction or "").strip()
    if not correction:
        return final_reply
    return f"{final_reply}\n\n---\n（更正）{correction}"
