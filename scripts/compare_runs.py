#!/usr/bin/env python3
"""Compare two evaluation result files:  python scripts/compare_runs.py <before_label> <after_label>"""
import json, sys
a, b = [json.load(open(f"eval/results/{x}.json")) for x in sys.argv[1:3]]
print(f"{'metric':28}{sys.argv[1]:>16}{sys.argv[2]:>16}{'delta':>10}")
for k in a["aggregate"]:
    x, y = a["aggregate"][k], b["aggregate"][k]
    d = f"{y - x:+.3f}" if isinstance(x, (int, float)) and isinstance(y, (int, float)) else ""
    print(f"{k:28}{str(x):>16}{str(y):>16}{d:>10}")
