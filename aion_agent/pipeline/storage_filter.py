"""过滤器4：StorageFilter — 认知持久化存储

管道-过滤器模式的末端过滤器（有副作用）。
将分流后的认知条目（dict）转换为实体对象，写入认知存储。

MVP 简化：认知、状态、笔记共用一个 ICognitiveRepo 实例。
"""

import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

from aion_agent.core.entities.agent_state import AgentState
from aion_agent.core.entities.cognitive_triple import CognitiveTriple, Dimension
from aion_agent.core.entities.note import Note, NoteType
from aion_agent.core.ports.i_cognitive_repo import ICognitiveRepo

logger = logging.getLogger(__name__)


class StorageFilter:
    """存储过滤器 — 将分流后的认知写入持久化存储"""

    def __init__(self, cognitive_repo: Optional[ICognitiveRepo] = None):
        self._cognitive_repo = cognitive_repo

    # ==================== 转换辅助 ====================

    @staticmethod
    def _to_triple(td: Dict[str, Any]) -> Optional[CognitiveTriple]:
        """将 triple 字典转换为 CognitiveTriple 实体"""
        try:
            subject = str(td.get("subject", "")).strip()
            predicate = str(td.get("predicate", "")).strip()
            obj = str(td.get("object", "")).strip()
            if not subject or not predicate or not obj:
                logger.warning(f"StorageFilter: triple 字段不完整: {td}")
                return None

            dimension_raw = td.get("dimension", "world")
            try:
                dimension = (
                    dimension_raw
                    if isinstance(dimension_raw, Dimension)
                    else Dimension(str(dimension_raw))
                )
            except ValueError:
                logger.warning(
                    f"StorageFilter: 未知 dimension={dimension_raw}, 回退 world"
                )
                dimension = Dimension.WORLD

            expires_at = None
            expires_raw = td.get("expires_at")
            if expires_raw:
                try:
                    expires_at = (
                        expires_raw
                        if isinstance(expires_raw, datetime)
                        else datetime.fromisoformat(str(expires_raw))
                    )
                except (ValueError, TypeError):
                    logger.warning(
                        f"StorageFilter: expires_at 解析失败: {expires_raw}"
                    )

            confidence = td.get("confidence", 0.8)
            try:
                confidence = float(confidence)
            except (ValueError, TypeError):
                confidence = 0.8

            return CognitiveTriple(
                subject=subject,
                predicate=predicate,
                object=obj,
                dimension=dimension,
                user_id=str(td.get("user_id", "")),
                confidence=confidence,
                source=td.get("source"),
                expires_at=expires_at,
            )
        except Exception as e:
            logger.error(f"StorageFilter: triple 转换失败: {e}")
            return None

    @staticmethod
    def _to_state(sd: Dict[str, Any]) -> Optional[AgentState]:
        """将 state 字典转换为 AgentState 实体"""
        try:
            state_name = str(sd.get("state_name", "")).strip()
            if not state_name:
                logger.warning(f"StorageFilter: state 缺 state_name: {sd}")
                return None

            expires_at = None
            expires_raw = sd.get("expires_at")
            if expires_raw:
                try:
                    expires_at = (
                        expires_raw
                        if isinstance(expires_raw, datetime)
                        else datetime.fromisoformat(str(expires_raw))
                    )
                except (ValueError, TypeError):
                    expires_at = None

            return AgentState(
                user_id=str(sd.get("user_id", "")),
                state_type=str(sd.get("state_type", "task")),
                state_name=state_name,
                description=sd.get("description"),
                priority=int(sd.get("priority", 0) or 0),
                expires_at=expires_at,
            )
        except Exception as e:
            logger.error(f"StorageFilter: state 转换失败: {e}")
            return None

    @staticmethod
    def _to_note(nd: Dict[str, Any]) -> Optional[Note]:
        """将 note 字典转换为 Note 实体"""
        try:
            content = str(nd.get("content", "")).strip()
            if not content:
                logger.warning(f"StorageFilter: note 缺 content: {nd}")
                return None

            note_type_raw = nd.get("note_type", "long_text")
            try:
                note_type = (
                    note_type_raw
                    if isinstance(note_type_raw, NoteType)
                    else NoteType(str(note_type_raw))
                )
            except ValueError:
                logger.warning(
                    f"StorageFilter: 未知 note_type={note_type_raw}, 回退 long_text"
                )
                note_type = NoteType.LONG_TEXT

            tags = nd.get("tags", []) or []
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]

            return Note(
                user_id=str(nd.get("user_id", "")),
                note_type=note_type,
                title=str(nd.get("title", "") or "未命名笔记"),
                content=content,
                tags=tags,
            )
        except Exception as e:
            logger.error(f"StorageFilter: note 转换失败: {e}")
            return None

    # ==================== 持久化 ====================

    async def process(
        self,
        triples: List[Dict[str, Any]],
        states: List[Dict[str, Any]],
        notes: List[Dict[str, Any]],
    ) -> bool:
        """持久化分流后的认知数据

        Returns:
            True 表示全部写入成功，False 表示部分失败
        """
        all_success = True

        for td in triples:
            if td is None:
                continue
            try:
                if self._cognitive_repo:
                    triple = self._to_triple(td)
                    if triple is not None:
                        await self._cognitive_repo.save_triple(triple)
                    else:
                        all_success = False
            except Exception as e:
                logger.error(f"三元组保存失败: {e}", exc_info=True)
                all_success = False

        for sd in states:
            if sd is None:
                continue
            try:
                if self._cognitive_repo:
                    state = self._to_state(sd)
                    if state is not None:
                        await self._cognitive_repo.save_state(state)
                    else:
                        all_success = False
            except Exception as e:
                logger.warning(f"状态保存失败: {e}", exc_info=True)
                all_success = False

        for nd in notes:
            if nd is None:
                continue
            try:
                if self._cognitive_repo:
                    note = self._to_note(nd)
                    if note is not None:
                        await self._cognitive_repo.save_note(note)
                    else:
                        all_success = False
            except Exception as e:
                logger.warning(f"笔记保存失败: {e}", exc_info=True)
                all_success = False

        logger.info(
            f"认知持久化: triples={len(triples)}, "
            f"states={len(states)}, notes={len(notes)}, "
            f"all_success={all_success}"
        )
        return all_success