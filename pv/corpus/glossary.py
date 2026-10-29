"""Lay-language -> medical-term seeds. This is curated domain knowledge, used (a) to give the
retriever a medical query when no LLM is available (graceful degradation) and (b) as a
cross-check on the LLM's suggestions. The LLM extends it; it does not replace it.

Section 3 of the brief: reporters say "skin peeling", package inserts say "exfoliative
dermatitis / toxic epidermal necrolysis". Retrieval on the reporter's words alone misses.
"""
import re

LAY_TO_MEDICAL = {
    "skin peel": ["exfoliative dermatitis", "toxic epidermal necrolysis", "Stevens-Johnson syndrome"],
    "peeling": ["exfoliative dermatitis", "toxic epidermal necrolysis", "Stevens-Johnson syndrome"],
    "skin came off": ["toxic epidermal necrolysis", "Stevens-Johnson syndrome"],
    "blister": ["bullous", "Stevens-Johnson syndrome", "toxic epidermal necrolysis"],
    "mouth sores": ["Stevens-Johnson syndrome", "stomatitis"],
    "rash": ["rash", "dermatitis"],
    "itch": ["pruritic", "pruritus", "rash"],
    "hives": ["urticaria"],
    "swelling of face": ["angioedema"],
    "swollen face": ["angioedema"],
    "headache": ["headache"],
    "dizz": ["dizziness"],
    "drows": ["somnolence", "drowsiness"],
    "sleepy": ["somnolence", "drowsiness"],
    "sick to my stomach": ["nausea"],
    "nausea": ["nausea"],
    "vomit": ["vomiting"],
    "double vision": ["diplopia"],
    "blurred vision": ["blurred vision"],
    "unsteady": ["ataxia"],
    "yellow skin": ["hepatitis", "jaundice"],
    "yellow eyes": ["hepatitis", "jaundice"],
    "liver": ["hepatitis", "hepatic"],
    "low blood count": ["aplastic anemia", "agranulocytosis", "leukopenia"],
    "blood counts": ["aplastic anemia", "agranulocytosis", "leukopenia"],
    "bruis": ["thrombocytopenia", "purpura"],
    "low sodium": ["hyponatremia"],
    "irregular heartbeat": ["arrhythmia", "AV block"],
    "slow heart": ["bradycardia", "AV block"],
    "suicid": ["suicidal ideation"],
    "seizure": ["seizure"],
    "fits": ["seizure"],
    "birth defect": ["spina bifida", "congenital malformation"],
    "cant pass urine": ["urinary retention"],
    "can't pass urine": ["urinary retention"],
    "hair loss": ["alopecia"],
    "diarrh": ["diarrhea"],
    "constipat": ["constipation"],
}

# Specific medical terms the system recognises even when the reporter already used them.
# Not every one is in the label: that is what makes an "unexpected" finding possible.
MEDICAL_VOCAB = sorted({t for v in LAY_TO_MEDICAL.values() for t in v} | {
    "tendon rupture", "pulmonary embolism", "pancreatitis", "glaucoma", "myocardial infarction",
    "anaphylaxis", "rhabdomyolysis", "osteonecrosis", "retinal detachment", "tinnitus",
})

GENERIC_TERMS = {"rash", "dermatitis", "skin", "dermatologic reactions", "dermatologic"}


def _flat(s):
    return re.sub(r"[\s\-]+", " ", s.lower())


def lay_candidates(text: str):
    t = re.sub(r"[^a-z' ]", " ", text.lower())
    flat = _flat(text)
    out = []
    for key, terms in LAY_TO_MEDICAL.items():
        if key in t:
            out.extend(terms)
    for term in MEDICAL_VOCAB:
        if _flat(term) in flat:
            out.append(term)
    return list(dict.fromkeys(out))


def term_regex(term: str):
    """Tolerant matcher: 'Stevens-Johnson' also matches 'Stevens- Johnson' / 'StevensJohnson'."""
    words = [w for w in re.split(r"[\s\-]+", term.strip()) if w]
    body = r"[\s\-]*".join(re.escape(w) for w in words)
    if len(words) == 1 and not body.endswith("s"):
        body += r"(?:s|es)?"
    return re.compile(r"(?<![A-Za-z])" + body + r"(?![A-Za-z])", re.IGNORECASE)
