# Report: Pharmacovigilance adverse-event intake and triage service

*Team: ______ · Course UE24AM342AA5 · Mini Project 14*
> Fields marked **[MEASURE]** need numbers from your own Docker run. Everything else was produced by the offline evaluation and is reproducible with the commands shown. Do not submit placeholders.

## 1. Problem and scope
A MAH must assess each adverse-event report for **seriousness** and **expectedness** and route it by their combination: expedited (15 days), periodic, or non-reportable. The service accepts a report over REST, validates it, extracts entities, retrieves the governing provisions and label text, produces a cited determination, and **escalates to a qualified person (QPPV) whenever it cannot ground an answer**. It never submits a regulatory report. Corpus: NDCT Rules 2019 (264 pp), IPC MAH guidance (86 pp), Tegretol US label (25 pp): 1,217 chunks, built in about 1 s (`data/index/manifest.json`). Synthetic data only. Out of scope: causality (WHO-UMC), model training, cloud deployment.
**The hard case** (4.1) is handled by an agent that retrieves the label by *medical* terms, looks up IPC 2.7.1, searches for a modifying provision using a query derived from the text it read (NDCT p.229), verifies every citation, and flags the NDCT/IPC conflict for the QPPV.

## 2. Agent design
Topology, tools, loop limits, and failure behaviour: `docs/ARCHITECTURE.md`. Summary: four agents (Intake, Evidence, Assessor, Verifier) communicating through a run context; five tools (search_corpus, get_clause, get_report_field, find_modifiers, search_label); LangGraph with a conditional verify -> retry edge; bounds `max_steps=12`, `max_tool_calls=14`, `run_timeout=45 s`, `tool_timeout=5 s`, 2 retrieval strategies; the model proposes tool calls and translates vocabulary but never decides the route; verification is independent and deterministic.

## 3. Architecture and deployment boundaries
Diagram and justification of each boundary (scaling, failure isolation, independent release, resource profile): `docs/ARCHITECTURE.md`. Nine containers from one `docker compose up`: redis, indexer (one-shot), api, agent-worker (scalable), web, prometheus, cadvisor, jaeger, grafana; plus llm-stub and locust in the load-test overlay.

## 4. Design decisions and what was rejected
| Decision | Chosen | Rejected, and why |
|---|---|---|
| Who decides the route | deterministic table over verified quotes | LLM-decided route: not reproducible or auditable; a hallucination becomes a regulatory error |
| Retrieval | TF-IDF + optional vectors (RRF) | embeddings only: weak on exact provision wording and clause ids; the lay/medical gap needs more than similarity |
| Text extraction | pdftotext, pypdf fallback | pypdf only: split words, 4x noise (incident #1) |
| API style | 202 + polling | synchronous (tens of seconds, proxy timeouts); callbacks (caller needs a public endpoint, delivery retries) |
| Queue | Redis list, `BLMOVE` + lease | Celery/RQ: heavier; Kafka: overkill; plain `LPOP`: loses jobs on crash |
| Validation | pure-Python, one source for validator + JSON Schema | Pydantic: idiomatic but a second source of truth for the published contract (acceptable alternative) |
| Failure of model | rule-plan fallback, flag `degraded` | fail the run: loses the cases the rule plan can still answer safely; silent fallback: dishonest |
| Serious + expected | expedited + conflict flag | periodic: reads NDCT in isolation; risks missing an expedited timeline |
| Evaluation | deterministic metrics vs gold | LLM judge only: costs money, non-deterministic; kept as an extension |
| Load test | stub replaces only the LLM | stub replacing the whole engine (does not test our system) |

### 4.3 Section 4.1 reference answer
Vamshi confirmed on 29/10/2026 (time not provided) that a serious, expected reaction routes to `expedited_report`. The result surfaces the IPC/NDCT conflict and requires QPPV confirmation.

## 5. Evaluation (before / after the deliberate change)
26 scenarios (22 full + 4 boundary), gold passages located from the corpus text. Metric definitions: `docs/EVALUATION.md`. **Change: single retrieval pass (before) -> agentic retrieval (after)**; no LLM in either.

| Metric | Before: single pass | After: agentic |
|---|---|---|
| Route accuracy (22 valid cases) | 0.32 | 1.00 |
| Answered (not escalated) | 0.00 | 0.68 |
| Escalation-reason accuracy | 1.00 | 1.00 |
| Seriousness accuracy | 1.00 | 1.00 |
| Expectedness accuracy | 0.40 | 1.00 |
| Groundedness (answered cases) | n/a (nothing answered) | 1.00 |
| Faithfulness (answered cases) | n/a (nothing answered) | 1.00 |
| Context relevance | 0.04 | 0.37 |
| Context recall of gold passages | 0.09 | 0.94 |
| Hallucination rate | 0.00 | 0.00 |
| Unsafe answer rate | 0.00 | 0.00 |
| Mean tool calls / run | 0.86 | 4.77 |

Interpretation: single pass cannot ground any answer (recall 0.09) but stays safe (0 hallucinations, everything escalated); the agent recovers 0.94 of gold passages and routes all 22 correctly for the right reasons. Context relevance (0.37) is the weak spot: the agent over-retrieves by design. **Prompt variants (C5, Gemini free tier):** `normalise_v1` scored 0.9091 route accuracy versus 0.8636 for `normalise_v2` (delta -0.0455); both had zero hallucination and unsafe-answer rates. The comparison is inconclusive because 16/22 and 15/22 valid cases respectively degraded on `llm_unavailable`; see [`docs/EVALUATION.md`](./EVALUATION.md). Self-verification catching an injected fabricated citation: `tests/test_agent.py::Verification`.
Limitations: labels authored by the team; same authors wrote system and scenarios; deterministic metrics.

## 6. Performance, SLO and cost
* **SLO (declared before measuring, `docs/SLO.md`):** p95 submission->result <= 30 s at >= 1.0 runs/s with 3 workers, 0 failures; LLM stub 1.2 s/call. Justified against section 1 (clocks are in days; the target protects the human's triage session and burst isolation).
* **Engine-only time (no LLM, measured offline):** median 27 ms, p95 92 ms per run (rules-only, single thread, warm index; index load 0.23 s).
* **Measured (Docker, Locust, stub):** 1 worker: RPS **[MEASURE]**, p50 **[MEASURE]**, p95 **[MEASURE]**; 3 workers: **[MEASURE]**. Target met? **[MEASURE]**. Concurrency at which it fails: **[MEASURE]** users. Component responsible: **[MEASURE]** (prediction: worker slots, Little's law). Effect of `--scale`: **[MEASURE]**.
* **Caching (D7):** `python scripts/cache_effect.py` -> p95 and cost/run, cache off vs on **[MEASURE]**. Rate limiting: `scripts/demo.py flood`.
* **Cost (L5):** `python scripts/cost_report.py` -> cost per 100 runs **[MEASURE]**, largest driver **[MEASURE]** (prediction: `evidence_action`, one model call per loop iteration).

## 7. What did not work / lessons
* pypdf extraction (incident #1). 
* First seriousness queries described the criterion instead of quoting it (incident #2); the evaluation found it, not intuition.
* Verifier initially knew only two routes (incident #3).
* A test that passed for the wrong reason: the "derived query" test accepted any follow-up search of the Rules, hiding that the policy used its fallback query; found only by reading a real trace and fixed by asserting the query *is* the derived one. Lesson: assert behaviour, not outcomes that several behaviours can produce.
* Negation ("no admission") and hyphenation bugs (incident #4).
* Context relevance is low; reranking not implemented.
* Not done: WHO-UMC causality, Indian-market insert, second drug, LLM-judge metrics, OCR path (corpus has a text layer), callback endpoint.
* **Verification status:** see `docs/LEARN.md` section 7: which parts were executed and which still need a first Docker run.

## 8. Requirement coverage
| | Status | Where |
|---|---|---|
| F1-F8 | implemented | `pv/api.py`, `schemas.py`, `rules.py`, agents, `/health /ready /metrics` |
| A1-A5 | implemented | `agents/`, `llm/resilience.py`, `api.py`, `worker.py`, `store.py` |
| A6 | implemented and verified | Grafana dashboard: all 24 panels rendered; four idle panels correctly show no data with the LLM disabled and no feedback |
| A7 | implemented, measure | `docker-compose.loadtest.yml`, `loadtest/` |
| A8 | implemented | `frontend/index.html` |
| L1 | implemented (vectors optional, untested in build env) | `corpus/` |
| L2 | prompts versioned and compared with Gemini; results limited by `llm_unavailable` | `prompts/`, `docs/EVALUATION.md` |
| L3 | set + metrics + before/after done | `eval/`, `pv/eval/` |
| L4 | implemented (rule-based) | `guardrails.py` |
| L5 | implemented, measure | `llm/resilience.py`, `store.stats` |
| L6 | implemented | feedback endpoints, live sampling, quality panel |
| D1-D2 | implemented | `schemas.py`, `corpus/build.py` |
| D3-D4 | implemented | compose, `.env.example` |
| D5 | implemented and verified | Prometheus targets, Grafana dashboard, Jaeger traces, logging, metrics, tracing, `/trace` |
| D6 | declared; measure | `docs/SLO.md` |
| D7 | implemented, measure | cache + rate limit |
| D8 | written | `RUNBOOK.md`, `INCIDENT_LOG.md` (replace with your own incidents) |

## 9. C4 observability verification

The clean-clone stack was scaled to three healthy `agent-worker` replicas. Prometheus reported all three workers plus the API and cAdvisor as `up`; `/ready` reported three workers and an empty queue. A 20-report mixed-case smoke batch was accepted and completed in 6.03 seconds. This is an observability exercise, not a Locust/SLO performance measurement.

Grafana loaded and rendered all 24 panels. The dashboard screenshot and panel definition are in [`evidence/c4/grafana-dashboard.png`](../evidence/c4/grafana-dashboard.png) and [`evidence/c4/grafana-dashboard-after-fix.json`](../evidence/c4/grafana-dashboard-after-fix.json). Four panels correctly have no series in this provider-free run: LLM latency, retries, token/cost, and user feedback. During verification, the error-rate panel was made to display zero when no failures exist, and cAdvisor CPU/memory queries were aligned with the `id` labels exposed by Docker Desktop; the cgroup series are not individual-container measurements.

Jaeger returned worker traces containing both `pv-api` and `pv-worker` spans. The 57-test suite passed, and the 26-scenario evaluation remained at 1.00 route accuracy with 0.00 hallucination and unsafe-answer rates. Supporting outputs are recorded in [`evidence/c4/`](../evidence/c4/).
