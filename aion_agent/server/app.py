"""Aion Agent 本地服务 —— FastAPI + SSE 流式对话 + 认知记忆管理

启动方式：
    python -m aion_agent serve --host 0.0.0.0 --port 8000

API：
    GET  /api/health            服务与 LLM 配置状态
    POST /api/session           创建/复用会话
    POST /api/chat              SSE 流式对话（ReAct 循环事件）
    GET  /api/history           会话历史
    GET  /api/memory            认知记忆列表（三元组/状态/笔记）
    DELETE /api/memory/{rel_id} 删除三元组（软删除）
    GET  /                      静态 Web UI（PWA）
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import AsyncGenerator, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

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
from aion_agent.use_cases.react_chat_session import ReActChatSession

logger = logging.getLogger(__name__)

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
        self._pipeline = CognitionPipeline(
            cognitive_repo=self._repo, embedder=self._embedder
        )
        self._sessions: Dict[str, ReActChatSession] = {}
        self._llm = None
        self._llm_error: Optional[str] = None

    # ---------- LLM ----------

    def get_llm(self) -> OpenAICompatibleClient:
        if self._llm is None:
            self._load_dotenv_files()
            cfg = get_config()
            if not cfg.get("api_key"):
                self._llm_error = (
                    "未配置 LLM：请在应用目录 .env 中设置 AION_LLM_API_KEY"
                    "（或 LLM_API_KEY）。"
                )
                raise HTTPException(status_code=400, detail=self._llm_error)
            self._llm = OpenAICompatibleClient(
                api_key=cfg["api_key"],
                base_url=cfg["base_url"],
                model=cfg["model"],
            )
        return self._llm

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


def create_app(runtime: Optional[AppRuntime] = None) -> FastAPI:
    rt = runtime or AppRuntime()
    app = FastAPI(title="Aion Agent", version="0.1.0")

    # ---------- 健康 / 状态 ----------

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "llm": rt.llm_status()}

    # ---------- 会话 ----------

    @app.post("/api/session")
    async def create_session(body: dict):
        user_id = str(body.get("user_id") or "chat_user")
        session = rt.create_session(user_id)
        return {"session_id": session.session_id, "user_id": user_id}

    # ---------- 对话（SSE） ----------

    @app.post("/api/chat")
    async def chat(body: dict):
        message = str(body.get("message") or "").strip()
        if not message:
            raise HTTPException(status_code=400, detail="消息不能为空")
        user_id = str(body.get("user_id") or "chat_user")
        session_id = body.get("session_id") or None
        session = rt.create_session(user_id, session_id)

        async def event_stream() -> AsyncGenerator[str, None]:
            try:
                async for event in session.react_stream(message):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except Exception as e:  # noqa: BLE001
                logger.exception("chat 流式处理失败")
                payload = {"type": "error", "error": str(e)}
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ---------- 会话管理 ----------

    @app.get("/api/sessions")
    async def list_sessions(user_id: str = "chat_user"):
        return {"sessions": await rt.repo_chat.list_sessions(user_id)}

    @app.delete("/api/session/{session_id}")
    async def delete_session(session_id: str):
        ok = await rt.repo_chat.delete_session(session_id)
        if not ok:
            raise HTTPException(status_code=404, detail=f"未找到会话 {session_id}")
        rt.drop_session(session_id)
        return {"deleted": session_id}

    # ---------- 历史 ----------

    @app.get("/api/history")
    async def history(session_id: str):
        session = rt.get_session(session_id)
        if session is None:
            return {"messages": []}
        msgs = await session.get_history()
        return {
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    "created_at": _iso(m.created_at),
                }
                for m in msgs
            ]
        }

    # ---------- 记忆 ----------

    @app.get("/api/memory")
    async def memory(user_id: str = "chat_user"):
        triples = []
        for dim in (Dimension.USER, Dimension.SELF, Dimension.WORLD, Dimension.ENV):
            for t in await rt.repo.list_triples_by_dimension(user_id, dim):
                triples.append(_triple_to_dict(t))
        states = await rt.repo.get_active_states(user_id)
        notes = await rt.repo.get_notes_for_injection(user_id, top_k=100)
        return {
            "triples": triples,
            "states": [_state_to_dict(s) for s in states],
            "notes": [_note_to_dict(n) for n in notes],
        }

    @app.delete("/api/memory/{rel_id}")
    async def delete_memory(rel_id: str):
        ok = await rt.repo.delete_triple(rel_id, soft=True)
        if not ok:
            raise HTTPException(status_code=404, detail=f"未找到 {rel_id}")
        return {"deleted": rel_id}

    # ---------- 静态 UI（PWA） ----------

    @app.get("/")
    async def index():
        return FileResponse(_ui_dir() / "index.html")

    @app.get("/sw.js")
    async def service_worker():
        return FileResponse(_ui_dir() / "sw.js", media_type="application/javascript")

    app.mount("/static", StaticFiles(directory=_ui_dir()), name="static")

    return app


_default_app = None


def _get_app() -> FastAPI:
    """模块级默认应用实例（CLI / 桌面壳共用）"""
    global _default_app
    if _default_app is None:
        _default_app = create_app()
    return _default_app


def run_server(host: str = "0.0.0.0", port: int = 8000, open_browser: bool = True) -> None:
    """启动 uvicorn（CLI 入口，前台阻塞）"""
    import uvicorn

    url = f"http://127.0.0.1:{port}"
    print("=" * 60)
    print("Aion Agent 本地服务已启动")
    print(f"  本机访问:  {url}")
    print("  手机访问:  同一 Wi-Fi 下访问 http://<本机IP>:{0}".format(port))
    print("  数据目录:  {0}".format(_default_data_dir()))
    print("  按 Ctrl+C 停止服务")
    print("=" * 60)
    if open_browser:
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    uvicorn.run(_get_app(), host=host, port=port, log_level="warning")


def run_server_in_background(host: str = "127.0.0.1", port: int = 8000):
    """后台线程启动 uvicorn（桌面壳用），返回 server 对象"""
    import threading

    import uvicorn

    config = uvicorn.Config(_get_app(), host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None
    thread = threading.Thread(
        target=server.run, daemon=True, name="aion-server"
    )
    thread.start()
    return server
