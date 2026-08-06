"""过滤器：SemanticDedupFilter — 语义去重过滤器（移植自 zero_code）

去除重复的认知条目：
- 精确去重：相同 (subject, predicate, object) 只保留第一条
- 语义去重：启用 embedder 时，两两余弦相似度 > 0.85 视为重复（保留前者）

与存储层精确去重的分工：本过滤器负责「本条认知块内」与「跨会话候选」去重，
存储层负责最终落库时的精确合并（save_triple 的 max-confidence + usage+1）。
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 语义相似度阈值：超过视为同义重复（与 zero_code 一致）
_DEDUP_THRESHOLD = 0.85


class SemanticDedupFilter:
    """语义去重过滤器（纯函数优先，embedder 可选）"""

    def __init__(self, embedder=None):
        """Args:
            embedder: （可选）嵌入器，须提供 embed(text)->List[float] 与 is_loaded
        """
        self._embedder = embedder

    def process(
        self,
        items: List[Dict[str, Any]],
        existing_items: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """去重认知条目

        Args:
            items: 新提取的认知条目列表
            existing_items: （可选）已存在的认知条目，用于跨批次去重

        Returns:
            去重后的认知条目列表
        """
        if not items:
            return []

        seen = set()
        deduped = []

        existing_keys = set()
        if existing_items:
            for item in existing_items:
                key = self._make_key(item)
                if key:
                    existing_keys.add(key)

        for item in items:
            key = self._make_key(item)
            if not key:
                deduped.append(item)
                continue
            if key in seen:
                logger.debug(f"精确去重跳过 (重复键): {key}")
                continue
            if key in existing_keys:
                logger.debug(f"跨批次去重跳过 (已存在): {key}")
                continue
            seen.add(key)
            deduped.append(item)

        if len(deduped) > 1 and self._embedder is not None:
            deduped = self._semantic_dedup(deduped)

        removed = len(items) - len(deduped)
        if removed > 0:
            logger.info(f"去重: 移除 {removed}/{len(items)} 条重复")
        return deduped

    @staticmethod
    def _make_key(item: dict) -> Optional[str]:
        """生成精确去重键 (subject, predicate, object)"""
        subject = item.get("subject", "")
        predicate = item.get("predicate", "")
        obj = item.get("object", "")
        if not subject or not predicate or not obj:
            return None
        return f"{subject}|{predicate}|{obj}"

    def _semantic_dedup(
        self, items: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """语义去重：两两余弦相似度 > 阈值视为重复"""
        if self._embedder is None or not getattr(
            self._embedder, "is_loaded", False
        ):
            return items

        vecs: List[Optional[List[float]]] = []
        for item in items:
            text = (
                f"{item.get('subject', '')} {item.get('predicate', '')} "
                f"{item.get('object', '')}"
            )
            try:
                vec = self._embedder.embed(text)
            except Exception as e:
                logger.warning(f"语义去重 embed 失败: {e}")
                vec = None
            vecs.append(vec)

        indices_to_remove = set()
        for i in range(len(vecs)):
            if i in indices_to_remove:
                continue
            vec_i = vecs[i]
            if not vec_i:
                continue
            norm_i = math.sqrt(sum(v * v for v in vec_i))
            if norm_i == 0:
                continue
            for j in range(i + 1, len(vecs)):
                if j in indices_to_remove:
                    continue
                vec_j = vecs[j]
                if not vec_j:
                    continue
                norm_j = math.sqrt(sum(v * v for v in vec_j))
                if norm_j == 0:
                    continue
                cos_sim = sum(
                    a * b for a, b in zip(vec_i, vec_j)
                ) / (norm_i * norm_j)
                if cos_sim > _DEDUP_THRESHOLD:
                    indices_to_remove.add(j)
                    logger.debug(
                        f"语义去重跳过 (相似度 {cos_sim:.3f}): "
                        f"'{items[i].get('subject')} {items[i].get('predicate')}'"
                        f" ≈ '{items[j].get('subject')} {items[j].get('predicate')}'"
                    )

        return [
            item for i, item in enumerate(items)
            if i not in indices_to_remove
        ]
