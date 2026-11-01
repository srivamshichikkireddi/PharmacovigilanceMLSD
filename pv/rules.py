"""Regulatory knowledge as code: what each provision says, how to find its text in retrieved
passages, and the decision table. No model is involved here, so every determination is
deterministic and every claim carries a verbatim quote that the verifier re-checks (A3)."""
import json, re
from datetime import date, timedelta
from pathlib import Path

from pv.corpus.glossary import GENERIC_TERMS, MEDICAL_VOCAB, lay_candidates, term_regex

IPC, NDCT = "ipc_mah_v2", "cdsco_rules"

# anchor id -> (source document, regex that must match verbatim text, what it supports)
ANCHORS = {
    "sev_death": (IPC, r"Death: If, the patient died", "IPC 2.5.3.6(i) seriousness criterion: death"),
    "sev_life": (IPC, r"Life-threatening: If", "IPC 2.5.3.6(ii) seriousness criterion: life-threatening"),
    "sev_hosp": (IPC, r"Hospitali[sz]ation\s*/\s*prolongation of existing hospitali[sz]ation: If", "IPC 2.5.3.6(iii) seriousness criterion: hospitalisation"),
    "sev_disab": (IPC, r"Disability: If Adverse Events resulted", "IPC 2.5.3.6(iv) seriousness criterion: disability"),
    "sev_cong": (IPC, r"Congenital anomaly: If exposure", "IPC 2.5.3.6(v) seriousness criterion: congenital anomaly"),
    "sev_med": (IPC, r"Other medically important condition", "IPC 2.5.3.6(vi) seriousness criterion: other medically important condition"),
    "sev_list": (IPC, r"Seriousness of the reaction", "IPC 2.5.3.6 list of seriousness criteria"),
    "tl_serious": (IPC, r"All Serious Adverse Events must be reported by MAH within 15 calendar days", "IPC 2.7.1: serious events, 15 calendar days"),
    "tl_nonserious": (IPC, r"All Non-Serious Adverse Events must be reported by MAH within 90 calendar days", "IPC 2.7.2: non-serious events, 90 calendar days"),
    "ndct_exception": (NDCT, r"all cases involving serious unexpected adverse reactions must be reported to the licencing authority within fifteen days", "NDCT Fifth Schedule: serious UNEXPECTED reactions, 15 days"),
    "ndct_psur": (NDCT, r"Periodic safety update reports due for a period must be submitted within thirty calendar days", "NDCT Fifth Schedule: PSUR within 30 days of period end"),
    "valid_icsr": (IPC, r"valid ICSR would be one, which meets the following minimum criteria", "IPC 2.5: minimum criteria for a valid ICSR"),
}
def anchor_query(a):
    """Search text built from the anchor's own wording (regex punctuation stripped)."""
    return re.sub(r"\s+", " ", re.sub(r"[\\\[\]?*+()|^$]|\bs\b", " ", ANCHORS[a][1].replace("[sz]", "s"))).strip()


FLAG_ANCHOR = {"death": "sev_death", "life_threatening": "sev_life", "hospitalization": "sev_hosp",
               "disability": "sev_disab", "congenital_anomaly": "sev_cong"}

IME_TERMS = {"stevens-johnson syndrome", "toxic epidermal necrolysis", "anaphylaxis", "aplastic anemia",
             "agranulocytosis", "suicidal ideation", "pulmonary embolism", "myocardial infarction", "rhabdomyolysis"}


def squash(s):
    """Whitespace-insensitive comparison key (PDF extraction can split/merge words)."""
    return re.sub(r"\s+", "", s or "").lower()


def quote_in(text, rx, tail=60):
    """Verbatim substring of `text` starting at the regex match, extended to the sentence end."""
    m = re.search(rx, text, re.I)
    if not m:
        return None
    end = m.end()
    stop = re.search(r"[.;]\s", text[end:end + tail + 80])
    end = end + (stop.start() + 1 if stop else min(tail, len(text) - end))
    return text[m.start():end].strip()


def find_anchor(evidence, anchor_id):
    src, rx, _ = ANCHORS[anchor_id]
    for p in evidence.values():
        if p["source"] == src:
            q = quote_in(p["text"], rx)
            if q:
                return p, q
    return None


def basis_item(evidence, anchor_id, claim):
    hit = find_anchor(evidence, anchor_id)
    if not hit:
        return None
    p, q = hit
    return {"claim": claim, "anchor": anchor_id, "passage_id": p["id"], "source": p["source"], "page": p["page"], "quote": q}


# ------------------------------------------------------------------ intake / entities
_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
        "eight": 8, "nine": 9, "ten": 10, "couple": 2}
_UNIT = {"day": 1, "week": 7, "month": 30}


def narrative_latency_days(text):
    m = re.search(r"\b(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten)\s+(day|week|month)s?\s+(?:after|since|following|from)", text.lower())
    if not m:
        return None
    n = int(m.group(1)) if m.group(1).isdigit() else _NUM[m.group(1)]
    return n * _UNIT[m.group(2)]


VAGUE_TIMING_RX = re.compile(
    r"\b(?:(?:some\s+time|a\s+while|awhile|sometime)\s+(?:after|following|later)|"
    r"after\s+(?:some\s+time|a\s+while|a\s+long\s+time))\b",
    re.I,
)


HOSP_RX = re.compile(r"\b(admitted|admission|hospitali[sz]ed|hospitali[sz]ation|taken to (the )?hospital|in hospital|icu|intensive care)\b", re.I)
DEATH_RX = re.compile(r"\b(died|death|passed away|fatal|deceased)\b", re.I)
NO_EVENT_RX = re.compile(r"\b(no (adverse )?(reaction|event|side effect|problem)s?|did not (experience|have)|nothing (happened|unusual)|without any (reaction|problem|side effect))\b", re.I)


NEG = re.compile(r"\b(no|not|without|never|denies|denied|nil)\b[^.;]{0,25}$", re.I)


def mentions(rx, text):
    """True if `rx` occurs in `text` outside a negation ('no admission', 'did not die')."""
    return any(not NEG.search(text[max(0, m.start() - 30):m.start()]) for m in rx.finditer(text))


def load_drugs(corpus_dir="corpus"):
    p = Path(corpus_dir) / "drugs.json"
    return {k: v for k, v in json.loads(p.read_text()).items() if not k.startswith("_")} if p.exists() else {}


def resolve_drug(name, drugs):
    n = re.sub(r"[^a-z0-9 \-]", " ", (name or "").lower())
    for generic, cfg in drugs.items():
        if any(re.search(r"(?<![a-z])" + re.escape(a) + r"(?![a-z])", n) for a in cfg["aliases"]):
            return generic, cfg["label_source"]
    return None, None


def extract_entities(report, drugs):
    """F3. Structured fields + what the narrative adds. Returns entities and escalation reasons."""
    text = report["reaction_description"]
    generic, label_src = resolve_drug(report["suspect_drug"], drugs)
    sd, rd = date.fromisoformat(report["start_date"]), date.fromisoformat(report["reaction_date"])
    latency = (rd - sd).days
    nl = narrative_latency_days(text)
    reasons = []
    if generic is None:
        reasons.append("drug_outside_corpus")
    if VAGUE_TIMING_RX.search(text) or (nl is not None and abs(nl - latency) > max(3, 0.5 * nl)):
        reasons.append("timing_unclear")
    if mentions(HOSP_RX, text) and not report["hospitalization"]:
        reasons.append("narrative_flag_conflict:hospitalization")
    if mentions(DEATH_RX, text) and not report["death"]:
        reasons.append("narrative_flag_conflict:death")
    serious = [f for f in FLAG_ANCHOR if report.get(f)]
    cands = lay_candidates(text)
    ents = {
        "suspect_drug": report["suspect_drug"], "drug_generic": generic, "label_source": label_src,
        "reaction_text": text, "age": report["patient_age"], "sex": report["patient_sex"],
        "start_date": report["start_date"], "reaction_date": report["reaction_date"], "onset_latency_days": latency,
        "narrative_latency_days": nl, "outcome": report["outcome"], "seriousness_flags": serious,
        "candidate_terms": cands, "direct_terms": [t for t in MEDICAL_VOCAB if re.sub(r"[\s\-]+", " ", t.lower()) in re.sub(r"[\s\-]+", " ", text.lower())],
        "no_event_described": bool(NO_EVENT_RX.search(text)) and not cands,
        "received_date": report["received_date"],
    }
    return ents, reasons


# ------------------------------------------------------------------ expectedness
def match_label(terms, evidence, label_source, direct_terms, searched):
    """Is the reaction listed in the package insert? Returns (expectedness, confidence, hits)."""
    hits = []
    for t in dict.fromkeys(terms):
        rx = term_regex(t)
        for p in evidence.values():
            if p["source"] != label_source:
                continue
            m = rx.search(p["text"])
            if m:
                hits.append({"term": t, "passage_id": p["id"], "page": p["page"], "source": p["source"],
                             "quote": quote_in(p["text"], re.escape(m.group(0)), tail=90),
                             "specific": t.lower() not in GENERIC_TERMS})
                break
    specific = [h for h in hits if h["specific"]]
    if specific:
        return "expected", 0.95 if len({h["term"] for h in specific}) >= 2 else 0.9, hits
    if hits:
        return "unmatched", 0.6, hits                       # only generic words matched: not confident
    dts = [t for t in direct_terms if t.lower() not in GENERIC_TERMS]
    if dts and all(t.lower() in {s.lower() for s in searched} for t in dts):
        return "unexpected", 0.8, hits                      # reporter used a specific term, label was searched, absent
    return "unmatched", 0.0, hits


# ------------------------------------------------------------------ decision table
def decide(serious: bool, expectedness: str):
    """The brief: 'the timeline depends on the combination, not on either assessment alone'."""
    if not serious:
        return {"route": "periodic_safety_report", "days": 90, "anchors": ["tl_nonserious", "ndct_psur"], "conflict": None,
                "basis": "Non-serious: IPC 2.7.2 requires reporting within 90 calendar days; also captured in the PSUR cycle (NDCT Fifth Schedule)."}
    if expectedness == "unexpected":
        return {"route": "expedited_report", "days": 15, "anchors": ["tl_serious", "ndct_exception"], "conflict": None,
                "basis": "Serious and unexpected: 15 days under both IPC 2.7.1 and the NDCT Fifth Schedule."}
    return {"route": "expedited_report", "days": 15, "anchors": ["tl_serious", "ndct_exception", "ndct_psur"],
            "conflict": ("Provisions differ for serious but EXPECTED reactions. IPC 2.7.1 requires 15-day reporting for ALL serious events; "
                         "the NDCT Fifth Schedule singles out serious UNEXPECTED reactions for 15 days and otherwise uses the periodic (PSUR) cycle. "
                         "Routed to the stricter, expedited stream because missing an expedited timeline is a regulatory finding; "
                         "the qualified person must confirm."),
            "basis": "Serious and expected (listed): IPC 2.7.1 requires 15 calendar days for all serious events; the NDCT PSUR cycle alone would not."}


def deadline(received_iso, days):
    d = date.fromisoformat(received_iso) + timedelta(days=days)
    return {"days": days, "clock_starts": received_iso, "due_date": d.isoformat()}
