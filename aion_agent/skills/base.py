"""Skill 基类 —— 通用 agent 的扩展单元

一个 Skill = 元数据（name/version/description）+ 工具注册 + 生命周期。
任何新能力都以 Skill 形式接入：注册自己的工具集，底座不感知具体功能。
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from aion_agent.core.ports.i_tool_registry import IToolRegistry


class Skill:
    """技能单元：把一组工具 + 元数据打包成可安装 / 启停的扩展。"""

    def __init__(
        self,
        name: str,
        version: str = "1.0.0",
        description: str = "",
        tools: Optional[List[str]] = None,
        register_func: Optional[Callable[[IToolRegistry], None]] = None,
        level: str = "skill",
    ):
        if not name or not name.strip():
            raise ValueError("Skill name 不能为空")
        if level not in ("system", "builtin", "skill"):
            raise ValueError(f"无效的技能层级: {level}")
        self.name = name.strip()
        self.version = version or "1.0.0"
        self.description = description or ""
        self.tools = list(tools or [])
        self.level = level
        self._register_func = register_func

    def register_tools(self, registry: IToolRegistry) -> None:
        """把本技能的工具注册进 ToolRegistry（未提供 register_func 时为 no-op）"""
        if self._register_func is not None:
            self._register_func(registry)

    def to_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "tools": list(self.tools),
            "level": self.level,
        }

    def to_manifest(self) -> Dict[str, object]:
        """导出为可分发 manifest（生态协议：任何实现可据此发现/安装能力）

        与 to_dict 的区别：manifest 是协议层的稳定描述，格式见
        docs/sync-protocol.md 2.8 节。
        """
        return {
            "manifest_version": "1.0",
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "level": self.level,
            "tools": list(self.tools),
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Skill {self.name} v{self.version} tools={len(self.tools)}>"
