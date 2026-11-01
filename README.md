# Pharmacovigilance adverse-event intake & triage service

Multi-agent service that accepts an adverse-event report, validates it, assesses **seriousness** and **expectedness** against the NDCT Rules 2019, the IPC MAH guidance and a package insert, and recommends a reporting stream (**expedited / periodic / non-reportable**) with verbatim, page-level citations. It **escalates to a qualified person** whenever it cannot ground an answer. It never submits a regulatory report. Synthetic data only.

> **New to the project?** Start with this quick start. Learning notes are in `docs/LEARN.md`; demo instructions are in `docs/DEMO.md`.

## Run it (clean clone)
Requires Docker Desktop (or Docker Engine) with Compose v2, with the engine running. The service runs in containers; the demo client uses Python 3 and only the standard library.

```powershell
# From the repository root after cloning:
Copy-Item .env.example .env
notepad .env  # Set a unique local GRAFANA_PASSWORD. Leave provider fields blank for rules-only mode.
docker compose up --build
```
The first build downloads images. The one-shot `indexer` should exit successfully; API, worker, Redis, web, and observability services should become healthy. Check with `docker compose ps`.

| URL | What |
|---|---|
| http://localhost:8080 | web client: submit, see recommendation + citations + escalation path, give feedback |
| http://localhost:8000/docs | REST API (OpenAPI) |
| http://localhost:3000 | Grafana (dashboard *Pharmacovigilance agent service*) |
| http://localhost:16686 | Jaeger (traces) |
| http://localhost:9090 | Prometheus |

Submit a valid **synthetic** report and poll its result from PowerShell:
```powershell
$today = Get-Date
$report = @{
    patient_age = 35
    patient_sex = 'female'
    suspect_drug = 'Tegretol (carbamazepine)'
    start_date = $today.AddDays(-10).ToString('yyyy-MM-dd')
    reaction_date = $today.AddDays(-7).ToString('yyyy-MM-dd')
    reporter_type = 'doctor'
    reaction_description = 'Synthetic demo: mild headache began a few days after starting the medicine.'
} | ConvertTo-Json
$accepted = Invoke-RestMethod -Method Post -Uri 'http://localhost:8000/reports' -ContentType 'application/json' -Body $report
$accepted.run_id
$result = Invoke-RestMethod -Uri "http://localhost:8000/runs/$($accepted.run_id)"
while ($result.status -eq 'queued' -or $result.status -eq 'running') {
    Start-Sleep -Seconds 1
    $result = Invoke-RestMethod -Uri "http://localhost:8000/runs/$($accepted.run_id)"
}
$result.result | Select-Object route, serious, expectedness, basis, escalation
```
For the section 4.1 synthetic case and a full trace: `python scripts/demo.py submit case41`, then `python scripts/demo.py trace <run_id>`. Scale the agents with `docker compose up -d --scale agent-worker=3`.

### Add a model (optional)
An OpenAI-compatible provider can be enabled by setting `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL` in `.env`. **Set a spending limit on the account first.** Recreate workers: `docker compose up -d --force-recreate agent-worker`. Optional vector retrieval: build with `WITH_EMBEDDINGS=1`, set `EMBEDDINGS_ENABLED=true`, and configure `EMBEDDING_MODEL` in `.env` (large image: CPU torch + sentence-transformers).

## API
| Method & path | Purpose |
|---|---|
| `POST /reports` | submit (validated at the boundary); `202 {run_id}` or `422` + quarantine. Header `Idempotency-Key`, `X-Client-Id`; `?nocache=true` |
| `GET /runs/{id}` | status, and when done the result |
| `GET /runs/{id}/trace` | full run reconstruction |
| `POST /runs/{id}/feedback` | `{satisfactory, reason}` |
| `GET /feedback/summary`, `/ops/summary`, `/ops/quality` | aggregates, cost per 100 runs, quality over time |
| `GET /quarantine`, `/quarantine/{id}` | rejected requests with field-level errors |
| `GET /contract` | published JSON Schema (also `contracts/report.v1.schema.json`) |
| `GET /health`, `/ready`, `/metrics` | liveness, readiness (+index freshness), Prometheus |

Contract details: `docs/DATA_CONTRACT.md`.

## Without Docker (logic only)
```powershell
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pv.corpus.build
python -m unittest discover -s tests -t .       # 77 tests
python -m pv.eval.run --validate-gold
$env:LLM_PROVIDER = 'none'
python -m pv.eval.run --label agentic           # 29 scenarios
$env:AGENTIC_RETRIEVAL = 'false'
python -m pv.eval.run --label single_pass_baseline
```
The corpus build is idempotent. `poppler-utils` (`pdftotext`) is recommended for PDF extraction. The full service stack and Locust load tests require Docker; this section runs local logic/evaluation only.

## Load test (stub LLM)
Before starting the load-test overlay, add these values to your local `.env` (the Compose service name is used inside the Docker network; do not use `localhost`):
```dotenv
LOADTEST_LLM_BASE_URL=http://llm-stub:9000/v1
LOADTEST_LLM_MODEL=loadtest-stub
```
The stub returns fixed responses with a realistic delay; the model label is required by the client but is not used by the stub. These settings are needed only for the load-test overlay.

```bash
docker compose -f docker-compose.yml -f docker-compose.loadtest.yml up --build -d --scale agent-worker=3
docker compose -f docker-compose.yml -f docker-compose.loadtest.yml run --rm locust -f /mnt/locust/locustfile.py \
   --host http://api:8000 --headless --csv /mnt/locust/results/w3 --csv-full-history
# Wait until the CSV files have been written under loadtest/results/; press Ctrl+C when the CSVs have been written.
python loadtest/analyze.py loadtest/results/w3
```
Declared SLO: `docs/SLO.md` (commit it *before* measuring).

## Layout
`pv/` application · `prompts/` versioned prompts · `corpus/` source PDFs + `drugs.json` · `eval/` scenarios + results · `tests/` · `loadtest/` · `observability/` · `frontend/` · `scripts/` · `docs/` (architecture, data contract, SLO, runbook, incident log, evaluation, report, learning guide, demo)

## Configuration
Runtime provider credentials, endpoints, and model identifiers belong in the local `.env`; keep that file untracked. `.env.example` contains blank provider fields and no real credentials.

## Status and honesty
The current suite passes **77 tests**; `eval/scenarios.json` contains **29 scenarios**. Stub-backed Locust load tests and run-cache effects have been measured (see `docs/REPORT.md`): with three workers and cache off, the corrected ramp met the declared SLO through 32 users (1.3807 runs/s, p95 17 s), while the separate 48/64/96-user ramp first breached the p95 target at 64 users. Run-cache p95 and estimated cost effects were also measured.

Known limitations: the **post-A3 L2/L3 provider comparison is partial**. The provider quota stopped the refreshed v1 run at PV-08; post-A3 v2 ran no scenarios, so there is no current prompt winner or full post-fix 29-scenario provider aggregate. Historical pre-A3 comparisons are not post-fix evidence. **Default retrieval is lexical TF-IDF only** (`EMBEDDINGS_ENABLED=false`); vector retrieval and incremental embedding behavior were exercised separately, but a fresh embedding-enabled image rebuild remains unverified. The selected label is a US Tegretol insert, the corpus covers one drug, evaluation labels need an independent reviewer, guardrails are rule-based, and the serious-expected IPC/NDCT conflict remains flagged for QPPV confirmation. See `docs/EVALUATION.md`, `docs/REPORT.md`, `docs/EVALUATION1_PROJECT_PLAN.md`, and `docs/LEARN.md`.

Credential-pattern history scan: the 16 commits on the preserved local-history backup branch were scanned. The scan found a synthetic Bearer-header test fixture, but no Google-style API key, `sk-` token, or non-empty `LLM_API_KEY` assignment. This was not an exhaustive scan of every remote ref or all GitHub history.
