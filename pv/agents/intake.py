"""Agent 1: Intake. Entity extraction (F3) and the checks that need no retrieval. Early exits (F7)."""
from pv.guardrails import injection_hits
from pv.rules import extract_entities

BLOCKING = ("drug_outside_corpus", "timing_unclear", "narrative_flag_conflict")


def run_intake(ctx):
    # Defence in depth: the boundary already rejects these, but jobs could be enqueued by other paths.
    if injection_hits(ctx.report["reaction_description"]) or injection_hits(ctx.report["suspect_drug"]):
        ctx.early_reasons.append("guardrail_blocked:injection")
        ctx.ents = {"label_source": None, "seriousness_flags": [], "candidate_terms": [], "direct_terms": [],
                    "reaction_text": "[withheld]", "suspect_drug": "[withheld]", "no_event_described": False,
                    "received_date": ctx.report.get("received_date")}
        return
    ctx.ents, reasons = extract_entities(ctx.report, ctx.drugs)
    ctx.early_reasons += reasons
    ctx.emit("decision", what="entities", drug=ctx.ents["drug_generic"], latency_days=ctx.ents["onset_latency_days"],
             flags=ctx.ents["seriousness_flags"], candidate_terms=ctx.ents["candidate_terms"], early_reasons=reasons)


def blocking(ctx):
    return [r for r in ctx.early_reasons if r.startswith(BLOCKING) or r.startswith("guardrail_blocked")]
