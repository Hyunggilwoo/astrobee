"""ROS 2 mapping and fail-closed command path. No rclpy and no ROS master."""
import os
import threading
import time

import pytest

import astrobee_cmd as ac
import bridge_codec as codec
from ros2_backend import Ros2Backend

MSG_DIR = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "..", "ros2", "hci_ff_msgs", "msg"))


def test_command_roundtrip_keeps_origin_empty_and_world_frame():
    cmd = ac.build_simple_move([11.3, -7.5, 4.85], [0.0, 0.0, 0.0, 1.0], frame="world")
    raw = codec.command_to_dict(cmd)
    assert raw["cmd_origin"] == ""
    assert raw["cmd_src"] == "guest_science"
    assert raw["args"][0]["s"] == "world"
    assert raw["args"][0]["data_type"] == ac.DATA_TYPE_STRING
    assert raw["args"][1]["vec3d"] == pytest.approx([11.3, -7.5, 4.85])
    assert raw["args"][3]["mat33f"][:4] == pytest.approx([0.0, 0.0, 0.0, 1.0])
    back = codec.dict_to_command(raw)
    assert back.cmd_origin == ""
    assert back.cmd_name == ac.CMD_SIMPLE_MOVE6DOF
    assert back.args[1].value == pytest.approx([11.3, -7.5, 4.85])


def test_dock_and_stop_map_without_guessing_fields():
    dock = codec.command_to_dict(ac.build_dock(1))
    assert dock["args"][0]["data_type"] == ac.DATA_TYPE_INT
    assert dock["args"][0]["i"] == 1
    assert dock["cmd_origin"] == ""
    stop = codec.command_to_dict(ac.build_stop())
    assert stop["cmd_name"] == "stopAllMotion"
    assert stop["args"] == []
    assert codec.dict_to_command(stop).cmd_name == ac.CMD_STOP_ALL_MOTION


def test_frames_reassemble_a_split_command():
    frame = codec.encode_frame(codec.command_to_dict(ac.build_undock()))
    buf = codec.FrameBuffer()
    assert buf.feed(frame[:6]) == []
    got = buf.feed(frame[6:])
    assert got[0]["cmd_name"] == "undock"
    assert got[0]["cmd_origin"] == ""


def test_pose_and_image_roundtrip():
    pos, quat = codec.pose_from_dict(codec.pose_to_dict([9.816, -9.806, 4.293], [0, 0, 0, 1]))
    assert pos == pytest.approx([9.816, -9.806, 4.293])
    assert quat == pytest.approx([0, 0, 0, 1])
    raw = codec.image_to_dict(2, 2, "mono8", 2, b"\x00\x10\x20\x30")
    image = codec.image_from_dict(raw)
    assert image["data"] == b"\x00\x10\x20\x30"
    assert image["encoding"] == "mono8"


def test_ros2_msg_files_have_the_codec_fields():
    for name, fields in codec.required_msg_fields().items():
        text = open(os.path.join(MSG_DIR, name + ".msg")).read()
        found = codec.msg_field_names(text)
        for field in fields:
            assert field in found, name


def test_latency_summary():
    summary = codec.summarize_latency([
        {"cmd_bridge_ms": 1.0, "ack_bridge_ms": 2.0, "round_trip_bridge_ms": 3.0, "wall_ms": 30000.0},
        {"cmd_bridge_ms": 3.0, "ack_bridge_ms": 4.0, "round_trip_bridge_ms": 7.0, "wall_ms": 40000.0},
    ])
    assert summary["count"] == 2
    assert summary["mean_round_trip_bridge_ms"] == pytest.approx(5.0)
    assert summary["mean_wall_ms"] == pytest.approx(35000.0)
    assert "round-trip mean 5.00 ms" in codec.format_latency(summary)
    assert codec.summarize_latency([])["count"] == 0


class FakeTransport:
    def __init__(self, ready=True, ack_delay_s=0.0):
        self.is_ready = ready
        self.sent = []
        self._acks = {}
        self._cv = threading.Condition()
        self.ack_delay_s = ack_delay_s
        self.pose = ([9.8, -9.8, 4.3], [0.0, 0.0, 0.0, 1.0])

    def ready(self):
        return self.is_ready

    def publish_command(self, msg, t_send):
        del t_send
        self.sent.append(msg)
        if self.ack_delay_s <= 0:
            self._store(msg)
            return
        threading.Thread(target=self._later, args=(msg,), daemon=True).start()

    def _later(self, msg):
        time.sleep(self.ack_delay_s)
        self._store(msg)

    def _store(self, msg):
        with self._cv:
            self._acks[msg["cmd_id"]] = codec.ack_to_dict(msg["cmd_id"], ac.OK, "ok")
            self._cv.notify_all()

    def poll_ack(self, cmd_id):
        with self._cv:
            return self._acks.get(cmd_id)

    def wait(self, timeout_s):
        with self._cv:
            self._cv.wait(timeout_s)

    def latest_pose(self):
        return self.pose

    def wait_image(self, topic, timeout_s):
        del topic, timeout_s
        return None


def test_motion_is_fail_closed_when_the_bridge_is_down():
    backend = Ros2Backend(transport=FakeTransport(ready=False))
    result = backend.execute(ac.build_undock(), 1.0)
    assert result.completed == ac.EXEC_FAILED
    assert backend.transport.sent == []


def test_stop_is_still_sent_when_the_bridge_is_down():
    transport = FakeTransport(ready=False)
    backend = Ros2Backend(transport=transport)
    result = backend.execute(ac.build_stop(), 1.0)
    assert transport.sent[0]["cmd_name"] == "stopAllMotion"
    assert transport.sent[0]["cmd_origin"] == ""
    assert result.ok


def test_backend_roundtrip_uses_the_mapped_command():
    transport = FakeTransport()
    backend = Ros2Backend(transport=transport)
    result = backend.execute(ac.build_dock(1), 1.0)
    assert result.ok
    assert transport.sent[0]["args"][0]["i"] == 1
    assert backend.current_pose()[0] == pytest.approx([9.8, -9.8, 4.3])


def test_stop_during_a_move_does_not_wait_for_the_move_ack():
    transport = FakeTransport(ack_delay_s=5.0)
    backend = Ros2Backend(transport=transport)
    backend.abort_grace_s = 0.2
    backend.abort_event = threading.Event()

    def trip():
        time.sleep(0.05)
        backend.abort_event.set()

    threading.Thread(target=trip, daemon=True).start()
    started = time.time()
    result = backend.execute(ac.build_simple_move([1, 2, 3], [0, 0, 0, 1]), 10.0)
    assert time.time() - started < 2.0
    assert result.completed == ac.CANCELED


def test_cli_rejects_mock_mixed_with_ros2():
    import cli
    with pytest.raises(SystemExit):
        cli.main(["--mock", "--backend", "ros2", "-c", "undock"])
