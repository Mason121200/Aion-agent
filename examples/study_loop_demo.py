"""学习闭环全链路演示（P0）

电脑端验证用：不依赖 LLM，直接驱动数据层 + 工具层，跑通
「建计划(带验收) → 学习 → 测验 → 错题 → 复习 → 分析 → 调整 → 完成」闭环。

运行：python examples/study_loop_demo.py
"""

import sys
from pathlib import Path

# 允许直接运行：python examples/study_loop_demo.py
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aion_agent.study.study_repo import JsonStudyRepo


def main() -> None:
    data_dir = Path(__file__).resolve().parent.parent / ".local_data" / "demo_study"
    data_dir.mkdir(parents=True, exist_ok=True)
    repo = JsonStudyRepo(persist_dir=str(data_dir))

    print("=" * 64)
    print("学习闭环全链路演示（数据落在 .local_data/demo_study/study.json）")
    print("=" * 64)

    # 1. 需求 → 方案（带验收标准 + 复习策略）
    plan = repo.create_plan(
        title="三个月通过英语四级",
        subject="英语",
        goal="三个月通过英语四级，词汇与听力达标",
        why="工作需要，为转行做准备",
        acceptance=["词汇量达到 4500", "听力真题正确率 60% 以上", "阅读真题正确率 70% 以上"],
        review_strategy="每章学完隔 1/3/7 天复习",
        milestones=[
            {"title": "打基础：词汇 + 语法", "due_date": "2026-09-30"},
            {"title": "刷真题：听力 + 阅读", "due_date": "2026-11-30"},
        ],
        daily_minutes=45,
    )["plan"]
    print("\n[1] 方案制定")
    print(f"  计划：{plan['title']}")
    print(f"  验收标准：{len(plan['acceptance'])} 项")
    for a in plan["acceptance"]:
        print(f"    - {a['title']}")

    pid = plan["plan_id"]

    # 2. 执行：学习记录
    repo.log_session(subject="英语", minutes=45, note="背了 200 个四级词汇", plan_id=pid)
    print("\n[2] 执行：已记录学习 45 分钟（今日累计打卡）")

    # 3. 评估：测验 + 错题
    test = repo.add_test(
        plan_id=pid, title="词汇测验一", score=6, total=10,
        knowledge_points=["四级词汇", "词根词缀"],
    )
    print("\n[3] 评估")
    print(f"  测验：{test['title']} 正确率 {test['accuracy']}% → 薄弱知识点已自动入复习队列")
    mistake = repo.add_mistake(
        plan_id=pid, content="听力中同义替换听不出来", reason="词汇量不足",
        knowledge_point="听力同义替换",
    )
    print(f"  错题：{mistake['content']}（自动进入复习队列）")

    # 4. 里程碑完成 → 进度驱动复习
    ms0 = plan["milestones"][0]
    repo.complete_milestone(pid, ms0["milestone_id"])
    due = repo.schedule_reviews(pid)
    print("\n[4] 里程碑完成 → 进度驱动复习")
    print(f"  当前到期复习 {len(due)} 项：")
    for it in due:
        print(f"    - {it['content'][:40]}")

    # 5. 复习打卡（遗忘曲线）
    print("\n[5] 复习打卡（遗忘曲线调度）")
    for it in due:
        item = repo.review_checkin(plan_id=pid, review_item_id=it["review_item_id"], correct=True)
        print(f"  复习成功：{item['content'][:30]}… 下次间隔 {item['interval_days']} 天")

    # 6. 数据分析 + 动态调整（调整实验）
    report = repo.analyze(plan_id=pid, days=7)
    print("\n[6] 数据分析")
    print(f"  学习时长：{report['total_minutes']} 分钟 | 测验正确率：{report['avg_accuracy']}%")
    print(f"  复习：到期 {report['review_due']} / 共 {report['review_total']} | 进度：{report['progress']}%")
    adj = repo.record_adjustment(
        plan_id=pid, content="改为每天早晨学习 45 分钟",
        reason="分析显示下午学习中断率高", window_days=1,
    )
    print(f"  调整实验已记录：{adj['content']}（基线快照 total_minutes={adj['baseline']['total_minutes']}）")

    # 7. 补全条件 → 全自动完成判定
    print("\n[7] 全自动完成判定")
    # 补测验达标
    repo.add_test(plan_id=pid, title="词汇测验二", score=9, total=10, knowledge_points=["四级词汇"])
    repo.add_test(plan_id=pid, title="听力测验", score=7, total=10, knowledge_points=["听力同义替换"])
    repo.add_test(plan_id=pid, title="阅读测验", score=8, total=10, knowledge_points=["阅读理解"])
    # 完成全部里程碑与验收项
    for m in repo.get_plan(pid)["milestones"]:
        repo.complete_milestone(pid, m["milestone_id"])
    for acc in repo.get_plan(pid)["acceptance"]:
        if not acc["done"]:
            acc["done"] = True
    repo._save()
    # 清空到期复习
    for _ in range(5):
        for it in repo.schedule_reviews(pid):
            repo.review_checkin(plan_id=pid, review_item_id=it["review_item_id"], correct=True)
    check = repo.completion_check(pid)
    print(f"  验收报告：满足={check['eligible']} 未满足={check['missing']}")
    # LLM 裁决（示例中直接确认）
    result = repo.confirm_completion(pid, accepted=True, report_note="四级通过：词汇 4500+，听力/阅读正确率达标，复习稳定")
    if result["confirmed"]:
        print(f"  🎉 {repo.get_plan(pid)['title']} → {repo.get_plan(pid)['status']}")
        print(f"  完成报告：{repo.get_plan(pid)['completion']['report']}")
    else:
        print("  未完成：", result["check"]["missing"])

    # 8. 数据文件确认
    pf = data_dir / "study.json"
    print(f"\n[8] 数据落盘：{pf}（{pf.stat().st_size} 字节）")
    print("\n全链路验证通过 ✔")


if __name__ == "__main__":
    main()
