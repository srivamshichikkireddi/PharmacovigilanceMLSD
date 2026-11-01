"""All environment-specific configuration. Nothing here is a secret or an endpoint:
every value is read from the environment at call time (D4)."""
import os


def env(name, default=None):
    v = os.environ.get(name)
    return default if v in (None, "") else v


def env_int(name, default):
    return int(env(name, default))


def env_float(name, default):
    return float(env(name, default))


def env_bool(name, default=False):
    return str(env(name, "1" if default else "0")).lower() in ("1", "true", "yes", "on")


class Settings:
    """Read lazily so tests can monkeypatch os.environ."""

    @property
    def redis_url(self): return env("REDIS_URL", "redis://localhost:6379/0")
    @property
    def corpus_dir(self): return env("CORPUS_DIR", "corpus")
    @property
    def index_dir(self): return env("INDEX_DIR", "data/index")
    # --- LLM (provider-agnostic: any OpenAI-compatible endpoint) ---
    @property
    def llm_provider(self): return env("LLM_PROVIDER", "none")      # none | openai_compat
    @property
    def llm_base_url(self): return env("LLM_BASE_URL", "")
    @property
    def llm_api_key(self): return env("LLM_API_KEY", "")
    @property
    def llm_model(self): return env("LLM_MODEL", "")
    @property
    def llm_timeout_s(self): return env_float("LLM_TIMEOUT_S", 20)
    @property
    def llm_max_retries(self): return env_int("LLM_MAX_RETRIES", 2)
    @property
    def llm_min_interval_s(self): return env_float("LLM_MIN_INTERVAL_S", 0)
    @property
    def price_in_per_1m(self): return env_float("LLM_PRICE_IN_PER_1M", 0.15)    # notional USD
    @property
    def price_out_per_1m(self): return env_float("LLM_PRICE_OUT_PER_1M", 0.60)  # notional USD
    # --- breaker ---
    @property
    def breaker_threshold(self): return env_int("BREAKER_FAIL_THRESHOLD", 3)
    @property
    def breaker_window_s(self): return env_float("BREAKER_WINDOW_S", 60)
    @property
    def breaker_cooldown_s(self): return env_float("BREAKER_COOLDOWN_S", 20)
    # --- agent loop bounds (A1) ---
    @property
    def max_steps(self): return env_int("AGENT_MAX_STEPS", 12)
    @property
    def max_tool_calls(self): return env_int("AGENT_MAX_TOOL_CALLS", 14)
    @property
    def run_timeout_s(self): return env_float("AGENT_RUN_TIMEOUT_S", 45)
    @property
    def tool_timeout_s(self): return env_float("TOOL_TIMEOUT_S", 5)
    @property
    def max_retries_strategy(self): return env_int("VERIFY_MAX_STRATEGIES", 2)
    # --- thresholds ---
    @property
    def label_match_min(self): return env_float("LABEL_MATCH_MIN_CONFIDENCE", 0.75)
    # --- platform ---
    @property
    def rate_limit_per_min(self): return env_int("CLIENT_RATE_LIMIT_PER_MINUTE", 30)
    @property
    def trust_proxy(self): return env_bool("TRUST_PROXY", True)
    @property
    def cache_enabled(self): return env_bool("RUN_CACHE_ENABLED", True)
    @property
    def worker_concurrency(self): return env_int("WORKER_CONCURRENCY", 4)
    @property
    def worker_metrics_port(self): return env_int("WORKER_METRICS_PORT", 9101)
    @property
    def quality_sample_rate(self): return env_float("QUALITY_SAMPLE_RATE", 0.25)
    @property
    def embeddings_enabled(self): return env_bool("EMBEDDINGS_ENABLED", False)
    @property
    def embedding_model(self): return env("EMBEDDING_MODEL", "")
    @property
    def fault_injection(self): return env("FAULT_INJECTION", "")
    @property
    def prompt_normalise(self): return env("PROMPT_NORMALISE", "normalise_v2")
    @property
    def agentic(self): return env_bool("AGENTIC_RETRIEVAL", True)   # False = single-pass baseline (ablation)


settings = Settings()
