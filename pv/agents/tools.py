"""A1: tools the agent CHOOSES to call. Retrieval is a tool, not a fixed stage.
Every call: validated, budget-checked, run with its own timeout, and written to the trace."""
import re, time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout

from pv import metrics
from pv.agents.state import BudgetExceeded
from pv.corpus.glossary import term_regex

_POOL = ThreadPoolExecutor(max_workers=8)
SOURCES = {"ipc_mah_v2", "cdsco_rules", "tegretol_us_pi"}
REPORT_FIELDS = {"patient_age", "patient_sex", "suspect_drug", "start_date", "reaction_date", "received_date", "hospitalization",
                 "death", "life_threatening", "disability", "congenital_anomaly", "outcome", "reporter_type", "reaction_description"}
CUES = re.compile(r"\b(however|except|unless|provided that|subject to|notwithstanding|in case of|all cases involving|but)\b", re.I)
REFS = re.compile(r"\b(NDCT|New Drugs and Clinical Trials Rules|Schedule|Rule \d+|Rules?,? 2019|PSUR|periodic safety update)\b", re.I)
STOP = {"adverse", "events", "event", "reported", "receipt", "information", "source", "calendar", "days", "mah", "must", "email", "mail", "commission", "www", "gov", "ii", "iii", "page"}

TOOL_SPECS = {
    "search_corpus": {"args": {"query": "str", "source": "ipc_mah_v2|cdsco_rules|tegretol_us_pi (optional)", "k": "int<=8"},
                      "doc": "Ranked passages for a free-text query."},
    "get_clause": {"args": {"identifier": "e.g. 2.7.1", "source": "optional"}, "doc": "Passage containing a numbered clause."},
    "get_report_field": {"args": {"field": "name of a field of THIS request"}, "doc": "Read one field of the submitted report."},
    "find_modifiers": {"args": {"passage_id": "id of a passage already read"},
                       "doc": "Find 'however/except/unless' cues and cross-references in a passage, and a follow-up query derived from it."},
    "search_label": {"args": {"terms": "list of medical terms (<=4)"}, "doc": "Search the package insert for specific medical terms."},
}


def _add(ctx, passages):
    for p in passages:
        ctx.evidence.setdefault(p["id"], p)
    return list(dict.fromkeys(p["id"] for p in passages))


def t_search_corpus(ctx, query, source=None, k=5):
    if source and source not in SOURCES: raise ValueError("unknown source")
    ps = ctx.index.search(str(query)[:400], max(1, min(int(k), 8)), source)
    return {"passage_ids": _add(ctx, ps), "preview": [p["text"][:80] for p in ps[:2]]}


def t_get_clause(ctx, identifier, source=None):
    if not re.fullmatch(r"[\d.]{2,12}", str(identifier)): raise ValueError("bad clause id")
    ps = ctx.index.lookup_clause(str(identifier), source)
    return {"passage_ids": _add(ctx, ps), "found": bool(ps)}


def t_get_report_field(ctx, field):
    if field not in REPORT_FIELDS: raise ValueError("unknown field")
    v = ctx.report.get(field)
    return {"field": field, "value": v if field != "reaction_description" else "<DATA>" + str(v)[:500] + "</DATA>", "passage_ids": []}


def t_find_modifiers(ctx, passage_id):
    p = ctx.evidence.get(passage_id) or ctx.index.by_id.get(passage_id)
    if not p: raise ValueError("passage not read")
    sents = re.split(r"(?<=[.;])\s+", p["text"])
    cues = [s[:240] for s in sents if CUES.search(s)][:3]
    refs = sorted({m.group(0) for m in REFS.finditer(p["text"])})[:5]
    terms = ctx.index.top_terms([p], 6, exclude=STOP)
    extra = "unexpected expected periodic safety update"      # the second assessment dimension may modify the timeline
    return {"cues": cues, "cross_refs": refs, "derived_query": " ".join(terms[:4]) + " " + extra, "passage_ids": []}


def t_search_label(ctx, terms):
    src = ctx.ents.get("label_source")
    if not src: raise ValueError("no label source for this drug")
    found, previews = [], []
    for t in list(terms)[:4]:
        t = str(t)[:60]
        ctx.searched_label_terms.add(t.lower())
        ps = [p for p in ctx.index.search(t, 6, src) if term_regex(t).search(p["text"])][:2]
        found += ps
    return {"passage_ids": _add(ctx, found), "terms_searched": list(terms)[:4], "hits": len(found)}


TOOLS = {"search_corpus": t_search_corpus, "get_clause": t_get_clause, "get_report_field": t_get_report_field,
         "find_modifiers": t_find_modifiers, "search_label": t_search_label}


def call_tool(ctx, name, args, why=""):
    """Returns result dict (may contain 'error'). Raises BudgetExceeded only for budget violations."""
    if name not in TOOLS:
        ctx.emit("tool_call", tool=name, args=args, error="unknown_tool")
        return {"error": "unknown_tool", "passage_ids": []}
    ctx.check_budget("tool")
    ctx.tool_calls += 1
    t0 = time.monotonic()
    fut = _POOL.submit(TOOLS[name], ctx, **args)
    try:
        res = fut.result(timeout=min(ctx.s.tool_timeout_s, max(ctx.time_left(), 0.1)))
        err = None
    except FutTimeout:
        res, err = {"passage_ids": []}, "timeout"
    except TypeError as exc:
        res, err = {"passage_ids": []}, f"bad_args: {exc}"[:120]
    except Exception as exc:  # noqa
        res, err = {"passage_ids": []}, f"{type(exc).__name__}: {exc}"[:120]
    lat = time.monotonic() - t0
    metrics.TOOL_SECONDS.labels(name).observe(lat)
    source = ctx.ents.get("label_source") if name == "search_label" else args.get("source")
    ctx.emit("tool_call", tool=name, args=args, why=why, latency_ms=round(lat * 1000), passage_ids=res.get("passage_ids", []),
             error=err, backend=ctx.index.backend, **({"source": source} if source else {}),
             **({"derived_query": res["derived_query"], "cues": res["cues"]} if "derived_query" in res else {}))
    if err:
        res = {**res, "error": err}
    return res
