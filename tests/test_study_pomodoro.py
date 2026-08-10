"""番茄钟测试：启动 / 单实例约束 / 停止自动记时长 / 今日统计 / 工具层全流程"""

import asyncio
import sys

sys.path.insert(0, ".")

from aion_agent.study.study_repo import JsonStudyRepo  # noqa: E402


def _repo(tmp_path):
    return JsonStudyRepo(persist_dir=str(tmp_path))


def test_start_pomodoro_and_single_running(tmp_path):
    repo = _repo(tmp_path)
    r1 = repo.start_pomodoro(minutes=25, block_name="审查 filter")
    assert r1["reused"] is False
    p = r1["pomodoro"]
    assert p["status"] == "running"
    assert p["minutes"] == 25
    assert p["block_name"] == "审查 filter"

    r2 = repo.start_pomodoro(minutes=10)
    assert r2["reused"] is True
    assert r2["pomodoro"]["pomodoro_id"] == p["pomodoro_id"]


def test_stop_pomodoro_logs_focus_session(tmp_path):
    repo = _repo(tmp_path)
    repo.start_pomodoro(minutes=25, plan_id="pl_1", block_name="内容块")
    done = repo.stop_pomodoro()
    assert done is not None
    assert done["status"] == "completed"
    assert done["elapsed_minutes"] >= 1
    assert done["stopped_at"] is not None

    assert repo.stop_pomodoro() is None
    sessions = repo.list_sessions(limit=50)
    assert any(s["subject"] == "番茄钟专注" for s in sessions)
    assert repo.today_minutes() >= 1


def test_pomodoro_status_today_counts(tmp_path):
    repo = _repo(tmp_path)
    repo.start_pomodoro(minutes=25)
    running = repo.pomodoro_status()
    assert running["running"] is not None
    assert running["remaining_minutes"] is not None

    done = repo.stop_pomodoro()
    st = repo.pomodoro_status()
    assert st["running"] is None
    assert st["remaining_minutes"] is None
    assert st["today_done"] == 1
    assert st["today_focus_minutes"] == done["elapsed_minutes"]


def test_tool_flow_pomodoro_start_status_stop(tmp_path):
    """工具层全流程：pomodoro_start → pomodoro_status → pomodoro_stop"""
    from aion_agent.storage.in_memory_cognitive_repo import InMemoryCognitiveRepo
    from aion_agent.tools import ToolExecutor, ToolRegistry, register_study_tools

    cog = InMemoryCognitiveRepo(persist_dir=str(tmp_path))
    study = JsonStudyRepo(persist_dir=str(tmp_path))
    reg = ToolRegistry()
    register_study_tools(reg, study, cognitive_repo=cog, user_id="u1")
    exec_ = ToolExecutor(reg)

    def run(coro):
        return asyncio.run(coro)

    res = run(exec_.execute("pomodoro_start", {
        "minutes": 25, "block_name": "上午审查",
    }))
    assert res.success, res.error
    p = res.data["pomodoro"]
    assert p["status"] == "running"

    res = run(exec_.execute("pomodoro_status", {}))
    assert res.success, res.error
    assert res.data["running"]["pomodoro_id"] == p["pomodoro_id"]

    res = run(exec_.execute("pomodoro_stop", {}))
    assert res.success, res.error
    assert res.data["pomodoro"]["status"] == "completed"

    res = run(exec_.execute("pomodoro_status", {}))
    assert res.success, res.error
    assert res.data["running"] is None
    assert res.data["today_done"] == 1