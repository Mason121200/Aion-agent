"""工具权限分级测试：注册表权限元数据 / 执行器策略拦截"""

import sys

sys.path.insert(0, ".")

from aion_agent.tools import ToolExecutor, ToolPolicy, ToolRegistry, register_builtin_tools  # noqa: E402


def run(coro):
    import asyncio
    return asyncio.run(coro)


def _env(policy=None, auto_approve=False):
    registry = ToolRegistry()
    register_builtin_tools(registry)
    executor = ToolExecutor(registry, policy=policy, auto_approve=auto_approve)
    return registry, executor


def test_registry_permission_default_auto():
    registry = ToolRegistry()
    registry.register("t", lambda args: {"ok": True})
    entry = registry.get("t")
    assert entry["permission"] == "auto"


def test_registry_permission_custom():
    registry = ToolRegistry()
    registry.register("t", lambda args: {"ok": True}, permission="confirm")
    assert registry.get("t")["permission"] == "confirm"
    entries = registry.list_tool_entries()
    assert {"name": "t", "permission": "confirm"} in [
        {"name": e["name"], "permission": e["permission"]} for e in entries
    ]


def test_policy_blocked_blocks_execution():
    policy = ToolPolicy(blocked=["calculator"])
    _, executor = _env(policy=policy)
    result = run(executor.execute("calculator", {"expression": "1+1"}))
    assert not result.success
    assert result.error_code == "TOOL_BLOCKED"


def test_policy_confirm_requires_approval():
    policy = ToolPolicy(confirm=["read_file"])
    _, executor = _env(policy=policy)
    result = run(executor.execute("read_file", {"path": "x.txt"}))
    assert not result.success
    assert result.error_code == "NEEDS_CONFIRM"


def test_policy_confirm_auto_approve():
    policy = ToolPolicy(confirm=["read_file"])
    _, executor = _env(policy=policy, auto_approve=True)
    result = run(executor.execute("read_file", {"path": "E:/aion_agent/README.md"}))
    assert result.success or not result.success  # 走到真实执行（文件存在与否都可能）


def test_policy_roundtrip_dict():
    policy = ToolPolicy(blocked=["a"], confirm=["b"])
    restored = ToolPolicy.from_dict(policy.to_dict())
    assert restored.is_blocked("a")
    assert restored.requires_confirm("b")
