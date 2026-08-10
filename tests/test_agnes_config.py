"""Agnes 生图/视频配置可换测试：环境变量覆盖模型 + 设置页保存接口 + Android 侧写入"""
# -*- coding: utf-8 -*-

import os
import sys

import pytest

sys.path.insert(0, ".")

from fastapi.testclient import TestClient  # noqa: E402

from aion_agent.ecommerce import agnes_client  # noqa: E402
from aion_agent.server.app import create_app  # noqa: E402
from aion_agent.server.runtime import AppRuntime  # noqa: E402


# ---------------- 模型环境变量覆盖 ----------------

def test_image_video_model_env_override(monkeypatch):
    monkeypatch.setenv("AGNES_API_KEY", "test_key")
    monkeypatch.setenv("AGNES_IMAGE_MODEL", "my-image-model")
    monkeypatch.setenv("AGNES_VIDEO_MODEL", "my-video-model")
    captured = {}

    def fake_http(url, headers=None, payload=None, timeout=None):
        captured["payload"] = payload
        if "generations" in url:
            return 200, '{"data": [{"url": "u"}]}'
        return 200, '{"task_id": "t", "video_id": "v"}'

    monkeypatch.setattr(agnes_client, "_http_request", fake_http)
    agnes_client.generate_image("hello")
    assert captured["payload"]["model"] == "my-image-model"
    agnes_client.submit_video("hello")
    assert captured["payload"]["model"] == "my-video-model"


def test_model_env_override_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("AGNES_API_KEY", "test_key")
    monkeypatch.delenv("AGNES_IMAGE_MODEL", raising=False)
    monkeypatch.delenv("AGNES_VIDEO_MODEL", raising=False)
    captured = {}

    def fake_http(url, headers=None, payload=None, timeout=None):
        captured["payload"] = payload
        return 200, '{"data": [{"url": "u"}]}' if "generations" in url else \
            '{"task_id": "t", "video_id": "v"}'

    monkeypatch.setattr(agnes_client, "_http_request", fake_http)
    agnes_client.generate_image("hello")
    assert captured["payload"]["model"] == "agnes-image-2.1-flash"
    agnes_client.submit_video("hello")
    assert captured["payload"]["model"] == "agnes-video-v2.0"


# ---------------- 设置页接口 ----------------

def test_config_endpoint_saves_and_health(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "pathlib.Path.home", classmethod(lambda cls: tmp_path)
    )
    for v in ("AGNES_API_KEY", "AGNES_BASE_URL", "AGNES_IMAGE_MODEL", "AGNES_VIDEO_MODEL"):
        monkeypatch.setenv(v, "")

    rt = AppRuntime(data_dir=tmp_path / "data")
    app = create_app(rt)
    client = TestClient(app)

    resp = client.post("/api/config/agnes", json={
        "api_key": "agnes-key",
        "base_url": "https://example.com/v1",
        "image_model": "img-x",
        "video_model": "vid-x",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["saved"] is True
    assert body["agnes"]["configured"] is True
    assert body["agnes"]["image_model"] == "img-x"
    assert body["agnes"]["video_model"] == "vid-x"
    assert body["agnes"]["base_url"] == "https://example.com/v1"

    env_text = (tmp_path / ".aion_agent" / ".env").read_text(encoding="utf-8")
    assert "AGNES_API_KEY=agnes-key" in env_text
    assert "AGNES_IMAGE_MODEL=img-x" in env_text
    assert "AGNES_VIDEO_MODEL=vid-x" in env_text

    health = client.get("/api/health").json()
    assert health["agnes"]["configured"] is True
    assert health["agnes"]["image_model"] == "img-x"


def test_config_endpoint_requires_key(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "pathlib.Path.home", classmethod(lambda cls: tmp_path)
    )
    rt = AppRuntime(data_dir=tmp_path / "data")
    app = create_app(rt)
    client = TestClient(app)
    resp = client.post("/api/config/agnes", json={"api_key": ""})
    assert resp.status_code == 400


# ---------------- Android 侧写入 ----------------

def test_set_agnes_config_local_server(monkeypatch, tmp_path):
    from aion_agent.server import local_server

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    env = data_dir / ".env"
    env.write_text("AION_LLM_API_KEY=llm-old\n", encoding="utf-8")

    rt = AppRuntime(data_dir=data_dir)
    monkeypatch.setattr(local_server, "_runtime", rt)
    for v in ("AGNES_API_KEY", "AGNES_BASE_URL", "AGNES_IMAGE_MODEL", "AGNES_VIDEO_MODEL"):
        monkeypatch.setenv(v, "")

    ok = local_server.set_agnes_config("agnes-key", "", "img-new", "vid-new")
    assert ok is True
    text = env.read_text(encoding="utf-8")
    assert "AGNES_API_KEY=agnes-key" in text
    assert "AGNES_IMAGE_MODEL=img-new" in text
    assert "AGNES_VIDEO_MODEL=vid-new" in text
    assert "AION_LLM_API_KEY=llm-old" in text  # 保留已有 LLM key
