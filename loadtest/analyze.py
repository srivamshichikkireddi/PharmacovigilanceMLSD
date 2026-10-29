#!/usr/bin/env python3
"""D6: compares Locust results with the declared SLO (docs/SLO.md) and finds the concurrency where it breaks.
   python loadtest/analyze.py loadtest/results/w1 loadtest/results/w3
Needs the CSVs from:  --csv <prefix> --csv-full-history"""
import csv, sys, os
SLO_P95_S, SLO_MIN_RPS = float(os.environ.get("SLO_P95_S", 30)), float(os.environ.get("SLO_MIN_RPS", 1.0))


def read(path):
    with open(path) as f: return list(csv.DictReader(f))


for prefix in sys.argv[1:]:
    stats = [r for r in read(prefix + "_stats.csv") if r["Name"] == "E2E run"]
    if not stats: print(prefix, ": no 'E2E run' row"); continue
    s = stats[0]; p50, p95, rps, fails = float(s["50%"]) / 1000, float(s["95%"]) / 1000, float(s["Requests/s"]), int(s["Failure Count"])
    print(f"\n== {prefix}\n   runs={s['Request Count']} failures={fails}  throughput={rps:.2f} runs/s  p50={p50:.1f}s  p95={p95:.1f}s")
    print(f"   SLO p95 <= {SLO_P95_S}s: {'MET' if p95 <= SLO_P95_S else 'NOT MET'} (overall)")
    h = [r for r in read(prefix + "_stats_history.csv") if r.get("Name") == "E2E run" and r["95%"] not in ("N/A", "")]
    broke = next((r for r in h if float(r["95%"]) / 1000 > SLO_P95_S), None)
    if broke: print(f"   SLO first broken at {broke['User Count']} concurrent users (rolling p95 {float(broke['95%'])/1000:.1f}s, {broke['Requests/s']} runs/s)")
    else: print("   SLO never broken in this ramp: raise the top stage in loadtest/locustfile.py")
    by_users = {}
    for r in h: by_users.setdefault(int(r["User Count"]), []).append(float(r["95%"]) / 1000)
    print("   users -> median rolling p95:", {u: round(sorted(v)[len(v)//2], 1) for u, v in sorted(by_users.items())})
