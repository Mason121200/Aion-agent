"""AgentState 与 Note 实体单元测试"""

from datetime import datetime, timedelta

from aion_agent.core.entities.agent_state import AgentState
from aion_agent.core.entities.note import Note, NoteStatus, NoteType


class TestAgentState:
    def test_defaults(self):
        s = AgentState(user_id="u1", state_type="task", state_name="running")
        assert s.is_active is True
        assert s.priority == 0
        assert s.state_id is None

    def test_release(self):
        s = AgentState(user_id="u1", state_type="task", state_name="running")
        s.release("completed")
        assert s.is_active is False
        assert s.released_reason == "completed"
        assert s.released_at is not None

    def test_expired(self):
        s = AgentState(
            user_id="u1", state_type="task", state_name="running",
            expires_at=datetime.now() - timedelta(days=1),
        )
        assert s.is_expired() is True

    def test_never_expires(self):
        s = AgentState(user_id="u1", state_type="task", state_name="running")
        assert s.is_expired() is False


class TestNote:
    def test_defaults(self):
        n = Note(user_id="u1", title="t", content="c")
        assert n.note_type == NoteType.LONG_TEXT
        assert n.status == NoteStatus.ACTIVE.value
        assert n.is_archived() is False

    def test_archive(self):
        n = Note(user_id="u1", title="t", content="c")
        n.archive()
        assert n.is_archived() is True
        assert n.status == NoteStatus.ARCHIVED.value
        assert n.archived_at is not None

    def test_string_note_type_coerced(self):
        n = Note(user_id="u1", title="t", content="c", note_type="knowledge")
        assert n.note_type == NoteType.KNOWLEDGE

    def test_generate_summary_short(self):
        n = Note(user_id="u1", title="t", content="这是短内容")
        assert n.generate_summary() == "这是短内容"
        assert n.summary == "这是短内容"

    def test_generate_summary_long(self):
        n = Note(user_id="u1", title="t", content="字" * 300)
        summary = n.generate_summary(max_length=200)
        assert summary.endswith("...")
        assert len(summary) == 203

    def test_generate_summary_section(self):
        content = "开头\n## 摘要\n这里是摘要正文\n## 其他\n继续"
        n = Note(user_id="u1", title="t", content=content)
        assert n.generate_summary() == "这里是摘要正文"

    def test_touch_updates_updated_at(self):
        n = Note(user_id="u1", title="t", content="c")
        assert n.updated_at is None
        n.touch()
        assert n.updated_at is not None

    def test_to_summary(self):
        n = Note(user_id="u1", title="第8章笔记", content="ReAct 强调推理轨迹")
        s = n.to_summary()
        assert "第8章笔记" in s
        assert "ReAct" in s