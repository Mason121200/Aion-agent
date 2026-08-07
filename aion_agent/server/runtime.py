"""Aion Agent 服务运行时 —— 纯标准库依赖（FastAPI 服务与手机内嵌服务器共用）

从 server/app.py 解耦而来：AppRuntime / 数据目录 / 序列化 helper。
不依赖 fastapi/uvicorn，便于 Chaquopy 打包进 Android APK。
"""

from __future__ import annotations

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
from aion_agent.llm.embedding import build_embedder
from aion_agent.llm.openai_compatible import (
    OpenAICompatibleClient,
    get_config,
    load_env_from_dotenv,
)
from aion_agent.pipeline.cognition_pipeline import CognitionPipeline
from aion_agent.storage.in_memory_cognitive_repo import InMemoryCognitiveRepo
from aion_agent.storage.json_chat_repo import JsonChatRepo
from aion_agent.study.study_repo import JsonStudyRepo
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
        return self._sessions.get(session_id)

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
            user_id=user_id,
            session_id=session_id,
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

    # ---------- 提醒通知 ----------

    def _watch_reminders(self, interval: float = 15.0) -> None:
        """后台定时检查到期提醒，放入待通知队列（幂等，重启不重复）"""
        while True:
            try:
                fired = self._study_repo.fire_due_reminders()
                if fired:
                    with self._notification_lock:
                        self._pending_notifications.extend(fired)
                        # 队列上限，防止长时间未打开 UI 时堆积
                        self._pending_notifications = self._pending_notifications[-50:]
            except Exception:  # noqa: BLE001
                logger.exception("提醒检查失败")
            time.sleep(interval)

    def pending_notifications(self) -> List[dict]:
        with self._notification_lock:
            return list(self._pending_notifications)

    def ack_notifications(self) -> None:
        with self._notification_lock:
            self._pending_notifications.clear()
