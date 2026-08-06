"""认知存储端口 —— 定义五维认知的存储和检索接口

MVP 简化：裁掉 merge_triples / resolve_conflict / confirm_triple /
search_triples / update_note_content / create_or_update_note 等高级方法，
保留认知闭环所需的最小接口。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from aion_agent.core.entities.agent_state import AgentState
from aion_agent.core.entities.cognitive_triple import CognitiveTriple, Dimension
from aion_agent.core.entities.note import Note


class ICognitiveRepo(ABC):
    """认知存储接口（大脑皮层 + 临时状态 + 笔记本）"""

    # ===== 三元组操作（大脑皮层） =====

    @abstractmethod
    async def save_triple(self, triple: CognitiveTriple) -> str:
        """保存认知三元组，返回 rel_id"""
        ...

    @abstractmethod
    async def retrieve(
        self,
        user_id: str,
        query: str = "*",
        top_k: int = 5,
        dimensions: Optional[List[Dimension]] = None,
        min_confidence: float = 0.5,
    ) -> List[CognitiveTriple]:
        """RAG 检索认知三元组"""
        ...

    @abstractmethod
    async def get_triple(self, rel_id: str) -> Optional[CognitiveTriple]:
        """根据 ID 获取三元组"""
        ...

    @abstractmethod
    async def update_confidence(self, rel_id: str, confidence: float) -> None:
        """更新置信度（首重效应）"""
        ...

    @abstractmethod
    async def increment_usage(self, rel_id: str) -> None:
        """增加使用次数"""
        ...

    @abstractmethod
    async def delete_triple(self, rel_id: str, soft: bool = True) -> bool:
        """删除三元组（默认软删除）"""
        ...

    @abstractmethod
    async def update_triple(
        self,
        rel_id: str,
        subject: Optional[str] = None,
        predicate: Optional[str] = None,
        object_: Optional[str] = None,
        dimension: Optional[Dimension] = None,
        confidence: Optional[float] = None,
    ) -> Optional[CognitiveTriple]:
        """修改三元组内容，返回修改后的三元组"""
        ...

    @abstractmethod
    async def list_triples_by_dimension(
        self,
        user_id: str,
        dimension: Dimension,
        is_active: bool = True,
    ) -> List[CognitiveTriple]:
        """按维度列出三元组"""
        ...

    # ===== 状态认知操作（AgentState） =====

    @abstractmethod
    async def save_state(self, state: AgentState) -> str:
        """保存状态认知"""
        ...

    @abstractmethod
    async def get_active_states(
        self, user_id: str, session_id: Optional[str] = None
    ) -> List[AgentState]:
        """获取活跃状态"""
        ...

    @abstractmethod
    async def release_state(self, state_id: str, reason: str) -> None:
        """释放状态"""
        ...

    @abstractmethod
    async def release_states_by_session(
        self, session_id: str, reason: str = "session_end"
    ) -> int:
        """释放会话的所有状态"""
        ...

    # ===== 笔记操作（笔记本长文本） =====

    @abstractmethod
    async def save_note(self, note: Note) -> str:
        """保存笔记，返回 note_id"""
        ...

    @abstractmethod
    async def get_note(self, note_id: str) -> Optional[Note]:
        """根据 ID 获取笔记"""
        ...

    @abstractmethod
    async def get_notes_for_injection(
        self, user_id: str, top_k: int = 5
    ) -> List[Note]:
        """获取待注入的笔记"""
        ...