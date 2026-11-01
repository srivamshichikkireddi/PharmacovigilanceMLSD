"""A1/A2: the agent topology.

    intake --(blocking issue)--------------------------------------> finalize
       |
       v
    evidence (tool loop, bounded) -> assess -> verify --passed/exhausted--> finalize
                  ^                              |
                  |                              +--failed & retryable--> retry (new strategy)
                  +------------------------------------------------------------+

Four agents with distinct responsibilities, communicating only through RunContext:
  Intake (entities, early escalation) | Evidence (tool use) | Assessor (determination) | Verifier (gate)
Failure of any agent mid-run => the run ends as an escalation, never as a confident answer."""
import time

from pv import metrics, tracing
from pv.agents.assess import run_assess
from pv.agents.evidence import run_evidence
from pv.agents.intake import blocking, run_intake
from pv.agents.policy import label_terms, unmet_needs
from pv.agents.verify import run_verify
from pv.guardrails import check_output_result, check_output_text
from pv.llm.client import LLMUnavailable, parse_json
from pv.llm.prompts import render
from pv.logging_utils import current_step

DISCLAIMER = ("Decision support only. This system never submits a regulatory report. "
              "A qualified person for pharmacovigilance (QPPV) decides.")


# ------------------------------------------------------------------ nodes
def node_retry(ctx):
    ctx.attempts += 1
    ctx.strategy += 1
    metrics.RETRIES.labels("strategy").inc()
    ctx.emit("decision", what="retry_with_new_strategy", strategy=ctx.strategy, failures=(ctx.verification or {}).get("failures"))
    ctx.reset_attempt()
    return "ok"


def _template_summary(ctx, res):
    if res["route"] == "human_escalation":
        return "Escalated to a qualified person: " + "; ".join(res["escalation"]["reasons"][:3]) + "."
    d = ctx.determination
    return (f"Recommended route: {res['route'].replace('_', ' ')} ({d.get('serious') and 'serious' or 'non-serious'}, "
            f"{d.get('expectedness')}). {d.get('route_basis')} Cited provisions are listed below.")


def node_finalize(ctx):
    det, ver = ctx.determination, ctx.verification
    reasons = list(dict.fromkeys(ctx.early_reasons))
    assessment_unverified = bool(det and (not ver or ver.get("status") != "passed"))
    if getattr(ctx, "agent_error", None):
        reasons.append(f"agent_failed:{ctx.agent_error}")
    if ctx.budget_exhausted:
        reasons.append(f"budget_exhausted:{ctx.budget_exhausted}")
    if det and det.get("reasons"):
        reasons += det["reasons"]
    if det is None and not reasons:
        reasons.append("no_determination")
    if assessment_unverified:
        reasons.append("verification_failed" if ver and ver.get("status") == "failed" else "verification_not_passed")
    reasons = list(dict.fromkeys(reasons))
    escalate = bool(reasons) or det is None or det.get("route") == "human_escalation"
    res = {
        "run_id": ctx.run_id, "route": "human_escalation" if escalate else det["route"],
        "serious": det.get("serious") if det and not assessment_unverified else None,
        "seriousness_reasons": det.get("seriousness_reasons", []) if det and not assessment_unverified else [],
        "expectedness": det.get("expectedness") if det and not assessment_unverified else None,
        "expectedness_confidence": det.get("expectedness_confidence") if det and not assessment_unverified else None,
        "assessment_status": "unverified" if assessment_unverified else "verified" if ver and ver.get("status") == "passed" else "not_assessed",
        "unverified_fields": ["seriousness", "expectedness"] if assessment_unverified else [],
        "route_basis": (det or {}).get("route_basis") if not escalate else
                       "Escalated: " + ", ".join(reasons) if reasons else "Escalated to a qualified person.",
        "deadline": det.get("deadline") if (det and not escalate) else None,
        "basis": det["basis"] if (det and "reaction_unmatched" not in reasons and ver and ver["status"] == "passed") else [],
        "conflicts": det.get("conflicts", []) if det else [],
        "escalation": {"required": escalate, "reasons": reasons,
                       "path": "Route to the qualified person for pharmacovigilance (QPPV) with the trace attached." if escalate else None},
        "requires_qppv_confirmation": bool(escalate or (det and det.get("conflicts")) or ctx.degraded),
        "verification": ver, "degraded": ctx.degraded, "entities": {k: v for k, v in ctx.ents.items() if k != "reaction_text"},
        "disclaimer": DISCLAIMER,
    }
    if escalate and det and not assessment_unverified:
        res["partial_findings"] = {"serious": det.get("serious"), "expectedness": det.get("expectedness"),
                                   "expectedness_confidence": det.get("expectedness_confidence"), "seriousness_reasons": det.get("seriousness_reasons")}
    # justification text: LLM-drafted when available, filtered; template otherwise
    summary = None
    if ctx.llm.client.name != "none" and not escalate and ctx.llm.breaker.state != "open":
        try:
            facts = f"route={res['route']}; serious={res['serious']}; expectedness={res['expectedness']}; basis={res['route_basis']}"
            prompt, ver_p = render("summary_v1", facts=facts)
            ctx.prompt_versions["summary"] = ver_p
            summary = parse_json(ctx.llm.call(ctx, "draft_summary", "summary_v1", ver_p, [{"role": "user", "content": prompt}])).get("summary")
        except LLMUnavailable as exc:
            ctx.degrade("llm_unavailable:summary")
            res["degraded"] = ctx.degraded
    if summary and check_output_text(summary):
        ctx.emit("guardrail", kind="output_filter_blocked_summary", violations=check_output_text(summary))
        summary = None
    res["summary"] = summary or _template_summary(ctx, res)
    viol = check_output_result(res)
    if viol:                                           # the system may not issue what it is not authorised to
        ctx.emit("guardrail", kind="output_filter_blocked_result", violations=viol)
        res.update(route="human_escalation", basis=[], deadline=None,
                   serious=None, seriousness_reasons=[], expectedness=None, expectedness_confidence=None,
                   assessment_status="unverified", unverified_fields=["seriousness", "expectedness"],
                   escalation={"required": True, "reasons": reasons + ["output_filter_blocked"], "path": "QPPV review"},
                   summary="Output withheld by the safety filter; escalated to a qualified person.")
    ctx.final = res
    return "ok"


NODES = {"intake": run_intake, "evidence": run_evidence, "assess": run_assess, "verify": run_verify,
         "retry": node_retry, "finalize": node_finalize}
AGENT_OF = {"intake": "intake", "evidence": "evidence", "assess": "assessor", "verify": "verifier", "retry": "controller", "finalize": "finalizer"}


# ------------------------------------------------------------------ edges
def route_after_intake(ctx):
    return "finalize" if (getattr(ctx, "agent_error", None) or blocking(ctx)) else "evidence"


def route_after_evidence(ctx): return "finalize" if getattr(ctx, "agent_error", None) else "assess"
def route_after_assess(ctx): return "finalize" if getattr(ctx, "agent_error", None) else "verify"


def route_after_verify(ctx):
    v = ctx.verification
    if getattr(ctx, "agent_error", None) or not v or v["status"] == "passed":
        return "finalize"
    if v["retryable"] and ctx.s.agentic and ctx.strategy + 1 < ctx.s.max_retries_strategy and ctx.time_left() > 4:
        return "retry"
    return "finalize"


EDGES = {"intake": route_after_intake, "evidence": route_after_evidence, "assess": route_after_assess,
         "verify": route_after_verify, "retry": lambda ctx: "evidence", "finalize": None}


def _timed(name):
    fn = NODES[name]

    def run(ctx):
        t0, agent = time.monotonic(), AGENT_OF[name]
        ctx.step_name = name
        current_step.set(name)
        ctx.graph_steps += 1
        outcome = "ok"
        with tracing.span(f"agent.{name}", run_id=ctx.run_id, strategy=ctx.strategy):
            try:
                fn(ctx)
                if name == "evidence" and ctx.s.agentic and unmet_needs(ctx):
                    outcome = "incomplete"
                if name == "assess" and ctx.determination and ctx.determination["status"] == "insufficient":
                    outcome = "incomplete"
                if name == "verify" and ctx.verification and ctx.verification["status"] != "passed":
                    outcome = "failed_check"
            except Exception as exc:  # noqa  agent crash => escalate, never a confident answer
                ctx.agent_error, outcome = name, "error"
                ctx.emit("agent_error", error=f"{type(exc).__name__}: {exc}"[:200])
        lat = time.monotonic() - t0
        metrics.STEP_SECONDS.labels(name).observe(lat)
        metrics.AGENT_RUNS.labels(agent, outcome).inc()
        ctx.emit("step_done", step=name, latency_ms=round(lat * 1000), outcome=outcome)
    return run


def run_sequential(ctx):
    node = "intake"
    while node:
        _timed(node)(ctx)
        if node == "finalize":
            break
        node = EDGES[node](ctx)
        if ctx.graph_steps > 40:
            raise RuntimeError("graph step limit")


def run_langgraph(ctx):
    try:
        from typing_extensions import TypedDict
    except ImportError:  # pragma: no cover
        from typing import TypedDict
    from langgraph.graph import END, START, StateGraph

    class S(TypedDict, total=False):
        ctx: object
        phase: str

    g = StateGraph(S)
    for name in NODES:
        runner = _timed(name)
        g.add_node(name, lambda st, r=runner, n=name: (r(st["ctx"]), {"phase": n})[1])
    g.add_edge(START, "intake")
    for name, router in EDGES.items():
        if router is None:
            g.add_edge(name, END)
        else:
            targets = {"intake": ["evidence", "finalize"], "evidence": ["assess", "finalize"], "assess": ["verify", "finalize"],
                       "verify": ["finalize", "retry"], "retry": ["evidence"]}[name]
            g.add_conditional_edges(name, lambda st, r=router: r(st["ctx"]), {t: t for t in targets})
    g.compile().invoke({"ctx": ctx, "phase": "start"}, {"recursion_limit": 40})
