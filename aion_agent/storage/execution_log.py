"""执行日志 —— 可回放、可审计的事件流水

统一记录用户消息、LLM 调用、工具调用与结果、反思、验收、认知沉淀、错误。
按 JSONL 落盘（execution_log.jsonl）；无持久化目录时退化为内存环形缓冲。
所有写入线程安全（后台工具线程 + 主循环共用）。
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_MAX_CONTENT_LEN = 2000
_DEFAULT_MAX_MEMORY = 500


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _truncate(text: str, limit: int = _MAX_CONTENT_LEN) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


class JsonExecutionLog:
    """执行日志（JSONL 追加写，内存兜底）"""

    def __init__(self, persist_dir: Optional[str] = None):
        self._file = (
            Path(persist_dir) / "execution_log.jsonl"
            if persist_dir else None
        )
        if self._file is not None:
            try:
                self._file.parent.mkdir(parents=True, exist_ok=True)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"执行日志目录创建失败，退化为内存模式: {e}")
                self._file = None
        self._memory: List[dict] = []
        self._max_memory = _DEFAULT_MAX_MEMORY
        self._lock = threading.Lock()
        self._seq = 0

    # ---------- 写入 ----------

    def append(
        self,
        *,
        session_id: str,
        event_type: str,
        content: str = "",
        meta: Optional[Dict] = None,
    ) -> None:
        with self._lock:
            self._seq += 1
        record = {
            "ts": _now_iso(),
            "seq": self._seq,
            "session_id": str(session_id or ""),
            "type": str(event_type or ""),
            "content": _truncate(content),
            "meta": dict(meta or {}),
        }
        with self._lock:
            if self._file is not None:
                try:
                    with open(self._file, "a", encoding="utf-8") as f:
                        f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    return
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"执行日志写入失败，写入内存兜底: {e}")
            self._memory.append(record)
            if len(self._memory) > self._max_memory:
                self._memory = self._memory[-self._max_memory:]

    # ---------- 读取 ----------

    def _read_all(self) -> List[dict]:
        with self._lock:
            if self._file is not None:
                try:
                    with open(self._file, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    records = []
                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
                    return records
                except FileNotFoundError:
                    return []
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"执行日志读取失败，使用内存缓冲: {e}")
            return list(self._memory)

    def query(
        self,
        session_id: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[dict]:
        records = self._read_all()
        if session_id:
            records = [r for r in records if r.get("session_id") == session_id]
        if event_type:
            records = [r for r in records if r.get("type") == event_type]
        records.sort(
            key=lambda r: (r.get("ts") or "", r.get("seq", 0)),
            reverse=True,
        )
        return records[: max(int(limit), 1)]

    def recent(self, limit: int = 50) -> List[dict]:
        return self.query(limit=limit)

    def count(self) -> int:
        return len(self._read_all())
