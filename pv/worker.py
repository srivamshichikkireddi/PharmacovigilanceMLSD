"""The agent worker: claims jobs from the queue and runs the agent graph. Scale it:
    docker compose up --scale agent-worker=3
Each process runs WORKER_CONCURRENCY claim loops. Exposes Prometheus metrics on WORKER_METRICS_PORT."""
import json, logging, os, signal, socket, threading, time

from pv import metrics, tracing
from pv.config import settings
from pv.logging_utils import correlation_id, log_kv, setup_logging

log = logging.getLogger("worker")


def quality_sample(index, res):
    """L6: reference-free quality score of a live run (no gold label exists for live traffic)."""
    from pv.eval import metrics as M
    if res["route"] == "human_escalation":
        return {"groundedness": None, "faithfulness": None, "answered": 0}
    return {"groundedness": M.groundedness(index, res), "faithfulness": M.faithfulness(res), "answered": 1}


def process(store, index, llm, raw, job, settings=settings):
    from pv.agents.pipeline import run_case
    run_id = job["run_id"]
    correlation_id.set(run_id)
    rec = store.get_run(run_id) or {"run_id": run_id}
    rec.update(status="running", started_at=time.time())
    store.put_run(rec)
    metrics.INFLIGHT.inc()
    try:
        ckey = store.cache_key(job["report"], index.version, f"{settings.agentic}|{settings.prompt_normalise}|{settings.llm_model}")
        cached = None if (job.get("bypass_cache") or not settings.cache_enabled) else store.cache_get(ckey)
        with tracing.span("worker.process", parent=tracing.extract_context(job.get("trace_ctx")), run_id=run_id):
            if cached:
                metrics.CACHE.labels("run", "hit").inc()
                res, trace = dict(cached["result"], run_id=run_id, cache_hit=True, cost={"total_usd": 0.0, "prompt_tokens": 0, "completion_tokens": 0, "per_step": {}}), \
                    [{"t_ms": 0, "type": "cache_hit", "agent_step": "worker", "key": ckey[:12], "original_run": cached["result"].get("run_id")}]
            else:
                metrics.CACHE.labels("run", "miss").inc()
                res, trace = run_case(run_id, job["report"], index, llm, settings)
                res["cache_hit"] = False
                if not res["degraded"] and "verification_failed" not in res["escalation"]["reasons"]:
                    store.cache_put(ckey, {"result": res})   # never cache a degraded or unverified answer
        done = time.time()
        res["queue_wait_seconds"] = round(rec["started_at"] - job["submitted_at"], 3)
        res["end_to_end_seconds"] = round(done - job["submitted_at"], 3)
        rec.update(status="done", finished_at=done, result=res)
        store.put_trace(run_id, trace)
        store.put_run(rec)
        store.record_run_stats(res, res["end_to_end_seconds"])
        metrics.E2E_SECONDS.labels(res["route"]).observe(res["end_to_end_seconds"])
        metrics.RUNS.labels("done", res["route"]).inc()
        import random
        if random.random() < settings.quality_sample_rate:
            q = quality_sample(index, res)
            store.add_quality({"ts": done, "run_id": run_id, **q})
            for k in ("groundedness", "faithfulness"):
                if q[k] is not None:
                    metrics.QUALITY.labels(k).observe(q[k])
        log_kv(log, "run_done", route=res["route"], e2e_s=res["end_to_end_seconds"], tool_calls=res["tool_calls"], cache_hit=res["cache_hit"])
    except Exception as exc:  # noqa  a crashed run is a failed run, never a confident answer
        log.exception("run_failed")
        rec.update(status="failed", finished_at=time.time(), error=f"{type(exc).__name__}: {exc}"[:300])
        store.put_run(rec)
        metrics.RUNS.labels("failed", "none").inc()
    finally:
        metrics.INFLIGHT.dec()


def loop(store, index, llm, stop):
    while not stop.is_set():
        try:
            raw, job = store.claim(timeout=2, lease_s=int(settings.run_timeout_s) + 45)
            if job is None:
                continue
            process(store, index, llm, raw, job)
            store.ack(raw, job["run_id"])
        except Exception:  # noqa  e.g. Redis blip: log, back off, keep serving
            log.exception("worker_loop_error")
            time.sleep(1)


def main():
    setup_logging("worker")
    tracing.init_tracing("pv-worker")
    import redis
    from pv.corpus.retriever import get_index
    from pv.llm.client import make_client
    from pv.llm.resilience import ResilientLLM
    from pv.store import Store
    store = Store(redis.Redis.from_url(settings.redis_url))
    index = get_index()
    metrics.publish_index_metrics(index.manifest)
    llm = ResilientLLM(make_client(settings), settings)
    try:
        from prometheus_client import start_http_server
        start_http_server(settings.worker_metrics_port)
    except Exception:  # noqa
        log.warning("metrics server not started")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    log_kv(log, "worker_started", concurrency=settings.worker_concurrency, provider=settings.llm_provider,
           backend=index.backend, corpus_version=index.version, chunks=len(index.chunks))

    def housekeeping():
        host = socket.gethostname()
        while not stop.is_set():
            store.r.set(f"pv:worker:{host}", "1", ex=30)
            metrics.QUEUE_DEPTH.set(store.queue_depth())
            n = store.reap()
            if n:
                metrics.RETRIES.labels("job").inc(n)
                log_kv(log, "reaped_jobs", n=n)
            stop.wait(10)

    threads = [threading.Thread(target=housekeeping, daemon=True)] + \
              [threading.Thread(target=loop, args=(store, index, llm, stop)) for _ in range(settings.worker_concurrency)]
    for t in threads:
        t.start()
    for t in threads[1:]:
        t.join()
    log.info("worker_stopped_gracefully")


if __name__ == "__main__":
    main()
