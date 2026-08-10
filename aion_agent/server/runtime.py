"""Aion Agent 服务运行时 —— 纯标准库依赖（FastAPI 服务与手机内嵌服务器共用）

从 server/app.py 解耦而来：AppRuntime / 数据目录 / 序列化 helper。
不依赖 fastapi/uvicorn，便于 Chaquopy 打包进 Android APK。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from aion_agent.core.entities.agent_state import AgentState
from aion_agent.core.entities.cognitive_triple import CognitiveTriple, Dimension
from aion_agent.core.entities.note import Note
from aion_agent.ecommerce.agnes_client import (
    DEFAULT_BASE_URL as AGNES_DEFAULT_BASE_URL,
    DEFAULT_IMAGE_MODEL as AGNES_DEFAULT_IMAGE_MODEL,
    DEFAULT_VIDEO_MODEL as AGNES_DEFAULT_VIDEO_MODEL,
)
from aion_agent.llm.embedding import build_embedder
from aion_agent.llm.openai_compatible import (
    OpenAICompatibleClient,
    get_config,
    load_env_from_dotenv,
)
from aion_agent.pipeline.cognition_pipeline import CognitionPipeline
from aion_agent.planner.planner_repo import JsonPlanRepo
from aion_agent.skills import build_default_skills
from aion_agent.storage.execution_log import JsonExecutionLog
from aion_agent.storage.in_memory_cognitive_repo import InMemoryCognitiveRepo
from aion_agent.storage.json_chat_repo import JsonChatRepo
from aion_agent.study.study_repo import JsonStudyRepo
from aion_agent.sync import SYNC_FILES, build_bundle, get_device_id, merge_bundle, pull_bundle
from aion_agent.tools.executor import ToolPolicy
from aion_agent.use_cases.react_chat_session import ReActChatSession

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    """运行时配置错误（如未配置 LLM API Key）"""


def _ui_dir() -> Path:
    """UI 静态目录：源码与 PyInstaller 冻结环境通用"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "aion_agent" / "server" / "ui"
    return Path(__file__).resolve().parent / "ui"


def _default_data_dir() -> Path:
    override = os.environ.get("AION_DATA_DIR")
    if override:
        return Path(override).expanduser() / "server"
    return Path.home() / ".aion_agent" / "server"


def _iso(dt) -> Optional[str]:
    return dt.isoformat() if dt else None


def _triple_to_dict(t: CognitiveTriple) -> dict:
    return {
        "rel_id": t.rel_id,
        "subject": t.subject,
        "predicate": t.predicate,
        "object": t.object,
        "dimension": t.dimension.value,
        "confidence": t.confidence,
        "usage_count": t.usage_count,
        "is_confirmed": t.is_confirmed_by_user,
        "created_at": _iso(t.created_at),
        "expires_at": _iso(t.expires_at),
    }


def _state_to_dict(s: AgentState) -> dict:
    return {
        "state_id": s.state_id,
        "state_type": s.state_type,
        "state_name": s.state_name,
        "description": s.description,
        "priority": s.priority,
        "expires_at": _iso(s.expires_at),
    }


def _note_to_dict(n: Note) -> dict:
    return {
        "note_id": n.note_id,
        "note_type": n.note_type.value,
        "title": n.title,
        "content": n.content,
        "summary": n.summary,
        "created_at": _iso(n.created_at),
        "archived": n.is_archived(),
    }


class AppRuntime:
    """服务运行时：LLM / 认知仓库 / 会话注册表，跨请求复用"""

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = Path(data_dir) if data_dir else _default_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._embedder = build_embedder()
        self._repo = InMemoryCognitiveRepo(
            embedder=self._embedder, persist_dir=self.data_dir
        )
        self._chat_repo = JsonChatRepo(persist_dir=self.data_dir)
        self._study_repo = JsonStudyRepo(persist_dir=self.data_dir)
        self._planner_repo = JsonPlanRepo(persist_dir=self.data_dir)
        self._execution_log = JsonExecutionLog(persist_dir=self.data_dir)
        self._tool_policy = ToolPolicy.from_dict(
            self._load_json_file(self.data_dir / "tool_policy.json", None)
        )
        self._skills_config: Dict[str, bool] = self._load_json_file(
            self.data_dir / "skills.json", {}
        ) or {}
        self._pipeline = CognitionPipeline(
            cognitive_repo=self._repo, embedder=self._embedder
        )
        self._sessions: Dict[str, ReActChatSession] = {}
        self._llm = None
        self._llm_error: Optional[str] = None
        # 提醒通知：定时器把到期提醒放入队列，Web UI 轮询拉取展示
        self._pending_notifications: List[dict] = []
        self._notification_lock = threading.Lock()
        self._reminder_watcher = threading.Thread(
            target=self._watch_reminders, daemon=True, name="aion-reminder-watch"
        )
        self._reminder_watcher.start()

    # ---------- LLM ----------

    def get_llm(self) -> OpenAICompatibleClient:
        if self._llm is None:
            self._load_dotenv_files()
            cfg = get_config()
            if not cfg.get("api_key"):
                self._llm_error = (
                    "未配置 LLM：请在应用设置中填写 API Key"
                    "（或 .env 设置 AION_LLM_API_KEY）。"
                )
                raise ConfigError(self._llm_error)
            self._llm = OpenAICompatibleClient(
                api_key=cfg["api_key"],
                base_url=cfg["base_url"],
                model=cfg["model"],
            )
        return self._llm

    def save_llm_config(
        self, *, api_key: str, base_url: str = "", model: str = "",
    ) -> dict:
        """把 LLM 配置持久化到 ~/.aion_agent/.env 并立即生效（重启不丢失）"""
        env_path = Path.home() / ".aion_agent" / ".env"
        env_path.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        if env_path.exists():
            try:
                lines = env_path.read_text(encoding="utf-8").splitlines()
            except Exception:  # noqa: BLE001
                lines = []

        def upsert(key: str, value: str) -> None:
            nonlocal lines
            kept = []
            found = False
            for line in lines:
                stripped = line.strip()
                if stripped.startswith(key + "="):
                    if value:
                        kept.append(f"{key}={value}")
                        found = True
                    continue
                kept.append(line)
            if value and not found:
                kept.append(f"{key}={value}")
            lines = kept

        upsert("AION_LLM_API_KEY", str(api_key or "").strip())
        upsert("AION_LLM_BASE_URL", str(base_url or "").strip())
        upsert("AION_LLM_MODEL", str(model or "").strip())
        try:
            env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception:  # noqa: BLE001
            logger.exception("写入 LLM 配置失败")
            raise RuntimeError("写入 LLM 配置失败（~/.aion_agent/.env 不可写）")
        # 立即生效：直接覆盖 os.environ（load_env_from_dotenv 只在未设置时写入）
        if str(api_key or "").strip():
            os.environ["AION_LLM_API_KEY"] = str(api_key).strip()
        if str(base_url or "").strip():
            os.environ["AION_LLM_BASE_URL"] = str(base_url).strip()
        if str(model or "").strip():
            os.environ["AION_LLM_MODEL"] = str(model).strip()
        self.reset_llm()
        return self.llm_status()

    def reset_llm(self) -> None:
        """清空已缓存的 LLM 实例（修改 API Key 后重新加载）"""
        self._llm = None
        self._llm_error = None

    def llm_status(self) -> dict:
        cfg = get_config()
        return {
            "configured": bool(cfg.get("api_key")),
            "model": cfg.get("model"),
            "base_url": cfg.get("base_url"),
            "error": self._llm_error,
        }

    # ---------- Agnes 生图/视频 ----------

    def agnes_status(self) -> dict:
        self._load_dotenv_files()
        env_path = self.data_dir / ".env"
        if env_path.exists():
            load_env_from_dotenv(env_path)
        return {
            "configured": bool(os.getenv("AGNES_API_KEY", "").strip()),
            "base_url": (
                os.getenv("AGNES_BASE_URL", "").strip()
                or AGNES_DEFAULT_BASE_URL
            ),
            "image_model": (
                os.getenv("AGNES_IMAGE_MODEL", "").strip()
                or AGNES_DEFAULT_IMAGE_MODEL
            ),
            "video_model": (
                os.getenv("AGNES_VIDEO_MODEL", "").strip()
                or AGNES_DEFAULT_VIDEO_MODEL
            ),
        }

    def save_agnes_config(
        self, *, api_key: str, base_url: str = "",
        image_model: str = "", video_model: str = "",
    ) -> dict:
        """把 Agnes 配置持久化到 ~/.aion_agent/.env 并立即生效（重启不丢失）"""
        self._load_dotenv_files()
        env_path = Path.home() / ".aion_agent" / ".env"
        env_path.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        if env_path.exists():
            try:
                lines = env_path.read_text(encoding="utf-8").splitlines()
            except Exception:  # noqa: BLE001
                lines = []

        def upsert(key: str, value: str) -> None:
            nonlocal lines
            kept = []
            found = False
            for line in lines:
                stripped = line.strip()
                if stripped.startswith(key + "="):
                    if value:
                        kept.append(f"{key}={value}")
                        found = True
                    continue
                kept.append(line)
            if value and not found:
                kept.append(f"{key}={value}")
            lines = kept

        entries = (
            ("AGNES_API_KEY", str(api_key or "").strip()),
            ("AGNES_BASE_URL", str(base_url or "").strip()),
            ("AGNES_IMAGE_MODEL", str(image_model or "").strip()),
            ("AGNES_VIDEO_MODEL", str(video_model or "").strip()),
        )
        for key, value in entries:
            upsert(key, value)
        try:
            env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception:  # noqa: BLE001
            logger.exception("写入 Agnes 配置失败")
            raise RuntimeError("写入 Agnes 配置失败（~/.aion_agent/.env 不可写）")
        # 立即生效：直接覆盖 os.environ（空值 = 清除）
        for key, value in entries:
            if value:
                os.environ[key] = value
            else:
                os.environ.pop(key, None)
        return self.agnes_status()

    # ---------- 配置发现 ----------

    @staticmethod
    def _load_dotenv_files() -> None:
        """从多个位置自动加载 .env（当前目录 / exe 同目录 / 用户主目录）"""
        candidates = []
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).resolve().parent
            candidates.append(exe_dir / ".env")
        candidates.append(Path.cwd() / ".env")
        candidates.append(Path.home() / ".aion_agent" / ".env")
        seen = set()
        for p in candidates:
            try:
                rp = p.resolve()
            except Exception:  # noqa: BLE001
                continue
            if rp in seen:
                continue
            seen.add(rp)
            load_env_from_dotenv(rp)

    # ---------- 会话 ----------

    def get_session(self, session_id: str) -> Optional[ReActChatSession]:
        session = self._sessions.get(session_id)
        if session is not None:
            return session
        # 重启后内存注册表为空：从持久化仓库恢复历史会话
        if self._chat_repo.has_session(session_id):
            try:
                return self.create_session(
                    self._chat_repo.session_user_id(session_id), session_id
                )
            except ConfigError:
                return None
        return None

    def create_session(
        self, user_id: str, session_id: Optional[str] = None
    ) -> ReActChatSession:
        if session_id and session_id in self._sessions:
            return self._sessions[session_id]
        session = ReActChatSession(
            self.get_llm(),
            cognitive_repo=self._repo,
            chat_repo=self._chat_repo,
            pipeline=self._pipeline,
            study_repo=self._study_repo,
            planner_repo=self._planner_repo,
            execution_log=self._execution_log,
            tool_policy=self._tool_policy,
            disabled_skills={
                name for name, enabled in self._skills_config.items() if not enabled
            },
            user_id=user_id,
            session_id=session_id,
            tool_timeout_seconds=int(
                os.environ.get("AION_TOOL_TIMEOUT_SECONDS", "120")
            ),
        )
        self._chat_repo.ensure_session(session.session_id, user_id)
        self._sessions[session.session_id] = session
        return session

    def drop_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    @property
    def repo(self) -> InMemoryCognitiveRepo:
        return self._repo

    @property
    def repo_chat(self) -> JsonChatRepo:
        return self._chat_repo

    @property
    def repo_study(self) -> JsonStudyRepo:
        return self._study_repo

    @property
    def repo_planner(self) -> JsonPlanRepo:
        return self._planner_repo

    @property
    def execution_log(self) -> JsonExecutionLog:
        return self._execution_log

    @property
    def tool_policy(self) -> ToolPolicy:
        return self._tool_policy

    # ---------- 工具权限策略 ----------

    def set_tool_policy(self, *, blocked=None, confirm=None) -> None:
        self._tool_policy.set_blocked(blocked)
        self._tool_policy.set_confirm(confirm)
        self._save_json_file(
            self.data_dir / "tool_policy.json", self._tool_policy.to_dict()
        )

    # ---------- 跨设备同步 ----------

    @property
    def device_id(self) -> str:
        return get_device_id(self.data_dir)

    def sync_export(self) -> dict:
        return build_bundle(self.data_dir)

    def sync_import(self, bundle: dict) -> dict:
        return merge_bundle(self.data_dir, bundle)

    def sync_pull(self, url: str) -> dict:
        bundle = pull_bundle(url)
        return merge_bundle(self.data_dir, bundle)

    def sync_status(self) -> dict:
        files = {}
        for name in SYNC_FILES:
            p = self.data_dir / name
            files[name] = p.stat().st_size if p.exists() else 0
        return {"device_id": self.device_id, "files": files}

    # ---------- 技能启停 ----------

    def is_skill_enabled(self, name: str) -> bool:
        return bool(self._skills_config.get(name, True))

    def set_skill_enabled(self, name: str, enabled: bool) -> bool:
        known = {
            s.name
            for s in build_default_skills(
                cognitive_repo=self._repo,
                study_repo=self._study_repo,
                planner_repo=self._planner_repo,
                user_id="chat_user",
            )
        }
        if name not in known:
            return False
        self._skills_config[name] = bool(enabled)
        self._save_json_file(self.data_dir / "skills.json", self._skills_config)
        return True

    # ---------- JSON 持久化小工具 ----------

    @staticmethod
    def _load_json_file(path: Path, default):
        try:
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"读取 {path.name} 失败: {e}")
        return default

    def _save_json_file(self, path: Path, data) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:  # noqa: BLE001
            logger.error(f"写入 {path.name} 失败: {e}")

    # ---------- 提醒通知 ----------

    def _watch_reminders(
        self, interval: float = 15.0, adjust_interval: float = 60.0,
    ) -> None:
        """后台定时检查：到期提醒触发 + 调整实验到期自动结算（均幂等，重启不重复）"""
        next_adjust_check = 0.0
        while True:
            try:
                fired = self._study_repo.fire_due_reminders()
                if fired:
                    with self._notification_lock:
                        self._pending_notifications.extend(fired)
                        # 队列上限，防止长时间未打开 UI 时堆积
                        self._pending_notifications = self._pending_notifications[-50:]
                now_t = time.monotonic()
                if now_t >= next_adjust_check:
                    next_adjust_check = now_t + adjust_interval
                    self._settle_adjustments()
            except Exception:  # noqa: BLE001
                logger.exception("后台定时检查失败")
            time.sleep(interval)

    def _settle_adjustments(self) -> None:
        """调整实验到期结算：只生成对比报告并通知（待 LLM 裁决），不自动沉淀"""
        settled = self._study_repo.settle_due_adjustments()
        if not settled:
            return
        with self._notification_lock:
            for adj in settled:
                self._pending_notifications.append(
                    self._adjustment_notification(adj)
                )
            self._pending_notifications = self._pending_notifications[-50:]

    @staticmethod
    def _adjustment_notification(adj: dict) -> dict:
        """结算通知：带唯一 reminder_id 供 UI 去重展示

        规则层只出对比报告，不裁决；裁决由 LLM 在下一次对话中通过
        confirm_adjustment 完成（采纳 → 沉淀为有效方法）。
        """
        plan_title = str(adj.get("plan_title") or "")
        content = str(adj.get("content") or "")[:80]
        if adj.get("conclusion") == "pending_review":
            report = adj.get("report") or {}
            metrics = report.get("metrics") or []
            summary = "；".join(
                f"{m.get('label')} {m.get('before')}→{m.get('after')}"
                for m in metrics if m.get("improved") is not None
            ) or "数据不足"
            verdict = f"对比报告已生成（{summary}），待你在对话中裁决是否采纳"
            title = "调整实验待裁决"
        elif adj.get("conclusion") == "effective":
            verdict = "已裁决为有效，方法已沉淀为你的有效学习方法"
            title = "调整实验已裁决"
        else:
            verdict = "已裁决为无效，结论已记录"
            title = "调整实验已裁决"
        return {
            "reminder_id": "adj_settled_" + str(adj.get("adjustment_id") or ""),
            "title": title,
            "content": f"计划《{plan_title}》{verdict}｜调整：{content}",
            "type": "adjustment_settled",
            "plan_id": adj.get("plan_id"),
            "adjustment_id": adj.get("adjustment_id"),
            "conclusion": adj.get("conclusion"),
            "settled_at": adj.get("evaluated_at"),
        }

    def pending_notifications(self) -> List[dict]:
        with self._notification_lock:
            return list(self._pending_notifications)

    def ack_notifications(self) -> None:
        with self._notification_lock:
            self._pending_notifications.clear()
