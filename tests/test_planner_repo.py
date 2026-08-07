"""通用规划器仓库测试：长期任务 / 进度追溯 / 动态调整 / 持久化"""

import sys
from datetime import datetime, timedelta

sys.path.insert(0, ".")

from aion_agent.planner.planner_repo import JsonPlanRepo  # noqa: E402


def _repo(tmp_path):
    return JsonPlanRepo(persist_dir=str(tmp_path))


def test_create_plan_with_milestones_and_dedup(tmp_path):
    repo = _repo(tmp_path)
    r1 = repo.create_plan(
        title="三个月完成项目上线",
        goal="MVP 上线并跑通付费链路",
        end_date=(datetime.now() + timedelta(days=90)).date().isoformat(),
        daily_minutes=60,
        milestones=[{"title": "需求梳理", "due_date": "2026-09-30"}, {"title": "开发冲刺"}],
    )
    assert r1["reused"] is False
    plan = r1["plan"]
    assert len(plan["milestones"]) == 2
    assert plan["status"] == "active"
    assert plan["plan_id"].startswith("task_")
    # 同标题活跃任务去重
    r2 = repo.create_plan(title="三个月完成项目上线", goal="不同目标")
    assert r2["reused"] is True
    assert r2["plan"]["plan_id"] == plan["plan_id"]


def test_progress_tracking_by_milestone(tmp_path):
    repo = _repo(tmp_path)
    r = repo.create_plan(
        title="备考计划",
        end_date=(datetime.now() + timedelta(days=30)).date().isoformat(),
        milestones=[{"title": "阶段一"}, {"title": "阶段二"}],
    )
    pid = r["plan"]["plan_id"]
    repo.complete_milestone(pid, r["plan"]["milestones"][0]["milestone_id"])
    detail = repo.plan_detail(pid)
    info = detail["progress_info"]
    assert info["milestones_done"] == 1
    assert info["milestones_total"] == 2
    assert info["progress"] == 50


def test_dynamic_adjustment_with_decision_log(tmp_path):
    repo = _repo(tmp_path)
    plan = repo.create_plan(title="读书计划")["plan"]
    updated = repo.update_plan(
        plan["plan_id"],
        decision="发现时间不足，延期两周",
        end_date=(datetime.now() + timedelta(days=14)).date().isoformat(),
        progress=30,
        current_status="节奏偏慢，已调整",
        next_steps=["压缩每天阅读时长到 40 分钟", "周末补进度"],
    )
    assert updated["progress"] == 30
    assert len(updated["decision_log"]) == 2
    assert updated["decision_log"][-1]["text"] == "发现时间不足，延期两周"
    assert updated["next_steps"][0].startswith("压缩")


def test_milestone_add_and_archive(tmp_path):
    repo = _repo(tmp_path)
    plan = repo.create_plan(title="写作任务")["plan"]
    pid = plan["plan_id"]
    repo.add_milestone(pid, "写大纲")
    assert len(repo.get_plan(pid)["milestones"]) == 1
    repo.archive_plan(pid)
    assert repo.get_plan(pid)["status"] == "archived"
    assert repo.list_plans(status="active") == []


def test_persistence_roundtrip(tmp_path):
    repo = _repo(tmp_path)
    repo.create_plan(title="持久化任务", goal="重启后仍可追溯")
    repo2 = JsonPlanRepo(persist_dir=str(tmp_path))
    plans = repo2.list_plans()
    assert len(plans) == 1
    assert plans[0]["title"] == "持久化任务"
    assert (tmp_path / "plans.json").exists()
