"""通用规划器仓库 —— 长期任务规划 / 进度追溯 / 动态调整

与学习场景的 JsonStudyRepo 相对：这是「通用任务」层，不绑定学科 / 学习场景。
任何目标型需求（项目、备考、健身、写作……）都可用 task_* 工具管理。
数据落盘 plans.json。
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_PLAN_STATUSES = {"active", "paused", "completed", "archived"}


def _now() -> datetime:
    return datetime.now()


def _parse_dt(value) -> Optional[datetime]:
    if value is None or value == "":
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def _to_iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat(timespec="seconds") if value else None


class JsonPlanRepo:
    """通用长期任务规划仓库（JSON 持久化）"""

    def __init__(self, persist_dir: Optional[str] = None):
        self._persist_dir = Path(persist_dir) if persist_dir else None
        self._data: Dict = {"plans": {}}
        self._load()

    # ---------- 持久化 ----------

    def _persist_file(self) -> Optional[Path]:
        if self._persist_dir is None:
            return None
        return self._persist_dir / "plans.json"

    def _load(self) -> None:
        pf = self._persist_file()
        if pf is None or not pf.exists():
            return
        try:
            with open(pf, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"任务规划恢复失败，从空库开始: {e}")
            self._data = {"plans": {}}

    def _save(self) -> None:
        pf = self._persist_file()
        if pf is None:
            return
        try:
            pf.parent.mkdir(parents=True, exist_ok=True)
            with open(pf, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception as e:  # noqa: BLE001
            logger.error(f"任务规划持久化失败: {e}")

    # ---------- 计划：创建 / 查询 ----------

    def create_plan(
        self,
        *,
        title: str,
        goal: str = "",
        why: str = "",
        tags: Optional[List[str]] = None,
        end_date=None,
        daily_minutes: int = 0,
        priority: str = "normal",
        milestones: Optional[List[dict]] = None,
        plan_text: str = "",
        acceptance_criteria: Optional[List[str]] = None,
    ) -> dict:
        """创建长期任务（同标题活跃任务去重，返回现有计划）"""
        title = str(title or "").strip()
        if not title:
            raise ValueError("缺少参数 title（任务标题）")
        for p in self.list_plans(status=None):
            if p.get("status") not in ("completed", "archived") and p.get("title") == title:
                return {"plan": p, "reused": True}
        plan_id = f"task_{uuid.uuid4().hex[:8]}"
        plan = {
            "plan_id": plan_id,
            "title": title,
            "goal": str(goal or "").strip(),
            "why": str(why or "").strip(),
            "tags": [str(t).strip() for t in (tags or []) if str(t).strip()][:10],
            "start_date": _to_iso(_now()),
            "end_date": _to_iso(_parse_dt(end_date)),
            "daily_minutes": max(int(daily_minutes or 0), 0),
            "priority": priority if priority in ("low", "normal", "high", "urgent") else "normal",
            "status": "active",
            "progress": 0,
            "current_status": "（刚开始）",
            "next_steps": [],
            "plan_text": str(plan_text or "").strip(),
            "acceptance_criteria": [
                str(a).strip() for a in (acceptance_criteria or [])
                if str(a).strip()
            ],
            "milestones": [
                {
                    "milestone_id": f"ms_{uuid.uuid4().hex[:8]}",
                    "title": str(m.get("title") or "").strip() or f"阶段{i + 1}",
                    "due_date": _to_iso(_parse_dt(m.get("due_date"))),
                    "steps": [
                        str(s).strip() for s in (m.get("steps") or [])
                        if str(s).strip()
                    ],
                    "output": str(m.get("output") or "").strip(),
                    "acceptance": str(m.get("acceptance") or "").strip(),
                    "done": False,
                    "done_at": None,
                }
                for i, m in enumerate(milestones or [])
                if str(m.get("title") or "").strip()
            ],
            "decision_log": [{"ts": _to_iso(_now()), "text": "创建任务"}],
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
        return {**dict(plan), "progress_info": self._plan_progress(plan)}

    def _plan_progress(self, plan: dict) -> dict:
        """进度推算：里程碑优先，其次手动 progress"""
        milestones = plan.get("milestones") or []
        total_ms = len(milestones)
        done_ms = sum(1 for m in milestones if m.get("done"))
        progress = max(int(plan.get("progress") or 0), 0)
        source = "manual"
        if total_ms:
            ms_progress = round(done_ms / total_ms * 100)
            if not progress:
                progress, source = ms_progress, "milestone"
        end = _parse_dt(plan.get("end_date"))
        days_left = None
        if end is not None:
            days_left = max((end.date() - _now().date()).days, 0)
        return {
            "progress": min(progress, 100),
            "progress_source": source,
            "milestones_total": total_ms,
            "milestones_done": done_ms,
            "days_left": days_left,
        }

    # ---------- 计划：动态调整 ----------

    def update_plan(
        self, plan_id: str, *, decision: Optional[str] = None, **fields
    ) -> Optional[dict]:
        """更新任务字段（目标/截止/时长/进度/状态/当前状态/下一步），可追加决策记录"""
        plan = self.get_plan(plan_id)
        if plan is None:
            return None
        allowed = {
            "title", "goal", "why", "end_date", "daily_minutes", "priority",
            "status", "progress", "current_status", "next_steps", "tags",
            "plan_text", "acceptance_criteria",
            # 内部关联字段（由 planner 工具层写入，不暴露给 LLM schema）
            "state_id", "rel_id",
        }
        for k, v in fields.items():
            if k in allowed and v is not None:
                if k == "progress":
                    plan[k] = max(0, min(100, int(v or 0)))
                elif k == "daily_minutes":
                    plan[k] = max(int(v or 0), 0)
                elif k == "next_steps":
                    plan[k] = [str(x).strip() for x in (v or []) if str(x).strip()][:5]
                elif k == "tags":
                    plan[k] = [str(x).strip() for x in (v or []) if str(x).strip()][:10]
                elif k == "end_date":
                    plan[k] = _to_iso(_parse_dt(v))
                elif k in ("status", "priority"):
                    allowed_set = (
                        _PLAN_STATUSES if k == "status"
                        else {"low", "normal", "high", "urgent"}
                    )
                    plan[k] = v if v in allowed_set else plan.get(k)
                elif k == "acceptance_criteria":
                    plan[k] = [
                        str(x).strip() for x in (v or [])
                        if str(x).strip()
                    ][:10]
                elif k == "plan_text":
                    plan[k] = str(v or "").strip()
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
