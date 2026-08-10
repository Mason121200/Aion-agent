"""学习闭环（P0）测试：验收 / 测验 / 错题 / 复习 / 调整实验 / 分析 / 完成判定"""

from datetime import datetime, timedelta

from aion_agent.study.study_repo import JsonStudyRepo
from aion_agent.tools.registry import ToolRegistry
from aion_agent.tools.study_tools import register_study_tools


def _repo(tmp_path):
    return JsonStudyRepo(persist_dir=str(tmp_path))


def _plan(repo, title="英语四级"):
    return repo.create_plan(
        title=title, subject="英语", goal="三个月通过四级",
        acceptance=["词汇量 4500", "听力真题 60% 以上", "阅读真题 70% 以上"],
        review_strategy="每章学完隔 1/3/7 天复习",
        milestones=[
            {"title": "打基础", "due_date": "2026-09-30"},
            {"title": "刷真题", "due_date": "2026-11-30"},
        ],
        daily_minutes=45,
    )["plan"]


def _all_review_items(repo, plan_id):
    return [
        it for it in repo._data["review_items"].values()
        if it.get("plan_id") == plan_id
    ]


def test_create_plan_with_acceptance_and_review_strategy(tmp_path):
    repo = _repo(tmp_path)
    plan = _plan(repo)
    assert len(plan["acceptance"]) == 3
    assert all(a["done"] is False for a in plan["acceptance"])
    assert plan["review_strategy"] == "每章学完隔 1/3/7 天复习"
    assert plan["adjustments"] == []


def test_test_log_weak_points_enter_review_queue(tmp_path):
    repo = _repo(tmp_path)
    plan = _plan(repo)
    test = repo.add_test(
        plan_id=plan["plan_id"], title="词汇测验", score=5, total=10,
        knowledge_points=["四级词汇"],
    )
    assert test["accuracy"] == 50
    items = _all_review_items(repo, plan["plan_id"])
    assert len(items) == 1
    assert items[0]["source_type"] == "knowledge_point"
    assert "四级词汇" in items[0]["content"]

    ok = repo.add_test(
        plan_id=plan["plan_id"], title="词汇测验二", score=9, total=10,
        knowledge_points=["四级词汇"],
    )
    assert ok["accuracy"] == 90
    # 正确率达标不新增复习项
    assert len(_all_review_items(repo, plan["plan_id"])) == 1


def test_mistake_auto_enters_review_queue(tmp_path):
    repo = _repo(tmp_path)
    plan = _plan(repo)
    mistake = repo.add_mistake(
        plan_id=plan["plan_id"], content="过去完成时用错", reason="概念不清",
        knowledge_point="时态",
    )
    items = _all_review_items(repo, plan["plan_id"])
    assert len(items) == 1
    assert items[0]["source_type"] == "mistake"
    assert items[0]["source_id"] == mistake["mistake_id"]
    assert repo.list_mistakes(plan_id=plan["plan_id"])[0]["content"] == "过去完成时用错"


def test_milestone_done_drives_material_review(tmp_path):
    repo = _repo(tmp_path)
    plan = _plan(repo)
    ms = plan["milestones"][0]
    repo.complete_milestone(plan["plan_id"], ms["milestone_id"])
    items = _all_review_items(repo, plan["plan_id"])
    assert any(it["source_type"] == "material" and it["source_id"] == ms["milestone_id"] for it in items)
    # 幂等：重复调度不重复入队
    repo.schedule_reviews(plan["plan_id"])
    assert len(_all_review_items(repo, plan["plan_id"])) == len(items)


def test_review_forgetting_curve_interval(tmp_path):
    repo = _repo(tmp_path)
    plan = _plan(repo)
    ms = plan["milestones"][0]
    repo.complete_milestone(plan["plan_id"], ms["milestone_id"])
    item = _all_review_items(repo, plan["plan_id"])[0]

    # 首次成功：间隔 1 -> 2
    item = repo.review_checkin(plan_id=plan["plan_id"], review_item_id=item["review_item_id"], correct=True)
    assert item["interval_days"] == 2
    assert item["streak"] == 1
    # 再次成功：2 -> 4
    item = repo.review_checkin(plan_id=plan["plan_id"], review_item_id=item["review_item_id"], correct=True)
    assert item["interval_days"] == 4
    # 失败：重置为 1
    item = repo.review_checkin(plan_id=plan["plan_id"], review_item_id=item["review_item_id"], correct=False)
    assert item["interval_days"] == 1
    assert item["streak"] == 0


def test_review_resolved_after_three_streaks(tmp_path):
    repo = _repo(tmp_path)
    plan = _plan(repo)
    mistake = repo.add_mistake(plan_id=plan["plan_id"], content="错题X", knowledge_point="K")
    item = _all_review_items(repo, plan["plan_id"])[0]
    for _ in range(3):
        item = repo.review_checkin(plan_id=plan["plan_id"], review_item_id=item["review_item_id"], correct=True)
    assert item["resolved"] is True
    assert repo.list_mistakes(plan_id=plan["plan_id"]) == []  # 错题已解决


def test_adjustment_experiment_baseline_and_evaluation(tmp_path):
    repo = _repo(tmp_path)
    plan = _plan(repo)
    repo.log_session(subject="英语", minutes=30, plan_id=plan["plan_id"])
    adj = repo.record_adjustment(
        plan_id=plan["plan_id"], content="改为每天早晨学习 45 分钟",
        reason="下午学习效率低", window_days=1,
    )
    assert adj["baseline"]["total_minutes"] == 30
    assert plan["decision_log"][-1]["text"].startswith("调整：")

    # 窗口未到：不结算
    assert repo.evaluate_adjustments(plan["plan_id"]) == []
    # 手动把实验时间拨到窗口前
    pid = plan["plan_id"]
    adj_in_data = repo.get_plan(pid)["adjustments"][0]
    adj_in_data["ts"] = (datetime.now() - timedelta(days=2)).isoformat()
    repo._save()
    results = repo.evaluate_adjustments(pid)
    assert len(results) == 1
    # 规则层只出报告，不裁决
    assert results[0]["conclusion"] == "pending_review"
    assert results[0]["evaluated_at"] is not None
    assert results[0]["report"]["improved_count"] >= 0
    assert any("待 LLM 裁决" in d["text"] for d in repo.get_plan(pid)["decision_log"])
    # LLM 裁决：不采纳 → ineffective + 留痕，不沉淀
    adj_id = results[0]["adjustment_id"]
    conf = repo.confirm_adjustment(pid, adj_id, accepted=False, reason="早晨时段与作息冲突")
    assert conf["confirmed"] is True
    assert repo.get_plan(pid)["adjustments"][0]["conclusion"] == "ineffective"
    assert repo.get_plan(pid)["adjustments"][0]["review_reason"] == "早晨时段与作息冲突"
    assert repo.get_plan(pid)["adjustments"][0]["reviewer"] == "llm"
    assert repo.get_profile().get("effective_methods") in (None, [])
    # 幂等：已裁决不可重复裁决
    again = repo.confirm_adjustment(pid, adj_id, accepted=True)
    assert again["already"] is True
    assert repo.get_plan(pid)["adjustments"][0]["conclusion"] == "ineffective"
    # 第二条实验：LLM 采纳 → effective + 沉淀画像
    repo.record_adjustment(
        plan_id=pid, content="错题重做+专项小练", reason="正确率下滑", window_days=1,
    )
    adj2 = repo.get_plan(pid)["adjustments"][-1]
    adj2["ts"] = (datetime.now() - timedelta(days=2)).isoformat()
    repo._save()
    results2 = repo.evaluate_adjustments(pid)
    assert len(results2) == 1
    conf2 = repo.confirm_adjustment(pid, results2[0]["adjustment_id"], accepted=True, reason="针对性强化有效")
    assert conf2["confirmed"] is True
    assert repo.get_plan(pid)["adjustments"][-1]["conclusion"] == "effective"
    assert "错题重做+专项小练" in (repo.get_profile().get("effective_methods") or [])


def test_completion_check_and_llm_confirm(tmp_path):
    repo = _repo(tmp_path)
    plan = _plan(repo)
    # 条件未满足：验收项未完成
    check = repo.completion_check(plan["plan_id"])
    assert check["eligible"] is False
    assert any("验收项" in m for m in check["missing"])

    # 补全验收项 + 里程碑 + 测验达标 + 复习清空
    pid = plan["plan_id"]
    for a in plan["acceptance"]:
        for acc in repo.get_plan(pid)["acceptance"]:
            if acc["acceptance_id"] == a["acceptance_id"] and not acc["done"]:
                acc["done"] = True
    repo._save()
    for m in repo.get_plan(pid)["milestones"]:
        repo.complete_milestone(pid, m["milestone_id"])
    repo.add_test(plan_id=pid, title="期末测验", score=8, total=10, knowledge_points=["综合"])
    # 完成里程碑带入队的复习项全部打卡正确
    for it in _all_review_items(repo, pid):
        repo.review_checkin(plan_id=pid, review_item_id=it["review_item_id"], correct=True)
    for it in _all_review_items(repo, pid):
        if not it["resolved"]:
            repo.review_checkin(plan_id=pid, review_item_id=it["review_item_id"], correct=True)

    # 先看验收报告（规则层只出数据）
    check = repo.completion_check(pid)
    assert check["eligible"] is True
    # LLM 裁决完成
    result = repo.confirm_completion(pid, accepted=True, report_note="四级通过，词汇与听力达标")
    assert result["confirmed"] is True
    assert repo.get_plan(pid)["status"] == "completed"
    assert repo.get_plan(pid)["completion"]["report"] == "四级通过，词汇与听力达标"
    assert any("完成裁决" in d["text"] for d in repo.get_plan(pid)["decision_log"])
    # 已完成的再次确认：幂等
    again = repo.confirm_completion(pid, accepted=False)
    assert again["already"] is True
    # 暂不完成路径：记录理由，保持 active
    repo2 = _repo(tmp_path / "r2")
    plan2 = _plan(repo2)
    r2 = repo2.confirm_completion(plan2["plan_id"], accepted=False, report_note="还想再巩固听力")
    assert r2["confirmed"] is False
    assert repo2.get_plan(plan2["plan_id"])["status"] == "active"
    assert any("暂不完成" in d["text"] for d in repo2.get_plan(plan2["plan_id"])["decision_log"])


def test_analyze_stagnation_detection(tmp_path):
    repo = _repo(tmp_path)
    plan = _plan(repo)
    repo.log_session(subject="英语", minutes=30, plan_id=plan["plan_id"])
    # 把学习记录时间拨到 5 天前
    session = list(repo._data["sessions"].values())[0]
    session["created_at"] = (datetime.now() - timedelta(days=5)).isoformat()
    repo._save()
    report = repo.analyze(plan_id=plan["plan_id"], days=7)
    assert report["stagnant"] is True
    assert report["stagnant_days"] == 5
    assert report["review_due"] == 0


def test_adjustment_need_triggered_on_decline(tmp_path):
    """正确率下滑且低于阈值 → 建议调整方案"""
    repo = _repo(tmp_path)
    plan = _plan(repo)
    repo.add_test(plan_id=plan["plan_id"], title="测验一", score=8, total=10)
    repo.add_test(plan_id=plan["plan_id"], title="测验二", score=5, total=10)
    adj = repo.evaluate_adjustment_need(plan["plan_id"])
    assert adj["needed"] is True
    assert "下滑" in adj["reason"] or "连续" in adj["reason"]
    assert adj["suggestion"]


def test_adjustment_need_not_triggered_when_ok(tmp_path):
    """方案有效（正确率达标）→ 无需调整，给出稳定理由"""
    repo = _repo(tmp_path)
    plan = _plan(repo)
    repo.add_test(plan_id=plan["plan_id"], title="测验一", score=9, total=10)
    adj = repo.evaluate_adjustment_need(plan["plan_id"])
    assert adj["needed"] is False
    assert "无需调整" in adj["reason"]


def test_test_log_returns_adjustment_evaluation(tmp_path):
    """test_log 工具结果包含调整评估信号，供 agent 决策"""
    from aion_agent.tools.registry import ToolRegistry
    from aion_agent.tools.study_tools import register_study_tools
    repo = _repo(tmp_path)
    plan = _plan(repo)
    registry = ToolRegistry()
    register_study_tools(registry, repo, user_id="u1")
    handler = registry.get("test_log")["func"]
    result = handler({
        "plan_id": plan["plan_id"], "title": "测验", "total": 10, "correct": 5,
    })
    assert "adjustment" in result
    assert result["adjustment"]["needed"] is True
    assert "调整评估" in result["content"]


def test_register_study_tools_has_loop_tools(tmp_path):
    repo = _repo(tmp_path)
    registry = ToolRegistry()
    register_study_tools(registry, repo, user_id="u1")
    names = set(registry._tools.keys())
    for expected in ("profile_collect", "profile_read", "test_log", "mistake_add",
                     "list_reviews", "review_checkin", "plan_adjust",
                     "study_analyze", "confirm_adjustment",
                     "plan_completion_check", "confirm_plan_completion"):
        assert expected in names
    assert len(names) == 31

    # list_reviews 返回复习项及 id，供 review_checkin 打卡
    plan = _plan(repo)
    repo.add_mistake(plan_id=plan["plan_id"], content="错题A", knowledge_point="K")
    items = repo.list_reviews(plan_id=plan["plan_id"])
    assert len(items) == 1
    assert items[0]["review_item_id"].startswith("rv_")


def test_profile_collect_and_summary(tmp_path):
    """学习画像收集：保存 + 摘要 + 认知可沉淀"""
    repo = _repo(tmp_path)
    assert repo.profile_exists() is False
    profile = repo.update_profile(
        learning_style="讲解型", preferred_method="先讲解再练习",
        best_time="早晨", focus_minutes=45,
        review_pref="喜欢被提问", motivation="目标驱动（考证）",
    )
    assert profile["learning_style"] == "讲解型"
    assert profile["focus_minutes"] == 45
    assert repo.profile_exists() is True
    summary = repo.profile_summary()
    assert "讲解型" in summary
    assert "早晨" in summary
    # 覆盖更新不丢失
    repo.update_profile(best_time="晚上")
    assert repo.get_profile()["best_time"] == "晚上"
    assert repo.get_profile()["learning_style"] == "讲解型"


def test_overview_contains_profile(tmp_path):
    repo = _repo(tmp_path)
    ov = repo.overview()
    assert "profile" in ov
    assert ov["profile_summary"] == ""
    repo.update_profile(learning_style="视觉型")
    ov = repo.overview()
    assert "视觉型" in ov["profile_summary"]


def test_settle_due_adjustments_settles_expired_only(tmp_path):
    """到期实验自动结算（幂等）：过期窗口结算，已结算跳过，写决策日志"""
    from datetime import timedelta
    repo = _repo(tmp_path)
    plan = _plan(repo)
    adj = repo.record_adjustment(
        plan_id=plan["plan_id"], content="改成案例驱动教学",
        reason="正确率下滑", window_days=1,
    )
    assert adj["conclusion"] == "pending"
    future = datetime.now() + timedelta(days=2)
    settled = repo.settle_due_adjustments(now=future)
    assert len(settled) == 1
    assert settled[0]["adjustment_id"] == adj["adjustment_id"]
    assert settled[0]["plan_title"] == plan["title"]
    assert settled[0]["conclusion"] == "pending_review"
    assert settled[0]["report"] is not None
    assert settled[0]["evaluated_at"]
    # 幂等：再次结算为空
    assert repo.settle_due_adjustments(now=future) == []
    # 决策日志留下结算记录
    plan_after = repo.get_plan(plan["plan_id"])
    assert any("调整实验结算" in d["text"] for d in plan_after["decision_log"])


def test_add_effective_method_dedup_and_summary(tmp_path):
    """有效学习方法沉淀画像：去重 + 摘要注入"""
    repo = _repo(tmp_path)
    repo.add_effective_method("案例驱动+边做边学")
    repo.add_effective_method("案例驱动+边做边学")
    profile = repo.get_profile()
    assert profile["effective_methods"] == ["案例驱动+边做边学"]
    assert "已验证有效" in repo.profile_summary()
    repo.add_effective_method("")
    assert len(repo.get_profile()["effective_methods"]) == 1
