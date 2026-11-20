#!/usr/bin/env python3
"""Given a pose CSV and the stopAllMotion ACK wall time, report speed before/after.
Usage: tools_halt_check.py poses.csv <stop_ack_epoch>"""
import csv, sys
rows = [(float(r["wall_time"]), float(r["x"]), float(r["y"]), float(r["z"]))
        for r in csv.DictReader(open(sys.argv[1]))]
t0 = float(sys.argv[2])
def speed(a, b):
    w = [r for r in rows if a <= r[0] <= b]
    if len(w) < 2: return float("nan"), 0.0
    d = sum((w[-1][i] - w[0][i]) ** 2 for i in (1, 2, 3)) ** 0.5
    return d / (w[-1][0] - w[0][0]), d
for label, a, b in [("2s before stop ack", t0 - 2, t0), ("0-2s after", t0, t0 + 2),
                    ("2-5s after", t0 + 2, t0 + 5), ("5-10s after", t0 + 5, t0 + 10)]:
    v, d = speed(a, b)
    print("%-20s speed %.3f m/s  displacement %.3f m" % (label, v, d))
