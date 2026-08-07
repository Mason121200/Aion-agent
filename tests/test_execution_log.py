"""执行日志测试：JSONL 落盘 / 过滤查询 / 内存兜底"""

import sys

sys.path.insert(0, ".")

from aion_agent.storage.execution_log import JsonExecutionLog  # noqa: E402


def test_append_and_query_persisted(tmp_path):
    log = JsonExecutionLog(persist_dir=str(tmp_path))
    log.append(session_id="s1", event_type="user_message", content="你好")
    log.append(session_id="s1", event_type="tool_call", content="calculator", meta={"args": {"expression": "1+1"}})
    log.append(session_id="s2", event_type="user_message", content="其他会话")
    assert (tmp_path / "execution_log.jsonl").exists()

    events = log.query(session_id="s1")
    assert len(events) == 2
    assert all(e["session_id"] == "s1" for e in events)

    tool_events = log.query(session_id="s1", event_type="tool_call")
    assert len(tool_events) == 1
    assert tool_events[0]["meta"]["args"]["expression"] == "1+1"

    all_events = log.query()
    assert len(all_events) == 3


def test_reload_from_disk(tmp_path):
    log = JsonExecutionLog(persist_dir=str(tmp_path))
    log.append(session_id="s1", event_type="error", content="boom")
    log2 = JsonExecutionLog(persist_dir=str(tmp_path))
    events = log2.query()
    assert len(events) == 1
    assert events[0]["type"] == "error"


def test_memory_fallback_without_dir():
    log = JsonExecutionLog(persist_dir=None)
    log.append(session_id="s1", event_type="user_message", content="x")
    events = log.query()
    assert len(events) == 1
    assert events[0]["session_id"] == "s1"


def test_query_limit_and_ordering(tmp_path):
    log = JsonExecutionLog(persist_dir=str(tmp_path))
    for i in range(10):
        log.append(session_id="s1", event_type="user_message", content=str(i))
    events = log.query(session_id="s1", limit=3)
    assert len(events) == 3
    assert events[0]["content"] == "9"  # 最新在前
