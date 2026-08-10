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
import uuid
from pathlib import Path
from typing import AsyncGenerator, Dict, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from aion_agent.server.runtime import (
    AppRuntime,
    ConfigError,
    _default_data_dir,
    _iso,
    _note_to_dict,
    _state_to_dict,
    _triple_to_dict,
    _ui_dir,
)

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
from aion_agent.skills import build_default_skills
from aion_agent.storage.in_memory_cognitive_repo import InMemoryCognitiveRepo
from aion_agent.storage.json_chat_repo import JsonChatRepo
from aion_agent.use_cases.react_chat_session import ReActChatSession

logger = logging.getLogger(__name__)

def _catalog_tools(rt: AppRuntime) -> list:
    """构建一次性的工具目录：默认技能全量注册，返回工具名 + 权限 + 所属技能"""
    from aion_agent.skills import build_default_skills
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
        {
            "name": e["name"],
            "permission": e["permission"],
            "level": e["level"],
            "skill": skill_of.get(e["name"], ""),
        }
        for e in registry.list_tool_entries()
    ]


def create_app(runtime: Optional[AppRuntime] = None) -> FastAPI:
    rt = runtime or AppRuntime()
    app = FastAPI(title="Aion Agent", version="0.1.0")

    # 本地应用：禁用缓存，保证 UI 更新立即生效（Android WebView / 浏览器通用）
    @app.middleware("http")
    async def no_store_cache(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    # ---------- 健康 / 状态 ----------

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "llm": rt.llm_status(), "agnes": rt.agnes_status()}

    # ---------- 会话 ----------

    @app.post("/api/session")
    async def create_session(body: dict):
        user_id = str(body.get("user_id") or "chat_user")
        try:
            session = rt.create_session(user_id)
        except ConfigError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"session_id": session.session_id, "user_id": user_id}

    @app.post("/api/session/{session_id}/meta")
    async def update_session_meta(session_id: str, body: dict):
        title = body.get("title")
        pinned = body.get("pinned")
        if title is None and pinned is None:
            raise HTTPException(status_code=400, detail="缺少参数 title / pinned")
        meta = rt.repo_chat.update_session_meta(
            session_id, title=title, pinned=pinned
        )
        if meta is None:
            raise HTTPException(status_code=404, detail=f"未找到会话 {session_id}")
        return meta

    # ---------- LLM 配置（设置页） ----------

    @app.post("/api/config/llm")
    async def config_llm(body: dict):
        api_key = str(body.get("api_key") or "").strip()
        if not api_key:
            raise HTTPException(status_code=400, detail="api_key 不能为空")
        try:
            status = rt.save_llm_config(
                api_key=api_key,
                base_url=str(body.get("base_url") or "").strip(),
                model=str(body.get("model") or "").strip(),
            )
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=str(e))
        return {"saved": True, "llm": status}

    # ---------- Agnes 生图/视频配置（设置页） ----------

    @app.post("/api/config/agnes")
    async def config_agnes(body: dict):
        api_key = str(body.get("api_key") or "").strip()
        if not api_key:
            raise HTTPException(status_code=400, detail="api_key 不能为空")
        try:
            status = rt.save_agnes_config(
                api_key=api_key,
                base_url=str(body.get("base_url") or "").strip(),
                image_model=str(body.get("image_model") or "").strip(),
                video_model=str(body.get("video_model") or "").strip(),
            )
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=str(e))
        return {"saved": True, "agnes": status}

    # ---------- 对话（SSE） ----------

    @app.post("/api/chat")
    async def chat(body: dict):
        message = str(body.get("message") or "").strip()
        if not message:
            raise HTTPException(status_code=400, detail="消息不能为空")
        user_id = str(body.get("user_id") or "chat_user")
        session_id = body.get("session_id") or None
        images = [str(u).strip() for u in (body.get("images") or []) if str(u).strip()]
        try:
            session = rt.create_session(user_id, session_id)
        except ConfigError as e:
            raise HTTPException(status_code=400, detail=str(e))

        async def event_stream() -> AsyncGenerator[str, None]:
            try:
                async for event in session.react_stream(message, images=images):
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
                    "images": m.images or [],
                    "created_at": _iso(m.created_at),
                }
                for m in msgs
            ]
        }

    # ---------- 图片上传 ----------

    @app.post("/api/upload")
    async def upload_image(file: UploadFile = File(...)):
        upload_dir = rt.data_dir / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        ext = (Path(file.filename or "").suffix or ".png").lower()
        if ext not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
            raise HTTPException(status_code=400, detail=f"不支持的图片格式: {ext}")
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="空文件")
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="图片过大（上限 10MB）")
        name = f"up_{uuid.uuid4().hex[:12]}{ext}"
        (upload_dir / name).write_bytes(content)
        return {"url": f"/uploads/{name}", "name": name}

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

    # ---------- 学习场景 ----------

    @app.get("/api/study/overview")
    async def study_overview():
        return rt.repo_study.overview()

    @app.get("/api/study/notifications")
    async def study_notifications():
        return {"notifications": rt.pending_notifications()}

    @app.post("/api/study/notifications/ack")
    async def study_notifications_ack():
        rt.ack_notifications()
        return {"ok": True}

    @app.post("/api/study/complete_reminder")
    async def study_complete_reminder(body: dict):
        rid = str(body.get("reminder_id") or "")
        if not rid:
            raise HTTPException(status_code=400, detail="缺少参数 reminder_id")
        ok = rt.repo_study.complete_reminder(rid)
        if not ok:
            raise HTTPException(status_code=404, detail=f"未找到提醒 {rid}")
        return {"ok": True}

    @app.post("/api/study/log_session")
    async def study_log_session(body: dict):
        subject = str(body.get("subject") or "").strip()
        minutes = int(body.get("minutes") or 0)
        if not subject or minutes <= 0:
            raise HTTPException(status_code=400, detail="缺少参数 subject/minutes")
        session = rt.repo_study.log_session(
            subject=subject,
            minutes=minutes,
            note=str(body.get("note") or "").strip(),
            plan_id=str(body.get("plan_id") or "") or None,
        )
        return {"session": session, "today_minutes": rt.repo_study.today_minutes()}

    # ---------- 学习中心（闭环 UI API） ----------

    @app.get("/api/study/plans/{plan_id}")
    async def study_plan_detail(plan_id: str):
        detail = rt.repo_study.plan_detail(plan_id)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"未找到计划 {plan_id}")
        detail["tests"] = rt.repo_study.list_tests(plan_id=plan_id)
        detail["mistakes"] = rt.repo_study.list_mistakes(plan_id=plan_id, include_resolved=True)
        detail["reviews"] = rt.repo_study.list_reviews(plan_id=plan_id, include_done=True)
        return detail

    @app.get("/api/study/plans/{plan_id}/analysis")
    async def study_plan_analysis(plan_id: str, days: int = 7):
        try:
            return rt.repo_study.analyze(plan_id=plan_id, days=days)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/api/study/tests")
    async def study_test_log(body: dict):
        plan_id = str(body.get("plan_id") or "").strip()
        total = int(body.get("total") or 0)
        if not plan_id or total <= 0:
            raise HTTPException(status_code=400, detail="缺少参数 plan_id / total（total 需大于 0）")
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
            raise HTTPException(status_code=400, detail=str(e))
        return {"test": test}

    @app.post("/api/study/mistakes")
    async def study_mistake_add(body: dict):
        plan_id = str(body.get("plan_id") or "").strip()
        content = str(body.get("content") or "").strip()
        if not plan_id or not content:
            raise HTTPException(status_code=400, detail="缺少参数 plan_id / content")
        try:
            mistake = rt.repo_study.add_mistake(
                plan_id=plan_id, content=content,
                reason=str(body.get("reason") or "").strip(),
                knowledge_point=str(body.get("knowledge_point") or "").strip(),
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"mistake": mistake}

    @app.post("/api/study/plans/{plan_id}/reviews/{review_item_id}/checkin")
    async def study_review_checkin(plan_id: str, review_item_id: str, body: dict):
        try:
            item = rt.repo_study.review_checkin(
                plan_id=plan_id, review_item_id=review_item_id,
                correct=bool(body.get("correct")),
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        due = rt.repo_study.schedule_reviews(plan_id)
        return {"review_item": item, "due_remaining": len(due)}

    # ---------- 静态 UI（PWA） ----------

    @app.get("/")
    async def index():
        return FileResponse(_ui_dir() / "index.html")

    @app.get("/sw.js")
    async def service_worker():
        return FileResponse(_ui_dir() / "sw.js", media_type="application/javascript")

    # ---------- 执行日志 ----------

    @app.get("/api/execution_log")
    async def execution_log(session_id: str = "", event_type: str = "", limit: int = 100):
        return {
            "events": rt.execution_log.query(
                session_id=session_id or None,
                event_type=event_type or None,
                limit=limit,
            )
        }

    # ---------- 通用任务规划 ----------

    @app.get("/api/plans")
    async def list_plans(status: str = ""):
        return {"plans": rt.repo_planner.list_plans(status=status or None)}

    # ---------- 跨设备同步 ----------

    @app.get("/api/sync/export")
    async def sync_export():
        return rt.sync_export()

    @app.post("/api/sync/import")
    async def sync_import(body: dict):
        bundle = body.get("bundle")
        if not isinstance(bundle, dict):
            raise HTTPException(status_code=400, detail="缺少参数 bundle")
        return {"merged": rt.sync_import(bundle)}

    @app.post("/api/sync/pull")
    async def sync_pull(body: dict):
        url = str(body.get("url") or "").strip()
        if not url:
            raise HTTPException(status_code=400, detail="缺少参数 url")
        try:
            return {"merged": rt.sync_pull(url)}
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"拉取失败: {e}")

    @app.get("/api/sync/status")
    async def sync_status():
        return rt.sync_status()

    # ---------- 技能 / 工具 ----------

    @app.get("/api/skills")
    async def list_skills():
        skills = []
        for s in build_default_skills(
            cognitive_repo=rt.repo,
            study_repo=rt.repo_study,
            planner_repo=rt.repo_planner,
            user_id="chat_user",
        ):
            skills.append({**s.to_dict(), "enabled": rt.is_skill_enabled(s.name)})
        return {"skills": skills}

    @app.post("/api/skills/{name}/toggle")
    async def toggle_skill(name: str, body: dict):
        enabled = bool(body.get("enabled", True))
        if not rt.set_skill_enabled(name, enabled):
            raise HTTPException(status_code=404, detail=f"未找到技能: {name}")
        return {"name": name, "enabled": enabled}

    @app.get("/api/tools")
    async def list_tools():
        return {"tools": _catalog_tools(rt), "policy": rt.tool_policy.to_dict()}

    @app.post("/api/tools/policy")
    async def set_tool_policy(body: dict):
        rt.set_tool_policy(
            blocked=body.get("blocked") or [],
            confirm=body.get("confirm") or [],
        )
        return {"policy": rt.tool_policy.to_dict()}

    upload_dir = rt.data_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=upload_dir), name="uploads")
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
