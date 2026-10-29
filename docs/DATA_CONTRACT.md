# Data contracts (D1)

Machine-readable: `contracts/report.v1.schema.json` (generated from `pv/schemas.py::FIELDS`, so enforcement and documentation cannot drift) and `GET /contract`. Contract version: **1.0**.

## POST /reports  (request)

| Field | Type | Required | Rule |
|---|---|---|---|
| patient_age | integer | yes | 0-120 |
| patient_sex | enum | yes | female, male, other, unknown |
| suspect_drug | string | yes | 2-150 chars; free text (guarded) |
| reaction_description | string | yes | 8-4000 chars; free text (guarded) |
| start_date | date | yes | ISO `YYYY-MM-DD`, not in the future |
| reaction_date | date | yes | ISO, not in the future, **>= start_date** |
| received_date | date | no | defaults to today; **>= reaction_date**; starts the reporting clock |
| hospitalization, death, life_threatening, disability, congenital_anomaly | boolean | no | default false; must be real booleans |
| outcome | enum | no | recovered, not_recovered, recovering, fatal, recovered_with_sequelae, unknown |
| reporter_type | enum | yes | doctor, pharmacist, patient, other |

Unknown fields are rejected (`additionalProperties: false`).

**Cross-field rules:** reaction_date >= start_date; received_date >= reaction_date; no date in the future; `death=true` => outcome in {fatal, unknown}; `outcome=fatal` => `death=true`.

**Free-text guardrails (before any model sees the text):** instruction-injection patterns (NFKC-normalised, zero-width characters stripped) and abusive language are rejected. Medical words such as "suicide" or "overdose" are deliberately *not* blocked: they are legitimate adverse-event content.

## Responses
| Status | Meaning | Body |
|---|---|---|
| 202 | accepted, queued | `run_id, status_url, trace_url, rate_limit_remaining` |
| 200 | idempotent replay (same `Idempotency-Key`) | `run_id` |
| 422 | rejected at the boundary | `quarantine_id, errors[{loc,code,message}], agent_run_started:false` |
| 429 | per-client rate limit | `Retry-After: 60` |

Error `code`s: `missing, type, range, category, format, cross_field, extra_forbidden, injection, toxicity, json`. **All** field errors are returned together and stored in quarantine (`GET /quarantine/{id}`), with evidence that no agent run started (queue depth unchanged; `agent_run_started=false`).

## GET /runs/{id}  (result contract, abridged)
`status` (queued | running | done | failed) and, when done, `result`:
`route` (expedited_report | periodic_safety_report | non_reportable | human_escalation), `serious`, `seriousness_reasons`, `expectedness` (expected | unexpected | not_applicable | unmatched), `expectedness_confidence`, `deadline{days,clock_starts,due_date}`, `basis[]` (each: `claim, source, page, passage_id, quote`), `conflicts[]`, `escalation{required,reasons[],path}`, `requires_qppv_confirmation`, `verification{status,failures,checks}`, `degraded[]`, `mode`, `prompt_versions`, `cost{total_usd,per_step}`, `steps`, `tool_calls`, `end_to_end_seconds`, `disclaimer`.

A `failed` run carries **no** `result`: a run that did not complete is never returned as an answer.

## Other interfaces
`GET /runs/{id}/trace` (full run reconstruction) · `POST /runs/{id}/feedback {satisfactory:bool, reason:str}` · `GET /feedback/summary` · `GET /ops/summary` · `GET /ops/quality` · `GET /health` (liveness) · `GET /ready` (Redis + index + >=1 worker; also reports index freshness) · `GET /metrics` (Prometheus).

## Internal contract: queue job (Redis)
`{run_id, report (already validated), client, submitted_at, trace_ctx (W3C traceparent), bypass_cache, attempts}`. Workers trust nothing else: the Intake agent re-runs the injection check (defence in depth).
