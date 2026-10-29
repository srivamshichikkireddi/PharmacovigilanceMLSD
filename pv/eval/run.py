"""Batch evaluation from the command line (L3).

    python -m pv.eval.run --label agentic
    AGENTIC_RETRIEVAL=false python -m pv.eval.run --label single_pass_baseline
    python -m pv.eval.run --validate-gold          # check every gold passage exists in the corpus
"""
import argparse, json, re, sys, time
from datetime import date
from pathlib import Path

from pv.config import settings
from pv.corpus.retriever import get_index
from pv.eval import metrics as M
from pv.llm.client import make_client
from pv.llm.resilience import ResilientLLM
from pv.rules import ANCHORS
from pv.schemas import validate_report
from pv.agents.pipeline import run_case
from pv.corpus.glossary import term_regex

TODAY = date(2026, 10, 27)


def validate_gold(index, scenarios):
    bad = 0
    for s in scenarios:
        for g in s["gold"]:
            chunks = [c for c in index.chunks if c["source"] == g["source"] and c["page"] == g["page"]]
            rx = re.compile(ANCHORS[g["anchor"]][1], re.I) if "anchor" in g else term_regex(g["term"])
            if not any(rx.search(c["text"]) for c in chunks):
                bad += 1; print("GOLD MISSING", s["id"], g)
    print(f"gold check: {sum(len(s['gold']) for s in scenarios)} passages, {bad} missing")
    return bad == 0


def evaluate(scenarios, index, llm, label):
    rows = []
    for s in scenarios:
        clean, errs = validate_report(s["report"], today=TODAY)
        if s["kind"] == "reject":
            codes = {e["code"] for e in errs}
            ok = bool(errs) and s["expect"]["error_code"] in codes
            rows.append(dict(id=s["id"], kind="reject", route_ok=int(ok), expected_route="quarantined", route="quarantined" if errs else "ACCEPTED", answered=False))
            continue
        assert not errs, (s["id"], errs)
        t0 = time.monotonic()
        res, trace = run_case(s["id"], clean, index, llm, settings)
        ev_ids = list({i for t in trace if t["type"] == "tool_call" for i in t.get("passage_ids", [])})
        rel, rec = M.retrieval_scores(index, ev_ids, s["gold"])
        exp = s["expect"]
        answered = res["route"] != "human_escalation"
        resolves = all(M._resolves(index, b) for b in res["basis"])
        route_ok = res["route"] == exp["route"]
        rows.append(dict(
            id=s["id"], kind="valid", description=s["description"], expected_route=exp["route"], route=res["route"], route_ok=int(route_ok),
            answered=int(answered), serious_ok=(None if exp["serious"] is None else int(res["serious"] == exp["serious"])),
            exp_ok=(None if exp["expectedness"] is None else int(res["expectedness"] == exp["expectedness"])),
            reasons=res["escalation"]["reasons"], expected_reasons=exp["escalation_reasons"],
            reasons_ok=int(all(any(er in r for r in res["escalation"]["reasons"]) for er in exp["escalation_reasons"])),
            groundedness=M.groundedness(index, res) if answered else None, faithfulness=M.faithfulness(res) if answered else None,
            context_relevance=rel, context_recall=rec,
            hallucinated=int(answered and (not resolves or not route_ok or exp["route"] == "human_escalation")),
            tool_calls=res["tool_calls"], steps=res["steps"], cost_usd=res["cost"]["total_usd"], seconds=round(time.monotonic() - t0, 3),
            verification=(res["verification"] or {}).get("status"), degraded=res["degraded"], prompt_versions=res["prompt_versions"]))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="run")
    ap.add_argument("--scenarios", default="eval/scenarios.json")
    ap.add_argument("--validate-gold", action="store_true")
    ap.add_argument("--out", default="eval/results")
    a = ap.parse_args()
    scen = json.loads(Path(a.scenarios).read_text())
    index = get_index()
    if a.validate_gold:
        sys.exit(0 if validate_gold(index, scen) else 1)
    llm = ResilientLLM(make_client(settings), settings)
    rows = evaluate(scen, index, llm, a.label)
    agg = M.aggregate(rows)
    meta = {"label": a.label, "agentic": settings.agentic, "provider": settings.llm_provider, "prompt": settings.prompt_normalise,
            "corpus_version": index.version, "backend": index.backend}
    Path(a.out).mkdir(parents=True, exist_ok=True)
    Path(a.out, f"{a.label}.json").write_text(json.dumps({"meta": meta, "aggregate": agg, "rows": rows}, indent=1))
    print(f"\n{'id':7}{'expected':24}{'got':24}ok  tools  description")
    for r in rows:
        print(f"{r['id']:7}{r['expected_route']:24}{r['route']:24}{'Y' if r['route_ok'] else 'N'}   {r.get('tool_calls', '-'):<5}  {r.get('description', '')[:48]}")
    print("\n" + json.dumps({**meta, **agg}, indent=1))


if __name__ == "__main__":
    main()
