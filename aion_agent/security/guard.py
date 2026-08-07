"""安全守卫 —— PathGuard（敏感路径黑名单）+ CommandWhitelist（命令白名单）

给通用工具（文件操作 / shell / 网络抓取）提供统一的安全边界：
- PathGuard.check_read：只读操作，拦截敏感路径（密钥、系统目录、.git 等）
- PathGuard.check_write：写操作，在只读拦截之上，可额外限定允许根目录
- CommandWhitelist：只允许白名单内的只读命令，并拦截 shell 元字符与危险模式
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List, Optional

# 敏感路径片段（出现在路径任意层级即拦截）
_BLOCKED_PATH_PARTS = {
    ".env", ".pem", ".key", ".p12", ".pfx", "id_rsa", "id_ed25519",
    ".git", ".svn", ".hg", "__pycache__", "node_modules", "venv", ".venv",
    "System Volume Information", "$RECYCLE.BIN", "secrets", "credentials",
}

# 系统目录前缀（绝对路径开头即拦截，避免读写系统关键区）
_BLOCKED_ROOT_PREFIXES = (
    "c:/windows", "c:/program files", "c:/program files (x86)",
    "/etc", "/usr", "/bin", "/sbin", "/lib", "/var", "/proc", "/sys",
    "/boot", "/dev", "/root", "/private/etc",
)

# 只读安全命令白名单（前缀匹配）
_ALLOWED_COMMANDS = (
    "pwd", "ls", "dir", "echo", "whoami", "hostname", "date",
)

# shell 危险模式：元字符 / 重定向 / 命令链接 / 递归删除
_DANGER_PATTERNS = [
    re.compile(r"[|;&`]"),
    re.compile(r"[<>]"),
    re.compile(r"\$\("),
    re.compile(r"rm\b.*-r"),
    re.compile(r"del\b.*/s"),
    re.compile(r"format\b"),
    re.compile(r"shutdown\b"),
    re.compile(r"mkfs\b"),
    re.compile(r"--no-preserve-root"),
]


class PathGuard:
    """路径守卫：解析 + 敏感拦截 + 可选写根限制"""

    def __init__(
        self,
        allowed_roots: Optional[Iterable[str]] = None,
        blocked_parts: Optional[Iterable[str]] = None,
    ) -> None:
        self._allowed_roots = [
            Path(p).resolve() for p in (allowed_roots or []) if str(p).strip()
        ]
        self._blocked_parts = set(blocked_parts or _BLOCKED_PATH_PARTS)

    @staticmethod
    def resolve(path: str) -> Path:
        return Path(str(path or "")).expanduser().resolve()

    def check_read(self, path: str) -> Path:
        """只读路径检查：拦截敏感路径"""
        p = self.resolve(path)
        self._reject(p)
        return p

    def check_write(self, path: str) -> Path:
        """写路径检查：敏感拦截 + 允许根限制"""
        p = self.resolve(path)
        self._reject(p)
        if self._allowed_roots and not any(
            self._is_within(p, root) for root in self._allowed_roots
        ):
            raise PermissionError(f"路径不在允许的根目录内: {p}")
        return p

    def _reject(self, p: Path) -> None:
        for part in p.parts:
            if part in self._blocked_parts:
                raise PermissionError(f"敏感路径被拦截: {p}")
        # Windows resolve() 返回反斜杠路径，先归一化为 / 再匹配前缀
        lowered = str(p).lower().replace("\\", "/")
        for prefix in _BLOCKED_ROOT_PREFIXES:
            if lowered.startswith(prefix):
                raise PermissionError(f"系统目录被拦截: {p}")

    @staticmethod
    def _is_within(p: Path, root: Path) -> bool:
        try:
            p.relative_to(root)
            return True
        except ValueError:
            return False


class CommandWhitelist:
    """命令白名单：只允许只读命令，危险模式一律拒绝"""

    def __init__(
        self,
        allowed: Optional[Iterable[str]] = None,
        danger_patterns: Optional[List[re.Pattern]] = None,
    ) -> None:
        self._allowed = tuple(
            sorted(set(allowed or _ALLOWED_COMMANDS), key=len, reverse=True)
        )
        self._danger = list(danger_patterns or _DANGER_PATTERNS)

    def check(self, command: str) -> str:
        cmd = str(command or "").strip()
        if not cmd:
            raise ValueError("命令为空")
        first = cmd.split()[0].lower() if cmd.split() else ""
        if first not in self._allowed:
            raise PermissionError(f"命令不在白名单内: {first or '(空)'}")
        for pattern in self._danger:
            if pattern.search(cmd):
                raise PermissionError(f"命令包含危险模式: {cmd[:80]}")
        return cmd
