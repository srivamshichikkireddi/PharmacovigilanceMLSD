#!/usr/bin/env python3
"""D7: measured effect of the whole-run cache on p95 and cost per run. Needs a running stack.
Run once with the stub overlay OR a real provider so that a miss actually costs something."""
import json, statistics, sys, time, urllib.request
sys.path.insert(0, "scripts")
from demo import API, CASES, http

N = int(sys.argv[1]) if len(sys.argv) > 1 else 20


def batch(nocache):
    lat, cost = [], []
    for i in range(N):
        st, j = http("POST", f"{API}/reports" + ("?nocache=true" if nocache else ""), CASES["case41"], {"X-Client-Id": f"cache-{nocache}-{i}"})
        t0 = time.time()
        while True:
            _, rec = http("GET", f"{API}/runs/{j['run_id']}")
            if rec["status"] in ("done", "failed"): break
            time.sleep(0.2)
        r = rec["result"]; lat.append(r["end_to_end_seconds"]); cost.append(r["cost"]["total_usd"])
    lat.sort(); return {"p50": round(statistics.median(lat), 2), "p95": round(lat[int(0.95 * (len(lat) - 1))], 2), "mean_cost_usd": round(statistics.mean(cost), 6)}


http("POST", f"{API}/reports", CASES["case41"], {"X-Client-Id": "warm"})   # prime
time.sleep(5)
print("cache OFF (?nocache=true):", off := batch(True)); print("cache ON:               ", on := batch(False))
print(f"p95 {off['p95']}s -> {on['p95']}s ; cost/run ${off['mean_cost_usd']} -> ${on['mean_cost_usd']}")
