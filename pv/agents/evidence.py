"""Agent 2: the Evidence agent. Owns tool use. Bounded by steps, tool calls and wall-clock."""
import json

from pv.agents.policy import llm_action, normalise_terms, rule_action, unmet_needs
from pv.agents.state import BudgetExceeded
from pv.agents.tools import call_tool
from pv.llm.resilience import OPEN


def run_evidence(ctx):
    if not hasattr(ctx, "need_attempts"):
        ctx.need_attempts = {}
    if not ctx.s.agentic:                        # single-pass baseline (the 'before' in the L3 ablation)
        q = (ctx.ents["reaction_text"] + " " + ctx.ents["suspect_drug"])[:300]
        ctx.steps += 1
        call_tool(ctx, "search_corpus", {"query": q, "k": 8}, "baseline: one embedding/lexical pass, no follow-up")
        return
    if ctx.strategy == 0 and not ctx.llm_terms and ctx.ents.get("label_source") and not ctx.ents.get("no_event_described"):
        normalise_terms(ctx)
    while True:
        needs = unmet_needs(ctx)
        if not needs:
            ctx.emit("decision", what="evidence_sufficient", evidence=len(ctx.evidence))
            return
        try:
            ctx.check_budget("step")
            ctx.steps += 1
            action = None
            if ctx.llm.breaker.state != OPEN and ctx.llm.client.name != "none":
                action = llm_action(ctx, needs)
            if action is None:
                action = rule_action(ctx, needs)
                if action is None:
                    ctx.emit("decision", what="no_further_actions", unmet=needs)
                    return
                action["chosen_by"] = "rules"
            key = (action["tool"], json.dumps(action["args"], sort_keys=True))
            ctx.seen_calls.add(key)
            need = action["need"]
            res = call_tool(ctx, action["tool"], action["args"], f"[{action['chosen_by']}] {action['why']}")
            if action["tool"] == "find_modifiers" and "derived_query" in res:
                ctx.derived_query = res["derived_query"]
            if action["tool"] == "find_modifiers":
                pass                                  # reading a passage for modifiers is not an attempt at the need itself
            elif need == "label":
                ctx.label_stage += 1
            else:
                ctx.need_attempts[need] = ctx.need_attempts.get(need, 0) + 1
        except BudgetExceeded as exc:
            ctx.emit("decision", what="budget_exhausted", kind=exc.kind)
            ctx.budget_exhausted = exc.kind
            return
