"""端口层 —— 依赖倒置的抽象接口"""

from aion_agent.core.ports.i_chat_repo import IChatRepo
from aion_agent.core.ports.i_cognitive_repo import ICognitiveRepo
from aion_agent.core.ports.i_llm_client import ILLMClient, LLMResponse, StreamChunk
from aion_agent.core.ports.i_tool_executor import (
    IToolExecutor,
    ToolExecutionError,
    ToolResult,
)
from aion_agent.core.ports.i_tool_registry import IToolRegistry

__all__ = [
    "IChatRepo",
    "ICognitiveRepo",
    "ILLMClient",
    "LLMResponse",
    "StreamChunk",
    "IToolExecutor",
    "ToolExecutionError",
    "ToolResult",
    "IToolRegistry",
]