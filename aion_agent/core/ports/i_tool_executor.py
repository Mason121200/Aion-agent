"""工具执行端口 —— 定义工具的统一执行接口与结果载体"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ToolResult:
    """统一工具执行结果"""

    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    error_code: Optional[str] = None  # TOOL_NOT_FOUND | TIMEOUT | EXECUTION_ERROR | ...
    meta: Optional[Dict[str, Any]] = None

    def is_success(self) -> bool:
        return self.success


class ToolExecutionError(Exception):
    """工具执行异常"""

    def __init__(self, message: str, error_code: Optional[str] = None):
        self.message = message
        self.error_code = error_code
        super().__init__(message)


class IToolExecutor(ABC):
    """工具执行器接口 —— 支持超时熔断"""

    @abstractmethod
    async def execute(
        self,
        tool_name: str,
        args: Dict[str, Any],
        timeout_seconds: int = 30,
    ) -> ToolResult:
        """执行工具"""
        ...