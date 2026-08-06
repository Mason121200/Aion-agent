"""Observe / Reflect 单元测试：工具结果摘要、错误分类、反思决策"""

import asyncio

from aion_agent.core.ports.i_llm_client import LLMResponse
from aion_agent.core.ports.i_tool_executor import ToolResult
from aion_agent.use_cases.react.observe import classify_error, observe
from aion_agent.use_cases.react.reflect import reflect, reflect_with_llm


def run(coro):
    return asyncio.run(coro)


class TestObserve:
    def test_success_dict_content(self):
        result = ToolResult(success=True, data={"content": "abc"})
        obs = observe(result)
        assert obs["content"] == "abc"
        assert obs["error_type"] is None

    def test_success_long_truncated(self):
        result = ToolResult(success=True, data={"content": "x" * 2500})
        obs = observe(result)
        assert len(obs["content"]) <= 2000 + 40
        assert "截断" in obs["content"]

    def test_success_no_output(self):
        obs = observe(ToolResult(success=True, data=None))
        assert "执行成功" in obs["content"]

    def test_failure_classified(self):
        result = ToolResult(success=False, error="FileNotFoundError: 找不到文件")
        obs = observe(result)
        assert obs["error_type"] == "FileNotFoundError"
        assert obs["suggestion"]

    def test_failure_unknown(self):
        obs = observe(ToolResult(success=False, error="奇怪的错误"))
        assert obs["error_type"] == "UNKNOWN"

    def test_classify_error_keyword(self):
        assert classify_error("no such file") == "FileNotFoundError"
        assert classify_error("permission denied") == "PermissionError"
        assert classify_error("随便") == "UNKNOWN"


class TestReflect:
    def test_no_tool_calls_stop(self):
        decision = reflect([], turn=0)
        assert decision["action"] == "stop"

    def test_all_success_continue(self):
        decision = reflect(
            [{"tool_call_id": "c1", "success": True}], turn=0
        )
        assert decision["action"] == "continue"

    def test_failure_fallback(self):
        decision = reflect(
            [{"tool_call_id": "c1", "success": False, "error": "boom"}], turn=0
        )
        assert decision["action"] == "fallback"
        assert "boom" in decision["correction"]


class _RaisingLLM:
    async def complete(self, *args, **kwargs):
        raise RuntimeError("网络挂了")


class _JsonLLM:
    async def complete(self, *args, **kwargs):
        return LLMResponse(
            content='{"action": "fallback", "reason": "参数错误", "correction": "修正参数"}'
        )


class TestReflectWithLLM:
    def test_success_uses_rule_path(self):
        """全部成功时不调用 LLM"""
        decision = run(reflect_with_llm(
            _JsonLLM(), [{"tool_call_id": "c1", "success": True}], turn=0
        ))
        assert decision["action"] == "continue"

    def test_llm_failure_falls_back(self):
        decision = run(reflect_with_llm(
            _RaisingLLM(),
            [{"tool_call_id": "c1", "success": False, "error": "boom"}],
            turn=0,
        ))
        assert decision["action"] == "fallback"

    def test_llm_decision_marked_reflected(self):
        decision = run(reflect_with_llm(
            _JsonLLM(),
            [{"tool_call_id": "c1", "success": False, "error": "boom"}],
            turn=0,
        ))
        assert decision["action"] == "fallback"
        assert decision["reflected"] is True