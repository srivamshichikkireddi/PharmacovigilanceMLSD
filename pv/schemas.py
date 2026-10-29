"""D1/F2: the data contract, enforced at the ingestion boundary BEFORE any agent starts.

Single source of truth: FIELDS. The published JSON Schema (contracts/report.v1.schema.json)
is generated from it (python -m pv.schemas) so docs and enforcement cannot drift."""
import json
from datetime import date, datetime, timezone

from pv.guardrails import injection_hits, toxicity_hits

CONTRACT_VERSION = "1.0"
SEX = ["female", "male", "other", "unknown"]
REPORTER = ["doctor", "pharmacist", "patient", "other"]
OUTCOME = ["recovered", "not_recovered", "recovering", "fatal", "recovered_with_sequelae", "unknown"]

# name: (kind, required, spec, description)
FIELDS = {
    "patient_age": ("int", True, {"min": 0, "max": 120}, "Age in years (0 for neonates)."),
    "patient_sex": ("enum", True, SEX, "Patient sex."),
    "suspect_drug": ("str", True, {"min": 2, "max": 150, "free_text": True}, "Suspect drug, brand or generic name."),
    "reaction_description": ("str", True, {"min": 8, "max": 4000, "free_text": True}, "Reporter's narrative of the reaction, in their own words."),
    "start_date": ("date", True, {}, "Date the suspect drug was started (ISO 8601)."),
    "reaction_date": ("date", True, {}, "Date the reaction began (ISO 8601). Not before start_date."),
    "received_date": ("date", False, {}, "Date the MAH first received the report. Starts the reporting clock. Defaults to today."),
    "hospitalization": ("bool", False, {}, "Caused or prolonged hospitalisation."),
    "death": ("bool", False, {}, "Resulted in death."),
    "life_threatening": ("bool", False, {}, "Patient at substantial risk of dying at the time of the event."),
    "disability": ("bool", False, {}, "Resulted in substantial disability."),
    "congenital_anomaly": ("bool", False, {}, "Congenital anomaly / birth defect."),
    "outcome": ("enum", False, OUTCOME, "Outcome at time of report. Default 'unknown'."),
    "reporter_type": ("enum", True, REPORTER, "Who reported."),
}


def _err(loc, code, msg):
    return {"loc": loc, "code": code, "message": msg}


def _parse_date(v):
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, str) and len(v) == 10:
        return date.fromisoformat(v)
    raise ValueError


def validate_report(payload, today=None):
    """Return (clean_dict | None, errors). Never raises on bad input."""
    today = today or datetime.now(timezone.utc).date()
    if not isinstance(payload, dict):
        return None, [_err("body", "type", "Body must be a JSON object")]
    errors, clean = [], {}
    for k in payload:
        if k not in FIELDS:
            errors.append(_err(k, "extra_forbidden", f"Unknown field '{k}'"))
    for name, (kind, required, spec, _d) in FIELDS.items():
        if name not in payload or payload[name] is None:
            if required:
                errors.append(_err(name, "missing", "Field is required"))
            continue
        v = payload[name]
        if kind == "int":
            if isinstance(v, bool) or not isinstance(v, int):
                errors.append(_err(name, "type", "Must be an integer")); continue
            if not spec["min"] <= v <= spec["max"]:
                errors.append(_err(name, "range", f"Must be between {spec['min']} and {spec['max']}")); continue
        elif kind == "bool":
            if not isinstance(v, bool):
                errors.append(_err(name, "type", "Must be true or false")); continue
        elif kind == "enum":
            if v not in spec:
                errors.append(_err(name, "category", f"Must be one of {spec}")); continue
        elif kind == "str":
            if not isinstance(v, str):
                errors.append(_err(name, "type", "Must be a string")); continue
            v = v.strip()
            if not spec["min"] <= len(v) <= spec["max"]:
                errors.append(_err(name, "range", f"Length must be {spec['min']}-{spec['max']}")); continue
            if spec.get("free_text"):
                if injection_hits(v):
                    errors.append(_err(name, "injection", "Instruction-like content is not accepted in free-text fields")); continue
                if toxicity_hits(v):
                    errors.append(_err(name, "toxicity", "Abusive language is not accepted")); continue
        elif kind == "date":
            try:
                v = _parse_date(v)
            except (ValueError, TypeError):
                errors.append(_err(name, "format", "Must be an ISO date YYYY-MM-DD")); continue
            if v > today:
                errors.append(_err(name, "range", "Date cannot be in the future")); continue
        clean[name] = v
    # ---- cross-field rules ----
    sd, rd, rc = clean.get("start_date"), clean.get("reaction_date"), clean.get("received_date")
    if sd and rd and rd < sd:
        errors.append(_err("reaction_date", "cross_field", "reaction_date cannot be before start_date"))
    if rd and rc and rc < rd:
        errors.append(_err("received_date", "cross_field", "received_date cannot be before reaction_date"))
    if clean.get("death") and clean.get("outcome") not in (None, "fatal", "unknown"):
        errors.append(_err("outcome", "cross_field", "death=true requires outcome 'fatal' or 'unknown'"))
    if clean.get("outcome") == "fatal" and not clean.get("death"):
        errors.append(_err("death", "cross_field", "outcome 'fatal' requires death=true"))
    if errors:
        return None, errors
    clean.setdefault("received_date", today)
    for b in ("hospitalization", "death", "life_threatening", "disability", "congenital_anomaly"):
        clean.setdefault(b, False)
    clean.setdefault("outcome", "unknown")
    return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in clean.items()}, []


def json_schema():
    props, req = {}, []
    for name, (kind, required, spec, desc) in FIELDS.items():
        p = {"description": desc}
        if kind == "int": p.update(type="integer", minimum=spec["min"], maximum=spec["max"])
        elif kind == "bool": p.update(type="boolean", default=False)
        elif kind == "enum": p.update(type="string", enum=spec)
        elif kind == "str": p.update(type="string", minLength=spec["min"], maxLength=spec["max"])
        elif kind == "date": p.update(type="string", format="date")
        props[name] = p
        if required: req.append(name)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema", "title": "AdverseEventReport",
        "x-contract-version": CONTRACT_VERSION, "type": "object", "additionalProperties": False,
        "properties": props, "required": req,
        "x-cross-field-rules": [
            "reaction_date >= start_date", "received_date >= reaction_date",
            "no date may be in the future", "death=true => outcome in {fatal, unknown}", "outcome=fatal => death=true"],
        "x-free-text-guardrails": ["prompt-injection patterns rejected", "abusive language rejected"],
    }


if __name__ == "__main__":
    from pathlib import Path
    Path("contracts").mkdir(exist_ok=True)
    Path("contracts/report.v1.schema.json").write_text(json.dumps(json_schema(), indent=2))
    print("wrote contracts/report.v1.schema.json")
