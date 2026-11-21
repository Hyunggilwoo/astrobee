"""Point-and-fly projection, zone clamp, and the phone confirm gate."""
import os
import time

import astrobee_cmd as ac
import point_fly
import zones
from intent_parser import Intent
from sequencer import Sequencer
from tests.test_phone import Client, make_ctrl, start_http

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEEPIN, KEEPOUT = zones.load_zones(os.path.join(HERE, "zones_iss.json"))
LO, HI = point_fly.jem_box(KEEPIN)


def _frac(point):
    u = (point[0] - LO[0]) / (HI[0] - LO[0])
    v = (HI[1] - point[1]) / (HI[1] - LO[1])
    return u, v


def _pose(point, quat=(0.0, 0.0, 0.0, 1.0)):
    return (list(point), list(quat))


def test_center_pixel_looks_along_body_plus_x():
    ray = point_fly.body_ray(160.0, 120.0, 320, 240)
    assert ray[0] > 0.99
    assert abs(ray[1]) < 0.02
    assert abs(ray[2]) < 0.02


def test_image_right_and_down_match_body_axes():
    right = point_fly.body_ray(240, 120, 320, 240)
    down = point_fly.body_ray(160, 180, 320, 240)
    assert right[1] < -0.2
    assert down[2] < -0.2


def test_map_center_is_inside_the_flight_volume():
    point = point_fly.map_point(0.5, 0.5, KEEPIN)
    assert zones.body_clear(point, KEEPIN, KEEPOUT)


def test_keepout_tap_is_rejected_and_a_near_wall_tap_is_clamped():
    u, v = _frac((10.01, -5.53, 4.85))
    try:
        point_fly.resolve_target(
            {"source": "map", "u": u, "v": v},
            _pose((11.0, -7.0, 4.85)),
            KEEPIN,
            KEEPOUT,
        )
    except point_fly.PlanError as exc:
        assert "keep-out" in str(exc)
    else:
        raise AssertionError("keep-out tap was accepted")

    outside = [HI[0] + 0.08, -6.5, 0.5 * (LO[2] + HI[2])]
    clamped = zones.clamp_point(outside, KEEPIN, KEEPOUT)
    assert clamped is not None
    assert clamped[0] < HI[0] - zones.CLEARANCE + 1e-6
    assert zones.clamp_point([HI[0] + 2.0, -6.5, outside[2]], KEEPIN, KEEPOUT) is None


def test_plan_reaches_rack_alpha_and_stays_clear():
    start = (11.25, -6.59, 4.8)
    u, v = _frac((11.3, -7.5, 4.85))
    plan = point_fly.build_plan(
        {"source": "map", "u": u, "v": v}, _pose(start), KEEPIN, KEEPOUT
    )
    goal = plan["target"]
    assert abs(goal[0] - 11.3) < 0.05
    assert abs(goal[1] - -7.5) < 0.05
    assert zones.body_clear(goal, KEEPIN, KEEPOUT)
    for index in range(len(plan["path"]) - 1):
        assert zones.segment_clear(
            plan["path"][index], plan["path"][index + 1], KEEPIN, KEEPOUT
        )


def test_path_around_a_keepout_does_not_cut_through_it():
    start = (10.3, -8.0, 4.85)
    goal = (10.3, -4.5, 4.85)
    assert not zones.segment_clear(start, goal, KEEPIN, KEEPOUT)
    path = zones.plan_path(start, goal, KEEPIN, KEEPOUT)
    assert path is not None
    for index in range(len(path) - 1):
        assert zones.segment_clear(path[index], path[index + 1], KEEPIN, KEEPOUT)


def test_camera_center_tap_stops_short_of_the_wall():
    start = (10.6, -7.0, 4.85)
    plan = point_fly.build_plan(
        {"source": "camera", "u": 0.5, "v": 0.5}, _pose(start), KEEPIN, KEEPOUT
    )
    assert plan["target"][0] > start[0] + 0.2
    assert plan["target"][0] < HI[0] - zones.CLEARANCE + 1e-6


def test_docked_pose_is_refused():
    try:
        point_fly.build_plan(
            {"source": "map", "u": 0.5, "v": 0.5},
            _pose((9.816, -9.806, 4.293)),
            KEEPIN,
            KEEPOUT,
        )
    except point_fly.PlanError as exc:
        assert "undock" in str(exc)
    else:
        raise AssertionError("dock pose was accepted")


def test_phone_tap_does_not_move_until_confirm_and_stop_still_wins():
    ctrl = make_ctrl(move_time=0.4)
    ctrl.inner.docked = False
    ctrl.inner.pos = [11.25, -6.59, 4.8]
    server = start_http(ctrl)
    try:
        client = Client(server, ctrl.code)
        status, body = client.request(
            "POST", "/api/pair", {"code": ctrl.code}, auth=False
        )
        assert status == 200
        client.client_id = body["client_id"]
        u, v = _frac((11.3, -4.2, 4.85))
        status, preview = client.request(
            "POST", "/api/preview_point", {"source": "map", "u": u, "v": v}
        )
        assert status == 200, preview
        assert preview["action"] == "fly_path"
        assert "About to fly" in preview["readback"]
        assert ctrl.inner.pos[1] == -6.59
        bad_u, bad_v = _frac((10.01, -5.53, 4.85))
        status, rejected = client.request(
            "POST", "/api/preview_point", {"source": "map", "u": bad_u, "v": bad_v}
        )
        assert status == 400
        assert "keep-out" in rejected["error"]
        status, _body = client.request(
            "POST", "/api/confirm", {"preview_id": preview["preview_id"]}
        )
        assert status == 200
        deadline = time.time() + 2
        while ctrl.session.busy and time.time() < deadline:
            time.sleep(0.05)
        assert not ctrl.session.busy
        assert ctrl.session.results[-1].ok
        names = [cmd.cmd_name for cmd in ctrl.inner.sent]
        assert ac.CMD_SIMPLE_MOVE6DOF in names
        assert ac.CMD_STOP_ALL_MOTION not in names
        goal = preview["target_xyz"]
        assert abs(ctrl.inner.pos[0] - goal[0]) < 1e-6
        status, stopped = client.request("POST", "/api/stop", {})
        assert status == 200
        assert stopped["ack"] in ("OK", "EXEC_FAILED")
    finally:
        server.shutdown()
        ctrl.close()


def test_sequencer_sends_one_move_per_waypoint():
    backend = ac.MockBackend()
    backend.docked = False
    commander = ac.AstrobeeCommander(backend)
    seq = Sequencer(commander, {})
    intent = Intent(
        "fly_path",
        waypoints=[
            {"pos": [11.0, -7.0, 4.85], "quat": [0, 0, 0, 1]},
            {"pos": [11.2, -6.0, 4.85], "quat": [0, 0, 0, 1]},
        ],
    )
    result = seq.execute(intent)
    assert result.ok
    assert [cmd.cmd_name for cmd in backend.sent] == [
        ac.CMD_SIMPLE_MOVE6DOF,
        ac.CMD_SIMPLE_MOVE6DOF,
    ]
