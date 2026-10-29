"""Agent 3: the Assessor. Turns retrieved evidence into a determination. Deterministic: the model
is not asked to decide seriousness, expectedness or route. Every claim gets a verbatim quote."""
from pv.agents.policy import label_terms
from pv.rules import (FLAG_ANCHOR, IME_TERMS, basis_item, decide, deadline, match_label)


def run_assess(ctx):
    e, ev, s = ctx.ents, ctx.evidence, ctx.s
    det = {"status": "ok", "basis": [], "missing": [], "reasons": [], "conflicts": []}
    basis = det["basis"]

    def need(anchor, claim):
        b = basis_item(ev, anchor, claim)
        if b: basis.append(b)
        else: det["missing"].append(anchor)

    if e.get("no_event_described"):
        det.update(serious=False, expectedness="not_applicable", expectedness_confidence=1.0, route="non_reportable",
                   route_basis="No adverse event is described, so the minimum criteria for a valid ICSR (IPC 2.5) are not met.")
        need("valid_icsr", "A valid ICSR requires an identifiable adverse event; none is described")
        return _fin(ctx, det)

    flags = e["seriousness_flags"]
    serious = bool(flags)
    det["serious"] = serious
    det["seriousness_reasons"] = flags
    for f in flags:
        need(FLAG_ANCHOR[f], f"Seriousness criterion met: {f.replace('_', ' ')}")
    if not flags:
        need("sev_list", "No seriousness criterion was reported (checked against the IPC list of criteria)")

    exp, conf, hits = match_label(label_terms(ctx), ev, e["label_source"], e["direct_terms"], ctx.searched_label_terms)
    det["expectedness"], det["expectedness_confidence"] = exp, conf
    for h in hits[:3]:
        if h["specific"] or exp != "expected":
            basis.append({"claim": f"Reaction term '{h['term']}' is listed in the package insert", "anchor": "label_listing",
                          "passage_id": h["passage_id"], "source": h["source"], "page": h["page"], "quote": h["quote"]})
    if exp == "unexpected":
        basis.append({"claim": "Reaction not listed in the package insert", "anchor": "label_absence", "kind": "absence",
                      "terms_searched": sorted(ctx.searched_label_terms), "source": e["label_source"]})
    if exp == "unmatched" or conf < s.label_match_min:
        det["reasons"].append("reaction_unmatched")
    if not serious and any(h["term"].lower() in IME_TERMS for h in hits):
        det["reasons"].append("medically_important_unconfirmed")

    if exp == "unmatched" or conf < s.label_match_min:
        det["route"] = "human_escalation"
        det["route_basis"] = "The reaction could not be matched to the package insert with sufficient confidence."
        need("tl_serious" if serious else "tl_nonserious", "Reporting provision that would apply if confirmed")
        return _fin(ctx, det)

    d = decide(serious, exp)
    for a in d["anchors"]:
        need(a, ANCHOR_CLAIM.get(a, a))
    if d["conflict"]:
        det["conflicts"].append(d["conflict"])
    det.update(route=d["route"], route_basis=d["basis"], deadline=deadline(e["received_date"], d["days"]))
    if s.fault_injection == "fabricated_citation" and ctx.strategy == 0 and ctx.attempts == 0:
        basis.append({"claim": "Reporting window is 7 days", "anchor": "tl_serious", "passage_id": "ipc_mah_v2_p0033_c01",
                      "source": "ipc_mah_v2", "page": 33, "quote": "must be reported within 7 days of receipt"})   # fabricated on purpose
        ctx.emit("fault_injected", kind="fabricated_citation")
    return _fin(ctx, det)


ANCHOR_CLAIM = {
    "tl_serious": "Serious adverse events must be reported within 15 calendar days (IPC 2.7.1)",
    "tl_nonserious": "Non-serious adverse events must be reported within 90 calendar days (IPC 2.7.2)",
    "ndct_exception": "NDCT Fifth Schedule: serious unexpected reactions within 15 days, an exception to the PSUR cycle",
    "ndct_psur": "NDCT Fifth Schedule: periodic safety update reports within 30 days of the period end",
}


def _fin(ctx, det):
    if det["missing"]:
        det["status"] = "insufficient"
    elif det["reasons"]:
        det["status"] = "escalate"
    ctx.determination = det
    ctx.emit("decision", what="determination", status=det["status"], route=det.get("route"),
             serious=det.get("serious"), expectedness=det.get("expectedness"), missing=det["missing"], reasons=det["reasons"])
