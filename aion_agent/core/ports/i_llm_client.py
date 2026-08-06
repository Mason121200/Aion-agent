"""LLM 客户端端口 —— 与 LLM 交互的抽象接口（流式/非流式/工具调用）"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional


@dataclass
class StreamChunk:
    """流式响应块（增量）"""

    content: str = ""                # 可见回复文本（增量）
    reasoning: str = ""              # 思考链（增量，DeepSeek 等支持）
    is_final: bool = False
    tool_calls: Optional[List[Dict[str, Any]]] = None  # None=无工具调用
    usage: Optional[Dict[str, int]] = None


@dataclass
class LLMResponse:
    """非流式响应"""

    content: str
    reasoning: str = ""
    tool_calls: Optional[List[Dict[str, Any]]] = None
    usage: Dict[str, int] = field(default_factory=dict)
    finish_reason: Optional[str] = None


class ILLMClient(ABC):
    """LLM 客户端接口 —— 支持流式和非流式调用"""

    @abstractmethod
    async def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = "auto",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """非流式调用 LLM"""
        ...

    @abstractmethod
    async def stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = "auto",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[StreamChunk, None]:
        """流式调用 LLM，逐个产出 StreamChunk"""
        ...