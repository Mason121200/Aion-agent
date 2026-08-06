"""CLI 数据目录解析测试（打包后持久化位置）"""

from pathlib import Path

from aion_agent.cli import _default_data_dir


def test_default_data_dir_in_home(monkeypatch):
    monkeypatch.delenv("AION_DATA_DIR", raising=False)
    assert _default_data_dir() == Path.home() / ".aion_agent"


def test_data_dir_override(monkeypatch, tmp_path):
    monkeypatch.setenv("AION_DATA_DIR", str(tmp_path / "custom"))
    assert _default_data_dir() == tmp_path / "custom"


def test_data_dir_expands_user(monkeypatch):
    monkeypatch.setenv("AION_DATA_DIR", "~/aion_test_dir")
    assert _default_data_dir() == Path("~/aion_test_dir").expanduser()
