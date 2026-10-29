from tests.common import *
from pv.agents.state import RunContext
from pv.llm.client import LLMUnavailable


class Clock:
    t = 0.0
    def __call__(self): return self.t


class Breaker(unittest.TestCase):
    def test_open_then_half_open_then_close(self):
        c = Clock(); b = CircuitBreaker(3, 60, 20, clock=c)
        for _ in range(3): b.failure()
        self.assertEqual(b.state, "open"); self.assertFalse(b.allow())
        c.t = 21; self.assertTrue(b.allow()); self.assertEqual(b.state, "half_open"); self.assertFalse(b.allow())   # one probe only
        b.success(); self.assertEqual(b.state, "closed")

    def test_half_open_failure_reopens(self):
        c = Clock(); b = CircuitBreaker(2, 60, 10, clock=c)
        b.failure(); b.failure(); c.t = 11; self.assertTrue(b.allow()); b.failure()
        self.assertEqual(b.state, "open"); self.assertFalse(b.allow())

    def test_old_failures_age_out(self):
        c = Clock(); b = CircuitBreaker(3, 10, 5, clock=c)
        b.failure(); b.failure(); c.t = 30; b.failure(); self.assertEqual(b.state, "closed")


class Resilient(Env):
    def ctx(self, llm): return RunContext("t", {}, INDEX, llm, settings, {})

    def test_exponential_backoff_and_bounded_retries(self):
        self.setenv(LLM_MAX_RETRIES=2, BREAKER_FAIL_THRESHOLD=99)
        sleeps = []
        def boom(m): raise TimeoutError("slow")
        llm = ResilientLLM(FakeLLM(boom), settings, sleep=sleeps.append)
        ctx = self.ctx(llm)
        with self.assertRaises(LLMUnavailable): llm.call(ctx, "s", "p", "v", [{"role": "user", "content": "x"}])
        self.assertEqual(llm.client.calls, 3)                       # 1 try + 2 retries, no more
        self.assertEqual(len(sleeps), 2); self.assertGreater(sleeps[1], sleeps[0] - 0.3)   # backoff grows (jitter <= 0.25)

    def test_breaker_opens_and_fast_fails(self):
        self.setenv(LLM_MAX_RETRIES=0, BREAKER_FAIL_THRESHOLD=2)
        def boom(m): raise ConnectionError("down")
        llm = ResilientLLM(FakeLLM(boom), settings, sleep=lambda s: None); ctx = self.ctx(llm)
        for _ in range(2):
            with self.assertRaises(LLMUnavailable): llm.call(ctx, "s", "p", "v", [{"role": "user", "content": "x"}])
        self.assertEqual(llm.breaker.state, "open"); calls = llm.client.calls
        with self.assertRaises(LLMUnavailable) as cm: llm.call(ctx, "s", "p", "v", [{"role": "user", "content": "x"}])
        self.assertEqual(llm.client.calls, calls); self.assertIn("circuit_open", str(cm.exception))   # provider not touched

    def test_cost_attributed_per_step(self):
        llm = fake(lambda m: '{"terms":[]}'); ctx = self.ctx(llm)
        llm.call(ctx, "normalise_terms", "p", "v1", [{"role": "user", "content": "x" * 400}])
        s = ctx.cost_summary(); self.assertIn("normalise_terms", s["per_step"]); self.assertGreater(s["total_usd"], 0)
