# Demo playbook (all ten items)

> **Rule zero from the brief: if the system does not start and serve a valid request at the demo, the mark is zero.** Everything below is secondary to rehearsing item 1 on the *exact machine and network* you will present on.

## T-24h and T-1h checklist
- [ ] Fresh clone on the demo machine: clone the course repository URL, enter `pv-service`, run `Copy-Item .env.example .env`, then `docker compose up --build -d`. Time it. Fix whatever breaks (Docker not running, a port already in use, no internet for the image build, or cAdvisor mounts on Windows).
- [ ] `docker compose build` the night before and **do not clear the build cache**; at the demo, `up` should take seconds.
- [ ] Pre-pull images (`docker compose pull`) so a flaky venue Wi-Fi cannot stop you.
- [ ] Have `python scripts/demo.py --help` working (stdlib only, Python 3.8+).
- [ ] Decide LLM mode: **no key** = rules-only (always works); **key** = LLM-assisted (set `LLM_PROVIDER/BASE_URL/API_KEY/MODEL`, with a spending limit). Rehearse in the mode you will present. Items 8 and 9 use the stub, not your key.
- [ ] Pre-record: Locust CSVs for 1 and 3 workers, Grafana screenshots, one trace JSON. Show live where you can, fall back to recordings where you must.
- [ ] Open tabs: web UI :8080 · Grafana :3000 · Jaeger :16686 · Prometheus :9090 · terminal in repo root.
- [ ] Spare laptop with a second copy.

## Phase A: base stack (`docker compose up --build`)

### 1. Clean clone to a working system
From the repository root in PowerShell:
```powershell
Copy-Item .env.example .env
docker compose up --build -d
docker compose ps
Invoke-RestMethod http://localhost:8000/ready | ConvertTo-Json -Depth 6
```
The same commands work after cloning the course repository and changing into its `pv-service` directory. Expected state: redis, api, agent-worker and observability services healthy; indexer exits successfully after building the index. `/ready` reports `ready: true`, at least one worker, queue depth, chunks, corpus version and build time.
*Say:* the indexer is a one-shot that must complete before api/worker start; deployment boundaries are justified in `ARCHITECTURE.md`.

### 2. Valid request -> routed recommendation with citations
```powershell
python scripts/demo.py submit headache        # also try the web UI at http://localhost:8080
```
*Show:* `202` returned immediately (async), route `periodic_safety_report`, 90-day clock, citations each with source + page + verbatim quote. The demo case is synthetic. *Say:* each quote is re-checked against retrieved text before release.

### 3. One run traced end to end
```powershell
python scripts/demo.py trace <run_id>         # from step 2
```
*Show:* every tool call, its query, passages returned, prompt versions (`name@hash`), per-step latency, tokens and cost, verification outcome. Then Jaeger (service `pv-worker`, find the run; spans per agent) and `docker compose logs agent-worker | Select-String <run_id>` in PowerShell (`grep <run_id>` in Bash).

### 4. Malformed request rejected and quarantined; no agent run
```powershell
$before = (Invoke-RestMethod http://localhost:8000/ready).queue_depth
$out = python scripts/demo.py submit malformed
$out
$qid = ($out | Select-String '"quarantine_id"\s*:\s*"([^"]+)"').Matches.Groups[1].Value
Invoke-RestMethod "http://localhost:8000/quarantine/$qid" | ConvertTo-Json -Depth 5
$after = (Invoke-RestMethod http://localhost:8000/ready).queue_depth
"queue depth before=$before after=$after"
```
The quarantine response must show field-level errors and `agent_run_started: false`; record queue depth before and after if proving it did not change. There is no run ID for a quarantined request, so do not grep worker logs for an unrelated run. *Say:* validation happens before enqueue; a malformed request cannot start a run. Prometheus: `pv_intake_rejected_total`.

### 5. A case the corpus cannot answer -> escalation, not fabrication
```powershell
python scripts/demo.py submit unknown_drug    # drug_outside_corpus
python scripts/demo.py submit unmatched       # reaction_unmatched (hair turned blue)
```
*Show:* `human_escalation`, reason and QPPV path. An unknown drug is escalated before retrieval and has no citations; for an unmatched reaction, retain escalation and do not infer expectedness.

### 6. Self-verification catching an unsupported answer
```powershell
$env:LLM_PROVIDER = 'none'
$env:LLM_MIN_INTERVAL_S = '0'
$env:LLM_MAX_RETRIES = '0'
$env:FAULT_INJECTION = 'fabricated_citation'
docker compose up -d --force-recreate agent-worker
$out = python scripts/demo.py submit case41
$out
$run_id = ($out | Select-String 'run_id:\s*([a-f0-9]+)').Matches.Groups[1].Value
python scripts/demo.py trace $run_id
$env:FAULT_INJECTION = 'disabled'
docker compose up -d --force-recreate agent-worker
```
*Show:* verification `FAILED` (`quote_not_in_passage`) -> `retry_with_new_strategy` -> verification `PASSED`; the invented claim is absent from the returned answer. This is a deterministic fault-injection test, not a live model hallucination.

### 7. Prompt injection and the guardrail response
```powershell
python scripts/demo.py submit injection       # 422 code=injection, quarantined, no run
python scripts/demo.py submit toxic           # 422 code=toxicity
python scripts/demo.py flood                  # 40 rapid submits from one client -> 429s (cost control)
```
*Show:* the current flood limit is 30 accepted submits then 10 HTTP 429s per minute for that client. *Say:* layered: boundary rejection; worker re-check; the model has no authority over the route; output filter and verifier. Show `tests/test_boundary.py` and `tests/test_agent.py::test_output_filter_blocks_unauthorised_summary`. Be candid that rule-based defences are bypassable and the structure is the real protection.

## Phase B: isolated local stub (items 8 and 9)

### 8. Circuit breaker opens; degradation path
```powershell
# Set LOADTEST_LLM_BASE_URL in ignored .env to the llm-stub Compose-network URL.
$env:LOADTEST_RUN_CACHE_ENABLED = 'false'
$env:BREAKER_FAIL_THRESHOLD = '3'
$env:BREAKER_COOLDOWN_S = '2'
docker compose -p pv-demo-stub -f docker-compose.yml -f docker-compose.loadtest.yml up --build -d --scale agent-worker=1 api agent-worker llm-stub
$env:PV_DEMO_API_URL = 'http://127.0.0.1:18000'
$env:PV_DEMO_STUB_URL = 'http://127.0.0.1:19000'
$env:PV_DEMO_BREAKER_COOLDOWN_S = '2'
python scripts/demo.py breaker
docker compose -p pv-demo-stub -f docker-compose.yml -f docker-compose.loadtest.yml down -v
```
*Show:* the local stub switches to fail; failed calls produce degraded, verified rule-based results; later calls report `breaker=open`. The script restores the stub, waits for cooldown, and submits a successful half-open probe. This overlay disables retries, so do not claim exponential backoff in this demonstration.

### 9. Locust vs target; failing concurrency; effect of `--scale`
```powershell
# Set LOADTEST_LLM_BASE_URL in ignored .env to the llm-stub Compose-network URL.
$env:LOADTEST_RUN_CACHE_ENABLED = 'false'
docker compose -p pv-loadtest -f docker-compose.yml -f docker-compose.loadtest.yml up --build -d --scale agent-worker=3 api agent-worker llm-stub
docker compose --profile tools -p pv-loadtest -f docker-compose.yml -f docker-compose.loadtest.yml run --rm locust -f /mnt/locust/locustfile.py --host http://api:8000 --headless --csv /mnt/locust/results/w3 --csv-full-history -t 7m --stop-timeout 240
python loadtest/analyze.py loadtest/results/w3
docker compose -p pv-loadtest -f docker-compose.yml -f docker-compose.loadtest.yml down -v
```
Each ramp takes 7 minutes: run it beforehand and present the output; run a short live segment. *Say:* declared target, measured RPS/p50/p95, the user count where p95 broke, which component (use `pv_inflight_runs`, `pv_queue_depth` and `docker stats` as evidence), and how much `--scale` helped. Do not assume the bottleneck before measuring.

### 10. The section 4.1 case
```powershell
python scripts/demo.py submit case41
```
*Show:* `expedited_report`; hospitalization seriousness (IPC p.31); listed skin-reaction terms (Tegretol label); 15-day timeline (IPC 2.7.1 p.33); NDCT p.229 provision; conflict flag; and QPPV confirmation path. Trace must show `find_modifiers` and the derived search. For the optional provider-on rehearsal, use this case once only: set `LLM_PROVIDER=openai_compat`, `LLM_MIN_INTERVAL_S=5`, `LLM_MAX_RETRIES=0`, and `AGENT_RUN_TIMEOUT_S=180` in the same shell, recreate api/worker, submit one report, then restore `LLM_PROVIDER=none`. Never print the API key or endpoint.
```powershell
$env:LLM_PROVIDER = 'openai_compat'
$env:LLM_MIN_INTERVAL_S = '5'
$env:LLM_MAX_RETRIES = '0'
$env:AGENT_RUN_TIMEOUT_S = '180'
docker compose up -d --force-recreate api agent-worker
python scripts/demo.py submit case41
$env:LLM_PROVIDER = 'none'
$env:LLM_MIN_INTERVAL_S = '0'
$env:LLM_MAX_RETRIES = '0'
$env:AGENT_RUN_TIMEOUT_S = '45'
docker compose up -d --force-recreate api agent-worker
```
Use the endpoint, model and key already configured in `.env`; never print or copy them into the terminal transcript. This optional provider-on check is one report submission only.

## What if something breaks live
| Failure | Do |
|---|---|
| Stack will not start | `docker compose logs --tail=80 <service>`; port clash -> use the configured host-port variables in `.env`; avoid `down -v` unless you intend to delete local run and index data |
| LLM key/quota fails | Set `LLM_PROVIDER=none` and recreate worker: system still answers (rules-only). Mention this *is* graceful degradation |
| Grafana empty | Wait ~30 s; check Prometheus targets at :9090/targets; use recorded screenshots |
| cAdvisor unhappy (Windows) | Use `docker stats`; say why |
| Total Docker failure | Offline fallback: `python -m pv.eval.run` and `python -m unittest discover -s tests -t .` show the agent working with no containers (partial credit for honesty; rehearse so it never comes to this) |
