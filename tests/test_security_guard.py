"""安全守卫测试：PathGuard 敏感路径 / 允许根 / 系统目录，CommandWhitelist"""

import sys

import pytest

sys.path.insert(0, ".")

from aion_agent.security.guard import CommandWhitelist, PathGuard  # noqa: E402


def test_read_blocks_sensitive_paths(tmp_path):
    guard = PathGuard()
    for sensitive in (".env", ".pem", ".git", "id_rsa"):
        with pytest.raises(PermissionError):
            guard.check_read(str(tmp_path / sensitive))
    ok = guard.check_read(str(tmp_path / "notes" / "a.txt"))
    assert ok.name == "a.txt"


def test_read_blocks_system_dirs():
    guard = PathGuard()
    with pytest.raises(PermissionError):
        guard.check_read("C:/Windows/System32/config")
    with pytest.raises(PermissionError):
        guard.check_read("C:/Windows/win.ini")
    with pytest.raises(PermissionError):
        guard.check_read("C:/Program Files/Common Files")


def test_write_requires_allowed_root(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    guard = PathGuard(allowed_roots=[str(tmp_path)])
    with pytest.raises(PermissionError):
        guard.check_write(str(outside))
    ok = guard.check_write(str(tmp_path / "in.txt"))
    assert ok.name == "in.txt"


def test_write_without_roots_allows_normal(tmp_path):
    guard = PathGuard()
    p = guard.check_write(str(tmp_path / "x.txt"))
    assert p.name == "x.txt"


def test_command_whitelist_allows_readonly():
    wl = CommandWhitelist()
    assert wl.check("ls -la") == "ls -la"
    assert wl.check("pwd") == "pwd"
    with pytest.raises(PermissionError):
        wl.check("rm -rf /")
    with pytest.raises(PermissionError):
        wl.check("cat /etc/passwd")
    with pytest.raises(PermissionError):
        wl.check("python -c 'print(1)'")


def test_command_whitelist_danger_patterns():
    wl = CommandWhitelist()
    with pytest.raises(PermissionError):
        wl.check("ls | grep x")
    with pytest.raises(PermissionError):
        wl.check("ls > out.txt")
    with pytest.raises(PermissionError):
        wl.check("pwd; rm -rf /")
    with pytest.raises(ValueError):
        wl.check("")
