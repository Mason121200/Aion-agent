"""CognitionChatSession —— 对话即记忆的体验闭环

每一轮用户消息的处理流程：
1. 从记忆库构建动态上下文（RAG 注入，token 预算裁剪）
2. system = 基础提示 + 认知提取规则 + 动态上下文；user = 用户消息
3. 调用 LLM 流式输出回复
4. 回复文本实时送入认知管道 → 流式状态机剥离认知块 →
   认知块自动解析、分流、去重保存（用户看不到 JSON 噪声）
5. 下一轮提问时，本轮沉淀的记忆自动注入 → LLM「想起来」

这就是 MVP 的核心体验：对话过程自动沉淀记忆，记忆反过来影响回答。
"""

from __future__ import annotations

import logging
from typing import AsyncGenerator, Dict, Optional, Tuple

from aion_agent.pipeline.cognition_pipeline import CognitionPipeline
from aion_agent.pipeline.dimension_split_filter import DispatchResult
from aion_agent.storage.hash_embedder import HashEmbedder
from aion_agent.storage.in_memory_cognitive_repo import InMemoryCognitiveRepo
from aion_agent.use_cases.cognition_injector import CognitionInjector

logger = logging.getLogger(__name__)

_DEFAULT_BASE_PROMPT = (
    "你是一个乐于助人的 AI 助手。\n"
    "请把系统注入的【已知认知记忆】当作你自己的记忆，回答时自然引用。\n"
    "每次回复末尾，把对话中值得长期记住的信息按【认知提取规则】输出认知块。"
)


def _cognition_summary(result: DispatchResult) -> Dict[str, int]:
    """把分流结果压缩为记忆沉淀摘要"""
    return {
        "triples": len(result.triples),
        "states": len(result.states),
        "notes": len(result.notes),
        "skipped": result.skipped,
        "total": (
            len(result.triples) + len(result.states) + len(result.notes)
        ),
    }


def _merge_summary(total: Dict[str, int], part: Dict[str, int]) -> Dict[str, int]:
    for key in ("triples", "states", "notes", "skipped", "total"):
        total[key] = total.get(key, 0) + part.get(key, 0)
    return total


class CognitionChatSession:
    """对话即记忆：一轮对话 = 一次「注入记忆 → 生成回复 → 沉淀记忆」"""

    def __init__(
        self,
        llm,
        cognitive_repo: Optional[InMemoryCognitiveRepo] = None,
        pipeline: Optional[CognitionPipeline] = None,
        injector: Optional[CognitionInjector] = None,
        user_id: str = "chat_user",
        base_prompt: str = _DEFAULT_BASE_PROMPT,
        token_budget: int = 2000,
    ):
        self._llm = llm
        self._user_id = user_id
        self._base_prompt = base_prompt
        self._repo = cognitive_repo or InMemoryCognitiveRepo(embedder=HashEmbedder())
        self._pipeline = pipeline or CognitionPipeline(cognitive_repo=self._repo)
        self._injector = injector or CognitionInjector(
            self._repo, token_budget=token_budget
        )

    async def chat_stream(
        self, user_message: str
    ) -> AsyncGenerator[Tuple[Optional[str], Optional[Dict[str, int]]], None]:
        """流式对话

        Yields:
            (text_delta, None)        —— 可见回复文本增量（认知块已被剥离）
            (None, cognition_summary) —— 某个认知块沉淀完成后的摘要
        """
        dynamic = await self._injector.build_dynamic_context(
            self._user_id, current_message=user_message
        )
        system = (
            self._injector.build_static_system_prompt(base_prompt=self._base_prompt)
            + dynamic
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ]

        # 每轮重置流式状态机
        self._pipeline.reset_markdown()

        for delta in self._llm.stream_chat(messages):
            if not delta:
                continue
            visible, block = self._pipeline.feed_markdown(delta)
            if visible:
                yield visible, None
            if block:
                result = await self._pipeline.process_block(block, self._user_id)
                yield None, _cognition_summary(result)

        # 冲刷残留
        visible, block = self._pipeline.flush_markdown()
        if visible:
            yield visible, None
        if block:
            result = await self._pipeline.process_block(block, self._user_id)
            yield None, _cognition_summary(result)

    async def chat(self, user_message: str) -> Dict:
        """非流式对话（供测试/脚本使用）

        Returns:
            {"reply": str, "cognition": {triples, states, notes, skipped, total}}
        """
        reply_parts: list = []
        totals = _cognition_summary(DispatchResult())

        async for delta, summary in self.chat_stream(user_message):
            if delta:
                reply_parts.append(delta)
            if summary:
                _merge_summary(totals, summary)

        return {"reply": "".join(reply_parts), "cognition": totals}