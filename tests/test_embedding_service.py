"""LLMEmbeddingService 测试（本地 mock /embeddings 接口）"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aion_agent.llm.embedding import LLMEmbeddingService


class _EmbedHandler(BaseHTTPRequestHandler):
    captured = None

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        _EmbedHandler.captured = json.loads(self.rfile.read(length).decode("utf-8"))
        inputs = _EmbedHandler.captured["input"]
        data = [
            {"index": i, "embedding": [float(i + 1), float(i + 1) * 2]}
            for i in range(len(inputs))
        ]
        payload = json.dumps({"data": data, "model": _EmbedHandler.captured["model"]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    _EmbedHandler.captured = None
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _EmbedHandler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _service(server):
    return LLMEmbeddingService(
        api_key="test-key",
        base_url=f"http://127.0.0.1:{server.server_address[1]}/v1",
        model="test-embed",
    )


def test_embed_calls_endpoint(server):
    svc = _service(server)
    vec = svc.embed("你好")
    assert vec == [1.0, 2.0]
    assert _EmbedHandler.captured["model"] == "test-embed"
    assert _EmbedHandler.captured["input"] == ["你好"]


def test_embed_many_keeps_order(server):
    svc = _service(server)
    vecs = svc.embed_many(["a", "bb", "ccc"])
    assert vecs == [[1.0, 2.0], [2.0, 4.0], [3.0, 6.0]]


def test_embed_caches(server):
    svc = _service(server)
    v1 = svc.embed("同一句话")
    v2 = svc.embed("同一句话")
    assert v1 == v2
    # 只请求过一次
    assert _EmbedHandler.captured["input"] == ["同一句话"]


def test_is_loaded(server):
    assert _service(server).is_loaded is True
    assert LLMEmbeddingService(api_key="", base_url="x").is_loaded is False
