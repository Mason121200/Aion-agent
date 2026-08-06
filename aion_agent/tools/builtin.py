"""内置工具 —— MVP 最小工具集

三个工具覆盖 ReAct 循环最典型的「环境反馈」场景：
- get_current_time：查询当前时间（环境反馈）
- calculator：数学计算（AST 安全求值，禁止 eval/exec）
- read_file：读取本地文本文件（带大小上限）

handler 约定：接收 args: dict，返回任意可 JSON 化的值；
失败时抛出异常，由 ToolExecutor 统一收敛为 ToolResult(success=False)。
"""

from __future__ import annotations

import ast
import operator
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from aion_agent.core.ports.i_tool_registry import IToolRegistry

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


# ==================== 工具实现 ====================

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
    # 防御：禁止明显危险的调用
    for token in ("__", "import", "exec", "eval", "open", "lambda"):
        if token in expression:
            raise ValueError(f"表达式中包含禁止内容: {token}")
    tree = ast.parse(expression, mode="eval")
    value = _safe_eval_ast(tree)
    return {"content": f"{expression} = {value}"}


def _read_file(args: Dict[str, Any]) -> dict:
    path = str(args.get("path", "")).strip()
    if not path:
        raise ValueError("缺少参数 path")
    p = Path(path)
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


# ==================== OpenAI 格式 schema ====================

_BUILTIN_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "获取当前日期和时间（环境反馈），回答与时间相关的问题时使用",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "执行数学计算，返回计算结果。支持 + - * / // % ** 和括号",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "数学表达式，例如 (12 + 8) * 3 / 2",
                    },
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取本地文本文件的内容（UTF-8，大小上限 200KB）",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径，例如 C:/notes/readme.txt",
                    },
                },
                "required": ["path"],
            },
        },
    },
]


def register_builtin_tools(registry: IToolRegistry) -> None:
    """把内置工具注册进注册表（handler 与 schema 成对注册）"""
    handlers = {
        "get_current_time": _get_current_time,
        "calculator": _calculator,
        "read_file": _read_file,
    }
    for tool in _BUILTIN_TOOLS:
        name = tool["function"]["name"]
        registry.register(name, handlers[name], schema=tool)