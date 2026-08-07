"""完整规划方案落盘测试：plan_text / 验收标准 / 里程碑步骤详情

保证 agent 规划出的完整方案可持久化，作为后续追踪进度、更新与维护的数据基础。
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


PLAN_TEXT = (
    "## 背景\n三个月完成项目上线。\n"
    "## 阶段一（第1-4周）\n- 需求梳理\n- 架构设计\n"
    "## 阶段二（第5-8周）\n- 核心开发\n- 联调测试\n"
    "## 每日安排\n每天 2 小时，晚上 9 点后。"
)


def test_task_create_persists_plan_text_and_acceptance(tmp_path):
    cog, _, exec_ = _env(tmp_path)
    res = run(exec_.execute("task_create", {
        "title": "三个月完成项目上线",
        "goal": "上线 MVP 并跑通付费链路",
        "plan_text": PLAN_TEXT,
        "acceptance_criteria": ["功能完整", "通过验收测试", "成功上线发布"],
    }))
    assert res.success
    plan = res.data["plan"]
    assert plan["plan_text"] == PLAN_TEXT
    assert plan["acceptance_criteria"] == ["功能完整", "通过验收测试", "成功上线发布"]
    # 落盘到磁盘
    persisted = JsonPlanRepo(persist_dir=str(tmp_path)).get_plan(plan["plan_id"])
    assert persisted["plan_text"] == PLAN_TEXT
    assert len(persisted["acceptance_criteria"]) == 3


def test_task_create_persists_milestone_steps(tmp_path):
    cog, _, exec_ = _env(tmp_path)
    res = run(exec_.execute("task_create", {
        "title": "备考计划",
        "milestones": [
            {
                "title": "阶段一 打基础",
                "steps": ["Python 语法", "numpy 入门", "刷题 50 道"],
                "output": "基础笔记",
                "acceptance": "能独立完成简单算法题",
            },
            {"title": "阶段二 冲刺"},
        ],
    }))
    assert res.success
    plan = res.data["plan"]
    ms = plan["milestones"]
    assert len(ms) == 2
    assert ms[0]["steps"] == ["Python 语法", "numpy 入门", "刷题 50 道"]
    assert ms[0]["output"] == "基础笔记"
    assert ms[0]["acceptance"] == "能独立完成简单算法题"
    assert ms[1]["steps"] == []


def test_task_read_returns_plan_detail(tmp_path):
    cog, _, exec_ = _env(tmp_path)
    p = run(exec_.execute("task_create", {
        "title": "写作计划",
        "plan_text": PLAN_TEXT,
        "acceptance_criteria": ["完稿", "发布"],
        "milestones": [{"title": "收集素材", "steps": ["列提纲"]}],
    })).data["plan"]
    res = run(exec_.execute("task_read", {"plan_id": p["plan_id"]}))
    assert res.success
    content = res.data["content"]
    assert "完整方案" in content
    assert "需求梳理" in content
    assert "验收标准" in content
    assert "收集素材" in content
    assert "列提纲" in content


def test_task_update_rewrites_plan_text(tmp_path):
    cog, _, exec_ = _env(tmp_path)
    p = run(exec_.execute("task_create", {
        "title": "健身计划",
        "plan_text": "旧方案",
    })).data["plan"]
    res = run(exec_.execute("task_update", {
        "plan_id": p["plan_id"],
        "plan_text": "新方案：每周 4 练，阶段拆分如下……",
    }))
    assert res.success
    plan = res.data["plan"]
    assert plan["plan_text"].startswith("新方案")
    persisted = JsonPlanRepo(persist_dir=str(tmp_path)).get_plan(p["plan_id"])
    assert persisted["plan_text"].startswith("新方案")
