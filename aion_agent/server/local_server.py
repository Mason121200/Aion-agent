"""Aion Agent 手机本地服务器 —— 纯标准库（http.server），供 Android APK 内嵌

实现与 FastAPI 版一致的 API 协议，Web UI（PWA）无需任何改动：
    GET    /api/health                 服务与 LLM 配置状态
    POST   /api/config/agnes           Agnes 生图/视频配置（key/URL/模型）
    POST   /api/upload                 图片上传（multipart/form-data）
    GET    /uploads/*                  已上传图片的静态访问
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
    GET    /api/study/plans/{id}        学习计划详情（含测试/错题/复习）
    POST   /api/study/tests             记录测试成绩
    POST   /api/study/mistakes          添加错题
    GET    /api/study/plans/{id}/analysis 计划效果分析
    POST   /api/study/plans/{id}/reviews/{rid}/checkin 复习打卡
    GET    /api/sync/status             跨设备同步状态
    GET    /api/sync/export             导出同步包
    POST   /api/sync/import             导入同步包
    POST   /api/sync/pull               从对端拉取同步包
    GET    /api/skills                  技能列表（含启停状态）
    POST   /api/skills/{name}/toggle    启停技能
    GET    /api/tools                   工具目录与权限策略
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
import re
import uuid
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Optional

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
from aion_agent.skills import build_default_skills

logger = logging.getLogger(__name__)

_MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webmanifest": "application/manifest+json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
}

_runtime: Optional[AppRuntime] = None
_server: Optional[ThreadingHTTPServer] = None


def get_runtime() -> Optional[AppRuntime]:
    return _runtime


def _catalog_tools(rt: AppRuntime) -> list:
    """构建一次性的工具目录：默认技能全量注册，返回工具名 + 权限 + 所属技能"""
    from aion_agent.tools import ToolRegistry

    skills = build_default_skills(
        cognitive_repo=rt.repo,
        study_repo=rt.repo_study,
        planner_repo=rt.repo_planner,
        user_id="chat_user",
    )
    registry = ToolRegistry()
    skill_of: Dict[str, str] = {}
    for skill in skills:
        for name in skill.tools:
            skill_of[name] = skill.name
        skill.register_tools(registry)
    return [
        {"name": e["name"], "permission": e["permission"], "level": e["level"],
         "skill": skill_of.get(e["name"], "")}
        for e in registry.list_tool_entries()
    ]


_ALLOWED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _parse_multipart(body: bytes, content_type: str) -> Optional[dict]:
    """极简 multipart/form-data 解析：提取名为 file 的文件字段（纯标准库）"""
    m = re.search(r'boundary="?([^";]+)"?', content_type or "")
    if not m:
        return None
    boundary = m.group(1).encode("utf-8")
    for part in body.split(b"--" + boundary):
        if b"\r\n\r\n" not in part:
            continue
        header, _, content = part.partition(b"\r\n\r\n")
        head_text = header.decode("utf-8", errors="replace")
        if 'name="file"' not in head_text:
            continue
        fm = re.search(r'filename="([^"]*)"', head_text)
        filename = fm.group(1) if fm else ""
        return {
            "filename": filename,
            "content": content[:-2] if content.endswith(b"\r\n") else content,
        }
    return None


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
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)
        self.wfile.flush()
        return True

    def _serve_upload(self, path: str, rt: AppRuntime) -> bool:
        """返回 True 表示已处理（含 404）；服务 data_dir/uploads 下的文件"""
        upload_root = (rt.data_dir / "uploads").resolve()
        rel = path[len("/uploads/"):].lstrip("/")
        target = (upload_root / rel).resolve()
        if not str(target).startswith(str(upload_root)):
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
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)
        self.wfile.flush()
        return True


    def _study_plan_get(self, path: str, q: dict, rt: AppRuntime) -> None:
        rest = path[len("/api/study/plans/"):]
        if rest.endswith("/analysis"):
            plan_id = rest[:-len("/analysis")]
            try:
                self._send_json(200, rt.repo_study.analyze(plan_id=plan_id, days=int(q.get("days") or 7)))
            except ValueError as e:
                self._send_error_json(404, str(e))
            return
        plan_id = rest
        detail = rt.repo_study.plan_detail(plan_id)
        if detail is None:
            self._send_error_json(404, f"未找到计划 {plan_id}")
            return
        detail["tests"] = rt.repo_study.list_tests(plan_id=plan_id)
        detail["mistakes"] = rt.repo_study.list_mistakes(plan_id=plan_id, include_resolved=True)
        detail["reviews"] = rt.repo_study.list_reviews(plan_id=plan_id, include_done=True)
        self._send_json(200, detail)


    # ---------- GET ----------

    def do_GET(self):  # noqa: N802
        path, q = self._parse_path()
        rt = _runtime
        if rt is None:
            self._send_error_json(503, "runtime not initialized")
            return
        try:
            if path == "/api/health":
                self._send_json(200, {"status": "ok", "llm": rt.llm_status(), "agnes": rt.agnes_status()})
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
            elif path.startswith("/api/study/plans/"):
                self._study_plan_get(path, q, rt)
            elif path == "/api/sync/status":
                self._send_json(200, rt.sync_status())
            elif path == "/api/sync/export":
                self._send_json(200, rt.sync_export())
            elif path == "/api/skills":
                skills = []
                for s in build_default_skills(
                    cognitive_repo=rt.repo,
                    study_repo=rt.repo_study,
                    planner_repo=rt.repo_planner,
                    user_id="chat_user",
                ):
                    skills.append({**s.to_dict(), "enabled": rt.is_skill_enabled(s.name)})
                self._send_json(200, {"skills": skills})
            elif path == "/api/tools":
                self._send_json(200, {"tools": _catalog_tools(rt), "policy": rt.tool_policy.to_dict()})
            elif path.startswith("/uploads/"):
                self._serve_upload(path, rt)
            elif path.startswith("/api/"):
                self._send_error_json(404, "unknown api")
            else:
                self._serve_static(path)
        except ConfigError as e:
            self._send_error_json(400, str(e))
        except Exception as e:  # noqa: BLE001
            logger.exception("GET 处理失败: %s", path)
            self._send_error_json(500, str(e))

    def _handle_upload(self, rt: AppRuntime) -> None:
        """处理图片上传（multipart/form-data），保存到 data_dir/uploads"""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                self._send_error_json(400, "空文件")
                return
            raw = self.rfile.read(length)
            parsed = _parse_multipart(raw, self.headers.get("Content-Type") or "")
            if not parsed or not parsed["content"]:
                self._send_error_json(400, "缺少文件字段 file")
                return
            ext = (Path(parsed["filename"] or "").suffix or ".png").lower()
            if ext not in _ALLOWED_IMAGE_EXT:
                self._send_error_json(400, f"不支持的图片格式: {ext}")
                return
            if len(parsed["content"]) > _MAX_UPLOAD_BYTES:
                self._send_error_json(400, "图片过大（上限 10MB）")
                return
            upload_dir = rt.data_dir / "uploads"
            upload_dir.mkdir(parents=True, exist_ok=True)
            name = f"up_{uuid.uuid4().hex[:12]}{ext}"
            (upload_dir / name).write_bytes(parsed["content"])
            self._send_json(200, {"url": f"/uploads/{name}", "name": name})
        except Exception as e:  # noqa: BLE001
            logger.exception("上传处理失败")
            self._send_error_json(500, str(e))


    # ---------- POST ----------
    # ---------- POST ----------

    def do_POST(self):  # noqa: N802
        path, _ = self._parse_path()
        rt = _runtime
        if rt is None:
            self._send_error_json(503, "runtime not initialized")
            return
        if path == "/api/upload":
            self._handle_upload(rt)
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
            elif path.startswith("/api/session/") and path.endswith("/meta"):
                session_id = path[len("/api/session/"):-len("/meta")]
                title = body.get("title")
                pinned = body.get("pinned")
                if title is None and pinned is None:
                    raise ConfigError("缺少参数 title / pinned")
                meta = rt.repo_chat.update_session_meta(
                    session_id, title=title, pinned=pinned
                )
                if meta is None:
                    self._send_error_json(404, f"未找到会话 {session_id}")
                    return
                self._send_json(200, meta)
            elif path == "/api/config/llm":
                api_key = str(body.get("api_key") or "").strip()
                if not api_key:
                    raise ConfigError("api_key 不能为空")
                try:
                    status = rt.save_llm_config(
                        api_key=api_key,
                        base_url=str(body.get("base_url") or "").strip(),
                        model=str(body.get("model") or "").strip(),
                    )
                except RuntimeError as e:
                    self._send_error_json(500, str(e))
                    return
                self._send_json(200, {"saved": True, "llm": status})
            elif path == "/api/config/agnes":
                api_key = str(body.get("api_key") or "").strip()
                if not api_key:
                    raise ConfigError("api_key 不能为空")
                try:
                    status = rt.save_agnes_config(
                        api_key=api_key,
                        base_url=str(body.get("base_url") or "").strip(),
                        image_model=str(body.get("image_model") or "").strip(),
                        video_model=str(body.get("video_model") or "").strip(),
                    )
                except RuntimeError as e:
                    self._send_error_json(500, str(e))
                    return
                self._send_json(200, {"saved": True, "agnes": status})
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
            elif path == "/api/study/tests":
                plan_id = str(body.get("plan_id") or "").strip()
                total = int(body.get("total") or 0)
                if not plan_id or total <= 0:
                    raise ConfigError("缺少参数 plan_id / total（total 需大于 0）")
                try:
                    test = rt.repo_study.add_test(
                        plan_id=plan_id,
                        title=str(body.get("title") or "").strip(),
                        score=int(body.get("score") or 0),
                        total=total,
                        correct=body.get("correct"),
                        knowledge_points=body.get("knowledge_points") or [],
                        note=str(body.get("note") or "").strip(),
                    )
                except ValueError as e:
                    self._send_error_json(400, str(e))
                    return
                self._send_json(200, {"test": test})
            elif path == "/api/study/mistakes":
                plan_id = str(body.get("plan_id") or "").strip()
                content = str(body.get("content") or "").strip()
                if not plan_id or not content:
                    raise ConfigError("缺少参数 plan_id / content")
                try:
                    mistake = rt.repo_study.add_mistake(
                        plan_id=plan_id, content=content,
                        reason=str(body.get("reason") or "").strip(),
                        knowledge_point=str(body.get("knowledge_point") or "").strip(),
                    )
                except ValueError as e:
                    self._send_error_json(400, str(e))
                    return
                self._send_json(200, {"mistake": mistake})
            elif path.startswith("/api/study/plans/") and path.endswith("/checkin"):
                inner = path[len("/api/study/plans/"):-len("/checkin")]
                plan_id, _, review_item_id = inner.partition("/reviews/")
                try:
                    item = rt.repo_study.review_checkin(
                        plan_id=plan_id, review_item_id=review_item_id,
                        correct=bool(body.get("correct")),
                    )
                except ValueError as e:
                    self._send_error_json(400, str(e))
                    return
                due = rt.repo_study.schedule_reviews(plan_id)
                self._send_json(200, {"review_item": item, "due_remaining": len(due)})
            elif path == "/api/sync/import":
                bundle = body.get("bundle")
                if not isinstance(bundle, dict):
                    raise ConfigError("缺少参数 bundle")
                self._send_json(200, {"merged": rt.sync_import(bundle)})
            elif path == "/api/sync/pull":
                url = str(body.get("url") or "").strip()
                if not url:
                    raise ConfigError("缺少参数 url")
                try:
                    self._send_json(200, {"merged": rt.sync_pull(url)})
                except Exception as e:  # noqa: BLE001
                    self._send_error_json(400, f"拉取失败: {e}")
            elif path.startswith("/api/skills/") and path.endswith("/toggle"):
                name = path[len("/api/skills/"):-len("/toggle")]
                enabled = bool(body.get("enabled", True))
                if not rt.set_skill_enabled(name, enabled):
                    self._send_error_json(404, f"未找到技能: {name}")
                    return
                self._send_json(200, {"name": name, "enabled": enabled})
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
        images = [str(u).strip() for u in (body.get("images") or []) if str(u).strip()]
        session = rt.create_session(user_id, session_id)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True

        async def drive():
            try:
                async for event in session.react_stream(message, images=images):
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
    uploads = rt.data_dir / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
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


def set_agnes_config(
    api_key: str = "",
    base_url: str = "",
    image_model: str = "",
    video_model: str = "",
) -> bool:
    """写入 Agnes 配置到数据目录 .env 并立即生效（App 设置页调用）"""
    if _runtime is None:
        return False
    env_path = _runtime.data_dir / ".env"
    lines = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

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

    for key, value in (
        ("AGNES_API_KEY", str(api_key or "").strip()),
        ("AGNES_BASE_URL", str(base_url or "").strip()),
        ("AGNES_IMAGE_MODEL", str(image_model or "").strip()),
        ("AGNES_VIDEO_MODEL", str(video_model or "").strip()),
    ):
        upsert(key, value)
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for key in ("AGNES_API_KEY", "AGNES_BASE_URL", "AGNES_IMAGE_MODEL", "AGNES_VIDEO_MODEL"):
        os.environ.pop(key, None)
    load_env_from_dotenv(env_path)
    return True
