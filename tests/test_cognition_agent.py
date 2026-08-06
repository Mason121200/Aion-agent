"""CognitionChatSession 单元测试：对话自动沉淀记忆 + 记忆影响下一轮"""

import asyncio

from aion_agent.storage.in_memory_cognitive_repo import InMemoryCognitiveRepo
from aion_agent.use_cases.cognition_agent import CognitionChatSession


def run(coro):
    return asyncio.run(coro)


class FakeLLM:
    """可编程假 LLM：记录收到的请求，返回写死的回复"""

    def __init__(self, reply: str):
        self.reply = reply
        self.requests = []

    def stream_chat(self, messages):
        self.requests.append(messages)
        for i in range(0, len(self.reply), 8):
            yield self.reply[i:i + 8]


REPLY_WITH_COGNITION = (
    "你好小杨！根据我的记忆，你偏好中文。\n"
    "<!--COGNITION_START-->"
    '[{"type":"triple","subject":"小杨","predicate":"偏好语言","object":"中文","dimension":"user","confidence":0.95},'
    '{"type":"triple","subject":"小杨","predicate":"在学","object":"大语言模型","dimension":"state","confidence":0.8,"expires_in":7}]'
    "<!--COGNITION_END-->"
)


class TestChatStream:
    def test_cognition_block_stripped_from_output(self):
        """用户看到的是干净回复，认知块被自动剥离并沉淀"""
        llm = FakeLLM(REPLY_WITH_COGNITION)
        repo = InMemoryCognitiveRepo()
        session = CognitionChatSession(llm, cognitive_repo=repo)

        visible = []
        summaries = []

        async def _go():
            async for delta, summary in session.chat_stream("我是小杨"):
                if delta:
                    visible.append(delta)
                if summary:
                    summaries.append(summary)

        run(_go())

        reply = "".join(visible)
        assert "你好小杨" in reply
        assert "COGNITION_START" not in reply
        assert "<!--" not in reply

        total_triples = sum(s.get("triples", 0) for s in summaries)
        assert total_triples == 2
        triples = run(repo.retrieve("chat_user", query="*"))
        assert len(triples) == 2
        assert any(t.subject == "小杨" for t in triples)

    def test_memory_injected_into_next_turn(self):
        """第二轮请求的 system 提示里应包含第一轮沉淀的记忆（即使问法无词面重叠）"""
        llm = FakeLLM(REPLY_WITH_COGNITION)
        repo = InMemoryCognitiveRepo()
        session = CognitionChatSession(llm, cognitive_repo=repo)

        run(session.chat("我是小杨"))
        run(session.chat("我叫什么？"))

        second_system = llm.requests[1][0]["content"]
        assert "小杨偏好语言中文" in second_system
        assert "已知认知记忆" in second_system

    def test_no_cognition_block(self):
        llm = FakeLLM("好的，我记住了。")
        repo = InMemoryCognitiveRepo()
        session = CognitionChatSession(llm, cognitive_repo=repo)
        result = run(session.chat("随便聊聊"))
        assert result["reply"] == "好的，我记住了。"
        assert result["cognition"]["total"] == 0

    def test_chat_returns_merged_summary(self):
        llm = FakeLLM(REPLY_WITH_COGNITION)
        repo = InMemoryCognitiveRepo()
        session = CognitionChatSession(llm, cognitive_repo=repo)
        result = run(session.chat("我是小杨"))
        assert result["cognition"]["triples"] == 2
        assert result["cognition"]["states"] == 0  # state 维度的 triple 仍计入 triples