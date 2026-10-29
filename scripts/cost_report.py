#!/usr/bin/env python3
"""L5: cost per 100 runs and the largest single cost driver, from the live service."""
import json, urllib.request
s = json.load(urllib.request.urlopen("http://localhost:8000/ops/summary"))
c = s["cost"]
print(f"runs: {s['runs']}   e2e p50/p95: {s['e2e_seconds']['p50']}s / {s['e2e_seconds']['p95']}s")
print(f"cost per 100 runs: ${c['per_100_runs_usd']}   (total ${c['total_usd']})")
for k, v in sorted(c["per_step_usd"].items(), key=lambda kv: -kv[1]): print(f"  {k:20s} ${v:.6f}")
print("largest single cost driver:", c["largest_driver"])
