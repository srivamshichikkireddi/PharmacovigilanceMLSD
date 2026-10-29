"""L4: guardrails on every free-text field that can reach a model, plus an output filter.

Honest scope: these are rule-based. They stop known/obvious attacks and make the *system*
unable to emit an unauthorised determination; they are not a complete defence, which is why
the verifier and the 'never submits a report' design are the real safety net."""
import re, unicodedata

_ZW = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)


def normalise(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").translate(_ZW).lower()
    return re.sub(r"\s+", " ", t)


INJECTION_PATTERNS = [
    r"\b(ignore|disregard|forget|override|bypass|skip)\b.{0,40}\b(previous|prior|above|earlier|system|developer|all)\b.{0,30}\b(instruction|prompt|rule|guardrail|polic)",
    r"\b(system|developer|assistant)\s*(prompt|message)?\s*:",
    r"\byou\s+are\s+now\b",
    r"\bact\s+as\b.{0,30}\b(admin|developer|system|unrestricted)",
    r"\b(reveal|print|show|repeat)\b.{0,30}\b(system prompt|instructions|api key|secret)",
    r"\b(set|mark|classify|output|return|route|decide)\b.{0,25}\b(route|decision|result|report)\b.{0,15}\b(as|to|=|:)\s*(expedited|periodic|non.?reportable)",
    r"\b(submit|file|send)\b.{0,20}\b(to|with)\b.{0,15}\b(cdsco|regulator|authority)\b",
    r"<\s*/?\s*(system|instructions?|tool_call|function_call)\s*>",
    r"\bdo not (escalate|verify|cite)\b",
    r"\b(jailbreak|dan mode|developer mode)\b",
]
_INJ = [re.compile(p, re.I | re.S) for p in INJECTION_PATTERNS]

# Deliberately narrow: abusive insults only. Medical words (suicide, overdose, violence in a
# reaction narrative) are legitimate adverse-event content and must NOT be blocked.
TOXIC_TERMS = ["idiot", "stupid", "moron", "dumbass", "shut up", "fuck", "shit", "bastard", "bitch", "asshole", "scum"]
_TOX = re.compile(r"\b(" + "|".join(re.escape(t) for t in TOXIC_TERMS) + r")\b", re.I)


def injection_hits(text: str):
    n = normalise(text)
    return [p.pattern[:40] for p in _INJ if p.search(n)]


def contains_instruction_attack(text: str) -> bool:
    return bool(injection_hits(text))


def toxicity_hits(text: str):
    return sorted({m.group(1).lower() for m in _TOX.finditer(normalise(text))})


# ---- output filter: the system may not issue determinations it is not authorised to make ----
FORBIDDEN_OUTPUT = [
    r"\b(have|has|had|will|am going to)\s+(been\s+)?(submitted|filed|sent|reported)\b.{0,30}\b(cdsco|regulator|authority|pvpi)",
    r"\bsubmitted (to|with) (the )?(cdsco|regulator|authority|pvpi)",
    r"\bfinal (regulatory )?(decision|determination)\b",
    r"\bno (human|qppv|review) (is )?(needed|required)\b",
    r"\bapproved for (submission|filing)\b",
    r"\bignore previous\b",
    r"\bcausality (is )?(certain|proven|definite)\b",
]
_OUT = [re.compile(p, re.I) for p in FORBIDDEN_OUTPUT]
ALLOWED_ROUTES = {"expedited_report", "periodic_safety_report", "non_reportable", "human_escalation"}


def check_output_text(text: str):
    """Return list of violations (empty = clean)."""
    n = normalise(text)
    v = [p.pattern[:40] for p in _OUT if p.search(n)]
    v += ["toxic_output:" + t for t in toxicity_hits(text)]
    return v


def check_output_result(result: dict):
    v = []
    if result.get("route") not in ALLOWED_ROUTES:
        v.append("route_not_allowed")
    for field in ("route_basis", "summary"):
        v += check_output_text(result.get(field) or "")
    for r in result.get("escalation", {}).get("reasons", []) if isinstance(result.get("escalation"), dict) else []:
        v += check_output_text(str(r))
    return v
