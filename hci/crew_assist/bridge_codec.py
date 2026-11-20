"""Wire format for the HCI ROS 1 <-> ROS 2 relay.

The dicts match the ROS 2 messages in hci/ros2/hci_ff_msgs field for field.
Nothing here imports rospy or rclpy, so the mapping tests run in CI.

Official ros1_bridge does not support Jazzy (Ubuntu 24.04 has no ROS 1), and a
prebuilt bridge would not know ff_msgs. This codec is the mapping those
approaches would have generated.
"""
from __future__ import annotations

import base64
import json
import struct
from typing import Dict, Iterable, List, Optional

import astrobee_cmd as ac

BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 11511
MAX_FRAME = 8_000_000

TOPIC_COMMAND = "/command"
TOPIC_ACK = "/mgt/ack"
TOPIC_TRUTH = "/loc/truth/pose"
TOPIC_POSE = "/loc/pose"
TOPIC_IMAGE = "/hw/cam_nav"
TOPIC_READY = "/hci_bridge/ready"


def _zeros(n: int) -> List[float]:
    return [0.0] * n


def arg_to_dict(arg: ac.Arg) -> dict:
    out = {
        "data_type": int(arg.data_type),
        "b": False,
        "d": 0.0,
        "f": 0.0,
        "i": 0,
        "ll": 0,
        "s": "",
        "vec3d": _zeros(3),
        "mat33f": _zeros(9),
    }
    kind = arg.data_type
    if kind == ac.DATA_TYPE_BOOL:
        out["b"] = bool(arg.value)
    elif kind == ac.DATA_TYPE_DOUBLE:
        out["d"] = float(arg.value)
    elif kind == ac.DATA_TYPE_FLOAT:
        out["f"] = float(arg.value)
    elif kind == ac.DATA_TYPE_INT:
        out["i"] = int(arg.value)
    elif kind == ac.DATA_TYPE_LONGLONG:
        out["ll"] = int(arg.value)
    elif kind == ac.DATA_TYPE_STRING:
        out["s"] = str(arg.value)
    elif kind == ac.DATA_TYPE_VEC3d:
        out["vec3d"] = [float(v) for v in arg.value]
    elif kind == ac.DATA_TYPE_MAT33f:
        out["mat33f"] = [float(v) for v in arg.value]
    else:
        raise ValueError("unsupported CommandArg data_type %s" % kind)
    return out


def dict_to_arg(raw: dict) -> ac.Arg:
    kind = int(raw["data_type"])
    if kind == ac.DATA_TYPE_BOOL:
        value = bool(raw["b"])
    elif kind == ac.DATA_TYPE_DOUBLE:
        value = float(raw["d"])
    elif kind == ac.DATA_TYPE_FLOAT:
        value = float(raw["f"])
    elif kind == ac.DATA_TYPE_INT:
        value = int(raw["i"])
    elif kind == ac.DATA_TYPE_LONGLONG:
        value = int(raw["ll"])
    elif kind == ac.DATA_TYPE_STRING:
        value = str(raw["s"])
    elif kind == ac.DATA_TYPE_VEC3d:
        value = [float(v) for v in raw["vec3d"]]
    elif kind == ac.DATA_TYPE_MAT33f:
        value = [float(v) for v in raw["mat33f"]]
    else:
        raise ValueError("unsupported CommandArg data_type %s" % kind)
    return ac.Arg(kind, value)


def command_to_dict(cmd: ac.Command) -> dict:
    return {
        "header": {"stamp": {"sec": 0, "nanosec": 0}, "frame_id": ""},
        "cmd_name": cmd.cmd_name,
        "cmd_id": cmd.cmd_id,
        "cmd_src": cmd.cmd_src,
        "cmd_origin": cmd.cmd_origin,
        "subsys_name": cmd.subsys_name,
        "args": [arg_to_dict(arg) for arg in cmd.args],
    }


def dict_to_command(raw: dict) -> ac.Command:
    return ac.Command(
        cmd_name=str(raw["cmd_name"]),
        cmd_id=str(raw["cmd_id"]),
        args=[dict_to_arg(arg) for arg in raw.get("args", [])],
        cmd_src=str(raw.get("cmd_src", ac.CMD_SRC)),
        cmd_origin=str(raw.get("cmd_origin", "")),
        subsys_name=str(raw.get("subsys_name", ac.SUBSYS_NAME)),
    )


def ack_to_dict(cmd_id: str, completed: int, message: str, status: int = ac.ACK_COMPLETED) -> dict:
    return {
        "header": {"stamp": {"sec": 0, "nanosec": 0}, "frame_id": ""},
        "cmd_id": cmd_id,
        "status": {"status": int(status)},
        "completed_status": {"status": int(completed)},
        "message": message or "",
    }


def pose_to_dict(pos: Iterable[float], quat: Iterable[float], frame_id: str = "world") -> dict:
    px, py, pz = [float(v) for v in pos]
    qx, qy, qz, qw = [float(v) for v in quat]
    return {
        "header": {"stamp": {"sec": 0, "nanosec": 0}, "frame_id": frame_id},
        "pose": {
            "position": {"x": px, "y": py, "z": pz},
            "orientation": {"x": qx, "y": qy, "z": qz, "w": qw},
        },
    }


def pose_from_dict(raw: dict):
    pose = raw["pose"]
    pos = pose["position"]
    quat = pose["orientation"]
    return [float(pos["x"]), float(pos["y"]), float(pos["z"])], [
        float(quat["x"]), float(quat["y"]), float(quat["z"]), float(quat["w"])]


def image_to_dict(width: int, height: int, encoding: str, step: int, data: bytes,
                  frame_id: str = "") -> dict:
    return {
        "header": {"stamp": {"sec": 0, "nanosec": 0}, "frame_id": frame_id},
        "height": int(height),
        "width": int(width),
        "encoding": encoding,
        "is_bigendian": False,
        "step": int(step),
        "data_b64": base64.b64encode(data).decode("ascii"),
    }


def image_from_dict(raw: dict):
    return {
        "width": int(raw["width"]),
        "height": int(raw["height"]),
        "encoding": raw["encoding"],
        "step": int(raw["step"]),
        "data": base64.b64decode(raw["data_b64"]),
    }


def encode_frame(obj: dict) -> bytes:
    payload = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_FRAME:
        raise ValueError("frame is %d bytes" % len(payload))
    return struct.pack(">I", len(payload)) + payload


class FrameBuffer:
    def __init__(self) -> None:
        self.buf = b""

    def feed(self, data: bytes) -> List[dict]:
        self.buf += data
        out: List[dict] = []
        while len(self.buf) >= 4:
            size = struct.unpack(">I", self.buf[:4])[0]
            if size > MAX_FRAME:
                raise ValueError("frame header %d is over the cap" % size)
            if len(self.buf) < 4 + size:
                break
            payload = self.buf[4:4 + size]
            self.buf = self.buf[4 + size:]
            out.append(json.loads(payload.decode("utf-8")))
        return out


def summarize_latency(rows: List[dict]) -> dict:
    """Bridge overhead is cmd one-way plus ack one-way. Wall time includes the flight."""
    if not rows:
        return {
            "count": 0,
            "mean_cmd_bridge_ms": None,
            "mean_ack_bridge_ms": None,
            "mean_round_trip_bridge_ms": None,
            "max_round_trip_bridge_ms": None,
            "mean_wall_ms": None,
        }

    def mean(key: str) -> float:
        return sum(float(row[key]) for row in rows) / len(rows)

    rounds = [float(row["round_trip_bridge_ms"]) for row in rows]
    return {
        "count": len(rows),
        "mean_cmd_bridge_ms": mean("cmd_bridge_ms"),
        "mean_ack_bridge_ms": mean("ack_bridge_ms"),
        "mean_round_trip_bridge_ms": mean("round_trip_bridge_ms"),
        "max_round_trip_bridge_ms": max(rounds),
        "mean_wall_ms": mean("wall_ms"),
    }


def format_latency(summary: dict) -> str:
    if not summary["count"]:
        return "bridge latency: no command/ack samples"
    return (
        "bridge latency: n=%d  command one-way mean %.2f ms  ack one-way mean %.2f ms  "
        "round-trip mean %.2f ms (max %.2f ms)  command->ack wall mean %.1f s"
        % (
            summary["count"],
            summary["mean_cmd_bridge_ms"],
            summary["mean_ack_bridge_ms"],
            summary["mean_round_trip_bridge_ms"],
            summary["max_round_trip_bridge_ms"],
            summary["mean_wall_ms"] / 1000.0,
        )
    )


def loads_latency_file(path: str) -> List[dict]:
    rows = []
    try:
        handle = open(path)
    except OSError:
        return rows
    with handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def required_msg_fields() -> Dict[str, List[str]]:
    return {
        "CommandArg": ["data_type", "b", "d", "f", "i", "ll", "s", "vec3d", "mat33f"],
        "CommandStamped": ["header", "cmd_name", "cmd_id", "cmd_src", "cmd_origin",
                           "subsys_name", "args"],
        "AckStatus": ["status"],
        "AckCompletedStatus": ["status"],
        "AckStamped": ["header", "cmd_id", "status", "completed_status", "message"],
    }


def msg_field_names(text: str) -> List[str]:
    names = []
    for line in text.splitlines():
        body = line.split("#", 1)[0].strip()
        if not body or "=" in body.split()[0:1]:
            # constant lines look like "uint8 FOO = 0" and still have a name,
            # but they are not fields. A field line has a type and a name and
            # no '='.
            if "=" in body:
                continue
        parts = body.split()
        if len(parts) < 2:
            continue
        names.append(parts[-1].rstrip("[]"))
    return names
