"""ReActLoop / ReActChatSession 单元测试

覆盖：
- 无工具调用直接完成
- 工具调用 → 观察 → 反思 → 第二轮完成
- 步数耗尽兜底总结
- token 预算提前收尾
- 认知块自动剥离并沉淀
- 会话级：消息持久化 + 记忆跨轮注入
"""

import asyncio

from aion_agent.core.entities.message import Message
from aion_agent.core.ports.i_llm_client import LLMResponse, StreamChunk
from aion_agent.pipeline.cognition_pipeline import CognitionPipeline
from aion_agent.storage.in_memory_cognitive_repo import InMemoryCognitiveRepo
from aion_agent.storage.json_chat_repo import JsonChatRepo
from aion_agent.tools import ToolExecutor, ToolRegistry, register_builtin_tools
from aion_agent.use_cases.react.react_loop import ReActLoop
from aion_agent.use_cases.react_chat_session import ReActChatSession


def run(coro):
    return asyncio.run(coro)


class FakeAsyncLLM:
    """可编程假 LLM：async stream() / complete()"""

    def __init__(self, turns=None):
        # turns: [{content, tool_calls, usage_total}]
        self.turns = turns or [{"content": "好的。", "tool_calls": None}]
        self.requests = []
        self._index = 0

    async def stream(self, messages, tools=None, tool_choice="auto",
                     temperature=0.7, max_tokens=4096):
        self.requests.append(list(messages))
        turn = self.turns[min(self._index, len(self.turns) - 1)]
        self._index += 1
        content = turn.get("content", "")
        for i in range(0, len(content), 7):
            yield StreamChunk(content=content[i:i + 7], is_final=False)
        yield StreamChunk(
            content="",
            is_final=True,
            tool_calls=turn.get("tool_calls") or None,
            usage={"total_tokens": turn.get("usage_total", 10)},
        )

    async def complete(self, messages, tools=None, tool_choice="auto",
                       temperature=0.7, max_tokens=4096):
        return LLMResponse(
            content='{"action": "fallback", "reason": "测试", "correction": "修正"}'
        )


def _make_env(max_steps=5, max_tokens_budget=8000, max_context_messages=20,
              llm_reflect_enabled=False):
    """构造 循环 + 工具注册表/执行器 + 认知管道"""
    registry = ToolRegistry()
    register_builtin_tools(registry)
    executor = ToolExecutor(registry)
    repo = InMemoryCognitiveRepo()
    pipeline = CognitionPipeline(cognitive_repo=repo)
    return registry, executor, repo, pipeline




async def _collect(loop):
    return [event async for event in loop.run()]


def _run_events(llm, history=None, user_id="u1", session_id="s1", **kwargs):
    registry, executor, repo, pipeline = _make_env()
    loop = ReActLoop(
        llm_client=llm,
        history=history or [],
        user_id=user_id,
        session_id=session_id,
        system_prompt="你是助手",
        pipeline=pipeline,
        tool_registry=registry,
        tool_executor=executor,
        **kwargs,
    )
    return run(_collect(loop)), repo


class TestReActLoop:
    def test_no_tool_call_finishes(self):
        llm = FakeAsyncLLM([{"content": "你好，我是助手"}])
        events, _ = _run_events(llm)
        types = [e["type"] for e in events]
        assert types.count("token") > 0
        assert "tool_call" not in types
        final = [e for e in events if e["type"] == "final"][0]
        assert final["content"] == "你好，我是助手"
        session = [e for e in events if e["type"] == "session"][0]
        assert session["steps"] == 1

    def test_tool_call_then_finish(self):
        """第 1 轮调用 calculator，第 2 轮无工具调用直接完成"""
        llm = FakeAsyncLLM([
            {
                "content": "让我计算一下。",
                "tool_calls": [{
                    "id": "c1", "type": "function",
                    "function": {"name": "calculator",
                                 "arguments": {"expression": "2 + 3 * 4"}},
                }],
            },
            {"content": "计算结果为 14。"},
        ])
        events, _ = _run_events(llm)
        types = [e["type"] for e in events]
        assert types.count("tool_call") == 1
        assert types.count("tool_result") == 1
        result = [e for e in events if e["type"] == "tool_result"][0]
        assert result["tool_call"]["success"] is True
        final = [e for e in events if e["type"] == "final"][0]
        assert "14" in final["content"]
        # 第二轮请求里应包含工具观察结果（tool 消息）
        assert any(
            m.get("role") == "tool" for m in llm.requests[1]
        )

    def test_failed_tool_triggers_fallback_and_retries(self):
        """工具失败 → 反思 fallback → 注入修正指令 → 下一轮请求包含系统提示"""
        llm = FakeAsyncLLM([
            {
                "content": "读文件。",
                "tool_calls": [{
                    "id": "c1", "type": "function",
                    "function": {"name": "read_file",
                                 "arguments": {"path": "Z:/不存在.txt"}},
                }],
            },
            {"content": "文件不存在，我换一种方式。"},
        ])
        events, _ = _run_events(llm, llm_reflect_enabled=False)
        result = [e for e in events if e["type"] == "tool_result"][0]
        assert result["tool_call"]["success"] is False
        assert "文件不存在" in (result["tool_call"]["error"] or "")
        # fallback 修正指令被注入为 user 消息
        assert any(
            m.get("role") == "user" and "系统提示" in m.get("content", "")
            for m in llm.requests[1]
        )

    def test_step_exhaustion_summary(self):
        """每轮都调用工具 → 步数耗尽 → 兜底总结"""
        llm = FakeAsyncLLM([
            {
                "content": "继续算",
                "tool_calls": [{
                    "id": f"c{i}", "type": "function",
                    "function": {"name": "calculator",
                                 "arguments": {"expression": f"1+{i}"}},
                }],
            }
            for i in range(3)
        ])
        events, _ = _run_events(llm, max_steps=2)
        final = [e for e in events if e["type"] == "final"][0]
        assert "步数上限" in final["content"]
        session = [e for e in events if e["type"] == "session"][0]
        assert session["exhausted"] is True
        assert session["steps"] == 2

    def test_token_budget_exhausted(self):
        """累计 token 达到预算 → 提前收尾"""
        llm = FakeAsyncLLM([
            {
                "content": "算一下",
                "tool_calls": [{
                    "id": "c1", "type": "function",
                    "function": {"name": "calculator",
                                 "arguments": {"expression": "1+1"}},
                }],
                "usage_total": 10,
            },
            {"content": "继续", "usage_total": 10},
        ])
        events, _ = _run_events(llm, max_tokens_budget=10)
        types = [e["type"] for e in events]
        assert "budget_exhausted" in types
        session = [e for e in events if e["type"] == "session"][0]
        assert session["tokens"] == 10

    def test_cognition_block_stripped_and_stored(self):
        """回复中的认知块被剥离、解析并沉淀为记忆"""
        content = (
            "我记住了。\n"
            "<!--COGNITION_START-->"
            '[{"type":"triple","subject":"小明","predicate":"喜欢","object":"数学","dimension":"user","confidence":0.95}]'
            "<!--COGNITION_END-->"
        )
        llm = FakeAsyncLLM([{"content": content}])
        events, repo = _run_events(llm)
        final = [e for e in events if e["type"] == "final"][0]
        assert "COGNITION_START" not in final["content"]
        assert "记住了" in final["content"]

        cognition_events = [e for e in events if e["type"] == "cognition"]
        assert any(e.get("triples", 0) >= 1 for e in cognition_events)

        triples = run(repo.retrieve("u1", query="*"))
        assert any(t.subject == "小明" for t in triples)

    def test_history_window_trimmed_silently(self):
        """历史超过窗口 → 循环仍正常执行"""
        history = [
            Message(session_id="s1", role="user" if i % 2 == 0 else "assistant",
                    content=f"历史{i}")
            for i in range(25)
        ]
        llm = FakeAsyncLLM([{"content": "好的"}])
        events, _ = _run_events(llm, history=history, max_context_messages=5)
        session = [e for e in events if e["type"] == "session"][0]
        assert session["steps"] == 1

    def test_error_event_on_llm_failure(self):
        class BoomLLM(FakeAsyncLLM):
            async def stream(self, *args, **kwargs):
                raise RuntimeError("boom")
                yield  # pragma: no cover

        events, _ = _run_events(BoomLLM())
        assert any(e["type"] == "error" for e in events)


class TestReActChatSession:
    def test_session_persists_messages(self):
        llm = FakeAsyncLLM([
            {"content": "你好小杨！\n<!--COGNITION_START-->"
                       '[{"type":"triple","subject":"小杨","predicate":"偏好语言","object":"中文","dimension":"user","confidence":0.95}]'
                       "<!--COGNITION_END-->"}
        ])
        repo = InMemoryCognitiveRepo()
        chat_repo = JsonChatRepo()
        session = ReActChatSession(
            llm, cognitive_repo=repo, chat_repo=chat_repo, user_id="u1",
        )
        run(session.create_session("u1"))
        result = run(session.chat("我叫小杨"))
        assert result["reply"]

        history = run(session.get_history())
        roles = [m.role for m in history]
        assert roles == ["user", "assistant"]

    def test_memory_injected_next_turn(self):
        llm = FakeAsyncLLM([
            {"content": "记住了。\n<!--COGNITION_START-->"
                       '[{"type":"triple","subject":"小杨","predicate":"偏好语言","object":"中文","dimension":"user","confidence":0.95}]'
                       "<!--COGNITION_END-->"},
            {"content": "你叫小杨。"},
        ])
        repo = InMemoryCognitiveRepo()
        session = ReActChatSession(llm, cognitive_repo=repo, user_id="u1")
        run(session.chat("我叫小杨"))
        run(session.chat("我叫什么？"))

        # 记忆注入在独立的【动态上下文】system 消息中（静态规则 + 动态上下文分离）
        second_system_messages = [
            m for m in llm.requests[1] if m.get("role") == "system"
        ]
        assert any(
            "小杨偏好语言中文" in m.get("content", "")
            for m in second_system_messages
        )

    def test_session_saves_full_streamed_reply_with_tool(self):
        """工具调用轮的正文（问候语）也要进历史，不能只剩最后一句确认"""
        llm = FakeAsyncLLM([
            {
                "content": "你好，小王！很高兴认识你！我已经记住你的名字了。",
                "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {
                        "name": "create_cognition",
                        "arguments": {
                            "subject": "用户", "predicate": "名字是",
                            "object": "小王", "dimension": "user",
                            "confidence": 0.95,
                        },
                    },
                }],
            },
            {"content": "已经把你的名字记到长期记忆里了。"},
        ])
        repo = InMemoryCognitiveRepo()
        chat_repo = JsonChatRepo()
        session = ReActChatSession(
            llm, cognitive_repo=repo, chat_repo=chat_repo, user_id="u1",
        )
        run(session.create_session("u1"))
        run(session.chat("我是小王，你好"))

        history = run(session.get_history())
        assistant_msgs = [m.content for m in history if m.role == "assistant"]
        assert assistant_msgs and "小王！很高兴认识你" in assistant_msgs[-1]
        assert "已经把你的名字记到长期记忆里了" in assistant_msgs[-1]

        # create_cognition 工具确实写入了长期记忆
        triples = asyncio.run(repo.search_triples("u1", "小王"))
        assert any(t.subject == "用户" and "小王" in t.object for t in triples)

    def test_cognition_event_carries_records(self):
        """cognition 事件携带具体记录内容，供 CLI 展示简洁提示"""
        content = (
            "记住了。\n"
            "<!--COGNITION_START-->"
            '[{"type":"triple","subject":"小杨","predicate":"偏好语言","object":"中文","dimension":"user","confidence":0.95}]'
            "<!--COGNITION_END-->"
        )
        llm = FakeAsyncLLM([{"content": content}])
        events, _ = _run_events(llm)
        cog = [e for e in events if e["type"] == "cognition"][0]
        assert cog["records"] == ["小杨偏好语言中文"]
        assert cog["total"] == 1

    def test_rule_based_extract_fallback(self):
        """LLM 未输出认知块时，规则兜底仍能提取并沉淀自我介绍"""
        llm = FakeAsyncLLM([{"content": "你好！很高兴认识你。"}])
        repo = InMemoryCognitiveRepo()
        session = ReActChatSession(llm, cognitive_repo=repo, user_id="u1")
        run(session.create_session("u1"))
        result = run(session.chat("我叫小李，我喜欢看电影"))

        cog_events = [e for e in result["events"] if e["type"] == "cognition"]
        assert cog_events, "缺少规则兜底 cognition 事件"
        records = [r for e in cog_events for r in e.get("records", [])]
        assert any("用户名字是小李" in r for r in records)
        assert any("用户喜欢看电影" in r for r in records)

        triples = run(repo.search_triples("u1", "小李"))
        assert any(t.subject == "用户" and t.object == "小李" for t in triples)
        prefs = run(repo.search_triples("u1", "看电影"))
        assert any(t.subject == "用户" and "看电影" in t.object for t in prefs)

    def test_rule_based_extract_skips_questions(self):
        """反问句（我叫什么）不应被当作名字沉淀"""
        llm = FakeAsyncLLM([{"content": "你叫小李。"}])
        repo = InMemoryCognitiveRepo()
        session = ReActChatSession(llm, cognitive_repo=repo, user_id="u1")
        run(session.create_session("u1"))
        result = run(session.chat("我叫什么？"))
        cog_events = [e for e in result["events"] if e["type"] == "cognition"]
        assert not cog_events
        triples = run(repo.retrieve("u1", query="*")) or []
        assert not any(t.subject == "用户" and "什么" in t.object for t in triples)
