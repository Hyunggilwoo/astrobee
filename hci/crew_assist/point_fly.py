"""Turn a phone tap into a confirmed flight path.

Two inputs:
  * a fraction on the nav-camera image, projected with the sim camera model
  * a fraction on the JEM top-down map

Neither path moves the robot. The phone stores the plan and waits for Confirm.
"""
from __future__ import annotations

import math
from typing import List, Sequence, Tuple

import zones as zone_mod

# Sim nav cam. description/.../sensor_nav_cam.urdf.xacro is 320x240 with this
# horizontal fov. FillCameraInfo in simulation/src/astrobee_gazebo.cc builds K
# as fx = fy = width / (2 tan(hfov/2)), principal point at the center.
# The flight intrinsic_matrix in sim.config is for the 1280-wide sensor, so it
# is not used on the 320x240 Gazebo image.
NAV_WIDTH = 320
NAV_HEIGHT = 240
NAV_HFOV = 2.957216683909311

# astrobee/config/robots/sim.config nav_cam_transform, body frame.
# quat (0.5, 0.5, 0.5, 0.5) sends camera +Z to body +X. Pixels use the ROS
# optical axes: +u right, +v down, so body +Y (left) is image left and body
# +Z (up) is image up.
CAM_OFFSET = (0.1177, -0.0422, -0.0826)
STANDOFF_M = 0.55
MIN_HIT_M = 0.30
# JEM keep-in is index 6 in zones_iss.json.
JEM_INDEX = 6


class PlanError(ValueError):
    pass


def nav_intrinsics(width: int = NAV_WIDTH, height: int = NAV_HEIGHT):
    focal = width / (2.0 * math.tan(NAV_HFOV / 2.0))
    return focal, focal, width / 2.0, height / 2.0


def quat_to_matrix(quat: Sequence[float]):
    """Body-from-world? Quaternion rotates body vectors into world (xyzw)."""
    x, y, z, w = quat
    return (
        (1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
        (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
        (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)),
    )


def rotate(matrix, vector):
    return [
        sum(matrix[row][col] * vector[col] for col in range(3)) for row in range(3)
    ]


def body_ray(u: float, v: float, width: int, height: int) -> List[float]:
    """Unit ray in the body frame for a pixel. +u right, +v down."""
    fx, fy, cx, cy = nav_intrinsics(width, height)
    right = (u - cx) / fx
    down = (v - cy) / fy
    forward = 1.0
    body = (forward, -right, -down)
    norm = math.sqrt(sum(c * c for c in body))
    return [c / norm for c in body]


def level_quat(dx: float, dy: float) -> Tuple[float, float, float, float]:
    if dx * dx + dy * dy < 1e-8:
        return (0.0, 0.0, 0.0, 1.0)
    yaw = math.atan2(dy, dx)
    return (0.0, 0.0, math.sin(yaw / 2.0), math.cos(yaw / 2.0))


def jem_box(keepin):
    return keepin[JEM_INDEX]


def jem_overlay(keepin=None, keepout=None) -> dict:
    """Keep-out rectangles as fractions of the JEM top-down map."""
    if keepin is None or keepout is None:
        keepin, keepout = zone_mod.load_zones()
    lo, hi = jem_box(keepin)
    span_x = hi[0] - lo[0]
    span_y = hi[1] - lo[1]
    cruise_z = 0.5 * (lo[2] + hi[2])
    marks = []
    for box in keepout:
        blo, bhi = box
        if bhi[2] < cruise_z - zone_mod.CLEARANCE or blo[2] > cruise_z + zone_mod.CLEARANCE:
            continue
        x0 = max(blo[0], lo[0])
        x1 = min(bhi[0], hi[0])
        y0 = max(blo[1], lo[1])
        y1 = min(bhi[1], hi[1])
        if x1 <= x0 or y1 <= y0:
            continue
        marks.append(
            {
                "u": (x0 - lo[0]) / span_x,
                "v": (hi[1] - y1) / span_y,
                "w": (x1 - x0) / span_x,
                "h": (y1 - y0) / span_y,
            }
        )
    return {
        "module": "JEM",
        "min": lo,
        "max": hi,
        "keepouts": marks,
    }


def map_point(u: float, v: float, keepin) -> List[float]:
    """u,v are fractions of the JEM top-down drawing. v grows downward."""
    if not 0.0 <= u <= 1.0 or not 0.0 <= v <= 1.0:
        raise PlanError("tap is outside the module map")
    lo, hi = jem_box(keepin)
    x = lo[0] + u * (hi[0] - lo[0])
    y = hi[1] - v * (hi[1] - lo[1])
    z = 0.5 * (lo[2] + hi[2])
    return [x, y, z]


def _ray_box(origin, direction, box, t_min: float):
    lo, hi = box
    near = t_min
    far = 1e9
    for axis in range(3):
        if abs(direction[axis]) < 1e-9:
            if origin[axis] < lo[axis] or origin[axis] > hi[axis]:
                return None
            continue
        inv = 1.0 / direction[axis]
        t1 = (lo[axis] - origin[axis]) * inv
        t2 = (hi[axis] - origin[axis]) * inv
        if t1 > t2:
            t1, t2 = t2, t1
        near = max(near, t1)
        far = min(far, t2)
        if near > far:
            return None
    # Inside a box, `near` is behind the origin and `far` is the exit wall.
    if far < t_min:
        return None
    if near >= t_min:
        return near
    return far


def camera_target(u_frac: float, v_frac: float, pose, keepin, keepout) -> dict:
    if pose is None:
        raise PlanError("no pose yet; the camera tap needs the robot pose")
    if not 0.0 <= u_frac <= 1.0 or not 0.0 <= v_frac <= 1.0:
        raise PlanError("tap is outside the camera image")
    pos, quat = pose
    width_px = u_frac * (NAV_WIDTH - 1)
    height_px = v_frac * (NAV_HEIGHT - 1)
    direction_body = body_ray(width_px, height_px, NAV_WIDTH, NAV_HEIGHT)
    matrix = quat_to_matrix(quat)
    direction = rotate(matrix, direction_body)
    origin = [
        pos[i] + rotate(matrix, CAM_OFFSET)[i] for i in range(3)
    ]
    best = None
    kind = None
    for box in keepout:
        hit = _ray_box(origin, direction, box, MIN_HIT_M)
        if hit is not None and (best is None or hit < best):
            best = hit
            kind = "keepout"
    for box in keepin:
        hit = _ray_box(origin, direction, box, MIN_HIT_M)
        if hit is not None and (best is None or hit < best):
            best = hit
            kind = "keepin"
    if best is None:
        raise PlanError("that camera ray does not meet a wall in the zone model")
    standoff = max(MIN_HIT_M, best - STANDOFF_M)
    target = [origin[i] + direction[i] * standoff for i in range(3)]
    if kind == "keepout":
        # Do not park against an obstacle. Reject if the standoff is still unsafe.
        if zone_mod.clamp_point(target, keepin, keepout) is None:
            raise PlanError("that tap aims at a keep-out; it was not flown")
    clamped = zone_mod.clamp_point(target, keepin, keepout)
    if clamped is None:
        raise PlanError("that camera target is outside the flight volume")
    shifted = math.sqrt(sum((clamped[i] - target[i]) ** 2 for i in range(3)))
    return {
        "target": clamped,
        "clamped": shifted > 0.01,
        "look": direction,
        "kind": kind,
    }


def resolve_target(spec: dict, pose, keepin, keepout) -> dict:
    source = spec.get("source")
    try:
        u = float(spec["u"])
        v = float(spec["v"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PlanError("tap needs source, u, and v") from exc
    if source == "map":
        raw = map_point(u, v, keepin)
        if any(zone_mod.overlaps(raw, zone_mod.CLEARANCE, box) for box in keepout):
            raise PlanError("that map point is inside a keep-out; it was not flown")
        clamped = zone_mod.clamp_point(raw, keepin, keepout)
        if clamped is None:
            raise PlanError("that map point is outside the flight volume")
        shifted = math.sqrt(sum((clamped[i] - raw[i]) ** 2 for i in range(3)))
        look = (1.0, 0.0, 0.0)
        return {"target": clamped, "clamped": shifted > 0.01, "look": look, "kind": "map"}
    if source == "camera":
        return camera_target(u, v, pose, keepin, keepout)
    raise PlanError("tap source must be map or camera")


def build_plan(spec: dict, pose, keepin=None, keepout=None) -> dict:
    if keepin is None or keepout is None:
        keepin, keepout = zone_mod.load_zones()
    if pose is None:
        raise PlanError("no pose yet")
    start = [float(v) for v in pose[0]]
    if not zone_mod.body_clear(start, keepin, keepout):
        raise PlanError("the robot is not in a flight zone yet; undock first")
    chosen = resolve_target(spec, pose, keepin, keepout)
    goal = chosen["target"]
    path = zone_mod.plan_path(start, goal, keepin, keepout)
    if path is None:
        raise PlanError("no safe path to that point")
    waypoints = []
    previous = start
    for point in path[1:]:
        dx = point[0] - previous[0]
        dy = point[1] - previous[1]
        if dx * dx + dy * dy < 1e-6:
            dx, dy = chosen["look"][0], chosen["look"][1]
        waypoints.append({"pos": point, "quat": list(level_quat(dx, dy))})
        previous = point
    if not waypoints:
        raise PlanError("that point is where the robot already is")
    length = 0.0
    cursor = start
    for wp in waypoints:
        length += math.sqrt(sum((wp["pos"][i] - cursor[i]) ** 2 for i in range(3)))
        cursor = wp["pos"]
    return {
        "target": waypoints[-1]["pos"],
        "path": [start] + [wp["pos"] for wp in waypoints],
        "waypoints": waypoints,
        "length_m": length,
        "clamped": bool(chosen["clamped"]),
        "source": spec.get("source"),
    }
