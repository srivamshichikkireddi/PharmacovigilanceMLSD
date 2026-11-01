"""D5/A6/L5/D2: Prometheus metrics. If prometheus_client is missing (unit-test env) every
metric becomes a harmless no-op, so the pipeline never depends on monitoring being installed."""
try:
    from prometheus_client import Counter, Gauge, Histogram
except Exception:  # pragma: no cover
    class _Noop:
        def __init__(self, *a, **k): pass
        def labels(self, *a, **k): return self
        def inc(self, *a, **k): pass
        def observe(self, *a, **k): pass
        def set(self, *a, **k): pass
        def dec(self, *a, **k): pass
    Counter = Gauge = Histogram = _Noop

LAT = (0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 45, 60, 90, 120)

RUN_SECONDS = Histogram("pv_run_seconds", "End-to-end run time in the worker (excludes queue wait)", ["route"], buckets=LAT)
E2E_SECONDS = Histogram("pv_e2e_seconds", "Submission to final result (includes queue wait)", ["route"], buckets=LAT)
STEP_SECONDS = Histogram("pv_step_seconds", "Latency per pipeline step/agent", ["step"], buckets=LAT)
TOOL_SECONDS = Histogram("pv_tool_seconds", "Latency per tool call", ["tool"], buckets=LAT)
LLM_SECONDS = Histogram("pv_llm_seconds", "Latency per LLM call", ["step"], buckets=LAT)
STEPS_PER_RUN = Histogram("pv_steps_per_run", "Graph steps per run", buckets=(1, 2, 3, 4, 5, 6, 8, 10, 15, 20))
TOOLS_PER_RUN = Histogram("pv_tool_calls_per_run", "Tool calls per run", buckets=(0, 1, 2, 3, 4, 6, 8, 10, 14, 20))
RUNS = Counter("pv_runs_total", "Finished runs", ["status", "route"])
AGENT_RUNS = Counter("pv_agent_runs_total", "Per-agent outcomes (task success rate)", ["agent", "outcome"])
ESCALATIONS = Counter("pv_escalations_total", "Escalations by reason", ["reason"])
VERIFY = Counter("pv_verification_total", "Verification outcomes", ["outcome"])
RETRIES = Counter("pv_retries_total", "Retries", ["kind"])           # llm | strategy | job
LLM_CALLS = Counter("pv_llm_calls_total", "LLM calls", ["step", "outcome"])
TOKENS = Counter("pv_llm_tokens_total", "LLM tokens", ["agent", "step", "kind"])
COST = Counter("pv_cost_usd_total", "Notional LLM cost in USD", ["agent", "step"])
BREAKER_STATE = Gauge("pv_circuit_state", "LLM circuit breaker: 0 closed, 1 half-open, 2 open")
DEGRADED = Counter("pv_degraded_runs_total", "Runs that used the fallback path", ["reason"])
CACHE = Counter("pv_cache_total", "Run/retrieval cache lookups", ["layer", "result"])
QUEUE_DEPTH = Gauge("pv_queue_depth", "Jobs waiting")
INFLIGHT = Gauge("pv_inflight_runs", "Runs executing in this worker")
QUALITY = Histogram("pv_quality_score", "Live-sampled quality score", ["metric"], buckets=(0, .25, .5, .75, .9, 1.0))
FEEDBACK = Counter("pv_feedback_total", "User feedback", ["satisfactory"])
INDEX_BUILT_TS = Gauge("pv_index_built_timestamp_seconds", "When the corpus index was built")
INDEX_CHUNKS = Gauge("pv_index_chunks", "Chunks in the index")
INDEX_THROUGHPUT = Gauge("pv_index_throughput_chunks_per_s", "Indexing throughput of the last build")
REQUESTS = Counter("pv_api_requests_total", "API requests", ["endpoint", "status"])
REQ_SECONDS = Histogram("pv_api_request_seconds", "API request latency", ["endpoint"], buckets=LAT)
REJECTED = Counter("pv_intake_rejected_total", "Requests quarantined at the boundary", ["code"])
RATE_LIMITED = Counter("pv_rate_limited_total", "Requests blocked by the rate limiter")


def publish_index_metrics(manifest: dict):
    from datetime import datetime
    try:
        INDEX_CHUNKS.set(manifest.get("chunks", 0))
        INDEX_THROUGHPUT.set(manifest.get("throughput_chunks_per_s", 0))
        INDEX_BUILT_TS.set(datetime.fromisoformat(manifest["built_utc"]).timestamp())
    except Exception:  # noqa
        pass
