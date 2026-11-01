"""Evaluation-only single-pass retrieval and one-shot answer baseline."""
import json
import hashlib
from pathlib import Path

from pv import metrics
from pv.agents.state import RunContext
from pv.llm.client import LLMQuotaExceeded, is_provider_limit_error, parse_json
from pv.llm.resilience import price

SINGLE_PASS_TOP_K = 5


def _render(report, passages):
    template = (Path(__file__).parent / "prompts" / "single_pass_v1.txt").read_text(encoding="utf-8")
    version = "single_pass_v1@" + hashlib.sha1(template.encode("utf-8")).hexdigest()[:8]
    text = template.replace("{{report}}", json.dumps(report, ensure_ascii=False))
    text = text.replace("{{passages}}", json.dumps(passages, ensure_ascii=False))
    return text, version


def run_baseline(run_id, report, index, llm, settings):
    if llm.client.name == "none":
        raise RuntimeError("single-pass baseline requires a configured LLM provider")

    price(settings, 0, 0)
    query = f"{report['suspect_drug']} {report['reaction_description']}"[:400]
    passages = index.search(query, SINGLE_PASS_TOP_K)
    prompt, version = _render(
        report,
        [
            {key: passage[key] for key in ("id", "source", "page", "text")}
            for passage in passages
        ],
    )
    ctx = RunContext(run_id, report, index, llm, settings, {})
    ctx.step_name = "baseline"
    ctx.prompt_versions["baseline"] = version
    ctx.emit("baseline_retrieval", query=query, top_k=SINGLE_PASS_TOP_K,
             passage_ids=[p["id"] for p in passages])
    messages = [{"role": "user", "content": prompt}]
    try:
        response = llm.client.chat(messages, timeout=settings.llm_timeout_s)
    except Exception as exc:
        if is_provider_limit_error(exc):
            ctx.emit("llm_call", step="baseline_answer", prompt="single_pass_v1",
                     prompt_version=version, outcome="unavailable",
                     attempts=1, error="provider_rate_limit_or_quota")
            quota_error = LLMQuotaExceeded("provider rate limit or quota exhausted")
            quota_error.trace = ctx.trace
            raise quota_error from exc
        raise

    cost = price(settings, response.prompt_tokens, response.completion_tokens)
    ctx.add_cost("baseline_answer", response.prompt_tokens, response.completion_tokens, cost)
    metrics.TOKENS.labels("baseline", "baseline_answer", "prompt").inc(response.prompt_tokens)
    metrics.TOKENS.labels("baseline", "baseline_answer", "completion").inc(response.completion_tokens)
    metrics.COST.labels("baseline", "baseline_answer").inc(cost)
    ctx.emit("llm_call", step="baseline_answer", prompt="single_pass_v1",
             prompt_version=version, outcome="ok", attempts=1,
             prompt_tokens=response.prompt_tokens, completion_tokens=response.completion_tokens,
             cost_usd=cost, response_preview=response.text[:160])

    answer = parse_json(response.text)
    raw_basis = answer.get("basis")
    basis = raw_basis if isinstance(raw_basis, list) else []
    route = answer.get("route")
    if route not in {"expedited_report", "periodic_safety_report", "non_reportable", "human_escalation"}:
        route = "human_escalation"
    expectedness = answer.get("expectedness")
    if expectedness not in {"expected", "unexpected", "unmatched", "not_applicable"}:
        expectedness = "unmatched"
    serious = answer.get("serious")
    if not isinstance(serious, bool):
        serious = None
    raw_reasons = answer.get("seriousness_reasons")
    seriousness_reasons = raw_reasons if isinstance(raw_reasons, list) else []
    raw_deadline = answer.get("deadline")
    deadline = raw_deadline if isinstance(raw_deadline, dict) and isinstance(raw_deadline.get("days"), int) else None
    route_basis = answer.get("route_basis")
    escalation = answer.get("escalation")
    if not isinstance(escalation, dict):
        escalation = {"required": route == "human_escalation", "reasons": [], "path": None}
    elif not isinstance(escalation.get("reasons"), list):
        escalation = {**escalation, "reasons": []}
    citation_fields = {"claim", "anchor", "passage_id", "source", "page", "quote"}
    citations_valid = isinstance(raw_basis, list) and all(
        isinstance(citation, dict) and citation_fields.issubset(citation)
        and isinstance(citation.get("quote"), str)
        for citation in raw_basis
    )
    result = {
        "run_id": run_id,
        "route": route,
        "serious": serious,
        "expectedness": expectedness,
        "seriousness_reasons": seriousness_reasons,
        "deadline": deadline,
        "route_basis": route_basis if isinstance(route_basis, str) else "",
        "basis": basis,
        "escalation": escalation,
        "verification": None,
        "degraded": [],
        "cost": ctx.cost_summary(),
        "prompt_versions": ctx.prompt_versions,
        "steps": 1,
        "tool_calls": 0,
        "mode": "single_pass_eval_baseline",
        "agentic": False,
        "result_schema_valid": isinstance(answer.get("route"), str)
        and isinstance(answer.get("serious"), bool)
        and isinstance(answer.get("expectedness"), str)
        and citations_valid
        and isinstance(raw_reasons, list)
        and isinstance(answer.get("route_basis"), str)
        and isinstance(answer.get("escalation"), dict),
    }
    return result, ctx.trace, [p["id"] for p in passages]
