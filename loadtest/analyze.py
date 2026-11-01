#!/usr/bin/env python3
"""D6: compares Locust results with the declared SLO (docs/SLO.md) and finds the concurrency where it breaks.
   python loadtest/analyze.py loadtest/results/w1 loadtest/results/w3
Needs the CSVs from:  --csv <prefix> --csv-full-history"""
import csv, sys, os
SLO_P95_S, SLO_MIN_RPS = float(os.environ.get("SLO_P95_S", 30)), float(os.environ.get("SLO_MIN_RPS", 1.0))
CACHE_ENABLED = os.environ.get("LOADTEST_RUN_CACHE_ENABLED", "false").lower() in {"1", "true", "yes"}


def read(path):
    with open(path) as f: return list(csv.DictReader(f))


for prefix in sys.argv[1:]:
    stats = [r for r in read(prefix + "_stats.csv") if r["Name"] == "E2E run"]
    if not stats: print(prefix, ": no 'E2E run' row"); continue
    s = stats[0]; p50, p95, rps, fails = float(s["50%"]) / 1000, float(s["95%"]) / 1000, float(s["Requests/s"]), int(s["Failure Count"])
    print(f"\n== {prefix}\n   runs={s['Request Count']} failures={fails}  throughput={rps:.2f} runs/s  p50={p50:.3f}s  p95={p95:.3f}s")
    if CACHE_ENABLED:
        print("   Declared SLO: NOT APPLICABLE (the declared measurement requires run cache off)")
    else:
        p95_met, rps_met, failures_met = p95 <= SLO_P95_S, rps >= SLO_MIN_RPS, fails == 0
        print(f"   Overall SLO (p95 <= {SLO_P95_S}s, throughput >= {SLO_MIN_RPS} runs/s, zero failures): "
              f"{'MET' if p95_met and rps_met and failures_met else 'NOT MET'} "
              f"(p95={'MET' if p95_met else 'NOT MET'}, throughput={'MET' if rps_met else 'NOT MET'}, "
              f"failures={'MET' if failures_met else 'NOT MET'})")
    h = [r for r in read(prefix + "_stats_history.csv") if r.get("Name") == "E2E run" and r["95%"] not in ("N/A", "")]
    broke = next((r for r in h if float(r["95%"]) / 1000 > SLO_P95_S), None)
    if broke: print(f"   Rolling p95 first exceeds {SLO_P95_S}s at {broke['User Count']} concurrent users "
                     f"({float(broke['95%'])/1000:.3f}s, {broke['Requests/s']} runs/s)")
    else: print("   Rolling p95 never exceeded the target in this ramp")
    by_users = {}
    for r in h: by_users.setdefault(int(r["User Count"]), []).append(float(r["95%"]) / 1000)
    print("   users -> median rolling p95 (s):", {u: round(sorted(v)[len(v)//2], 3) for u, v in sorted(by_users.items())})
