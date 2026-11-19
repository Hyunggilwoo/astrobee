"""Every flyable pose must keep Astrobee's whole body (0.32 m cube, +margin)
inside the union of keep-in boxes and outside every keep-out box, using the
zones dumped from the sim (tools_dump_zones.py -> zones_iss.json)."""
import itertools
import json
import os

import pytest

from intent_parser import load_locations

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCS = load_locations(os.path.join(HERE, "locations.yaml"))
ZONES = json.load(open(os.path.join(HERE, "zones_iss.json")))["zones"]
HALF = 0.16 + 0.10   # half body size + 10 cm margin


def box(z):
    lo = [min(a, b) for a, b in zip(z["min"], z["max"])]
    hi = [max(a, b) for a, b in zip(z["min"], z["max"])]
    return lo, hi


KEEPIN = [box(z) for z in ZONES if z["type"] == "keepin"]
KEEPOUT = [box(z) for z in ZONES if z["type"] == "keepout"]


def inside(p, b):
    return all(lo <= v <= hi for v, lo, hi in zip(p, *b))


def corners(p):
    return [[c + d for c, d in zip(p, s)] for s in itertools.product((-HALF, HALF), repeat=3)]


FLYABLE = [n for n, l in LOCS.items() if l.get("goto_allowed", True) and not l.get("todo")]


def test_zone_file_has_both_kinds():
    assert KEEPIN and KEEPOUT


@pytest.mark.parametrize("name", FLYABLE)
def test_pose_in_keepin_and_clear_of_keepouts(name):
    p = LOCS[name]["pos"]
    for c in corners(p):
        assert any(inside(c, b) for b in KEEPIN), "%s corner %s outside keep-in" % (name, c)
    for lo, hi in KEEPOUT:
        overlap = all(p[i] + HALF > lo[i] and p[i] - HALF < hi[i] for i in range(3))
        assert not overlap, "%s body overlaps keep-out %s..%s" % (name, lo, hi)


@pytest.mark.parametrize("name", [n for n in FLYABLE if "recorded" in LOCS[n]])
def test_recorded_pose_close_to_target(name):
    loc = LOCS[name]
    err = sum((a - b) ** 2 for a, b in zip(loc["pos"], loc["recorded"][:3])) ** 0.5
    assert err < 0.05, "%s arrived %.3f m from target" % (name, err)
