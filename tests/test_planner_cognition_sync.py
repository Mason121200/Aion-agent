"""任务-状态联动测试：plan ↔ state ↔ triple 的一致性同步

覆盖用户实际操作场景：
- 创建任务 → 建立 state + triple 关联
- 停止/归档/完成任务 → state 释放、triple 失效
- 修改标题/截止 → state 描述与 triple 同步更新
- 检视打卡 → state 刷新活跃时间
"""

import asyncio
import sys

sys.path.insert(0, ".")

from aion_agent.planner.planner_repo import JsonPlanRepo  # noqa: E402
from aion_agent.storage.in_memory_cognitive_repo import InMemoryCognitiveRepo  # noqa: E402
from aion_agent.tools import ToolExecutor, ToolRegistry, register_planner_tools  # noqa: E402


def _env(tmp_path):
    cog = InMemoryCognitiveRepo(persist_dir=str(tmp_path))
    plan = JsonPlanRepo(persist_dir=str(tmp_path))
    reg = ToolRegistry()
    register_planner_tools(reg, plan, cognitive_repo=cog, user_id="u1")
    exec_ = ToolExecutor(reg)
    return cog, plan, exec_


def run(coro):
    return asyncio.run(coro)


def _create(exec_, title="三个月完成项目上线", goal="上线 MVP"):
    res = run(exec_.execute("task_create", {"title": title, "goal": goal}))
    assert res.success, res.error
    return res.data


def test_task_create_links_state_and_triple(tmp_path):
    cog, _, exec_ = _env(tmp_path)
    res = _create(exec_)
    p = res["plan"]
    assert p.get("state_id")
    assert p.get("rel_id")
    state = run(cog.get_state(p["state_id"]))
    assert state is not None
    assert state.task_id == p["plan_id"]
    assert state.is_active is True
    triple = run(cog.get_triple(p["rel_id"]))
    assert triple is not None
    assert triple.subject == "我"
    assert triple.predicate == "正在执行长期任务"
    assert triple.object == p["title"]
    assert triple.is_active is True


def test_task_update_completed_releases(tmp_path):
    cog, _, exec_ = _env(tmp_path)
    p = _create(exec_)["plan"]
    res = run(exec_.execute("task_update", {"plan_id": p["plan_id"], "status": "completed"}))
    assert res.success
    state = run(cog.get_state(p["state_id"]))
    assert state.is_active is False
    assert state.released_reason == "completed"
    triple = run(cog.get_triple(p["rel_id"]))
    assert triple.is_active is False


def test_task_update_archived_releases(tmp_path):
    cog, _, exec_ = _env(tmp_path)
    p = _create(exec_)["plan"]
    res = run(exec_.execute("task_update", {"plan_id": p["plan_id"], "status": "archived"}))
    assert res.success
    state = run(cog.get_state(p["state_id"]))
    assert state.is_active is False
    assert state.released_reason == "cancelled"


def test_task_archive_releases(tmp_path):
    cog, _, exec_ = _env(tmp_path)
    p = _create(exec_)["plan"]
    res = run(exec_.execute("task_archive", {"plan_id": p["plan_id"]}))
    assert res.success
    state = run(cog.get_state(p["state_id"]))
    assert state.is_active is False
    assert state.released_reason == "cancelled"
    triple = run(cog.get_triple(p["rel_id"]))
    assert triple.is_active is False


def test_task_update_title_syncs(tmp_path):
    cog, _, exec_ = _env(tmp_path)
    p = _create(exec_)["plan"]
    res = run(exec_.execute("task_update", {"plan_id": p["plan_id"], "title": "改为完成产品上线"}))
    assert res.success
    state = run(cog.get_state(p["state_id"]))
    assert "改为完成产品上线" in state.description
    triple = run(cog.get_triple(p["rel_id"]))
    assert triple.object == "改为完成产品上线"
    assert triple.is_active is True


def test_task_update_end_date_syncs(tmp_path):
    cog, _, exec_ = _env(tmp_path)
    p = _create(exec_)["plan"]
    res = run(exec_.execute("task_update", {"plan_id": p["plan_id"], "end_date": "2027-01-01"}))
    assert res.success
    state = run(cog.get_state(p["state_id"]))
    assert state.expires_at is not None
    assert "2027" in str(state.expires_at)
    triple = run(cog.get_triple(p["rel_id"]))
    assert triple.expires_at is not None
    assert "2027" in str(triple.expires_at)


def test_task_checkin_touches_state(tmp_path):
    cog, _, exec_ = _env(tmp_path)
    p = _create(exec_)["plan"]
    before = run(cog.get_state(p["state_id"])).last_updated_at
    res = run(exec_.execute(
        "task_checkin",
        {"plan_id": p["plan_id"], "summary": "完成阶段一", "progress": 50},
    ))
    assert res.success
    state = run(cog.get_state(p["state_id"]))
    assert state.is_active is True
    assert state.last_updated_at >= before


def test_task_plan_progress_syncs_after_milestone(tmp_path):
    cog, _, exec_ = _env(tmp_path)
    res = run(exec_.execute("task_create", {
        "title": "备考计划",
        "milestones": [{"title": "阶段一"}, {"title": "阶段二"}],
    }))
    p = res.data["plan"]
    ms = p["milestones"]
    res = run(exec_.execute("task_complete_milestone", {
        "plan_id": p["plan_id"], "milestone_id": ms[0]["milestone_id"],
    }))
    assert res.success
    state = run(cog.get_state(p["state_id"]))
    assert state.is_active is True
