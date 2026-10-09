"""LLM access layer (Step 2). Every agent calls the single governed entry point `LLMClient.call_llm`.

Responsibilities: Groq calls, retry/back-off on rate limits, automatic fallback model, patient-id
redaction of everything sent to the model, token/latency/cost accounting.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import config
from .guardrails import redact_patient_ids


class LLMError(RuntimeError):
    pass


class ModelNotFoundError(LLMError):
    """The configured model does not exist / was decommissioned (HTTP 404 or model_decommissioned)."""


class ToolCallFormatError(LLMError):
    """The model produced a malformed tool call (Groq returns HTTP 400 'tool_use_failed')."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class LLMResult:
    content: str
    tool_calls: List[ToolCall] = field(default_factory=list)
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0

    def assistant_message(self) -> Dict[str, Any]:
        msg: Dict[str, Any] = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = [{"id": t.id, "type": "function", "function": {"name": t.name, "arguments": t.arguments}}
                                 for t in self.tool_calls]
        return msg


def _exc_name(e: Exception) -> str:
    return type(e).__name__


def _retry_after_seconds(e: Exception) -> float:
    try:
        resp = getattr(e, "response", None)
        if resp is not None and resp.headers.get("retry-after"):
            return float(resp.headers["retry-after"])
    except Exception:
        pass
    m = re.search(r"try again in (?:(\d+)m)?\s*([\d.]+)s", str(e))
    if m:
        return float(m.group(1) or 0) * 60 + float(m.group(2))
    return 0.0


class LLMClient:
    # models that are not chat/tool models, or are less suitable; skipped when auto-discovering a replacement
    _SKIP = ("whisper", "guard", "tts", "orpheus", "embed", "safeguard", "compound", "playai", "allam", "vision-preview")
    _PREFER = ("openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/", "meta-llama/llama-4", "moonshotai/", "llama-3")

    def __init__(self, client: Any = None, api_key: Optional[str] = None):
        self._model_map: Dict[str, str] = {}
        self._client = client
        self._api_key = api_key if api_key is not None else config.GROQ_API_KEY

    @property
    def available(self) -> bool:
        return self._client is not None or bool(self._api_key and not self._api_key.startswith("your_"))

    def _get_client(self):
        if self._client is None:
            if not self.available:
                raise LLMError("GROQ_API_KEY is not set. Add your free key to the .env file "
                               "(get one at https://console.groq.com/keys) and restart.")
            from groq import Groq
            self._client = Groq(api_key=self._api_key, timeout=config.LLM_TIMEOUT_S, max_retries=0)
        return self._client

    @staticmethod
    def _sanitize(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for m in messages:
            m2 = dict(m)
            if isinstance(m2.get("content"), str):
                m2["content"] = redact_patient_ids(m2["content"])  # guardrail: no raw patient ids reach the model
            out.append(m2)
        return out

    def call_llm(self, messages: List[Dict[str, Any]], model: str, tools: Optional[List[dict]] = None,
                 json_mode: bool = False, temperature: float = 0.1, max_tokens: int = 1200,
                 trace: Any = None, agent: str = "") -> LLMResult:
        client = self._get_client()
        msgs = self._sanitize(messages)
        tried_fallback = False
        heals = 0
        current = self._model_map.get(model, model)
        while True:
            try:
                res = self._call_with_retries(client, msgs, current, tools, json_mode, temperature, max_tokens)
                if trace is not None:
                    trace.record_llm(agent, res.model, res.latency_ms, res.prompt_tokens, res.completion_tokens,
                                     [t.name for t in res.tool_calls])
                return res
            except ModelNotFoundError as e:
                heals += 1
                new = self._discover_replacement(client, current) if heals <= 2 else None
                if not new:
                    raise LLMError("Model '%s' is not available on your Groq account and no replacement was found. "
                                   "Open https://console.groq.com/docs/models and set MODEL_AGENT / MODEL_ROUTER / "
                                   "MODEL_WRITER / MODEL_FALLBACK in .env. (%s)" % (current, str(e)[:120])) from e
                if trace is not None:
                    trace.add_event(agent, "fallback", "model_auto_replaced", "%s -> %s" % (current, new))
                self._model_map[model] = new
                self._model_map[current] = new
                current = new
                continue
            except LLMError as e:
                if "RATE_LIMIT" in str(e) and not tried_fallback and config.MODEL_FALLBACK and current != config.MODEL_FALLBACK:
                    tried_fallback = True
                    current = self._model_map.get(config.MODEL_FALLBACK, config.MODEL_FALLBACK)
                    if trace is not None:
                        trace.add_event(agent, "fallback", "model_fallback", "%s rate-limited -> %s" % (model, current))
                    continue
                raise

    def _discover_replacement(self, client: Any, bad: str) -> Optional[str]:
        """Ask Groq which models this key can use and pick the best chat/tool model (self-healing on deprecations)."""
        try:
            ids = [m.id for m in client.models.list().data]
        except Exception:  # noqa: BLE001
            return None
        usable = [i for i in ids if i != bad and not any(x in i.lower() for x in self._SKIP)]
        for pref in self._PREFER:
            for i in usable:
                if i == pref or (pref.endswith("/") or pref == "llama-3") and i.startswith(pref):
                    return i
        return usable[0] if usable else None

    def _call_with_retries(self, client, msgs, model, tools, json_mode, temperature, max_tokens) -> LLMResult:
        last: Optional[Exception] = None
        optional = True  # response_format / reasoning_effort are dropped if the model rejects them
        for attempt in range(config.LLM_MAX_RETRIES + 2):
            t0 = time.time()
            try:
                kwargs: Dict[str, Any] = dict(model=model, messages=msgs, temperature=temperature, max_tokens=max_tokens)
                if tools:
                    kwargs["tools"] = tools
                    kwargs["tool_choice"] = "auto"
                if json_mode and optional:
                    kwargs["response_format"] = {"type": "json_object"}
                if optional and config.REASONING_EFFORT and model.startswith("openai/gpt-oss"):
                    kwargs["reasoning_effort"] = config.REASONING_EFFORT
                resp = client.chat.completions.create(**kwargs)
                msg = resp.choices[0].message
                tcs = [ToolCall(id=t.id, name=t.function.name, arguments=t.function.arguments or "{}")
                       for t in (getattr(msg, "tool_calls", None) or [])]
                usage = getattr(resp, "usage", None)
                return LLMResult(content=msg.content or "", tool_calls=tcs, model=model,
                                 prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                                 completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                                 latency_ms=int((time.time() - t0) * 1000))
            except Exception as e:  # noqa: BLE001 - classify below
                last = e
                name, text = _exc_name(e), str(e)
                if name == "AuthenticationError" or "invalid_api_key" in text:
                    raise LLMError("Groq rejected the API key. Check GROQ_API_KEY in your .env file.") from e
                if name == "NotFoundError" or "model_not_found" in text or "model_decommissioned" in text:
                    raise ModelNotFoundError(text[:300]) from e
                if name == "BadRequestError" and ("tool_use_failed" in text or "Failed to call a function" in text):
                    raise ToolCallFormatError(text[:300]) from e
                if name == "BadRequestError" and optional and any(k in text for k in ("response_format", "reasoning_effort", "json")):
                    optional = False  # retry once without the optional parameters
                    continue
                if name == "RateLimitError" or "rate_limit" in text.lower() or "429" in text[:40]:
                    daily = "per day" in text or "tokens per day" in text.lower()
                    wait = _retry_after_seconds(e) or (2 ** attempt * 2)
                    if daily or attempt >= config.LLM_MAX_RETRIES + 1 or wait > 45:
                        raise LLMError("RATE_LIMIT: %s" % text[:200]) from e
                    time.sleep(wait + 0.5)
                    continue
                if name in ("APIConnectionError", "APITimeoutError", "InternalServerError") or "503" in text[:40] or "500" in text[:40]:
                    if attempt >= config.LLM_MAX_RETRIES + 1:
                        raise LLMError("Groq API unreachable: %s" % text[:200]) from e
                    time.sleep(2 ** attempt)
                    continue
                raise LLMError("%s: %s" % (name, text[:300])) from e
        raise LLMError("LLM call failed: %s" % last)
