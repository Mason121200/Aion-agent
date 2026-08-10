# -*- coding: utf-8 -*-
"""Aion Agent 记忆层 Benchmark —— 测量认知基础设施的核心指标

在临时数据目录中构造已知记忆，测量：
  1. recall@k      检索命中率（向量/关键词检索质量）
  2. dedup_exact   精确去重率（相同三元组不重复入库）
  3. dedup_semantic 语义去重率（相似表述合并）
  4. inject_saving 注入上下文 token 节省比例（RAG 注入 vs 全量）
  5. latency       写入 / 检索平均耗时（毫秒）

运行：
    python -m aion_agent.benchmark
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List

from aion_agent.core.entities.cognitive_triple import CognitiveTriple, Dimension
from aion_agent.core.entities.agent_state import AgentState
from aion_agent.llm.embedding import build_embedder
from aion_agent.pipeline.cognition_pipeline import CognitionPipeline
from aion_agent.storage.in_memory_cognitive_repo import InMemoryCognitiveRepo

USER = "bench_user"


def _est_tokens(text: str) -> int:
    """粗略估算 token：中文按字、ASCII 按 4 字符一个 token"""
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return cjk + other // 4 + 1


async def _measure_recall(repo: InMemoryCognitiveRepo, k_values=(1, 3, 5)) -> dict:
    topics = [
        "Python", "Java", "Rust", "Go", "TypeScript", "Docker", "Kubernetes",
        "FastAPI", "PostgreSQL", "Redis", "Nginx", "Git", "ChromaDB", "MCP",
        "WebSocket", "SSE", "向量检索", "RAG", "ReAct", "认知层",
    ]
    for i, topic in enumerate(topics):
        await repo.save_triple(CognitiveTriple(
            subject="用户", predicate="擅长", object=topic,
            dimension=Dimension.USER, confidence=0.95, user_id=USER,
        ))
    total = len(topics)
    hits = {k: 0 for k in k_values}
    for topic in topics:
        results = await repo.retrieve(USER, query=topic, top_k=max(k_values))
        hit_ids = {t.object for t in results}
        for k in k_values:
            if topic in {t.object for t in results[:k]}:
                hits[k] += 1
    return {
        "queries": total,
        "recall_at_1": round(hits[1] / total, 3),
        "recall_at_3": round(hits[3] / total, 3),
        "recall_at_5": round(hits[5] / total, 3),
    }


async def _measure_exact_dedup(repo: InMemoryCognitiveRepo) -> dict:
    before = len(await repo.list_triples_by_dimension(USER, Dimension.USER))
    ids = set()
    for _ in range(5):
        rid = await repo.save_triple(CognitiveTriple(
            subject="用户", predicate="城市是", object="深圳",
            dimension=Dimension.USER, confidence=0.9, user_id=USER,
        ))
        ids.add(rid)
    after = len(await repo.list_triples_by_dimension(USER, Dimension.USER))
    return {
        "attempts": 5,
        "unique_ids": len(ids),
        "stored_growth": after - before,
    }


def _measure_semantic_dedup(embedder) -> dict:
    items = []
    for i in range(6):
        items.append({"subject": "用户", "predicate": "喜欢", "object": f"读书主题{i}",
                      "dimension": "user", "confidence": 0.9})
    near_dup = [
        {"subject": "用户", "predicate": "喜欢", "object": "读书主题0",
         "dimension": "user", "confidence": 0.9},
        {"subject": "用户", "predicate": "喜爱", "object": "读书主题1",
         "dimension": "user", "confidence": 0.9},
        {"subject": "用户", "predicate": "偏好", "object": "读书主题2",
         "dimension": "user", "confidence": 0.9},
    ]
    try:
        pipeline = CognitionPipeline(cognitive_repo=None, embedder=embedder)
        kept = pipeline.deduplicate(items + near_dup)
        return {"input": len(items) + len(near_dup), "after_dedup": len(kept)}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:120]}


async def _measure_inject_saving(repo: InMemoryCognitiveRepo) -> dict:
    all_triples = []
    for dim in Dimension:
        all_triples.extend(await repo.list_triples_by_dimension(USER, dim))
    full_text = json.dumps(
        [{"s": t.subject, "p": t.predicate, "o": t.object} for t in all_triples],
        ensure_ascii=False,
    )
    full_tokens = _est_tokens(full_text)
    top = await repo.retrieve(USER, query="技能 技术", top_k=8)
    injected_text = json.dumps(
        [{"s": t.subject, "p": t.predicate, "o": t.object} for t in top],
        ensure_ascii=False,
    )
    injected_tokens = _est_tokens(injected_text)
    return {
        "total_triples": len(all_triples),
        "injected_triples": len(top),
        "full_tokens_est": full_tokens,
        "injected_tokens_est": injected_tokens,
        "saving_ratio": round(1 - injected_tokens / max(full_tokens, 1), 3),
    }


async def _measure_latency(repo: InMemoryCognitiveRepo, n: int = 50) -> dict:
    write_times, read_times = [], []
    for i in range(n):
        t0 = time.perf_counter()
        await repo.save_triple(CognitiveTriple(
            subject="延迟测试", predicate="条目", object=f"item{i}",
            dimension=Dimension.WORLD, confidence=0.8, user_id=USER,
        ))
        write_times.append((time.perf_counter() - t0) * 1000)
    for i in range(n):
        t0 = time.perf_counter()
        await repo.retrieve(USER, query=f"item{i}", top_k=5)
        read_times.append((time.perf_counter() - t0) * 1000)
    return {
        "write_avg_ms": round(sum(write_times) / n, 3),
        "retrieve_avg_ms": round(sum(read_times) / n, 3),
        "samples": n,
    }


async def run(data_dir: Path) -> dict:
    embedder = build_embedder()
    repo = InMemoryCognitiveRepo(embedder=embedder, persist_dir=data_dir)
    report = {}
    report["recall"] = await _measure_recall(repo)
    report["dedup_exact"] = await _measure_exact_dedup(repo)
    report["dedup_semantic"] = _measure_semantic_dedup(embedder)
    report["inject_saving"] = await _measure_inject_saving(repo)
    report["latency"] = await _measure_latency(repo)
    report["embedder"] = type(embedder).__name__
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Aion Agent 记忆层 Benchmark")
    parser.add_argument("--data-dir", help="数据目录（默认使用临时目录，不污染真实数据）")
    args = parser.parse_args(argv)
    tmp = Path(args.data_dir) if args.data_dir else Path(tempfile.mkdtemp(prefix="aion_bench_"))
    report = asyncio.run(run(tmp))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())