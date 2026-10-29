# Pharmacovigilance adverse-event intake & triage service

Multi-agent service that accepts an adverse-event report, validates it, assesses **seriousness** and **expectedness** against the NDCT Rules 2019, the IPC MAH guidance and a package insert, and recommends a reporting stream (**expedited / periodic / non-reportable**) with verbatim, page-level citations. It **escalates to a qualified person** whenever it cannot ground an answer. It never submits a regulatory report. Synthetic data only.

> **New to the project? Read `docs/LEARN.md` first.** The Evaluation 1 corpus confirmation and project plan are in `docs/EVALUATION1_PROJECT_PLAN.md`. Demo: `docs/DEMO.md`.

## Run it (clean clone)
Requires Docker with Compose v2. Nothing else runs outside containers. (`scripts/*.py` use only the Python standard library.)

```bash
git clone <repo-url> pv-service && cd pv-service
cp .env.example .env              # works as-is: no model key needed (rules-only mode)
docker compose up --build         # first build downloads images; later starts take seconds
```
When `docker compose ps` shows `api`, `agent-worker`, `redis` healthy (the `indexer` exits 0 after building the index):

| URL | What |
|---|---|
| http://localhost:8080 | web client: submit, see recommendation + citations + escalation path, give feedback |
| http://localhost:8000/docs | REST API (OpenAPI) |
| http://localhost:3000 | Grafana (dashboard *Pharmacovigilance agent service*) |
| http://localhost:16686 | Jaeger (traces) |
| http://localhost:9090 | Prometheus |

Try it:
```bash
python scripts/demo.py submit case41      # the section 4.1 case
python scripts/demo.py trace <run_id>     # every tool call, passage, prompt version, latency, cost
```
Scale the agents: `docker compose up -d --scale agent-worker=3`.

### Add a model (optional)
Any OpenAI-compatible provider (free tier or local Ollama). Edit `.env`: `LLM_PROVIDER=openai_compat`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`. **Set a spending limit on the account first.** Recreate workers: `docker compose up -d --force-recreate agent-worker`. Optional vector retrieval: build with `WITH_EMBEDDINGS=1` and set `EMBEDDINGS_ENABLED=true` (large image: CPU torch + sentence-transformers).

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
```bash
pip install -r requirements-dev.txt            # python 3.10+; poppler-utils recommended (pdftotext)
python -m pv.corpus.build                      # idempotent index build  -> data/index
python -m unittest discover -s tests -t .      # 57 tests (4 need FastAPI)
python -m pv.eval.run --validate-gold          # gold passages exist in the corpus
python -m pv.eval.run --label agentic          # 26-scenario evaluation
AGENTIC_RETRIEVAL=false python -m pv.eval.run --label single_pass_baseline   # the "before"
```

## Load test (stub LLM)
```bash
docker compose -f docker-compose.yml -f docker-compose.loadtest.yml up --build -d --scale agent-worker=3
docker compose -f docker-compose.yml -f docker-compose.loadtest.yml run --rm locust -f /mnt/locust/locustfile.py \
   --host http://api:8000 --headless --csv /mnt/locust/results/w3 --csv-full-history
python loadtest/analyze.py loadtest/results/w3
```
Declared SLO: `docs/SLO.md` (commit it *before* measuring).

## Layout
`pv/` application · `prompts/` versioned prompts · `corpus/` source PDFs + `drugs.json` · `eval/` scenarios + results · `tests/` · `loadtest/` · `observability/` · `frontend/` · `scripts/` · `docs/` (architecture, data contract, SLO, runbook, incident log, evaluation, report, learning guide, demo)

## Configuration
Everything environment-specific is injected at runtime; see `.env.example` (documented). The repository contains no credentials, endpoints or model identifiers.

## Status and honesty
C0-C6 are recorded in Git. The 57-test suite passed (4 skipped); Docker/API and observability were exercised; the C5 Gemini comparison was mostly degraded and did not change the production prompt; C6 vector retrieval and incremental embeddings were exercised. A later dependency-alignment image rebuild stalled during Docker unpacking, so verify a fresh embedding-enabled build and tracing before relying on it. Locust/SLO, cache-effect and cost measurements remain outstanding; do not treat the C4 smoke batch as a load test. The evaluation labels need an independent reviewer. Limitations: the selected label is a US Tegretol insert, there is one drug, guardrails are rule-based, and the serious-expected IPC/NDCT conflict remains flagged for QPPV confirmation. See `docs/EVALUATION1_PROJECT_PLAN.md` and `docs/LEARN.md`.
