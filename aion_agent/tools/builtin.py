"""内置工具 —— 通用基础工具集（T1 固化层，随框架发布）

覆盖 ReAct 循环的「环境反馈」场景：
- get_current_time / calculator / read_file（原有）
- file_write / file_list（PathGuard 守卫的本地文件操作）
- web_fetch（抓取网页文本，限时限量）
- shell（CommandWhitelist 只读命令白名单，默认需用户确认）

handler 约定：接收 args: dict，返回可 JSON 化 dict；
失败抛异常，由 ToolExecutor 统一收敛为 ToolResult(success=False)。
"""

from __future__ import annotations

import ast
import operator
import shlex
import subprocess
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from aion_agent.core.ports.i_tool_registry import IToolRegistry
from aion_agent.security.guard import CommandWhitelist, PathGuard

# 允许的 AST 运算（白名单求值，杜绝 eval）
_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARYOPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_MAX_READ_BYTES = 200_000
_MAX_WRITE_BYTES = 2_000_000
_MAX_LIST_ITEMS = 200
_MAX_FETCH_BYTES = 500_000
_MAX_FETCH_CHARS = 8000
_MAX_SHELL_OUTPUT = 4000
_SHELL_TIMEOUT_SECONDS = 10
_WEB_TIMEOUT_SECONDS = 15


def _safe_eval_ast(node: ast.AST) -> Any:
    """AST 白名单求值：只允许数字常量、四则运算、幂、取模、一元正负"""
    if isinstance(node, ast.Expression):
        return _safe_eval_ast(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](
            _safe_eval_ast(node.left), _safe_eval_ast(node.right)
        )
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARYOPS:
        return _UNARYOPS[type(node.op)](_safe_eval_ast(node.operand))
    raise ValueError(f"不支持的表达式节点: {type(node).__name__}")


# ==================== 基础工具实现 ====================

def _get_current_time(args: Dict[str, Any]) -> dict:
    now = datetime.now()
    return {
        "content": (
            f"当前时间：{now.strftime('%Y年%m月%d日 %H:%M:%S')}"
            f"（{now.strftime('%A')}）"
        ),
    }


def _calculator(args: Dict[str, Any]) -> dict:
    expression = str(args.get("expression", "")).strip()
    if not expression:
        raise ValueError("缺少参数 expression")
    for token in ("__", "import", "exec", "eval", "open", "lambda"):
        if token in expression:
            raise ValueError(f"表达式中包含禁止内容: {token}")
    tree = ast.parse(expression, mode="eval")
    value = _safe_eval_ast(tree)
    return {"content": f"{expression} = {value}"}


# ==================== 通用工具实现（带安全守卫，闭包构造） ====================

def _make_general_handlers(
    guard: PathGuard, whitelist: CommandWhitelist
) -> Dict[str, Any]:
    def _read_file(args: Dict[str, Any]) -> dict:
        path = str(args.get("path", "")).strip()
        if not path:
            raise ValueError("缺少参数 path")
        p = guard.check_read(path)
        if not p.exists():
            raise FileNotFoundError(f"文件不存在: {path}")
        if not p.is_file():
            raise ValueError(f"不是文件: {path}")
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        if size > _MAX_READ_BYTES:
            raise ValueError(
                f"文件过大（{size} 字节，上限 {_MAX_READ_BYTES}），请改用其他方式"
            )
        content = p.read_text(encoding="utf-8", errors="replace")
        if len(content) > 2000:
            content = content[:2000] + f"\n... (截断，共 {len(content)} 字符)"
        return {"content": content, "path": str(p), "size": size}

    def _file_write(args: Dict[str, Any]) -> dict:
        path = str(args.get("path", "")).strip()
        content = str(args.get("content", "") or "")
        append = bool(args.get("append", False))
        if not path:
            raise ValueError("缺少参数 path")
        p = guard.check_write(path)
        existing = p.stat().st_size if p.exists() else 0
        total = existing + len(content.encode("utf-8"))
        if total > _MAX_WRITE_BYTES:
            raise ValueError(f"文件过大（{total} 字节，上限 {_MAX_WRITE_BYTES}）")
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a" if append else "w", encoding="utf-8") as f:
            f.write(content)
        return {
            "content": f"已写入 {p}（{total} 字节）",
            "path": str(p),
            "size": total,
            "append": append,
        }

    def _file_list(args: Dict[str, Any]) -> dict:
        path = str(args.get("path", ".")).strip()
        p = guard.check_read(path)
        if not p.exists():
            raise FileNotFoundError(f"目录不存在: {path}")
        if not p.is_dir():
            raise ValueError(f"不是目录: {path}")
        entries: List[dict] = []
        children = sorted(p.iterdir(), key=lambda c: c.name.lower())
        for child in children[:_MAX_LIST_ITEMS]:
            try:
                if child.is_dir():
                    entries.append({"name": child.name, "type": "dir"})
                else:
                    entries.append(
                        {"name": child.name, "type": "file", "size": child.stat().st_size}
                    )
            except OSError:
                continue
        lines = [f"目录 {p}（{len(entries)} 项）："]
        for e in entries:
            if e["type"] == "dir":
                lines.append(f"  📁 {e['name']}/")
            else:
                lines.append(f"  📄 {e['name']}（{e['size']} B）")
        return {"content": "\n".join(lines), "path": str(p), "entries": entries}

    def _web_fetch(args: Dict[str, Any]) -> dict:
        url = str(args.get("url", "")).strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError("仅支持 http/https URL")
        req = urllib.request.Request(
            url, headers={"User-Agent": "AionAgent/1.0"}
        )
        with urllib.request.urlopen(req, timeout=_WEB_TIMEOUT_SECONDS) as resp:
            raw = resp.read(_MAX_FETCH_BYTES + 1)
        text = raw.decode("utf-8", errors="replace")
        text = " ".join(text.split())
        if len(text) > _MAX_FETCH_CHARS:
            text = text[:_MAX_FETCH_CHARS] + "...（截断）"
        return {
            "content": text or "（页面无文本内容）",
            "url": url,
            "chars": len(text),
        }

    def _shell(args: Dict[str, Any]) -> dict:
        command = str(args.get("command", "")).strip()
        if not command:
            raise ValueError("缺少参数 command")
        whitelist.check(command)  # 白名单 + 危险模式拦截（抛 PermissionError）
        proc = subprocess.run(
            shlex.split(command),
            capture_output=True,
            text=True,
            timeout=_SHELL_TIMEOUT_SECONDS,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        if len(output) > _MAX_SHELL_OUTPUT:
            output = output[:_MAX_SHELL_OUTPUT] + "\n...（截断）"
        if proc.returncode != 0:
            raise RuntimeError(f"命令执行失败（exit {proc.returncode}）：{output[:500]}")
        return {"content": output or "（无输出）", "exit_code": proc.returncode}

    return {
        "read_file": _read_file,
        "file_write": _file_write,
        "file_list": _file_list,
        "web_fetch": _web_fetch,
        "shell": _shell,
    }


# ==================== OpenAI 格式 schema ====================

def _tool(name: str, description: str, properties: Dict[str, Any], required: List[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


_BUILTIN_TOOLS = [
    _tool(
        "get_current_time",
        "获取当前日期和时间（环境反馈），回答与时间相关的问题时使用",
        {},
        [],
    ),
    _tool(
        "calculator",
        "执行数学计算，返回计算结果。支持 + - * / // % ** 和括号",
        {
            "expression": {
                "type": "string",
                "description": "数学表达式，例如 (12 + 8) * 3 / 2",
            },
        },
        ["expression"],
    ),
    _tool(
        "read_file",
        "读取本地文本文件的内容（UTF-8，大小上限 200KB，敏感路径自动拦截）",
        {
            "path": {"type": "string", "description": "文件路径，例如 C:/notes/readme.txt"},
        },
        ["path"],
    ),
    _tool(
        "file_write",
        "把文本内容写入本地文件（UTF-8，大小上限 2MB；敏感路径自动拦截）。需要创建新文件或更新笔记时使用",
        {
            "path": {"type": "string", "description": "目标文件路径"},
            "content": {"type": "string", "description": "要写入的文本内容"},
            "append": {"type": "boolean", "description": "是否追加（默认覆盖）"},
        },
        ["path", "content"],
    ),
    _tool(
        "file_list",
        "列出本地目录内容（文件/子目录及大小，上限 200 项，敏感路径自动拦截）",
        {
            "path": {"type": "string", "description": "目录路径，默认当前目录"},
        },
        [],
    ),
    _tool(
        "web_fetch",
        "抓取一个网页的文本内容（仅 http/https，限时 15 秒、限量 500KB），用于获取公开网页信息",
        {
            "url": {"type": "string", "description": "网页 URL，例如 https://example.com/article"},
        },
        ["url"],
    ),
    _tool(
        "shell",
        "执行本地命令（仅限只读白名单：pwd/ls/dir/echo/whoami/hostname/date，危险模式自动拦截）。"
        "该工具需要用户确认后才可执行",
        {
            "command": {"type": "string", "description": "要执行的命令，例如 ls -la"},
        },
        ["command"],
    ),
]


def register_builtin_tools(
    registry: IToolRegistry,
    *,
    level: str = "builtin",
    allowed_roots: Optional[List[str]] = None,
    shell_permission: str = "confirm",
) -> None:
    """把内置通用工具注册进注册表（T1 固化层，实现不可改、权限可调）"""
    guard = PathGuard(allowed_roots=allowed_roots)
    whitelist = CommandWhitelist()
    handlers = {
        "get_current_time": _get_current_time,
        "calculator": _calculator,
    }
    handlers.update(_make_general_handlers(guard, whitelist))
    for tool in _BUILTIN_TOOLS:
        name = tool["function"]["name"]
        permission = shell_permission if name == "shell" else "auto"
        registry.register(name, handlers[name], schema=tool, permission=permission, level=level)
