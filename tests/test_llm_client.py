"""OpenAI 兼容客户端单元测试（本地 mock HTTP 服务）"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aion_agent.llm.openai_compatible import OpenAICompatibleClient, get_config, get_config


class _Handler(BaseHTTPRequestHandler):
    mode = "chat"
    captured_body = None
    captured_headers = None

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        _Handler.captured_body = json.loads(body.decode("utf-8"))
        _Handler.captured_headers = dict(self.headers)

        if self.mode == "error":
            body_bytes = json.dumps({"error": "bad key"}).encode("utf-8")
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body_bytes)))
            self.end_headers()
            self.wfile.write(body_bytes)
            return

        if self.mode == "stream":
            payload = (
                'data: {"choices":[{"delta":{"content":"你"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"好"}}]}\n\n'
                'data: {"choices":[{"delta":{}}]}\n\n'
                "data: [DONE]\n\n"
            )
        elif self.mode == "text-delta":
            payload = (
                'data: {"choices":[{"text":"A"}]}\n\n'
                'data: {"choices":[{"text":"B"}]}\n\n'
                "data: [DONE]\n\n"
            )
        else:
            payload = json.dumps({
                "choices": [{"message": {"content": "你好，我是助手"}}]
            })

        data = payload.encode("utf-8")
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/event-stream"
            if self.mode in ("stream", "text-delta")
            else "application/json",
        )
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    _Handler.mode = "chat"
    _Handler.captured_body = None
    _Handler.captured_headers = None
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _client(server):
    url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    return OpenAICompatibleClient(
        api_key="test-key", base_url=url, model="test-model"
    )


class TestChat:
    def test_chat_returns_content(self, server):
        out = _client(server).chat([{"role": "user", "content": "hi"}])
        assert out == "你好，我是助手"

    def test_request_payload(self, server):
        _client(server).chat([{"role": "user", "content": "hi"}])
        body = _Handler.captured_body
        assert body["model"] == "test-model"
        assert body["stream"] is False
        assert body["messages"][0]["role"] == "user"
        assert body["messages"][0]["content"] == "hi"

    def test_auth_header(self, server):
        _client(server).chat([{"role": "user", "content": "hi"}])
        assert (
            _Handler.captured_headers.get("Authorization")
            == "Bearer test-key"
        )

    def test_http_error_raises(self, server):
        _Handler.mode = "error"
        with pytest.raises(RuntimeError, match="401"):
            _client(server).chat([{"role": "user", "content": "hi"}])


class TestStreamChat:
    def test_stream_accumulates_deltas(self, server):
        _Handler.mode = "stream"
        parts = list(_client(server).stream_chat([{"role": "user", "content": "hi"}]))
        assert "".join(parts) == "你好"

    def test_stream_sets_stream_flag(self, server):
        _Handler.mode = "stream"
        list(_client(server).stream_chat([{"role": "user", "content": "hi"}]))
        assert _Handler.captured_body["stream"] is True

    def test_text_format_deltas(self, server):
        """兼容 choices[].text 流式格式（非 OpenAI 标准 delta 格式）"""
        _Handler.mode = "text-delta"
        parts = list(_client(server).stream_chat([{"role": "user", "content": "hi"}]))
        assert "".join(parts) == "AB"

    def test_stream_error_raises(self, server):
        _Handler.mode = "error"
        with pytest.raises(RuntimeError, match="401"):
            list(_client(server).stream_chat([{"role": "user", "content": "hi"}]))

class TestDefaults:
    def test_default_model_and_base_url(self):
        client = OpenAICompatibleClient(api_key="k")
        assert client.model == "deepseek-v4-flash"
        assert client.base_url == "https://api.deepseek.com/v1"

    def test_get_config_defaults(self, monkeypatch):
        for key in (
            "AION_LLM_API_KEY", "LLM_API_KEY",
            "AION_LLM_BASE_URL", "LLM_BASE_URL",
            "AION_LLM_MODEL", "LLM_MODEL",
        ):
            monkeypatch.delenv(key, raising=False)
        cfg = get_config()
        assert cfg["base_url"] == "https://api.deepseek.com/v1"
        assert cfg["model"] == "deepseek-v4-flash"
        assert cfg["api_key"] == ""
