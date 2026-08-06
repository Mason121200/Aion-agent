"""工具调用实体 —— 记录一次工具调用的生命周期"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional


class ToolCallStatus(str, Enum):
    PENDING = "pending"
    EXECUTING = "executing"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass
class ToolCall:
    """工具调用记录（与 zero_code 语义一致）"""

    id: str
    session_id: str
    name: str
    args: Dict[str, Any] = field(default_factory=dict)
    result: Optional[Any] = None
    error: Optional[str] = None
    status: ToolCallStatus = ToolCallStatus.PENDING
    duration_ms: float = 0.0
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    def is_completed(self) -> bool:
        return self.status in (
            ToolCallStatus.SUCCESS,
            ToolCallStatus.FAILED,
            ToolCallStatus.TIMEOUT,
        )

    def is_success(self) -> bool:
        return self.status == ToolCallStatus.SUCCESS