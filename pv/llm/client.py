"""Provider-agnostic LLM interface (brief §12: 'place the model behind an interface you control').
Any OpenAI-compatible endpoint works (Groq, Gemini's OpenAI shim, Ollama, vLLM, the load-test stub)
purely by configuration: LLM_PROVIDER / LLM_BASE_URL / LLM_API_KEY / LLM_MODEL."""
import json, re
from dataclasses import dataclass


class LLMUnavailable(Exception):
    """Raised when the model cannot be used (no provider, breaker open, retries exhausted)."""


@dataclass
class LLMResult:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class NoLLM:
    name = "none"
    def chat(self, messages, timeout):
        raise LLMUnavailable("no LLM provider configured (LLM_PROVIDER=none)")


class OpenAICompat:
    name = "openai_compat"

    def __init__(self, base_url, api_key, model):
        self.base_url, self.api_key, self.model = base_url.rstrip("/"), api_key, model

    def chat(self, messages, timeout):
        import httpx
        r = httpx.post(f"{self.base_url}/chat/completions", timeout=timeout,
                       headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
                       json={"model": self.model, "messages": messages, "temperature": 0, "max_tokens": 400})
        r.raise_for_status()
        d = r.json()
        u = d.get("usage") or {}
        text = d["choices"][0]["message"]["content"] or ""
        return LLMResult(text, u.get("prompt_tokens") or sum(len(m["content"]) for m in messages) // 4,
                         u.get("completion_tokens") or len(text) // 4)


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
        return OpenAICompat(settings.llm_base_url, settings.llm_api_key, settings.llm_model)
    return NoLLM()
