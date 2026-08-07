"""技能层 —— 通用 agent 的扩展机制

安装 / 启停 / 展开 Skill，把「新能力 = 新技能包」变成底座的自然延伸。
"""

from aion_agent.skills.base import Skill
from aion_agent.skills.catalog import build_default_skills
from aion_agent.skills.registry import SkillRegistry

__all__ = [
    "Skill",
    "SkillRegistry",
    "build_default_skills",
]
