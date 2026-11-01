"""Provider-agnostic LLM interface; provider endpoint and model are runtime configuration."""
import json, re
from dataclasses import dataclass
from contextlib import nullcontext
import threading
import time
from typing import Optional


class LLMUnavailable(Exception):
    """Raised when the model cannot be used (no provider, breaker open, retries exhausted)."""


class LLMQuotaExceeded(LLMUnavailable):
    """Raised when a provider reports a rate limit or exhausted quota."""


def is_provider_limit_error(exc):
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    return status == 429 or bool(re.search(r"\b429\b|rate.?limit|quota", str(exc), re.I))


@dataclass
class LLMResult:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: Optional[str] = None


class NoLLM:
    name = "none"
    def chat(self, messages, timeout):
        raise LLMUnavailable("no LLM provider configured (LLM_PROVIDER=none)")


class OpenAICompat:
    name = "openai_compat"

    def __init__(self, base_url, api_key, model, min_interval_s=0):
        self.base_url, self.api_key, self.model = base_url.rstrip("/"), api_key, model
        self.min_interval_s = max(0.0, float(min_interval_s))
        self._request_lock = threading.Lock()
        self._last_request_at = 0.0

    def chat(self, messages, timeout):
        import httpx
        with (self._request_lock if self.min_interval_s > 0 else nullcontext()):
            wait = self.min_interval_s - (time.monotonic() - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
            self._last_request_at = time.monotonic()
            r = httpx.post(f"{self.base_url}/chat/completions", timeout=timeout,
                           headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
                           json={"model": self.model, "messages": messages, "temperature": 0, "max_tokens": 400})
            r.raise_for_status()
            d = r.json()
            u = d.get("usage") or {}
            choice = d["choices"][0]
            text = choice["message"]["content"] or ""
            return LLMResult(text, u.get("prompt_tokens") or sum(len(m["content"]) for m in messages) // 4,
                             u.get("completion_tokens") or len(text) // 4, choice.get("finish_reason"))


class FakeLLM:
    """Scripted model for tests: `script` is a callable(messages) -> str, or raises."""
    name = "fake"
    def __init__(self, script): self.script, self.calls = script, 0
    def chat(self, messages, timeout):
        self.calls += 1
        t = self.script(messages)
        return LLMResult(t, sum(len(m["content"]) for m in messages) // 4, len(t) // 4)


def parse_json(text):
    """Tolerant JSON extraction (models wrap JSON in fences or prose). Returns {} on failure."""
    if not text:
        return {}
    m = re.search(r"\{.*\}", text, re.S)
    try:
        v = json.loads(m.group(0)) if m else {}
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def make_client(settings):
    if settings.llm_provider == "openai_compat" and settings.llm_base_url:
        return OpenAICompat(settings.llm_base_url, settings.llm_api_key, settings.llm_model, settings.llm_min_interval_s)
    return NoLLM()
