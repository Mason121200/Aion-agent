"""认知工具集 —— 主动回忆 / 认知写入 / 认知修正（移植自 zero_code）

9 个工具让 ReAct 循环可以像「操作大脑皮层」一样：
- 主动回忆：search_cognition / search_by_relation / search_entity / search_notes
- 认知写入：create_cognition
- 认知修正：update_cognition / delete_cognition / merge_cognition / confirm_cognition

实现约定：
- handler 为同步函数（ToolExecutor 在后台线程调用），内部用 asyncio.run 调仓库异步方法
- 所有修改操作（update/delete/merge/confirm）自动写入仓库的错题本（correction_log）
- 返回统一 JSON 可序列化 dict，content 字段为给 LLM 阅读的自然语言结果
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from aion_agent.core.entities.cognitive_triple import CognitiveTriple, Dimension
from aion_agent.core.entities.note import NoteType
from aion_agent.core.ports.i_cognitive_repo import ICognitiveRepo
from aion_agent.core.ports.i_tool_registry import IToolRegistry

logger = logging.getLogger(__name__)

_DIM_VALUES = ["user", "self", "env", "world", "state"]
_NOTE_TYPES = ["task_log", "state_log", "long_text", "summary", "task", "worker", "knowledge"]


# ==================== 序列化辅助 ====================

def _triple_to_dict(t: CognitiveTriple) -> Dict[str, Any]:
    return {
        "rel_id": t.rel_id,
        "subject": t.subject,
        "predicate": t.predicate,
        "object": t.object,
        "dimension": t.dimension.value,
        "confidence": round(t.confidence, 3),
        "usage_count": t.usage_count,
        "is_confirmed_by_user": t.is_confirmed_by_user,
    }


def _triples_content(triples: List[CognitiveTriple], limit: int = 50) -> str:
    if not triples:
        return "（未找到匹配的三元组）"
    lines = []
    for t in triples[:limit]:
        flags = []
        if t.is_confirmed_by_user:
            flags.append("用户已确认")
        if flags:
            flags_text = f"（{'、'.join(flags)}）"
        else:
            flags_text = ""
        lines.append(
            f"- [{t.dimension.value}] {t.subject}{t.predicate}{t.object} "
            f"(置信度 {t.confidence:.0%}, 使用 {t.usage_count} 次){flags_text}"
            f" [rel_id={t.rel_id}]"
        )
    if len(triples) > limit:
        lines.append(f"... 等共 {len(triples)} 条")
    return "\n".join(lines)


def _notes_content(notes, limit: int = 10) -> str:
    if not notes:
        return "（未找到匹配的笔记）"
    lines = []
    for n in notes[:limit]:
        preview = (n.content or "")[:120].replace("\n", " ")
        lines.append(
            f"- [{n.note_type.value}] {n.title or '未命名'}: {preview}"
            f" [note_id={n.note_id}]"
        )
    return "\n".join(lines)


def _parse_dimension(value: Any) -> Optional[Dimension]:
    if value is None or str(value).strip() == "":
        return None
    try:
        return Dimension(str(value))
    except ValueError:
        raise ValueError(
            f"无效维度: {value}（可选 {'/'.join(_DIM_VALUES)}）"
        )


def _parse_time_range(value: Any) -> Optional[datetime]:
    """解析时间范围（如 7d/30d/90d/24h/all）为截止时间"""
    if not value or str(value).lower() == "all":
        return None
    s = str(value).lower()
    try:
        num = int("".join(c for c in s if c.isdigit()))
        if "d" in s:
            return datetime.now() - timedelta(days=num)
        if "h" in s:
            return datetime.now() - timedelta(hours=num)
    except (ValueError, TypeError):
        pass
    return None


# ==================== 工具工厂 ====================

def _make_handlers(repo: ICognitiveRepo, user_id: str) -> Dict[str, Callable]:
    """基于仓库与当前用户构建 9 个认知工具 handler"""

    def _search_cognition(args: Dict[str, Any]) -> dict:
        query = str(args.get("query", "")).strip()
        if not query:
            raise ValueError("缺少参数 query")
        top_k = min(int(args.get("top_k") or 50), 100)
        dimension = _parse_dimension(args.get("dimension"))
        min_conf = float(args.get("min_confidence") or 0.5)
        include_notes = bool(args.get("include_notes", True))

        triples = asyncio.run(repo.search_triples(
            user_id=user_id,
            query=query,
            is_active=True,
            dimension=dimension,
        ))
        triples = [t for t in triples if t.confidence >= min_conf][:top_k]
        out: Dict[str, Any] = {
            "content": _triples_content(triples),
            "triples": [_triple_to_dict(t) for t in triples],
        }
        if include_notes:
            notes = asyncio.run(repo.search_notes(
                user_id=user_id, query=query, top_k=top_k,
            ))
            out["content"] += "\n\n【笔记】\n" + _notes_content(notes)
            out["notes"] = [
                {"note_id": n.note_id, "title": n.title, "content": n.content[:300]}
                for n in notes
            ]
        return out

    def _search_by_relation(args: Dict[str, Any]) -> dict:
        relation = str(args.get("relation", "")).strip()
        if not relation:
            raise ValueError("缺少参数 relation")
        top_k = min(int(args.get("top_k") or 20), 50)
        dimension = _parse_dimension(args.get("dimension"))
        triples = asyncio.run(repo.search_triples(
            user_id=user_id, query=relation, is_active=True, dimension=dimension,
        ))
        # 关系检索：以谓词匹配为准
        matched = [
            t for t in triples
            if t.predicate == relation
        ][:top_k]
        return {
            "content": _triples_content(matched),
            "triples": [_triple_to_dict(t) for t in matched],
        }

    def _search_entity(args: Dict[str, Any]) -> dict:
        entity = str(args.get("entity", "")).strip()
        if not entity:
            raise ValueError("缺少参数 entity")
        top_k = min(int(args.get("top_k") or 20), 50)
        dimension = _parse_dimension(args.get("dimension"))
        triples = asyncio.run(repo.search_triples(
            user_id=user_id, query=entity, is_active=True, dimension=dimension,
        ))
        # 实体检索：以主语或宾语为准
        matched = [
            t for t in triples
            if entity in t.subject or entity in t.object
        ][:top_k]
        return {
            "content": _triples_content(matched),
            "triples": [_triple_to_dict(t) for t in matched],
        }

    def _search_notes(args: Dict[str, Any]) -> dict:
        query = str(args.get("query", "") or "")
        top_k = min(int(args.get("top_k") or 5), 50)
        note_type = str(args.get("note_type") or "") or None
        if note_type and note_type not in _NOTE_TYPES:
            raise ValueError(
                f"无效 note_type: {note_type}（可选 {'/'.join(_NOTE_TYPES)}）"
            )
        include_archived = bool(args.get("include_archived", True))
        time_range = _parse_time_range(args.get("time_range"))
        notes = asyncio.run(repo.search_notes(
            user_id=user_id,
            query=query,
            top_k=top_k,
            note_type=note_type,
            include_archived=include_archived,
            time_range=time_range.isoformat() if time_range else None,
        ))
        return {
            "content": _notes_content(notes),
            "notes": [
                {"note_id": n.note_id, "title": n.title, "content": n.content[:300]}
                for n in notes
            ],
        }

    def _create_cognition(args: Dict[str, Any]) -> dict:
        subject = str(args.get("subject", "")).strip()
        predicate = str(args.get("predicate", "")).strip()
        obj = str(args.get("object", "")).strip()
        if not subject or not predicate or not obj:
            raise ValueError("subject/predicate/object 均不能为空")
        dimension = _parse_dimension(args.get("dimension") or "world") or Dimension.WORLD
        try:
            confidence = float(args.get("confidence") or 0.7)
        except (TypeError, ValueError):
            raise ValueError("confidence 必须是 0-1 之间的数字")
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence 必须在 0-1 之间")
        triple = CognitiveTriple(
            subject=subject,
            predicate=predicate,
            object=obj,
            dimension=dimension,
            user_id=user_id,
            confidence=confidence,
            source="tool:create_cognition",
        )
        rel_id = asyncio.run(repo.save_triple(triple))
        return {
            "content": f"已创建认知三元组 [rel_id={rel_id}]",
            "rel_id": rel_id,
        }

    def _update_cognition(args: Dict[str, Any]) -> dict:
        rel_id = str(args.get("rel_id", "")).strip()
        if not rel_id:
            raise ValueError("缺少参数 rel_id")
        dimension = _parse_dimension(args.get("dimension"))
        confidence = None
        if args.get("confidence") is not None:
            try:
                confidence = float(args["confidence"])
            except (TypeError, ValueError):
                raise ValueError("confidence 必须是数字")
        updated = asyncio.run(repo.update_triple(
            rel_id=rel_id,
            subject=str(args["subject"]) if args.get("subject") else None,
            predicate=str(args["predicate"]) if args.get("predicate") else None,
            object_=str(args["object"]) if args.get("object") else None,
            dimension=dimension,
            confidence=confidence,
        ))
        if updated is None:
            raise ValueError(f"三元组 {rel_id} 不存在或已删除")
        return {
            "content": f"已更新认知三元组：{_triples_content([updated])}",
            "triple": _triple_to_dict(updated),
        }

    def _delete_cognition(args: Dict[str, Any]) -> dict:
        rel_id = str(args.get("rel_id", "")).strip()
        if not rel_id:
            raise ValueError("缺少参数 rel_id")
        ok = asyncio.run(repo.delete_triple(rel_id, soft=True))
        if not ok:
            raise ValueError(f"三元组 {rel_id} 不存在")
        return {"content": f"已删除认知三元组 [rel_id={rel_id}]（软删除，可审计）"}

    def _merge_cognition(args: Dict[str, Any]) -> dict:
        source_id = str(args.get("source_id", "")).strip()
        target_id = str(args.get("target_id", "")).strip()
        if not source_id or not target_id:
            raise ValueError("source_id/target_id 均不能为空")
        merged = asyncio.run(repo.merge_triples(source_id, target_id))
        if merged is None:
            raise ValueError("合并失败：目标三元组不存在")
        return {
            "content": f"已合并：{source_id} → {target_id}，"
                       f"保留置信度 {merged.confidence:.0%}",
            "triple": _triple_to_dict(merged),
        }

    def _confirm_cognition(args: Dict[str, Any]) -> dict:
        rel_id = str(args.get("rel_id", "")).strip()
        if not rel_id:
            raise ValueError("缺少参数 rel_id")
        confirmed = asyncio.run(repo.confirm_triple(rel_id))
        if confirmed is None:
            raise ValueError(f"三元组 {rel_id} 不存在或已删除")
        return {
            "content": f"已确认认知（置信度 1.0）：{_triples_content([confirmed])}",
            "triple": _triple_to_dict(confirmed),
        }

    return {
        "search_cognition": _search_cognition,
        "search_by_relation": _search_by_relation,
        "search_entity": _search_entity,
        "search_notes": _search_notes,
        "create_cognition": _create_cognition,
        "update_cognition": _update_cognition,
        "delete_cognition": _delete_cognition,
        "merge_cognition": _merge_cognition,
        "confirm_cognition": _confirm_cognition,
    }


# ==================== OpenAI 格式 schema ====================

def _schema(name: str, description: str, properties: Dict[str, Any], required: List[str]) -> dict:
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


_DIM_ENUM = {"type": "string", "enum": _DIM_VALUES, "description": "限定认知维度"}

_COGNITION_TOOL_SCHEMAS = [
    _schema(
        "search_cognition",
        "【主动回忆】从大脑皮层（认知三元组）中按关键词检索信息。"
        "当你需要回忆用户/项目/世界知识来完成推理时调用。"
        "多个关键词用空格分隔，任一关键词命中即返回（OR 关系），"
        "建议用已知实体名（人名/项目名）作为锚点。"
        "注意：已注入系统提示词的记忆无需重复查询，此工具用于查深层记忆。",
        {
            "query": {"type": "string", "description": "查询关键词，如「何福建 女朋友 零代码项目」"},
            "top_k": {"type": "integer", "description": "返回数量上限，默认 50，最大 100", "default": 50},
            "dimension": _DIM_ENUM,
            "min_confidence": {"type": "number", "description": "最低置信度阈值，默认 0.5", "default": 0.5},
            "include_notes": {"type": "boolean", "description": "是否同时搜索笔记，默认 true", "default": True},
        },
        ["query"],
    ),
    _schema(
        "search_by_relation",
        "【关系检索】沿指定关系（谓词）检索所有三元组。"
        "例如 search_by_relation('女朋友') 返回所有「X的女朋友是Y」。",
        {
            "relation": {"type": "string", "description": "关系名称（谓词），如「女朋友」「擅长」「朋友」"},
            "dimension": _DIM_ENUM,
            "top_k": {"type": "integer", "description": "返回数量上限，默认 20，最大 50", "default": 20},
        },
        ["relation"],
    ),
    _schema(
        "search_entity",
        "【实体检索】围绕指定实体检索其所有关联三元组。"
        "例如 search_entity('张三') 返回张三的所有关系（偏好、职业、朋友等）。",
        {
            "entity": {"type": "string", "description": "实体名称，如「张三」「Aion 项目」"},
            "dimension": _DIM_ENUM,
            "top_k": {"type": "integer", "description": "返回数量上限，默认 20，最大 50", "default": 20},
        },
        ["entity"],
    ),
    _schema(
        "search_notes",
        "【笔记检索】搜索笔记本中的长文本笔记（任务日志/状态归档/知识笔记）。",
        {
            "query": {"type": "string", "description": "关键词，匹配标题/内容/标签"},
            "top_k": {"type": "integer", "description": "返回数量，默认 5", "default": 5},
            "note_type": {"type": "string", "enum": _NOTE_TYPES, "description": "可选过滤笔记类型"},
            "time_range": {"type": "string", "description": "可选时间范围，如 7d/30d/90d/all"},
            "include_archived": {"type": "boolean", "description": "是否包含已归档笔记，默认 true", "default": True},
        },
        ["query"],
    ),
    _schema(
        "create_cognition",
        "【认知写入】显式创建一条认知三元组（用户画像/客观知识/环境信息）。"
        "subject/predicate/object 使用用户当前语言。"
        "与 COGNITION 标记块互补：此工具用于单个精确写入。",
        {
            "subject": {"type": "string", "description": "主体，保持用户原文用词"},
            "predicate": {"type": "string", "description": "谓词/关系，保持用户原文用词"},
            "object": {"type": "string", "description": "客体/值，保持用户原文用词"},
            "dimension": {"type": "string", "enum": ["user", "self", "env", "world"], "description": "认知维度，默认 world"},
            "confidence": {"type": "number", "description": "置信度 0-1，默认 0.7"},
        },
        ["subject", "predicate", "object"],
    ),
    _schema(
        "update_cognition",
        "【认知修正】修改一条认知三元组的字段。"
        "当用户纠正认知细节时调用，例如用户说「我今年26岁不是25」→ 更新对应三元组 object='26岁'。"
        "修改前会记录错题本（correction_log）。",
        {
            "rel_id": {"type": "string", "description": "要修改的三元组 rel_id"},
            "subject": {"type": "string", "description": "（可选）新的 subject"},
            "predicate": {"type": "string", "description": "（可选）新的 predicate"},
            "object": {"type": "string", "description": "（可选）新的 object"},
            "dimension": _DIM_ENUM,
            "confidence": {"type": "number", "description": "（可选）新置信度 0-1"},
        },
        ["rel_id"],
    ),
    _schema(
        "delete_cognition",
        "【认知修正】删除一条认知三元组（软删除，可审计）。"
        "当用户明确指出某条认知是错误时调用。",
        {
            "rel_id": {"type": "string", "description": "要删除的三元组 rel_id（从 search_cognition 结果获取）"},
        },
        ["rel_id"],
    ),
    _schema(
        "merge_cognition",
        "【认知修正】合并两条重复的认知三元组（source 软删除，target 置信度取较高值）。"
        "当你发现两条认知表达相同含义时调用。",
        {
            "source_id": {"type": "string", "description": "来源三元组 rel_id（将被软删除）"},
            "target_id": {"type": "string", "description": "保留的目标三元组 rel_id"},
        },
        ["source_id", "target_id"],
    ),
    _schema(
        "confirm_cognition",
        "【认知修正】确认某条认知为正确：置信度提升至 1.0 并标记 is_confirmed_by_user。"
        "当用户明确表示「是的/没错/就是这样」时调用。",
        {
            "rel_id": {"type": "string", "description": "要确认的三元组 rel_id"},
        },
        ["rel_id"],
    ),
]


def register_cognition_tools(
    registry: IToolRegistry,
    repo: ICognitiveRepo,
    user_id: str = "default",
) -> None:
    """把 9 个认知工具注册进注册表（handler 与 schema 成对注册）"""
    handlers = _make_handlers(repo, user_id)
    for tool in _COGNITION_TOOL_SCHEMAS:
        name = tool["function"]["name"]
        registry.register(name, handlers[name], schema=tool)
    logger.info(f"已注册 {len(_COGNITION_TOOL_SCHEMAS)} 个认知工具（user_id={user_id}）")
