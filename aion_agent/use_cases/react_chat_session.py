"""ReActChatSession —— 对话即 ReAct：注入记忆 → 循环推理 → 工具行动 → 认知沉淀

与 CognitionChatSession（一轮一问一答）的区别：
- 完整 ReAct 循环：Think（流式）→ Act（工具调用）→ Observe（观察）→ Reflect（反思）
- 首次引入「上下文窗口管理」：历史窗口（max_context_messages）+ Token 预算（max_tokens_budget）
- 用户消息与最终回复持久化到对话历史，下一轮从窗口内恢复上下文
"""

from __future__ import annotations

import logging
import uuid
from typing import AsyncGenerator, Dict, Optional

from aion_agent.core.entities.message import Message
from aion_agent.core.ports.i_cognitive_repo import ICognitiveRepo
from aion_agent.pipeline.cognition_pipeline import CognitionPipeline
from aion_agent.storage.hash_embedder import HashEmbedder
from aion_agent.storage.in_memory_cognitive_repo import InMemoryCognitiveRepo
from aion_agent.storage.json_chat_repo import JsonChatRepo
from aion_agent.tools import ToolExecutor, ToolRegistry, register_builtin_tools
from aion_agent.use_cases.cognition_injector import CognitionInjector
from aion_agent.use_cases.react.prompts import REACT_TOOL_HINT
from aion_agent.use_cases.react.react_loop import ReActLoop

logger = logging.getLogger(__name__)

_DEFAULT_BASE_PROMPT = (
    "你是一个乐于助人的 AI 助手。\n"
    "请把系统注入的【已知认知记忆】当作你自己的记忆，回答时自然引用。\n"
    "每次回复末尾，把对话中值得长期记住的信息按【认知提取规则】输出认知块。"
)


class ReActChatSession:
    """ReAct 对话会话（体验模式核心）"""

    def __init__(
        self,
        llm,
        cognitive_repo: Optional[ICognitiveRepo] = None,
        chat_repo=None,
        pipeline: Optional[CognitionPipeline] = None,
        injector: Optional[CognitionInjector] = None,
        user_id: str = "chat_user",
        session_id: Optional[str] = None,
        base_prompt: str = _DEFAULT_BASE_PROMPT,
        token_budget: int = 2000,
        max_steps: int = 8,
        max_tokens_budget: int = 8000,
        max_context_messages: int = 20,
        tools_enabled: bool = True,
        llm_reflect_enabled: bool = True,
        tool_timeout_seconds: int = 30,
    ):
        self._llm = llm
        self._user_id = user_id
        self._base_prompt = base_prompt

        self._repo = cognitive_repo or InMemoryCognitiveRepo(
            embedder=HashEmbedder()
        )
        self._chat_repo = chat_repo or JsonChatRepo()
        self._session_id = session_id or f"session_{uuid.uuid4().hex[:8]}"
        self._pipeline = pipeline or CognitionPipeline(cognitive_repo=self._repo)
        self._injector = injector or CognitionInjector(
            self._repo, token_budget=token_budget
        )

        self._max_steps = max_steps
        self._max_tokens_budget = max_tokens_budget
        self._max_context_messages = max_context_messages
        self._tools_enabled = tools_enabled
        self._llm_reflect_enabled = llm_reflect_enabled
        self._tool_timeout_seconds = tool_timeout_seconds

        # 工具层：注册内置工具（get_current_time / calculator / read_file）
        self._tool_registry: Optional[ToolRegistry] = None
        self._tool_executor: Optional[ToolExecutor] = None
        if tools_enabled:
            self._tool_registry = ToolRegistry()
            register_builtin_tools(self._tool_registry)
            self._tool_executor = ToolExecutor(self._tool_registry)

    # ==================== 会话 ====================

    @property
    def session_id(self) -> str:
        return self._session_id

    async def create_session(self, user_id: str) -> str:
        self._user_id = user_id
        self._session_id = await self._chat_repo.create_session(user_id)
        return self._session_id

    async def get_history(self, limit: Optional[int] = None) -> list:
        return await self._chat_repo.get_history(self._session_id, limit=limit)

    # ==================== 主入口 ====================

    async def react_stream(
        self, user_message: str
    ) -> AsyncGenerator[Dict, None]:
        """ReAct 对话流：产出事件字典

        事件：reasoning / token / cognition / tool_call / tool_result /
              reflect / context / budget_exhausted / error / final / session
        """
        # 1) 持久化用户消息
        await self._chat_repo.save_message(Message(
            session_id=self._session_id,
            role="user",
            content=user_message,
        ))

        # 2) 从窗口加载历史（多取一些，窗口裁剪由 ReActLoop 负责）
        history = await self._chat_repo.get_history(
            self._session_id, limit=self._max_context_messages * 2
        )

        # 3) 注入记忆 + 组装 system prompt
        dynamic = await self._injector.build_dynamic_context(
            self._user_id, current_message=user_message,
        )
        system = self._injector.build_static_system_prompt(
            base_prompt=self._base_prompt
        )
        if self._tools_enabled:
            system += REACT_TOOL_HINT

        # 4) ReAct 循环
        loop = ReActLoop(
            llm_client=self._llm,
            history=history,
            user_id=self._user_id,
            session_id=self._session_id,
            system_prompt=system,
            dynamic_context=dynamic,
            pipeline=self._pipeline,
            tool_registry=self._tool_registry,
            tool_executor=self._tool_executor,
            max_steps=self._max_steps,
            max_tokens_budget=self._max_tokens_budget,
            max_context_messages=self._max_context_messages,
            tool_timeout_seconds=self._tool_timeout_seconds,
            llm_reflect_enabled=self._llm_reflect_enabled,
        )

        final_content = ""
        async for event in loop.run():
            if event.get("type") == "final":
                final_content = event.get("content", "")
            yield event

        # 5) 持久化最终回复（只存最终总结，工具中间消息不入历史）
        if final_content:
            await self._chat_repo.save_message(Message(
                session_id=self._session_id,
                role="assistant",
                content=final_content,
            ))

    async def chat(self, user_message: str) -> Dict:
        """非流式对话（供测试/脚本使用）

        Returns:
            {"reply": str, "events": [事件字典]}
        """
        events = []
        reply = ""
        async for event in self.react_stream(user_message):
            events.append(event)
            if event.get("type") == "final":
                reply = event.get("content", "")
        return {"reply": reply, "events": events}