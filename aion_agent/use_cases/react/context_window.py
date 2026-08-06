"""上下文窗口管理 —— 历史窗口 + Token 预算

教材第8章「上下文学习」在工程上的直接落地：模型上下文有长度上限，
Agent 必须在窗口内做取舍——
- 窗口裁剪：只保留最近 max_messages 条消息
- Token 预算：按估算 token 裁剪历史，超出预算丢最旧的非 system 消息
- 本轮保护：最新的用户消息永不丢弃
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

from aion_agent.core.entities.message import Message


def estimate_tokens(text: str) -> int:
    """估算文本 token 数（中文按 1.5、英文按 1.3、其余按 0.25）"""
    if not text:
        return 0
    chinese_chars = len(re.findall(
        r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]", text
    ))
    english_words = len(re.findall(r"[a-zA-Z]+", text))
    remaining = len(text) - chinese_chars - sum(
        len(w) for w in re.findall(r"[a-zA-Z]+", text)
    )
    tokens = int(
        chinese_chars * 1.5
        + english_words * 1.3
        + remaining * 0.25
    )
    return max(1, tokens)


def estimate_message_tokens(msg: Message) -> int:
    """估算一条消息的 token 数（含思考链）"""
    return estimate_tokens(msg.content) + estimate_tokens(msg.reasoning or "")


def trim_history(history: List[Message], max_messages: int) -> List[Message]:
    """历史窗口裁剪：只保留最近 max_messages 条消息

    - max_messages <= 0 视为不裁剪
    - 永远至少保留最后一条（最新用户消息）
    """
    if max_messages <= 0 or len(history) <= max_messages:
        return list(history)
    return history[-max_messages:]


def trim_messages_by_tokens(
    messages: List[Dict[str, str]],
    budget: int,
    protected_tail: int = 1,
) -> Tuple[List[Dict[str, str]], int]:
    """按 token 预算裁剪消息列表

    规则：
    - system 消息永不裁剪（认知规则/动态上下文必须保留）
    - 尾部 protected_tail 条（最新用户消息）永不裁剪
    - 从最旧的历史消息开始丢弃，直到估算总量 <= budget

    Returns:
        (裁剪后的消息列表, 丢弃条数)
    """
    result = list(messages)
    if budget <= 0:
        return result, 0

    total = sum(estimate_tokens(m.get("content") or "") for m in result)
    if total <= budget:
        return result, 0

    tail = result[-protected_tail:] if protected_tail > 0 else []
    head = result[:-protected_tail] if protected_tail > 0 else list(result)

    dropped = 0
    while len(head) > 1 and total > budget:
        popped = False
        for i, m in enumerate(head):
            if m.get("role") != "system":
                total -= estimate_tokens(m.get("content") or "")
                head.pop(i)
                dropped += 1
                popped = True
                break
        if not popped:
            break

    return head + tail, dropped