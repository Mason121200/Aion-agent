"""JSON 版对话历史仓库 —— 会话与消息持久化到 chat.json

MVP 简化：zero_code 的 SQLite 对话仓库被替换为轻量 JSON，
原子写（先写 .tmp 再替换），重启后可从磁盘恢复上下文。
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from aion_agent.core.entities.message import Message
from aion_agent.core.ports.i_chat_repo import IChatRepo

logger = logging.getLogger(__name__)


def _parse_dt(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def _to_iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


class JsonChatRepo(IChatRepo):
    """内存 + JSON 落盘的对话历史仓库"""

    def __init__(self, persist_dir: Optional[str] = None):
        self._persist_dir = Path(persist_dir) if persist_dir else None
        # session_id -> {"user_id": str, "messages": [dict], "created_at": iso}
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._load()

    # ==================== 持久化 ====================

    @property
    def _persist_file(self) -> Optional[Path]:
        if self._persist_dir is None:
            return None
        return self._persist_dir / "chat.json"

    def _load(self) -> None:
        pf = self._persist_file
        if pf is None or not pf.exists():
            return
        try:
            self._sessions = json.loads(pf.read_text(encoding="utf-8"))
            logger.info(f"已从 {pf} 恢复 {len(self._sessions)} 个会话")
        except Exception as e:
            logger.warning(f"对话历史恢复失败，从空库开始: {e}")

    def _save(self) -> None:
        pf = self._persist_file
        if pf is None:
            return
        try:
            pf.parent.mkdir(parents=True, exist_ok=True)
            tmp = pf.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(self._sessions, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
            tmp.replace(pf)
        except Exception as e:  # 落盘失败不应中断主流程
            logger.error(f"对话历史持久化失败: {e}")

    # ==================== IChatRepo 实现 ====================

    async def create_session(self, user_id: str) -> str:
        session_id = f"session_{uuid.uuid4().hex[:8]}"
        self._sessions[session_id] = {
            "user_id": user_id,
            "created_at": _to_iso(datetime.now()),
            "messages": [],
        }
        self._save()
        return session_id

    async def save_message(self, message: Message) -> str:
        sid = message.session_id
        if sid not in self._sessions:
            self._sessions[sid] = {
                "user_id": "unknown",
                "created_at": _to_iso(datetime.now()),
                "messages": [],
            }
        msg_id = f"msg_{uuid.uuid4().hex[:8]}"
        self._sessions[sid]["messages"].append({
            "id": msg_id,
            "session_id": sid,
            "role": message.role,
            "content": message.content,
            "reasoning": message.reasoning,
            "tool_call_id": message.tool_call_id,
            "created_at": _to_iso(message.created_at),
        })
        self._save()
        return msg_id

    async def get_history(
        self, session_id: str, limit: Optional[int] = None
    ) -> List[Message]:
        session = self._sessions.get(session_id)
        if session is None:
            return []
        raw = session.get("messages", [])
        if limit is not None and limit > 0:
            raw = raw[-limit:]
        messages = []
        for m in raw:
            try:
                messages.append(Message(
                    session_id=m.get("session_id", session_id),
                    role=m.get("role", "user"),
                    content=m.get("content", ""),
                    reasoning=m.get("reasoning"),
                    tool_call_id=m.get("tool_call_id"),
                    created_at=_parse_dt(m.get("created_at")) or datetime.now(),
                ))
            except Exception:
                continue
        return messages

    async def list_sessions(self, user_id: str) -> List[dict]:
        out = []
        for sid, session in self._sessions.items():
            if session.get("user_id") != user_id:
                continue
            messages = session.get("messages", [])
            preview = ""
            if messages:
                content = messages[-1].get("content", "") or ""
                preview = content if len(content) <= 50 else content[:50] + "..."
            out.append({
                "session_id": sid,
                "user_id": user_id,
                "created_at": session.get("created_at"),
                "message_count": len(messages),
                "preview": preview,
            })
        return out

    async def delete_session(self, session_id: str) -> bool:
        existed = session_id in self._sessions
        self._sessions.pop(session_id, None)
        if existed:
            self._save()
        return existed