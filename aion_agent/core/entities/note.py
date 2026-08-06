"""Note 实体 —— 笔记本记忆（长文本、任务日志、状态归档）"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional


class NoteType(str, Enum):
    """笔记类型"""
    TASK_LOG = "task_log"       # 任务追踪记录
    STATE_LOG = "state_log"     # 状态变更日志（AgentState 释放后归档）
    LONG_TEXT = "long_text"     # 长文本内容存档
    SUMMARY = "summary"         # 会话摘要
    TASK = "task"               # 任务笔记
    WORKER = "worker"           # 执行笔记
    KNOWLEDGE = "knowledge"     # 知识笔记


class NoteStatus(str, Enum):
    """笔记状态"""
    ACTIVE = "active"
    COMPLETED = "completed"
    ARCHIVED = "archived"
    PENDING = "pending"


@dataclass
class Note:
    """笔记实体 —— 双层记忆中的「笔记本」层

    与 CognitiveTriple（大脑皮层）的区别：
    - Note 存储长文本、任务记录、状态日志等非结构化内容
    - Note 有 archived_at，完成后可归档而非永久保留在活跃记忆中
    - Note 不进入 RAG 向量检索的主索引，通过标签和时间查询
    """

    note_id: Optional[str] = None
    user_id: str = "default"
    note_type: NoteType = NoteType.LONG_TEXT
    title: str = ""
    content: str = ""
    tags: List[str] = field(default_factory=list)
    related_session_id: Optional[str] = None
    summary: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: Optional[datetime] = None
    archived_at: Optional[datetime] = None
    status: str = NoteStatus.ACTIVE.value

    def __post_init__(self) -> None:
        if isinstance(self.note_type, str):
            self.note_type = NoteType(self.note_type)

    def is_archived(self) -> bool:
        """是否已归档"""
        return self.archived_at is not None or self.status == NoteStatus.ARCHIVED.value

    def archive(self) -> None:
        """归档笔记"""
        self.archived_at = datetime.now()
        self.status = NoteStatus.ARCHIVED.value

    def touch(self) -> None:
        """更新 updated_at"""
        self.updated_at = datetime.now()

    def generate_summary(self, max_length: int = 200) -> str:
        """生成自动摘要（规则版）

        1. 优先提取 ## 摘要 段落
        2. ≤200 字符取全文
        3. >200 字符取前 200 字符 + ...
        """
        content = self.content or ""

        summary_match = re.search(
            r"##\s*摘要\s*\n+(.*?)(?:\n##|\Z)",
            content, re.DOTALL
        )
        if summary_match:
            extracted = summary_match.group(1).strip()
            self.summary = (
                extracted
                if len(extracted) <= max_length
                else extracted[:max_length] + "..."
            )
            return self.summary

        if len(content) <= max_length:
            self.summary = content
            return content

        self.summary = content[:max_length] + "..."
        return self.summary

    def to_summary(self, max_length: int = 120) -> str:
        """生成短摘要（用于注入时的简要展示）"""
        content_preview = self.content[:max_length]
        if len(self.content) > max_length:
            content_preview += "..."
        return (
            f'"{self.title}" '
            f'({self.created_at.strftime("%m-%d")}): '
            f"{content_preview}"
        )