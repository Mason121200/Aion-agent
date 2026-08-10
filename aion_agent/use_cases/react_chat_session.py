"""ReActChatSession —— 对话即 ReAct：注入记忆 → 循环推理 → 工具行动 → 认知沉淀

与 CognitionChatSession（一轮一问一答）的区别：
- 完整 ReAct 循环：Think（流式）→ Act（工具调用）→ Observe（观察）→ Reflect（反思）
- 首次引入「上下文窗口管理」：历史窗口（max_context_messages）+ Token 预算（max_tokens_budget）
- 用户消息与最终回复持久化到对话历史，下一轮从窗口内恢复上下文
"""

from __future__ import annotations

import asyncio
import logging
import textwrap
import uuid
from datetime import datetime
from typing import AsyncGenerator, Dict, List, Optional

import re

from aion_agent.core.entities.cognitive_triple import CognitiveTriple, Dimension
from aion_agent.core.entities.message import Message
from aion_agent.core.ports.i_cognitive_repo import ICognitiveRepo
from aion_agent.pipeline.cognition_pipeline import CognitionPipeline
from aion_agent.storage.hash_embedder import HashEmbedder
from aion_agent.storage.in_memory_cognitive_repo import InMemoryCognitiveRepo
from aion_agent.storage.json_chat_repo import JsonChatRepo
from aion_agent.skills import SkillRegistry, build_default_skills
from aion_agent.tools import ToolExecutor, ToolRegistry
from aion_agent.use_cases.cognition_injector import CognitionInjector
from aion_agent.use_cases.react.prompts import (
    ECOMMERCE_TOOL_HINT,
    REACT_TOOL_HINT,
    STUDY_TOOL_HINT,
)
from aion_agent.use_cases.react.react_loop import ReActLoop

logger = logging.getLogger(__name__)

# ---- 规则兜底：LLM 未输出认知块时，从自介绍句式直接提取（高置信） ----
_NAME_PATTERNS = [
    re.compile(r"我叫(?P<name>[一-龥A-Za-z]{1,8})"),
    re.compile(r"我的名字(?:是|叫)(?P<name>[一-龥A-Za-z]{1,8})"),
    re.compile(r"你可以叫我(?P<name>[一-龥A-Za-z]{1,8})"),
    re.compile(r"我是(?P<name>[一-龥A-Za-z]{1,8})(?=[，。！？!?,\s]|你好|$)"),
]
_PREFER_PATTERNS = [
    re.compile(r"我(?:特别)?喜欢(?P<obj>[一-龥A-Za-z0-9]{1,30})"),
]
_NAME_STOPWORDS = {"我", "你", "谁", "什么", "啥", "什么名字", "助手", "人类", "机器人", "ai", "一个", "那个"}
_NAME_BAD_PREFIXES = ("做", "在", "来", "想", "个", "一", "这", "那", "搞", "写", "学", "开")


_DEFAULT_BASE_PROMPT = textwrap.dedent("""\
    你是 Aion Agent（艾昂）—— 一个拥有「认知记忆」的 AI 助手，而不是用完即忘的通用聊天机器人。

    ## 你是谁
    - 名字：Aion Agent，认知记忆助手。
    - 能力：你拥有一条长期记忆（认知记忆）：对话中值得记住的信息会通过认知块自动沉淀，
      并在之后的每一轮对话中注入给你，让你能记住用户、跨会话续上话题。
    - 性格：温和、可靠、细腻。说话自然、简洁，偶尔带一点认真与幽默，像一位有记忆的私人助理。

    ## 让用户感知到你的记忆
    - 开场：新会话或用户第一次打招呼时，主动简短介绍自己：
      「我是 Aion Agent，一个有长期记忆的 AI 助手。告诉我你的名字或偏好，我会一直记得你。」
    - 对话中：把系统注入的【已知认知记忆】当作你自己的记忆，自然引用，像老朋友重逢
      （例如「我记得你之前提过…」），但不要生硬堆砌。
    - 收尾：本轮回确有值得记住的信息且已输出认知块时，用一句话自然确认沉淀结果，
      例如「我记下了：你的名字是小李」，让用户明确感知到记忆真的落库了。

    ## 记忆行为准则
    - 每次回复末尾，把对话中值得长期记住的信息按【认知提取规则】输出认知块。
    - 【记忆沉淀铁律】当用户自我介绍（名字/称呼/职业/偏好/目标）时，
      你必须在回复末尾输出对应的认知块（type=triple, dimension=user）。
      只有当你确实输出了认知块，才可以告诉用户「已记住 / 以后记得你」；
      如果没有输出认知块，绝不要声称自己记住了。
    - 用户问「你记得我吗 / 我叫什么 / 我喜欢什么」时，优先从【已知认知记忆】中回忆；
      记忆里没有的信息，坦诚说明，不要编造。
""")


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
        max_tokens_budget: int = 20000,
        max_context_messages: int = 20,
        study_repo=None,
        planner_repo=None,
        execution_log=None,
        tool_policy=None,
        tool_auto_approve: bool = False,
        disabled_skills=None,
        tools_enabled: bool = True,
        llm_reflect_enabled: bool = True,
        tool_timeout_seconds: int = 120,
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
        self._study_repo = study_repo
        self._planner_repo = planner_repo
        self._execution_log = execution_log
        self._tools_enabled = tools_enabled
        self._llm_reflect_enabled = llm_reflect_enabled
        self._tool_timeout_seconds = tool_timeout_seconds

        # 工具层：注册内置工具（get_current_time / calculator / read_file）
        self._tool_registry: Optional[ToolRegistry] = None
        self._tool_executor: Optional[ToolExecutor] = None
        self._skill_registry: Optional[SkillRegistry] = None
        if tools_enabled:
            self._tool_registry = ToolRegistry()
            self._skill_registry = SkillRegistry()
            for skill in build_default_skills(
                cognitive_repo=self._repo,
                study_repo=self._study_repo,
                planner_repo=self._planner_repo,
                user_id=self._user_id,
            ):
                enabled = skill.name not in (disabled_skills or set())
                self._skill_registry.install(skill, enabled=enabled)
            self._skill_registry.apply_tools(self._tool_registry)
            self._tool_executor = ToolExecutor(
                self._tool_registry,
                policy=tool_policy,
                auto_approve=tool_auto_approve,
            )

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
        self,
        user_message: str,
        images: Optional[List[str]] = None,
    ) -> AsyncGenerator[Dict, None]:
        """ReAct 对话流：产出事件字典

        事件：reasoning / token / cognition / tool_call / tool_result /
              reflect / context / budget_exhausted / error / final / session
        """
        # 1) 持久化用户消息（images：用户上传的参考图 URL，随消息落库）
        images = [str(u).strip() for u in (images or []) if str(u).strip()]
        await self._chat_repo.save_message(Message(
            session_id=self._session_id,
            role="user",
            content=user_message,
            images=images,
        ))
        if self._execution_log is not None:
            self._execution_log.append(
                session_id=self._session_id,
                event_type="user_message",
                content=user_message,
            )

        # 2) 从窗口加载历史（多取一些，窗口裁剪由 ReActLoop 负责）
        history = await self._chat_repo.get_history(
            self._session_id, limit=self._max_context_messages * 2
        )

        # 3) 注入记忆 + 组装 system prompt
        dynamic = await self._injector.build_dynamic_context(
            self._user_id, current_message=user_message,
        )
        # 学习场景：到期提醒 / 到期复习 / 停滞 / 进行中计划 注入动态上下文，
        # 让 agent 主动发起复习、主动提醒学习、主动采集数据
        if self._study_repo is not None:
            try:
                ov = self._study_repo.overview()
                ctx_lines = []
                due = ov.get("due_reminders") or []
                upcoming = ov.get("upcoming_reminders") or []
                if due:
                    ctx_lines.append("【已到期提醒】" + "；".join(
                        f"{r['title']}（{r['remind_at'][11:16]}）" for r in due
                    ))
                if upcoming:
                    ctx_lines.append("【近期提醒】" + "；".join(
                        f"{r['title']}（{r['remind_at'][5:16].replace('T', ' ')}）"
                        for r in upcoming
                    ))
                now_iso = datetime.now().isoformat()
                review_due_lines = []
                for p in ov.get("active_plans") or []:
                    for it in self._study_repo.list_reviews(plan_id=p["plan_id"]):
                        if it.get("resolved"):
                            continue
                        if (it.get("next_review_at") or "") <= now_iso:
                            review_due_lines.append(f"{p['title']}·{it['content'][:40]}")
                if review_due_lines:
                    ctx_lines.append("【到期复习】" + "；".join(review_due_lines[:6]))
                stagnant = [
                    f"{p['title']}已停滞{p['stagnant_days']}天"
                    for p in ov.get("active_plans") or []
                    if (p.get("stagnant_days") or 0) >= 3
                ]
                if stagnant:
                    ctx_lines.append("【学习停滞】" + "；".join(stagnant))
                active = ov.get("active_plans") or []
                if active:
                    ctx_lines.append("【进行中计划】" + "；".join(
                        f"{p['title']}（{p.get('progress_info', {}).get('progress', 0)}%）" for p in active[:3]
                    ))
                # 待裁决调整：规则层已出对比报告，由 LLM 决定是否采纳（confirm_adjustment）
                pending_reviews = []
                for p in active:
                    for a in (p.get("adjustments") or []):
                        if a.get("conclusion") != "pending_review":
                            continue
                        rep = a.get("report") or {}
                        mets = "；".join(
                            f"{m.get('label')} {m.get('before')}→{m.get('after')}"
                            for m in (rep.get("metrics") or []) if m.get("improved") is not None
                        )
                        pending_reviews.append(
                            f"{p['title']}·{str(a.get('content') or '')[:30]}（{mets or '数据不足'}）"
                        )
                if pending_reviews:
                    ctx_lines.append("【待裁决调整】" + "；".join(pending_reviews[:3]))
                profile_summary = self._study_repo.profile_summary()
                if profile_summary:
                    ctx_lines.append("【学习画像】" + profile_summary)
                if ctx_lines:
                    dynamic = (dynamic + "\n\n" if dynamic else "") + "\n".join(ctx_lines)
            except Exception:  # noqa: BLE001 学习上下文注入失败不影响主流程
                pass
        system = self._injector.build_static_system_prompt(
            base_prompt=self._base_prompt
        )
        if self._tools_enabled:
            system += REACT_TOOL_HINT
            system += STUDY_TOOL_HINT
            system += ECOMMERCE_TOOL_HINT

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
            execution_log=self._execution_log,
        )

        final_content = ""
        streamed_reply: List[str] = []
        cognition_count = 0
        generated_images: List[str] = []
        try:
            async for event in loop.run():
                if event.get("type") == "token":
                    streamed_reply.append(event.get("content", ""))
                elif event.get("type") == "final":
                    final_content = event.get("content", "")
                elif event.get("type") == "tool_retry":
                    # 幻觉守卫触发：丢弃本轮已流出的文本，避免编造内容写入历史
                    streamed_reply.clear()
                elif event.get("type") == "tool_result":
                    data = (event.get("tool_call") or {}).get("data") or {}
                    url = str(data.get("image_url") or "").strip()
                    if url:
                        generated_images.append(url)
                elif event.get("type") == "cognition":
                    cognition_count += 1
                elif event.get("type") == "error" and self._execution_log is not None:
                    self._execution_log.append(
                        session_id=self._session_id,
                        event_type="error",
                        content=str(event.get("error", "")),
                    )
                yield event
        except (asyncio.CancelledError, GeneratorExit):
            # 客户端断开 / 用户停止生成：保存已流出的部分，避免丢失
            partial = "".join(streamed_reply).strip() or final_content
            await self._save_assistant_reply(partial)
            raise

        # 兜底：LLM 未输出任何认知块时，用规则提取明确的自我介绍
        if not cognition_count:
            for summary in await self._rule_based_extract(user_message):
                yield {"type": "cognition", **summary}

        # 5) 持久化最终回复：优先保存完整流式回复（含工具调用前的正文），
        #    避免历史里只剩最后一段确认语；工具中间消息不入历史
        saved_reply = "".join(streamed_reply).strip() or final_content
        await self._save_assistant_reply(saved_reply, images=generated_images)

    async def _save_assistant_reply(
        self,
        content: str,
        images: Optional[List[str]] = None,
    ) -> None:
        """把助手回复写入会话历史 + 执行日志（空内容跳过）"""
        content = str(content or "").strip()
        if not content:
            return
        await self._chat_repo.save_message(Message(
            session_id=self._session_id,
            role="assistant",
            content=content,
            images=[str(u).strip() for u in (images or []) if str(u).strip()],
        ))
        if self._execution_log is not None:
            self._execution_log.append(
                session_id=self._session_id,
                event_type="assistant_reply",
                content=content,
            )

    async def _rule_based_extract(self, user_message: str) -> list:
        """LLM 未输出认知块时的规则兜底：只提取高置信的自介绍句式"""
        summaries = []

        async def store(subject: str, predicate: str, obj: str,
                        dimension: str = "user", confidence: float = 0.85) -> None:
            triple = CognitiveTriple(
                subject=subject,
                predicate=predicate,
                object=obj,
                dimension=Dimension(dimension),
                user_id=self._user_id,
                confidence=confidence,
            )
            await self._repo.save_triple(triple)
            summaries.append({
                "triples": 1, "states": 0, "notes": 0, "skipped": 0,
                "total": 1,
                "records": [f"{subject}{predicate}{obj}"],
            })

        for pattern in _NAME_PATTERNS:
            match = pattern.search(user_message)
            if not match:
                continue
            name = match.group("name")
            if not name or name in _NAME_STOPWORDS:
                continue
            if name.startswith(_NAME_BAD_PREFIXES) or "的" in name or "了" in name:
                continue
            await store("用户", "名字是", name, "user", 0.9)
            break

        for pattern in _PREFER_PATTERNS:
            match = pattern.search(user_message)
            if match and match.group("obj"):
                await store("用户", "喜欢", match.group("obj"), "user", 0.85)

        return summaries

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
