"""电商工具测试：Agnes 客户端（mock 网络）+ 工具注册与调用 + catalog 接入"""

import sys
import pytest

sys.path.insert(0, ".")

from aion_agent.ecommerce import agnes_client  # noqa: E402
from aion_agent.ecommerce.ecommerce_tools import register_ecommerce_tools  # noqa: E402
from aion_agent.skills import build_default_skills  # noqa: E402
from aion_agent.tools import ToolRegistry  # noqa: E402


# ---------------- 客户端：图片 ----------------

def _fake_resp(status_code=200, json_data=None):
    class Resp:
        def __init__(self):
            self.status_code = status_code
            self.text = "mock"
        def json(self):
            return json_data if json_data is not None else {}
    return Resp()


def test_generate_image_success(monkeypatch):
    monkeypatch.setenv("AGNES_API_KEY", "test_key")
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["payload"] = json
        captured["headers"] = headers
        return _fake_resp(200, {"data": [{"url": "https://img.example/a.png"}]})

    monkeypatch.setattr(agnes_client.requests, "post", fake_post)
    url = agnes_client.generate_image("白色保温杯", size="1024x768")
    assert url == "https://img.example/a.png"
    assert captured["url"] == "https://apihub.agnes-ai.com/v1/images/generations"
    assert captured["payload"]["model"] == "agnes-image-2.1-flash"
    assert captured["payload"]["extra_body"]["response_format"] == "url"
    assert captured["headers"]["Authorization"] == "Bearer test_key"


def test_generate_image_with_ref_images(monkeypatch):
    monkeypatch.setenv("AGNES_API_KEY", "test_key")
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["payload"] = json
        return _fake_resp(200, {"data": [{"url": "u"}]})

    monkeypatch.setattr(agnes_client.requests, "post", fake_post)
    agnes_client.generate_image("换白色背景", ref_images=["https://x/1.jpg"])
    assert captured["payload"]["extra_body"]["image"] == ["https://x/1.jpg"]


def test_generate_image_missing_key(monkeypatch):
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    with pytest.raises(agnes_client.AgnesError, match="AGNES_API_KEY"):
        agnes_client.generate_image("x")


def test_generate_image_http_error(monkeypatch):
    monkeypatch.setenv("AGNES_API_KEY", "test_key")
    monkeypatch.setattr(
        agnes_client.requests, "post",
        lambda *a, **k: _fake_resp(401, {"error": "unauthorized"}),
    )
    with pytest.raises(agnes_client.AgnesError, match="401"):
        agnes_client.generate_image("x")


def test_generate_image_business_error(monkeypatch):
    monkeypatch.setenv("AGNES_API_KEY", "test_key")
    monkeypatch.setattr(
        agnes_client.requests, "post",
        lambda *a, **k: _fake_resp(200, {"error": {"message": "模型不存在"}}),
    )
    with pytest.raises(agnes_client.AgnesError, match="模型不存在"):
        agnes_client.generate_image("x")


# ---------------- 客户端：视频 ----------------

def test_submit_video(monkeypatch):
    monkeypatch.setenv("AGNES_API_KEY", "test_key")
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["payload"] = json
        return _fake_resp(200, {"task_id": "t1", "video_id": "v1"})

    monkeypatch.setattr(agnes_client.requests, "post", fake_post)
    result = agnes_client.submit_video("360 度展示", image_urls=["https://x/1.jpg"])
    assert result == {"task_id": "t1", "video_id": "v1"}
    assert captured["url"].endswith("/videos")
    assert captured["payload"]["model"] == "agnes-video-v2.0"


def test_get_video_status(monkeypatch):
    monkeypatch.setenv("AGNES_API_KEY", "test_key")
    captured = {}

    def fake_get(url, headers, timeout):
        captured["url"] = url
        return _fake_resp(200, {"status": "completed", "url": "https://v/mp4"})

    monkeypatch.setattr(agnes_client.requests, "get", fake_get)
    result = agnes_client.get_video_status("v1")
    assert result["status"] == "completed"
    assert captured["url"] == "https://apihub.agnes-ai.com/agnesapi?video_id=v1"


# ---------------- 本地参考图 → base64 Data URI ----------------

_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c626001000000ffff03000006000557bfabd400000000"
    "49454e44ae426082"
)


def test_resolve_upload_path_to_data_uri(monkeypatch, tmp_path):
    import base64

    uploads = tmp_path / "server" / "uploads"
    uploads.mkdir(parents=True)
    (uploads / "a.png").write_bytes(_TINY_PNG)
    monkeypatch.setenv("AION_DATA_DIR", str(tmp_path))
    expected = "data:image/png;base64," + base64.b64encode(_TINY_PNG).decode("ascii")
    assert agnes_client._resolve_image_ref("/uploads/a.png") == expected


def test_resolve_localhost_url_to_data_uri(monkeypatch, tmp_path):
    uploads = tmp_path / "server" / "uploads"
    uploads.mkdir(parents=True)
    (uploads / "a.png").write_bytes(_TINY_PNG)
    monkeypatch.setenv("AION_DATA_DIR", str(tmp_path))
    uri = agnes_client._resolve_image_ref("http://127.0.0.1:8010/uploads/a.png")
    assert uri.startswith("data:image/png;base64,")


def test_resolve_public_url_passthrough():
    assert agnes_client._resolve_image_ref("https://img.example.com/a.png") == "https://img.example.com/a.png"


def test_resolve_data_uri_passthrough():
    uri = "data:image/png;base64,AAAA"
    assert agnes_client._resolve_image_ref(uri) == uri


def test_resolve_missing_local_file_passthrough(monkeypatch, tmp_path):
    monkeypatch.setenv("AION_DATA_DIR", str(tmp_path))
    assert agnes_client._resolve_image_ref("/uploads/nope.png") == "/uploads/nope.png"


def test_generate_image_converts_local_ref_to_data_uri(monkeypatch, tmp_path):
    import base64

    monkeypatch.setenv("AGNES_API_KEY", "test_key")
    uploads = tmp_path / "server" / "uploads"
    uploads.mkdir(parents=True)
    (uploads / "a.png").write_bytes(_TINY_PNG)
    monkeypatch.setenv("AION_DATA_DIR", str(tmp_path))
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["payload"] = json
        return _fake_resp(200, {"data": [{"url": "u"}]})

    monkeypatch.setattr(agnes_client.requests, "post", fake_post)
    agnes_client.generate_image("换白色背景", ref_images=["/uploads/a.png"])
    expected = "data:image/png;base64," + base64.b64encode(_TINY_PNG).decode("ascii")
    assert captured["payload"]["extra_body"]["image"] == [expected]


# ---------------- 工具层 ----------------

def _registry():
    reg = ToolRegistry()
    register_ecommerce_tools(reg)
    return reg


def test_register_ecommerce_tools():
    reg = _registry()
    names = set(reg._tools.keys())
    assert names == {
        "generate_product_image", "edit_product_image",
        "submit_product_video", "generation_status",
    }


def test_ecommerce_tools_timeout_override():
    reg = _registry()
    assert reg.get("generate_product_image")["timeout_seconds"] == 300
    assert reg.get("edit_product_image")["timeout_seconds"] == 300
    assert reg.get("submit_product_video")["timeout_seconds"] == 180
    assert reg.get("generation_status")["timeout_seconds"] is None


def test_generate_product_image_handler(monkeypatch):
    monkeypatch.setattr(
        agnes_client, "generate_image", lambda prompt, size="1024x768", ref_images=None: "https://img/a.png"
    )
    reg = _registry()
    handler = reg.get("generate_product_image")["func"]
    res = handler({"prompt": "白色保温杯", "size": "1024x768"})
    assert res["image_url"] == "https://img/a.png"
    assert "白色保温杯" in res["content"] or "已生成" in res["content"]


def test_edit_product_image_handler(monkeypatch):
    seen = {}
    def fake(prompt, size="1024x768", ref_images=None):
        seen["prompt"] = prompt
        seen["ref"] = ref_images
        return "https://img/b.png"
    monkeypatch.setattr(agnes_client, "generate_image", fake)
    reg = _registry()
    handler = reg.get("edit_product_image")["func"]
    res = handler({"prompt": "换成白色背景", "image_url": "https://x/1.jpg"})
    assert res["image_url"] == "https://img/b.png"
    assert seen["ref"] == ["https://x/1.jpg"]


def test_edit_product_image_multi_urls(monkeypatch):
    seen = {}

    def fake(prompt, size="1024x768", ref_images=None):
        seen["ref"] = ref_images
        return "https://img/m.png"

    monkeypatch.setattr(agnes_client, "generate_image", fake)
    reg = _registry()
    handler = reg.get("edit_product_image")["func"]
    res = handler({"prompt": "合成多图", "image_urls": ["https://x/1.jpg", "https://x/2.jpg"]})
    assert res["mode"] == "multi_image_to_image"
    assert res["image_url"] == "https://img/m.png"
    assert seen["ref"] == ["https://x/1.jpg", "https://x/2.jpg"]


def test_edit_product_image_multi_comma_string(monkeypatch):
    seen = {}

    def fake(prompt, size="1024x768", ref_images=None):
        seen["ref"] = ref_images
        return "https://img/m.png"

    monkeypatch.setattr(agnes_client, "generate_image", fake)
    reg = _registry()
    handler = reg.get("edit_product_image")["func"]
    handler({"prompt": "改背景", "image_urls": "https://x/1.jpg, https://x/2.jpg"})
    assert seen["ref"] == ["https://x/1.jpg", "https://x/2.jpg"]


def test_handler_missing_args():
    reg = _registry()
    with pytest.raises(ValueError, match="prompt"):
        reg.get("generate_product_image")["func"]({})
    with pytest.raises(ValueError, match="image_url"):
        reg.get("edit_product_image")["func"]({"prompt": "改背景"})


def test_catalog_includes_ecommerce():
    skills = build_default_skills()
    names = [s.name for s in skills]
    assert "ecommerce" in names
    ecom = next(s for s in skills if s.name == "ecommerce")
    assert set(ecom.tools) == {
        "generate_product_image", "edit_product_image",
        "submit_product_video", "generation_status",
    }
