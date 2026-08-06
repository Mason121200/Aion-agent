"""五维认知三元组实体 —— 大脑皮层长期记忆的最小单元

所有字段沿用 zero_code 语义：subject/predicate/object 使用用户原文语言。
MVP 简化：pydantic → dataclass，置信度校验移入 __post_init__。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Dimension(str, Enum):
    """五维认知分类索引"""

    USER = "user"    # 用户画像、身份、偏好
    SELF = "self"    # Agent 自身认知、能力清单
    ENV = "env"      # 环境认知（设备、时间、工作区）
    WORLD = "world"  # 世界认知（规则、常识、行业共识）
    STATE = "state"  # 状态认知（情绪、进度、趋势）


@dataclass
class CognitiveTriple:
    """五维认知三元组 —— 统一存储结构

    三元组全部使用用户原文语言，不做翻译或规范化。
    例如用户说中文 → subject/predicate/object 都是中文。
    """

    subject: str = ""
    predicate: str = ""
    object: str = ""
    dimension: Dimension = Dimension.WORLD
    user_id: str = "default"
    confidence: float = 0.6
    usage_count: int = 0
    is_active: bool = True
    is_confirmed_by_user: bool = False
    source: Optional[str] = None
    rel_id: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None  # 过期时间，None=永久有效

    def __post_init__(self) -> None:
        if isinstance(self.dimension, str):
            self.dimension = Dimension(self.dimension)
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"confidence 必须在 0.0-1.0 之间，收到 {self.confidence}"
            )

    def is_expired(self) -> bool:
        """判断认知是否已过期"""
        if self.expires_at is None:
            return False
        return datetime.now() > self.expires_at

    def to_natural_language(self) -> str:
        """转换为自然语言描述（用于 RAG 检索注入）

        三元组全部使用用户语言，直接按「主语+谓语+宾语」拼接即可。
        """
        return f"{self.subject}{self.predicate}{self.object}。"