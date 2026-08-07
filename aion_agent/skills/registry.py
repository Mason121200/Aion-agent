"""SkillRegistry —— 技能的安装、启停与工具展开

- install: 安装技能，名字冲突直接拒绝（防止覆盖）
- enable / disable: 控制技能是否参与工具展开
- apply_tools: 把所有已启用技能的工具注册进 ToolRegistry，并报告命名冲突
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from aion_agent.core.ports.i_tool_registry import IToolRegistry
from aion_agent.skills.base import Skill

logger = logging.getLogger(__name__)


class SkillRegistry:
    """技能注册中心（无全局变量，所有状态在实例内）"""

    def __init__(self) -> None:
        self._skills: Dict[str, Skill] = {}
        self._enabled: Dict[str, bool] = {}
        self._conflicts: List[dict] = []

    # ---------- 安装 / 启停 ----------

    def install(self, skill: Skill, *, enabled: bool = True) -> None:
        if skill.name in self._skills:
            raise ValueError(f"技能已安装: {skill.name}")
        self._skills[skill.name] = skill
        self._enabled[skill.name] = enabled

    def enable(self, name: str) -> bool:
        if name not in self._skills:
            return False
        self._enabled[name] = True
        return True

    def disable(self, name: str) -> bool:
        if name not in self._skills:
            return False
        skill = self._skills[name]
        if skill.level in ("system", "builtin"):
            logger.warning(f"[Skills] 固化技能 '{name}'（{skill.level}）不允许被禁用")
            return False
        self._enabled[name] = False
        return True

    def is_enabled(self, name: str) -> bool:
        return self._enabled.get(name, False)

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def list_skills(self) -> List[dict]:
        return [
            {**skill.to_dict(), "enabled": self._enabled.get(skill.name, False)}
            for skill in self._skills.values()
        ]

    def names(self) -> List[str]:
        return list(self._skills.keys())

    # ---------- 工具展开 ----------

    def apply_tools(self, registry: IToolRegistry) -> List[str]:
        """把所有已启用技能的工具展开到 ToolRegistry，返回新增的工具名列表。"""
        self._conflicts = []
        registered: List[str] = []
        for skill in self._skills.values():
            if not self._enabled.get(skill.name, False):
                logger.info(f"[Skills] 技能 '{skill.name}' 已禁用，跳过工具注册")
                continue
            before = set(registry.list_tool_names())
            skill.register_tools(registry)
            after = set(registry.list_tool_names())
            new_names = sorted(after - before)
            registered.extend(new_names)
            overlaps = sorted(set(skill.tools) & before)
            if overlaps:
                self._conflicts.append({"skill": skill.name, "tools": overlaps})
                logger.warning(
                    f"[Skills] 技能 '{skill.name}' 存在工具名冲突: {overlaps}"
                )
        return registered

    @property
    def conflicts(self) -> List[dict]:
        return list(self._conflicts)
