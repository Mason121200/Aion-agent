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
from aion_agent.study.study_repo import JsonStudyRepo, _parse_dt

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
    """基于学习仓库与认知仓库构建 31 个学习工具 handler"""

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
        acceptance = args.get("acceptance") or []
        result = study.create_plan(
            title=title, subject=subject, goal=goal, why=why,
            cadence=cadence, end_date=args.get("end_date"),
            daily_minutes=int(args.get("daily_minutes") or 0),
            milestones=milestones,
            acceptance=acceptance,
            review_strategy=str(args.get("review_strategy") or "").strip(),
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

    # ---------------- 学习画像 ----------------

    def _profile_collect(args: Dict[str, Any]) -> dict:
        """收集/更新学习画像（学习风格/偏好方法/时段/专注/复习偏好/动机）"""
        fields = {}
        for k in ("learning_style", "preferred_method", "best_time",
                  "focus_minutes", "review_pref", "motivation", "notes"):
            if args.get(k) is not None:
                fields[k] = args[k]
        if not fields:
            raise ValueError("缺少画像字段（如 learning_style / preferred_method / best_time）")
        profile = study.update_profile(**fields)
        # 沉淀进长期记忆（USER 维度画像）
        if repo is not None:
            pairs = [
                ("学习风格", profile.get("learning_style")),
                ("偏好的学习方法", profile.get("preferred_method")),
                ("最佳学习时段", profile.get("best_time")),
                ("复习偏好", profile.get("review_pref")),
                ("学习动机", profile.get("motivation")),
            ]
            for pred, obj in pairs:
                if obj:
                    asyncio.run(repo.save_triple(CognitiveTriple(
                        subject="用户", predicate=pred, object=obj,
                        dimension=Dimension.USER, user_id=user_id,
                        confidence=0.75, source="profile_collect",
                    )))
        return {
            "content": f"学习画像已保存：{study.profile_summary() or '（空）'}",
            "profile": profile,
        }

    def _profile_read(args: Dict[str, Any]) -> dict:
        profile = study.get_profile()
        if not profile:
            return {"content": "还没有学习画像。可以让我先了解你：喜欢的讲解方式、最佳学习时段、单次能专注多久等。", "profile": {}}
        lines = [
            f"- 学习风格：{profile.get('learning_style', '未知')}",
            f"- 偏好方法：{profile.get('preferred_method', '未知')}",
            f"- 最佳时段：{profile.get('best_time', '未知')}",
            f"- 单次专注：{profile.get('focus_minutes', '未知')} 分钟",
            f"- 复习偏好：{profile.get('review_pref', '未知')}",
            f"- 动机：{profile.get('motivation', '未知')}",
        ]
        if profile.get("notes"):
            lines.append(f"- 备注：{profile['notes']}")
        return {"content": "学习画像：\n" + "\n".join(lines), "profile": profile}

    # ---------------- 评估：测验 / 错题 ----------------

    def _test_log(args: Dict[str, Any]) -> dict:
        plan_id = str(args.get("plan_id") or "").strip()
        if not plan_id:
            raise ValueError("缺少参数 plan_id")
        test = study.add_test(
            plan_id=plan_id,
            title=str(args.get("title") or "").strip(),
            score=int(args.get("score") or 0),
            total=int(args.get("total") or 0),
            correct=args.get("correct"),
            knowledge_points=args.get("knowledge_points") or [],
            note=str(args.get("note") or "").strip(),
        )
        if repo is not None and test["accuracy"] < 70 and test["knowledge_points"]:
            asyncio.run(repo.save_note(Note(
                user_id=user_id, note_type=NoteType.KNOWLEDGE,
                title=f"薄弱点：{test['title']}",
                content=(
                    f"测验「{test['title']}」正确率 {test['accuracy']}%"
                    f"（{test['correct']}/{test['total']}），"
                    f"薄弱知识点：{', '.join(test['knowledge_points'])}"
                ),
                tags=test["knowledge_points"],
                summary=f"测验正确率 {test['accuracy']}%",
            )))
        adj = study.evaluate_adjustment_need(plan_id)
        adj_line = (
            f"⚠️ 调整评估：建议调整方案（{adj['reason']}）→ {adj['suggestion']}"
            if adj["needed"]
            else f"调整评估：{adj['reason']}"
        )
        return {
            "content": (
                f"已记录测验：{test['title']}，正确率 {test['accuracy']}%"
                f"（{test['correct']}/{test['total']}）"
                + ("，薄弱知识点已加入复习队列" if test["accuracy"] < 70 and test["knowledge_points"] else "")
                + "。\n" + adj_line
            ),
            "test": test,
            "adjustment": adj,
        }

    def _mistake_add(args: Dict[str, Any]) -> dict:
        plan_id = str(args.get("plan_id") or "").strip()
        if not plan_id:
            raise ValueError("缺少参数 plan_id")
        mistake = study.add_mistake(
            plan_id=plan_id,
            content=str(args.get("content") or "").strip(),
            reason=str(args.get("reason") or "").strip(),
            knowledge_point=str(args.get("knowledge_point") or "").strip(),
        )
        if repo is not None:
            asyncio.run(repo.save_note(Note(
                user_id=user_id, note_type=NoteType.KNOWLEDGE,
                title=f"错题：{mistake['content'][:30]}",
                content=(
                    f"【错题·{mistake['knowledge_point']}】{mistake['content']}"
                    + (f"\n归因：{mistake['reason']}" if mistake["reason"] else "")
                ),
                tags=[mistake["knowledge_point"]] if mistake["knowledge_point"] else [],
                summary=mistake["content"][:60],
            )))
        return {
            "content": f"已记录错题（自动进入复习队列）：{mistake['content'][:50]}",
            "mistake": mistake,
        }

    # ---------------- 复习 ----------------

    def _list_reviews(args: Dict[str, Any]) -> dict:
        plan_id = str(args.get("plan_id") or "").strip() or None
        items = study.list_reviews(
            plan_id=plan_id, include_done=bool(args.get("include_done")),
        )
        if not items:
            return {"content": "当前没有待复习项" + ("（该计划）" if plan_id else "")}
        now = datetime.now()
        lines = []
        for it in items:
            due = "🔴到期" if (_parse_dt(it.get("next_review_at")) or now) <= now else "🟢未到"
            lines.append(
                f"- [{due}] {it['content'][:50]}"
                f"（间隔{it.get('interval_days')}天，连对{it.get('streak')}次）"
                f" {it['review_item_id']}"
            )
        return {"content": "复习队列：\n" + "\n".join(lines), "review_items": items}

    def _review_checkin(args: Dict[str, Any]) -> dict:
        plan_id = str(args.get("plan_id") or "").strip()
        item_id = str(args.get("review_item_id") or "").strip()
        if not plan_id or not item_id:
            raise ValueError("缺少参数 plan_id / review_item_id")
        correct = bool(args.get("correct"))
        item = study.review_checkin(plan_id=plan_id, review_item_id=item_id, correct=correct)
        result = "已掌握" if correct else "没记住，安排更短间隔复习"
        due_after = study.schedule_reviews(plan_id)
        if repo is not None and item.get("resolved"):
            asyncio.run(repo.save_triple(CognitiveTriple(
                subject="我", predicate="已掌握", object=item.get("content", "")[:50],
                dimension=Dimension.STATE, user_id=user_id,
                confidence=0.8, source="review_tool",
            )))
        return {
            "content": (
                f"复习打卡：{result}（下次间隔 {item['interval_days']} 天，"
                f"剩余到期 {len(due_after)} 项）"
            ),
            "review_item": item,
            "due_remaining": len(due_after),
        }

    # ---------------- 动态调整实验 ----------------

    def _plan_adjust(args: Dict[str, Any]) -> dict:
        plan_id = str(args.get("plan_id") or "").strip()
        content = str(args.get("content") or "").strip()
        if not plan_id or not content:
            raise ValueError("缺少参数 plan_id / content（调整内容）")
        fields = {}
        for k in ("title", "subject", "goal", "why", "cadence", "review_strategy",
                  "end_date", "daily_minutes", "status", "progress",
                  "current_status", "next_steps"):
            if args.get(k) is not None:
                fields[k] = args[k]
        adj = study.record_adjustment(
            plan_id=plan_id, content=content,
            reason=str(args.get("reason") or "").strip(),
            window_days=int(args.get("window_days") or 7), **fields,
        )
        if repo is not None:
            asyncio.run(repo.save_triple(CognitiveTriple(
                subject="我", predicate="调整了学习方案", object=content[:50],
                dimension=Dimension.STATE, user_id=user_id,
                confidence=0.7, source="adjust_tool",
            )))
        return {
            "content": f"已记录调整实验（{adj['window_days']} 天后对比效果）：{content}",
            "adjustment": adj,
        }

    # ---------------- 数据分析 ----------------

    def _study_analyze(args: Dict[str, Any]) -> dict:
        plan_id = str(args.get("plan_id") or "").strip() or None
        days = int(args.get("days") or 7)
        report = study.analyze(plan_id=plan_id, days=days)
        parts = [f"学习分析（近 {days} 天）："]
        parts.append(
            f"- 学习时长：共 {report['total_minutes']} 分钟"
            + (f"，日均 {round(report['total_minutes'] / max(days, 1))} 分钟" if report['total_minutes'] else "")
        )
        if report["stagnant_days"] is not None:
            parts.append(
                f"- 最近学习：{report['stagnant_days']} 天前"
                + ("（⚠️ 停滞，需要干预）" if report["stagnant"] else "")
            )
        if report["avg_accuracy"] is not None:
            parts.append(f"- 测验正确率：平均 {report['avg_accuracy']}%")
        parts.append(
            f"- 复习：到期 {report['review_due']} 项 / 共 {report['review_total']} 项，"
            f"已掌握 {report['review_resolved']} 项"
            + (f"，复习正确率 {report['review_accuracy']}%" if report["review_accuracy"] is not None else "")
        )
        if report["milestone_ratio"] is not None:
            parts.append(f"- 里程碑达成：{report['milestone_ratio']}%")
        parts.append(f"- 进度：{report['progress']}%（节奏 {_pace_text(report['pace'])}）")
        if report["adjustments_evaluated"]:
            for adj in report["adjustments_evaluated"]:
                verdict = ("✅有效" if adj["conclusion"] == "effective"
                           else "⏳待裁决" if adj["conclusion"] == "pending_review" else "❌无效")
                parts.append(f"- 调整实验：{adj['content'][:40]} → {verdict}")
        if report["stagnant"]:
            parts.append("建议：已停滞，可调整节奏/方式或降低目标")
        return {"content": "\n".join(parts), "report": report}

    # ---------------- 完成判定（LLM 裁决） ----------------

    def _plan_completion_check(args: Dict[str, Any]) -> dict:
        """完成验收报告：规则层只出客观数据（验收项/里程碑/复习/测验），不裁决"""
        plan_id = str(args.get("plan_id") or "").strip()
        if not plan_id:
            raise ValueError("缺少参数 plan_id")
        check = study.completion_check(plan_id)
        plan = study.get_plan(plan_id)
        missing = check.get("missing") or []
        parts = [f"完成验收报告《{plan.get('title', '')}》："]
        parts.append(
            f"- 验收项：{check.get('acceptance_done', 0)}/{check.get('acceptance_total', 0)}"
        )
        if missing:
            parts.append("- 未满足：" + "；".join(missing))
        else:
            parts.append("- 客观条件全部满足（是否完成由你裁决）")
        return {
            "content": "\n".join(parts),
            "check": check,
            "plan": plan,
        }

    def _plan_completion_confirm(args: Dict[str, Any]) -> dict:
        """LLM 完成裁决：基于验收报告做最终决策，规则层只执行"""
        plan_id = str(args.get("plan_id") or "").strip()
        if not plan_id:
            raise ValueError("缺少参数 plan_id")
        accepted = bool(args.get("accepted"))
        result = study.confirm_completion(
            plan_id,
            accepted=accepted,
            report_note=str(args.get("report_note") or "").strip(),
        )
        plan = result.get("plan") or {}
        if result.get("already"):
            return {"content": f"计划已完成（重复确认）", "plan": plan}
        if not result["confirmed"]:
            return {
                "content": f"已记录裁决：暂不完成（{plan.get('title', '')}）"
                           + (f"｜理由：{args.get('report_note')}" if args.get("report_note") else ""),
                "plan": plan,
                "check": result.get("check"),
            }
        if repo is not None:
            asyncio.run(repo.save_note(Note(
                user_id=user_id, note_type=NoteType.SUMMARY,
                title=f"学习总结：{plan['title']}",
                content=plan.get("completion", {}).get("report", ""),
                summary=f"完成学习计划：{plan['title']}",
            )))
        return {
            "content": (
                f"🎉 计划已完成（LLM 裁决）：{plan['title']}\n"
                f"完成报告：{plan.get('completion', {}).get('report', '')}"
            ),
            "plan": plan,
            "check": result.get("check"),
        }

    # ---------------- 调整实验裁决（LLM） ----------------

    def _adjustment_confirm(args: Dict[str, Any]) -> dict:
        """LLM 裁决到期调整实验：采纳 → 沉淀为有效方法；不采纳 → 记录理由"""
        plan_id = str(args.get("plan_id") or "").strip()
        adjustment_id = str(args.get("adjustment_id") or "").strip()
        if not plan_id or not adjustment_id:
            raise ValueError("缺少参数 plan_id / adjustment_id")
        accepted = bool(args.get("accepted"))
        reason = str(args.get("reason") or "").strip()
        result = study.confirm_adjustment(
            plan_id, adjustment_id, accepted=accepted, reason=reason,
        )
        if result.get("already"):
            return {"content": "该实验已完成裁决（重复确认）", "adjustment": result.get("adjustment")}
        if not result["confirmed"]:
            raise ValueError(result.get("error") or "裁决失败")
        adj = result["adjustment"]
        verdict = "已采纳并沉淀为有效学习方法" if accepted else "已记录为无效，方案保持不变"
        if accepted and repo is not None:
            asyncio.run(repo.save_triple(CognitiveTriple(
                subject="我", predicate="有效的学习方法", object=str(adj.get("content") or "")[:50],
                dimension=Dimension.USER, user_id=user_id,
                confidence=0.8, source="adjustment_review",
            )))
        return {
            "content": f"调整裁决完成：{verdict}｜调整：{adj.get('content')}"
                       + (f"｜理由：{reason}" if reason else ""),
            "adjustment": adj,
        }

    # ---------------- 每日执行单 ----------------

    def _daily_start(args: Dict[str, Any]) -> dict:
        result = study.create_daily(
            date=str(args.get("date") or "").strip(),
            routine_id=str(args.get("routine_id") or "").strip(),
            focus=str(args.get("focus") or "").strip(),
            output_goal=str(args.get("output_goal") or "").strip(),
            delivery_goal=str(args.get("delivery_goal") or "").strip(),
            blocks=args.get("blocks") or [],
        )
        entry = result["daily"]
        if result.get("reused"):
            return {
                "content": (
                    f"今日执行单已存在（{entry['daily_id']}）：{entry['date']}｜"
                    f"产出物：{entry['output_goal'] or '未定'}"
                ),
                "daily": entry,
            }
        lines = [f"已生成今日执行单（{entry['daily_id']}）：{entry['date']}"]
        if entry.get("focus"):
            lines.append(f"今日主线：{entry['focus']}")
        if entry.get("output_goal"):
            lines.append(f"今日产出物：{entry['output_goal']}")
        if entry.get("delivery_goal"):
            lines.append(f"今日投递目标：{entry['delivery_goal']}")
        for b in entry.get("blocks") or []:
            mark = "✅" if b.get("done") else "⬜"
            lines.append(
                f"  {mark} {b['name']}（{b.get('slot') or '未定时段'}｜"
                f"{b.get('focus') or '未定'}）"
            )
        return {"content": "\n".join(lines), "daily": entry}

    def _daily_list(args: Dict[str, Any]) -> dict:
        date = str(args.get("date") or "").strip()
        items = study.list_dailies(date=date)
        if not items:
            return {"content": "没有找到执行单。可用 daily_start 生成今天的执行单。"}
        lines = []
        for d in items:
            mark = "✅" if d.get("status") == "completed" else "⬜"
            theme = d.get("focus") or d.get("output_goal") or "（未定主题）"
            lines.append(f"{mark} {d['date']} {theme}（{d['daily_id']}）")
        return {"content": "执行单列表：\n" + "\n".join(lines), "dailies": items}

    def _daily_block_done(args: Dict[str, Any]) -> dict:
        daily_id = str(args.get("daily_id") or "").strip()
        block_id = str(args.get("block_id") or "").strip()
        if not daily_id or not block_id:
            raise ValueError("缺少参数 daily_id / block_id")
        entry = study.update_daily_block(
            daily_id,
            block_id,
            done=bool(args.get("done", True)),
            note=str(args.get("note") or "").strip(),
        )
        if entry is None:
            raise ValueError(f"未找到执行单：{daily_id} 或块：{block_id}")
        return {"content": f"已更新执行块：{entry['date']}", "daily": entry}

    def _daily_summary(args: Dict[str, Any]) -> dict:
        daily_id = str(args.get("daily_id") or "").strip()
        if not daily_id:
            raise ValueError("缺少参数 daily_id")
        summary = str(args.get("summary") or "").strip()
        if not summary:
            raise ValueError("缺少参数 summary（今日总结）")
        entry = study.complete_daily(
            daily_id,
            summary=summary,
            output_done=bool(args.get("output_done")),
            blockers=args.get("blockers") or [],
            tomorrow_focus=str(args.get("tomorrow_focus") or "").strip(),
        )
        if entry is None:
            raise ValueError(f"未找到执行单：{daily_id}")
        if repo is not None:
            asyncio.run(repo.save_note(Note(
                user_id=user_id,
                note_type=NoteType.SUMMARY,
                title=f"每日总结 · {entry['date']}",
                content=summary,
                related_session_id=None,
            )))
        return {"content": f"已记录今日收尾总结（{entry['date']}）：{summary}", "daily": entry}

    def _pomodoro_start(args: Dict[str, Any]) -> dict:
        result = study.start_pomodoro(
            minutes=int(args.get("minutes") or 25),
            plan_id=str(args.get("plan_id") or "").strip(),
            block_id=str(args.get("block_id") or "").strip(),
            block_name=str(args.get("block_name") or "").strip(),
        )
        p = result["pomodoro"]
        if result.get("reused"):
            return {"content": f"已有运行中的番茄钟（{p['pomodoro_id']}），请先停止再开启新的。", "pomodoro": p}
        text = f"🍅 番茄钟已启动（{p['pomodoro_id']}）：{p['minutes']} 分钟"
        if p.get("block_name"):
            text += f"｜{p['block_name']}"
        return {"content": text, "pomodoro": p}

    def _pomodoro_stop(args: Dict[str, Any]) -> dict:
        p = study.stop_pomodoro()
        if p is None:
            return {"content": "当前没有运行中的番茄钟。可用 pomodoro_start 启动一个。"}
        return {
            "content": f"🍅 番茄钟已结束（{p['pomodoro_id']}）：专注 {p['elapsed_minutes']} 分钟，已记入学习时长。",
            "pomodoro": p,
        }

    def _pomodoro_status(args: Dict[str, Any]) -> dict:
        st = study.pomodoro_status()
        if st["running"] is None:
            return {
                "content": f"今日已完成 {st['today_done']} 个番茄钟，专注 {st['today_focus_minutes']} 分钟。当前无运行中的番茄钟。",
                "running": None,
                "remaining_minutes": None,
                "today_done": st["today_done"],
                "today_focus_minutes": st["today_focus_minutes"],
            }
        p = st["running"]
        text = f"🍅 运行中（{p['pomodoro_id']}）：剩余约 {st['remaining_minutes']} 分钟"
        if p.get("block_name"):
            text += f"｜{p['block_name']}"
        text += f"｜今日已完成 {st['today_done']} 个，专注 {st['today_focus_minutes']} 分钟。"
        return {"content": text, **st}

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
        "profile_collect": _profile_collect,
        "profile_read": _profile_read,
        "test_log": _test_log,
        "list_reviews": _list_reviews,
        "mistake_add": _mistake_add,
        "review_checkin": _review_checkin,
        "plan_adjust": _plan_adjust,
        "study_analyze": _study_analyze,
        "confirm_adjustment": _adjustment_confirm,
        "plan_completion_check": _plan_completion_check,
        "confirm_plan_completion": _plan_completion_confirm,
        "daily_start": _daily_start,
        "daily_list": _daily_list,
        "daily_block_done": _daily_block_done,
        "daily_summary": _daily_summary,
        "pomodoro_start": _pomodoro_start,
        "pomodoro_stop": _pomodoro_stop,
        "pomodoro_status": _pomodoro_status,
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
                "acceptance": {
                    "type": "array",
                    "description": "验收标准（可选），如 [能独立完成四级真题听力, 词汇量达到 4500]——验收不靠自评，靠里程碑完成与测验数据",
                    "items": {"type": "string"},
                },
                "review_strategy": {"type": "string", "description": "复习策略（可选），如：每章学完隔 1/3/7 天复习"},
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
    {
        "type": "function",
        "function": {
            "name": "profile_collect",
            "description": "收集/更新用户学习画像：学习风格、偏好教学方法、最佳学习时段、单次专注时长、复习偏好、动机。开始系统学习或计划创建前应主动收集（对话中轻量询问后调用）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "learning_style": {"type": "string", "description": "学习风格，如：视觉型/听觉型/读写型/实操型，或具体描述"},
                    "preferred_method": {"type": "string", "description": "偏好教学方法，如：先讲解再练习/边做边学/案例驱动/直接出题/项目实践"},
                    "best_time": {"type": "string", "description": "最佳学习时段，如：早晨/上午/下午/晚上/深夜"},
                    "focus_minutes": {"type": "integer", "description": "单次能专注的分钟数"},
                    "review_pref": {"type": "string", "description": "复习偏好，如：喜欢被提问/自己重看笔记/做题自测"},
                    "motivation": {"type": "string", "description": "学习动机，如：目标驱动（考证/求职）/兴趣驱动/外部压力"},
                    "notes": {"type": "string", "description": "其他画像备注（可选）"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "profile_read",
            "description": "读取用户学习画像（无则提示先收集）。教学前可调用以按画像调整讲解与出题方式。",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "test_log",
            "description": "记录一次测验成绩（客观验收数据）：分数/总题数/答对数/关联知识点。正确率低于 70% 的知识点自动进入复习队列。验收不依赖用户自评。",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string", "description": "计划 ID（plan_ 开头）"},
                    "title": {"type": "string", "description": "测验名称，如：阶段测验一"},
                    "score": {"type": "integer", "description": "得分（默认等于答对数）"},
                    "total": {"type": "integer", "description": "总题数/总分"},
                    "correct": {"type": "integer", "description": "答对题数（可选）"},
                    "knowledge_points": {"type": "array", "description": "涉及的知识点（可选）", "items": {"type": "string"}},
                    "note": {"type": "string", "description": "备注（可选）"},
                },
                "required": ["plan_id", "total"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mistake_add",
            "description": "记录错题：内容/归因/关联知识点，自动进入复习队列，连续复习正确后标记解决",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string"},
                    "content": {"type": "string", "description": "错题内容"},
                    "reason": {"type": "string", "description": "错误归因：概念不清/粗心/没见过"},
                    "knowledge_point": {"type": "string", "description": "关联知识点"},
                },
                "required": ["plan_id", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_reviews",
            "description": "列出复习队列（含到期状态与复习项 ID）。拿到 review_item_id 后用于 review_checkin 打卡",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string", "description": "计划 ID（可选）"},
                    "include_done": {"type": "boolean", "description": "是否包含已掌握的复习项（默认否）"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "review_checkin",
            "description": "复习打卡：报告某个复习项是否复习成功（正确/记住）。按遗忘曲线更新间隔——成功间隔翻倍拉长，失败重置为 1 天。",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string"},
                    "review_item_id": {"type": "string", "description": "复习项 ID（rv_ 开头）"},
                    "correct": {"type": "boolean", "description": "本次复习是否成功"},
                },
                "required": ["plan_id", "review_item_id", "correct"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_adjust",
            "description": "动态调整计划（调整实验）：记录调整前基线、调整内容与原因，观察窗口后自动对比调整前后效果，用于寻找最适合用户的学习方法。可同时修改方案字段（节奏/方式/验收等）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string"},
                    "content": {"type": "string", "description": "调整内容描述，如：改为每天早晨学习 45 分钟"},
                    "reason": {"type": "string", "description": "调整原因（来自数据分析）"},
                    "window_days": {"type": "integer", "description": "观察窗口天数（默认 7）"},
                    "daily_minutes": {"type": "integer"},
                    "end_date": {"type": "string", "description": "ISO 日期"},
                    "cadence": {"type": "string", "description": "检视节奏"},
                    "review_strategy": {"type": "string", "description": "复习策略"},
                    "goal": {"type": "string"},
                    "current_status": {"type": "string"},
                    "next_steps": {"type": "array", "items": {"type": "string"}},
                    "status": {"type": "string", "enum": ["active", "paused", "completed", "archived"]},
                },
                "required": ["plan_id", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "study_analyze",
            "description": "学习数据分析（规则层）：时长趋势/停滞检测/测验正确率/复习稳定性/里程碑达成率，并结算到期调整实验。分析结果用于动态调整依据。",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string", "description": "计划 ID（可选，缺省分析全部）"},
                    "days": {"type": "integer", "description": "分析窗口天数（默认 7）"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "confirm_adjustment",
            "description": "裁决到期调整实验（状态为待裁决/已出报告时使用）：采纳 → 沉淀为有效学习方法；不采纳 → 记录理由。调整是否有效由你基于对比报告判断，规则层不替你裁决。",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string"},
                    "adjustment_id": {"type": "string"},
                    "accepted": {"type": "boolean", "description": "是否采纳该调整（true=有效/采纳，false=无效/不采纳）"},
                    "reason": {"type": "string", "description": "裁决理由（不采纳时必填，采纳时建议填写）"},
                },
                "required": ["plan_id", "adjustment_id", "accepted"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_completion_check",
            "description": "完成验收报告：查看客观数据（验收项/里程碑/复习/测验正确率）是否满足完成条件。只出报告，是否完成由你裁决。",
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
            "name": "confirm_plan_completion",
            "description": "LLM 完成裁决：基于 plan_completion_check 的验收报告，由你决定是否宣布计划完成。规则层只执行你的裁决。",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string"},
                    "accepted": {"type": "boolean", "description": "true=确认完成，false=暂不完成"},
                    "report_note": {"type": "string", "description": "完成报告内容（accepted=true 时必填）"},
                },
                "required": ["plan_id", "accepted"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "daily_start",
            "description": "【每日执行单】基于重复日程模板生成某天的执行单：今日主线/产出物/投递目标/各执行块（上午审查/中午内容/下午任务）。同日期已存在则返回现有。",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "日期 YYYY-MM-DD（默认今天）"},
                    "routine_id": {"type": "string", "description": "来源重复日程任务 ID（可选）"},
                    "focus": {"type": "string", "description": "今日主线"},
                    "output_goal": {"type": "string", "description": "今日产出物（可展示物）"},
                    "delivery_goal": {"type": "string", "description": "今日投递目标"},
                    "blocks": {
                        "type": "array",
                        "description": "执行块（来自锁定方案，不得随意更改）：[{name, slot, focus, minutes}]",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "slot": {"type": "string"},
                                "focus": {"type": "string"},
                                "minutes": {"type": "integer"},
                            },
                        },
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "daily_list",
            "description": "【每日执行单】列出执行单，可按日期过滤",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "日期 YYYY-MM-DD（可选）"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "daily_block_done",
            "description": "【每日执行单】标记执行单中某个执行块的完成状态",
            "parameters": {
                "type": "object",
                "properties": {
                    "daily_id": {"type": "string"},
                    "block_id": {"type": "string"},
                    "done": {"type": "boolean", "description": "是否完成（默认 true）"},
                    "note": {"type": "string", "description": "该块备注（可选）"},
                },
                "required": ["daily_id", "block_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "daily_summary",
            "description": "【每日执行单】收尾总结：今日总结/产出物是否完成/卡点/明日主线，写入记忆笔记",
            "parameters": {
                "type": "object",
                "properties": {
                    "daily_id": {"type": "string"},
                    "summary": {"type": "string", "description": "今日总结"},
                    "output_done": {"type": "boolean", "description": "产出物是否完成"},
                    "blockers": {"type": "array", "items": {"type": "string"}, "description": "卡点（可选）"},
                    "tomorrow_focus": {"type": "string", "description": "明日主线（可选）"},
                },
                "required": ["daily_id", "summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pomodoro_start",
            "description": "【番茄钟】启动一个专注番茄钟（同一时间仅一个运行中；已有运行中则返回现有）",
            "parameters": {
                "type": "object",
                "properties": {
                    "minutes": {"type": "integer", "description": "专注时长（分钟，默认 25）"},
                    "plan_id": {"type": "string", "description": "关联计划（可选）"},
                    "block_id": {"type": "string", "description": "关联执行块（可选）"},
                    "block_name": {"type": "string", "description": "执行块名称（可选）"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pomodoro_stop",
            "description": "【番茄钟】停止运行中的番茄钟，专注时长自动记入学习时长",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pomodoro_status",
            "description": "【番茄钟】查询运行中的番茄钟、剩余时间、今日已完成番茄数与专注分钟",
            "parameters": {"type": "object", "properties": {}},
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
    """注册 31 个学习工具（handler 与 schema 成对注册，T2 技能层）"""
    handlers = _make_handlers(study_repo, cognitive_repo, user_id)
    for tool in _STUDY_TOOLS:
        name = tool["function"]["name"]
        registry.register(name, handlers[name], schema=tool, level=level)
