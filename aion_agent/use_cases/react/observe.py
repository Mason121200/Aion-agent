"""Observe 阶段 —— 摘要工具结果 + 错误分类

移植自 zero_code 的 src/use_cases/react/observe.py（原样保留语义）。
"""

from __future__ import annotations

from typing import Any, Dict

from aion_agent.core.ports.i_tool_executor import ToolResult

# ===== 错误类型映射 =====
ERROR_TYPE_MAP: Dict[str, Dict[str, str]] = {
    "FileNotFoundError": {"suggested": "检查文件路径是否正确，使用 read_file 前先确认文件存在"},
    "PermissionError": {"suggested": "检查文件权限，或换一个可读路径"},
    "SyntaxError": {"suggested": "检查输入语法是否正确"},
    "TimeoutError": {"suggested": "操作超时，尝试简化操作或增加超时时间"},
    "ConnectionError": {"suggested": "检查网络连接，稍后重试"},
    "COMMAND_BLOCKED": {"suggested": "操作被安全策略拦截，请使用其他方式"},
    "ValueError": {"suggested": "参数不合法，检查传入的参数类型与取值范围"},
}


def classify_error(error_msg: str) -> str:
    """分类错误信息（先精确匹配类型名，再关键词模糊匹配）"""
    error_lower = error_msg.lower()
    for pattern in ERROR_TYPE_MAP:
        if pattern.lower() in error_lower:
            return pattern

    keywords = {
        "FileNotFoundError": ["file not found", "no such file", "找不到文件", "文件不存在"],
        "PermissionError": ["permission denied", "access denied", "权限被拒绝", "权限不足"],
        "SyntaxError": ["syntax error", "invalid syntax", "语法错误"],
        "TimeoutError": ["timeout", "timed out", "超时"],
        "ConnectionError": ["connection", "network", "网络"],
        "COMMAND_BLOCKED": ["blocked", "被拦截", "安全策略"],
        "ValueError": ["缺少参数", "invalid", "不合法", "unsupported", "参数"],
    }
    for error_type, terms in keywords.items():
        for term in terms:
            if term in error_lower:
                return error_type
    return "UNKNOWN"


def observe(result: ToolResult) -> Dict[str, Any]:
    """Observe 阶段：摘要工具结果 + 错误分类

    Returns:
        {
            "content": str,      # 摘要后的内容（供 LLM 下一轮使用）
            "error_type": str,   # 错误类型（如有）
            "suggestion": str,   # 建议（如有）
        }
    """
    if result.success:
        data = result.data
        if isinstance(data, dict):
            content = (
                data.get("content")
                or data.get("message")
                or data.get("data")
                or str(data)
            )
        elif isinstance(data, str):
            content = data
        elif data is None:
            content = "✅ 执行成功（无输出）"
        else:
            content = str(data)

        if len(content) > 2000:
            content = content[:2000] + f"\n... (截断，共 {len(content)} 字符)"

        return {
            "content": content,
            "error_type": None,
            "suggestion": None,
        }

    error_msg = result.error or "未知错误"
    error_type = classify_error(error_msg)
    suggestion = ERROR_TYPE_MAP.get(
        error_type, {}
    ).get("suggested", "请检查输入或尝试其他方法")

    return {
        "content": f"❌ {error_msg}\n💡 建议: {suggestion}",
        "error_type": error_type,
        "suggestion": suggestion,
    }