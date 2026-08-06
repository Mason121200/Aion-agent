"""对话消息实体 —— ReAct 循环的输入/输出单元

与 zero_code 的 Message 语义一致，MVP 用 dataclass 替代 pydantic。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Message:
    """对话消息

    - role: user | assistant | system | tool
    - tool_call_id: 仅 role=tool 时携带，关联 assistant 上一步的工具调用
    - reasoning: 思考链（仅 assistant 消息可能有）
    """

    session_id: str
    role: str
    content: str
    reasoning: Optional[str] = None
    tool_call_id: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)

    def is_user(self) -> bool:
        return self.role == "user"

    def is_assistant(self) -> bool:
        return self.role == "assistant"

    def is_tool(self) -> bool:
        return self.role == "tool"