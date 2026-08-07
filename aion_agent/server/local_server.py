"""Aion Agent 手机本地服务器 —— 纯标准库（http.server），供 Android APK 内嵌

实现与 FastAPI 版一致的 API 协议，Web UI（PWA）无需任何改动：
    GET    /api/health                 服务与 LLM 配置状态
    POST   /api/session                创建/复用会话
    POST   /api/chat                   SSE 流式对话（ReAct 循环事件）
    GET    /api/sessions               会话列表
    DELETE /api/session/{session_id}   删除会话
    GET    /api/history                会话历史
    GET    /api/memory                 认知记忆列表
    DELETE /api/memory/{rel_id}        删除三元组（软删除）
    GET    /api/study/overview         学习概览（计划/进度/提醒/资料）
    POST   /api/study/complete_reminder 完成提醒
    POST   /api/study/log_session      记录学习时长
    GET    /api/study/notifications    待展示的到期提醒
    POST   /api/study/notifications/ack 确认已展示提醒
    GET    / /static/* /sw.js          Web UI（PWA）

零第三方依赖（不依赖 fastapi / uvicorn / pydantic），
便于 Chaquopy 打包进 Android APK；数据默认存 App 私有目录。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from aion_agent.llm.openai_compatible import load_env_from_dotenv
from aion_agent.server.runtime import (
    AppRuntime,
    ConfigError,
    _iso,
    _note_to_dict,
    _state_to_dict,
    _triple_to_dict,
    _ui_dir,
)

logger = logging.getLogger(__name__)

_MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".webmanifest": "application/manifest+json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
}

_runtime: Optional[AppRuntime] = None
_server: Optional[ThreadingHTTPServer] = None


def get_runtime() -> Optional[AppRuntime]:
    return _runtime


class LocalHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # ---------- 通用 ----------

    def _send_json(self, code: int, obj) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    def _send_error_json(self, code: int, detail: str) -> None:
        self._send_json(code, {"detail": detail})

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        try:
            raw = self.rfile.read(length)
            data = json.loads(raw.decode("utf-8") or "{}")
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    def _parse_path(self):
        """解析 path 与 query，返回 (path, query_dict)"""
        parsed = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        return parsed.path, {k: v[0] for k, v in query.items()}

    # ---------- 静态 UI ----------

    def _serve_static(self, path: str) -> bool:
        """返回 True 表示已处理（含 404）"""
        ui = _ui_dir()
        rel = path.lstrip("/")
        if rel == "":
            rel = "index.html"
        elif rel.startswith("static/"):
            rel = rel[len("static/"):]
        target = (ui / rel).resolve()
        # 防目录穿越：必须仍在 ui 目录内
        if not str(target).startswith(str(ui.resolve())):
            self._send_error_json(404, "not found")
            return True
        if not target.is_file():
            self._send_error_json(404, "not found")
            return True
        mime = _MIME.get(target.suffix.lower(), "application/octet-stream")
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)
        self.wfile.flush()
        return True

    # ---------- GET ----------

    def do_GET(self):  # noqa: N802
        path, q = self._parse_path()
        rt = _runtime
        if rt is None:
            self._send_error_json(503, "runtime not initialized")
            return
        try:
            if path == "/api/health":
                self._send_json(200, {"status": "ok", "llm": rt.llm_status()})
            elif path == "/api/sessions":
                user_id = q.get("user_id", "chat_user")
                sessions = self._sync(rt.repo_chat.list_sessions(user_id))
                self._send_json(200, {"sessions": sessions})
            elif path == "/api/history":
                session_id = q.get("session_id", "")
                session = rt.get_session(session_id)
                msgs = self._sync(session.get_history()) if session else []
                self._send_json(200, {
                    "messages": [
                        {
                            "role": m.role,
                            "content": m.content,
                            "created_at": _iso(m.created_at),
                        }
                        for m in msgs
                    ]
                })
            elif path == "/api/memory":
                user_id = q.get("user_id", "chat_user")
                self._send_json(200, self._memory_payload(rt, user_id))
            elif path == "/api/study/overview":
                self._send_json(200, rt.repo_study.overview())
            elif path == "/api/study/notifications":
                self._send_json(200, {"notifications": rt.pending_notifications()})
            elif path.startswith("/api/"):
                self._send_error_json(404, "unknown api")
            else:
                self._serve_static(path)
        except ConfigError as e:
            self._send_error_json(400, str(e))
        except Exception as e:  # noqa: BLE001
            logger.exception("GET 处理失败: %s", path)
            self._send_error_json(500, str(e))

    # ---------- POST ----------

    def do_POST(self):  # noqa: N802
        path, _ = self._parse_path()
        rt = _runtime
        if rt is None:
            self._send_error_json(503, "runtime not initialized")
            return
        body = self._read_json_body()
        try:
            if path == "/api/session":
                user_id = str(body.get("user_id") or "chat_user")
                session = rt.create_session(user_id)
                self._send_json(200, {
                    "session_id": session.session_id,
                    "user_id": user_id,
                })
            elif path == "/api/chat":
                self._stream_chat(body)
            elif path == "/api/study/complete_reminder":
                rid = str(body.get("reminder_id") or "")
                if not rid:
                    raise ConfigError("缺少参数 reminder_id")
                ok = rt.repo_study.complete_reminder(rid)
                if not ok:
                    self._send_error_json(404, f"未找到提醒 {rid}")
                    return
                self._send_json(200, {"ok": True})
            elif path == "/api/study/notifications/ack":
                rt.ack_notifications()
                self._send_json(200, {"ok": True})
            elif path == "/api/study/log_session":
                subject = str(body.get("subject") or "").strip()
                minutes = int(body.get("minutes") or 0)
                if not subject or minutes <= 0:
                    raise ConfigError("缺少参数 subject/minutes")
                session = rt.repo_study.log_session(
                    subject=subject,
                    minutes=minutes,
                    note=str(body.get("note") or "").strip(),
                    plan_id=str(body.get("plan_id") or "") or None,
                )
                self._send_json(200, {
                    "session": session,
                    "today_minutes": rt.repo_study.today_minutes(),
                })
            else:
                self._send_error_json(404, "unknown api")
        except ConfigError as e:
            self._send_error_json(400, str(e))
        except Exception as e:  # noqa: BLE001
            logger.exception("POST 处理失败: %s", path)
            self._send_error_json(500, str(e))

    # ---------- DELETE ----------

    def do_DELETE(self):  # noqa: N802
        path, _ = self._parse_path()
        rt = _runtime
        if rt is None:
            self._send_error_json(503, "runtime not initialized")
            return
        try:
            if path.startswith("/api/session/"):
                session_id = path[len("/api/session/"):]
                ok = self._sync(rt.repo_chat.delete_session(session_id))
                if not ok:
                    self._send_error_json(404, f"未找到会话 {session_id}")
                    return
                rt.drop_session(session_id)
                self._send_json(200, {"deleted": session_id})
            elif path.startswith("/api/memory/"):
                rel_id = path[len("/api/memory/"):]
                ok = self._sync(rt.repo.delete_triple(rel_id, soft=True))
                if not ok:
                    self._send_error_json(404, f"未找到 {rel_id}")
                    return
                self._send_json(200, {"deleted": rel_id})
            else:
                self._send_error_json(404, "unknown api")
        except Exception as e:  # noqa: BLE001
            logger.exception("DELETE 处理失败: %s", path)
            self._send_error_json(500, str(e))

    # ---------- SSE 对话 ----------

    def _stream_chat(self, body: dict) -> None:
        message = str(body.get("message") or "").strip()
        if not message:
            self._send_error_json(400, "消息不能为空")
            return
        rt = _runtime
        user_id = str(body.get("user_id") or "chat_user")
        session_id = body.get("session_id") or None
        session = rt.create_session(user_id, session_id)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True

        async def drive():
            try:
                async for event in session.react_stream(message):
                    self._write_sse(event)
            except Exception as e:  # noqa: BLE001
                logger.exception("chat 流式处理失败")
                self._write_sse({"type": "error", "error": str(e)})
            self._write_raw(b"data: [DONE]\n\n")

        try:
            asyncio.run(drive())
        except Exception as e:  # noqa: BLE001
            logger.exception("SSE 驱动失败")
            try:
                self._write_sse({"type": "error", "error": str(e)})
            except Exception:  # noqa: BLE001
                pass

    def _write_sse(self, event: dict) -> None:
        self._write_raw(
            ("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode("utf-8")
        )

    def _write_raw(self, data: bytes) -> None:
        self.wfile.write(data)
        self.wfile.flush()

    # ---------- 辅助 ----------

    @staticmethod
    def _sync(coro):
        """在请求线程里同步执行 async 函数"""
        return asyncio.run(coro)

    @staticmethod
    def _memory_payload(rt: AppRuntime, user_id: str) -> dict:
        triples = []
        for dim in ("user", "self", "world", "env"):
            for t in LocalHandler._sync(
                rt.repo.list_triples_by_dimension(user_id, dim)
            ):
                triples.append(_triple_to_dict(t))
        states = LocalHandler._sync(rt.repo.get_active_states(user_id))
        notes = LocalHandler._sync(rt.repo.get_notes_for_injection(user_id, top_k=100))
        return {
            "triples": triples,
            "states": [_state_to_dict(s) for s in states],
            "notes": [_note_to_dict(n) for n in notes],
        }

    def log_message(self, format, *args):  # noqa: A002
        logger.info("local-server %s - %s", self.address_string(), format % args)


# ==================== 生命周期 ====================


def _load_env(data_dir: Path) -> None:
    """优先加载数据目录下的 .env（App 设置页写入的 API Key）"""
    env_path = data_dir / ".env"
    if env_path.exists():
        os.environ.pop("AION_LLM_API_KEY", None)
        os.environ.pop("LLM_API_KEY", None)
        load_env_from_dotenv(env_path)


def start_local_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    data_dir: Optional[Path] = None,
) -> ThreadingHTTPServer:
    """启动本地服务器（后台线程），返回 server 对象"""
    global _runtime, _server
    data = Path(data_dir) if data_dir else None
    if data is not None:
        data.mkdir(parents=True, exist_ok=True)
        _load_env(data)
    rt = AppRuntime(data_dir=data)
    _runtime = rt
    server = ThreadingHTTPServer((host, port), LocalHandler)
    server.daemon_threads = True
    _server = server
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="aion-local")
    thread.start()
    logger.info("local server started at http://%s:%d data=%s", host, port, rt.data_dir)
    return server


def stop_local_server() -> None:
    global _server
    if _server is not None:
        _server.shutdown()
        _server.server_close()
        _server = None


def set_api_key(key: str) -> bool:
    """写入 API Key 到数据目录 .env 并重置 LLM 缓存（App 设置页调用）"""
    if _runtime is None:
        return False
    key = str(key or "").strip()
    env_path = _runtime.data_dir / ".env"
    lines = []
    if env_path.exists():
        lines = [
            ln for ln in env_path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("AION_LLM_API_KEY=")
            and not ln.strip().startswith("LLM_API_KEY=")
        ]
    if key:
        lines.append(f"AION_LLM_API_KEY={key}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ.pop("AION_LLM_API_KEY", None)
    os.environ.pop("LLM_API_KEY", None)
    load_env_from_dotenv(env_path)
    _runtime.reset_llm()
    return True
