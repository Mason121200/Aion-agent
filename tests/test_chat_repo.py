"""JsonChatRepo 单元测试：会话与消息持久化"""

import asyncio

from aion_agent.core.entities.message import Message
from aion_agent.storage.json_chat_repo import JsonChatRepo


def run(coro):
    return asyncio.run(coro)


class TestJsonChatRepo:
    def test_create_save_get(self):
        repo = JsonChatRepo()
        sid = run(repo.create_session("u1"))
        run(repo.save_message(Message(session_id=sid, role="user", content="你好")))
        run(repo.save_message(Message(session_id=sid, role="assistant", content="你好！")))

        history = run(repo.get_history(sid))
        assert [m.role for m in history] == ["user", "assistant"]
        assert history[-1].content == "你好！"

    def test_get_history_limit(self):
        repo = JsonChatRepo()
        sid = run(repo.create_session("u1"))
        for i in range(5):
            run(repo.save_message(Message(
                session_id=sid, role="user", content=f"第{i}条"
            )))
        history = run(repo.get_history(sid, limit=2))
        assert len(history) == 2
        assert history[0].content == "第3条"
        assert history[-1].content == "第4条"

    def test_unknown_session_empty(self):
        repo = JsonChatRepo()
        assert run(repo.get_history("nope")) == []

    def test_persistence_across_instances(self, tmp_path):
        path = str(tmp_path / "chat")
        repo1 = JsonChatRepo(persist_dir=path)
        sid = run(repo1.create_session("u1"))
        run(repo1.save_message(Message(session_id=sid, role="user", content="记住我")))

        repo2 = JsonChatRepo(persist_dir=path)
        history = run(repo2.get_history(sid))
        assert len(history) == 1
        assert history[0].content == "记住我"

    def test_list_and_delete(self):
        repo = JsonChatRepo()
        sid = run(repo.create_session("u1"))
        run(repo.create_session("u2"))
        sessions = run(repo.list_sessions("u1"))
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == sid
        assert run(repo.delete_session(sid)) is True
        assert run(repo.list_sessions("u1")) == []