# -*- coding: utf-8 -*-
"""记忆层 Benchmark 冒烟测试：确认关键指标可测量且数值合理"""
import asyncio

from aion_agent.benchmark import run


def test_benchmark_runs_and_sane(tmp_path):
    report = asyncio.run(run(tmp_path))
    assert "recall" in report and "dedup_exact" in report
    assert "dedup_semantic" in report and "inject_saving" in report
    assert "latency" in report

    recall = report["recall"]
    assert recall["queries"] >= 10
    assert recall["recall_at_1"] >= 0.9, f"recall@1 过低: {recall}"

    dedup = report["dedup_exact"]
    assert dedup["attempts"] >= 3
    assert dedup["unique_ids"] == 1, f"精确去重未生效: {dedup}"

    sem = report["dedup_semantic"]
    assert "error" not in sem, f"语义去重异常: {sem}"
    assert sem["after_dedup"] < sem["input"], f"语义去重未生效: {sem}"

    saving = report["inject_saving"]
    assert saving["total_triples"] >= 10
    assert saving["saving_ratio"] > 0.3, f"注入节省过低: {saving}"

    latency = report["latency"]
    assert latency["samples"] >= 10
    assert latency["retrieve_avg_ms"] >= 0
    assert latency["write_avg_ms"] >= 0