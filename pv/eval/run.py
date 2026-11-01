"""Batch evaluation from the command line (L3).

    python -m pv.eval.run --label agentic
    AGENTIC_RETRIEVAL=false python -m pv.eval.run --label single_pass_baseline
    python -m pv.eval.run --validate-gold          # check every gold passage exists in the corpus
"""
import argparse, hashlib, json, os, re, sys, time
from datetime import date
from pathlib import Path

from pv.config import settings
from pv.corpus.retriever import get_index
from pv.eval import metrics as M
from pv.llm.client import make_client
from pv.llm.resilience import ResilientLLM, price
from pv.rules import ANCHORS
from pv.schemas import validate_report
from pv.agents.pipeline import run_case
from pv.corpus.glossary import term_regex
from pv.eval.baseline import run_baseline
from pv.llm.client import LLMQuotaExceeded

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


class ProviderLimitEncountered(RuntimeError):
    def __init__(self, scenario_id, trace=None):
        super().__init__(scenario_id)
        self.scenario_id = scenario_id
        self.trace = trace or []


def _provider_limit_in_trace(trace):
    return any(t.get("type") == "llm_call" and t.get("error") == "provider_rate_limit_or_quota"
               for t in trace)


def evaluate(scenarios, index, llm, label, baseline=False, cached_rows=None, checkpoint=None, batch_size=5):
    cached_rows = cached_rows or {}
    rows_by_id = dict(cached_rows)
    completed = 0
    for s in scenarios:
        if s["id"] in rows_by_id:
            completed += 1
            continue
        clean, errs = validate_report(s["report"], today=TODAY)
        if s["kind"] == "reject":
            codes = {e["code"] for e in errs}
            ok = bool(errs) and s["expect"]["error_code"] in codes
            rows_by_id[s["id"]] = dict(id=s["id"], kind="reject", route_ok=int(ok), expected_route="quarantined", route="quarantined" if errs else "ACCEPTED", answered=False)
            completed += 1
            if checkpoint:
                checkpoint([rows_by_id[x["id"]] for x in scenarios if x["id"] in rows_by_id])
            continue
        assert not errs, (s["id"], errs)
        t0 = time.monotonic()
        try:
            if baseline:
                res, trace, ev_ids = run_baseline(s["id"], clean, index, llm, settings)
            else:
                res, trace = run_case(s["id"], clean, index, llm, settings)
                ev_ids = list({i for t in trace if t["type"] == "tool_call" for i in t.get("passage_ids", [])})
        except LLMQuotaExceeded as exc:
            raise ProviderLimitEncountered(s["id"], getattr(exc, "trace", None)) from exc
        if _provider_limit_in_trace(trace):
            raise ProviderLimitEncountered(s["id"], trace)
        rel, rec = M.retrieval_scores(index, ev_ids, s["gold"])
        exp = s["expect"]
        answered = res["route"] != "human_escalation"
        resolves = all(M._resolves(index, b) for b in res["basis"])
        route_ok = res["route"] == exp["route"]
        row = dict(
            id=s["id"], kind="valid", description=s["description"], expected_route=exp["route"], route=res["route"], route_ok=int(route_ok),
            answered=int(answered), serious_ok=(None if exp["serious"] is None else int(res["serious"] == exp["serious"])),
            exp_ok=(None if exp["expectedness"] is None else int(res["expectedness"] == exp["expectedness"])),
            reasons=res["escalation"]["reasons"], expected_reasons=exp["escalation_reasons"],
            reasons_ok=int(all(any(er in r for r in res["escalation"]["reasons"]) for er in exp["escalation_reasons"])),
            groundedness=M.groundedness(index, res) if answered else 0.0, faithfulness=M.faithfulness(res) if answered else 0.0,
            context_relevance=rel, context_recall=rec,
            hallucinated=int(answered and (not res["basis"] or not resolves or not route_ok or exp["route"] == "human_escalation")),
            tool_calls=res["tool_calls"], steps=res["steps"], cost_usd=res["cost"]["total_usd"], seconds=round(time.monotonic() - t0, 3),
            prompt_tokens=res["cost"]["prompt_tokens"], completion_tokens=res["cost"]["completion_tokens"],
            verification=(res.get("verification") or {}).get("status"), degraded=res["degraded"], prompt_versions=res["prompt_versions"],
            provider_calls=sum(t.get("attempts", 0) for t in trace if t.get("type") == "llm_call"),
            result_schema_valid=res.get("result_schema_valid", True), result=res, trace=trace, retrieved_passage_ids=ev_ids)
        rows_by_id[s["id"]] = row
        completed += 1
        ordered_rows = [rows_by_id[x["id"]] for x in scenarios if x["id"] in rows_by_id]
        if checkpoint:
            checkpoint(ordered_rows)
        if batch_size > 0 and completed % batch_size == 0:
            _print_batch_usage(ordered_rows, completed)
    return [rows_by_id[x["id"]] for x in scenarios if x["id"] in rows_by_id]


def _print_batch_usage(rows, completed, extra_provider_calls=0):
    prompt_tokens = sum(r.get("prompt_tokens", 0) for r in rows)
    completion_tokens = sum(r.get("completion_tokens", 0) for r in rows)
    cost = sum(r.get("cost_usd", 0) for r in rows)
    print(f"BATCH completed={completed} provider_calls={sum(r.get('provider_calls', 0) for r in rows) + extra_provider_calls} "
          f"tokens={prompt_tokens}+{completion_tokens} notional_cost_usd={cost:.8f}", flush=True)


def _scenario_digest(scenarios):
    canonical = json.dumps(scenarios, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _save_results(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="run")
    ap.add_argument("--scenarios", default="eval/scenarios.json")
    ap.add_argument("--validate-gold", action="store_true")
    ap.add_argument("--out", default="eval/results")
    ap.add_argument("--baseline", action="store_true", help="evaluation-only one-retrieval, one-call answer baseline")
    ap.add_argument("--resume", action="store_true", help="reuse completed cached scenarios for this exact input")
    ap.add_argument("--batch-size", type=int, default=5, help="print cumulative provider usage after each batch")
    a = ap.parse_args()
    scen = json.loads(Path(a.scenarios).read_text())
    index = get_index()
    if a.validate_gold:
        sys.exit(0 if validate_gold(index, scen) else 1)
    price(settings, 0, 0)
    llm = ResilientLLM(make_client(settings), settings)
    output_path = Path(a.out, f"{a.label}.json")
    meta = {"label": a.label, "mode": "single_pass_retrieval_one_shot_answer" if a.baseline else "agentic",
            "agentic": False if a.baseline else settings.agentic, "provider": settings.llm_provider,
            "prompt": "single_pass_v1" if a.baseline else settings.prompt_normalise,
            "corpus_version": index.version, "backend": index.backend, "scenario_digest": _scenario_digest(scen),
            "provider_config_fingerprint": hashlib.sha256(
                f"{settings.llm_provider}|{settings.llm_base_url}|{settings.llm_model}".encode("utf-8")
            ).hexdigest()}
    cached_rows = {}
    latest_rows = []
    if a.resume and output_path.exists():
        existing = json.loads(output_path.read_text(encoding="utf-8"))
        if any(existing.get("meta", {}).get(k) != v for k, v in meta.items()):
            raise SystemExit("--resume refused: cached run configuration/scenarios do not match")
        cached_rows = {r["id"]: r for r in existing.get("rows", [])}

    def checkpoint(rows, status="running", stop_scenario=None, failed_attempt=None):
        nonlocal latest_rows
        latest_rows = rows
        payload_meta = {**meta, "status": status, "stop_scenario": stop_scenario}
        payload = {"meta": payload_meta, "aggregate": M.aggregate(rows), "rows": rows}
        if failed_attempt:
            payload["provider_failure"] = failed_attempt
        _save_results(output_path, payload)

    checkpoint([r for s in scen for r in [cached_rows.get(s["id"])] if r])
    try:
        rows = evaluate(scen, index, llm, a.label, baseline=a.baseline, cached_rows=cached_rows,
                        checkpoint=lambda completed: checkpoint(completed), batch_size=a.batch_size)
    except ProviderLimitEncountered as exc:
        rows = latest_rows
        failed_calls = sum(t.get("attempts", 0) for t in exc.trace if t.get("type") == "llm_call")
        checkpoint(rows, status="stopped_provider_limit_or_quota", stop_scenario=exc.scenario_id,
                   failed_attempt={"scenario_id": exc.scenario_id, "trace": exc.trace,
                                   "provider_calls": failed_calls})
        _print_batch_usage(rows, len(rows), extra_provider_calls=failed_calls)
        print(f"STOP: provider rate limit/quota at {exc.scenario_id}; retries disabled for the run; "
              f"partial results cached at {output_path}. Remaining provider quota is not available here.", flush=True)
        return 2
    agg = M.aggregate(rows)
    checkpoint(rows, status="complete")
    print(f"\n{'id':7}{'expected':24}{'got':24}ok  tools  description")
    for r in rows:
        print(f"{r['id']:7}{r['expected_route']:24}{r['route']:24}{'Y' if r['route_ok'] else 'N'}   {r.get('tool_calls', '-'):<5}  {r.get('description', '')[:48]}")
    _print_batch_usage(rows, len(rows))
    print("\n" + json.dumps({**meta, **agg}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
