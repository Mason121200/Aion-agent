"""过滤器3：DimensionSplitFilter — 认知维度分流路由

管道-过滤器模式的第三道过滤器（去重由存储层负责）。
将解析后的认知条目分流到 Triple / State / Note / Skip。

分流规则：
- type=triple + user/self/world → CognitiveTriple（大脑长期记忆）
- type=triple + env（配置级） → CognitiveTriple
- type=triple + env（快照级） → Skip（关键词 + 数值模式）
- type=state → AgentState（笔记本临时状态）
- type=note → Note（兼容旧格式）
- object > 200 字符 → Note（自动降级）
- 无 type 旧格式 → 兼容处理为 triple

纯函数约束：只依赖输入参数，可独立单元测试。
"""

import logging
import re
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


@dataclass
class DispatchResult:
    """分流结果 — 传递给 StorageFilter 写入"""

    triples: List[Dict[str, Any]] = field(default_factory=list)
    states: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[Dict[str, Any]] = field(default_factory=list)
    skipped: int = 0
    store_success: bool = True


def _parse_expires_at(item: Dict[str, Any]) -> Optional[str]:
    """解析时效性：expires_in 天数 → ISO datetime 字符串"""
    expires_in = item.get("expires_in")
    if expires_in is None:
        return None
    try:
        days = int(expires_in)
        expires_dt = datetime.now() + timedelta(days=days)
        return expires_dt.isoformat()
    except (ValueError, TypeError):
        return None


class DimensionSplitFilter:
    """维度分流过滤器 — 将认知条目路由到正确的存储通道

    根据认知类型和维度，决定条目去向：
    - 大脑（triple）：长期记忆，RAG 注入
    - 笔记本（state）：临时状态，有生命周期
    - 笔记本（note）：长文本，事后生成
    - 跳过（skip）：环境快照 / 占位内容，无意义
    """

    # 快照特征：强关键词（无需数字配合）
    _SNAPSHOT_STRONG_KEYWORDS = [
        "git状态", "git status", "untracked", "未追踪",
    ]

    # 快照特征：弱关键词（需配合数值模式）
    _SNAPSHOT_WEAK_KEYWORDS = [
        "行数", "测试通过", "测试失败", "变更", "分支", "提交",
    ]

    # 快照特征：数值模式（N行 / N个变更 / N个通过 / N个失败 / N个文件）
    _SNAPSHOT_PATTERNS = [
        r"\d+\s*行",
        r"\d+\s*个\s*(变更|通过|失败|文件|提交|测试)",
        r"\d+\s*(files?|changes?|tests?)",
    ]

    # 笔记质量门禁：拒绝占位/空泛内容（如 LLM 输出的「内容」「讨论了xx」）。
    # 短事实应走 triple，note 只承载有实质内容的长文本。
    _MIN_NOTE_CONTENT_CHARS = 15
    _PLACEHOLDER_WORDS = {"内容", "无", "暂无", "无内容", "待补充", "待定", "-", "备忘", "记录"}

    # ==================== 入口 ====================

    def process(
        self,
        items: List[Dict[str, Any]],
        user_id: str,
    ) -> DispatchResult:
        """分流认知条目

        Args:
            items: 认知条目列表（LLM 提取的 JSON 条目）
            user_id: 用户标识

        Returns:
            DispatchResult：分流结果（triples/states/notes 为字典列表）
        """
        result = DispatchResult()
        if not items:
            return result

        for item in items:
            if not isinstance(item, dict):
                result.skipped += 1
                continue

            item_type = str(item.get("type", "")).strip().lower()

            if item_type == "triple" or not item_type:
                # type=triple 或 无 type（旧格式兼容）
                self._route_triple(item, user_id, result)
            elif item_type == "state":
                self._route_state(item, user_id, result)
            elif item_type == "note":
                self._route_note(item, user_id, result)
            else:
                logger.warning(f"未知认知类型，跳过: {item}")
                result.skipped += 1

        logger.info(
            f"分流完成: triples={len(result.triples)}, "
            f"states={len(result.states)}, notes={len(result.notes)}, "
            f"skipped={result.skipped}"
        )
        return result

    # ==================== 路由 ====================

    def _route_triple(
        self,
        item: Dict[str, Any],
        user_id: str,
        result: DispatchResult,
    ) -> None:
        """Triple 路由：user/self/world → triple；env → 快照过滤"""
        subject = str(item.get("subject", "")).strip()
        predicate = str(item.get("predicate", "")).strip()
        obj = str(item.get("object", "")).strip()

        # 必填字段不完整 → skip
        if not subject or not predicate or not obj:
            logger.warning(f"triple 字段不完整，跳过: {item}")
            result.skipped += 1
            return

        dimension = str(item.get("dimension", "world")).strip().lower()

        # env 快照过滤（关键词 + 数值模式）
        if dimension == "env" and self._is_env_snapshot(item):
            result.skipped += 1
            return

        # 长文本自动降级为 note（object > 200 字符）
        if len(obj) > 200:
            note_item = {
                "type": "note",
                "note_type": "long_text",
                "title": subject[:20] or "认知长文",
                "content": f"{subject} {predicate} {obj}",
                "user_id": item.get("user_id", user_id),
            }
            self._route_note(note_item, user_id, result)
            return

        triple: Dict[str, Any] = {
            "type": "triple",
            "subject": subject,
            "predicate": predicate,
            "object": obj,
            "dimension": dimension,
            "user_id": item.get("user_id", user_id),
            "confidence": _to_float(item.get("confidence"), 0.8),
            "expires_at": _parse_expires_at(item),
        }
        if item.get("source"):
            triple["source"] = item.get("source")

        result.triples.append(triple)

    def _route_state(
        self,
        item: Dict[str, Any],
        user_id: str,
        result: DispatchResult,
    ) -> None:
        """State 路由：type=state → AgentState（笔记本临时状态）"""
        state_name = str(item.get("state_name", "")).strip()
        if not state_name:
            logger.warning(f"state 缺 state_name，跳过: {item}")
            result.skipped += 1
            return

        state: Dict[str, Any] = {
            "type": "state",
            "user_id": item.get("user_id", user_id),
            "state_type": str(item.get("state_type", "task")),
            "state_name": state_name,
            "description": item.get("description"),
            "priority": _to_int(item.get("priority"), 0),
            "confidence": _to_float(item.get("confidence"), 0.9),
            "expires_at": _parse_expires_at(item),
        }
        result.states.append(state)

    def _route_note(
        self,
        item: Dict[str, Any],
        user_id: str,
        result: DispatchResult,
    ) -> None:
        """Note 路由：type=note → Note（笔记本长文本）"""
        content = str(item.get("content", "")).strip()
        if not content:
            logger.warning(f"note 缺 content，跳过: {item}")
            result.skipped += 1
            return

        # 质量门禁：无实质内容的笔记直接跳过（内容过短或纯占位文本）
        if self._is_placeholder_note(content):
            logger.info(f"note 内容无实质价值，跳过: {content[:40]}")
            result.skipped += 1
            return

        title = str(item.get("title", "")).strip()
        # 无标题自动生成：content 前 20 字符
        if not title:
            title = content[:20] if len(content) > 20 else content

        note: Dict[str, Any] = {
            "type": "note",
            "user_id": item.get("user_id", user_id),
            "note_type": str(item.get("note_type", "long_text")),
            "title": title,
            "content": content,
            "tags": item.get("tags", []),
            "confidence": _to_float(item.get("confidence"), 0.9),
        }
        result.notes.append(note)

    # ==================== 笔记质量门禁 ====================

    @staticmethod
    def _is_placeholder_note(content: str) -> bool:
        """判断笔记内容是否无实质价值（应跳过）"""
        normalized = re.sub(
            r"[\s，。！？、；：,.!?;:…\-—()（）【】\[\]]+", "", content
        )
        if len(normalized) < DimensionSplitFilter._MIN_NOTE_CONTENT_CHARS:
            return True
        return normalized in DimensionSplitFilter._PLACEHOLDER_WORDS

    # ==================== 快照过滤 ====================

    def _is_env_snapshot(self, item: Dict[str, Any]) -> bool:
        """判断 env 维度条目是否为环境快照（应跳过）

        特征：关键词 + 数值模式。
        - 强关键词（Git 状态等）：直接判定快照
        - 弱关键词（行数/测试结果/变更）：需配合数值
        """
        text = " ".join([
            str(item.get("subject", "")),
            str(item.get("predicate", "")),
            str(item.get("object", "")),
        ]).lower()

        # 强关键词 → 直接判定
        for kw in self._SNAPSHOT_STRONG_KEYWORDS:
            if kw in text:
                return True

        # 数值模式 → 直接判定
        for pat in self._SNAPSHOT_PATTERNS:
            if re.search(pat, text):
                return True

        # 弱关键词 + 任意数字 → 判定
        if re.search(r"\d+", text):
            for kw in self._SNAPSHOT_WEAK_KEYWORDS:
                if kw in text:
                    return True

        return False


def _to_float(value: Any, default: float) -> float:
    """安全转 float"""
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _to_int(value: Any, default: int) -> int:
    """安全转 int"""
    try:
        return int(value)
    except (ValueError, TypeError):
        return default