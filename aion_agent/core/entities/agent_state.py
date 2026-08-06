"""状态认知实体 —— 工作记忆（笔记本临时状态）"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass
class AgentState:
    """Agent 状态认知（工作记忆）

    与 CognitiveTriple（大脑皮层长期记忆）的区别：
    - 有生命周期：is_active / released_at / expires_at
    - 不进入 RAG 向量检索的主索引，按状态名与时间查询
    """

    state_id: Optional[str] = None
    user_id: str = "default"
    session_id: Optional[str] = None
    task_id: Optional[str] = None
    state_type: str = "task"      # task | agent | user
    state_name: str = ""          # running | thinking | correcting | completed
    description: Optional[str] = None
    context: Dict[str, Any] = field(default_factory=dict)
    started_at: datetime = field(default_factory=datetime.now)
    last_updated_at: datetime = field(default_factory=datetime.now)
    expires_at: Optional[datetime] = None
    is_active: bool = True
    priority: int = 0             # 排序优先级，越高越靠前
    released_at: Optional[datetime] = None
    released_reason: Optional[str] = None  # completed | cancelled | timeout

    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        return datetime.now() > self.expires_at

    def release(self, reason: str) -> None:
        """释放状态"""
        self.is_active = False
        self.released_at = datetime.now()
        self.released_reason = reason