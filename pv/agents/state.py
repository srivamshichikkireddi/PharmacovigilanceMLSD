"""RunContext: everything one run accumulates, including the full reconstructible trace (D5)."""
import time
from collections import defaultdict


class BudgetExceeded(Exception):
    def __init__(self, kind): super().__init__(kind); self.kind = kind


class RunContext:
    def __init__(self, run_id, report, index, llm, settings, drugs, strategy=0):
        self.run_id, self.report, self.index, self.llm, self.s, self.drugs = run_id, report, index, llm, settings, drugs
        self.t0 = time.monotonic()
        self.deadline = self.t0 + settings.run_timeout_s
        self.trace, self.evidence, self.degraded = [], {}, []
        self.steps = self.tool_calls = 0
        self.cost = defaultdict(float)
        self.tokens = defaultdict(lambda: [0, 0])
        self.prompt_versions = {}
        self.searched_label_terms, self.terms, self.llm_terms = set(), [], []
        self.ents, self.early_reasons, self.reasons = {}, [], []
        self.determination, self.verification, self.summary = None, None, None
        self.strategy, self.attempts, self.agent_outcomes = strategy, 0, {}
        self.label_stage, self.derived_done, self.seen_calls = 0, False, set()
        self.step_name = "-"
        self.need_attempts, self.derived_query, self.budget_exhausted, self.graph_steps = {}, None, None, 0
        self.mode = "llm_assisted" if llm.client.name != "none" else "rules_only"

    def reset_attempt(self):
        """New retrieval strategy: keep what was read (evidence is still valid) but redo the plan."""
        self.need_attempts, self.label_stage, self.derived_done = {}, 0, False
        self.determination, self.verification = None, None

    # ---- budget (A1): steps, tool calls, wall clock ----
    def time_left(self): return self.deadline - time.monotonic()

    def check_budget(self, kind="step"):
        if self.time_left() <= 0: raise BudgetExceeded("wall_clock")
        if kind == "step" and self.steps >= self.s.max_steps: raise BudgetExceeded("max_steps")
        if kind == "tool" and self.tool_calls >= self.s.max_tool_calls: raise BudgetExceeded("max_tool_calls")

    def emit(self, type_, **kw):
        self.trace.append({"t_ms": round((time.monotonic() - self.t0) * 1000), "type": type_, "agent_step": self.step_name, **kw})

    def add_cost(self, step, pt, ct, usd):
        self.cost[step] += usd
        self.tokens[step][0] += pt
        self.tokens[step][1] += ct

    def degrade(self, reason):
        if reason not in self.degraded:
            self.degraded.append(reason)
            self.emit("degraded", reason=reason)

    def cost_summary(self):
        return {"total_usd": round(sum(self.cost.values()), 8),
                "prompt_tokens": sum(v[0] for v in self.tokens.values()),
                "completion_tokens": sum(v[1] for v in self.tokens.values()),
                "per_step": {k: {"usd": round(v, 8), "prompt_tokens": self.tokens[k][0], "completion_tokens": self.tokens[k][1]}
                             for k, v in self.cost.items()}}
