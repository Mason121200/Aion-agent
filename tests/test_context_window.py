"""上下文窗口管理单元测试：token 估算 / 历史窗口裁剪 / 预算裁剪"""

from aion_agent.core.entities.message import Message
from aion_agent.use_cases.react.context_window import (
    estimate_message_tokens,
    estimate_tokens,
    trim_history,
    trim_messages_by_tokens,
)


def _messages(n: int):
    return [
        Message(session_id="s", role="user" if i % 2 == 0 else "assistant",
                content=f"消息{i}")
        for i in range(n)
    ]


class TestEstimateTokens:
    def test_empty(self):
        assert estimate_tokens("") == 0

    def test_chinese(self):
        assert estimate_tokens("中文中文") >= 1

    def test_english(self):
        assert estimate_tokens("hello world") >= 1


class TestTrimHistory:
    def test_keep_last_n(self):
        history = _messages(25)
        out = trim_history(history, max_messages=5)
        assert len(out) == 5
        assert out[-1].content == "消息24"
        assert out[0].content == "消息20"

    def test_noop_within_limit(self):
        history = _messages(3)
        out = trim_history(history, max_messages=10)
        assert len(out) == 3

    def test_zero_means_no_trim(self):
        history = _messages(25)
        out = trim_history(history, max_messages=0)
        assert len(out) == 25


class TestTrimMessagesByTokens:
    def test_within_budget_no_drop(self):
        messages = [
            {"role": "system", "content": "规则"},
            {"role": "user", "content": "你好"},
        ]
        out, dropped = trim_messages_by_tokens(messages, budget=1000)
        assert dropped == 0
        assert len(out) == 2

    def test_drops_oldest_keeps_system_and_tail(self):
        messages = [
            {"role": "system", "content": "规则" * 50},
            {"role": "user", "content": "A" * 5000},
            {"role": "assistant", "content": "B" * 5000},
            {"role": "user", "content": "最新问题"},
        ]
        out, dropped = trim_messages_by_tokens(messages, budget=100)
        assert dropped >= 1
        # system 消息永远保留
        assert out[0]["role"] == "system"
        # 最新用户消息受保护
        assert out[-1]["content"] == "最新问题"

    def test_only_system_no_drop(self):
        messages = [{"role": "system", "content": "S" * 5000}]
        out, dropped = trim_messages_by_tokens(messages, budget=10)
        assert dropped == 0
        assert len(out) == 1


class TestEstimateMessageTokens:
    def test_includes_reasoning(self):
        msg = Message(session_id="s", role="assistant", content="正文", reasoning="思考")
        assert estimate_message_tokens(msg) == estimate_tokens("正文") + estimate_tokens("思考")