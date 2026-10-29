"""Generates observability/grafana/dashboards/pv.json (D5). Run: python observability/make_dashboard.py"""
import json
P = {"type": "prometheus", "uid": "prom"}
panels, y = [], 0


def q(expr, legend=""): return {"datasource": P, "expr": expr, "legendFormat": legend, "refId": chr(65 + len(cur))}


def add(title, exprs, x, w=12, h=8, unit="short", kind="timeseries"):
    global cur
    cur = []
    for e, l in exprs:
        cur.append(q(e, l))
    panels.append({"id": len(panels) + 1, "title": title, "type": kind, "datasource": P, "gridPos": {"x": x, "y": y, "w": w, "h": h},
                   "targets": list(cur), "fieldConfig": {"defaults": {"unit": unit}, "overrides": []}})


def row():
    global y
    y += 8


H = lambda m, qn, by="le", w="1m": f"histogram_quantile({qn}, sum(rate({m}_bucket[{w}])) by ({by}))"
add("End-to-end latency p50 / p95 (submission to result)", [(H("pv_e2e_seconds", 0.5), "p50"), (H("pv_e2e_seconds", 0.95), "p95")], 0, unit="s")
add("Worker run latency p50 / p95", [(H("pv_run_seconds", 0.5), "p50"), (H("pv_run_seconds", 0.95), "p95")], 12, unit="s"); row()
add("API request latency p95 by endpoint", [(H("pv_api_request_seconds", 0.95, "le,endpoint"), "{{endpoint}}")], 0, unit="s")
add("Throughput (finished runs/s)", [('sum(rate(pv_runs_total[1m])) by (route)', "{{route}}")], 12, unit="ops"); row()
add("Error rate", [('(sum(rate(pv_runs_total{status="failed"}[1m])) or vector(0)) / clamp_min(sum(rate(pv_runs_total[1m])),1e-9)', "failed runs"),
                   ('(sum(rate(pv_api_requests_total{status=~"5.."}[1m])) or vector(0)) / clamp_min(sum(rate(pv_api_requests_total[1m])),1e-9)', "API 5xx")], 0, unit="percentunit")
add("Availability (up)", [('up{job=~"api|agent-worker"}', "{{job}} {{instance}}")], 12, unit="short"); row()
add("Container/cgroup CPU (cores)", [('sum(rate(container_cpu_usage_seconds_total{id=~".+"}[1m])) by (id)', "{{id}}")], 0)
add("Container/cgroup memory (working set)", [('sum(container_memory_working_set_bytes{id=~".+"}) by (id)', "{{id}}")], 12, unit="bytes"); row()
add("Per-step latency p95 (agents)", [(H("pv_step_seconds", 0.95, "le,step"), "{{step}}")], 0, unit="s")
add("Tool latency p95", [(H("pv_tool_seconds", 0.95, "le,tool"), "{{tool}}")], 12, unit="s"); row()
add("LLM latency p95 per step", [(H("pv_llm_seconds", 0.95, "le,step"), "{{step}}")], 0, unit="s")
add("Circuit breaker state (0 closed, 1 half-open, 2 open)", [("max(pv_circuit_state)", "state")], 12); row()
add("Task success rate per agent", [('sum(rate(pv_agent_runs_total{outcome="ok"}[5m])) by (agent) / sum(rate(pv_agent_runs_total[5m])) by (agent)', "{{agent}}")], 0, unit="percentunit")
add("Retries/s by kind", [('sum(rate(pv_retries_total[1m])) by (kind)', "{{kind}}")], 12); row()
add("Steps and tool calls per run (mean)", [("sum(rate(pv_steps_per_run_sum[5m]))/sum(rate(pv_steps_per_run_count[5m]))", "graph steps"),
                                            ("sum(rate(pv_tool_calls_per_run_sum[5m]))/sum(rate(pv_tool_calls_per_run_count[5m]))", "tool calls")], 0)
add("Tokens/s and cost/s by step", [('sum(rate(pv_llm_tokens_total[1m])) by (step)', "tokens {{step}}"), ('sum(rate(pv_cost_usd_total[1m])) by (step)', "USD {{step}}")], 12); row()
add("Escalations by reason", [('sum(increase(pv_escalations_total[10m])) by (reason)', "{{reason}}")], 0)
add("Verification outcomes", [('sum(increase(pv_verification_total[10m])) by (outcome)', "{{outcome}}")], 12); row()
add("Cache hit ratio", [('sum(rate(pv_cache_total{result="hit"}[5m]))/clamp_min(sum(rate(pv_cache_total[5m])),1e-9)', "run cache")], 0, unit="percentunit")
add("Queue depth and in-flight runs", [("max(pv_queue_depth)", "queued"), ("sum(pv_inflight_runs)", "in flight")], 12); row()
add("Live quality (sampled runs, mean)", [("sum(rate(pv_quality_score_sum[15m])) by (metric)/sum(rate(pv_quality_score_count[15m])) by (metric)", "{{metric}}")], 0, unit="percentunit")
add("User satisfaction (feedback)", [('sum(increase(pv_feedback_total{satisfactory="true"}[1h]))', "satisfied"), ('sum(increase(pv_feedback_total{satisfactory="false"}[1h]))', "unsatisfied")], 12); row()
add("Index freshness (age, s) / chunks / throughput", [("time() - max(pv_index_built_timestamp_seconds)", "index age s"), ("max(pv_index_chunks)", "chunks"), ("max(pv_index_throughput_chunks_per_s)", "chunks/s")], 0)
add("Quarantined at the boundary / rate limited", [('sum(increase(pv_intake_rejected_total[10m])) by (code)', "rejected {{code}}"), ('sum(increase(pv_rate_limited_total[10m]))', "rate limited")], 12)
dash = {"title": "Pharmacovigilance agent service", "uid": "pv-main", "schemaVersion": 39, "version": 1, "refresh": "5s", "time": {"from": "now-30m", "to": "now"},
        "panels": panels, "templating": {"list": []}, "tags": ["pv"]}
open("observability/grafana/dashboards/pv.json", "w").write(json.dumps(dash, indent=1))
print(len(panels), "panels")
