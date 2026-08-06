"""认知块处理：解析 JSON → 分流 → 持久化 → 返回结果摘要

MVP 简化：zero_code 的 handle_cognition_block（事件总线 + 异步生成器）被简化为
返回结果摘要的 process_cognition_block，便于直接嵌入 ReAct 循环或演示脚本。
"""

from __future__ import annotations

import ast
import json
import logging
import re
from typing import Any, Dict, List, Optional

from aion_agent.core.ports.i_cognitive_repo import ICognitiveRepo
from aion_agent.pipeline.cognition_pipeline import CognitionPipeline

logger = logging.getLogger(__name__)


async def process_cognition_block(
    user_id: str,
    block: str,
    cognitive_repo: ICognitiveRepo,
    pipeline: Optional[CognitionPipeline] = None,
) -> Dict[str, Any]:
    """处理单个认知块，返回结果摘要

    Args:
        user_id: 用户标识
        block: 认知块内容（可含 <!--COGNITION--> 标记）
        cognitive_repo: 认知存储
        pipeline: 可选，复用现有管道实例（默认新建）

    Returns:
        {"triples": n, "states": n, "notes": n, "skipped": n,
         "store_success": bool, "cards": {"triples": [...], ...}}
    """
    empty = {
        "triples": 0, "states": 0, "notes": 0, "skipped": 0,
        "store_success": True,
        "cards": {"triples": [], "states": [], "notes": []},
    }

    # 去除标记
    block = re.sub(
        r"<!--COGNITION_START-->|<!--COGNITION_END-->",
        "", block, flags=re.IGNORECASE,
    ).strip()
    if not block:
        return empty

    pipe = pipeline or CognitionPipeline(cognitive_repo=cognitive_repo)

    items = pipe.parse_json(block)
    if not items:
        # JSON 整体解析失败 → 兜底扫描 JSON 对象块
        extracted = extract_multiple_objects(block)
        if extracted is None:
            logger.warning(f"认知解析失败，已丢弃: {block[:100]}")
            return empty
        items = extracted

    result = pipe.split_dimension(items, user_id)
    result.store_success = await pipe.store(
        result.triples, result.states, result.notes
    )

    return {
        "triples": len(result.triples),
        "states": len(result.states),
        "notes": len(result.notes),
        "skipped": result.skipped,
        "store_success": result.store_success,
        "cards": {
            "triples": result.triples,
            "states": result.states,
            "notes": result.notes,
        },
    }


def _scan_json_objects(text: str) -> List[Dict[str, Any]]:
    """扫描文本中的 {..} 块，用大括号深度匹配提取每个顶层对象

    处理：对象字符串内的 { } 引号转义、嵌套对象、多个对象夹杂普通文本。
    """
    objects: List[Dict[str, Any]] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        # 匹配一个完整的 {...} 块
        depth = 0
        in_str = False
        escape = False
        j = i
        while j < n:
            ch = text[j]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        break
            j += 1
        if j < n and depth == 0:
            block = text[i:j + 1]
            try:
                obj = json.loads(block)
                if isinstance(obj, dict):
                    objects.append(obj)
            except Exception:
                pass
            i = j + 1
        else:
            i += 1
    return objects


def extract_multiple_objects(text: str) -> Optional[List[Dict[str, Any]]]:
    """兜底解析：当 JSON 整体解析失败时，从混合文本中提取 JSON 对象块

    支持场景（LLM 输出认知块时常见）：
    - 纯 JSON 对象 / 数组（ast.literal_eval 直接解析）
    - 多个 JSON 对象夹杂在普通文本中（正则扫描 {..} 块）
    - 多行 JSON 流（每行一个对象）
    - 带 <!--COGNITION_START/END--> 标记的文本

    一个对象都提取不出来时返回 None（与调用方约定：None 视为解析失败）。
    """
    try:
        text = text.strip()
        # 移除可能的注释
        text = re.sub(r"//[^\n]*", "", text)

        # 1) 先尝试直接解析纯 JSON / Python 字面量
        try:
            result = ast.literal_eval(text)
            if isinstance(result, dict):
                return [result]
            if isinstance(result, list):
                flat: List[Dict[str, Any]] = []

                def _flatten(items):
                    for it in items:
                        if isinstance(it, dict):
                            flat.append(it)
                        elif isinstance(it, list):
                            _flatten(it)

                _flatten(result)
                if flat:
                    return flat
                return result
        except Exception:
            pass

        # 2) 兜底：扫描文本中的所有 {...} JSON 对象块
        objects = _scan_json_objects(text)
        if objects:
            return objects
        return None
    except Exception:
        return None