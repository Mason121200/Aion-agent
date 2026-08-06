"""认知处理管道编排器 — 串联独立过滤器

```
原始文本 → MarkdownParseFilter → JsonParseFilter
         → DimensionSplitFilter → StorageFilter → 认知存储
```

设计原则：
- 过滤器之间不共享内部状态，只通过参数/返回值传递数据
- 每条过滤器可独立单元测试
- 管道编排器只负责串联，不包含业务逻辑

MVP 简化：zero_code 中的 SemanticDedupFilter（需 embedding 模型）被裁掉，
精确去重由存储层（InMemoryCognitiveRepo 的 subject/predicate/object 去重键）负责。
"""

import logging
from typing import List, Dict, Any, Optional

from aion_agent.core.ports.i_cognitive_repo import ICognitiveRepo
from aion_agent.pipeline.dimension_split_filter import (
    DimensionSplitFilter,
    DispatchResult,
)
from aion_agent.pipeline.json_parse_filter import JsonParseFilter
from aion_agent.pipeline.markdown_parse_filter import MarkdownParseFilter
from aion_agent.pipeline.storage_filter import StorageFilter

logger = logging.getLogger(__name__)


class CognitionPipeline:
    """认知处理管道编排器

    支持两种调用模式：
    - 流式模式：逐块 feed，适用于 LLM 流式输出
    - 批量模式：直接传入完整认知块 JSON

    用法示例（流式）：
        pipeline = CognitionPipeline(repo)
        for chunk in llm_stream:
            visible, block = pipeline.feed_markdown(chunk)
            yield visible  # 可见文本实时输出
        pipeline.flush_markdown()

    用法示例（批量）：
        pipeline = CognitionPipeline(repo)
        result = await pipeline.process_block(raw_block, user_id)
    """

    def __init__(self, cognitive_repo: Optional[ICognitiveRepo] = None):
        """初始化管道，注入存储仓库

        Args:
            cognitive_repo: 认知存储（triples/states/notes 共用）
        """
        self._markdown_filter = MarkdownParseFilter()
        self._json_filter = JsonParseFilter()
        self._dimension_filter = DimensionSplitFilter()
        self._storage_filter = StorageFilter(cognitive_repo=cognitive_repo)

    # ==================== 过滤器1: MarkdownParse ====================

    def feed_markdown(self, chunk: str) -> tuple:
        """输入 LLM 文本块，提取可见文本和认知块

        Returns:
            (visible_text, cognition_block_or_None)
        """
        return self._markdown_filter.feed(chunk)

    def flush_markdown(self) -> tuple:
        """冲刷剩余缓冲区，返回最后的可见文本和认知块"""
        return self._markdown_filter.flush()

    def reset_markdown(self) -> None:
        """重置标记解析状态机"""
        self._markdown_filter.reset()

    # ==================== 过滤器2: JsonParse ====================

    def parse_json(self, raw_json: str) -> List[Dict[str, Any]]:
        """解析 JSON 字符串为认知条目列表"""
        return self._json_filter.process(raw_json)

    # ==================== 过滤器3: DimensionSplit ====================

    def split_dimension(
        self, items: List[Dict[str, Any]], user_id: str
    ) -> DispatchResult:
        """维度分流

        Returns:
            DispatchResult 包含 triples, states, notes, skipped
        """
        return self._dimension_filter.process(items, user_id)

    # ==================== 过滤器4: Storage ====================

    async def store(
        self,
        triples: List[Dict[str, Any]],
        states: List[Dict[str, Any]],
        notes: List[Dict[str, Any]],
    ) -> bool:
        """持久化存储，返回是否全部成功"""
        return await self._storage_filter.process(triples, states, notes)

    # ==================== 全流程快捷方法 ====================

    async def process_stream(
        self, chunks: List[str], user_id: str
    ) -> tuple:
        """处理流式文本（全流程快捷方法）

        Args:
            chunks: LLM 输出的文本块列表
            user_id: 用户标识

        Returns:
            (visible_chunks, DispatchResult)
        """
        self.reset_markdown()
        visible_chunks: List[str] = []
        all_cognition_blocks: List[str] = []

        for chunk in chunks:
            visible, block = self.feed_markdown(chunk)
            if visible:
                visible_chunks.append(visible)
            if block:
                all_cognition_blocks.append(block)

        visible, block = self.flush_markdown()
        if visible:
            visible_chunks.append(visible)
        if block:
            all_cognition_blocks.append(block)

        if not all_cognition_blocks:
            return visible_chunks, DispatchResult()

        all_items: List[Dict[str, Any]] = []
        for block in all_cognition_blocks:
            all_items.extend(self.parse_json(block))

        if not all_items:
            return visible_chunks, DispatchResult()

        result = self.split_dimension(all_items, user_id)
        result.store_success = await self.store(
            result.triples, result.states, result.notes
        )
        return visible_chunks, result

    async def process_block(
        self, raw_block: str, user_id: str
    ) -> DispatchResult:
        """处理单个认知块（已去除标记或含标记均可）

        Args:
            raw_block: <!--COGNITION--> 块内的原始 JSON 字符串
            user_id: 用户标识

        Returns:
            分流结果（含 store_success 持久化结果）
        """
        items = self.parse_json(raw_block)
        if not items:
            return DispatchResult()

        result = self.split_dimension(items, user_id)
        result.store_success = await self.store(
            result.triples, result.states, result.notes
        )
        return result

    async def process_batch(
        self,
        items: List[Dict[str, Any]],
        user_id: str,
    ) -> DispatchResult:
        """批量处理（跳过 MarkdownParse 和 JsonParse）

        适用于已解析好的认知条目直接走 分流 → 存储。
        """
        result = self.split_dimension(items, user_id)
        result.store_success = await self.store(
            result.triples, result.states, result.notes
        )
        return result