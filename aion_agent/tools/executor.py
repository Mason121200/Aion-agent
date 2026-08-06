"""ToolExecutor —— 工具执行器（带超时熔断）

- 从注册表解析工具函数，统一包装为 ToolResult
- 同步 handler 在后台线程执行，支持超时熔断
- 任何异常都收敛为 ToolResult(success=False)，不向循环抛出
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from aion_agent.core.ports.i_tool_executor import IToolExecutor, ToolResult
from aion_agent.core.ports.i_tool_registry import IToolRegistry

logger = logging.getLogger(__name__)


class ToolExecutor(IToolExecutor):
    """工具执行器（MVP）"""

    def __init__(self, registry: IToolRegistry):
        self._registry = registry

    async def execute(
        self,
        tool_name: str,
        args: Dict[str, Any],
        timeout_seconds: int = 30,
    ) -> ToolResult:
        entry = self._registry.get(tool_name)
        if entry is None:
            return ToolResult(
                success=False,
                error=f"工具未注册: {tool_name}",
                error_code="TOOL_NOT_FOUND",
            )
        handler = entry.get("func")
        if handler is None:
            return ToolResult(
                success=False,
                error=f"工具 '{tool_name}' 缺少处理函数",
                error_code="TOOL_NOT_FOUND",
            )

        clean_args = args or {}
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(handler, clean_args),
                timeout=timeout_seconds,
            )
            return ToolResult(success=True, data=result)
        except asyncio.TimeoutError:
            logger.warning(f"[Executor] 工具 '{tool_name}' 超时（>{timeout_seconds}s）")
            return ToolResult(
                success=False,
                error=f"工具执行超时（>{timeout_seconds}s）",
                error_code="TIMEOUT",
            )
        except Exception as e:
            logger.warning(f"[Executor] 工具 '{tool_name}' 执行失败: {e}")
            return ToolResult(
                success=False,
                error=str(e),
                error_code="EXECUTION_ERROR",
            )