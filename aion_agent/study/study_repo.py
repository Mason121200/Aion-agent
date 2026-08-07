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


class JsonStudyRepo:
    """学习场景仓库：计划 / 资料 / 学习记录 / 提醒"""

    def __init__(self, persist_dir: Optional[str] = None):
        self._persist_dir = Path(persist_dir) if persist_dir else None
        self._data: Dict[str, Dict[str, Any]] = {
            "plans": {}, "materials": {}, "sessions": {}, "reminders": {},
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
            "title", "subject", "goal", "why", "cadence",
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

    # ==================== 概览 ====================

    def overview(self) -> dict:
        now = _now()
        active_plans = [
            p for p in self.list_plans(status=None)
            if p.get("status") in ("active", "paused")
        ]
        return {
            "active_plans": active_plans,
            "today_minutes": self.today_minutes(now),
            "sessions_today": self.list_sessions(
                since=now.replace(hour=0, minute=0, second=0, microsecond=0)
            ),
            "due_reminders": self.due_reminders(now=now),
            "upcoming_reminders": self.upcoming_reminders(limit=10, now=now),
            "recent_materials": self.list_materials(limit=10),
        }
