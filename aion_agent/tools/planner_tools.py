"""通用规划器工具集 —— 长期任务规划 / 进度追溯 / 动态调整

参考 study_tools 的「长期计划笔记体系」范式，但不绑定学习场景：
- task_create / task_list / task_read / task_update / task_checkin / task_archive
- task_milestone / task_complete_milestone（里程碑管理）
- 「活跃任务」只保留最新状态，决策记录只追加（进度追溯）
- 任务创建 / 检视时同步写入认知仓库（state + 笔记），让任务进入长期记忆

实现约定：
- handler 为同步函数（ToolExecutor 在后台线程调用），内部用 asyncio.run 调仓库异步方法
- 返回统一 JSON 可序列化 dict，content 字段为给 LLM 阅读的自然语言结果
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from aion_agent.core.entities.agent_state import AgentState
from aion_agent.core.entities.cognitive_triple import CognitiveTriple, Dimension
from aion_agent.core.entities.note import Note, NoteType
from aion_agent.core.ports.i_tool_registry import IToolRegistry

logger = logging.getLogger(__name__)

_PLAN_STATUSES = ["active", "paused", "completed", "archived"]
_PRIORITIES = ["low", "normal", "high", "urgent"]


def _fmt_dt(value, with_time: bool = True) -> str:
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return str(value)
    return dt.strftime("%Y-%m-%d %H:%M" if with_time else "%Y-%m-%d")


def _make_handlers(
    repo, cognitive_repo, user_id: str
) -> Dict[str, Callable[[Dict[str, Any]], dict]]:
    """基于任务仓库与认知仓库构建 8 个通用规划工具 handler"""

    # ---------------- 任务 ----------------

    def _parse_end(value):
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except (ValueError, TypeError):
            return None

    def _link_task_cognition(plan: dict) -> dict:
        """任务创建后建立 state + triple 关联，并把关联键写回 plan"""
        if cognitive_repo is None:
            return plan
        if plan.get("state_id") and plan.get("rel_id"):
            return plan
        expires = _parse_end(plan.get("end_date"))
        state_id = asyncio.run(cognitive_repo.save_state(AgentState(
            user_id=user_id,
            task_id=plan["plan_id"],
            state_type="user",
            state_name="task_plan",
            description=f"长期任务：{plan['title']}（{plan.get('goal') or '进行中'}）",
            expires_at=expires,
            priority=5,
        )))
        rel_id = asyncio.run(cognitive_repo.save_triple(CognitiveTriple(
            subject="我",
            predicate="正在执行长期任务",
            object=plan["title"],
            dimension=Dimension.STATE,
            user_id=user_id,
            confidence=0.85,
            source="task_tool",
            expires_at=expires,
        )))
        updated = repo.update_plan(plan["plan_id"], state_id=state_id, rel_id=rel_id)
        if updated is not None:
            return updated
        plan["state_id"] = state_id
        plan["rel_id"] = rel_id
        return plan

    def _sync_task_cognition(
        plan: dict,
        *,
        release_reason: Optional[str] = None,
        title: Optional[str] = None,
        goal: Optional[str] = None,
        end_date: Optional[str] = None,
        progress: Optional[int] = None,
        touch: bool = False,
    ) -> None:
        """plan 变更后同步 state / triple

        - release_reason（completed/cancelled）：释放 state + 软删除 triple
        - title/goal 变化：更新 state 描述与 triple 宾语
        - end_date 变化：更新双方过期时间
        - touch / progress：刷新 state.last_updated_at（体现活跃）
        """
        if cognitive_repo is None:
            return
        state_id = plan.get("state_id")
        rel_id = plan.get("rel_id")
        if release_reason:
            if state_id:
                asyncio.run(cognitive_repo.release_state(state_id, reason=release_reason))
            if rel_id:
                asyncio.run(cognitive_repo.delete_triple(rel_id, soft=True))
            return
        if state_id:
            state = asyncio.run(cognitive_repo.get_state(state_id))
            if state is not None:
                if title is not None or goal is not None:
                    state.description = (
                        f"长期任务：{title if title is not None else plan.get('title')}"
                        f"（{goal if goal is not None else plan.get('goal') or '进行中'}）"
                    )
                if end_date is not None:
                    state.expires_at = _parse_end(end_date)
                if touch or progress is not None:
                    state.last_updated_at = datetime.now()
                asyncio.run(cognitive_repo.save_state(state))
        if rel_id:
            triple = asyncio.run(cognitive_repo.get_triple(rel_id))
            if triple is not None:
                if title is not None and title != triple.object:
                    asyncio.run(cognitive_repo.update_triple(rel_id, object_=title))
                elif end_date is not None:
                    triple.expires_at = _parse_end(end_date)
                    asyncio.run(cognitive_repo.save_triple(triple))

    def _task_create(args: Dict[str, Any]) -> dict:
        title = str(args.get("title") or "").strip()
        if not title:
            raise ValueError("缺少参数 title（任务标题，如：三个月完成项目上线）")
        goal = str(args.get("goal") or "").strip()
        why = str(args.get("why") or "").strip()
        tags = [str(t).strip() for t in (args.get("tags") or []) if str(t).strip()]
        result = repo.create_plan(
            title=title,
            goal=goal,
            why=why,
            tags=tags,
            end_date=args.get("end_date"),
            daily_minutes=int(args.get("daily_minutes") or 0),
            priority=str(args.get("priority") or "normal").strip(),
            milestones=args.get("milestones") or [],
            plan_text=str(args.get("plan_text") or "").strip(),
            acceptance_criteria=args.get("acceptance_criteria"),
        )
        plan = result["plan"]
        if result.get("reused"):
            return {
                "content": f"已存在相同标题的活跃任务，返回现有任务：{title}（{plan['plan_id']}）",
                "plan": plan,
            }
        plan = _link_task_cognition(plan)
        return {"content": f"已创建长期任务：{title}（{plan['plan_id']}）", "plan": plan}

    def _task_list(args: Dict[str, Any]) -> dict:
        status = str(args.get("status") or "").strip() or None
        tasks = repo.list_plans(status=status)
        if not tasks:
            return {"content": "当前没有任务。可以让我帮你规划一个，例如：帮我制定一个三个月完成项目上线的任务计划。"}
        lines = []
        for p in tasks:
            info = p.get("progress_info") or {}
            days = info.get("days_left")
            days_text = f"，剩 {days} 天" if days is not None else ""
            lines.append(
                f"- {p['title']} [{p['status']}] 进度 {info.get('progress', 0)}%{days_text}（{p['plan_id']}）"
            )
        return {"content": "任务计划：\n" + "\n".join(lines), "plans": tasks}

    def _task_read(args: Dict[str, Any]) -> dict:
        plan_id = str(args.get("plan_id") or "").strip()
        if not plan_id:
            raise ValueError("缺少参数 plan_id")
        detail = repo.plan_detail(plan_id)
        if detail is None:
            raise ValueError(f"未找到任务：{plan_id}")
        info = detail.get("progress_info") or {}
        days = info.get("days_left")
        days_text = f"，剩 {days} 天" if days is not None else ""
        parts = [
            f"任务：{detail['title']}（状态 {detail['status']}，进度 {info.get('progress', 0)}%{days_text}）",
        ]
        if detail.get("goal"):
            parts.append(f"目标：{detail['goal']}")
        if detail.get("why"):
            parts.append(f"为什么：{detail['why']}")
        if detail.get("end_date"):
            parts.append(f"截止：{_fmt_dt(detail['end_date'], with_time=False)}")
        if detail.get("daily_minutes"):
            parts.append(f"每日目标：{detail['daily_minutes']} 分钟")
        pt = detail.get("plan_text") or ""
        if pt:
            parts.append(f"完整方案：\n{pt}")
        ac = detail.get("acceptance_criteria") or []
        if ac:
            parts.append("验收标准：" + "；".join(f"{i + 1}. {a}" for i, a in enumerate(ac)))
        ms = detail.get("milestones") or []
        if ms:
            parts.append("里程碑：")
            for m in ms:
                line = f"  {'✅' if m.get('done') else '⬜'} {m['title']}"
                if m.get("due_date"):
                    line += f"（{_fmt_dt(m.get('due_date'), with_time=False)}）"
                parts.append(line)
                steps = m.get("steps") or []
                for si, s in enumerate(steps):
                    parts.append(f"    {si + 1}. {s}")
                if m.get("output"):
                    parts.append(f"    产出：{m['output']}")
                if m.get("acceptance"):
                    parts.append(f"    验收：{m['acceptance']}")
        if detail.get("current_status"):
            parts.append(f"当前状态：{detail['current_status']}")
        ns = detail.get("next_steps") or []
        if ns:
            parts.append("下一步：" + "；".join(f"{i + 1}. {x}" for i, x in enumerate(ns)))
        dl = detail.get("decision_log") or []
        if dl:
            parts.append("决策记录：" + "；".join(
                f"{_fmt_dt(d.get('ts'))} {d['text']}" for d in dl[-10:]
            ))
        return {"content": "\n".join(parts), "plan": detail}

    def _task_update(args: Dict[str, Any]) -> dict:
        plan_id = str(args.get("plan_id") or "").strip()
        if not plan_id:
            raise ValueError("缺少参数 plan_id")
        fields = {}
        for k in ("title", "goal", "why", "end_date", "daily_minutes", "priority",
                  "status", "progress", "current_status", "next_steps", "tags",
                  "plan_text", "acceptance_criteria"):
            if k in args and args.get(k) is not None:
                fields[k] = args[k]
        plan = repo.update_plan(
            plan_id, decision=str(args.get("decision") or "").strip() or None, **fields
        )
        if plan is None:
            raise ValueError(f"未找到任务：{plan_id}")
        status = fields.get("status")
        if status in ("completed", "archived"):
            _sync_task_cognition(
                plan,
                release_reason="completed" if status == "completed" else "cancelled",
            )
        else:
            _sync_task_cognition(
                plan,
                title=fields.get("title"),
                goal=fields.get("goal"),
                end_date=fields.get("end_date"),
                progress=fields.get("progress"),
                touch=True,
            )
        info = repo._plan_progress(plan)
        return {
            "content": f"已更新任务：{plan['title']}（进度 {info['progress']}%）",
            "plan": {**plan, "progress_info": info},
        }

    def _task_checkin(args: Dict[str, Any]) -> dict:
        plan_id = str(args.get("plan_id") or "").strip()
        if not plan_id:
            raise ValueError("缺少参数 plan_id")
        summary = str(args.get("summary") or "").strip()
        if not summary:
            raise ValueError("缺少参数 summary（本次检视总结，如：已完成里程碑一，卡在环境配置）")
        fields = {}
        if args.get("progress") is not None:
            fields["progress"] = args["progress"]
        plan = repo.update_plan(
            plan_id,
            decision=f"检视：{summary}",
            **fields,
        )
        if plan is None:
            raise ValueError(f"未找到任务：{plan_id}")
        if cognitive_repo is not None:
            asyncio.run(cognitive_repo.save_note(Note(
                user_id=user_id,
                note_type=NoteType.TASK,
                title=f"{plan['title']} · 检视",
                content=summary,
                related_session_id=None,
            )))
        _sync_task_cognition(plan, touch=True)
        info = repo._plan_progress(plan)
        return {
            "content": f"已记录检视：{plan['title']}（进度 {info['progress']}%）",
            "plan": {**plan, "progress_info": info},
        }

    def _task_archive(args: Dict[str, Any]) -> dict:
        plan_id = str(args.get("plan_id") or "").strip()
        if not plan_id:
            raise ValueError("缺少参数 plan_id")
        plan = repo.archive_plan(plan_id)
        if plan is None:
            raise ValueError(f"未找到任务：{plan_id}")
        _sync_task_cognition(plan, release_reason="cancelled")
        return {"content": f"已归档任务：{plan['title']}", "plan": plan}

    # ---------------- 里程碑 ----------------

    def _task_milestone(args: Dict[str, Any]) -> dict:
        plan_id = str(args.get("plan_id") or "").strip()
        title = str(args.get("title") or "").strip()
        if not plan_id or not title:
            raise ValueError("缺少参数 plan_id / title")
        plan = repo.add_milestone(plan_id, title, due_date=args.get("due_date"))
        if plan is None:
            raise ValueError(f"未找到任务：{plan_id}")
        return {"content": f"已添加里程碑：{title}", "plan": plan}

    def _task_complete_milestone(args: Dict[str, Any]) -> dict:
        plan_id = str(args.get("plan_id") or "").strip()
        milestone_id = str(args.get("milestone_id") or "").strip()
        if not plan_id or not milestone_id:
            raise ValueError("缺少参数 plan_id / milestone_id")
        plan = repo.complete_milestone(plan_id, milestone_id)
        if plan is None:
            raise ValueError(f"未找到任务：{plan_id} 或里程碑：{milestone_id}")
        _sync_task_cognition(plan, touch=True)
        info = repo._plan_progress(plan)
        return {
            "content": f"已完成里程碑，任务进度 {info['progress']}%",
            "plan": {**plan, "progress_info": info},
        }

    return {
        "task_create": _task_create,
        "task_list": _task_list,
        "task_read": _task_read,
        "task_update": _task_update,
        "task_checkin": _task_checkin,
        "task_archive": _task_archive,
        "task_milestone": _task_milestone,
        "task_complete_milestone": _task_complete_milestone,
    }


def _tool(name: str, description: str, properties: Dict[str, Any], required: List[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


_TASK_TOOLS = [
    _tool(
        "task_create",
        "【通用规划器】创建长期任务：目标 / 截止日期 / 每日时长 / 里程碑。"
        "当用户提出需要长期推进的目标（项目、备考、健身、写作等）时调用。",
        {
            "title": {"type": "string", "description": "任务标题，如：三个月完成项目上线"},
            "goal": {"type": "string", "description": "目标描述（想达到什么、验收标准）"},
            "why": {"type": "string", "description": "为什么做（动机，帮助未来对齐方向）"},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "标签（可选）"},
            "end_date": {"type": "string", "description": "截止日期（可选），ISO 格式，如 2026-11-30"},
            "daily_minutes": {"type": "integer", "description": "每日目标时长（分钟，可选）"},
            "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"], "description": "优先级（可选）"},
            "plan_text": {
                "type": "string",
                "description": "完整规划方案（Markdown/分点）：背景、分阶段步骤、每日安排、资源与风险等，"
                "必须完整落盘以便后续追踪与调整",
            },
            "acceptance_criteria": {
                "type": "array",
                "items": {"type": "string"},
                "description": "整体验收标准（可选）：满足哪些条件才算完成",
            },
            "milestones": {
                "type": "array",
                "description": "里程碑 / 阶段（可选），每阶段可含步骤/产出/验收",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "due_date": {"type": "string", "description": "ISO 日期"},
                        "steps": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "本阶段的具体执行步骤",
                        },
                        "output": {"type": "string", "description": "本阶段的产出物"},
                        "acceptance": {"type": "string", "description": "本阶段的验收标准"},
                    },
                },
            },
        },
        ["title"],
    ),
    _tool(
        "task_list",
        "【通用规划器】列出任务（含进度、剩余天数摘要），可按状态过滤",
        {
            "status": {"type": "string", "enum": ["active", "paused", "completed", "archived"], "description": "按状态过滤（可选）"},
        },
        [],
    ),
    _tool(
        "task_read",
        "【通用规划器】查看某个任务的完整详情：目标 / 里程碑 / 当前状态 / 下一步 / 决策记录（进度追溯）",
        {"plan_id": {"type": "string", "description": "任务 ID（task_ 开头）"}},
        ["plan_id"],
    ),
    _tool(
        "task_update",
        "【通用规划器】动态调整任务：修改目标 / 截止日期 / 每日时长 / 优先级 / 状态 / 进度 / 当前状态 / 下一步，并追加一条决策记录",
        {
            "plan_id": {"type": "string", "description": "任务 ID"},
            "title": {"type": "string"},
            "goal": {"type": "string"},
            "why": {"type": "string"},
            "end_date": {"type": "string", "description": "ISO 日期"},
            "daily_minutes": {"type": "integer"},
            "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
            "status": {"type": "string", "enum": ["active", "paused", "completed", "archived"]},
            "progress": {"type": "integer", "description": "进度 0-100"},
            "current_status": {"type": "string", "description": "当前状态（只保留最新）"},
            "next_steps": {"type": "array", "items": {"type": "string"}, "description": "下一步（1-3 项，具体可执行）"},
            "plan_text": {"type": "string", "description": "更新完整规划方案（动态调整时重写）"},
            "acceptance_criteria": {"type": "array", "items": {"type": "string"}, "description": "更新验收标准"},
            "decision": {"type": "string", "description": "追加的决策记录（本次调整了什么、为什么）"},
        },
        ["plan_id"],
    ),
    _tool(
        "task_checkin",
        "【通用规划器】对任务执行一次检视：结合最近进展更新当前状态与下一步，追加检视记录并写入记忆笔记",
        {
            "plan_id": {"type": "string", "description": "任务 ID"},
            "summary": {"type": "string", "description": "本次检视总结，如：已完成里程碑一，卡在环境配置"},
            "progress": {"type": "integer", "description": "进度 0-100（可选）"},
        },
        ["plan_id", "summary"],
    ),
    _tool(
        "task_archive",
        "【通用规划器】归档任务（完成或放弃后归档，不再出现在活跃列表）",
        {"plan_id": {"type": "string", "description": "任务 ID"}},
        ["plan_id"],
    ),
    _tool(
        "task_milestone",
        "【通用规划器】给任务添加里程碑 / 阶段",
        {
            "plan_id": {"type": "string", "description": "任务 ID"},
            "title": {"type": "string", "description": "里程碑标题"},
            "due_date": {"type": "string", "description": "截止日期（可选），ISO 格式"},
        },
        ["plan_id", "title"],
    ),
    _tool(
        "task_complete_milestone",
        "【通用规划器】完成任务下的某个里程碑（完成后自动推进任务进度）",
        {
            "plan_id": {"type": "string", "description": "任务 ID"},
            "milestone_id": {"type": "string", "description": "里程碑 ID（ms_ 开头）"},
        },
        ["plan_id", "milestone_id"],
    ),
]


def register_planner_tools(
    registry: IToolRegistry,
    plan_repo,
    cognitive_repo=None,
    user_id: str = "chat_user",
    level: str = "skill",
) -> None:
    """把 8 个通用规划工具注册进注册表（handler 与 schema 成对注册，T2 技能层）"""
    handlers = _make_handlers(plan_repo, cognitive_repo, user_id)
    for tool in _TASK_TOOLS:
        name = tool["function"]["name"]
        registry.register(name, handlers[name], schema=tool, level=level)
    logger.info(f"已注册 {len(_TASK_TOOLS)} 个通用规划工具（user_id={user_id}）")
