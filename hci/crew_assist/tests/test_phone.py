"""Phone API: auth, one controller, read-back, stop bypass, dead-man, ROS 1 stop fallback."""
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request

import astrobee_cmd as ac
import bridge_codec as codec
from intent_parser import load_locations
from phone_control import FallbackBackend, PhoneController, make_session
from phone_http import PhoneServer

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCS = load_locations(os.path.join(HERE, "locations.yaml"))


class MarkBackend(ac.Backend):
    def __init__(self, inner):
        self.inner = inner
        self.stop_published_at = None
        self.last_stop_via = "primary"
        self.stops = []

    def execute(self, cmd, timeout_s):
        if cmd.cmd_name == ac.CMD_STOP_ALL_MOTION:
            self.stop_published_at = time.time()
            self.stops.append(cmd)
        return self.inner.execute(cmd, timeout_s)

    def current_pose(self):
        return self.inner.current_pose()

    def grab_image(self, topic, out_path, timeout_s):
        return self.inner.grab_image(topic, out_path, timeout_s)


def make_ctrl(move_time=0.0, heartbeat=2.0, code="123456"):
    inner = ac.MockBackend(move_time_s=move_time)
    backend = MarkBackend(inner)
    parser, session, echoes = make_session(backend, LOCS)
    ctrl = PhoneController(session, parser, backend, code=code, heartbeat_s=heartbeat)
    ctrl.echoes = echoes
    ctrl.inner = inner
    return ctrl


class Client:
    def __init__(self, server, code):
        self.base = "http://127.0.0.1:%d" % server.server_address[1]
        self.code = code
        self.client_id = ""

    def request(self, method, path, payload=None, auth=True):
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if auth:
            req.add_header("X-Pairing-Code", self.code)
            if self.client_id:
                req.add_header("X-Client-Id", self.client_id)
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())


def start_http(ctrl):
    server = PhoneServer(("127.0.0.1", 0), ctrl)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_auth_rejects_commands_without_the_pairing_code():
    ctrl = make_ctrl(code="654321")
    server = start_http(ctrl)
    try:
        guest = Client(server, "000000")
        status, body = guest.request("POST", "/api/preview", {"text": "undock"})
        assert status == 401
        assert "pairing" in body["error"]
        status, body = guest.request("POST", "/api/stop", {})
        assert status == 401
    finally:
        server.shutdown()
        ctrl.close()


def test_one_controller_others_are_view_only():
    ctrl = make_ctrl()
    server = start_http(ctrl)
    try:
        first = Client(server, ctrl.code)
        status, body = first.request(
            "POST", "/api/pair", {"code": ctrl.code}, auth=False
        )
        assert status == 200 and body["role"] == "controller"
        first.client_id = body["client_id"]
        second = Client(server, ctrl.code)
        status, body = second.request(
            "POST", "/api/pair", {"code": ctrl.code}, auth=False
        )
        assert body["role"] == "viewer"
        second.client_id = body["client_id"]
        status, body = second.request("POST", "/api/preview", {"text": "undock"})
        assert status == 403
        status, _ = first.request("POST", "/api/preview", {"text": "undock"})
        assert status == 200
    finally:
        server.shutdown()
        ctrl.close()


def test_inflight_status_names_the_command_just_confirmed():
    ctrl = make_ctrl(move_time=0.6)
    try:
        claimed = ctrl.claim()
        cid = claimed["client_id"]
        undock = ctrl.preview(cid, "undock")
        ctrl.confirm(cid, undock["preview_id"])
        ctrl.session.wait_idle()
        idle = ctrl.status(cid)
        assert idle["activity"] is None
        assert "undock" in idle["last"]
        nxt = ctrl.preview(cid, "go to rack alpha")
        sent = ctrl.confirm(cid, nxt["preview_id"])
        assert "rack" in sent["about"]
        seen = None
        deadline = time.time() + 2
        while time.time() < deadline:
            body = ctrl.status(cid)
            activity = body.get("activity") or {}
            if activity.get("action") == "go_to":
                seen = body
                break
            time.sleep(0.01)
        assert seen is not None, idle
        assert "undock" in seen["last"]
        assert seen["activity"]["ack"] in ("queued", "sending", "OK")
        assert (
            "fly to" in seen["activity"]["label"] or "rack" in seen["activity"]["label"]
        )
        ctrl.session.wait_idle()
        done = ctrl.status(cid)
        assert done["activity"] is None
        assert "simpleMove6DOF" in done["last"]
    finally:
        ctrl.close()


def test_confirm_then_motion_and_stop_bypasses_readback():
    ctrl = make_ctrl(move_time=3.0)
    server = start_http(ctrl)
    try:
        client = Client(server, ctrl.code)
        _, paired = client.request("POST", "/api/pair", {"code": ctrl.code}, auth=False)
        client.client_id = paired["client_id"]
        status, preview = client.request(
            "POST", "/api/preview", {"text": "go to rack alpha"}
        )
        assert status == 200
        assert "rack one" in preview["about"]
        assert "Heard" in preview["readback"]
        assert ctrl.inner.sent == []
        status, _ = client.request(
            "POST", "/api/confirm", {"preview_id": preview["preview_id"]}
        )
        assert status == 200
        deadline = time.time() + 2
        while not ctrl.inner.moving.is_set() and time.time() < deadline:
            time.sleep(0.01)
        assert ctrl.inner.moving.is_set()
        t_tap = time.time() * 1000.0
        status, stopped = client.request("POST", "/api/stop", {"t_tap": t_tap})
        assert status == 200
        assert stopped["server_to_publish_ms"] < 50
        assert stopped["tap_to_publish_ms"] < 200
        assert ctrl.backend.stops[-1].cmd_name == "stopAllMotion"
        assert ctrl.backend.stops[-1].cmd_origin == ""
        ctrl.session.wait_idle()
        assert not ctrl.session.busy
    finally:
        server.shutdown()
        ctrl.close()


def test_stop_clears_a_queued_step():
    ctrl = make_ctrl(move_time=2.0)
    try:
        claimed = ctrl.claim()
        first = ctrl.preview(claimed["client_id"], "go to rack alpha")
        ctrl.confirm(claimed["client_id"], first["preview_id"])
        while not ctrl.inner.moving.is_set():
            time.sleep(0.01)
        second = ctrl.preview(claimed["client_id"], "go to mid jem")
        ctrl.confirm(claimed["client_id"], second["preview_id"])
        ctrl.stop()
        ctrl.session.wait_idle()
        names = [cmd.cmd_name for cmd in ctrl.inner.sent]
        assert names.count("stopAllMotion") == 1
        assert "simpleMove6DOF" in names
        assert (
            ctrl.session.results[-1].ok is False
            or ctrl.inner.sent[-1].cmd_name == "stopAllMotion"
        )
    finally:
        ctrl.close()


def test_dead_man_stops_motion_when_the_heartbeat_stops():
    ctrl = make_ctrl(move_time=5.0, heartbeat=0.35)
    try:
        claimed = ctrl.claim()
        preview = ctrl.preview(claimed["client_id"], "go to rack bravo")
        started = time.time()
        ctrl.confirm(claimed["client_id"], preview["preview_id"])
        while not ctrl.inner.moving.is_set():
            time.sleep(0.01)
        deadline = time.time() + 2
        while ctrl.dead_man_count == 0 and time.time() < deadline:
            time.sleep(0.02)
        assert ctrl.dead_man_count == 1
        assert 0.35 <= ctrl.last_dead_man_s < 0.8
        assert time.time() - started < 2.0
        # The count is incremented just before the stop publish. Under load
        # the assertion can run in that gap.
        deadline = time.time() + 1
        while not ctrl.backend.stops and time.time() < deadline:
            time.sleep(0.01)
        assert ctrl.backend.stops[-1].cmd_name == "stopAllMotion"
    finally:
        ctrl.close()


def test_ros1_fallback_publishes_stop_when_ros2_path_is_down():
    class Dead:
        def ready(self):
            return False

        def execute(self, cmd, timeout_s):
            return ac.AckResult(
                cmd.cmd_id, ac.EXEC_FAILED, "not sent: ros2 bridge down"
            )

        def current_pose(self):
            return None

        def grab_image(self, topic, out_path, timeout_s):
            return False

    captured = []

    class Catch(ac.Backend):
        def execute(self, cmd, timeout_s):
            captured.append(cmd)
            self_time = time.time()
            backend.stop_published_at = self_time
            return ac.AckResult(cmd.cmd_id, ac.OK, "ros1")

        def current_pose(self):
            return None

        def grab_image(self, topic, out_path, timeout_s):
            return False

    backend = FallbackBackend(Dead(), Catch())
    parser, session, _ = make_session(backend, LOCS)
    ctrl = PhoneController(session, parser, backend, code="111111", heartbeat_s=5)
    try:
        claimed = ctrl.claim()
        preview = ctrl.preview(claimed["client_id"], "undock")
        ctrl.confirm(claimed["client_id"], preview["preview_id"])
        ctrl.session.wait_idle()
        result = ctrl.stop()
        assert result["via"] == "ros1"
        assert captured[-1].cmd_name == "stopAllMotion"
        assert captured[-1].cmd_origin == ""
        assert captured[-1].cmd_id
    finally:
        ctrl.close()


def test_relay_stop_frame_is_a_real_stop_command():
    received = []

    def serve(sock):
        conn, _ = sock.accept()
        buf = codec.FrameBuffer()
        while True:
            data = conn.recv(65536)
            if not data:
                break
            received.extend(buf.feed(data))
            break
        conn.close()

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    thread = threading.Thread(target=serve, args=(sock,), daemon=True)
    thread.start()
    port = sock.getsockname()[1]
    from phone_control import Ros1RelayStop

    backend = Ros1RelayStop("127.0.0.1", port)
    result = backend.execute(ac.build_stop(), 2.0)
    thread.join(2)
    sock.close()
    assert result.ok
    assert received[0]["topic"] == "/command"
    assert received[0]["msg"]["cmd_name"] == "stopAllMotion"
    assert received[0]["msg"]["cmd_origin"] == ""
