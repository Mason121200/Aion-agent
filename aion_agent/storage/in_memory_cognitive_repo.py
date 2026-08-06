"""内存版认知存储 —— 带去重，可选 numpy 向量检索，可 JSON 落盘

实现 ICognitiveRepo 全部接口。
- 精确去重：相同 (subject, predicate, object, user_id) 合并置信度
- 关键词检索：默认模式（子串匹配 + 使用次数/置信度排序）
- 向量检索：注入 HashEmbedder（或任意 embed 服务）后启用 NumpyVectorStore
- 持久化：persist_dir 指定时，三元组/状态/笔记落盘到 cognitive.json，
  向量索引落盘到 numpy_vector/；重启后从磁盘恢复全部记忆

MVP 简化：zero_code 的 SQLite 持久化被替换为轻量 JSON（无第三方依赖）。
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from aion_agent.core.entities.agent_state import AgentState
from aion_agent.core.entities.cognitive_triple import CognitiveTriple, Dimension
from aion_agent.core.entities.note import Note, NoteType
from aion_agent.core.ports.i_cognitive_repo import ICognitiveRepo
from aion_agent.storage.numpy_vector_store import NumpyVectorStore

logger = logging.getLogger(__name__)


def _parse_dt(value) -> Optional[datetime]:
    """ISO 字符串 → datetime（None 原样返回）"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def _to_iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


class InMemoryCognitiveRepo(ICognitiveRepo):
    """内存版认知存储（带去重 + 可选 JSON 落盘）"""

    def __init__(self, embedder=None, persist_dir: Optional[str] = None):
        self._persist_dir = Path(persist_dir) if persist_dir else None
        self._triples: Dict[str, CognitiveTriple] = {}
        self._dedup_index: Dict[Tuple[str, str, str, str], str] = {}
        self._states: Dict[str, AgentState] = {}
        self._notes: Dict[str, Note] = {}
        self._embedder = embedder
        self._vector_store = (
            NumpyVectorStore(persist_dir=self._persist_dir) if embedder else None
        )
        self._load_persisted()

    # ==================== 持久化 ====================

    @property
    def _persist_file(self) -> Optional[Path]:
        if self._persist_dir is None:
            return None
        return self._persist_dir / "cognitive.json"

    def _load_persisted(self) -> None:
        """启动时从磁盘恢复记忆（幂等）"""
        pf = self._persist_file
        if pf is None or not pf.exists():
            return
        try:
            data = json.loads(pf.read_text(encoding="utf-8"))
            for td in data.get("triples", []):
                triple = self._triple_from_dict(td)
                self._triples[triple.rel_id] = triple
                self._dedup_index[
                    (triple.subject, triple.predicate, triple.object, triple.user_id)
                ] = triple.rel_id
            for sd in data.get("states", []):
                state = self._state_from_dict(sd)
                self._states[state.state_id] = state
            for nd in data.get("notes", []):
                note = self._note_from_dict(nd)
                self._notes[note.note_id] = note

            # 重建向量索引，避免重启后出现孤儿向量
            if self._vector_store:
                self._vector_store.reset()
                for triple in self._triples.values():
                    self._sync_vector(triple)
            logger.info(
                f"已从 {pf} 恢复记忆：{len(self._triples)} 三元组 / "
                f"{len(self._states)} 状态 / {len(self._notes)} 笔记"
            )
        except Exception as e:
            logger.warning(f"记忆恢复失败，从空库开始: {e}")

    def _save_persisted(self) -> None:
        """全量落盘（原子写：先写临时文件再替换）"""
        pf = self._persist_file
        if pf is None:
            return
        try:
            pf.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "saved_at": datetime.now().isoformat(),
                "triples": [self._triple_to_dict(t) for t in self._triples.values()],
                "states": [self._state_to_dict(s) for s in self._states.values()],
                "notes": [self._note_to_dict(n) for n in self._notes.values()],
            }
            tmp = pf.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
            tmp.replace(pf)
        except Exception as e:  # 落盘失败不应中断主流程
            logger.error(f"认知持久化失败: {e}")

    # ==================== 序列化 ====================

    @staticmethod
    def _triple_to_dict(t: CognitiveTriple) -> dict:
        return {
            "rel_id": t.rel_id,
            "subject": t.subject,
            "predicate": t.predicate,
            "object": t.object,
            "dimension": t.dimension.value,
            "user_id": t.user_id,
            "confidence": t.confidence,
            "usage_count": t.usage_count,
            "is_active": t.is_active,
            "is_confirmed_by_user": t.is_confirmed_by_user,
            "source": t.source,
            "created_at": _to_iso(t.created_at),
            "updated_at": _to_iso(t.updated_at),
            "expires_at": _to_iso(t.expires_at),
        }

    @staticmethod
    def _triple_from_dict(d: dict) -> CognitiveTriple:
        return CognitiveTriple(
            rel_id=d.get("rel_id"),
            subject=d.get("subject", ""),
            predicate=d.get("predicate", ""),
            object=d.get("object", ""),
            dimension=Dimension(d.get("dimension", "world")),
            user_id=d.get("user_id", "default"),
            confidence=d.get("confidence", 0.6),
            usage_count=d.get("usage_count", 0),
            is_active=d.get("is_active", True),
            is_confirmed_by_user=d.get("is_confirmed_by_user", False),
            source=d.get("source"),
            created_at=_parse_dt(d.get("created_at")),
            updated_at=_parse_dt(d.get("updated_at")),
            expires_at=_parse_dt(d.get("expires_at")),
        )

    @staticmethod
    def _state_to_dict(s: AgentState) -> dict:
        return {
            "state_id": s.state_id,
            "user_id": s.user_id,
            "session_id": s.session_id,
            "task_id": s.task_id,
            "state_type": s.state_type,
            "state_name": s.state_name,
            "description": s.description,
            "context": s.context,
            "started_at": _to_iso(s.started_at),
            "last_updated_at": _to_iso(s.last_updated_at),
            "expires_at": _to_iso(s.expires_at),
            "is_active": s.is_active,
            "priority": s.priority,
            "released_at": _to_iso(s.released_at),
            "released_reason": s.released_reason,
        }

    @staticmethod
    def _state_from_dict(d: dict) -> AgentState:
        return AgentState(
            state_id=d.get("state_id"),
            user_id=d.get("user_id", "default"),
            session_id=d.get("session_id"),
            task_id=d.get("task_id"),
            state_type=d.get("state_type", "task"),
            state_name=d.get("state_name", ""),
            description=d.get("description"),
            context=d.get("context", {}),
            started_at=_parse_dt(d.get("started_at")),
            last_updated_at=_parse_dt(d.get("last_updated_at")),
            expires_at=_parse_dt(d.get("expires_at")),
            is_active=d.get("is_active", True),
            priority=d.get("priority", 0),
            released_at=_parse_dt(d.get("released_at")),
            released_reason=d.get("released_reason"),
        )

    @staticmethod
    def _note_to_dict(n: Note) -> dict:
        return {
            "note_id": n.note_id,
            "user_id": n.user_id,
            "note_type": n.note_type.value,
            "title": n.title,
            "content": n.content,
            "tags": n.tags,
            "related_session_id": n.related_session_id,
            "summary": n.summary,
            "created_at": _to_iso(n.created_at),
            "updated_at": _to_iso(n.updated_at),
            "archived_at": _to_iso(n.archived_at),
            "status": n.status,
        }

    @staticmethod
    def _note_from_dict(d: dict) -> Note:
        try:
            note_type = NoteType(d.get("note_type", "long_text"))
        except ValueError:
            note_type = NoteType.LONG_TEXT
        return Note(
            note_id=d.get("note_id"),
            user_id=d.get("user_id", "default"),
            note_type=note_type,
            title=d.get("title", ""),
            content=d.get("content", ""),
            tags=d.get("tags", []),
            related_session_id=d.get("related_session_id"),
            summary=d.get("summary"),
            created_at=_parse_dt(d.get("created_at")),
            updated_at=_parse_dt(d.get("updated_at")),
            archived_at=_parse_dt(d.get("archived_at")),
            status=d.get("status", "active"),
        )

    # ==================== 三元组操作（大脑皮层） ====================

    async def save_triple(self, triple: CognitiveTriple) -> str:
        dup_key = (
            triple.subject, triple.predicate,
            triple.object, triple.user_id,
        )
        existing_id = self._dedup_index.get(dup_key)

        if existing_id and existing_id in self._triples:
            existing = self._triples[existing_id]
            existing.confidence = max(triple.confidence, existing.confidence)
            existing.usage_count += 1
            existing.is_active = True
            self._sync_vector(existing)
            self._save_persisted()
            return existing_id

        if not triple.rel_id:
            triple.rel_id = f"rel_{uuid.uuid4().hex[:8]}"
        self._triples[triple.rel_id] = triple
        self._dedup_index[dup_key] = triple.rel_id
        self._sync_vector(triple)
        self._save_persisted()
        return triple.rel_id

    def _sync_vector(self, triple: CognitiveTriple) -> None:
        """同步三元组到向量索引（仅在启用 embedder 时）"""
        if not self._vector_store or not self._embedder:
            return
        try:
            vec = self._embedder.embed(triple.to_natural_language())
            self._vector_store.add(
                ids=[triple.rel_id],
                embeddings=[vec],
                metadatas=[{
                    "user_id": triple.user_id,
                    "dimension": triple.dimension.value,
                    "confidence": triple.confidence,
                }],
                documents=[triple.to_natural_language()],
            )
        except Exception:
            # 向量索引失败不应阻断主流程
            pass

    async def retrieve(
        self,
        user_id: str,
        query: str = "*",
        top_k: int = 5,
        dimensions: Optional[List[Dimension]] = None,
        min_confidence: float = 0.5,
    ) -> List[CognitiveTriple]:
        """RAG 检索认知三元组

        启用了 embedder 且 query 非空 → 向量检索（相关性排序）；
        否则 → 关键词子串检索（使用次数/置信度排序）。
        """
        if self._vector_store and self._embedder and query and query != "*":
            return await self._vector_retrieve(
                user_id, query, top_k, dimensions, min_confidence
            )
        return self._keyword_retrieve(
            user_id, query, top_k, dimensions, min_confidence
        )

    async def _vector_retrieve(
        self,
        user_id: str,
        query: str,
        top_k: int,
        dimensions: Optional[List[Dimension]],
        min_confidence: float,
    ) -> List[CognitiveTriple]:
        """向量检索：余弦相似度排序 + 维度/置信度过滤"""
        try:
            vec = self._embedder.embed(query)
            res = self._vector_store.query(
                vec,
                n_results=max(top_k * 5, 20),
                where={"user_id": user_id},
            )
        except Exception:
            return self._keyword_retrieve(
                user_id, query, top_k, dimensions, min_confidence
            )

        results: List[CognitiveTriple] = []
        for rid in res["ids"][0]:
            triple = self._triples.get(rid)
            if not triple or not triple.is_active:
                continue
            if triple.confidence < min_confidence:
                continue
            if triple.is_expired():
                continue
            if dimensions and triple.dimension not in dimensions:
                continue
            results.append(triple)
            if len(results) >= top_k:
                break
        return results

    def _keyword_retrieve(
        self,
        user_id: str,
        query: str = "*",
        top_k: int = 5,
        dimensions: Optional[List[Dimension]] = None,
        min_confidence: float = 0.5,
    ) -> List[CognitiveTriple]:
        """关键词检索：子串匹配，使用次数/置信度排序"""
        results = []
        query_lower = (query or "").lower()

        for triple in self._triples.values():
            if not triple.is_active:
                continue
            if triple.user_id != user_id:
                continue
            if triple.confidence < min_confidence:
                continue
            if triple.is_expired():
                continue
            if dimensions and triple.dimension not in dimensions:
                continue
            if query and query != "*":
                text = (
                    f"{triple.subject} {triple.predicate} {triple.object}"
                ).lower()
                if query_lower not in text:
                    continue
            results.append(triple)

        results.sort(
            key=lambda t: (t.usage_count, t.confidence), reverse=True
        )
        return results[:top_k]

    async def get_triple(self, rel_id: str) -> Optional[CognitiveTriple]:
        return self._triples.get(rel_id)

    async def update_confidence(
        self, rel_id: str, confidence: float
    ) -> None:
        if rel_id in self._triples:
            self._triples[rel_id].confidence = confidence
            self._save_persisted()

    async def increment_usage(self, rel_id: str) -> None:
        if rel_id in self._triples:
            self._triples[rel_id].usage_count += 1
            self._save_persisted()

    async def delete_triple(
        self, rel_id: str, soft: bool = True
    ) -> bool:
        if rel_id not in self._triples:
            return False
        if soft:
            self._triples[rel_id].is_active = False
            self._save_persisted()
            return True
        triple = self._triples.pop(rel_id)
        dup_key = (
            triple.subject, triple.predicate,
            triple.object, triple.user_id,
        )
        self._dedup_index.pop(dup_key, None)
        if self._vector_store:
            try:
                self._vector_store.delete([rel_id])
            except Exception:
                pass
        self._save_persisted()
        return True

    async def update_triple(
        self,
        rel_id: str,
        subject: Optional[str] = None,
        predicate: Optional[str] = None,
        object_: Optional[str] = None,
        dimension: Optional[Dimension] = None,
        confidence: Optional[float] = None,
    ) -> Optional[CognitiveTriple]:
        """修改三元组内容，同步更新去重索引与向量"""
        triple = self._triples.get(rel_id)
        if triple is None:
            return None

        old_key = (
            triple.subject, triple.predicate,
            triple.object, triple.user_id,
        )

        if subject is not None:
            triple.subject = subject
        if predicate is not None:
            triple.predicate = predicate
        if object_ is not None:
            triple.object = object_
        if dimension is not None:
            triple.dimension = dimension
        if confidence is not None:
            triple.confidence = confidence

        new_key = (
            triple.subject, triple.predicate,
            triple.object, triple.user_id,
        )
        if old_key != new_key:
            self._dedup_index.pop(old_key, None)
            self._dedup_index[new_key] = rel_id
        self._sync_vector(triple)
        self._save_persisted()
        return triple

    async def list_triples_by_dimension(
        self,
        user_id: str,
        dimension: Dimension,
        is_active: bool = True,
    ) -> List[CognitiveTriple]:
        return [
            t for t in self._triples.values()
            if t.user_id == user_id
            and t.dimension == dimension
            and t.is_active == is_active
        ]

    # ==================== 状态认知操作（AgentState） ====================

    async def save_state(self, state: AgentState) -> str:
        if not state.state_id:
            state.state_id = f"state_{uuid.uuid4().hex[:8]}"
        self._states[state.state_id] = state
        self._save_persisted()
        return state.state_id

    async def get_active_states(
        self, user_id: str, session_id: Optional[str] = None
    ) -> List[AgentState]:
        results = []
        for state in self._states.values():
            if not state.is_active:
                continue
            if state.user_id != user_id:
                continue
            if session_id and state.session_id != session_id:
                continue
            results.append(state)
        return sorted(
            results, key=lambda s: s.priority, reverse=True
        )

    async def release_state(self, state_id: str, reason: str) -> None:
        if state_id in self._states:
            self._states[state_id].is_active = False
            self._states[state_id].released_reason = reason
            self._save_persisted()

    async def release_states_by_session(
        self, session_id: str, reason: str = "session_end"
    ) -> int:
        count = 0
        for state in self._states.values():
            if state.session_id == session_id and state.is_active:
                state.is_active = False
                state.released_reason = reason
                count += 1
        if count:
            self._save_persisted()
        return count

    # ==================== 笔记操作（笔记本） ====================

    async def save_note(self, note: Note) -> str:
        if not note.note_id:
            note.note_id = f"note_{uuid.uuid4().hex[:8]}"
        self._notes[note.note_id] = note
        self._save_persisted()
        return note.note_id

    async def get_note(self, note_id: str) -> Optional[Note]:
        return self._notes.get(note_id)

    async def get_notes_for_injection(
        self, user_id: str, top_k: int = 5
    ) -> List[Note]:
        results = [
            n for n in self._notes.values()
            if n.user_id == user_id and not n.is_archived()
        ]
        results.sort(key=lambda n: n.created_at, reverse=True)
        return results[:top_k]
