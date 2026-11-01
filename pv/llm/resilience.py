"""A5: timeouts on every call, bounded retry with exponential backoff, a circuit breaker on the
provider, and a defined degradation path (callers catch LLMUnavailable and use the rule policy)."""
import random, time
from collections import deque

from pv import metrics
from pv.llm.client import LLMQuotaExceeded, LLMUnavailable, is_provider_limit_error

CLOSED, HALF_OPEN, OPEN = "closed", "half_open", "open"


class CircuitBreaker:
    """closed --(N failures within window)--> open --(cooldown)--> half_open --(1 probe)--> closed|open"""

    def __init__(self, threshold=3, window_s=60, cooldown_s=20, clock=time.monotonic):
        self.threshold, self.window, self.cooldown, self.clock = threshold, window_s, cooldown_s, clock
        self.failures, self.opened_at, self.state = deque(), None, CLOSED
        self._probe_out = False

    def _tick(self):
        if self.state == OPEN and self.clock() - self.opened_at >= self.cooldown:
            self.state, self._probe_out = HALF_OPEN, False
        metrics.BREAKER_STATE.set({CLOSED: 0, HALF_OPEN: 1, OPEN: 2}[self.state])

    def allow(self):
        self._tick()
        if self.state == CLOSED:
            return True
        if self.state == HALF_OPEN and not self._probe_out:
            self._probe_out = True
            return True
        return False

    def success(self):
        self.failures.clear()
        self.state, self.opened_at = CLOSED, None
        self._tick()

    def failure(self):
        now = self.clock()
        if self.state == HALF_OPEN:
            self.state, self.opened_at = OPEN, now
        else:
            self.failures.append(now)
            while self.failures and now - self.failures[0] > self.window:
                self.failures.popleft()
            if len(self.failures) >= self.threshold:
                self.state, self.opened_at = OPEN, now
        self._tick()

    def open(self):
        self.state, self.opened_at, self._probe_out = OPEN, self.clock(), False
        metrics.BREAKER_STATE.set(2)


def price(settings, pt, ct):
    return round(pt * settings.price_in_per_1m / 1e6 + ct * settings.price_out_per_1m / 1e6, 8)


class ResilientLLM:
    def __init__(self, client, settings, breaker=None, sleep=time.sleep):
        self.client, self.s, self.sleep = client, settings, sleep
        self.breaker = breaker or CircuitBreaker(settings.breaker_threshold, settings.breaker_window_s, settings.breaker_cooldown_s)

    def call(self, ctx, step, prompt_name, prompt_version, messages):
        """Returns text. Raises LLMUnavailable (caller degrades). Records a trace event either way."""
        t0 = time.monotonic()
        last = None
        quota_exceeded = False
        attempts = 0
        for attempt in range(self.s.llm_max_retries + 1):
            if not self.breaker.allow():
                last = "circuit_open"
                break
            left = ctx.time_left()
            if left <= 0.5:
                last = "run_budget_exhausted"
                break
            attempts += 1
            try:
                res = self.client.chat(messages, timeout=min(self.s.llm_timeout_s, left))
                self.breaker.success()
                lat = time.monotonic() - t0
                cost = price(self.s, res.prompt_tokens, res.completion_tokens)
                ctx.add_cost(step, res.prompt_tokens, res.completion_tokens, cost)
                agent = ctx.step_name
                metrics.TOKENS.labels(agent, step, "prompt").inc(res.prompt_tokens)
                metrics.TOKENS.labels(agent, step, "completion").inc(res.completion_tokens)
                metrics.COST.labels(agent, step).inc(cost)
                metrics.LLM_SECONDS.labels(step).observe(lat)
                if res.finish_reason == "length":
                    metrics.LLM_CALLS.labels(step, "truncated").inc()
                    ctx.emit("llm_call", step=step, prompt=prompt_name, prompt_version=prompt_version,
                             outcome="truncated", attempts=attempts, latency_ms=round(lat * 1000),
                             prompt_tokens=res.prompt_tokens, completion_tokens=res.completion_tokens,
                             cost_usd=cost, breaker=self.breaker.state, finish_reason=res.finish_reason,
                             response_preview=res.text[:160])
                    last = "truncated_completion"
                    break
                metrics.LLM_CALLS.labels(step, "ok").inc()
                ctx.emit("llm_call", step=step, prompt=prompt_name, prompt_version=prompt_version, outcome="ok",
                         attempts=attempts, latency_ms=round(lat * 1000), prompt_tokens=res.prompt_tokens,
                         completion_tokens=res.completion_tokens, cost_usd=cost, breaker=self.breaker.state,
                         finish_reason=res.finish_reason, response_preview=res.text[:160])
                return res.text
            except LLMUnavailable as exc:
                last = str(exc)
                break
            except Exception as exc:  # timeout / 5xx / network
                if is_provider_limit_error(exc):
                    last, quota_exceeded = "provider_rate_limit_or_quota", True
                    self.breaker.open()
                    metrics.LLM_CALLS.labels(step, "error").inc()
                    break
                last = f"{type(exc).__name__}: {exc}"[:160]
                self.breaker.failure()
                metrics.LLM_CALLS.labels(step, "error").inc()
                if attempt < self.s.llm_max_retries and self.breaker.state != OPEN:
                    metrics.RETRIES.labels("llm").inc()
                    self.sleep(min(8.0, 0.5 * 2 ** attempt) + random.uniform(0, 0.25))   # exp. backoff + jitter
        if last == "truncated_completion":
            raise LLMUnavailable(last)
        metrics.LLM_CALLS.labels(step, "unavailable").inc()
        ctx.emit("llm_call", step=step, prompt=prompt_name, prompt_version=prompt_version, outcome="unavailable",
                 attempts=attempts, latency_ms=round((time.monotonic() - t0) * 1000), error=last, breaker=self.breaker.state)
        if quota_exceeded:
            raise LLMQuotaExceeded("provider rate limit or quota exhausted")
        raise LLMUnavailable(last or "unavailable")
