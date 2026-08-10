"""学习闭环：agent 主动参与（教学 + 数据采集）测试"""

import asyncio

from aion_agent.storage.hash_embedder import HashEmbedder
from aion_agent.storage.in_memory_cognitive_repo import InMemoryCognitiveRepo
from aion_agent.storage.json_chat_repo import JsonChatRepo
from aion_agent.use_cases.react_chat_session import ReActChatSession
from tests.test_react_loop import FakeAsyncLLM


def _repo(tmp_path):
    from aion_agent.study.study_repo import JsonStudyRepo
    return JsonStudyRepo(persist_dir=str(tmp_path))


def _plan(repo):
    return repo.create_plan(
        title="英语四级", subject="英语", goal="过四级",
        acceptance=["词汇 4500"],
        milestones=[{"title": "打基础"}],
    )["plan"]


async def _collect(agen):
    return [event async for event in agen]


def run(agen):
    return asyncio.run(_collect(agen))


def test_study_hint_in_system_prompt(tmp_path):
    """system prompt 包含学习主动参与规范（教学者 + 数据采集者）"""
    study = _repo(tmp_path)
    llm = FakeAsyncLLM([{"content": "好的。"}])
    sess = ReActChatSession(
        llm=llm,
        cognitive_repo=InMemoryCognitiveRepo(embedder=HashEmbedder()),
        chat_repo=JsonChatRepo(persist_dir=str(tmp_path / "chat")),
        study_repo=study,
        user_id="u1",
        llm_reflect_enabled=False,
    )
    events = list(run(sess.react_stream("你好")))
    assert any(e.get("type") == "final" for e in events)
    system_content = llm.requests[0][0]["content"]
    assert "主动的教学者与数据采集者" in system_content
    assert "log_study_session" in system_content
    assert "list_reviews" in system_content
    assert "review_checkin" in system_content


def test_study_due_review_injected_into_context(tmp_path):
    """存在到期复习项时，动态上下文注入【到期复习】，供 agent 主动发起复习"""
    study = _repo(tmp_path)
    plan = _plan(study)
    study.add_mistake(plan_id=plan["plan_id"], content="听力同义替换", knowledge_point="听力")
    llm = FakeAsyncLLM([{"content": "我来帮你复习到期的内容。"}])
    sess = ReActChatSession(
        llm=llm,
        cognitive_repo=InMemoryCognitiveRepo(embedder=HashEmbedder()),
        chat_repo=JsonChatRepo(persist_dir=str(tmp_path / "chat")),
        study_repo=study,
        user_id="u1",
        llm_reflect_enabled=False,
    )
    events = list(run(sess.react_stream("你好，今天学什么")))
    assert any(e.get("type") == "final" for e in events)
    all_text = "\n".join(
        (m.get("content") or "") for m in llm.requests[0]
    )
    assert "到期复习" in all_text
    assert "听力同义替换" in all_text


def test_study_stagnant_injected_into_context(tmp_path):
    """计划停滞 3 天以上时，动态上下文注入【学习停滞】"""
    from datetime import datetime, timedelta
    study = _repo(tmp_path)
    plan = _plan(study)
    study.log_session(subject="英语", minutes=30, plan_id=plan["plan_id"])
    ses = list(study._data["sessions"].values())[0]
    ses["created_at"] = (datetime.now() - timedelta(days=5)).isoformat()
    study._save()
    llm = FakeAsyncLLM([{"content": "好的。"}])
    sess = ReActChatSession(
        llm=llm,
        cognitive_repo=InMemoryCognitiveRepo(embedder=HashEmbedder()),
        chat_repo=JsonChatRepo(persist_dir=str(tmp_path / "chat")),
        study_repo=study,
        user_id="u1",
        llm_reflect_enabled=False,
    )
    events = list(run(sess.react_stream("你好")))
    assert any(e.get("type") == "final" for e in events)
    all_text = "\n".join(
        (m.get("content") or "") for m in llm.requests[0]
    )
    assert "学习停滞" in all_text
    assert "停滞5天" in all_text


def test_study_pending_review_injected_into_context(tmp_path):
    """调整实验到期出报告（pending_review）时，动态上下文注入【待裁决调整】，
    供 LLM 在下一次对话中调用 confirm_adjustment 裁决（决策权归 LLM）"""
    from datetime import datetime, timedelta
    study = _repo(tmp_path)
    plan = _plan(study)
    study.record_adjustment(
        plan_id=plan["plan_id"], content="改为词根拆解背词", reason="正确率下滑", window_days=1,
    )
    adj = study.get_plan(plan["plan_id"])["adjustments"][0]
    adj["ts"] = (datetime.now() - timedelta(days=2)).isoformat()
    study._save()
    results = study.evaluate_adjustments(plan["plan_id"])
    assert len(results) == 1
    assert results[0]["conclusion"] == "pending_review"
    assert results[0]["report"]["improved_count"] >= 0
    llm = FakeAsyncLLM([{"content": "我看看对比报告再裁决。"}])
    sess = ReActChatSession(
        llm=llm,
        cognitive_repo=InMemoryCognitiveRepo(embedder=HashEmbedder()),
        chat_repo=JsonChatRepo(persist_dir=str(tmp_path / "chat")),
        study_repo=study,
        user_id="u1",
        llm_reflect_enabled=False,
    )
    events = list(run(sess.react_stream("继续学习")))
    assert any(e.get("type") == "final" for e in events)
    all_text = "\n".join((m.get("content") or "") for m in llm.requests[0])
    assert "待裁决调整" in all_text
    assert "词根拆解背词" in all_text
