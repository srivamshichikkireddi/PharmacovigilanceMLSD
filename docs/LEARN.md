# Learn it: everything you need to understand, run and defend this project

Read in order. Sections 1-2 are the domain (you will be asked about them), 3 explains each idea and where it lives in the code, 5 walks a real trace, 6 is a viva question bank.

---
## 1. The domain in five minutes

* **Pharmacovigilance (PV)**: watching drugs for harm after they are on the market.
* **MAH** (marketing authorisation holder): the company that sells the drug. It must assess every **adverse event** report it receives, from doctors, pharmacists, patients, literature.
* **ICSR** (individual case safety report): one report about one patient. A *valid* ICSR needs four minimum elements (identifiable patient, an adverse event, a suspect product, an identifiable reporter), IPC guidance section 2.5. Without them it is not reportable: that is our `non_reportable` route.
* **Seriousness**: did the event cause **death, a life-threatening situation, hospitalisation (or prolongation), persistent disability, a congenital anomaly**, or is it another **medically important** event (IPC 2.5.3.6, pages 31-32). Seriousness is about *outcome*, not severity ("severe headache" can be non-serious; "mild" hospitalisation is still serious).
* **Expectedness**: is the reaction **already listed in the approved product information** (the package insert / label)? Listed = *expected*; not listed = *unexpected*. It is about the document, not about whether doctors find it surprising.
* **Reporting stream**: the combination decides how fast and how it is reported. **Expedited** = individually and quickly (here 15 calendar days). **Periodic** = collected into periodic reports (PSURs) or reported on a longer clock (90 days for non-serious in the IPC guidance). Missing an expedited timeline is a *regulatory finding*: that asymmetry is why we escalate when unsure.
* **QPPV** (qualified person for PV): the human who decides. Our system *never submits* anything; it prepares an assessment with evidence. This is a design principle, not a disclaimer.
* The three documents: **NDCT Rules 2019** (the law, Fifth Schedule covers PSURs and the exception for serious unexpected reactions), **IPC guidance for MAHs** (the operating guidance: seriousness list, timelines, FAQ), and the **package insert** (what is "listed").

## 2. The hard case, and the thing nobody tells you about it

> "The patient's skin started peeling badly a week after starting the drug, and she was admitted to hospital."

1. **Vocabulary gap.** "Skin peeling" is not in the label; "exfoliative dermatitis", "toxic epidermal necrolysis" and "Stevens-Johnson syndrome" are (Tegretol label: boxed warning page 1, warnings page 4, adverse reactions page 13). A search with the reporter's words misses them. Our fixes: glossary, LLM translation, and a label search by *medical* terms.
2. **Seriousness.** "Admitted to hospital" -> the hospitalisation criterion (IPC 2.5.3.6(iii), page 31). Serious.
3. **Expectedness.** SJS/TEN are on the label -> **expected**.
4. **The provision the request never mentions.** What stream does *serious + expected* go to? Here the two documents say different things, and this is the part to understand deeply:
   * **IPC guidance 2.7.1 (page 33):** *all* serious adverse events are reported by the MAH within **15 calendar days**. Expectedness does not matter.
   * **NDCT Fifth Schedule (page 229):** PSURs are periodic (within 30 days of the period end), *"however"* all cases involving serious **unexpected** reactions are reported within **fifteen days**. Read alone, serious *expected* reactions fall in the periodic cycle.
   * **What the system does:** routes to **expedited (15 days)**, shows both quotes, flags a **conflict** and sets `requires_qppv_confirmation`. Reason: the IPC text is explicit ("all serious"), it is the operational guidance MAHs follow, and the cost of being wrong is asymmetric (a late expedited report is a finding; an early one is merely extra work).
   * **What you must do:** confirm this reading with your instructor at Evaluation 1 (the brief says the reference answer is fixed there). If they decide the intended answer is *periodic* for serious-expected, change **one line** in `rules.py::decide()`; the tests and eval gold will tell you everything that moves. Do not just trust my reading: I am a language model reading two PDFs, and I could not verify it with a regulatory professional. Also note: the IPC FAQ appears to contain internal inconsistencies (a question headed "serious unexpected" whose answer says all serious events; a 30-day/90-day mismatch). Citing that is a strength in a viva.

## 3. The ideas, and where they live

### 3.1 RAG and why one search is not enough
RAG = retrieve passages, then answer *from them* instead of from memory. A single embedding/keyword search returns passages *similar to the question*. The question here never says "unexpected", "PSUR" or "fifteen days", so the passages that decide the route are not similar to it. Measured: single pass retrieves ~9 % of the gold passages (`docs/EVALUATION.md`).

### 3.2 Hybrid retrieval (`pv/corpus/retriever.py`)
TF-IDF scores exact words (great for "15 calendar days", clause ids). Embeddings score *meaning* (helps "peeling" ~ "exfoliative"). Reciprocal-rank fusion merges the two rank lists. Embeddings are optional (`EMBEDDINGS_ENABLED`); the system degrades to TF-IDF and still works. `lookup_clause("2.7.1")` is exact-match retrieval by identifier.

### 3.3 Chunking (`pv/corpus/build.py`)
Page-aware chunks (1200 chars, 800 stride, 400 overlap). Overlap must exceed the longest sentence you cite, or a provision can be cut in half and become unfindable. Each chunk keeps `source`, `page` and nearest clause number so a citation can name a page. Extraction matters: `pypdf` mangled the Gazette PDF; `pdftotext` is clean (incident #1).

### 3.4 Agent vs pipeline (`pv/agents/`)
A **pipeline** is a fixed sequence (embed -> retrieve -> answer). An **agent** runs a loop where something *decides* the next action from what it has seen. Ours: the Evidence agent computes what is still missing (`policy.unmet_needs`), picks a tool (the LLM proposes; deterministic rules are the fallback), observes, repeats, within budgets. The two properties the brief assesses: step 2 (*does another provision modify this?* -> `find_modifiers`) and step 4 (*verify*). **Why four agents, not four prompts**: each has its own responsibility, inputs/outputs and failure handling, and the Verifier is deliberately independent code so the thing that produces an answer cannot also approve it.

### 3.5 Tool calling
A tool is a function the agent may call, with a name, arguments and a description. Our five: `search_corpus`, `get_clause`, `get_report_field`, `find_modifiers`, `search_label`. Every call is validated, budget-checked, timed out, and logged. The LLM's choice is untrusted: invalid or duplicate choices are overridden by the rule plan (`policy_override` in the trace).

### 3.6 Verification (`pv/agents/verify.py`), the heart of F5/F7/A3
Before returning, re-check: every citation's passage was retrieved *in this run*; the quote is really in it; the page/source match; the route follows from seriousness + expectedness; the deadline matches. Fail -> new strategy; fail again -> escalate. **An answer that cannot be traced to text is never released.** This is "knowing when not to answer".

### 3.7 Escalation (F7)
We escalate when: the drug is outside the corpus; the reaction cannot be matched to the label with confidence (confidence < 0.75, or only a generic word like "dermatitis" matched); timing is contradictory (narrative says "three months", dates say 7 days); narrative contradicts flags ("admitted to hospital" but `hospitalization=false`); a medically-important term appears with no seriousness flag; budgets or verification fail; an agent crashes. Absence of evidence is *not* treated as "unexpected" unless the reporter used a specific medical term and we searched for it.

### 3.8 Guardrails (L4)
Input: injection patterns (normalised for unicode tricks) and abusive language are rejected at the boundary, so injected text never reaches a model holding tools. Output: a filter blocks any claim that a report was submitted, a "final decision", "no QPPV needed", or certain causality; the route must be one of four allowed values. *Honest limit*: these are rules, not a proof. The real protections are structural: the model never decides the route, the verifier gates everything, and the system has no ability to submit.

### 3.9 Async API and the queue (A4)
A run takes tens of seconds; HTTP requests should not. POST returns 202 + `run_id`; the client polls. Polling (not callbacks) because the caller needs no public endpoint and there is no delivery retry to build. Redis list is the queue. **At-least-once with a lease**: `BLMOVE` atomically moves a job to a processing list; the worker sets a lease key with a TTL; if the worker dies, the lease expires and a reaper requeues the job (max 3 attempts). That is how a crash loses nothing.

### 3.10 Resilience patterns (A5)
*Timeout*: never wait forever. *Retry with exponential backoff + jitter*: transient errors, bounded so you do not hammer a struggling provider. *Circuit breaker*: after N failures stop calling for a cool-down (closed -> open -> half-open probe -> closed): protects the provider and your latency. *Graceful degradation*: when the model is unavailable, fall back to the rule plan and a template summary, flag the run `degraded`. *Never confident when incomplete*: unfinished or unverified -> escalation.

### 3.11 Observability (D5, A6): the three pillars
*Logs*: JSON, one line per event, with a **correlation id** (= run id) on every line across services. *Metrics*: Prometheus counters/histograms (latency percentiles, error rate, retries, tokens, cost, queue depth, circuit state, per-agent success) shown in Grafana. *Traces*: OpenTelemetry spans (API -> queue -> worker -> agent steps) in Jaeger, linked through the W3C `traceparent` carried inside the queued job. Separately, `GET /runs/{id}/trace` reconstructs a whole run: every tool call, query, passage, prompt version, per-step latency and cost, verification outcome.

### 3.12 Cost (L5) and caching/rate limiting (D7)
Each LLM call records prompt/completion tokens and cost *at call time*, attributed to a step (`normalise_terms`, `evidence_action`, `draft_summary`). Expect `evidence_action` (one call per loop iteration) to be the largest driver; confirm with `scripts/cost_report.py`. **Whole-run cache** keyed by `hash(report + corpus version + config)`: identical reports cost zero and return in milliseconds; degraded/unverified results are never cached. **Rate limit per client** is a *cost control*: one client looping against a multi-call agent could exhaust a budget in minutes.

### 3.13 Evaluation (L3)
Never trust a system you have not measured. Fixed scenarios with expected outcomes and gold passages; metrics computed the same way every time. Know each definition (`docs/EVALUATION.md`): groundedness (do quotes exist in the cited text), faithfulness (do the quotes entail the claims), context relevance/recall (retrieval quality), hallucination (confident and unsupported). **The deliberate change** single-pass -> agentic is your before/after. Know the weaknesses too: scenarios authored by the same team; deterministic metrics instead of an LLM judge.

### 3.14 SLO, load testing and Little's law (D6)
An **SLO** is a promise stated *before* measuring: "p95 <= 30 s at >= 1 run/s". **p95** means 95 % of runs are at least this fast; it exposes the tail the mean hides. Measure submission -> result, not the 202. Replace only the LLM with a stub so you measure *your* system. **Little's law**: in-flight work = throughput x latency. With 3 workers x 4 slots = 12 slots and ~8 s per run, the system saturates near 1.5 runs/s; past that, latency grows linearly as work waits for a slot. If `--scale` does not help, find the true bottleneck (CPU, Redis, one lock, the stub itself).

### 3.15 Data contracts and quarantine (D1)
A contract says exactly what is accepted. Validation sits at the boundary so garbage never starts an expensive run. Rejects are *quarantined* with field-level errors (not silently dropped) so you can debug a bad producer.

## 4. Code map
| Path | What it is |
|---|---|
| `pv/schemas.py` | the data contract and validator (single source of truth; generates the JSON schema) |
| `pv/guardrails.py` | injection, toxicity, output filter |
| `pv/corpus/build.py` | D2 pipeline: parse -> chunk -> (embed) -> manifest; idempotent |
| `pv/corpus/retriever.py` | index, hybrid search, clause lookup, derived-term extraction, LangChain adapter |
| `pv/corpus/glossary.py` | lay -> medical terms; tolerant term matching |
| `pv/rules.py` | anchors (the provisions), label matching, **decision table**, entities, deadline |
| `pv/llm/` | provider-agnostic client, circuit breaker + retries + cost, versioned prompts |
| `pv/agents/tools.py` | the tools, timeouts, budgets, tracing |
| `pv/agents/policy.py` | what is missing; LLM or rule choice of next action |
| `pv/agents/{intake,evidence,assess,verify}.py` | the four agents |
| `pv/agents/graph.py` | edges, retry loop, finalisation, LangGraph build + sequential twin |
| `pv/store.py` | queue with lease, runs, quarantine, cache, rate limit, feedback, stats |
| `pv/api.py` / `pv/worker.py` | HTTP layer / agent worker |
| `pv/eval/` | metrics + batch runner |
| `loadtest/` | LLM stub, Locust, SLO analysis |
| `observability/` | Prometheus config, generated Grafana dashboard |

## 5. A real run, step by step (the section 4.1 case, with a model configured)
```
  1ms DECIDE entities       drug=carbamazepine, onset 7 days, flags=[hospitalization]      <- Intake
  2ms LLM    normalise_terms (normalise_v2)  -> exfoliative dermatitis / TEN / SJS         <- Evidence agent starts
  9ms TOOL   search_label  [3 medical terms]            -> 3 label passages (pages 1/4/13)   step 1: what is listed?
 11ms TOOL   search_corpus "Hospitalisation / prolongation..." -> IPC criteria             seriousness criterion
 14ms TOOL   get_clause    2.7.1                         -> IPC p.33 timeline              step 1: the provision that applies
 35ms TOOL   find_modifiers IPC p.33 passage             -> derived query                  step 2: does anything modify it?
 37ms TOOL   search_corpus  <derived query>, source=NDCT -> NDCT p.229 ("however ... serious unexpected ... fifteen days")   step 3
 37ms DECIDE evidence_sufficient (14 passages)
 38ms DECIDE determination: expedited, serious, expected, no missing evidence             <- Assessor
 38ms VERIFY passed, 7 citations re-checked                                              step 4  <- Verifier
 38ms LLM    draft_summary (filtered)                                                    <- finalise
```
Result: route `expedited_report`, due 15 days after receipt, 7 citations (IPC p.31, p.33; NDCT p.229; Tegretol pp.1/4/13), conflict flagged, `requires_qppv_confirmation: true`. Run it yourself: `python scripts/demo.py submit case41` then `python scripts/demo.py trace <run_id>`.

## 6. Viva question bank (with the answers you should be able to give)
1. **Why is this an agent and not a pipeline?** The next action depends on what was just read: after reading the IPC timeline it decides whether to look for a modifying provision and builds the next query from that text; after verification fails it changes strategy or escalates. A fixed pipeline cannot branch on content.
2. **Why four agents?** Distinct responsibilities, distinct failure handling; the Verifier is independent so generation cannot approve itself. Two prompts in one process would share failure and trust.
3. **What happens if the Evidence agent crashes?** `_timed` catches it, records `agent_error`, routes to finalize, which emits `human_escalation` with `agent_failed:evidence`. It can only reduce confidence.
4. **How do you stop the agent looping forever?** Three budgets (steps, tool calls, wall-clock) checked before each action; per-need attempt caps; graph recursion limit; strategy retries capped. Exhaustion -> escalate.
5. **How do you prevent hallucinated citations?** The verifier requires every citation's passage id to be in this run's retrieved set and the quote to be a substring of that text (whitespace-insensitive); the eval re-checks against the index independently. Demo: fault injection.
6. **Why is the model not deciding the route?** Regulatory determinations must be reproducible and auditable; a table applied to verified quotes is both. The model translates vocabulary and picks tools. If it is down we still answer.
7. **What is a degraded run?** The model failed us (timeout/outage/breaker open) and we used the rule plan. Flagged, never cached, QPPV confirmation required.
8. **Explain the circuit breaker.** Closed counts failures in a window; at the threshold it opens and calls fail fast; after the cool-down it half-opens and allows one probe; success closes it, failure re-opens. It protects the provider and our latency.
9. **Why polling, not callbacks?** No public endpoint needed by the caller, no delivery retries to build, works from a browser. Trade-off: polling load; we poll at 1 Hz and it is cheap.
10. **Why a queue between API and worker?** Decouples rates and failure domains, enables scaling workers independently, and gives at-least-once recovery.
11. **How is a job not lost if a worker dies?** `BLMOVE` to a processing list + lease TTL; expired lease -> reaper requeues; 3 attempts then `failed`.
12. **Is the cache safe in a regulated setting?** Key includes the report, corpus version and config; degraded/unverified results are excluded; `?nocache=true` bypasses. The risk is staleness after a corpus change, handled by the corpus version in the key.
13. **What does rate limiting protect?** Budget and availability: an agent makes many model calls per request. Per client id (or forwarded IP via the proxy), fixed window.
14. **What did single-pass lose and why?** Recall of gold passages 0.09 vs 0.94. The decisive provisions are not lexically/semantically close to the question.
15. **How is groundedness different from faithfulness?** Groundedness: does the quote exist in the cited text. Faithfulness: does the quote *entail* the claim (right criterion, right number of days). A real quote can still support the wrong claim.
16. **What is p95 and why not the mean?** 95 % of runs complete within it; the mean hides the slow tail users actually feel.
17. **Where did you expect the bottleneck and why?** Worker slots (concurrency x LLM latency), by Little's law; retrieval is ~tens of ms. If measurement disagrees, say so and identify the real one.
18. **Why was the SLO declared first?** Otherwise the target can be fitted to the result. The git history proves ordering.
19. **Biggest weakness?** Labels authored by us; glossary coverage; the NDCT/IPC reading needs QPPV confirmation; TF-IDF precision is low (relevance ~0.37); US label rather than an Indian insert; single drug.
20. **What would you do next?** Reranking to improve precision; second drug; LLM-judge faithfulness alongside the deterministic metrics; human-reviewed gold labels; Indian PI.
21. **How do you handle prompt injection?** Reject at the boundary and again in the worker; the model never has authority over the route; output filter; verifier. Honest limit: rules are bypassable, structure is the defence.
22. **Why `pdftotext`?** Measured: pypdf split words and emitted 4x noise on the Gazette; exact-quote verification and TF-IDF need clean text (incident #1).
23. **How is the prompt versioned?** File stem + content hash recorded in every result; two variants compared on the same eval set.
24. **Why do you cite a conflict instead of choosing silently?** Because two authoritative sources disagree; surfacing it is more honest and more useful to the QPPV than hiding it.
25. **What is NOT verified?** See section 7.

## 7. What was and was not verified (be honest about this)
**Executed and passing in the build environment:** corpus build (idempotent), retrieval, validation, guardrails, the full agent logic on all 26 scenarios, the verifier incl. fault injection, resilience (breaker, backoff, budgets, crash), queue crash-recovery, caching, rate limiting, feedback, cost aggregation, and API handler logic.

**Verified with Docker Desktop during C1-C4:** clean-clone Compose startup and restart; real API and worker behavior; all 57 unit tests; Prometheus scraping of three workers, the API, and cAdvisor; Jaeger traces spanning `pv-api` and `pv-worker`; and all 24 Grafana panels rendered. The 20-report C4 batch completed successfully and was a smoke check, not a performance benchmark. C4 evaluation: route accuracy 1.00, context recall 0.9367, hallucination and unsafe-answer rates 0.00. Evidence is under `evidence/c1/` through `evidence/c4/`.

**Verified with a real LLM provider during C5:** Gemini via its OpenAI-compatible endpoint, using a user-authorized free-tier key and $0 paid-spend cap. Prompt comparison results and their `llm_unavailable` caveat are in `docs/EVALUATION.md`; the live default timeout remains 45 seconds. Other providers are untested. Chroma/sentence-transformer embeddings and incremental synchronization were exercised during C6, including a direct hybrid retrieval query. A later dependency-alignment image rebuild stalled during Docker unpacking; verify the rebuilt image and tracing before relying on it. Locust load curves and the declared SLO target (C8) remain unmeasured. Docker Desktop cAdvisor exposes cgroup IDs rather than individual container names here, so its CPU/memory panels are cgroup-level. Do not treat the C4 smoke-batch timings as load-test results.

## 8. Exercises (do these before the demo)
1. Break it on purpose: stop Redis; stop the worker mid-run; point `LLM_BASE_URL` at nothing. Watch what the system does.
2. Change `decide()` so serious-expected goes periodic; run `pytest`; see exactly which eval cases move.
3. Add a lay phrase to the glossary and a scenario for it.
4. Read one full `/trace` and explain every line to a teammate.
5. Predict the Locust result for 1 worker, then compare.
