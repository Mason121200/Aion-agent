"""OpenAI 兼容的 LLM 客户端（标准库 urllib 实现，零第三方依赖）

支持：
- 非流式 chat()（同步）/ complete()（异步，工具调用解析）
- 流式 stream_chat()（同步）/ stream()（异步，SSE + 工具调用增量合并）
- 兼容 choices[].delta 与 choices[].text 两种流式格式

配置（环境变量，AION_ 前缀优先，兼容 LLM_ 前缀）：
- AION_LLM_API_KEY / LLM_API_KEY
- AION_LLM_BASE_URL / LLM_BASE_URL
- AION_LLM_MODEL  / LLM_MODEL
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Iterator, List, Optional

from aion_agent.core.ports.i_llm_client import ILLMClient, LLMResponse, StreamChunk

logger = logging.getLogger(__name__)


def _coerce_usage(usage_raw: Optional[dict]) -> Optional[dict]:
    """Coerce usage values: scalars to int, nested dicts (e.g. *_tokens_details) kept as-is."""
    if not isinstance(usage_raw, dict):
        return None
    usage = {}
    for k, v in usage_raw.items():
        if isinstance(v, dict):
            usage[k] = v
        else:
            try:
                usage[k] = int(v or 0)
            except (TypeError, ValueError):
                usage[k] = v
    return usage


def load_env_from_dotenv(path: str | Path = ".env") -> None:
    """极简 .env 加载器（仅在变量未设置时写入 os.environ）"""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def get_config() -> Dict[str, str]:
    """从环境变量读取 LLM 配置"""
    return {
        "api_key": (
            os.environ.get("AION_LLM_API_KEY")
            or os.environ.get("LLM_API_KEY")
            or ""
        ),
        "base_url": (
            os.environ.get("AION_LLM_BASE_URL")
            or os.environ.get("LLM_BASE_URL")
            or "https://api.deepseek.com/v1"
        ),
        "model": (
            os.environ.get("AION_LLM_MODEL")
            or os.environ.get("LLM_MODEL")
            or "deepseek-v4-flash"
        ),
    }


class OpenAICompatibleClient(ILLMClient):
    """OpenAI / DeepSeek / 任意兼容 /chat/completions 的客户端"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 90.0,
    ):
        self.api_key = api_key
        self.base_url = (base_url or "https://api.deepseek.com/v1").rstrip("/")
        self.model = model or "deepseek-v4-flash"
        self.timeout = timeout

    # ==================== 请求构造 ====================

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _payload(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        temperature: float,
        max_tokens: Optional[int],
        stream: bool,
    ) -> Dict:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if max_tokens:
            payload["max_tokens"] = max_tokens
        return payload

    def _handle_http_error(self, e: urllib.error.HTTPError) -> RuntimeError:
        body = e.read().decode("utf-8", errors="replace")
        logger.error(f"LLM 请求失败 {e.code}: {body[:300]}")
        return RuntimeError(f"LLM 请求失败 {e.code}: {body[:300]}")

    # ==================== 底层 SSE 事件流（同步） ====================

    def _sse_events(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        temperature: float,
        max_tokens: Optional[int],
    ) -> Iterator[Dict[str, Any]]:
        """同步逐事件产出 SSE 数据

        Yields:
            {"type": "delta", "data": chunk_json}
            {"type": "error", "error": str}
            {"type": "done"}
        """
        payload = self._payload(messages, tools, temperature, max_tokens, stream=True)
        req = urllib.request.Request(
            self._endpoint(),
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            yield {"type": "error", "error": str(self._handle_http_error(e))}
            return
        except urllib.error.URLError as e:
            yield {"type": "error", "error": f"网络请求失败: {e.reason}"}
            return
        try:
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                yield {"type": "delta", "data": data}
        except Exception as e:
            yield {"type": "error", "error": f"流读取失败: {e}"}
            return
        yield {"type": "done"}

    # ==================== 同步接口（旧兼容） ====================

    def chat(
        self,
        messages: List[Dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> str:
        """非流式对话，返回回复全文"""
        payload = self._payload(messages, None, temperature, max_tokens, stream=False)
        req = urllib.request.Request(
            self._endpoint(),
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise self._handle_http_error(e) from e

        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise RuntimeError(
                f"LLM 响应格式异常: {str(data)[:200]}"
            ) from None

    def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> Iterator[str]:
        """流式对话（同步），逐个 yield 文本增量"""
        for event in self._sse_events(messages, None, temperature, max_tokens):
            if event["type"] == "error":
                raise RuntimeError(event["error"])
            if event["type"] != "delta":
                continue
            data = event["data"]
            try:
                choice = data["choices"][0]
            except (KeyError, IndexError, TypeError):
                continue
            delta = choice.get("delta") or {}
            content = delta.get("content")
            if content is None and "text" in choice:
                content = choice["text"]
            if content:
                yield content

    # ==================== 异步接口（ILLMClient） ====================

    async def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = "auto",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """非流式调用 LLM（工具调用解析为 dict）"""
        payload = self._payload(messages, tools, temperature, max_tokens, stream=False)

        def _request() -> dict:
            req = urllib.request.Request(
                self._endpoint(),
                data=json.dumps(payload).encode("utf-8"),
                headers=self._headers(),
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                raise self._handle_http_error(e) from e

        data = await asyncio.to_thread(_request)

        try:
            choice = data["choices"][0]
        except (KeyError, IndexError, TypeError):
            raise RuntimeError(f"LLM 响应格式异常: {str(data)[:200]}") from None

        message = choice.get("message") or {}
        tool_calls_parsed = []
        for tc in message.get("tool_calls") or []:
            func = tc.get("function") or {}
            raw_args = func.get("arguments") or {}
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args) if raw_args else {}
                except json.JSONDecodeError:
                    args = {"_raw": raw_args}
            else:
                args = raw_args
            tool_calls_parsed.append({
                "id": tc.get("id"),
                "type": "function",
                "function": {"name": func.get("name"), "arguments": args},
            })

        usage = data.get("usage") or {}
        if isinstance(usage, dict):
            usage = _coerce_usage(usage) or {}

        return LLMResponse(
            content=message.get("content") or "",
            reasoning=message.get("reasoning_content") or "",
            tool_calls=tool_calls_parsed or None,
            usage=usage,
            finish_reason=choice.get("finish_reason"),
        )

    async def stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = "auto",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[StreamChunk, None]:
        """流式调用 LLM（SSE 在后台线程读取，队列逐块透出）

        工具调用增量按 index 合并，finish 时统一解析 arguments 为 dict。
        """
        q: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        _END = {"type": "__end__"}

        def _producer() -> None:
            try:
                for event in self._sse_events(
                    messages, tools, temperature, max_tokens
                ):
                    q.put(event)
            except Exception as e:  # 兜底：任何异常都透出，不让线程挂死
                q.put({"type": "error", "error": str(e)})
            finally:
                q.put(_END)

        threading.Thread(target=_producer, daemon=True).start()

        tool_calls_buffer: Dict[int, Dict[str, Any]] = {}
        while True:
            event = await asyncio.to_thread(q.get)
            if event["type"] == "__end__":
                break
            if event["type"] == "error":
                yield StreamChunk(
                    content="", reasoning="", is_final=True,
                    tool_calls=None, usage=None,
                )
                continue

            data = event["data"]
            try:
                choice = data["choices"][0]
            except (KeyError, IndexError, TypeError):
                continue
            delta = choice.get("delta") or {}
            finish_reason = choice.get("finish_reason")

            content = delta.get("content") or ""
            reasoning = delta.get("reasoning_content") or ""

            for tcd in delta.get("tool_calls") or []:
                index = int(tcd.get("index") or 0)
                entry = tool_calls_buffer.setdefault(index, {
                    "id": "",
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                })
                if tcd.get("id"):
                    entry["id"] = tcd["id"]
                fn = tcd.get("function") or {}
                if fn.get("name"):
                    entry["function"]["name"] = fn["name"]
                if fn.get("arguments"):
                    entry["function"]["arguments"] += fn["arguments"]

            if content or reasoning:
                yield StreamChunk(
                    content=content, reasoning=reasoning, is_final=False,
                )

            if finish_reason:
                parsed = []
                for entry in tool_calls_buffer.values():
                    raw_args = entry["function"]["arguments"]
                    try:
                        args = json.loads(raw_args) if raw_args else {}
                    except json.JSONDecodeError:
                        args = {"_raw": raw_args}
                    parsed.append({
                        "id": entry["id"],
                        "type": "function",
                        "function": {
                            "name": entry["function"]["name"],
                            "arguments": args,
                        },
                    })
                usage_raw = data.get("usage")
                usage = None
                if usage_raw:
                    usage = _coerce_usage(usage_raw)
                yield StreamChunk(
                    content="", reasoning="", is_final=True,
                    tool_calls=parsed or None, usage=usage,
                )
                break