"""L3: evaluation metrics. Deterministic, so a run is reproducible and free. TruLens-style
'RAG triad' names are kept, but each is computed against the gold labels / corpus text, not by an
LLM judge (an LLM judge is a documented extension, see docs/EVALUATION.md).

For a run with an answered (non-escalated) result:
  groundedness      = share of cited claims whose quote appears verbatim (whitespace-insensitive) in the
                      TEXT OF THE CORPUS CHUNK it cites (re-read from the index, not from the run's own state)
  faithfulness      = share of the answer's assertions (seriousness, expectedness, route, deadline) that are
                      entailed by a cited quote (anchor regex must match the quote; days must equal the quote's days)
  context_relevance = share of retrieved passages that are relevant, i.e. on a gold (source, page)
  context_recall    = share of gold (source, page) passages that were retrieved
  hallucination     = answered AND (any citation fails to resolve OR the route is wrong OR it answered a case
                      that should have been escalated). 'Confident and unsupported' as a rate over ALL runs."""
import re

from pv.corpus.glossary import term_regex
from pv.rules import ANCHORS, FLAG_ANCHOR, squash


def _resolves(index, b):
    if not isinstance(b, dict):
        return False
    if b.get("kind") == "absence":
        return bool(b.get("terms_searched"))
    c = index.by_id.get(b.get("passage_id"))
    return bool(c and squash(b.get("quote")) in squash(c["text"]) and c["page"] == b.get("page"))


def groundedness(index, res):
    cl = res.get("basis")
    cl = cl if isinstance(cl, list) else []
    return 0.0 if not cl else sum(_resolves(index, b) for b in cl) / len(cl)


def faithfulness(res):
    b = res.get("basis")
    b = b if isinstance(b, list) else []
    quotes = [(x.get("anchor"), x.get("quote", ""), x) for x in b if isinstance(x, dict)]
    asserts = []
    seriousness_reasons = res.get("seriousness_reasons")
    seriousness_reasons = seriousness_reasons if isinstance(seriousness_reasons, list) else []
    if res.get("serious") is True:
        for f in seriousness_reasons:
            anchor = FLAG_ANCHOR.get(f)
            asserts.append(bool(anchor and any(a == anchor and re.search(ANCHORS[a][1], q, re.I) for a, q, _ in quotes)))
    elif res.get("serious") is False:
        asserts.append(any(a in ("sev_list", "valid_icsr") for a, _, _ in quotes))
    if res.get("expectedness") == "expected":
        asserts.append(any(a == "label_listing" for a, _, _ in quotes))
    elif res.get("expectedness") == "unexpected":
        asserts.append(any(a == "label_absence" for a, _, _ in quotes))
    deadline = res.get("deadline")
    if isinstance(deadline, dict) and isinstance(deadline.get("days"), int):
        d = deadline["days"]
        asserts.append(any(a in ("tl_serious", "tl_nonserious") and re.search(rf"\b{d}\b|\b{'fifteen' if d == 15 else 'ninety'}\b", q, re.I) for a, q, _ in quotes))
    return 0.0 if not asserts else sum(asserts) / len(asserts)


def retrieval_scores(index, evidence_ids, gold):
    gold_pages = {(g["source"], g["page"]) for g in gold}
    pages = {(index.by_id[i]["source"], index.by_id[i]["page"]) for i in evidence_ids if i in index.by_id}
    rel = sum(1 for i in evidence_ids if i in index.by_id and (index.by_id[i]["source"], index.by_id[i]["page"]) in gold_pages)
    rel_ratio = rel / len(evidence_ids) if evidence_ids else 0.0
    recall = len(gold_pages & pages) / len(gold_pages) if gold_pages else None
    return rel_ratio, recall


def aggregate(rows):
    valid = [r for r in rows if r["kind"] == "valid"]
    answered = [r for r in valid if r["answered"]]
    mean = lambda xs: round(sum(xs) / len(xs), 4) if xs else None
    esc_gold = [r for r in valid if r["expected_route"] == "human_escalation"]
    return {
        "scenarios": len(rows), "valid_cases": len(valid), "boundary_cases": len(rows) - len(valid),
        "route_accuracy": mean([r["route_ok"] for r in valid]),
        "answered_rate": mean([r["answered"] for r in valid]),
        "seriousness_accuracy": mean([r["serious_ok"] for r in valid if r["serious_ok"] is not None]),
        "expectedness_accuracy": mean([r["exp_ok"] for r in valid if r["exp_ok"] is not None]),
        "escalation_reason_accuracy": mean([r["reasons_ok"] for r in valid if r["expected_reasons"]]),
        "boundary_rejection_rate": mean([r["route_ok"] for r in rows if r["kind"] == "reject"]),
        "groundedness": mean([r["groundedness"] for r in answered]) if answered else 0.0,
        "faithfulness": mean([r["faithfulness"] for r in answered]) if answered else 0.0,
        "context_relevance": mean([r["context_relevance"] for r in valid if r["context_relevance"] is not None]),
        "context_recall": mean([r["context_recall"] for r in valid if r["context_recall"] is not None]),
        "hallucination_rate": mean([r["hallucinated"] for r in valid]),
        "unsafe_answer_rate": mean([1 if (r["answered"] and r["expected_route"] == "human_escalation") else 0 for r in esc_gold]),
        "mean_tool_calls": mean([r["tool_calls"] for r in valid]),
        "mean_cost_usd": mean([r["cost_usd"] for r in valid]),
    }
