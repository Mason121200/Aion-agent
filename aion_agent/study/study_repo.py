"""JsonStudyRepo —— 学习场景数据仓库（study.json）

参考 zero_code 的「长期计划笔记体系（LIFE_PLAN）」范式，适配 Aion 轻量 JSON 存储：
- 计划 = 活的文档：当前状态只保留最新；决策记录只追加一行（进度追溯）
- 进度：显式 progress（检视时设定）+ 里程碑 + 学习时长/期望时长 三重推算
- 动态调整：plan_update（改目标/截止/时长/状态/下一步）+ plan_checkin（检视）
- 资料 / 学习记录 / 提醒 与计划解耦，可按 plan_id 关联

纯标准库 + 同步接口（工具 handler 与本地服务器直接调用），
原子写落盘（先 .tmp 再替换），重启可恢复。
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PLAN_STATUSES = ("active", "paused", "completed", "archived")


def _now() -> datetime:
    return datetime.now()


def _parse_dt(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def _to_iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _next_interval(current: int, correct: bool) -> int:
    """遗忘曲线简化版：复习成功间隔翻倍（上限 30 天），失败重置为 1 天"""
    if correct:
        return min(max(int(current) * 2, int(current) + 1), 30)
    return 1


class JsonStudyRepo:
    """学习场景仓库：计划 / 资料 / 学习记录 / 提醒"""

    def __init__(self, persist_dir: Optional[str] = None):
        self._persist_dir = Path(persist_dir) if persist_dir else None
        self._data: Dict[str, Dict[str, Any]] = {
            "plans": {}, "materials": {}, "sessions": {}, "reminders": {},
            "tests": {}, "mistakes": {}, "review_items": {}, "profile": {},
            "dailies": {}, "pomodoros": {},
        }
        self._load()

    # ==================== 持久化 ====================

    @property
    def _persist_file(self) -> Optional[Path]:
        if self._persist_dir is None:
            return None
        return self._persist_dir / "study.json"

    def _load(self) -> None:
        pf = self._persist_file
        if pf is None or not pf.exists():
            return
        try:
            raw = json.loads(pf.read_text(encoding="utf-8"))
            for key in self._data:
                if isinstance(raw.get(key), dict):
                    self._data[key] = raw[key]
            logger.info(f"已从 {pf} 恢复学习数据")
        except Exception as e:
            logger.warning(f"学习数据恢复失败，从空库开始: {e}")

    def _save(self) -> None:
        pf = self._persist_file
        if pf is None:
            return
        try:
            pf.parent.mkdir(parents=True, exist_ok=True)
            tmp = pf.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
            tmp.replace(pf)
        except Exception as e:
            logger.error(f"学习数据持久化失败: {e}")

    # ==================== 计划：创建 / 查询 ====================

    def create_plan(
        self,
        *,
        title: str,
        subject: str = "",
        goal: str = "",
        why: str = "",
        cadence: str = "",
        end_date=None,
        daily_minutes: int = 0,
        milestones: Optional[List[dict]] = None,
        acceptance: Optional[List[str]] = None,
        review_strategy: str = "",
    ) -> dict:
        """创建长期学习计划（同标题活跃计划去重，返回现有计划）"""
        title = str(title or "").strip()
        if not title:
            raise ValueError("缺少参数 title（计划标题）")
        for p in self.list_plans(status=None):
            if p.get("status") not in ("completed", "archived") and p.get("title") == title:
                return {"plan": p, "reused": True}
        plan_id = f"plan_{uuid.uuid4().hex[:8]}"
        plan = {
            "plan_id": plan_id,
            "title": title,
            "subject": str(subject or "").strip(),
            "goal": str(goal or "").strip(),
            "why": str(why or "").strip(),
            "cadence": str(cadence or "").strip(),
            "start_date": _to_iso(_now()),
            "end_date": _to_iso(_parse_dt(end_date)),
            "daily_minutes": max(int(daily_minutes or 0), 0),
            "status": "active",
            "progress": 0,
            "current_status": "（刚开始）",
            "next_steps": [],
            "acceptance": [
                {
                    "acceptance_id": f"acc_{uuid.uuid4().hex[:8]}",
                    "title": str(a).strip(),
                    "done": False,
                    "done_at": None,
                }
                for a in (acceptance or [])
                if str(a).strip()
            ],
            "review_strategy": str(review_strategy or "").strip(),
            "adjustments": [],
            "milestones": [
                {
                    "milestone_id": f"ms_{uuid.uuid4().hex[:8]}",
                    "title": str(m.get("title") or "").strip() or f"阶段{i + 1}",
                    "due_date": _to_iso(_parse_dt(m.get("due_date"))),
                    "done": False,
                    "done_at": None,
                }
                for i, m in enumerate(milestones or [])
                if str(m.get("title") or "").strip()
            ],
            "decision_log": [
                {"ts": _to_iso(_now()), "text": "创建计划"}
            ],
            "created_at": _to_iso(_now()),
            "updated_at": _to_iso(_now()),
        }
        self._data["plans"][plan_id] = plan
        self._save()
        return {"plan": plan, "reused": False}

    def get_plan(self, plan_id: str) -> Optional[dict]:
        return self._data["plans"].get(plan_id)

    def list_plans(self, status: Optional[str] = None, limit: int = 50) -> List[dict]:
        plans = list(self._data["plans"].values())
        if status:
            plans = [p for p in plans if p.get("status") == status]
        plans.sort(key=lambda p: p.get("created_at") or "", reverse=True)
        out = []
        for p in plans[:limit]:
            item = dict(p)
            item["progress_info"] = self._plan_progress(p)
            out.append(item)
        return out

    def plan_detail(self, plan_id: str) -> Optional[dict]:
        plan = self.get_plan(plan_id)
        if plan is None:
            return None
        sessions = [
            s for s in self._data["sessions"].values()
            if s.get("plan_id") == plan_id
        ]
        sessions.sort(key=lambda s: s.get("created_at") or "", reverse=True)
        return {
            **dict(plan),
            "progress_info": self._plan_progress(plan),
            "sessions": sessions[:50],
        }

    def _plan_progress(self, plan: dict) -> dict:
        """进度推算：显式 progress 优先，其次里程碑，再按期望时长"""
        milestones = plan.get("milestones") or []
        total_ms = len(milestones)
        done_ms = sum(1 for m in milestones if m.get("done"))
        plan_sessions = [
            s for s in self._data["sessions"].values()
            if s.get("plan_id") == plan.get("plan_id")
        ]
        total_minutes = sum(int(s.get("minutes") or 0) for s in plan_sessions)
        start = _parse_dt(plan.get("start_date")) or _now()
        end = _parse_dt(plan.get("end_date"))
        daily = int(plan.get("daily_minutes") or 0)
        expected = None
        if daily > 0 and end is not None:
            total_days = max((end - start).days, 1)
            elapsed = max((_now() - start).days, 0)
            expected = min(elapsed, total_days) * daily
        progress = max(int(plan.get("progress") or 0), 0)
        source = "manual"
        if total_ms:
            ms_progress = round(done_ms / total_ms * 100)
            if not progress:
                progress, source = ms_progress, "milestone"
        elif expected and expected > 0:
            t_progress = min(round(total_minutes / expected * 100), 100)
            if not progress:
                progress, source = t_progress, "time"
        pace = "unknown"
        if expected and expected > 0:
            if total_minutes >= expected * 1.05:
                pace = "ahead"
            elif total_minutes >= expected * 0.8:
                pace = "on_track"
            else:
                pace = "behind"
        return {
            "progress": min(progress, 100),
            "progress_source": source,
            "milestones_total": total_ms,
            "milestones_done": done_ms,
            "total_minutes": total_minutes,
            "expected_minutes": expected,
            "pace": pace,
        }

    # ==================== 计划：动态调整 ====================

    def update_plan(self, plan_id: str, *, decision: Optional[str] = None, **fields) -> Optional[dict]:
        """更新计划字段（当前状态/下一步/截止/时长/进度/状态等），可选追加决策记录"""
        plan = self.get_plan(plan_id)
        if plan is None:
            return None
        allowed = {
            "title", "subject", "goal", "why", "cadence", "review_strategy",
            "end_date", "daily_minutes", "status", "progress",
            "current_status", "next_steps",
        }
        for k, v in fields.items():
            if k in allowed and v is not None:
                if k == "progress":
                    plan[k] = max(0, min(100, int(v or 0)))
                elif k == "daily_minutes":
                    plan[k] = max(int(v or 0), 0)
                elif k == "next_steps":
                    plan[k] = [str(x).strip() for x in (v or []) if str(x).strip()][:5]
                elif k == "end_date":
                    plan[k] = _to_iso(_parse_dt(v))
                elif k == "status":
                    plan[k] = v if v in _PLAN_STATUSES else plan.get("status")
                else:
                    plan[k] = str(v or "").strip()
        if decision and str(decision).strip():
            plan["decision_log"].append({
                "ts": _to_iso(_now()),
                "text": str(decision).strip(),
            })
        plan["updated_at"] = _to_iso(_now())
        self._save()
        return plan

    def add_milestone(self, plan_id: str, title: str, due_date=None) -> Optional[dict]:
        plan = self.get_plan(plan_id)
        if plan is None:
            return None
        plan["milestones"].append({
            "milestone_id": f"ms_{uuid.uuid4().hex[:8]}",
            "title": str(title or "").strip() or "新阶段",
            "due_date": _to_iso(_parse_dt(due_date)),
            "done": False,
            "done_at": None,
        })
        plan["updated_at"] = _to_iso(_now())
        self._save()
        return plan

    def complete_milestone(self, plan_id: str, milestone_id: str) -> Optional[dict]:
        plan = self.get_plan(plan_id)
        if plan is None:
            return None
        for m in plan.get("milestones") or []:
            if m.get("milestone_id") == milestone_id:
                m["done"] = True
                m["done_at"] = _to_iso(_now())
                self._ensure_review_item(
                    plan_id, "material", milestone_id,
                    f"复习里程碑：{m.get('title') or milestone_id}",
                )
                plan["updated_at"] = _to_iso(_now())
                self._save()
                return plan
        return None

    def archive_plan(self, plan_id: str) -> Optional[dict]:
        plan = self.get_plan(plan_id)
        if plan is None:
            return None
        plan["status"] = "archived"
        plan["updated_at"] = _to_iso(_now())
        self._save()
        return plan

    # ==================== 资料 ====================

    def add_material(
        self, *, title: str, subject: str = "", source: str = "",
        summary: str = "", tags: Optional[List[str]] = None,
    ) -> dict:
        material = {
            "material_id": f"mat_{uuid.uuid4().hex[:8]}",
            "title": str(title or "").strip(),
            "subject": str(subject or "").strip(),
            "source": str(source or "").strip(),
            "summary": str(summary or "").strip(),
            "tags": [str(t).strip() for t in (tags or []) if str(t).strip()],
            "created_at": _to_iso(_now()),
            "archived": False,
        }
        self._data["materials"][material["material_id"]] = material
        self._save()
        return material

    def list_materials(
        self, subject: Optional[str] = None, query: Optional[str] = None,
        limit: int = 50,
    ) -> List[dict]:
        materials = [m for m in self._data["materials"].values() if not m.get("archived")]
        if subject:
            materials = [m for m in materials if subject in (m.get("subject") or "")]
        if query:
            q = str(query).lower()
            materials = [
                m for m in materials
                if q in (
                    (m.get("title") or "") + (m.get("summary") or "") + (m.get("source") or "")
                ).lower()
            ]
        materials.sort(key=lambda m: m.get("created_at") or "", reverse=True)
        return materials[:limit]

    def archive_material(self, material_id: str) -> bool:
        m = self._data["materials"].get(material_id)
        if m is None:
            return False
        m["archived"] = True
        self._save()
        return True

    # ==================== 学习记录 ====================

    def log_session(
        self, *, subject: str, minutes: int, note: str = "", plan_id: Optional[str] = None,
    ) -> dict:
        session = {
            "session_id": f"ses_{uuid.uuid4().hex[:8]}",
            "subject": str(subject or "").strip(),
            "minutes": max(int(minutes or 0), 0),
            "note": str(note or "").strip(),
            "plan_id": plan_id,
            "created_at": _to_iso(_now()),
        }
        self._data["sessions"][session["session_id"]] = session
        self._save()
        return session

    def list_sessions(
        self, plan_id: Optional[str] = None, since: Optional[datetime] = None, limit: int = 50,
    ) -> List[dict]:
        sessions = list(self._data["sessions"].values())
        if plan_id:
            sessions = [s for s in sessions if s.get("plan_id") == plan_id]
        if since is not None:
            sessions = [
                s for s in sessions
                if (_parse_dt(s.get("created_at")) or _now()) >= since
            ]
        sessions.sort(key=lambda s: s.get("created_at") or "", reverse=True)
        return sessions[:limit]

    def today_minutes(self, day: Optional[datetime] = None) -> int:
        d = day or _now()
        return sum(
            int(s.get("minutes") or 0)
            for s in self._data["sessions"].values()
            if (_parse_dt(s.get("created_at")) or _now()).date() == d.date()
        )

    # ==================== 每日执行单 ====================

    def create_daily(
        self,
        *,
        date: str = "",
        routine_id: str = "",
        focus: str = "",
        output_goal: str = "",
        delivery_goal: str = "",
        blocks: Optional[List[dict]] = None,
    ) -> dict:
        """生成某天的执行单（同日期活跃执行单去重），基于重复日程模板 blocks 实例化"""
        day = str(date or "").strip() or _now().date().isoformat()
        for existing in self._data["dailies"].values():
            if existing.get("date") == day and existing.get("status") == "active":
                return {"daily": existing, "reused": True}
        daily_id = f"daily_{uuid.uuid4().hex[:8]}"
        normalized = []
        for b in blocks or []:
            name = str(b.get("name") or "").strip()
            if not name:
                continue
            normalized.append({
                "block_id": f"blk_{uuid.uuid4().hex[:8]}",
                "name": name,
                "slot": str(b.get("slot") or "").strip(),
                "focus": str(b.get("focus") or "").strip(),
                "minutes": max(int(b.get("minutes") or 0), 0),
                "done": False,
                "note": "",
            })
        entry = {
            "daily_id": daily_id,
            "date": day,
            "routine_id": str(routine_id or "").strip(),
            "focus": str(focus or "").strip(),
            "output_goal": str(output_goal or "").strip(),
            "delivery_goal": str(delivery_goal or "").strip(),
            "blocks": normalized,
            "status": "active",
            "summary": "",
            "output_done": False,
            "blockers": [],
            "tomorrow_focus": "",
            "created_at": _to_iso(_now()),
            "updated_at": _to_iso(_now()),
            "completed_at": None,
        }
        self._data["dailies"][daily_id] = entry
        self._save()
        return {"daily": entry, "reused": False}

    def get_daily(self, daily_id: str) -> Optional[dict]:
        return self._data["dailies"].get(daily_id)

    def list_dailies(self, date: str = "", limit: int = 60) -> List[dict]:
        items = list(self._data["dailies"].values())
        if date:
            items = [d for d in items if d.get("date") == date]
        items.sort(key=lambda d: d.get("date") or "", reverse=True)
        return items[:limit]

    def update_daily_block(
        self, daily_id: str, block_id: str, *, done: bool = True, note: str = ""
    ) -> Optional[dict]:
        """标记执行单中某个块的完成状态"""
        entry = self.get_daily(daily_id)
        if entry is None:
            return None
        for b in entry.get("blocks") or []:
            if b.get("block_id") == block_id:
                b["done"] = bool(done)
                b["note"] = str(note or "").strip()
                entry["updated_at"] = _to_iso(_now())
                self._save()
                return entry
        return None

    def complete_daily(
        self,
        daily_id: str,
        *,
        summary: str,
        output_done: bool = False,
        blockers: Optional[List[str]] = None,
        tomorrow_focus: str = "",
    ) -> Optional[dict]:
        """收尾总结：记录今日产出物完成情况、卡点、明日主线，并置为 completed"""
        entry = self.get_daily(daily_id)
        if entry is None:
            return None
        entry["summary"] = str(summary or "").strip()
        entry["output_done"] = bool(output_done)
        entry["blockers"] = [
            str(x).strip() for x in (blockers or []) if str(x).strip()
        ][:5]
        entry["tomorrow_focus"] = str(tomorrow_focus or "").strip()
        entry["status"] = "completed"
        entry["completed_at"] = _to_iso(_now())
        entry["updated_at"] = _to_iso(_now())
        self._save()
        return entry

    # ==================== 番茄钟 ====================

    def start_pomodoro(
        self,
        *,
        minutes: int = 25,
        plan_id: str = "",
        block_id: str = "",
        block_name: str = "",
    ) -> dict:
        """启动一个番茄钟（同一时间只允许一个运行中）"""
        minutes = max(int(minutes or 0), 1) if minutes else 25
        for p in self._data["pomodoros"].values():
            if p.get("status") == "running":
                return {"pomodoro": p, "reused": True}
        pomodoro = {
            "pomodoro_id": f"pom_{uuid.uuid4().hex[:8]}",
            "start_at": _to_iso(_now()),
            "minutes": minutes,
            "plan_id": str(plan_id or "").strip(),
            "block_id": str(block_id or "").strip(),
            "block_name": str(block_name or "").strip(),
            "status": "running",
            "stopped_at": None,
            "elapsed_minutes": 0,
        }
        self._data["pomodoros"][pomodoro["pomodoro_id"]] = pomodoro
        self._save()
        return {"pomodoro": pomodoro, "reused": False}

    def stop_pomodoro(self) -> Optional[dict]:
        """停止运行中的番茄钟：计算专注分钟，自动记入学习时长（sessions）"""
        running = None
        for p in self._data["pomodoros"].values():
            if p.get("status") == "running":
                running = p
                break
        if running is None:
            return None
        start = _parse_dt(running["start_at"]) or _now()
        elapsed = max(int((_now() - start).total_seconds() // 60), 1)
        running["status"] = "completed"
        running["stopped_at"] = _to_iso(_now())
        running["elapsed_minutes"] = elapsed
        self.log_session(
            subject="番茄钟专注",
            minutes=elapsed,
            note=f"🍅 {running.get('block_name') or '专注'}",
            plan_id=running.get("plan_id") or None,
        )
        return running

    def pomodoro_status(self) -> dict:
        """运行中的番茄钟 + 今日已完成番茄数与专注分钟"""
        now = _now()
        running = None
        today_done = 0
        today_focus_minutes = 0
        for p in self._data["pomodoros"].values():
            if p.get("status") == "running":
                running = p
            elif p.get("status") == "completed":
                done_at = _parse_dt(p.get("stopped_at"))
                if done_at is not None and done_at.date() == now.date():
                    today_done += 1
                    today_focus_minutes += int(p.get("elapsed_minutes") or 0)
        remaining = None
        if running is not None:
            start = _parse_dt(running["start_at"]) or now
            elapsed_now = int((now - start).total_seconds() // 60)
            remaining = max(int(running.get("minutes") or 25) - elapsed_now, 0)
        return {
            "running": running,
            "remaining_minutes": remaining,
            "today_done": today_done,
            "today_focus_minutes": today_focus_minutes,
        }

    # ==================== 提醒 ====================

    def create_reminder(self, *, title: str, remind_at, content: str = "") -> dict:
        parsed = _parse_dt(remind_at) or (_now() + timedelta(hours=1))
        reminder = {
            "reminder_id": f"rem_{uuid.uuid4().hex[:8]}",
            "title": str(title or "").strip(),
            "content": str(content or "").strip(),
            "remind_at": _to_iso(parsed),
            "done": False,
            "notified_at": None,
            "created_at": _to_iso(_now()),
        }
        self._data["reminders"][reminder["reminder_id"]] = reminder
        self._save()
        return reminder

    def list_reminders(self, include_done: bool = False, limit: int = 100) -> List[dict]:
        reminders = list(self._data["reminders"].values())
        if not include_done:
            reminders = [r for r in reminders if not r.get("done")]
        reminders.sort(key=lambda r: r.get("remind_at") or "")
        return reminders[:limit]

    def due_reminders(self, now: Optional[datetime] = None) -> List[dict]:
        now = now or _now()
        return [
            r for r in self._data["reminders"].values()
            if not r.get("done") and (_parse_dt(r.get("remind_at")) or now) <= now
        ]

    def fire_due_reminders(self, now: Optional[datetime] = None) -> List[dict]:
        """触发到期提醒：把「已到期且尚未通知」的提醒标记为已通知并返回

        供服务端定时器调用；标记持久化到 study.json，
        服务重启后同一提醒不会重复通知（避免重复轰炸）。
        """
        now = now or _now()
        fired = []
        for r in self._data["reminders"].values():
            if r.get("done") or r.get("notified_at"):
                continue
            due_at = _parse_dt(r.get("remind_at")) or now
            if due_at <= now:
                r["notified_at"] = _to_iso(now)
                fired.append(r)
        if fired:
            self._save()
        return fired

    def upcoming_reminders(self, limit: int = 10, now: Optional[datetime] = None) -> List[dict]:
        now = now or _now()
        upcoming = [
            r for r in self._data["reminders"].values()
            if not r.get("done") and (_parse_dt(r.get("remind_at")) or now) > now
        ]
        upcoming.sort(key=lambda r: r.get("remind_at") or "")
        return upcoming[:limit]

    def complete_reminder(self, reminder_id: str) -> bool:
        r = self._data["reminders"].get(reminder_id)
        if r is None:
            return False
        r["done"] = True
        self._save()
        return True

    # ==================== 评估：测验 / 错题 ====================

    def add_test(
        self, *, plan_id: str, title: str = "", score: int = 0, total: int = 0,
        correct: Optional[int] = None, knowledge_points: Optional[List[str]] = None,
        note: str = "",
    ) -> dict:
        """记录一次测验（客观数据，验收依据），正确率<70% 的知识点自动入复习队列"""
        plan = self.get_plan(plan_id)
        if plan is None:
            raise ValueError(f"未找到计划: {plan_id}")
        total = max(int(total or 0), 0)
        if total <= 0:
            raise ValueError("参数 total（总题数/总分）必须大于 0")
        score = max(int(score or 0), 0)
        correct = score if correct is None else max(min(int(correct or 0), total), 0)
        test = {
            "test_id": f"test_{uuid.uuid4().hex[:8]}",
            "plan_id": plan_id,
            "title": str(title or "").strip() or "阶段测验",
            "score": score,
            "total": total,
            "correct": correct,
            "accuracy": round(correct / total * 100),
            "knowledge_points": [str(k).strip() for k in (knowledge_points or []) if str(k).strip()],
            "note": str(note or "").strip(),
            "created_at": _to_iso(_now()),
        }
        self._data["tests"][test["test_id"]] = test
        if test["accuracy"] < 70 and test["knowledge_points"]:
            for kp in test["knowledge_points"]:
                self._ensure_review_item(
                    plan_id, "knowledge_point",
                    f"{test['test_id']}:{kp}",
                    f"薄弱知识点：{kp}（{test['title']} 正确率 {test['accuracy']}%）",
                )
        self._save()
        return test

    def list_tests(self, plan_id: Optional[str] = None, limit: int = 50) -> List[dict]:
        tests = list(self._data["tests"].values())
        if plan_id:
            tests = [t for t in tests if t.get("plan_id") == plan_id]
        tests.sort(key=lambda t: t.get("created_at") or "", reverse=True)
        return tests[:limit]

    def add_mistake(
        self, *, plan_id: str, content: str, reason: str = "",
        knowledge_point: str = "",
    ) -> dict:
        """记录错题（归因/关联知识点），自动进入复习队列"""
        plan = self.get_plan(plan_id)
        if plan is None:
            raise ValueError(f"未找到计划: {plan_id}")
        content = str(content or "").strip()
        if not content:
            raise ValueError("缺少参数 content（错题内容）")
        mistake = {
            "mistake_id": f"mis_{uuid.uuid4().hex[:8]}",
            "plan_id": plan_id,
            "content": content,
            "reason": str(reason or "").strip(),
            "knowledge_point": str(knowledge_point or "").strip(),
            "resolved": False,
            "created_at": _to_iso(_now()),
        }
        self._data["mistakes"][mistake["mistake_id"]] = mistake
        prefix = f"【错题·{knowledge_point}】" if knowledge_point else "【错题】"
        self._ensure_review_item(
            plan_id, "mistake", mistake["mistake_id"],
            f"{prefix}{content}" + (f"（归因：{reason}）" if reason else ""),
        )
        self._save()
        return mistake

    def list_mistakes(self, plan_id: Optional[str] = None, include_resolved: bool = False, limit: int = 50) -> List[dict]:
        mistakes = list(self._data["mistakes"].values())
        if plan_id:
            mistakes = [m for m in mistakes if m.get("plan_id") == plan_id]
        if not include_resolved:
            mistakes = [m for m in mistakes if not m.get("resolved")]
        mistakes.sort(key=lambda m: m.get("created_at") or "", reverse=True)
        return mistakes[:limit]

    # ==================== 复习：间隔调度（进度 + 遗忘曲线） ====================

    def _ensure_review_item(self, plan_id: str, source_type: str, source_id: str, content: str) -> dict:
        """确保复习队列存在某来源的复习项（幂等）：材料/知识点/错题"""
        for it in self._data["review_items"].values():
            if (it.get("plan_id") == plan_id and it.get("source_type") == source_type
                    and it.get("source_id") == source_id):
                return it
        item = {
            "review_item_id": f"rv_{uuid.uuid4().hex[:8]}",
            "plan_id": plan_id,
            "source_type": source_type,  # material | knowledge_point | mistake
            "source_id": source_id,
            "content": str(content or "").strip(),
            "interval_days": 1,
            "next_review_at": _to_iso(_now()),
            "last_result": None,
            "streak": 0,
            "last_reviewed_at": None,
            "resolved": False,
        }
        self._data["review_items"][item["review_item_id"]] = item
        self._save()
        return item

    def schedule_reviews(self, plan_id: str, now: Optional[datetime] = None) -> List[dict]:
        """复习调度：进度驱动（已完成的里程碑入队）+ 返回到期复习项"""
        plan = self.get_plan(plan_id)
        if plan is None:
            raise ValueError(f"未找到计划: {plan_id}")
        now = now or _now()
        for m in plan.get("milestones") or []:
            if m.get("done"):
                self._ensure_review_item(plan_id, "material", m["milestone_id"], f"复习里程碑：{m['title']}")
        items = [
            it for it in self._data["review_items"].values()
            if it.get("plan_id") == plan_id and not it.get("resolved")
        ]
        due = [it for it in items if (_parse_dt(it.get("next_review_at")) or now) <= now]
        due.sort(key=lambda it: it.get("next_review_at") or "")
        return due

    def list_reviews(
        self, plan_id: Optional[str] = None, include_done: bool = False, limit: int = 50,
    ) -> List[dict]:
        """列出复习队列（含到期状态），供 agent 拿到 review_item_id 后打卡"""
        items = list(self._data["review_items"].values())
        if plan_id:
            items = [it for it in items if it.get("plan_id") == plan_id]
        if not include_done:
            items = [it for it in items if not it.get("resolved")]
        items.sort(key=lambda it: it.get("next_review_at") or "")
        return items[:limit]

    def review_checkin(self, *, plan_id: str, review_item_id: str, correct: bool) -> dict:
        """复习打卡：按遗忘曲线更新间隔（正确翻倍拉长，错误重置为 1 天）"""
        item = self._data["review_items"].get(review_item_id)
        if item is None or item.get("plan_id") != plan_id:
            raise ValueError(f"未找到复习项: {review_item_id}")
        item["last_result"] = bool(correct)
        item["streak"] = (item.get("streak") or 0) + 1 if correct else 0
        item["last_reviewed_at"] = _to_iso(_now())
        item["interval_days"] = _next_interval(item.get("interval_days") or 1, bool(correct))
        item["next_review_at"] = _to_iso(_now() + timedelta(days=item["interval_days"]))
        if correct and (item.get("streak") or 0) >= 3:
            item["resolved"] = True
        if correct and (item.get("streak") or 0) >= 2 and item.get("source_type") == "mistake":
            mid = item.get("source_id")
            if mid in self._data["mistakes"]:
                self._data["mistakes"][mid]["resolved"] = True
        self._save()
        return item

    # ==================== 动态调整：调整实验（基线对比） ====================

    def _effect_snapshot(self, plan_id: str, window_days: int = 7) -> dict:
        """效果指标快照（调整实验的基线/结果对比用）"""
        now = _now()
        since = now - timedelta(days=window_days)
        sessions = [
            s for s in self._data["sessions"].values()
            if s.get("plan_id") == plan_id and (_parse_dt(s.get("created_at")) or now) >= since
        ]
        tests = [
            t for t in self._data["tests"].values()
            if t.get("plan_id") == plan_id and (_parse_dt(t.get("created_at")) or now) >= since
        ]
        items = [
            it for it in self._data["review_items"].values()
            if it.get("plan_id") == plan_id
        ]
        now_dt = _parse_dt
        days_active = len({(_parse_dt(s.get("created_at")) or now).date() for s in sessions})
        due = sum(1 for it in items if not it.get("resolved") and (now_dt(it.get("next_review_at")) or now) <= now)
        plan = self.get_plan(plan_id)
        return {
            "window_days": window_days,
            "total_minutes": sum(int(s.get("minutes") or 0) for s in sessions),
            "session_count": len(sessions),
            "days_active": days_active,
            "avg_accuracy": round(sum(t.get("accuracy", 0) for t in tests) / len(tests)) if tests else None,
            "review_due": due,
            "progress": (plan or {}).get("progress", 0),
            "pace": self._plan_progress(plan).get("pace") if plan else "unknown",
        }

    @staticmethod
    def _effect_report(current: dict, base: dict) -> dict:
        """调整前后效果对比报告（客观数据，供 LLM 裁决；规则层不产出结论）

        每项指标给出 before/after 与是否改善（improved），
        只汇总改善项数量，不判定有效/无效——裁决权归 LLM。
        """
        def metric(name: str, label: str, before, after, better) -> dict:
            return {
                "metric": name,
                "label": label,
                "before": before,
                "after": after,
                "improved": None if before is None or after is None else better(after, before),
            }

        def better_ge(a, b):
            return a >= b

        def better_le(a, b):
            return a <= b

        metrics = []
        ca, ba = current.get("avg_accuracy"), base.get("avg_accuracy")
        if ca is not None or ba is not None:
            metrics.append(metric("avg_accuracy", "测验正确率", ba, ca, better_ge))
        metrics.append(metric("total_minutes", "学习时长(分钟)", base.get("total_minutes"), current.get("total_minutes"), better_ge))
        metrics.append(metric("review_due", "复习积压", base.get("review_due"), current.get("review_due"), better_le))
        metrics.append(metric("progress", "计划进度", base.get("progress"), current.get("progress"), better_ge))
        pace_order = {"behind": 0, "unknown": 1, "on_track": 2, "ahead": 3}
        bp, cp = base.get("pace"), current.get("pace")
        if bp in pace_order and cp in pace_order:
            metrics.append(metric("pace", "节奏", bp, cp, lambda a, b: pace_order[a] >= pace_order[b]))
        evaluated = [m for m in metrics if m["improved"] is not None]
        return {
            "metrics": metrics,
            "evaluated_count": len(evaluated),
            "improved_count": sum(1 for m in evaluated if m["improved"]),
        }

    def record_adjustment(
        self, *, plan_id: str, content: str, reason: str = "", window_days: int = 7, **fields,
    ) -> dict:
        """记录一次调整实验：调整前基线快照 + 应用调整字段（自主调整，数据留痕）"""
        plan = self.get_plan(plan_id)
        if plan is None:
            raise ValueError(f"未找到计划: {plan_id}")
        content = str(content or "").strip()
        if not content:
            raise ValueError("缺少参数 content（调整内容）")
        adj = {
            "adjustment_id": f"adj_{uuid.uuid4().hex[:8]}",
            "ts": _to_iso(_now()),
            "content": content,
            "reason": str(reason or "").strip(),
            "baseline": self._effect_snapshot(plan_id),
            "window_days": max(int(window_days or 7), 1),
            "evaluated_at": None,
            "outcome": None,
            "conclusion": "pending",  # pending / effective / ineffective
        }
        plan.setdefault("adjustments", []).append(adj)
        allowed = {
            "title", "subject", "goal", "why", "cadence", "review_strategy",
            "end_date", "daily_minutes", "status", "progress",
            "current_status", "next_steps",
        }
        for k, v in fields.items():
            if k in allowed and v is not None:
                if k == "progress":
                    plan[k] = max(0, min(100, int(v or 0)))
                elif k == "daily_minutes":
                    plan[k] = max(int(v or 0), 0)
                elif k == "next_steps":
                    plan[k] = [str(x).strip() for x in (v or []) if str(x).strip()][:5]
                elif k == "end_date":
                    plan[k] = _to_iso(_parse_dt(v))
                elif k == "status":
                    plan[k] = v if v in _PLAN_STATUSES else plan.get("status")
                else:
                    plan[k] = str(v or "").strip()
        plan["decision_log"].append({
            "ts": _to_iso(_now()),
            "text": f"调整：{content}" + (f"（原因：{reason}）" if reason else ""),
        })
        plan["updated_at"] = _to_iso(_now())
        self._save()
        return adj

    def evaluate_adjustments(self, plan_id: str, now: Optional[datetime] = None) -> List[dict]:
        """结算到期调整实验：生成前后对比报告（不裁决），标记待 LLM 裁决

        conclusion: pending -> pending_review（报告已生成） -> effective/ineffective（LLM 裁决后）
        """
        plan = self.get_plan(plan_id)
        if plan is None:
            return []
        now = now or _now()
        results = []
        for adj in plan.get("adjustments") or []:
            if adj.get("evaluated_at"):
                continue
            started = _parse_dt(adj.get("ts")) or now
            if (now - started).days < int(adj.get("window_days") or 7):
                continue
            current = self._effect_snapshot(plan_id)
            adj["outcome"] = current
            adj["report"] = self._effect_report(current, adj.get("baseline") or {})
            adj["evaluated_at"] = _to_iso(now)
            adj["conclusion"] = "pending_review"  # 规则只出报告，裁决归 LLM
            plan["decision_log"].append({
                "ts": _to_iso(now),
                "text": "调整实验结算：对比报告已生成，待 LLM 裁决（"
                        + str(adj.get("content") or "") + "）",
            })
            results.append(adj)
        if results:
            plan["updated_at"] = _to_iso(now)
            self._save()
        return results

    def settle_due_adjustments(self, now: Optional[datetime] = None) -> List[dict]:
        """后台自动结算所有到期未评估的调整实验（幂等：已结算跳过）。

        遍历所有计划，把 window_days 已过且尚未 evaluate 的实验逐一结算，
        返回结算明细（计划标题/调整内容/结论/前后效果快照），
        由 runtime 定时器调用，负责通知用户与认知沉淀。
        """
        now = now or _now()
        settled = []
        for plan_id in list(self._data["plans"].keys()):
            for adj in self.evaluate_adjustments(plan_id, now=now):
                plan = self.get_plan(plan_id) or {}
                settled.append({
                    "plan_id": plan_id,
                    "plan_title": plan.get("title", ""),
                    "adjustment_id": adj.get("adjustment_id"),
                    "content": adj.get("content"),
                    "reason": adj.get("reason"),
                    "conclusion": adj.get("conclusion"),
                    "baseline": adj.get("baseline") or {},
                    "outcome": adj.get("outcome") or {},
                    "report": adj.get("report") or {},
                    "evaluated_at": adj.get("evaluated_at"),
                })
        return settled

    def confirm_adjustment(
        self, plan_id: str, adjustment_id: str, *, accepted: bool, reason: str = "",
    ) -> dict:
        """LLM 裁决到期调整：采纳 → effective + 沉淀画像；不采纳 → ineffective + 留痕原因

        规则层只执行裁决结果，不代替 LLM 判定。
        """
        plan = self.get_plan(plan_id)
        if plan is None:
            raise ValueError(f"未找到计划: {plan_id}")
        for adj in plan.get("adjustments") or []:
            if adj.get("adjustment_id") != adjustment_id:
                continue
            if adj.get("reviewed_at"):
                return {"confirmed": False, "already": True, "adjustment": adj}
            if adj.get("conclusion") != "pending_review":
                return {
                    "confirmed": False,
                    "error": f"该实验当前状态为 {adj.get('conclusion')}，仅待裁决状态可裁决",
                }
            adj["conclusion"] = "effective" if accepted else "ineffective"
            adj["accepted"] = bool(accepted)
            adj["reviewer"] = "llm"
            adj["review_reason"] = str(reason or "").strip()
            adj["reviewed_at"] = _to_iso(_now())
            plan["decision_log"].append({
                "ts": _to_iso(_now()),
                "text": "调整裁决：" + ("采纳" if accepted else "不采纳")
                        + "「" + str(adj.get("content") or "") + "」"
                        + (f"｜理由：{reason}" if str(reason or "").strip() else ""),
            })
            if accepted:
                self.add_effective_method(str(adj.get("content") or "").strip())
            plan["updated_at"] = _to_iso(_now())
            self._save()
            return {"confirmed": True, "adjustment": adj}
        raise ValueError(f"未找到调整实验: {adjustment_id}")

    # ==================== 数据分析（规则层） ====================

    def analyze(self, plan_id: Optional[str] = None, days: int = 7) -> dict:
        """规则层分析：时长趋势 / 停滞检测 / 测验正确率 / 复习稳定性 / 达成率"""
        now = _now()
        since = now - timedelta(days=days)
        if plan_id is not None and self.get_plan(plan_id) is None:
            raise ValueError(f"未找到计划: {plan_id}")
        daily: Dict[str, int] = {}
        for s in self._data["sessions"].values():
            if plan_id and s.get("plan_id") != plan_id:
                continue
            d = (_parse_dt(s.get("created_at")) or now).date()
            if d >= since.date():
                daily[str(d)] = daily.get(str(d), 0) + int(s.get("minutes") or 0)
        trend = [{"date": d, "minutes": m} for d, m in sorted(daily.items())]

        relevant_sessions = [
            s for s in self._data["sessions"].values()
            if not plan_id or s.get("plan_id") == plan_id
        ]
        recent_dates = sorted((_parse_dt(s.get("created_at")) or now) for s in relevant_sessions)
        stagnant_days = (now - recent_dates[-1]).days if recent_dates else None
        stagnant = stagnant_days is not None and stagnant_days >= 3

        tests = [
            t for t in self._data["tests"].values()
            if not plan_id or t.get("plan_id") == plan_id
        ]
        avg_accuracy = round(sum(t.get("accuracy", 0) for t in tests) / len(tests)) if tests else None

        items = [
            it for it in self._data["review_items"].values()
            if not plan_id or it.get("plan_id") == plan_id
        ]
        due_items = [
            it for it in items if not it.get("resolved") and (_parse_dt(it.get("next_review_at")) or now) <= now
        ]
        done_reviews = [it for it in items if it.get("last_result") is not None]
        review_accuracy = None
        if done_reviews:
            review_accuracy = round(
                sum(1 for it in done_reviews if it.get("last_result")) / len(done_reviews) * 100
            )

        plan = self.get_plan(plan_id) if plan_id else None
        ms = (plan or {}).get("milestones") or []
        milestone_ratio = round(sum(1 for m in ms if m.get("done")) / len(ms) * 100) if ms else None

        adjustments_evaluated = self.evaluate_adjustments(plan_id) if plan_id else []
        info = self._plan_progress(plan) if plan else {}
        return {
            "plan_id": plan_id,
            "days": days,
            "total_minutes": sum(t["minutes"] for t in trend),
            "daily_trend": trend,
            "stagnant_days": stagnant_days,
            "stagnant": stagnant,
            "avg_accuracy": avg_accuracy,
            "review_total": len(items),
            "review_due": len(due_items),
            "review_resolved": sum(1 for it in items if it.get("resolved")),
            "review_accuracy": review_accuracy,
            "milestone_ratio": milestone_ratio,
            "adjustments_evaluated": adjustments_evaluated,
            "pace": info.get("pace", "unknown"),
            "progress": info.get("progress", 0),
        }

    # ==================== 学习画像（前期收集，因材施教基础） ====================

    _PROFILE_FIELDS = (
        "learning_style", "preferred_method", "best_time", "focus_minutes",
        "review_pref", "motivation", "notes",
    )

    def get_profile(self) -> dict:
        """读取学习画像（空 dict 表示尚未收集）"""
        return dict(self._data.get("profile") or {})

    def profile_exists(self) -> bool:
        return bool(self._data.get("profile"))

    def update_profile(self, **fields) -> dict:
        """保存/更新学习画像；覆盖时追加 updated_at 与 update_count"""
        profile = self._data.setdefault("profile", {})
        changed = False
        for k in self._PROFILE_FIELDS:
            v = fields.get(k)
            if v is None:
                continue
            if k == "focus_minutes":
                v = max(int(v or 0), 0)
            else:
                v = str(v or "").strip()
            if profile.get(k) != v:
                profile[k] = v
                changed = True
        if changed:
            if "created_at" not in profile:
                profile["created_at"] = _to_iso(_now())
            profile["updated_at"] = _to_iso(_now())
            profile["update_count"] = int(profile.get("update_count") or 0) + 1
            self._save()
        return dict(profile)

    def add_effective_method(self, content: str) -> list:
        """把「实验验证有效」的学习方法沉淀进画像（去重，上限 10 条）"""
        content = str(content or "").strip()
        profile = self._data.setdefault("profile", {})
        methods = [str(m).strip() for m in (profile.get("effective_methods") or []) if str(m).strip()]
        if content and content not in methods:
            methods.append(content)
            profile["effective_methods"] = methods[-10:]
            if "created_at" not in profile:
                profile["created_at"] = _to_iso(_now())
            profile["updated_at"] = _to_iso(_now())
            profile["update_count"] = int(profile.get("update_count") or 0) + 1
            self._save()
        return profile.get("effective_methods") or []

    def profile_summary(self) -> str:
        """一句话画像摘要（供动态上下文注入）"""
        p = self.get_profile()
        if not p:
            return ""
        parts = []
        if p.get("learning_style"):
            parts.append(f"风格:{p['learning_style']}")
        if p.get("preferred_method"):
            parts.append(f"偏好:{p['preferred_method']}")
        if p.get("best_time"):
            parts.append(f"时段:{p['best_time']}")
        if p.get("focus_minutes"):
            parts.append(f"专注:{p['focus_minutes']}分钟")
        if p.get("review_pref"):
            parts.append(f"复习:{p['review_pref']}")
        if p.get("motivation"):
            parts.append(f"动机:{p['motivation']}")
        methods = p.get("effective_methods") or []
        if methods:
            parts.append("已验证有效:" + "；".join(str(m)[:40] for m in methods))
        return "；".join(parts) if parts else ""

    # ==================== 测评后决策：是否调整方案（规则层） ====================

    def evaluate_adjustment_need(self, plan_id: str, now: Optional[datetime] = None) -> dict:
        """每次测评总结时评估是否需要调整方案，返回信号与原因。

        信号（满足任一即建议调整）：
        - 正确率下滑且低于 70% 阈值
        - 连续 2 次测验低于 70%
        - 计划停滞 >= 3 天
        - 复习积压未完成
        """
        plan = self.get_plan(plan_id)
        if plan is None:
            raise ValueError(f"未找到计划: {plan_id}")
        now = now or _now()
        tests = [
            t for t in self._data["tests"].values()
            if t.get("plan_id") == plan_id
        ]
        tests.sort(key=lambda t: t.get("created_at") or "")
        if not tests:
            return {
                "needed": False, "reason": "暂无测评数据，先建立基线",
                "suggestion": "", "latest_accuracy": None, "avg_accuracy": None,
            }
        latest = tests[-1]
        acc = int(latest.get("accuracy") or 0)
        avg = round(sum(int(t.get("accuracy") or 0) for t in tests) / len(tests))
        prev = int(tests[-2].get("accuracy") or 0) if len(tests) >= 2 else None
        declining = prev is not None and acc < prev

        sessions = [
            s for s in self._data["sessions"].values()
            if s.get("plan_id") == plan_id
        ]
        stagnant_days = None
        if sessions:
            recent = max((_parse_dt(s.get("created_at")) or now) for s in sessions)
            stagnant_days = (now - recent).days

        due_items = [
            it for it in self._data["review_items"].values()
            if it.get("plan_id") == plan_id and not it.get("resolved")
            and (_parse_dt(it.get("next_review_at")) or now) <= now
        ]

        reasons: List[str] = []
        suggestion = ""
        if acc < 70 and declining:
            reasons.append(f"正确率下滑（{acc}% < 上次 {prev}%）且低于 70% 阈值")
            suggestion = "调整学习方法或内容侧重，针对薄弱知识点专项强化，并加密复习"
        elif acc < 70 and prev is not None and prev < 70:
            reasons.append(f"连续 2 次测验低于 70%（当前 {acc}%）")
            suggestion = "调整节奏或方法：先补薄弱知识点再推进新内容"
        elif acc < 70 and prev is None:
            reasons.append(f"首次测验即低于 70%（{acc}%）")
            suggestion = "检查方法是否匹配：先针对薄弱知识点调整学习方式再测"
        if stagnant_days is not None and stagnant_days >= 3:
            reasons.append(f"已停滞 {stagnant_days} 天")
            suggestion = suggestion or "调整节奏或目标，先恢复学习频率"
        if due_items:
            reasons.append(f"复习积压 {len(due_items)} 项未完成")
            suggestion = suggestion or "先完成到期复习再学新内容"
        if reasons:
            return {
                "needed": True,
                "reason": "；".join(reasons),
                "suggestion": suggestion,
                "latest_accuracy": acc,
                "avg_accuracy": avg,
            }
        return {
            "needed": False,
            "reason": f"当前方案有效（正确率 {acc}%），无需调整",
            "suggestion": "",
            "latest_accuracy": acc,
            "avg_accuracy": avg,
        }

    # ==================== 完成判定（全自动） ====================

    def completion_check(self, plan_id: str) -> dict:
        """完成判定规则：验收项全过 + 里程碑全完 + 复习无到期 + 测验达标"""
        plan = self.get_plan(plan_id)
        if plan is None:
            raise ValueError(f"未找到计划: {plan_id}")
        now = _now()
        missing = []
        acceptance = plan.get("acceptance") or []
        done_acc = [a for a in acceptance if a.get("done")]
        if acceptance and len(done_acc) < len(acceptance):
            missing.append(f"验收项未完成：{len(acceptance) - len(done_acc)} 项")
        ms = plan.get("milestones") or []
        if ms and sum(1 for m in ms if m.get("done")) < len(ms):
            missing.append("里程碑未全部完成")
        if not ms and (plan.get("progress") or 0) < 100:
            missing.append(f"进度未达 100%（当前 {plan.get('progress') or 0}%）")
        due_items = [
            it for it in self._data["review_items"].values()
            if it.get("plan_id") == plan_id and not it.get("resolved")
            and (_parse_dt(it.get("next_review_at")) or now) <= now
        ]
        if due_items:
            missing.append(f"有 {len(due_items)} 项复习到期未完成")
        tests = [t for t in self._data["tests"].values() if t.get("plan_id") == plan_id]
        if tests:
            avg = sum(t.get("accuracy", 0) for t in tests) / len(tests)
            if avg < 70:
                missing.append(f"平均测验正确率不足（{round(avg)}% < 70%）")
        return {
            "eligible": not missing,
            "missing": missing,
            "acceptance_done": len(done_acc),
            "acceptance_total": len(acceptance),
        }

    def confirm_completion(self, plan_id: str, *, accepted: bool, report_note: str = "") -> dict:
        """LLM 完成裁决：基于 completion_check 客观报告做最终决策；规则层只执行"""
        plan = self.get_plan(plan_id)
        if plan is None:
            raise ValueError(f"未找到计划: {plan_id}")
        if plan.get("status") == "completed":
            return {"confirmed": False, "already": True, "plan": plan}
        check = self.completion_check(plan_id)
        if not accepted:
            plan["decision_log"].append({
                "ts": _to_iso(_now()),
                "text": "完成裁决：暂不完成"
                        + (f"｜理由：{str(report_note or '').strip()}" if str(report_note or "").strip() else ""),
            })
            plan["updated_at"] = _to_iso(_now())
            self._save()
            return {"confirmed": False, "plan": plan, "check": check}
        plan["status"] = "completed"
        plan["completion"] = {
            "completed_at": _to_iso(_now()),
            "check": {k: v for k, v in check.items() if k != "missing"},
            "report": str(report_note or "").strip() or f"计划完成：{plan['title']}",
        }
        plan["updated_at"] = _to_iso(_now())
        plan["decision_log"].append({
            "ts": _to_iso(_now()),
            "text": "完成裁决：LLM 确认完成（基于验收数据报告）",
        })
        self._save()
        return {"confirmed": True, "plan": plan, "check": check}

    # ==================== 概览 ====================

    def overview(self) -> dict:
        now = _now()
        active_plans = [
            p for p in self.list_plans(status=None)
            if p.get("status") in ("active", "paused")
        ]
        review_due_total = 0
        for p in active_plans:
            pid = p["plan_id"]
            items = [
                it for it in self._data["review_items"].values()
                if it.get("plan_id") == pid and not it.get("resolved")
            ]
            p["review_due"] = sum(
                1 for it in items if (_parse_dt(it.get("next_review_at")) or now) <= now
            )
            review_due_total += p["review_due"]
            recent = [
                _parse_dt(s.get("created_at")) or now
                for s in self._data["sessions"].values()
                if s.get("plan_id") == pid
            ]
            p["stagnant_days"] = (now - max(recent)).days if recent else None
        return {
            "active_plans": active_plans,
            "today_minutes": self.today_minutes(now),
            "sessions_today": self.list_sessions(
                since=now.replace(hour=0, minute=0, second=0, microsecond=0)
            ),
            "due_reminders": self.due_reminders(now=now),
            "upcoming_reminders": self.upcoming_reminders(limit=10, now=now),
            "recent_materials": self.list_materials(limit=10),
            "review_due_total": review_due_total,
            "profile": self.get_profile(),
            "profile_summary": self.profile_summary(),
        }
