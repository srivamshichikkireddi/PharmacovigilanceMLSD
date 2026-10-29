# Demo playbook (all ten items)

> **Rule zero from the brief: if the system does not start and serve a valid request at the demo, the mark is zero.** Everything below is secondary to rehearsing item 1 on the *exact machine and network* you will present on.

## T-24h and T-1h checklist
- [ ] Fresh clone on the demo machine: `git clone <repo> && cd pv-service && cp .env.example .env && docker compose up --build`. Time it. Fix whatever breaks (first-run problems are almost always: Docker not running, a port already in use (8000/8080/3000/9090/16686/8081), no internet for the image build, cAdvisor mounts on Windows).
- [ ] `docker compose build` the night before and **do not clear the build cache**; at the demo, `up` should take seconds.
- [ ] Pre-pull images (`docker compose pull`) so a flaky venue Wi-Fi cannot stop you.
- [ ] Have `python scripts/demo.py --help` working (stdlib only, Python 3.8+).
- [ ] Decide LLM mode: **no key** = rules-only (always works); **key** = LLM-assisted (set `LLM_PROVIDER/BASE_URL/API_KEY/MODEL`, with a spending limit). Rehearse in the mode you will present. Items 8 and 9 use the stub, not your key.
- [ ] Pre-record: Locust CSVs for 1 and 3 workers, Grafana screenshots, one trace JSON. Show live where you can, fall back to recordings where you must.
- [ ] Open tabs: web UI :8080 · Grafana :3000 · Jaeger :16686 · Prometheus :9090 · terminal in repo root.
- [ ] Spare laptop with a second copy.

## Phase A: base stack (`docker compose up --build`)

### 1. Clean clone to a working system
```bash
git clone <repo> && cd pv-service && cp .env.example .env
docker compose up --build            # in another terminal:
docker compose ps                    # healthchecks: redis, api, agent-worker healthy; indexer Exited (0)
curl -s localhost:8000/ready | python -m json.tool     # workers>=1, index chunks, built_utc, corpus version
```
*Say:* one command, dependencies and health checks declared; the indexer is a one-shot that must complete before api/worker start; five boundaries (see ARCHITECTURE.md).

### 2. Valid request -> routed recommendation with citations
```bash
python scripts/demo.py submit headache        # also try the web UI at http://localhost:8080
```
*Show:* `202` returned immediately (async), route `periodic_safety_report`, 90-day clock, citations each with source + page + verbatim quote. *Say:* each quote is re-checked against retrieved text before release.

### 3. One run traced end to end
```bash
python scripts/demo.py trace <run_id>         # from step 2
```
*Show:* every tool call, its query, passages returned, prompt versions (`name@hash`), per-step latency, tokens and cost, verification outcome. Then Jaeger (service `pv-worker`, find the run; spans per agent) and `docker compose logs agent-worker | grep <run_id>` (correlation id).

### 4. Malformed request rejected and quarantined; no agent run
```bash
python scripts/demo.py submit malformed       # HTTP 422, all field errors at once, quarantine_id, agent_run_started=false
curl -s localhost:8000/quarantine/<quarantine_id> | python -m json.tool
curl -s localhost:8000/ready | grep queue_depth         # unchanged
docker compose logs agent-worker | grep -c <anything>   # worker never saw it
```
*Say:* validation happens before enqueue; a malformed request cannot start a run. Prometheus: `pv_intake_rejected_total`.

### 5. A case the corpus cannot answer -> escalation, not fabrication
```bash
python scripts/demo.py submit unknown_drug    # drug_outside_corpus
python scripts/demo.py submit unmatched       # reaction_unmatched (hair turned blue)
```
*Show:* `human_escalation`, reasons, escalation path, **zero citations**, partial findings marked unconfirmed.

### 6. Self-verification catching an unsupported answer
```bash
# in .env set FAULT_INJECTION=fabricated_citation, then:
docker compose up -d --force-recreate agent-worker
python scripts/demo.py submit case41
python scripts/demo.py trace <run_id>         # verification FAILED (quote_not_in_passage) -> retry_with_new_strategy -> verification PASSED
# afterwards: clear FAULT_INJECTION and recreate the worker
```
*Say:* the fault stands in for a model hallucination ("reporting window is 7 days", an invented quote). The verifier rejected it, the controller retried with a new strategy, and the invented claim never reached the answer. If retries are exhausted (`VERIFY_MAX_STRATEGIES=1`) the run escalates instead.

### 7. Prompt injection and the guardrail response
```bash
python scripts/demo.py submit injection       # 422 code=injection, quarantined, no run
python scripts/demo.py submit toxic           # 422 code=toxicity
python scripts/demo.py flood                  # 40 rapid submits from one client -> 429s (cost control)
```
*Say:* layered: boundary rejection; worker re-check; the model has no authority over the route; output filter blocks "I have submitted..." / "final decision"; verifier. Show `tests/test_boundary.py` and `tests/test_agent.py::test_output_filter_blocks_unauthorised_summary`. Be candid that rule-based defences are bypassable and the structure is the real protection.

### 10. The section 4.1 case
```bash
python scripts/demo.py submit case41
```
*Show:* `expedited_report`; serious (hospitalisation, IPC p.31); expected (Tegretol pp.1/4/13: TEN/SJS/exfoliative dermatitis); 15 days (IPC 2.7.1 p.33); NDCT p.229 exception; **conflict flagged**; `requires_qppv_confirmation`. Then trace: show `find_modifiers` and the derived query that retrieved NDCT p.229, which the request never mentions. *Say:* single pass retrieves 9 % of the gold passages, agentic 94 % (EVALUATION.md). Adapt the reaction/drug if the examiner asks.

## Phase B: stub overlay (items 8 and 9)
```bash
docker compose down
docker compose -f docker-compose.yml -f docker-compose.loadtest.yml up --build -d --scale agent-worker=3
```
### 8. Circuit breaker opens; degradation path
```bash
python scripts/demo.py breaker
```
*Show:* stub switched to `fail`; first runs retry with exponential backoff, then the breaker opens (trace `llm_call ... outcome=unavailable, breaker=open`, error `circuit_open`); runs still **complete** via the rule-based fallback, flagged `degraded`, `requires_qppv_confirmation`; Grafana *Circuit breaker state* = 2. Then stub restored; breaker half-opens and closes. *Say:* the incomplete never becomes confident.

### 9. Locust vs target; failing concurrency; effect of `--scale`
```bash
# SLO.md was committed BEFORE this. Run once per worker count:
docker compose -f docker-compose.yml -f docker-compose.loadtest.yml up -d --scale agent-worker=1
docker compose -f docker-compose.yml -f docker-compose.loadtest.yml run --rm locust -f /mnt/locust/locustfile.py --host http://api:8000 --headless --csv /mnt/locust/results/w1 --csv-full-history
docker compose -f docker-compose.yml -f docker-compose.loadtest.yml up -d --scale agent-worker=3   # repeat with results/w3
python loadtest/analyze.py loadtest/results/w1 loadtest/results/w3
```
Each ramp takes 7 minutes: run them beforehand and present the output; run a short live segment. *Say:* declared target, measured RPS/p50/p95, the user count where p95 broke, which component (worker slots by Little's law, or whatever `docker stats`/Grafana CPU shows), and how much `--scale` helped. If scaling did not help, name the real bottleneck.

## What if something breaks live
| Failure | Do |
|---|---|
| Stack will not start | `docker compose logs --tail=80 <service>`; port clash -> change the host port in compose; run `docker compose down -v` then `up` |
| LLM key/quota fails | Set `LLM_PROVIDER=none` and recreate worker: system still answers (rules-only). Mention this *is* graceful degradation |
| Grafana empty | Wait ~30 s; check Prometheus targets at :9090/targets; use recorded screenshots |
| cAdvisor unhappy (Windows) | Use `docker stats`; say why |
| Total Docker failure | Offline fallback: `python -m pv.eval.run` and `python -m unittest discover -s tests -t .` show the agent working with no containers (partial credit for honesty; rehearse so it never comes to this) |
