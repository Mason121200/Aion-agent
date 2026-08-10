"""每日执行单测试：生成 / 块完成 / 收尾总结 / 同日去重"""

import asyncio
import sys

sys.path.insert(0, ".")

from aion_agent.study.study_repo import JsonStudyRepo  # noqa: E402


def _repo(tmp_path):
    return JsonStudyRepo(persist_dir=str(tmp_path))


def _blocks():
    return [
        {"name": "上午审查", "slot": "上午 3-4h", "focus": "学习审查线", "minutes": 180},
        {"name": "中午内容", "slot": "中午 1h", "focus": "内容产出线", "minutes": 60},
        {"name": "下午任务", "slot": "下午 1h", "focus": "项目推进线", "minutes": 60},
    ]


def test_create_daily_and_same_day_dedup(tmp_path):
    repo = _repo(tmp_path)
    r1 = repo.create_daily(
        date="2026-08-10", focus="审查", output_goal="决策记录", blocks=_blocks()
    )
    assert r1["reused"] is False
    entry = r1["daily"]
    assert entry["date"] == "2026-08-10"
    assert entry["status"] == "active"
    assert len(entry["blocks"]) == 3
    assert entry["blocks"][0]["done"] is False
    r2 = repo.create_daily(
        date="2026-08-10", focus="审查", output_goal="决策记录", blocks=_blocks()
    )
    assert r2["reused"] is True
    assert r2["daily"]["daily_id"] == entry["daily_id"]


def test_daily_block_done_and_list(tmp_path):
    repo = _repo(tmp_path)
    entry = repo.create_daily(date="2026-08-10", blocks=_blocks())["daily"]
    block = entry["blocks"][0]
    updated = repo.update_daily_block(
        entry["daily_id"], block["block_id"], done=True, note="完成"
    )
    assert updated["blocks"][0]["done"] is True
    assert updated["blocks"][0]["note"] == "完成"
    items = repo.list_dailies(date="2026-08-10")
    assert len(items) == 1
    assert repo.update_daily_block("daily_nope", "blk_x") is None


def test_complete_daily(tmp_path):
    repo = _repo(tmp_path)
    entry = repo.create_daily(date="2026-08-10", blocks=_blocks())["daily"]
    done = repo.complete_daily(
        entry["daily_id"],
        summary="产出物完成，卡在投递渠道",
        output_done=True,
        blockers=["平台账号未注册"],
        tomorrow_focus="审查 _semantic_dedup",
    )
    assert done["status"] == "completed"
    assert done["output_done"] is True
    assert done["blockers"] == ["平台账号未注册"]
    assert done["tomorrow_focus"].startswith("审查")
    assert done["completed_at"] is not None
    assert repo.complete_daily("daily_nope", summary="x") is None


def test_tool_flow_daily_start_block_summary(tmp_path):
    """工具层全流程：daily_start → daily_block_done → daily_summary（写记忆笔记）"""
    from aion_agent.storage.in_memory_cognitive_repo import InMemoryCognitiveRepo
    from aion_agent.tools import ToolExecutor, ToolRegistry, register_study_tools

    cog = InMemoryCognitiveRepo(persist_dir=str(tmp_path))
    study = JsonStudyRepo(persist_dir=str(tmp_path))
    reg = ToolRegistry()
    register_study_tools(reg, study, cognitive_repo=cog, user_id="u1")
    exec_ = ToolExecutor(reg)

    def run(coro):
        return asyncio.run(coro)

    res = run(exec_.execute("daily_start", {
        "date": "2026-08-10",
        "focus": "学习审查线",
        "output_goal": "第一份决策记录",
        "delivery_goal": "投 1 个练手岗位",
        "blocks": _blocks(),
    }))
    assert res.success, res.error
    daily = res.data["daily"]
    assert daily["output_goal"] == "第一份决策记录"

    block_id = daily["blocks"][0]["block_id"]
    res = run(exec_.execute("daily_block_done", {
        "daily_id": daily["daily_id"], "block_id": block_id, "done": True,
    }))
    assert res.success, res.error

    res = run(exec_.execute("daily_summary", {
        "daily_id": daily["daily_id"],
        "summary": "审完 process 和 _make_key",
        "output_done": True,
        "blockers": [],
        "tomorrow_focus": "审 _semantic_dedup",
    }))
    assert res.success, res.error
    assert res.data["daily"]["status"] == "completed"
