"""方案锁定与调整裁决测试：重复日程模板 / 锁定后不可随意更改 / 调整需用户确认"""

import asyncio
import sys

sys.path.insert(0, ".")

import pytest  # noqa: E402

from aion_agent.planner.planner_repo import JsonPlanRepo  # noqa: E402


def _repo(tmp_path):
    return JsonPlanRepo(persist_dir=str(tmp_path))


def _routine():
    return {
        "recurrence": "daily",
        "blocks": [
            {"name": "上午审查", "slot": "上午 3-4h", "focus": "学习审查线", "minutes": 180},
            {"name": "中午内容", "slot": "中午 1h", "focus": "内容产出线", "minutes": 60},
            {"name": "下午任务", "slot": "下午 1h", "focus": "项目推进线", "minutes": 60},
        ],
    }


def test_create_plan_with_routine_and_locked_default(tmp_path):
    repo = _repo(tmp_path)
    plan = repo.create_plan(title="每日固定节奏", routine=_routine())["plan"]
    assert plan["routine"]["recurrence"] == "daily"
    assert len(plan["routine"]["blocks"]) == 3
    assert plan["routine"]["blocks"][0]["name"] == "上午审查"
    assert plan["locked"] is False
    assert plan["pending_adjustments"] == []


def test_lock_blocks_core_update_but_allows_progress(tmp_path):
    repo = _repo(tmp_path)
    plan = repo.create_plan(title="备考计划", goal="过四级")["plan"]
    pid = plan["plan_id"]
    repo.lock_plan(pid)
    with pytest.raises(ValueError, match="锁定"):
        repo.update_plan(pid, goal="过六级")
    with pytest.raises(ValueError, match="锁定"):
        repo.update_plan(pid, daily_minutes=120)
    updated = repo.update_plan(pid, progress=30, current_status="稳步推进")
    assert updated["progress"] == 30
    assert updated["locked"] is True


def test_add_milestone_blocked_on_locked(tmp_path):
    repo = _repo(tmp_path)
    plan = repo.create_plan(title="写作任务")["plan"]
    repo.lock_plan(plan["plan_id"])
    with pytest.raises(ValueError, match="锁定"):
        repo.add_milestone(plan["plan_id"], "新阶段")


def test_adjustment_request_requires_reason_and_core_field(tmp_path):
    repo = _repo(tmp_path)
    plan = repo.create_plan(title="周度计划")["plan"]
    pid = plan["plan_id"]
    repo.lock_plan(pid)
    with pytest.raises(ValueError, match="reason"):
        repo.create_adjustment_request(pid, reason="", goal="x")
    with pytest.raises(ValueError, match="核心字段"):
        repo.create_adjustment_request(pid, reason="想改进度", progress=50)


def test_adjustment_request_and_accept(tmp_path):
    repo = _repo(tmp_path)
    plan = repo.create_plan(title="读书计划", goal="读完十本", end_date="2026-12-31")["plan"]
    pid = plan["plan_id"]
    repo.lock_plan(pid)
    result = repo.create_adjustment_request(
        pid, reason="节奏太满，延长两周", end_date="2027-01-15"
    )
    adjustment = result["adjustment"]
    assert adjustment["status"] == "pending"
    # 未裁决前不生效
    assert repo.get_plan(pid)["end_date"] == plan["end_date"]
    confirmed = repo.confirm_adjustment(
        pid, adjustment["adjustment_id"], accepted=True, decision="同意延期"
    )
    assert confirmed["already_resolved"] is False
    current = repo.get_plan(pid)
    assert current["end_date"] == "2027-01-15T00:00:00"
    assert current["pending_adjustments"][0]["status"] == "accepted"
    assert current["decision_log"][-1]["text"].startswith("调整已确认")


def test_adjustment_reject_keeps_plan(tmp_path):
    repo = _repo(tmp_path)
    plan = repo.create_plan(title="健身计划", goal="增肌")["plan"]
    pid = plan["plan_id"]
    repo.lock_plan(pid)
    result = repo.create_adjustment_request(pid, reason="想换目标", goal="减脂")
    repo.confirm_adjustment(
        pid, result["adjustment"]["adjustment_id"], accepted=False, decision="维持原目标"
    )
    current = repo.get_plan(pid)
    assert current["goal"] == "增肌"
    assert current["pending_adjustments"][0]["status"] == "rejected"


def test_duplicate_confirm_returns_already_resolved(tmp_path):
    repo = _repo(tmp_path)
    plan = repo.create_plan(title="周度计划")["plan"]
    pid = plan["plan_id"]
    repo.lock_plan(pid)
    result = repo.create_adjustment_request(pid, reason="延后", end_date="2027-06-01")
    adj_id = result["adjustment"]["adjustment_id"]
    repo.confirm_adjustment(pid, adj_id, accepted=True)
    again = repo.confirm_adjustment(pid, adj_id, accepted=True)
    assert again["already_resolved"] is True


def test_tool_flow_lock_adjust_confirm(tmp_path):
    """工具层全流程：task_create(routine) → task_lock → task_update 被拒 → 调整请求 → 确认生效"""
    from aion_agent.tools import ToolExecutor, ToolRegistry, register_planner_tools

    plan = JsonPlanRepo(persist_dir=str(tmp_path))
    reg = ToolRegistry()
    register_planner_tools(reg, plan, cognitive_repo=None, user_id="u1")
    exec_ = ToolExecutor(reg)

    def run(coro):
        return asyncio.run(coro)

    res = run(exec_.execute(
        "task_create", {"title": "每日固定节奏", "routine": _routine()}
    ))
    assert res.success, res.error
    pid = res.data["plan"]["plan_id"]
    assert res.data["plan"]["routine"]["recurrence"] == "daily"

    res = run(exec_.execute("task_lock", {"plan_id": pid}))
    assert res.success, res.error
    assert res.data["plan"]["locked"] is True

    res = run(exec_.execute("task_update", {"plan_id": pid, "goal": "改成别的目标"}))
    assert res.success is False
    assert "锁定" in res.error

    res = run(exec_.execute(
        "task_adjust_request", {"plan_id": pid, "reason": "目标变更", "goal": "垂直化 MVP"}
    ))
    assert res.success, res.error
    adj_id = res.data["adjustment"]["adjustment_id"]

    res = run(exec_.execute(
        "task_adjust_confirm",
        {"plan_id": pid, "adjustment_id": adj_id, "accepted": True, "reason": "同意"},
    ))
    assert res.success, res.error
    assert res.data["plan"]["goal"] == "垂直化 MVP"
