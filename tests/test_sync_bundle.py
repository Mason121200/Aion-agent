"""跨设备数据同步测试：设备标识 / Bundle 打包 / 合并去重 / 远程拉取"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, ".")

from aion_agent.sync import (  # noqa: E402
    BUNDLE_SCHEMA_VERSION,
    build_bundle,
    get_device_id,
    merge_bundle,
    pull_bundle,
)
from aion_agent.sync.bundle import _merge_message_list  # noqa: E402


def _write(data_dir: Path, name: str, data) -> Path:
    p = data_dir / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def _read(data_dir: Path, name: str):
    return json.loads((data_dir / name).read_text(encoding="utf-8"))


def test_get_device_id_persists(tmp_path):
    first = get_device_id(tmp_path)
    second = get_device_id(tmp_path)
    assert first == second
    assert first.startswith("device_")
    saved = json.loads((tmp_path / "device.json").read_text(encoding="utf-8"))
    assert saved["device_id"] == first


def test_build_bundle_includes_files(tmp_path):
    _write(tmp_path, "chat.json", {"s1": {"user_id": "u1", "messages": []}})
    _write(tmp_path, "cognitive.json", {"triples": []})
    bundle = build_bundle(tmp_path)
    assert bundle["schema"] == BUNDLE_SCHEMA_VERSION
    assert bundle["device_id"]
    assert "chat.json" in bundle["files"]
    assert "cognitive.json" in bundle["files"]
    assert bundle["files"]["chat.json"]["s1"]["user_id"] == "u1"


def test_merge_bundle_appends_new_records(tmp_path):
    _write(tmp_path, "cognitive.json", {"triples": [{"rel_id": "r1", "obj": "a"}]})
    incoming = {
        "schema": "1.0",
        "device_id": "device_remote",
        "exported_at": "2026-01-01T00:00:00",
        "files": {
            "cognitive.json": {
                "triples": [{"rel_id": "r2", "obj": "b"}],
                "states": [],
                "notes": [],
                "correction_log": [],
            }
        },
    }
    counts = merge_bundle(tmp_path, incoming)
    assert counts["total"] == 1
    data = _read(tmp_path, "cognitive.json")
    rel_ids = {t["rel_id"] for t in data["triples"]}
    assert rel_ids == {"r1", "r2"}


def test_merge_bundle_new_value_wins(tmp_path):
    _write(
        tmp_path,
        "skills.json",
        {"basic": {"name": "basic", "updated_at": "2026-01-01T00:00:00", "status": "old"}},
    )
    incoming = {
        "files": {
            "skills.json": {
                "basic": {"name": "basic", "updated_at": "2026-01-02T00:00:00", "status": "new"},
            }
        }
    }
    merge_bundle(tmp_path, incoming)
    data = _read(tmp_path, "skills.json")
    assert data["basic"]["status"] == "new"


def test_merge_bundle_keeps_newer_existing(tmp_path):
    _write(
        tmp_path,
        "skills.json",
        {"basic": {"name": "basic", "updated_at": "2026-01-02T00:00:00", "status": "new"}},
    )
    incoming = {
        "files": {
            "skills.json": {
                "basic": {"name": "basic", "updated_at": "2026-01-01T00:00:00", "status": "old"},
            }
        }
    }
    merge_bundle(tmp_path, incoming)
    data = _read(tmp_path, "skills.json")
    assert data["basic"]["status"] == "new"


def test_merge_bundle_adds_new_session(tmp_path):
    _write(tmp_path, "chat.json", {"s1": {"user_id": "u1", "messages": []}})
    incoming = {
        "files": {
            "chat.json": {
                "s1": {"user_id": "u1", "created_at": "2026-01-01T00:00:00", "messages": []},
                "s2": {"user_id": "u1", "created_at": "2026-01-02T00:00:00", "messages": []},
            }
        }
    }
    merge_bundle(tmp_path, incoming)
    data = _read(tmp_path, "chat.json")
    assert set(data) == {"s1", "s2"}


def test_merge_message_list_dedup():
    existing = [
        {"id": "m1", "content": "hi", "created_at": "2026-01-01T00:00:00"},
        {"id": "m2", "content": "hey", "created_at": "2026-01-02T00:00:00"},
    ]
    incoming = [
        {"id": "m1", "content": "hi", "created_at": "2026-01-01T00:00:00"},
        {"id": "m3", "content": "yo", "created_at": "2026-01-03T00:00:00"},
    ]
    merged = _merge_message_list(existing, incoming)
    ids = [m["id"] for m in merged]
    assert ids == ["m1", "m2", "m3"]


def test_pull_bundle_fetches_remote():
    payload = {
        "schema": "1.0",
        "device_id": "device_remote",
        "exported_at": "2026-01-01T00:00:00",
        "files": {"chat.json": {"s1": {"messages": []}}},
    }

    class FakeResp:
        def read(self):
            return json.dumps(payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with patch("aion_agent.sync.bundle.urllib.request.urlopen", return_value=FakeResp()):
        bundle = pull_bundle("http://192.168.1.10:8010")
    assert bundle["device_id"] == "device_remote"
    assert "chat.json" in bundle["files"]


def test_pull_bundle_rejects_bad_url():
    with pytest.raises(ValueError):
        pull_bundle("ftp://x/api/sync/export")
