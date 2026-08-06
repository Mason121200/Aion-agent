"""对话历史存储端口 —— 会话级消息持久化"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from aion_agent.core.entities.message import Message


class IChatRepo(ABC):
    """会话与消息仓库接口（ReAct 循环的上下文来源）"""

    @abstractmethod
    async def create_session(self, user_id: str) -> str:
        """创建会话，返回 session_id"""
        ...

    @abstractmethod
    async def save_message(self, message: Message) -> str:
        """保存一条消息，返回消息 id"""
        ...

    @abstractmethod
    async def get_history(
        self, session_id: str, limit: Optional[int] = None
    ) -> List[Message]:
        """获取会话历史（limit=None 返回全部）"""
        ...

    @abstractmethod
    async def list_sessions(self, user_id: str) -> List[dict]:
        """列出用户的会话元数据"""
        ...

    @abstractmethod
    async def delete_session(self, session_id: str) -> bool:
        """删除会话"""
        ...