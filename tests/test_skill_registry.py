"""技能层测试：Skill 基类 / 注册表安装启停 / 工具展开与冲突检测"""

import sys

import pytest

sys.path.insert(0, ".")

from aion_agent.skills import Skill, SkillRegistry, build_default_skills  # noqa: E402
from aion_agent.tools import ToolRegistry, register_builtin_tools  # noqa: E402


def _dummy_skill(name="demo", tools=("t1", "t2")):
    def register(reg):
        for t in tools:
            reg.register(t, lambda args: {"ok": True}, schema={"type": "function"})
    return Skill(
        name=name,
        version="1.0.0",
        description="测试技能",
        tools=list(tools),
        register_func=register,
    )


def test_skill_metadata_and_repr():
    s = Skill(name="demo", description="演示")
    assert s.name == "demo"
    assert s.version == "1.0.0"
    d = s.to_dict()
    assert d["name"] == "demo"
    assert d["tools"] == []


def test_skill_to_manifest():
    s = Skill(name="demo", version="2.1.0", description="演示技能",
              tools=["t1", "t2"], level="skill")
    m = s.to_manifest()
    assert m["manifest_version"] == "1.0"
    assert m["name"] == "demo"
    assert m["version"] == "2.1.0"
    assert m["description"] == "演示技能"
    assert m["level"] == "skill"
    assert m["tools"] == ["t1", "t2"]


def test_default_catalog_manifests_valid():
    from aion_agent.planner.planner_repo import JsonPlanRepo
    from aion_agent.storage.in_memory_cognitive_repo import InMemoryCognitiveRepo
    from aion_agent.study.study_repo import JsonStudyRepo

    skills = build_default_skills(
        cognitive_repo=InMemoryCognitiveRepo(),
        study_repo=JsonStudyRepo(),
        planner_repo=JsonPlanRepo(),
        user_id="u1",
    )
    for s in skills:
        m = s.to_manifest()
        assert m["manifest_version"] == "1.0"
        assert m["name"] == s.name
        assert m["tools"], f"skill {s.name} 必须有工具"
        assert all(isinstance(t, str) for t in m["tools"])


def test_skill_name_required():
    with pytest.raises(ValueError):
        Skill(name="")


def test_registry_install_duplicate_rejected():
    reg = SkillRegistry()
    reg.install(_dummy_skill())
    with pytest.raises(ValueError):
        reg.install(_dummy_skill())


def test_registry_enable_disable_apply_tools():
    reg = SkillRegistry()
    reg.install(_dummy_skill())
    assert reg.is_enabled("demo") is True
    reg.disable("demo")
    assert reg.is_enabled("demo") is False
    tool_registry = ToolRegistry()
    names = reg.apply_tools(tool_registry)
    assert names == []  # 禁用后不展开
    reg.enable("demo")
    names = reg.apply_tools(tool_registry)
    assert sorted(names) == ["t1", "t2"]
    assert tool_registry.is_registered("t1")
    assert reg.list_skills()[0]["enabled"] is True


def test_apply_tools_conflict_detection():
    reg = SkillRegistry()
    reg.install(_dummy_skill("a", tools=("shared", "a1")))
    reg.install(_dummy_skill("b", tools=("shared", "b1")))
    tool_registry = ToolRegistry()
    reg.apply_tools(tool_registry)
    reg.apply_tools(tool_registry)
    conflicts = reg.conflicts
    assert any(c["skill"] == "b" and "shared" in c["tools"] for c in conflicts)


def test_default_catalog_builds_with_repos():
    from aion_agent.planner.planner_repo import JsonPlanRepo
    from aion_agent.storage.in_memory_cognitive_repo import InMemoryCognitiveRepo
    from aion_agent.study.study_repo import JsonStudyRepo

    skills = build_default_skills(
        cognitive_repo=InMemoryCognitiveRepo(),
        study_repo=JsonStudyRepo(),
        planner_repo=JsonPlanRepo(),
        user_id="u1",
    )
    names = [s.name for s in skills]
    assert names == ["builtin", "cognition", "planner", "study"]

    tool_registry = ToolRegistry()
    for s in skills:
        s.register_tools(tool_registry)
    assert tool_registry.is_registered("get_current_time")
    assert tool_registry.is_registered("search_cognition")
    assert tool_registry.is_registered("task_create")
    assert tool_registry.is_registered("plan_create")


def test_default_catalog_skips_missing_repos():
    skills = build_default_skills(cognitive_repo=None, study_repo=None, planner_repo=None)
    names = [s.name for s in skills]
    assert names == ["builtin", "cognition"]
