"""What evidence is still missing, and what to do about it.

Two policies share one interface:
  * LLMPolicy  - the model proposes the next tool call (real agency), validated before use.
  * RulePolicy - a deterministic plan. Used when the model is unavailable, returns nothing valid,
                 or repeats itself. This is the graceful-degradation path (A5).
Termination never depends on the model: each need has a bounded number of attempts."""
import json
import re

from pv.agents.tools import SOURCES, TOOL_SPECS, TOOLS
from pv.llm.client import LLMUnavailable, parse_json
from pv.llm.prompts import render
from pv.rules import ANCHORS, FLAG_ANCHOR, IPC, NDCT, anchor_query, find_anchor, match_label
from pv.corpus.glossary import term_regex

MAX_ATTEMPTS = 2


def label_terms(ctx):
    terms, seen = [], set()
    for term in ctx.llm_terms + ctx.ents.get("candidate_terms", []):
        key = re.sub(r"[\s\-]+", " ", term.strip().casefold())
        if key and key not in seen:
            seen.add(key)
            terms.append(term)
    return terms[:8]


def label_has_specific_hit(ctx):
    src = ctx.ents.get("label_source")
    _, _, hits = match_label(label_terms(ctx), ctx.evidence, src, [], ctx.searched_label_terms)
    return any(h["specific"] for h in hits)


def required_anchors(ctx):
    e = ctx.ents
    if e.get("no_event_described"):
        return ["valid_icsr"]
    flags = e.get("seriousness_flags", [])
    a = [FLAG_ANCHOR[f] for f in flags] or ["sev_list"]
    return a + (["tl_serious", "ndct_exception", "ndct_psur"] if flags else ["tl_nonserious", "ndct_psur"])


def unmet_needs(ctx):
    needs = []
    if ctx.s.agentic and not ctx.ents.get("no_event_described") and ctx.ents.get("label_source"):
        if ctx.label_stage < 4 and not (ctx.label_stage >= 1 and label_has_specific_hit(ctx)):
            needs.append("label")
    for a in required_anchors(ctx):
        if not find_anchor(ctx.evidence, a) and ctx.need_attempts.get(a, 0) < MAX_ATTEMPTS:
            needs.append(a)
    return needs


def rule_action(ctx, needs):
    """Deterministic plan: first unmet need wins. `ctx.strategy` selects the formulation."""
    strat, e = ctx.strategy, ctx.ents
    src = e.get("label_source")
    for need in needs:
        if need == "label":
            terms = label_terms(ctx)
            if ctx.label_stage == 0:
                if strat == 0 and terms:
                    return dict(need=need, tool="search_label", args={"terms": terms[:4]}, why="search package insert for candidate medical terms")
                q = e["reaction_text"][:200] + (" adverse reactions warnings" if strat else "")
                return dict(need=need, tool="search_corpus", args={"query": q, "source": src, "k": 8 if strat else 5}, why="lay-language search of the package insert")
            if ctx.label_stage == 1 and len(terms) > 4:
                return dict(need=need, tool="search_label", args={"terms": terms[4:8]}, why="remaining candidate terms")
            read = [p for p in ctx.evidence.values() if p["source"] == src]
            derived = " ".join(ctx.index.top_terms(read, 6)) or e["reaction_text"][:120]
            return dict(need=need, tool="search_corpus", args={"query": derived, "source": src, "k": 6}, why="query derived from the label text already read")
        if need in ("tl_serious", "tl_nonserious"):
            if strat == 0 and ctx.need_attempts.get(need, 0) == 0:
                return dict(need=need, tool="get_clause", args={"identifier": "2.7.1" if need == "tl_serious" else "2.7.2", "source": IPC}, why="look up the reporting-timeline clause directly")
            q = ("serious adverse events reported within 15 calendar days" if need == "tl_serious" else "non-serious adverse events reported within 90 calendar days")
            return dict(need=need, tool="search_corpus", args={"query": q, "source": IPC, "k": 5}, why="search for the timeline provision")
        if need in ("ndct_exception", "ndct_psur"):
            tl = next((p for p in ctx.evidence.values() if p["source"] == IPC and ("2.7.1" in p["text"] or "2.7.2" in p["text"])), None)
            if tl and not ctx.derived_done:
                return dict(need=need, tool="find_modifiers", args={"passage_id": tl["id"]}, why="does another provision modify the timeline just read?")
            dq = getattr(ctx, "derived_query", None)
            if dq and ctx.need_attempts.get(need, 0) == 0:
                return dict(need=need, tool="search_corpus", args={"query": dq, "source": NDCT, "k": 6}, why="query derived from the provision just read")
            return dict(need=need, tool="search_corpus", args={"query": "serious unexpected adverse reactions fifteen days periodic safety update reports", "source": NDCT, "k": 6}, why="fallback strategy: direct search of the Rules")
        # seriousness / validity anchors in the IPC guidance: query with the criterion's own wording
        att = ctx.need_attempts.get(need, 0)
        if att == 0:
            return dict(need=need, tool="search_corpus", args={"query": anchor_query(need), "source": IPC, "k": 4 + 4 * strat}, why=f"find criterion text: {ANCHORS[need][2]}")
        return dict(need=need, tool="search_corpus", args={"query": "seriousness of the reaction tick appropriate reason for seriousness " + anchor_query(need), "source": IPC, "k": 8}, why="second formulation of the criterion query")
    return None


def _query_has(query, *phrases):
    q = re.sub(r"[\s\-]+", " ", query.casefold())
    return any(bool(re.search(r"\b" + re.escape(phrase) + r"\b", q)) if phrase.isdigit() else phrase in q
               for phrase in phrases)


def _action_need(ctx, needs, tool, args):
    if tool == "search_label" and "label" in needs:
        terms = args.get("terms")
        candidates = label_terms(ctx)
        if (set(args) == {"terms"} and isinstance(terms, list) and 1 <= len(terms) <= 4 and
                all(isinstance(t, str) and any(term_regex(c).fullmatch(t.strip()) for c in candidates) for t in terms)):
            return "label"
        return None

    if tool == "search_corpus":
        query, source = args.get("query"), args.get("source")
        k = args.get("k", 5)
        if (set(args) - {"query", "source", "k"} or not isinstance(query, str) or not query.strip() or
                source is not None and source not in SOURCES or type(k) is not int or not 1 <= k <= 8):
            return None
        if ("label" in needs and source == ctx.ents.get("label_source") and
                any(term_regex(term).search(query) for term in label_terms(ctx))):
            return "label"
        for need in needs:
            if need not in ANCHORS or source != ANCHORS[need][0]:
                continue
            if need == "tl_serious" and _query_has(query, "serious") and _query_has(query, "15", "fifteen"):
                return need
            if need == "tl_nonserious" and _query_has(query, "non serious") and _query_has(query, "90", "ninety"):
                return need
            if need == "ndct_exception" and _query_has(query, "unexpected") and _query_has(query, "15", "fifteen"):
                return need
            if need == "ndct_psur" and _query_has(query, "psur", "periodic safety update") and _query_has(query, "30", "thirty"):
                return need
            if need.startswith("sev_"):
                terms = {
                    "sev_death": ("death", "died", "fatal"),
                    "sev_life": ("life-threatening", "life threatening"),
                    "sev_hosp": ("hospital", "hospitalisation", "hospitalization"),
                    "sev_disab": ("disability", "disabled"),
                    "sev_cong": ("congenital anomaly", "birth defect"),
                    "sev_med": ("medically important", "medical importance"),
                    "sev_list": ("seriousness", "seriousness criteria"),
                }.get(need, ())
                if _query_has(query, *terms):
                    return need
            if need == "valid_icsr" and _query_has(query, "valid icsr", "minimum criteria", "minimum information"):
                return need
        return None

    if tool == "get_clause":
        identifier = args.get("identifier")
        source = args.get("source")
        if set(args) <= {"identifier", "source"} and isinstance(identifier, str) and source == IPC:
            if "tl_serious" in needs and identifier == "2.7.1":
                return "tl_serious"
            if "tl_nonserious" in needs and identifier == "2.7.2":
                return "tl_nonserious"
        return None

    if tool == "find_modifiers" and any(n in needs for n in ("ndct_exception", "ndct_psur")):
        passage = ctx.evidence.get(args.get("passage_id")) if set(args) == {"passage_id"} else None
        if (passage and passage.get("source") == IPC and
                re.search(r"\b2\.7\.[12]\b", passage.get("text", ""))):
            return next(n for n in needs if n in ("ndct_exception", "ndct_psur"))
    return None


def llm_action(ctx, needs):
    """Ask the model for the next tool call. Returns validated action or None (-> rule plan)."""
    obs = [f"{t['tool']}->{len(t.get('passage_ids', []))} passages" for t in ctx.trace if t["type"] == "tool_call"][-4:]
    prompt, ver = render("action_v1", needs=", ".join(needs), tools=json.dumps(TOOL_SPECS), observations="; ".join(obs) or "none yet")
    ctx.prompt_versions["action"] = ver
    try:
        text = ctx.llm.call(ctx, "evidence_action", "action_v1", ver, [{"role": "user", "content": prompt}])
    except LLMUnavailable as exc:
        ctx.degrade("llm_unavailable:action")
        return None
    d = parse_json(text)
    if d.get("action") == "continue":
        return None
    tool, args = d.get("tool"), d.get("args")
    if tool in TOOLS and isinstance(args, dict):
        need = _action_need(ctx, needs, tool, args)
        key = (tool, json.dumps(args, sort_keys=True))
        if need and key not in ctx.seen_calls:
            return dict(need=need, tool=tool, args=args, why="chosen by model", chosen_by="llm")
    ctx.emit("policy_override", reason="llm_action_not_relevant_to_unmet_need", llm_choice=tool)
    ctx.degrade("llm_unusable:action")
    return None


def normalise_terms(ctx):
    """LLM lay -> medical translation (prompt is versioned). Falls back to the glossary seeds."""
    if ctx.llm.client.name == "none":
        ctx.mode = "rules_only"
        ctx.emit("decision", what="rules_only_mode", note="no LLM provider configured; glossary seeds + deterministic plan")
        return
    try:
        prompt, ver = render(ctx.s.prompt_normalise, drug=ctx.ents["suspect_drug"][:100], narrative=ctx.ents["reaction_text"][:1500])
        ctx.prompt_versions["normalise"] = ver
        text = ctx.llm.call(ctx, "normalise_terms", ctx.s.prompt_normalise, ver, [{"role": "user", "content": prompt}])
    except LLMUnavailable as exc:
        ctx.degrade("llm_unavailable:normalise")
        return
    terms = parse_json(text).get("terms")
    if not isinstance(terms, list):
        ctx.degrade("llm_unusable:normalise")
        ctx.emit("decision", what="llm_terms", terms=[], fallback="glossary")
        return
    valid_terms = [t.strip() for t in terms if isinstance(t, str) and 2 < len(t.strip()) < 60][:6]
    if not valid_terms:
        ctx.degrade("llm_unusable:normalise")
        ctx.emit("decision", what="llm_terms", terms=[], fallback="glossary")
        return
    ctx.llm_terms = valid_terms
    ctx.emit("decision", what="llm_terms", terms=ctx.llm_terms)
