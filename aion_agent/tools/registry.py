"""ToolRegistry —— 工具注册中心（MVP 版）

与 zero_code 的 DynamicToolRegistry 语义一致，裁掉动态加载/AST 安全扫描，
只保留注册、查询、列出 schema 的最小接口。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from aion_agent.core.ports.i_tool_registry import IToolRegistry

logger = logging.getLogger(__name__)


class ToolRegistry(IToolRegistry):
    """工具注册中心（无全局变量，所有状态在实例内）"""

    def __init__(self):
        self._tools: Dict[str, Dict[str, Any]] = {}

    def register(
        self,
        name: str,
        func: Callable,
        schema: Optional[Dict[str, Any]] = None,
        permission: str = "auto",
        level: str = "skill",
    ) -> None:
        if level not in ("system", "builtin", "skill"):
            level = "skill"
        if name in self._tools:
            existing_level = self._tools[name].get("level", "skill")
            if existing_level in ("system", "builtin"):
                logger.warning(
                    f"[Registry] 固化工具 '{name}'（{existing_level}）不允许被覆盖，已忽略"
                )
                return
            logger.warning(f"[Registry] 工具 '{name}' 已存在，将被覆盖")
        self._tools[name] = {
            "func": func,
            "schema": schema,
            "permission": permission if permission in ("auto", "confirm") else "auto",
            "level": level,
        }

    def get(self, name: str) -> Optional[Dict[str, Any]]:
        return self._tools.get(name)

    def list_tools(self) -> List[Dict[str, Any]]:
        return [
            entry["schema"]
            for entry in self._tools.values()
            if entry.get("schema")
        ]

    def list_tool_names(self) -> List[str]:
        return list(self._tools.keys())

    def list_tool_entries(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": name,
                "permission": entry.get("permission", "auto"),
                "level": entry.get("level", "skill"),
                "schema": entry.get("schema"),
            }
            for name, entry in self._tools.items()
        ]

    def unregister(self, name: str) -> bool:
        existed = name in self._tools
        if existed and self._tools[name].get("level") in ("system", "builtin"):
            logger.warning(f"[Registry] 固化工具 '{name}' 不允许被移除")
            return False
        self._tools.pop(name, None)
        return existed

    def is_registered(self, name: str) -> bool:
        return name in self._tools