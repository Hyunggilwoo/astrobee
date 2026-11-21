"""Keep-in and keep-out checks for a point-and-fly target.

The boxes are the sim zones in zones_iss.json. A pose is clear when the whole
body cube, plus a margin, sits inside some keep-in and outside every keep-out.
That is the same rule as tests/test_zones.py (0.32 m body, 10 cm margin).
"""
from __future__ import annotations

import json
import math
import os
from typing import List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ZONES = os.path.join(HERE, "zones_iss.json")

BODY_HALF = 0.16
MARGIN = 0.10
CLEARANCE = BODY_HALF + MARGIN  # 0.26 m

Box = Tuple[List[float], List[float]]


def _box(raw: dict) -> Box:
    lo = [min(a, b) for a, b in zip(raw["min"], raw["max"])]
    hi = [max(a, b) for a, b in zip(raw["min"], raw["max"])]
    return lo, hi


def load_zones(path: str = DEFAULT_ZONES):
    with open(path) as handle:
        zones = json.load(handle)["zones"]
    keepin = [_box(z) for z in zones if z["type"] == "keepin"]
    keepout = [_box(z) for z in zones if z["type"] == "keepout"]
    return keepin, keepout


def inside(point: Sequence[float], box: Box, inset: float = 0.0) -> bool:
    lo, hi = box
    return all(lo[i] + inset <= point[i] <= hi[i] - inset for i in range(3))


def overlaps(center: Sequence[float], half: float, box: Box) -> bool:
    lo, hi = box
    return all(center[i] + half > lo[i] and center[i] - half < hi[i] for i in range(3))


def corners(point: Sequence[float], half: float = CLEARANCE):
    import itertools

    return [
        [point[i] + sign[i] for i in range(3)]
        for sign in itertools.product((-half, half), repeat=3)
    ]


def body_clear(
    point: Sequence[float],
    keepin: Sequence[Box],
    keepout: Sequence[Box],
    clearance: float = CLEARANCE,
) -> bool:
    for corner in corners(point, clearance):
        if not any(inside(corner, box) for box in keepin):
            return False
    for box in keepout:
        if overlaps(point, clearance, box):
            return False
    return True


def _dist(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def clamp_point(
    point: Sequence[float],
    keepin: Sequence[Box],
    keepout: Sequence[Box],
    max_shift: float = 0.35,
    clearance: float = CLEARANCE,
) -> Optional[List[float]]:
    """Pull a near-miss back inside the flight volume.

    A point whose body overlaps a keep-out is rejected (None). It is not
    slid around the obstacle. A point just outside a keep-in, or closer to
    the wall than the body margin, is projected onto the inset box when that
    move is within max_shift metres.
    """
    raw = [float(v) for v in point]
    if any(overlaps(raw, clearance, box) for box in keepout):
        return None
    if body_clear(raw, keepin, keepout, clearance):
        return raw
    best = None
    best_d = max_shift
    for box in keepin:
        projected = [
            min(max(raw[i], box[0][i] + clearance), box[1][i] - clearance)
            for i in range(3)
        ]
        if any(overlaps(projected, clearance, ko) for ko in keepout):
            continue
        if not body_clear(projected, keepin, keepout, clearance):
            continue
        distance = _dist(raw, projected)
        if distance <= best_d:
            best = projected
            best_d = distance
    return best


def segment_clear(
    start: Sequence[float],
    goal: Sequence[float],
    keepin: Sequence[Box],
    keepout: Sequence[Box],
    step: float = 0.2,
    clearance: float = CLEARANCE,
) -> bool:
    length = _dist(start, goal)
    count = max(1, int(math.ceil(length / step)))
    for index in range(count + 1):
        t = index / count
        point = [start[i] + (goal[i] - start[i]) * t for i in range(3)]
        if not body_clear(point, keepin, keepout, clearance):
            return False
    return True


def plan_path(
    start: Sequence[float],
    goal: Sequence[float],
    keepin: Sequence[Box],
    keepout: Sequence[Box],
    resolution: float = 0.45,
    clearance: float = CLEARANCE,
) -> Optional[List[List[float]]]:
    """Waypoints from start to goal that stay in the flight volume.

    A clear straight line is one segment. Otherwise a coarse 6-connected
    search runs inside the keep-in boxes, then extra samples that can see
    each other are dropped. Returns None when no safe path exists.
    The start pose is included so the caller can draw it. The robot is
    already there, so the sequencer skips it.
    """
    start = [float(v) for v in start]
    goal = [float(v) for v in goal]
    if not body_clear(goal, keepin, keepout, clearance):
        return None
    if not body_clear(start, keepin, keepout, clearance):
        return None
    if segment_clear(start, goal, keepin, keepout, clearance=clearance):
        return [start, goal]

    lo = [min(box[0][i] + clearance for box in keepin) for i in range(3)]
    hi = [max(box[1][i] - clearance for box in keepin) for i in range(3)]

    def key(point):
        return tuple(int(round((point[i] - lo[i]) / resolution)) for i in range(3))

    def at(cell):
        return [lo[i] + cell[i] * resolution for i in range(3)]

    start_cell = key(start)
    goal_cell = key(goal)
    blocked = set()

    def walkable(cell):
        if cell in blocked:
            return False
        point = at(cell)
        ok = body_clear(point, keepin, keepout, clearance)
        if not ok:
            blocked.add(cell)
        return ok

    if not walkable(goal_cell):
        # Snap the goal cell to the goal itself if the grid point is blocked.
        goal_cell = key(goal)
    import heapq

    open_heap = [(0.0, start_cell)]
    came = {start_cell: None}
    cost = {start_cell: 0.0}
    found = None
    expansions = 0
    while open_heap and expansions < 8000:
        _score, cell = heapq.heappop(open_heap)
        expansions += 1
        if cell == goal_cell or _dist(at(cell), goal) <= resolution:
            found = cell
            break
        for axis in range(3):
            for sign in (-1, 1):
                nxt = list(cell)
                nxt[axis] += sign
                nxt_t = tuple(nxt)
                if nxt_t in came:
                    continue
                if any(nxt[i] < 0 or at(nxt)[i] > hi[i] + resolution for i in range(3)):
                    continue
                if not walkable(nxt_t):
                    continue
                step_cost = cost[cell] + resolution
                if step_cost >= cost.get(nxt_t, 1e9):
                    continue
                came[nxt_t] = cell
                cost[nxt_t] = step_cost
                heuristic = _dist(at(nxt_t), goal)
                heapq.heappush(open_heap, (step_cost + heuristic, nxt_t))
    if found is None:
        return None
    cells = []
    cursor = found
    while cursor is not None:
        cells.append(cursor)
        cursor = came[cursor]
    cells.reverse()
    points = [start] + [at(cell) for cell in cells[1:]] + [goal]
    return _shortcut(points, keepin, keepout, clearance)


def _shortcut(points, keepin, keepout, clearance):
    if len(points) < 3:
        return points
    kept = [points[0]]
    anchor = 0
    while anchor < len(points) - 1:
        nxt = len(points) - 1
        while nxt > anchor + 1 and not segment_clear(
            points[anchor], points[nxt], keepin, keepout, clearance=clearance
        ):
            nxt -= 1
        kept.append(points[nxt])
        anchor = nxt
    return kept
