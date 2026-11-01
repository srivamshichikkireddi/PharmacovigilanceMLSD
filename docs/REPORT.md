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

Interpretation: single pass cannot ground any answer (recall 0.09) but stays safe (0 hallucinations, everything escalated); the agent recovers 0.94 of gold passages and routes all 22 correctly for the right reasons. Context relevance (0.37) is the weak spot: the agent over-retrieves by design. **Prompt variants (C5, configured provider):** `normalise_v1` scored 0.9091 route accuracy versus 0.8636 for `normalise_v2` (delta -0.0455); both had zero hallucination and unsafe-answer rates. The comparison is inconclusive because 16/22 and 15/22 valid cases respectively degraded on `llm_unavailable`; see [`docs/EVALUATION.md`](./EVALUATION.md). Self-verification catching an injected fabricated citation: `tests/test_agent.py::Verification`.
Limitations: labels authored by the team; same authors wrote system and scenarios; deterministic metrics.

## 6. Performance, SLO and cost
* **SLO (declared before measuring, `docs/SLO.md`):** p95 submission->result <= 30 s at >= 1.0 runs/s with 3 workers, 0 failures; LLM stub 1.2 s/call. Justified against section 1 (clocks are in days; the target protects the human's triage session and burst isolation).
* **Engine-only time (no LLM, measured offline):** median 27 ms, p95 92 ms per run (rules-only, single thread, warm index; index load 0.23 s).
* **Measured (Docker, Locust, stub):** seven-minute 2/4/8/12/16/24/32-user ramp; fixed local stub delay 1.2 s/call +/-15%. With cache off, one worker measured 0.1811 completed runs/s, p50 81.0 s, p95 180.0 s; six clients reached the 180 s polling deadline while still running. The API later recorded all 109 runs complete (p95 180.875 s). Three workers measured 0.4497 runs/s, p50 29.0 s, p95 67.0 s, with zero Locust E2E failures; Locust recorded 218 E2E rows while the API counter later showed 219 completed runs. The declared SLO was **not met**: the three-worker p95 exceeded 30 s and throughput was below 1.0 run/s. First rolling p95 breach: 4 users with one worker (33 s), 12 users with three workers (32 s). Scaling from one to three workers increased throughput about 2.48x and reduced p95 from 180 s to 67 s, but did not meet the target. Near peak, all 12 worker slots were occupied and the queue reached 12; sampled worker CPU remained 0.02–3.64%, supporting worker concurrency/LLM wait as the bottleneck rather than CPU saturation at the sampled instant. Full result summary and CSV prefixes: [`phase8_measurements.json`](../loadtest/results/phase8_measurements.json); raw CSVs are locally generated and ignored by Git.
* **Original cache-off measurement (pre-fix; numbers above retained unchanged).** The client used one lock per worker process for every model request, including when `LLM_MIN_INTERVAL_S=0`; this serialized the four worker threads' LLM calls. The old isolated Redis volumes were removed, so the old run's queue-wait/run-time/model-call/tool-call split is unverified; its saved Locust CSVs contain end-to-end timings only.
* **Corrected cache-off rerun (same seven-minute Locust ramp, same 1.2 s +/-15% local stub, cache off):**

  | Workers | Measurement | E2E runs / failures | Runs/s | p50 | p95 | First rolling p95 breach | Declared SLO |
  |---:|---|---:|---:|---:|---:|---|---|
  | 1 | Original | 108 / 6 | 0.1811 | 81 s | 180 s | 4 users / 33 s | Not met |
  | 1 | After fix | 294 / 0 | 0.5406 | 19 s | 44 s | 24 users / 31 s | Not met |
  | 3 | Original | 218 / 0 | 0.4497 | 29 s | 67 s | 12 users / 32 s | Not met |
  | 3 | After fix | 599 / 0 | 1.3807 | 8.6 s | 17 s | None through 32 users | **Met** |

  From 295 completed uncached one-worker runs, mean queue wait was 23.252 s and mean `run_seconds` 5.618 s (28.870 s combined). The 1,330 model calls averaged 1.228 s each (4.508 calls/run); the 1,003 tool calls averaged 17.7 ms (3.400 calls/run). From 600 completed uncached three-worker run records, mean queue wait was 2.983 s and `run_seconds` 5.925 s (8.908 s combined). Its 2,869 model calls averaged 1.221 s each (4.782 calls/run); 2,189 tool calls averaged 18.1 ms (3.648 calls/run). Per-step and per-tool breakdowns for the one-worker run and all rerun aggregates are in [`phase8_throttle_rerun.json`](../loadtest/results/phase8_throttle_rerun.json). The one-worker comparison still misses both throughput and p95; the declared three-worker target passes after the fix. The 3-worker API counter (601) differs by one from the trace record count (600) and Locust E2E count (599); this discrepancy is unresolved.

  Cause: `OpenAICompat.chat` held its shared request lock across network I/O even with a zero minimum interval. This was not a 5 s provider delay: the live check's `LLM_MIN_INTERVAL_S=5` was a one-off runtime override; the load-test overlay explicitly sets `LLM_MIN_INTERVAL_S=0` and `LLM_MAX_RETRIES=0`. With zero interval, calls now bypass the lock; a positive interval still serializes and enforces spacing. The call traces now average about the stub delay, confirming the earlier 22–27 s worker occupancy came from lock contention across concurrent runs, not 4.5 sequential 1.2 s calls within one run. The SLO failure in the corrected one-worker comparison is queue wait/capacity at higher concurrency; the three-worker cache-off SLO passes. The rerun summary is [`phase8_throttle_rerun.json`](../loadtest/results/phase8_throttle_rerun.json).
* **Phase 9 high-concurrency ramp (2026-11-01; cache off, local fixed-response stub):** Ran three 60-second stages at 48, 64, and 96 users with three workers, four worker slots each, 1.2 s +/-15% stub delay, `LLM_MIN_INTERVAL_S=0`, and retries disabled. The run command used `LOADTEST_USER_STAGES=48,64,96`, `-t 3m`, and `--stop-timeout 240`; `docs/SLO.md` was not changed. Aggregate Locust E2E results: 456 rows, zero recorded failures, 2.044 runs/s, p50 29 s, p95 47 s, p99 48 s. The SLO throughput and failure conditions passed, but p95 failed the <=30 s target. Rolling p95 at 48 users had median 24 s (range 10–28 s); at 64 users median 30 s (range 25–36 s); at 96 users median 35 s (range 31–48 s). The first rolling p95 breach occurred at 64 users: 32 s at 2.0 runs/s.

  **Cause:** worker queue/capacity saturation. During the ramp the Redis processing list reached all 12 worker slots while the waiting queue peaked at 84 jobs. From 457 completed uncached Redis run records, mean queue wait was 23.756 s versus 5.746 s mean worker execution; traces contained 2,143 model calls averaging 1,214.5 ms and 1,623 tool calls averaging 8.9 ms. Queueing, not tool execution, dominates the added latency; the API submit row itself had p95 23 ms. Per-container CPU during this high-concurrency run was not captured, so CPU saturation is unverified.

  **Measurement caveat:** the final Locust CSV contains 456 E2E rows and 457 accepted POSTs; Redis contains 457 completed runs and an empty queue/processing list. This one-row difference is unresolved. The Compose runner did not exit after the CSVs were written and the queue had drained; its specific CLI processes were stopped, so the runner's terminal exit status is unverified. The persisted Locust CSVs are [`phase9_high_w3_stats.csv`](../loadtest/results/phase9_high_w3_stats.csv) and [`phase9_high_w3_stats_history.csv`](../loadtest/results/phase9_high_w3_stats_history.csv) (local ignored outputs).
* **Caching (D7):** same ramp, warm-cache comparison. One worker: cache off p95 180.0 s and $0.057092/100 runs; cache on p95 0.029 s and $0.000382/100 runs (99.38% run-cache hits). Three workers: cache off p95 67.0 s and $0.060566/100 runs; cache on p95 0.044 s and $0.000368/100 runs (99.43% hits). The cache-on runs are supplemental, not a pass of the declared SLO, whose measurement condition requires cache off. A cold one-worker ramp first exceeded 30 s at 8 users before cache warm-up. Rate limiting: `scripts/demo.py flood`.
* **Cost (L5):** saved historical real-provider v2 evaluation, pre-A3 (25 valid scenarios): $0.04673880 per 100 runs; `evidence_action` was the largest step at $0.01065030 across those 25 runs. This was recomputed offline from [`phase7_prompt_v2.json`](../eval/results/phase7_prompt_v2.json); no provider call was made for Phase 8.

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
| L2 | prompts versioned and compared using the configured provider; results limited by `llm_unavailable` | `prompts/`, `docs/EVALUATION.md` |
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
