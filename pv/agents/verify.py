"""A3: the verifier. Independent of the assessor: it trusts nothing the assessor says about itself.
An answer is released only if every citation resolves to text that was actually retrieved in this
run, the quote is really in that text, and the route follows from the determination."""
from pv.rules import ANCHORS, decide, squash
from pv.corpus.glossary import term_regex


def _same_term(left, right):
    return (isinstance(left, str) and isinstance(right, str) and bool(left.strip()) and bool(right.strip())
            and bool(term_regex(left).fullmatch(right.strip())))


def run_verify(ctx):
    det, ev, fails, checks = ctx.determination, ctx.evidence, [], 0
    kind = "ok"
    if det is None:
        ctx.verification = {"status": "failed", "failures": ["no_determination"], "checks": 0, "retryable": False}
        return
    if det["status"] == "insufficient":
        fails.append("missing_evidence:" + ",".join(det["missing"]))
        kind = "evidence"
    for b in det["basis"]:
        checks += 1
        if b.get("kind") == "absence":
            terms = b.get("terms_searched") or []
            source = b.get("source")
            if not isinstance(terms, list) or not terms or any(not isinstance(term, str) or not term.strip() for term in terms):
                fails.append("absence_claim_without_search_terms"); kind = "unsupported"
                terms = []
            if not isinstance(source, str) or not source:
                fails.append("absence_claim_without_label_source"); kind = "unsupported"
                source = None
            label_calls = [t for t in ctx.trace if t.get("type") == "tool_call" and
                           t.get("tool") == "search_label" and t.get("source") == source]
            searched = []
            for call in label_calls:
                args = call.get("args")
                call_terms = args.get("terms", []) if isinstance(args, dict) else []
                if isinstance(call_terms, list):
                    searched.extend(call_terms)
            if not terms or not all(any(_same_term(term, found) for found in searched) for term in terms):
                fails.append("absence_claim_without_matching_label_search"); kind = "unsupported"
            passages = []
            missing_passages = []
            for call in ctx.trace:
                if call.get("type") != "tool_call" or call.get("source") != source:
                    continue
                for passage_id in call.get("passage_ids", []):
                    passage = ev.get(passage_id)
                    if passage is None:
                        missing_passages.append(passage_id)
                    elif passage.get("source") != source:
                        fails.append(f"absence_search_source_mismatch:{passage_id}"); kind = "unsupported"
                    else:
                        passages.append(passage)
            if missing_passages:
                fails.append("absence_search_passage_not_retrieved"); kind = "unsupported"
            if any(term_regex(term).search(p.get("text", "")) for term in terms for p in passages):
                fails.append("absence_claim_contradicted_by_passage"); kind = "unsupported"
            continue
        p = ev.get(b.get("passage_id"))
        if p is None:
            fails.append(f"citation_not_retrieved:{b.get('passage_id')}"); kind = "unsupported"; continue
        if squash(b.get("quote")) not in squash(p["text"]):
            fails.append(f"quote_not_in_passage:{b['passage_id']}"); kind = "unsupported"; continue
        if b.get("page") != p["page"] or b.get("source") != p["source"]:
            fails.append(f"citation_metadata_mismatch:{b['passage_id']}"); kind = "unsupported"; continue
        a = b.get("anchor")
        if a in ANCHORS:
            import re
            if not re.search(ANCHORS[a][1], p["text"], re.I) and not re.search(ANCHORS[a][1], b["quote"], re.I):
                fails.append(f"quote_does_not_support_claim:{a}"); kind = "unsupported"
    if det["status"] == "ok":
        checks += 1
        if det["route"] == "non_reportable":
            if not any(b.get("anchor") == "valid_icsr" for b in det["basis"]):
                fails.append("non_reportable_without_validity_citation"); kind = "evidence"
        else:
            want = decide(det["serious"], det["expectedness"])
            if det["route"] != want["route"]:
                fails.append(f"route_inconsistent:{det['route']}!={want['route']}"); kind = "unsupported"
            elif det.get("deadline", {}).get("days") != want["days"]:
                fails.append("deadline_inconsistent"); kind = "unsupported"
            if not any(b.get("anchor") in ("tl_serious", "tl_nonserious") for b in det["basis"]):
                fails.append("route_has_no_provision_citation"); kind = "evidence"
    retryable = bool(fails) and kind in ("evidence", "unsupported") and not ctx.budget_exhausted
    ctx.verification = {"status": "failed" if fails else "passed", "failures": fails, "checks": checks,
                        "citations_checked": len(det["basis"]), "retryable": retryable, "strategy": ctx.strategy}
    ctx.emit("verification", outcome=ctx.verification["status"], failures=fails, citations_checked=len(det["basis"]), strategy=ctx.strategy)
