import json, time
from tests.common import *
from tests.fake_redis import FakeRedis
from pv import worker
from pv.store import Store, PROC, QUEUE


class Queue(unittest.TestCase):
    def setUp(self): self.r = FakeRedis(); self.s = Store(self.r)

    def test_fifo_claim_and_ack(self):
        a = self.s.submit(clean(), "c1"); b = self.s.submit(clean(), "c1")
        raw, job = self.s.claim(); self.assertEqual(job["run_id"], a)
        self.assertEqual(self.r.llen(PROC), 1); self.s.ack(raw, a); self.assertEqual(self.r.llen(PROC), 0)
        self.assertEqual(self.s.get_run(b)["status"], "queued")

    def test_crashed_worker_job_is_requeued_not_lost(self):
        rid = self.s.submit(clean(), "c1"); raw, job = self.s.claim(lease_s=1)
        self.assertEqual(self.s.reap(), 0)                          # lease still valid: not touched
        self.r.exp[f"pv:lease:{rid}"] = time.time() - 1             # worker died, lease expired
        self.assertEqual(self.s.reap(), 1); self.assertEqual(self.r.llen(QUEUE), 1); self.assertEqual(self.r.llen(PROC), 0)
        _, again = self.s.claim(); self.assertEqual(again["attempts"], 1)

    def test_poison_job_fails_after_max_attempts(self):
        rid = self.s.submit(clean(), "c1")
        for _ in range(3):
            self.s.claim(); self.r.exp[f"pv:lease:{rid}"] = time.time() - 1; self.s.reap()
        self.assertEqual(self.s.get_run(rid)["status"], "failed"); self.assertEqual(self.r.llen(QUEUE), 0)

    def test_rate_limit_per_client(self):
        self.assertTrue(all(self.s.rate_limit("a", 3)[0] for _ in range(3))); self.assertFalse(self.s.rate_limit("a", 3)[0])
        self.assertTrue(self.s.rate_limit("b", 3)[0])               # another client is unaffected

    def test_quarantine_records_errors_and_no_run(self):
        rec = self.s.quarantine('{"bad":1}', [{"loc": "x", "code": "missing", "message": "m"}], "c1")
        self.assertFalse(rec["agent_run_started"]); self.assertEqual(self.r.llen(QUEUE), 0)
        self.assertEqual(self.s.get_quarantine(rec["quarantine_id"])["errors"][0]["code"], "missing")

    def test_idempotency_key(self):
        self.assertIsNone(self.s.idem_get_or_set("k", "pending")); self.r.set("pv:idem:k", "run123"); self.assertEqual(self.s.idem_get_or_set("k", "x"), "run123")

    def test_feedback_aggregation(self):
        self.s.add_feedback({"satisfactory": True, "route": "expedited_report"}); self.s.add_feedback({"satisfactory": False, "reason": "wrong clause", "route": "periodic_safety_report"})
        f = self.s.feedback_summary(); self.assertEqual((f["total"], f["satisfaction_rate"]), (2, 0.5)); self.assertEqual(f["top_unsatisfactory_reasons"][0][0], "wrong clause")


class WorkerFlow(Env):
    def setUp(self): self.r = FakeRedis(); self.s = Store(self.r); self.setenv(QUALITY_SAMPLE_RATE=1)

    def drain(self, llm=None):
        raw, job = self.s.claim(); worker.process(self.s, INDEX, llm or nollm(), raw, job); self.s.ack(raw, job["run_id"]); return job["run_id"]

    def test_full_run_through_store_and_trace_reconstruction(self):
        rid = self.s.submit(clean(), "c1"); self.assertEqual(self.s.get_run(rid)["status"], "queued")
        self.drain(); rec = self.s.get_run(rid)
        self.assertEqual(rec["status"], "done"); self.assertEqual(rec["result"]["route"], "expedited_report"); self.assertGreater(rec["result"]["end_to_end_seconds"], 0)
        tr = self.s.get_trace(rid); types = {t["type"] for t in tr}
        self.assertTrue({"tool_call", "verification", "decision", "step_done"} <= types)
        self.assertTrue(all("t_ms" in t for t in tr)); self.assertTrue(all("latency_ms" in t for t in tr if t["type"] == "tool_call"))
        self.assertEqual(self.s.quality_series()[0]["groundedness"], 1.0)             # L6 live sampling

    def test_second_identical_run_is_a_cache_hit_with_zero_cost(self):
        self.s.submit(clean(), "c1"); self.drain(); rid = self.s.submit(clean(), "c2"); self.drain()
        res = self.s.get_run(rid)["result"]; self.assertTrue(res["cache_hit"]); self.assertEqual(res["cost"]["total_usd"], 0.0)
        self.assertEqual(res["route"], "expedited_report")

    def test_nocache_bypasses(self):
        self.s.submit(clean(), "c1"); self.drain(); rid = self.s.submit(clean(), "c1", bypass_cache=True); self.drain()
        self.assertFalse(self.s.get_run(rid)["result"]["cache_hit"])

    def test_degraded_results_are_not_cached(self):
        def boom(m): raise ConnectionError("x")
        self.setenv(LLM_MAX_RETRIES=0)
        r1 = self.s.submit(clean(), "c1"); self.drain(fake(boom)); self.assertTrue(self.s.get_run(r1)["result"]["degraded"])
        r2 = self.s.submit(clean(), "c1"); self.drain(); self.assertFalse(self.s.get_run(r2)["result"]["cache_hit"])   # healthy rerun not served a degraded answer

    def test_crash_inside_run_marks_failed_not_answered(self):
        import pv.agents.pipeline as P
        orig = P.run_case; P.run_case = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("kaboom"))
        try: rid = self.s.submit(clean(), "c1"); self.drain()
        finally: P.run_case = orig
        rec = self.s.get_run(rid); self.assertEqual(rec["status"], "failed"); self.assertNotIn("result", rec)

    def test_stats_cost_per_100_runs(self):
        self.setenv(RUN_CACHE_ENABLED="false")
        for _ in range(3): self.s.submit(clean(), "c1"); self.drain(fake(good_script))
        st = self.s.stats(); self.assertEqual(st["runs"], 3); self.assertGreater(st["cost"]["per_100_runs_usd"], 0); self.assertIn(st["cost"]["largest_driver"], st["cost"]["per_step_usd"])

