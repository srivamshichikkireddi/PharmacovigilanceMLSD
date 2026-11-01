# Runbook (D8): the two most likely failure modes

## 1. LLM provider failing or rate-limited (circuit breaker open)

**Symptom.** Runs still complete but results carry `degraded: ["llm_unavailable:..."]` and `requires_qppv_confirmation: true`; end-to-end latency may rise first (retries with backoff). Grafana: *Circuit breaker state* = 2, *Retries/s* kind=llm elevated, LLM latency p95 high.

**Confirm.**
```powershell
docker compose logs --tail 200 agent-worker | Select-String -Pattern 'llm_call|unavailable|circuit_open' | Select-Object -Last 20
# Set $runId to the affected run ID first.
$trace = Invoke-RestMethod -Uri "http://127.0.0.1:8000/runs/$runId/trace"
$trace.events | Where-Object { $_.type -in @('llm_call', 'verification') } | Select-Object type, agent_step, outcome, error, breaker
$query = [uri]::EscapeDataString('max(pv_circuit_state)')
Invoke-RestMethod -Uri "http://127.0.0.1:9090/api/v1/query?query=$query"
```
A trace showing `outcome: unavailable` with `error: circuit_open` confirms the breaker tripped; `Timeout`/`HTTPStatusError` entries before it show the underlying cause (outage vs rate limit vs bad key).

**Recover.**
1. Check provider status page and the key/quota (`LLM_API_KEY`, spending limit).
2. The system is **safe meanwhile**: the rule plan still produces verified, cited answers; nothing is fabricated. Tell the QPPV that results are flagged for confirmation.
3. Fix the cause (new key, wait for quota reset, or switch `LLM_BASE_URL`/`LLM_MODEL` in `.env`), then `docker compose up -d --force-recreate agent-worker`.
4. The breaker closes by itself: after `BREAKER_COOLDOWN_S` it half-opens, lets one probe through, and closes on success. No manual reset needed.
5. Re-run an affected synthetic case with `python scripts/demo.py submit headache` (the demo client submits with cache bypass enabled).

## 2. Runs stuck in `queued` / `running` (workers dead or backlog)

**Symptom.** `GET /runs/{id}` stays `queued` or `running`; `/ready` returns 503 with `workers: 0`; Grafana *Queue depth* climbing, *Availability* of `agent-worker` low.

**Confirm.**
```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/ready'
docker compose ps agent-worker
docker compose logs --tail 50 agent-worker
docker compose exec -T redis redis-cli LLEN pv:queue
docker compose exec -T redis redis-cli LLEN pv:processing
```
If `pv:processing` is non-zero but no worker is alive, those jobs await lease expiry (`AGENT_RUN_TIMEOUT_S + 45 s`) before another worker reaps and requeues them.

**Recover.**
1. `docker compose up -d --scale agent-worker=3` (restarts missing workers; healthchecks gate them).
2. Jobs claimed by dead workers are requeued automatically by the reaper (up to 3 attempts; then the run is marked `failed`, never answered).
3. If the cause is the index (`/ready` shows 0 chunks): `docker compose run --rm indexer` (idempotent) then restart workers.
4. If Redis lost data: it runs with AOF persistence on the `redisdata` volume; if the volume was wiped, queued runs are gone, so ask callers to resubmit (clients hold the `run_id` and will see `not_found`).
5. If the queue is just long (burst): scale out (`--scale agent-worker=6`); check *Per-step latency* to see whether the LLM or retrieval is slow.

## 3. (bonus) Quarantine growing fast
A client is sending malformed or injected requests. `Invoke-RestMethod -Uri 'http://127.0.0.1:8000/quarantine'` lists recent ids; set `$quarantineId` to one of them, then use `Invoke-RestMethod -Uri "http://127.0.0.1:8000/quarantine/$quarantineId"` to view field-level errors and the client id. Rate limiting (`CLIENT_RATE_LIMIT_PER_MINUTE`) bounds the damage; `pv_intake_rejected_total{code}` shows the dominant error.
