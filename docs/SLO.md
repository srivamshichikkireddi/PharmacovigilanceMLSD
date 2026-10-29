# Service level objective (D6)

> **Declared before any load measurement.** Commit this file *before* running Locust; the git timestamp is the evidence. A target set after seeing results does not count.

## The objective

> **p95 end-to-end latency of a complete run, measured from `POST /reports` to the run reaching a terminal state, is <= 30 s, while the system sustains >= 1.0 completed runs/s with 3 workers and 0 failed runs.**
> Measurement conditions: LLM replaced by the stub (`loadtest/stub_llm.py`, 1.2 s per call, +/-15 % jitter), run cache off, 3 `agent-worker` replicas x 4 concurrent slots.

## Why this number (justified against section 1)

* The regulatory clocks are in **days** (15 calendar days for serious events, 90 for non-serious). Latency of seconds cannot cause a missed deadline; what matters is that the system never silently drops or delays a case. That is the *availability/no-loss* half of the objective (0 failed runs; at-least-once queue).
* The latency target therefore protects a **human**: the qualified person triaging a case should have the recommendation while still on that case. Interactive tolerance is tens of seconds; beyond ~30 s people switch tasks, and the 15-day clock starts at *receipt*, so every wasted hour is real. 30 s at the 95th percentile keeps nearly every case within one sitting.
* Throughput 1.0 runs/s ~ 86,000 cases/day, orders of magnitude above realistic intake for one MAH. The floor is to ensure a **burst** (e.g. a batch import or one client looping) does not queue the QPPV's own cases for minutes.

## Predictions made before measuring (so the test can falsify them)

Per run: 1 normalise call + about 5 action calls + 1 summary call ~ 7 LLM calls x 1.2 s ~ **8-9 s unloaded**.
Capacity = slots / service time = (3 workers x 4 slots) / 8.4 s ~ **1.4 runs/s**.
By Little's law the knee is at ~ rate x latency ~ 1.4 x 8.4 ~ **12 runs in flight**. I therefore predict:

1. With 1 worker the SLO breaks at ~4-6 concurrent users (4 slots), throughput ~0.45 runs/s.
2. With 3 workers the SLO holds up to ~12 concurrent users, then p95 grows roughly linearly with users (queueing), throughput plateaus ~1.4 runs/s.
3. The component responsible is **worker slot capacity** (concurrency x LLM latency), not Redis, the API or TF-IDF retrieval (engine-only time measured offline: median 27 ms, p95 92 ms per run without an LLM). If `docker stats` shows worker CPU near 100 % before slots saturate, the bottleneck is CPU/GIL instead and more *processes*, not more threads, are the remedy.

## How to report (fill in after measuring; see docs/REPORT.md)
`python loadtest/analyze.py loadtest/results/w1 loadtest/results/w3` prints throughput, p50, p95, SLO verdict, and the user count where the rolling p95 first exceeds the target.
