"""固化层级测试：固定工具不可覆盖/不可删除，system 工具不可 block，固定技能不可禁用"""

import asyncio
import sys

import pytest

sys.path.insert(0, ".")

from aion_agent.skills import Skill, SkillRegistry  # noqa: E402
from aion_agent.tools import ToolExecutor, ToolPolicy, ToolRegistry, register_builtin_tools  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def test_register_stores_level():
    registry = ToolRegistry()
    registry.register("t", lambda args: {"ok": True}, level="system")
    assert registry.get("t")["level"] == "system"
    entry = registry.list_tool_entries()[0]
    assert entry["name"] == "t"
    assert entry["level"] == "system"


def test_invalid_level_falls_back_to_skill():
    registry = ToolRegistry()
    registry.register("t", lambda args: {"ok": True}, level="whatever")
    assert registry.get("t")["level"] == "skill"


def test_builtin_tool_cannot_be_overwritten():
    registry = ToolRegistry()
    register_builtin_tools(registry)
    original = registry.get("get_current_time")["func"]
    registry.register("get_current_time", lambda args: {"hacked": True})
    assert registry.get("get_current_time")["func"] is original


def test_builtin_tool_cannot_be_unregistered():
    registry = ToolRegistry()
    register_builtin_tools(registry)
    assert registry.unregister("calculator") is False
    assert registry.is_registered("calculator")


def test_skill_tool_can_be_unregistered():
    registry = ToolRegistry()
    registry.register("t", lambda args: {"ok": True}, level="skill")
    assert registry.unregister("t") is True
    assert not registry.is_registered("t")


def test_system_tool_ignores_blocked_policy():
    registry = ToolRegistry()
    registry.register("sys_tool", lambda args: {"ok": True}, level="system")
    executor = ToolExecutor(registry, policy=ToolPolicy(blocked=["sys_tool"]))
    result = run(executor.execute("sys_tool", {}))
    assert result.success


def test_builtin_tool_can_be_blocked_by_policy():
    registry = ToolRegistry()
    register_builtin_tools(registry)
    executor = ToolExecutor(registry, policy=ToolPolicy(blocked=["shell"]))
    result = run(executor.execute("shell", {"command": "whoami"}))
    assert not result.success
    assert result.error_code == "TOOL_BLOCKED"


def test_skill_level_validation():
    with pytest.raises(ValueError):
        Skill(name="bad", level="unknown")


def test_system_skill_cannot_be_disabled():
    reg = SkillRegistry()
    reg.install(Skill(name="core", level="system"))
    assert reg.disable("core") is False
    assert reg.is_enabled("core") is True


def test_builtin_skill_cannot_be_disabled():
    reg = SkillRegistry()
    reg.install(Skill(name="builtin", level="builtin"))
    assert reg.disable("builtin") is False


def test_normal_skill_can_be_disabled():
    reg = SkillRegistry()
    reg.install(Skill(name="demo"))
    assert reg.disable("demo") is True
    assert reg.is_enabled("demo") is False


def test_skill_to_dict_includes_level():
    s = Skill(name="demo", level="system")
    assert s.to_dict()["level"] == "system"
