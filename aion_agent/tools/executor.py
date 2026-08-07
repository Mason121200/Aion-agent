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


class ToolPolicy:
    """工具权限策略：blocked（禁用）/ confirm（需用户确认）

    - blocked: 无论注册表权限如何，一律拒绝执行
    - confirm: 标记为需确认，未开启 auto_approve 时拒绝执行
    策略可持久化（tool_policy.json）并热更新。
    """

    def __init__(self, blocked=None, confirm=None):
        self._blocked = set(str(x) for x in (blocked or []))
        self._confirm = set(str(x) for x in (confirm or []))

    def is_blocked(self, name: str) -> bool:
        return name in self._blocked

    def requires_confirm(self, name: str) -> bool:
        return name in self._confirm

    def set_blocked(self, names) -> None:
        self._blocked = set(str(x) for x in (names or []))

    def set_confirm(self, names) -> None:
        self._confirm = set(str(x) for x in (names or []))

    def to_dict(self) -> dict:
        return {
            "blocked": sorted(self._blocked),
            "confirm": sorted(self._confirm),
        }

    @classmethod
    def from_dict(cls, data) -> "ToolPolicy":
        data = data or {}
        return cls(
            blocked=data.get("blocked") or [],
            confirm=data.get("confirm") or [],
        )


class ToolExecutor(IToolExecutor):
    """工具执行器（含权限策略拦截）"""

    def __init__(
        self,
        registry: IToolRegistry,
        policy: Optional[ToolPolicy] = None,
        auto_approve: bool = False,
    ):
        self._registry = registry
        self._policy = policy
        self._auto_approve = auto_approve

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

        level = entry.get("level", "skill")
        if (
            self._policy is not None
            and self._policy.is_blocked(tool_name)
            and level != "system"
        ):
            return ToolResult(
                success=False,
                error=f"工具已被禁用: {tool_name}",
                error_code="TOOL_BLOCKED",
            )
        permission = entry.get("permission", "auto")
        if self._policy is not None and self._policy.requires_confirm(tool_name):
            permission = "confirm"
        if permission == "confirm" and not self._auto_approve:
            return ToolResult(
                success=False,
                error=f"工具需要用户确认后才可执行: {tool_name}（当前未开启自动执行）",
                error_code="NEEDS_CONFIRM",
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