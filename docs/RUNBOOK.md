# Runbook (D8): the two most likely failure modes

## 1. LLM provider failing or rate-limited (circuit breaker open)

**Symptom.** Runs still complete but results carry `degraded: ["llm_unavailable:..."]` and `requires_qppv_confirmation: true`; end-to-end latency may rise first (retries with backoff). Grafana: *Circuit breaker state* = 2, *Retries/s* kind=llm elevated, LLM latency p95 high.

**Confirm.**
```bash
docker compose logs agent-worker | grep -E "llm_call|unavailable|circuit_open" | tail
curl -s localhost:8000/runs/<run_id>/trace | python -m json.tool | grep -E '"outcome"|breaker|error'
curl -s localhost:9090/api/v1/query --data-urlencode 'query=max(pv_circuit_state)'   # 0 closed, 1 half-open, 2 open
```
A trace showing `outcome: unavailable` with `error: circuit_open` confirms the breaker tripped; `Timeout`/`HTTPStatusError` entries before it show the underlying cause (outage vs rate limit vs bad key).

**Recover.**
1. Check provider status page and the key/quota (`LLM_API_KEY`, spending limit).
2. The system is **safe meanwhile**: the rule plan still produces verified, cited answers; nothing is fabricated. Tell the QPPV that results are flagged for confirmation.
3. Fix the cause (new key, wait for quota reset, or switch `LLM_BASE_URL`/`LLM_MODEL` in `.env`), then `docker compose up -d --force-recreate agent-worker`.
4. The breaker closes by itself: after `BREAKER_COOLDOWN_S` it half-opens, lets one probe through, and closes on success. No manual reset needed.
5. Re-run affected cases: `curl -XPOST 'localhost:8000/reports?nocache=true' ...` (degraded results are never cached, so a normal resubmission also works).

## 2. Runs stuck in `queued` / `running` (workers dead or backlog)

**Symptom.** `GET /runs/{id}` stays `queued` or `running`; `/ready` returns 503 with `workers: 0`; Grafana *Queue depth* climbing, *Availability* of `agent-worker` low.

**Confirm.**
```bash
curl -s localhost:8000/ready                      # workers count, queue depth, index freshness
docker compose ps agent-worker                    # exited / unhealthy?
docker compose logs --tail=50 agent-worker        # JSON logs; grep the run_id (it is the correlation id)
docker compose exec redis redis-cli llen pv:queue         # waiting jobs
docker compose exec redis redis-cli llen pv:processing    # claimed jobs (should drain)
```
If `pv:processing` is non-zero but no worker is alive, those jobs await lease expiry (`AGENT_RUN_TIMEOUT_S + 45 s`) before another worker reaps and requeues them.

**Recover.**
1. `docker compose up -d --scale agent-worker=3` (restarts missing workers; healthchecks gate them).
2. Jobs claimed by dead workers are requeued automatically by the reaper (up to 3 attempts; then the run is marked `failed`, never answered).
3. If the cause is the index (`/ready` shows 0 chunks): `docker compose run --rm indexer` (idempotent) then restart workers.
4. If Redis lost data: it runs with AOF persistence on the `redisdata` volume; if the volume was wiped, queued runs are gone, so ask callers to resubmit (clients hold the `run_id` and will see `not_found`).
5. If the queue is just long (burst): scale out (`--scale agent-worker=6`); check *Per-step latency* to see whether the LLM or retrieval is slow.

## 3. (bonus) Quarantine growing fast
A client is sending malformed or injected requests. `curl localhost:8000/quarantine` lists recent ids; `GET /quarantine/{id}` shows field-level errors and the client id. Rate limiting (`CLIENT_RATE_LIMIT_PER_MINUTE`) bounds the damage; `pv_intake_rejected_total{code}` shows the dominant error.
