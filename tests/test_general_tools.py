"""通用工具测试：file_write / file_list / shell 白名单 / 敏感路径拦截"""

import asyncio
import sys

import pytest

sys.path.insert(0, ".")

from aion_agent.tools import ToolExecutor, ToolRegistry, register_builtin_tools  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def _env(allowed_roots=None, shell_permission="confirm", auto_approve=False):
    registry = ToolRegistry()
    register_builtin_tools(
        registry,
        allowed_roots=allowed_roots,
        shell_permission=shell_permission,
    )
    executor = ToolExecutor(registry, auto_approve=auto_approve)
    return registry, executor


def test_file_write_roundtrip(tmp_path):
    _, executor = _env(allowed_roots=[str(tmp_path)])
    target = tmp_path / "notes" / "a.txt"
    result = run(executor.execute("file_write", {"path": str(target), "content": "hello"}))
    assert result.success
    assert target.read_text(encoding="utf-8") == "hello"


def test_file_write_append(tmp_path):
    _, executor = _env(allowed_roots=[str(tmp_path)])
    target = tmp_path / "log.txt"
    run(executor.execute("file_write", {"path": str(target), "content": "1"}))
    result = run(executor.execute("file_write", {"path": str(target), "content": "2", "append": True}))
    assert result.success
    assert target.read_text(encoding="utf-8") == "12"


def test_file_write_blocks_sensitive(tmp_path):
    _, executor = _env()
    result = run(executor.execute("file_write", {"path": str(tmp_path / ".env"), "content": "x"}))
    assert not result.success
    assert result.error_code == "EXECUTION_ERROR"


def test_file_write_blocks_outside_allowed_root(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    _, executor = _env(allowed_roots=[str(tmp_path)])
    result = run(executor.execute("file_write", {"path": str(outside), "content": "x"}))
    assert not result.success


def test_read_file_roundtrip_and_sensitive(tmp_path):
    _, executor = _env()
    target = tmp_path / "readme.txt"
    target.write_text("hello world", encoding="utf-8")
    result = run(executor.execute("read_file", {"path": str(target)}))
    assert result.success
    assert "hello world" in result.data["content"]
    blocked = run(executor.execute("read_file", {"path": str(tmp_path / ".git" / "config")}))
    assert not blocked.success


def test_file_list(tmp_path):
    _, executor = _env(allowed_roots=[str(tmp_path)])
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    result = run(executor.execute("file_list", {"path": str(tmp_path)}))
    assert result.success
    names = [e["name"] for e in result.data["entries"]]
    assert "b.txt" in names
    assert "sub" in names


def test_shell_default_needs_confirm(tmp_path):
    _, executor = _env(allowed_roots=[str(tmp_path)])
    result = run(executor.execute("shell", {"command": "whoami"}))
    assert not result.success
    assert result.error_code == "NEEDS_CONFIRM"


def test_shell_whitelist_executes_with_auto_approve(tmp_path):
    _, executor = _env(allowed_roots=[str(tmp_path)], auto_approve=True)
    result = run(executor.execute("shell", {"command": "whoami"}))
    assert result.success
    assert result.data["exit_code"] == 0


def test_shell_blocks_dangerous_command(tmp_path):
    _, executor = _env(allowed_roots=[str(tmp_path)], auto_approve=True)
    result = run(executor.execute("shell", {"command": "rm -rf /"}))
    assert not result.success
    assert result.error_code == "EXECUTION_ERROR"


def test_shell_blocks_non_whitelist(tmp_path):
    _, executor = _env(allowed_roots=[str(tmp_path)], auto_approve=True)
    result = run(executor.execute("shell", {"command": "python -c 1"}))
    assert not result.success
    assert result.error_code == "EXECUTION_ERROR"


def test_web_fetch_rejects_non_http(tmp_path):
    _, executor = _env(allowed_roots=[str(tmp_path)])
    result = run(executor.execute("web_fetch", {"url": "file:///etc/passwd"}))
    assert not result.success
    assert "http" in result.error


def test_builtin_tools_level_and_names():
    registry, _ = _env()
    entries = {e["name"]: e for e in registry.list_tool_entries()}
    assert set(entries) == {
        "get_current_time", "calculator", "read_file",
        "file_write", "file_list", "web_fetch", "shell",
    }
    assert all(e["level"] == "builtin" for e in entries.values())
    assert entries["shell"]["permission"] == "confirm"
