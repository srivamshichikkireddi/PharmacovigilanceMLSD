"""Redis-backed platform state. One small class so the worker, API and tests share one contract.

Queue semantics (A4): at-least-once with a lease.
  submit -> LPUSH queue ; claim -> BLMOVE queue -> processing (atomic) + lease key with TTL ;
  ack -> LREM processing. A crashed worker's lease expires, and any worker's reaper moves the job back."""
import hashlib, json, time, uuid

QUEUE, PROC = "pv:queue", "pv:processing"
RUN_TTL = 7 * 86400


def now(): return time.time()


class Store:
    def __init__(self, r): self.r = r

    # ---- runs ----
    def new_run_id(self): return uuid.uuid4().hex[:16]

    def get_run(self, run_id):
        v = self.r.get(f"pv:run:{run_id}")
        return json.loads(v) if v else None

    def put_run(self, rec):
        self.r.set(f"pv:run:{rec['run_id']}", json.dumps(rec), ex=RUN_TTL)

    def submit(self, report, client, trace_ctx=None, bypass_cache=False):
        run_id = self.new_run_id()
        job = {"run_id": run_id, "report": report, "client": client, "submitted_at": now(),
               "trace_ctx": trace_ctx or {}, "bypass_cache": bypass_cache, "attempts": 0}
        self.put_run({"run_id": run_id, "status": "queued", "submitted_at": job["submitted_at"], "client": client})
        self.r.lpush(QUEUE, json.dumps(job))
        return run_id

    def claim(self, timeout=2, lease_s=90):
        raw = self.r.blmove(QUEUE, PROC, timeout, "RIGHT", "LEFT")
        if raw is None:
            return None, None
        raw = raw.decode() if isinstance(raw, bytes) else raw
        job = json.loads(raw)
        self.r.set(f"pv:lease:{job['run_id']}", "1", ex=lease_s)
        return raw, job

    def ack(self, raw, run_id):
        self.r.lrem(PROC, 1, raw)
        self.r.delete(f"pv:lease:{run_id}")

    def reap(self, max_attempts=3):
        """Requeue jobs whose worker died (lease expired). Returns number requeued/failed."""
        n = 0
        for raw in self.r.lrange(PROC, 0, -1):
            raw = raw.decode() if isinstance(raw, bytes) else raw
            job = json.loads(raw)
            if self.r.exists(f"pv:lease:{job['run_id']}"):
                continue
            self.r.lrem(PROC, 1, raw)
            job["attempts"] += 1
            if job["attempts"] >= max_attempts:
                rec = self.get_run(job["run_id"]) or {"run_id": job["run_id"]}
                rec.update(status="failed", error="worker_lost_after_retries", finished_at=now())
                self.put_run(rec)
            else:
                self.r.lpush(QUEUE, json.dumps(job))
            n += 1
        return n

    def queue_depth(self): return int(self.r.llen(QUEUE))

    # ---- trace ----
    def put_trace(self, run_id, trace): self.r.set(f"pv:trace:{run_id}", json.dumps(trace), ex=RUN_TTL)

    def get_trace(self, run_id):
        v = self.r.get(f"pv:trace:{run_id}")
        return json.loads(v) if v else None

    # ---- quarantine (D1) ----
    def quarantine(self, payload_text, errors, client):
        qid = "q_" + uuid.uuid4().hex[:12]
        rec = {"quarantine_id": qid, "received_at": now(), "client": client, "errors": errors,
               "payload_preview": (payload_text or "")[:2000], "agent_run_started": False}
        self.r.set(f"pv:quarantine:{qid}", json.dumps(rec), ex=RUN_TTL)
        self.r.lpush("pv:quarantine:index", qid)
        self.r.ltrim("pv:quarantine:index", 0, 999)
        return rec

    def get_quarantine(self, qid):
        v = self.r.get(f"pv:quarantine:{qid}")
        return json.loads(v) if v else None

    def quarantine_ids(self, n=20): return [i.decode() if isinstance(i, bytes) else i for i in self.r.lrange("pv:quarantine:index", 0, n - 1)]

    # ---- idempotency / cache (D7) ----
    def idem_get_or_set(self, key, run_id):
        ok = self.r.set(f"pv:idem:{key}", run_id, nx=True, ex=86400)
        if ok:
            return None
        v = self.r.get(f"pv:idem:{key}")
        return v.decode() if isinstance(v, bytes) else v

    @staticmethod
    def cache_key(report, corpus_version, salt):
        blob = json.dumps(report, sort_keys=True) + "|" + corpus_version + "|" + salt
        return hashlib.sha256(blob.encode()).hexdigest()

    def cache_get(self, key):
        v = self.r.get(f"pv:cache:{key}")
        return json.loads(v) if v else None

    def cache_put(self, key, value, ttl=86400): self.r.set(f"pv:cache:{key}", json.dumps(value), ex=ttl)

    # ---- rate limit (D7): fixed window per client ----
    def rate_limit(self, client, limit, window_s=60):
        k = f"pv:rl:{client}:{int(now() // window_s)}"
        n = self.r.incr(k)
        if n == 1:
            self.r.expire(k, window_s + 5)
        return n <= limit, max(0, limit - n)

    # ---- feedback (L6) ----
    def add_feedback(self, rec):
        self.r.lpush("pv:feedback", json.dumps(rec))
        self.r.ltrim("pv:feedback", 0, 9999)

    def feedback_summary(self):
        items = [json.loads(x) for x in self.r.lrange("pv:feedback", 0, -1)]
        ok = sum(1 for i in items if i["satisfactory"])
        by_route, reasons = {}, {}
        for i in items:
            d = by_route.setdefault(i.get("route") or "unknown", {"satisfactory": 0, "unsatisfactory": 0})
            d["satisfactory" if i["satisfactory"] else "unsatisfactory"] += 1
            if not i["satisfactory"] and i.get("reason"):
                reasons[i["reason"][:60]] = reasons.get(i["reason"][:60], 0) + 1
        return {"total": len(items), "satisfactory": ok, "satisfaction_rate": round(ok / len(items), 3) if items else None,
                "by_route": by_route, "top_unsatisfactory_reasons": sorted(reasons.items(), key=lambda kv: -kv[1])[:5]}

    # ---- cost / latency / quality aggregates (L5, L6, D6) ----
    def record_run_stats(self, res, e2e_s):
        self.r.hincrby("pv:stats", "runs", 1)
        self.r.hincrby("pv:stats", f"route:{res['route']}", 1)
        self.r.hincrbyfloat("pv:stats", "cost_total", res["cost"]["total_usd"])
        for step, v in res["cost"]["per_step"].items():
            self.r.hincrbyfloat("pv:stats", f"cost_step:{step}", v["usd"])
        self.r.lpush("pv:e2e", json.dumps([now(), round(e2e_s, 3)]))
        self.r.ltrim("pv:e2e", 0, 4999)

    def stats(self):
        h = {(k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v) for k, v in self.r.hgetall("pv:stats").items()}
        runs = int(h.get("runs", 0))
        steps = {k[len("cost_step:"):]: float(v) for k, v in h.items() if k.startswith("cost_step:")}
        lat = sorted(x[1] for x in (json.loads(i) for i in self.r.lrange("pv:e2e", 0, -1)))
        pct = lambda p: round(lat[min(len(lat) - 1, int(p * len(lat)))], 3) if lat else None
        total = float(h.get("cost_total", 0))
        return {"runs": runs, "routes": {k[6:]: int(v) for k, v in h.items() if k.startswith("route:")},
                "e2e_seconds": {"p50": pct(0.5), "p95": pct(0.95), "samples": len(lat)},
                "cost": {"total_usd": round(total, 6), "per_100_runs_usd": round(total / runs * 100, 6) if runs else None,
                         "per_step_usd": steps, "largest_driver": max(steps, key=steps.get) if steps else None},
                "queue_depth": self.queue_depth()}

    def add_quality(self, rec):
        self.r.lpush("pv:quality", json.dumps(rec))
        self.r.ltrim("pv:quality", 0, 999)

    def quality_series(self, n=200): return [json.loads(x) for x in self.r.lrange("pv:quality", 0, n - 1)][::-1]
