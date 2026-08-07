"""学习工具集 —— 辅助学习场景（长期规划 / 进度追溯 / 动态调整 / 资料 / 提醒）

参考 zero_code 的「长期计划笔记体系」范式：
- plan_create / plan_list / plan_read / plan_update / plan_checkin / plan_archive
- 「活的计划」：当前状态只保留最新，决策记录只追加（进度追溯）
- plan_checkin 在用户汇报/询问进度时调用，让计划保持最新
- 资料 / 学习记录 / 提醒 配套，学习数据落在 study.json

计划/提醒会同步写入认知仓库（state + triple），让「学习」进入长期记忆。
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional

from aion_agent.core.entities.agent_state import AgentState
from aion_agent.core.entities.cognitive_triple import CognitiveTriple, Dimension
from aion_agent.core.entities.note import Note, NoteType
from aion_agent.core.ports.i_cognitive_repo import ICognitiveRepo
from aion_agent.core.ports.i_tool_registry import IToolRegistry
from aion_agent.study.study_repo import JsonStudyRepo

logger = logging.getLogger(__name__)

_RELATIVE_HOUR = re.compile(r"^(\d+)\s*小时后?$")
_DAY_TIME = re.compile(r"^(今天|明天|后天)\s*(\d{1,2}):(\d{2})$")
_TIME_ONLY = re.compile(r"^(\d{1,2}):(\d{2})$")


def _parse_remind_at(value: Any) -> datetime:
    """解析提醒时间：ISO / 今天20:00 / 明天9:00 / 2小时后 / 20:00"""
    v = str(value or "").strip()
    if not v:
        raise ValueError("缺少参数 remind_at（例如：今天20:00、明天9:00、2小时后、2026-08-08T10:00）")
    now = datetime.now()
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        pass
    m = _RELATIVE_HOUR.fullmatch(v)
    if m:
        return now + timedelta(hours=int(m.group(1)))
    m = _DAY_TIME.fullmatch(v)
    if m:
        offset = {"今天": 0, "明天": 1, "后天": 2}[m.group(1)]
        return (now + timedelta(days=offset)).replace(
            hour=int(m.group(2)), minute=int(m.group(3)), second=0, microsecond=0)
    m = _TIME_ONLY.fullmatch(v)
    if m:
        d = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
        if d <= now:
            d += timedelta(days=1)
        return d
    raise ValueError(f"无法解析提醒时间: {value}（支持：今天20:00、明天9:00、2小时后、ISO 时间）")


def _fmt_dt(value: Optional[str], with_time: bool = True) -> str:
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return str(value)
    return dt.strftime("%Y-%m-%d %H:%M" if with_time else "%Y-%m-%d")


def _pace_text(pace: str) -> str:
    return {"ahead": "超前", "on_track": "按计划", "behind": "落后", "unknown": "未知"}.get(pace, pace)


def _make_handlers(
    study: JsonStudyRepo,
    repo: Optional[ICognitiveRepo],
    user_id: str,
) -> Dict[str, Callable]:
    """基于学习仓库与认知仓库构建 13 个学习工具 handler"""

    # ---------------- 计划 ----------------

    def _plan_create(args: Dict[str, Any]) -> dict:
        title = str(args.get("title") or "").strip()
        if not title:
            raise ValueError("缺少参数 title（计划标题，如：三个月通过英语四级）")
        subject = str(args.get("subject") or "").strip()
        goal = str(args.get("goal") or "").strip()
        why = str(args.get("why") or "").strip()
        cadence = str(args.get("cadence") or "").strip()
        milestones = args.get("milestones") or []
        result = study.create_plan(
            title=title, subject=subject, goal=goal, why=why,
            cadence=cadence, end_date=args.get("end_date"),
            daily_minutes=int(args.get("daily_minutes") or 0),
            milestones=milestones,
        )
        plan = result["plan"]
        if result.get("reused"):
            return {"content": f"已存在相同标题的活跃计划，返回现有计划：{title}（{plan['plan_id']}）", "plan": plan}
        if repo is not None:
            expires = None
            if plan.get("end_date"):
                try:
                    expires = datetime.fromisoformat(plan["end_date"])
                except ValueError:
                    expires = None
            asyncio.run(repo.save_state(AgentState(
                user_id=user_id, state_type="user", state_name="study_plan",
                description=f"学习计划：{title}（{goal or '进行中'}）",
                expires_at=expires, priority=5,
            )))
            asyncio.run(repo.save_triple(CognitiveTriple(
                subject="我", predicate="正在执行长期学习计划", object=title,
                dimension=Dimension.STATE, user_id=user_id,
                confidence=0.85, source="plan_tool", expires_at=expires,
            )))
        return {"content": f"已创建长期学习计划：{title}（{plan['plan_id']}）", "plan": plan}

    def _plan_list(args: Dict[str, Any]) -> dict:
        status = str(args.get("status") or "").strip() or None
        plans = study.list_plans(status=status)
        if not plans:
            return {"content": "当前没有学习计划。可以让我帮你制定一个，例如：帮我制定一个三个月备考计划。"}
        lines = []
        for p in plans:
            info = p.get("progress_info") or {}
            lines.append(
                f"- {p['title']} [{p['status']}] 进度 {info.get('progress', 0)}% "
                f"（{_pace_text(info.get('pace', 'unknown'))}，已学 {info.get('total_minutes', 0)} 分钟）"
                f" {p['plan_id']}"
            )
        return {"content": "学习计划：\n" + "\n".join(lines), "plans": plans}

    def _plan_read(args: Dict[str, Any]) -> dict:
        plan_id = str(args.get("plan_id") or "").strip()
        if not plan_id:
            raise ValueError("缺少参数 plan_id")
        detail = study.plan_detail(plan_id)
        if detail is None:
            raise ValueError(f"未找到计划: {plan_id}")
        info = detail.get("progress_info") or {}
        parts = [
            f"计划：{detail['title']}（状态 {detail['status']}，进度 {info.get('progress', 0)}%）",
        ]
        if detail.get("goal"):
            parts.append(f"目标：{detail['goal']}")
        if detail.get("why"):
            parts.append(f"为什么：{detail['why']}")
        if detail.get("end_date"):
            parts.append(f"截止：{_fmt_dt(detail['end_date'], with_time=False)}")
        if detail.get("daily_minutes"):
            parts.append(f"每日目标：{detail['daily_minutes']} 分钟")
        parts.append(f"已学：{info.get('total_minutes', 0)} 分钟，节奏：{_pace_text(info.get('pace', 'unknown'))}")
        ms = detail.get("milestones") or []
        if ms:
            parts.append("里程碑：" + "；".join(
                f"{'✅' if m.get('done') else '⬜'} {m['title']}" + (f"（{_fmt_dt(m.get('due_date'), with_time=False)}）" if m.get("due_date") else "")
                for m in ms
            ))
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
        sessions = detail.get("sessions") or []
        if sessions:
            parts.append("最近学习：" + "；".join(
                f"{_fmt_dt(s.get('created_at'))} {s['subject']} {s['minutes']} 分钟" for s in sessions[:5]
            ))
        return {"content": "\n".join(parts), "plan": detail}

    def _plan_update(args: Dict[str, Any]) -> dict:
        plan_id = str(args.get("plan_id") or "").strip()
        if not plan_id:
            raise ValueError("缺少参数 plan_id")
        fields = {}
        for k in ("title", "subject", "goal", "why", "cadence", "end_date",
                  "daily_minutes", "status", "progress", "current_status", "next_steps"):
            if args.get(k) is not None:
                fields[k] = args[k]
        plan = study.update_plan(
            plan_id,
            decision=str(args.get("decision") or "").strip() or None,
            **fields,
        )
        if plan is None:
            raise ValueError(f"未找到计划: {plan_id}")
        return {"content": f"计划已更新：{plan['title']}（{plan_id}）", "plan": plan}

    def _plan_checkin(args: Dict[str, Any]) -> dict:
        """检视：更新当前状态与下一步，追加一条决策记录（进度追溯）"""
        plan_id = str(args.get("plan_id") or "").strip()
        if not plan_id:
            raise ValueError("缺少参数 plan_id")
        summary = str(args.get("progress_summary") or "").strip()
        if not summary:
            raise ValueError("缺少参数 progress_summary（本次检视结论：进展/卡点/调整）")
        progress = args.get("progress")
        next_steps = args.get("next_steps")
        fields = {}
        if progress is not None:
            fields["progress"] = progress
        if next_steps is not None:
            fields["next_steps"] = next_steps
        fields["current_status"] = summary
        plan = study.update_plan(
            plan_id, decision=f"检视：{summary}", **fields,
        )
        if plan is None:
            raise ValueError(f"未找到计划: {plan_id}")
        info = study._plan_progress(plan)
        return {
            "content": f"已检视计划：{plan['title']}，进度 {info['progress']}%，"
                       f"节奏 {_pace_text(info['pace'])}（{plan_id}）",
            "plan": {**plan, "progress_info": info},
        }

    def _plan_archive(args: Dict[str, Any]) -> dict:
        plan_id = str(args.get("plan_id") or "").strip()
        if not plan_id:
            raise ValueError("缺少参数 plan_id")
        plan = study.archive_plan(plan_id)
        if plan is None:
            raise ValueError(f"未找到计划: {plan_id}")
        return {"content": f"计划已归档：{plan['title']}（{plan_id}）", "plan": plan}

    # ---------------- 学习记录 / 资料 / 提醒 ----------------

    def _log_study_session(args: Dict[str, Any]) -> dict:
        subject = str(args.get("subject") or "").strip()
        minutes = int(args.get("minutes") or 0)
        if not subject or minutes <= 0:
            raise ValueError("缺少参数 subject/minutes（例如 subject=英语, minutes=30）")
        plan_id = str(args.get("plan_id") or "").strip() or None
        note = str(args.get("note") or "").strip()
        session = study.log_session(subject=subject, minutes=minutes, note=note, plan_id=plan_id)
        today = study.today_minutes()
        return {
            "content": f"已记录学习：{subject} {minutes} 分钟（今日累计 {today} 分钟）",
            "session": session,
            "today_minutes": today,
        }

    def _add_study_material(args: Dict[str, Any]) -> dict:
        title = str(args.get("title") or "").strip()
        if not title:
            raise ValueError("缺少参数 title（资料标题）")
        subject = str(args.get("subject") or "").strip()
        source = str(args.get("source") or "").strip()
        summary = str(args.get("summary") or "").strip()
        tags = args.get("tags") or []
        material = study.add_material(
            title=title, subject=subject, source=source, summary=summary, tags=tags,
        )
        if repo is not None:
            asyncio.run(repo.save_note(Note(
                user_id=user_id, note_type=NoteType.KNOWLEDGE, title=title,
                content=(
                    (f"【学习资料·{subject}】" if subject else "【学习资料】")
                    + (f"\n来源：{source}" if source else "")
                    + (f"\n摘要：{summary}" if summary else "")
                ),
                tags=[str(t) for t in tags] if tags else [],
                summary=summary or title,
            )))
        return {"content": f"已保存学习资料：{title}", "material": material}

    def _search_study_materials(args: Dict[str, Any]) -> dict:
        subject = str(args.get("subject") or "").strip() or None
        query = str(args.get("query") or "").strip() or None
        items = study.list_materials(subject=subject, query=query)
        if not items:
            return {"content": "未找到匹配的学习资料"}
        lines = [
            f"- {m['title']}" + (f"（{m['subject']}）" if m.get("subject") else "")
            + (f"：{m['summary'][:60]}" if m.get("summary") else "")
            + f" {m['material_id']}"
            for m in items
        ]
        return {"content": "匹配的学习资料：\n" + "\n".join(lines), "materials": items}

    def _create_reminder(args: Dict[str, Any]) -> dict:
        title = str(args.get("title") or "").strip()
        if not title:
            raise ValueError("缺少参数 title（提醒事项）")
        remind_at = _parse_remind_at(args.get("remind_at"))
        content = str(args.get("content") or "").strip()
        reminder = study.create_reminder(title=title, remind_at=remind_at, content=content)
        if repo is not None:
            asyncio.run(repo.save_state(AgentState(
                user_id=user_id, state_type="user", state_name="reminder",
                description=f"提醒：{title}" + (f"（{content}）" if content else ""),
                expires_at=remind_at, priority=3,
            )))
        return {
            "content": f"已设置提醒：{title}（{remind_at.strftime('%Y-%m-%d %H:%M')}）",
            "reminder": reminder,
        }

    def _list_reminders(args: Dict[str, Any]) -> dict:
        include_done = bool(args.get("include_done"))
        items = study.list_reminders(include_done=include_done)
        if not items:
            return {"content": "当前没有提醒"}
        lines = [
            f"- {r['title']}（{_fmt_dt(r['remind_at'])}）"
            + (" [已完成]" if r.get("done") else "")
            + f" {r['reminder_id']}"
            for r in items
        ]
        return {"content": "提醒列表：\n" + "\n".join(lines), "reminders": items}

    def _complete_reminder(args: Dict[str, Any]) -> dict:
        rid = str(args.get("reminder_id") or "").strip()
        if not rid:
            raise ValueError("缺少参数 reminder_id")
        ok = study.complete_reminder(rid)
        if not ok:
            raise ValueError(f"未找到提醒: {rid}")
        return {"content": f"已完成提醒（{rid}）"}

    def _get_study_status(args: Dict[str, Any]) -> dict:
        ov = study.overview()
        parts = []
        plans = ov["active_plans"]
        if plans:
            parts.append("进行中的计划：" + "、".join(
                f"{p['title']}（{p.get('progress_info', {}).get('progress', 0)}%）"
                for p in plans
            ))
        else:
            parts.append("没有进行中的学习计划")
        parts.append(f"今日已学习 {ov['today_minutes']} 分钟")
        due = ov["due_reminders"]
        if due:
            parts.append("已到期提醒：" + "；".join(
                f"{r['title']}（{_fmt_dt(r['remind_at'])}）" for r in due
            ))
        upcoming = ov["upcoming_reminders"]
        if upcoming:
            parts.append("近期提醒：" + "；".join(
                f"{r['title']}（{_fmt_dt(r['remind_at'])}）" for r in upcoming[:5]
            ))
        return {"content": "学习状态：\n" + "\n".join(parts), "overview": ov}

    return {
        "plan_create": _plan_create,
        "plan_list": _plan_list,
        "plan_read": _plan_read,
        "plan_update": _plan_update,
        "plan_checkin": _plan_checkin,
        "plan_archive": _plan_archive,
        "log_study_session": _log_study_session,
        "add_study_material": _add_study_material,
        "search_study_materials": _search_study_materials,
        "create_reminder": _create_reminder,
        "list_reminders": _list_reminders,
        "complete_reminder": _complete_reminder,
        "get_study_status": _get_study_status,
    }


# ==================== OpenAI 格式 schema ====================

_STUDY_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "plan_create",
            "description": (
                "创建长期学习计划（仅当用户表达长期/周期学习目标时使用，如学英语/备考/读书/健身）。"
                "计划持久化保存，后续会话可随时查看、检视与调整。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "计划标题，如：三个月通过英语四级"},
                    "subject": {"type": "string", "description": "科目，如：英语"},
                    "goal": {"type": "string", "description": "目标描述（想达到什么、验收标准）"},
                    "why": {"type": "string", "description": "为什么学（动机，帮助未来对齐方向）"},
                    "cadence": {"type": "string", "description": "检视节奏（可选），如：每周日晚"},
                    "end_date": {"type": "string", "description": "截止日期（可选），ISO 格式，如 2026-11-30"},
                    "daily_minutes": {"type": "integer", "description": "每日目标时长（分钟，可选）"},
                    "milestones": {
                        "type": "array",
                        "description": "里程碑/阶段（可选），如 [{title: 打基础, due_date: 2026-09-30}]",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "due_date": {"type": "string", "description": "ISO 日期"},
                            },
                        },
                    },
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_list",
            "description": "列出学习计划（含进度、节奏、已学时长摘要）",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["active", "paused", "completed", "archived"], "description": "按状态过滤（可选）"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_read",
            "description": "查看某个学习计划的完整详情：目标/里程碑/当前状态/下一步/决策记录/学习记录（进度追溯）",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string", "description": "计划 ID（plan_ 开头）"},
                },
                "required": ["plan_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_update",
            "description": (
                "动态调整学习计划：修改目标/截止日期/每日时长/状态(active/paused/completed/archived)/"
                "进度/当前状态/下一步，并追加一条决策记录（说明本次调整了什么、为什么）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string", "description": "计划 ID"},
                    "goal": {"type": "string"},
                    "end_date": {"type": "string", "description": "ISO 日期"},
                    "daily_minutes": {"type": "integer"},
                    "status": {"type": "string", "enum": ["active", "paused", "completed", "archived"]},
                    "progress": {"type": "integer", "description": "进度 0-100"},
                    "current_status": {"type": "string", "description": "当前状态（只保留最新）"},
                    "next_steps": {"type": "array", "items": {"type": "string"}, "description": "下一步（1-3 项，具体可执行）"},
                    "decision": {"type": "string", "description": "追加的决策记录（本次调整了什么、为什么）"},
                },
                "required": ["plan_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_checkin",
            "description": (
                "对学习计划执行一次检视：结合最近对话更新当前状态与下一步，追加一条检视记录。"
                "在用户汇报进度、询问进度、或会话自然节点时调用，让计划保持最新。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string", "description": "计划 ID"},
                    "progress_summary": {"type": "string", "description": "本次检视结论：进展/卡点/调整"},
                    "progress": {"type": "integer", "description": "进度 0-100（可选）"},
                    "next_steps": {"type": "array", "items": {"type": "string"}, "description": "下一步（可选）"},
                },
                "required": ["plan_id", "progress_summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_archive",
            "description": "归档学习计划（完成/放弃后归档）",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string"},
                },
                "required": ["plan_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_study_session",
            "description": "记录一次学习（科目/时长/备注），可关联到学习计划；会累计今日与计划总时长",
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "description": "学习科目，如：英语"},
                    "minutes": {"type": "integer", "description": "时长（分钟）"},
                    "note": {"type": "string", "description": "备注（可选）"},
                    "plan_id": {"type": "string", "description": "关联计划 ID（可选）"},
                },
                "required": ["subject", "minutes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_study_material",
            "description": "保存一份学习资料（标题/科目/来源/摘要/标签），方便以后检索",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "资料标题"},
                    "subject": {"type": "string", "description": "科目（可选）"},
                    "source": {"type": "string", "description": "来源链接或出处（可选）"},
                    "summary": {"type": "string", "description": "摘要（可选）"},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "标签（可选）"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_study_materials",
            "description": "检索已保存的学习资料（按科目/关键词）",
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "description": "科目（可选）"},
                    "query": {"type": "string", "description": "关键词（可选）"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_reminder",
            "description": "创建提醒（如学习提醒、待办提醒）。支持：今天20:00、明天9:00、2小时后、2026-08-08T10:00",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "提醒事项"},
                    "remind_at": {"type": "string", "description": "提醒时间，如：今天20:00"},
                    "content": {"type": "string", "description": "补充说明（可选）"},
                },
                "required": ["title", "remind_at"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_reminders",
            "description": "列出提醒（未完成，按时间排序）",
            "parameters": {
                "type": "object",
                "properties": {
                    "include_done": {"type": "boolean", "description": "是否包含已完成（可选）"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_reminder",
            "description": "把提醒标记为已完成",
            "parameters": {
                "type": "object",
                "properties": {
                    "reminder_id": {"type": "string"},
                },
                "required": ["reminder_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_study_status",
            "description": "获取学习概览：进行中的计划/进度、今日已学时长、到期与近期提醒（在用户询问学习安排时调用）",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
]


def register_study_tools(
    registry: IToolRegistry,
    study_repo: JsonStudyRepo,
    cognitive_repo: Optional[ICognitiveRepo] = None,
    user_id: str = "chat_user",
    level: str = "skill",
) -> None:
    """注册 13 个学习工具（handler 与 schema 成对注册，T2 技能层）"""
    handlers = _make_handlers(study_repo, cognitive_repo, user_id)
    for tool in _STUDY_TOOLS:
        name = tool["function"]["name"]
        registry.register(name, handlers[name], schema=tool, level=level)
