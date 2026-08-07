"""工具层单元测试：注册表 / 执行器 / 内置工具"""

import asyncio
import time

from aion_agent.core.ports.i_tool_registry import IToolRegistry
from aion_agent.tools import ToolExecutor, ToolRegistry, register_builtin_tools


def run(coro):
    return asyncio.run(coro)


def _env():
    registry = ToolRegistry()
    register_builtin_tools(registry)
    executor = ToolExecutor(registry)
    return registry, executor


class TestRegistry:
    def test_list_tools_schemas(self):
        registry, _ = _env()
        tools = registry.list_tools()
        names = {t["function"]["name"] for t in tools}
        assert names == {
            "get_current_time", "calculator", "read_file",
            "file_write", "file_list", "web_fetch", "shell",
        }

    def test_get_and_is_registered(self):
        registry, _ = _env()
        assert registry.is_registered("calculator")
        assert registry.get("calculator")["func"] is not None

    def test_unregister(self):
        registry = ToolRegistry()
        registry.register("x", lambda args: "ok", schema={"type": "function"})
        assert registry.unregister("x") is True
        assert not registry.is_registered("x")


class TestExecutor:
    def test_calculator(self):
        _, executor = _env()
        result = run(executor.execute("calculator", {"expression": "2 + 3 * 4"}))
        assert result.success
        assert "14" in result.data["content"]

    def test_calculator_rejects_dangerous(self):
        _, executor = _env()
        result = run(executor.execute("calculator", {"expression": "eval('1')"}))
        assert not result.success
        assert "禁止" in result.error

    def test_get_current_time(self):
        _, executor = _env()
        result = run(executor.execute("get_current_time", {}))
        assert result.success
        assert "当前时间" in result.data["content"]

    def test_read_file_missing(self):
        _, executor = _env()
        result = run(executor.execute("read_file", {"path": "Z:/不存在.txt"}))
        assert not result.success
        assert "文件不存在" in result.error

    def test_unknown_tool(self):
        _, executor = _env()
        result = run(executor.execute("no_such_tool", {}))
        assert not result.success
        assert result.error_code == "TOOL_NOT_FOUND"

    def test_timeout(self):
        registry = ToolRegistry()
        registry.register(
            "slow", lambda args: time.sleep(1), schema={"type": "function"}
        )
        executor = ToolExecutor(registry)
        result = run(executor.execute("slow", {}, timeout_seconds=0.5))
        assert not result.success
        assert result.error_code == "TIMEOUT"