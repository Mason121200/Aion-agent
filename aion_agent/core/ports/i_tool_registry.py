"""工具注册端口 —— 定义工具的管理和发现接口"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional


class IToolRegistry(ABC):
    """工具注册中心接口"""

    @abstractmethod
    def register(
        self,
        name: str,
        func: Callable,
        schema: Optional[Dict[str, Any]] = None,
        permission: str = "auto",
        level: str = "skill",
    ) -> None:
        """注册工具（同步操作，在启动时调用）"""
        ...

    @abstractmethod
    def get(self, name: str) -> Optional[Dict[str, Any]]:
        """获取工具定义（含函数引用和 schema）"""
        ...

    @abstractmethod
    def list_tools(self) -> List[Dict[str, Any]]:
        """列出所有已注册工具的 OpenAI 格式 schema"""
        ...

    @abstractmethod
    def unregister(self, name: str) -> bool:
        """取消注册工具"""
        ...

    @abstractmethod
    def is_registered(self, name: str) -> bool:
        """检查工具是否已注册"""
        ...