"""图片上传 + 消息 images 透传 + 会话参考图注入与生成图持久化测试"""

import asyncio

from fastapi.testclient import TestClient

from aion_agent.core.ports.i_llm_client import StreamChunk
from aion_agent.ecommerce import agnes_client
from aion_agent.pipeline.cognition_pipeline import CognitionPipeline
from aion_agent.server.app import create_app
from aion_agent.server.runtime import AppRuntime
from aion_agent.storage.hash_embedder import HashEmbedder
from aion_agent.storage.in_memory_cognitive_repo import InMemoryCognitiveRepo
from aion_agent.storage.json_chat_repo import JsonChatRepo
from aion_agent.use_cases.react_chat_session import ReActChatSession


def run(coro):
    return asyncio.run(coro)


def _tiny_png():
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d49444154789c626001000000ffff03000006000557bfabd400000000"
        "49454e44ae426082"
    )


class _FakeLLM:
    """可编程假 LLM：第一轮调用 edit_product_image，第二轮输出最终回复"""

    def __init__(self):
        self.requests = []
        self._index = 0

    async def stream(self, messages, tools=None, tool_choice="auto",
                     temperature=0.7, max_tokens=4096):
        self.requests.append(list(messages))
        turns = [
            {
                "content": "我来根据参考图生成商品图。",
                "tool_calls": [{
                    "id": "c1", "type": "function",
                    "function": {
                        "name": "edit_product_image",
                        "arguments": {
                            "prompt": "把背景换成白色",
                            "image_urls": ["/uploads/a.png"],
                        },
                    },
                }],
            },
            {"content": "已生成商品图。", "tool_calls": None},
        ]
        turn = turns[min(self._index, len(turns) - 1)]
        self._index += 1
        content = turn.get("content", "")
        for i in range(0, len(content), 7):
            yield StreamChunk(content=content[i:i + 7], is_final=False)
        yield StreamChunk(
            content="", is_final=True,
            tool_calls=turn.get("tool_calls") or None,
            usage={"total_tokens": 10},
        )

    async def complete(self, messages, tools=None, tool_choice="auto",
                       temperature=0.7, max_tokens=4096):
        raise AssertionError("不应走到 complete")


# ---------------- 上传接口 ----------------

def test_upload_endpoint_and_static(tmp_path):
    rt = AppRuntime(data_dir=tmp_path / "data")
    app = create_app(rt)
    client = TestClient(app)
    resp = client.post(
        "/api/upload",
        files={"file": ("a.png", _tiny_png(), "image/png")},
    )
    assert resp.status_code == 200, resp.text
    url = resp.json()["url"]
    assert url.startswith("/uploads/up_")
    static = client.get(url)
    assert static.status_code == 200
    assert static.content == _tiny_png()


def test_upload_rejects_non_image(tmp_path):
    rt = AppRuntime(data_dir=tmp_path / "data")
    app = create_app(rt)
    client = TestClient(app)
    resp = client.post(
        "/api/upload",
        files={"file": ("a.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 400


# ---------------- 会话：参考图注入 + 生成图持久化 ----------------

def test_react_stream_reference_images_and_generated_image_persist(monkeypatch):
    monkeypatch.setattr(
        agnes_client, "generate_image",
        lambda prompt, size="1024x768", ref_images=None: "https://img.agnes/gen.png",
    )
    llm = _FakeLLM()
    chat_repo = JsonChatRepo()
    sid = run(chat_repo.create_session("u1"))
    repo = InMemoryCognitiveRepo(embedder=HashEmbedder())
    pipeline = CognitionPipeline(cognitive_repo=repo)

    session = ReActChatSession(
        llm=llm,
        cognitive_repo=repo,
        chat_repo=chat_repo,
        pipeline=pipeline,
        user_id="u1",
        session_id=sid,
        max_steps=3,
        llm_reflect_enabled=False,
    )

    async def _collect():
        return [e async for e in session.react_stream(
            "根据我上传的图生成商品图", images=["/uploads/a.png"]
        )]

    events = run(_collect())

    tool_results = [e for e in events if e.get("type") == "tool_result"]
    assert tool_results
    assert tool_results[0]["tool_call"]["data"]["image_url"] == "https://img.agnes/gen.png"

    # LLM 上下文里能看到参考图 URL（文本注入，供工具调用传参）
    user_msgs = [
        m for m in llm.requests[0]
        if m.get("role") == "user" and "/uploads/a.png" in str(m.get("content"))
    ]
    assert user_msgs, "LLM 上下文应包含参考图 URL"

    # 用户消息带 images 落库；生成图随助手回复一起持久化（会话重开可回放）
    history = run(chat_repo.get_history(sid))
    roles = [m.role for m in history]
    assert roles == ["user", "assistant"]
    assert history[0].images == ["/uploads/a.png"]
    assert history[1].images == ["https://img.agnes/gen.png"]
    assert "已生成商品图" in history[1].content
