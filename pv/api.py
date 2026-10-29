"""FastAPI service. Why asynchronous submit-and-poll (A4): a run takes tens of seconds (several LLM
calls), far longer than a sensible HTTP timeout, and clients/proxies would hold connections open.
POST returns 202 + run_id immediately; the caller polls GET /runs/{id}. Polling beats callbacks here:
the caller needs no public endpoint, there is no delivery-retry logic to build, and a browser can do it.
A Redis list is the queue between submission and execution (workers scale independently)."""
import json, logging, time, uuid

import redis
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from pv import metrics, tracing
from pv.config import settings
from pv.logging_utils import correlation_id, log_kv, setup_logging
from pv.schemas import CONTRACT_VERSION, json_schema, validate_report
from pv.store import Store

setup_logging("api")
tracing.init_tracing("pv-api")
log = logging.getLogger("api")
app = FastAPI(title="Pharmacovigilance Intake & Triage", version=CONTRACT_VERSION,
              description="Decision support only. The system never submits a regulatory report; a qualified person decides.")
_store = None


def store() -> Store:
    global _store
    if _store is None:
        _store = Store(redis.Redis.from_url(settings.redis_url))
    return _store


def client_id(request: Request):
    cid = request.headers.get("x-client-id")
    if cid:
        return "c:" + cid[:64]
    if settings.trust_proxy and request.headers.get("x-forwarded-for"):
        return "ip:" + request.headers["x-forwarded-for"].split(",")[0].strip()
    return "ip:" + (request.client.host if request.client else "unknown")


@app.middleware("http")
async def observe(request: Request, call_next):
    cid = request.headers.get("x-correlation-id") or uuid.uuid4().hex[:16]
    correlation_id.set(cid)
    t0 = time.monotonic()
    response = await call_next(request)
    route = request.scope.get("route")
    ep = route.path if route else "unmatched"
    metrics.REQUESTS.labels(ep, str(response.status_code)).inc()
    metrics.REQ_SECONDS.labels(ep).observe(time.monotonic() - t0)
    response.headers["x-correlation-id"] = cid
    return response


@app.on_event("startup")
def startup():
    try:
        from pv.corpus.retriever import get_index
        metrics.publish_index_metrics(get_index().manifest)
    except Exception:  # noqa
        log.warning("index not available at startup")


@app.get("/health")
def health():
    """Liveness: the process is up. Cheap, no dependencies."""
    return {"status": "ok"}


@app.get("/ready")
def ready():
    """Readiness: Redis reachable, index built, at least one worker alive."""
    try:
        store().r.ping()
        workers = sum(1 for _ in store().r.scan_iter("pv:worker:*"))
        from pv.corpus.retriever import get_index
        ix = get_index()
        ok = workers > 0 and len(ix.chunks) > 0
        body = {"ready": ok, "workers": workers, "queue_depth": store().queue_depth(), "index": {
            "built_utc": ix.manifest.get("built_utc"), "chunks": len(ix.chunks), "corpus_version": ix.version,
            "documents": [{"name": d["name"], "sha256": d["sha256"][:12], "pages": d["pdf_pages"]} for d in ix.manifest.get("documents", [])],
            "embedding_model": ix.manifest.get("embedding_model"), "throughput_chunks_per_s": ix.manifest.get("throughput_chunks_per_s")}}
        return JSONResponse(body, status_code=200 if ok else 503)
    except Exception as exc:  # noqa
        return JSONResponse({"ready": False, "error": str(exc)[:120]}, status_code=503)


@app.get("/metrics")
def prom():
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
    metrics.QUEUE_DEPTH.set(store().queue_depth())
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/contract")
def contract():
    """The published data contract for POST /reports (also in contracts/report.v1.schema.json)."""
    return json_schema()


@app.post("/reports", status_code=202)
async def submit_report(request: Request, nocache: bool = False):
    st, cid = store(), client_id(request)
    ok, remaining = st.rate_limit(cid, settings.rate_limit_per_min)
    if not ok:
        metrics.RATE_LIMITED.inc()
        return JSONResponse({"error": "rate_limited", "detail": f"limit {settings.rate_limit_per_min}/minute per client"},
                            status_code=429, headers={"Retry-After": "60"})
    raw = (await request.body()).decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw)
    except Exception:  # noqa
        payload = None
        errors = [{"loc": "body", "code": "json", "message": "Body is not valid JSON"}]
    else:
        clean, errors = validate_report(payload)
    if errors:                                              # D1: a malformed request must not start an agent run
        rec = st.quarantine(raw, errors, cid)
        for e in errors:
            metrics.REJECTED.labels(e["code"]).inc()
        log_kv(log, "quarantined", quarantine_id=rec["quarantine_id"], codes=[e["code"] for e in errors])
        return JSONResponse({"error": "validation_failed", "quarantine_id": rec["quarantine_id"], "errors": errors,
                             "agent_run_started": False}, status_code=422)
    idem = request.headers.get("idempotency-key")
    if idem:
        existing = st.idem_get_or_set(f"{cid}:{idem}", "pending")
        if existing and existing != "pending":
            return JSONResponse({"run_id": existing, "status_url": f"/runs/{existing}", "idempotent_replay": True}, status_code=200)
    with tracing.span("api.submit"):
        run_id = st.submit(clean, cid, tracing.inject_context(), bypass_cache=nocache)
    if idem:
        st.r.set(f"pv:idem:{cid}:{idem}", run_id, ex=86400)
    correlation_id.set(run_id)
    log_kv(log, "run_submitted", client=cid)
    return JSONResponse({"run_id": run_id, "status": "queued", "status_url": f"/runs/{run_id}", "trace_url": f"/runs/{run_id}/trace",
                         "rate_limit_remaining": remaining}, status_code=202)


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    rec = store().get_run(run_id)
    return JSONResponse(rec, status_code=200) if rec else JSONResponse({"error": "not_found"}, status_code=404)


@app.get("/runs/{run_id}/trace")
def get_trace(run_id: str):
    """D5: the whole run, reconstructible: every tool call, query, passage, prompt version, latency, cost, verification."""
    rec, tr = store().get_run(run_id), store().get_trace(run_id)
    if not rec or tr is None:
        return JSONResponse({"error": "not_found_or_not_finished"}, status_code=404)
    res = rec.get("result", {})
    passages = {}
    try:
        from pv.corpus.retriever import get_index
        ix = get_index()
        for ev in tr:
            for pid in ev.get("passage_ids", []) or []:
                if pid in ix.by_id:
                    c = ix.by_id[pid]
                    passages[pid] = {"source": c["source"], "page": c["page"], "text": c["text"]}
    except Exception:  # noqa
        pass
    return {"run_id": run_id, "status": rec["status"], "prompt_versions": res.get("prompt_versions"), "cost": res.get("cost"),
            "verification": res.get("verification"), "events": tr, "passages": passages,
            "latency": {"end_to_end_seconds": res.get("end_to_end_seconds"), "queue_wait_seconds": res.get("queue_wait_seconds"),
                        "run_seconds": res.get("run_seconds")}}


@app.post("/runs/{run_id}/feedback")
async def feedback(run_id: str, request: Request):
    try:
        body = await request.json()
    except Exception:  # noqa
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    rec = store().get_run(run_id)
    if not rec:
        return JSONResponse({"error": "not_found"}, status_code=404)
    sat, reason = body.get("satisfactory"), (body.get("reason") or "")
    if not isinstance(sat, bool) or not isinstance(reason, str) or len(reason) > 500 or (not sat and len(reason.strip()) < 3):
        return JSONResponse({"error": "satisfactory (bool) required; reason (3-500 chars) required when unsatisfactory"}, status_code=422)
    store().add_feedback({"ts": time.time(), "run_id": run_id, "satisfactory": sat, "reason": reason.strip(),
                          "route": (rec.get("result") or {}).get("route")})
    metrics.FEEDBACK.labels(str(sat).lower()).inc()
    return {"recorded": True}


@app.get("/feedback/summary")
def feedback_summary():
    return store().feedback_summary()


@app.get("/quarantine/{qid}")
def get_quarantine(qid: str):
    rec = store().get_quarantine(qid)
    return JSONResponse(rec, status_code=200) if rec else JSONResponse({"error": "not_found"}, status_code=404)


@app.get("/quarantine")
def list_quarantine():
    return {"recent": store().quarantine_ids(20)}


@app.get("/ops/summary")
def ops_summary():
    return {**store().stats(), "quality_samples": len(store().quality_series()), "feedback": store().feedback_summary()}


@app.get("/ops/quality")
def ops_quality():
    return {"series": store().quality_series()}
