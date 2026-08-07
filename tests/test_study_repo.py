"""学习场景仓库测试 —— 长期规划 / 进度追溯 / 动态调整 / 资料 / 提醒"""

import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, ".")

from aion_agent.study.study_repo import JsonStudyRepo  # noqa: E402
from aion_agent.tools.registry import ToolRegistry  # noqa: E402
from aion_agent.tools.study_tools import register_study_tools  # noqa: E402


def _repo(tmp_path):
    return JsonStudyRepo(persist_dir=str(tmp_path))


def test_create_plan_with_milestones_and_dedup(tmp_path):
    repo = _repo(tmp_path)
    r1 = repo.create_plan(
        title="三个月通过英语四级",
        subject="英语",
        goal="四级 500+",
        end_date=(datetime.now() + timedelta(days=90)).date().isoformat(),
        daily_minutes=30,
        milestones=[{"title": "打基础", "due_date": "2026-09-30"}, {"title": "真题冲刺"}],
    )
    assert r1["reused"] is False
    plan = r1["plan"]
    assert len(plan["milestones"]) == 2
    assert plan["status"] == "active"
    # 同标题活跃计划去重
    r2 = repo.create_plan(title="三个月通过英语四级", subject="英语")
    assert r2["reused"] is True
    assert r2["plan"]["plan_id"] == plan["plan_id"]


def test_progress_tracking_by_milestone_and_time(tmp_path):
    repo = _repo(tmp_path)
    r = repo.create_plan(
        title="备考计划",
        end_date=(datetime.now() + timedelta(days=30)).date().isoformat(),
        daily_minutes=30,
        milestones=[{"title": "阶段一"}, {"title": "阶段二"}],
    )
    pid = r["plan"]["plan_id"]
    repo.log_session(subject="英语", minutes=30, plan_id=pid)
    repo.complete_milestone(pid, r["plan"]["milestones"][0]["milestone_id"])
    detail = repo.plan_detail(pid)
    info = detail["progress_info"]
    assert info["milestones_done"] == 1
    assert info["milestones_total"] == 2
    assert info["total_minutes"] == 30
    assert info["progress"] == 50  # 里程碑推算优先


def test_dynamic_adjustment_with_decision_log(tmp_path):
    repo = _repo(tmp_path)
    plan = repo.create_plan(title="读书计划")["plan"]
    updated = repo.update_plan(
        plan["plan_id"],
        decision="考试提前，截止提前两周",
        end_date=(datetime.now() + timedelta(days=20)).date().isoformat(),
        daily_minutes=45,
    )
    assert updated["daily_minutes"] == 45
    assert len(updated["decision_log"]) == 2  # 创建 + 追加
    assert updated["decision_log"][-1]["text"] == "考试提前，截止提前两周"
    # 决策记录只追加，不覆盖
    repo.update_plan(plan["plan_id"], decision="进度正常")
    assert len(repo.get_plan(plan["plan_id"])["decision_log"]) == 3


def test_reminders_due_upcoming_complete(tmp_path):
    repo = _repo(tmp_path)
    repo.create_reminder(
        title="背单词",
        remind_at=(datetime.now() - timedelta(hours=1)).isoformat(),
    )
    repo.create_reminder(
        title="复习英语",
        remind_at=(datetime.now() + timedelta(hours=3)).isoformat(),
    )
    assert len(repo.due_reminders()) == 1
    assert len(repo.upcoming_reminders()) == 1
    rid = repo.due_reminders()[0]["reminder_id"]
    assert repo.complete_reminder(rid) is True
    assert len(repo.due_reminders()) == 0
    assert len(repo.list_reminders(include_done=True)) == 2


def test_materials_search(tmp_path):
    repo = _repo(tmp_path)
    repo.add_material(title="英语高频词 3000", subject="英语", source="https://example.com", tags=["词汇"])
    assert len(repo.list_materials(query="高频词")) == 1
    assert len(repo.list_materials(query="不存在")) == 0
    assert len(repo.list_materials(subject="英语")) == 1


def test_overview_and_persistence(tmp_path):
    repo = _repo(tmp_path)
    repo.create_plan(title="健身计划", daily_minutes=20)
    repo.log_session(subject="跑步", minutes=25)
    ov = repo.overview()
    assert len(ov["active_plans"]) == 1
    assert ov["today_minutes"] == 25
    # 重启恢复
    repo2 = JsonStudyRepo(persist_dir=str(tmp_path))
    assert len(repo2.list_plans()) == 1
    assert repo2.today_minutes() == 25


def test_register_study_tools_registers_13_tools(tmp_path):
    repo = _repo(tmp_path)
    registry = ToolRegistry()
    register_study_tools(registry, repo, user_id="u1")
    names = {
        "plan_create", "plan_list", "plan_read", "plan_update",
        "plan_checkin", "plan_archive", "log_study_session",
        "add_study_material", "search_study_materials",
        "create_reminder", "list_reminders", "complete_reminder",
        "get_study_status",
    }
    assert names.issubset(set(registry._tools.keys()))
