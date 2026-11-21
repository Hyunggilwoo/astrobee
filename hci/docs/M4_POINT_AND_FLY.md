# M4 — point and fly

From the phone page the operator taps the nav camera or the JEM top-down map.
The server turns that tap into a 3D target, keeps it inside the sim flight
zones, and shows the target and the path. Nothing is sent until Confirm.
STOP and the dead-man switch are the same ones as M3.

## Camera tap

The Gazebo nav camera is 320×240 with horizontal fov 2.957216683909311 rad
(`description/description/urdf/sensor_nav_cam.urdf.xacro`). The intrinsic
matrix is the one `FillCameraInfo` publishes:

```
fx = fy = width / (2 tan(hfov / 2))
cx = width / 2
cy = height / 2
```

The flight `intrinsic_matrix` in `sim.config` is for the 1280-wide sensor, so
it is not applied to this image. The wide-angle `k1` is not inverted. A tap
away from the center is a pinhole ray through that K, which is a poor fit in
the corners of a 169° lens.

The camera pose is `nav_cam_transform` from `astrobee/config/robots/sim.config`:
translation `(0.1177, -0.0422, -0.0826)` in the body frame. Quaternion
`(0.5, 0.5, 0.5, 0.5)` points camera +Z along body +X. Pixel +u is right and
+v is down, so image right is body −Y and image down is body −Z.

The ray is intersected with the keep-in and keep-out boxes. The nearest hit
in front of the camera is the wall or the obstacle. The target is 0.55 m
short of that hit, then clamped into the flight volume.

## Map tap

The map is JEM keep-in index 6 (x 9.86–12.00, y −9.24–−2.75, z 3.78–5.92).
A tap is (x, y) on that rectangle at the mid-height of the box. Keep-out
boxes that cross that height are drawn in red.

## Zones and path

The body is treated as a 0.32 m cube plus a 10 cm margin (0.26 m from the
center to the box face), the same rule as `tests/test_zones.py`.

- A tap whose body would overlap a keep-out is rejected. It is not slid
  around the obstacle.
- A tap just outside a keep-in is pulled onto the inset box if the move is
  at most 0.35 m. Farther taps are rejected.
- A straight line that stays clear is the path. If it does not, a coarse
  search finds another path inside the keep-ins. If that fails, the tap is
  rejected.
- The robot has to be in a flight zone already. A docked pose is rejected
  with "undock first".

Confirm queues `simpleMove6DOF` for each waypoint, frame `world`, through the
existing sequencer. STOP still skips the queue and the read-back.

## API

`POST /api/preview_point` with `{ "source": "map"|"camera", "u": 0..1, "v": 0..1 }`.
The response includes `readback`, `target_xyz`, `path`, `length_m`, and
`clamped`. `GET /api/map` returns the keep-out rectangles for the drawing.
Both need the pairing code. Confirm is the same `/api/confirm` as a typed command.
