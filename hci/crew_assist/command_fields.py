"""CommandArg field layout shared by every ROS 1 and ROS 2 publisher.

The integer types match ff_msgs/CommandArg.msg and hci_ff_msgs/CommandArg.msg.
This module does not import rospy, rclpy, or astrobee_cmd, so either side of
the bridge can fill a message from the same dict.
"""
from __future__ import annotations

from typing import Any, Tuple

DATA_TYPE_BOOL = 0
DATA_TYPE_DOUBLE = 1
DATA_TYPE_FLOAT = 2
DATA_TYPE_INT = 3
DATA_TYPE_LONGLONG = 4
DATA_TYPE_STRING = 5
DATA_TYPE_VEC3D = 6
DATA_TYPE_MAT33F = 7


def _zeros(n: int):
    return [0.0] * n


def arg_dict(data_type: int, value: Any) -> dict:
    """One CommandArg as the bridge dict. Unused fields stay at zero."""
    out = {
        "data_type": int(data_type),
        "b": False,
        "d": 0.0,
        "f": 0.0,
        "i": 0,
        "ll": 0,
        "s": "",
        "vec3d": _zeros(3),
        "mat33f": _zeros(9),
    }
    kind = int(data_type)
    if kind == DATA_TYPE_BOOL:
        out["b"] = bool(value)
    elif kind == DATA_TYPE_DOUBLE:
        out["d"] = float(value)
    elif kind == DATA_TYPE_FLOAT:
        out["f"] = float(value)
    elif kind == DATA_TYPE_INT:
        out["i"] = int(value)
    elif kind == DATA_TYPE_LONGLONG:
        out["ll"] = int(value)
    elif kind == DATA_TYPE_STRING:
        out["s"] = str(value)
    elif kind == DATA_TYPE_VEC3D:
        out["vec3d"] = [float(v) for v in value]
    elif kind == DATA_TYPE_MAT33F:
        out["mat33f"] = [float(v) for v in value]
    else:
        raise ValueError("unsupported CommandArg data_type %s" % kind)
    return out


def value_of(raw: dict) -> Tuple[int, Any]:
    """Inverse of arg_dict."""
    kind = int(raw["data_type"])
    if kind == DATA_TYPE_BOOL:
        value = bool(raw["b"])
    elif kind == DATA_TYPE_DOUBLE:
        value = float(raw["d"])
    elif kind == DATA_TYPE_FLOAT:
        value = float(raw["f"])
    elif kind == DATA_TYPE_INT:
        value = int(raw["i"])
    elif kind == DATA_TYPE_LONGLONG:
        value = int(raw["ll"])
    elif kind == DATA_TYPE_STRING:
        value = str(raw["s"])
    elif kind == DATA_TYPE_VEC3D:
        value = [float(v) for v in raw["vec3d"]]
    elif kind == DATA_TYPE_MAT33F:
        value = [float(v) for v in raw["mat33f"]]
    else:
        raise ValueError("unsupported CommandArg data_type %s" % kind)
    return kind, value


def fill_arg(arg_msg, item: dict) -> None:
    """Copy one bridge dict onto a rospy or rclpy CommandArg."""
    arg_msg.data_type = int(item["data_type"])
    arg_msg.b = bool(item["b"])
    arg_msg.d = float(item["d"])
    arg_msg.f = float(item["f"])
    arg_msg.i = int(item["i"])
    arg_msg.ll = int(item["ll"])
    arg_msg.s = str(item["s"])
    arg_msg.vec3d = [float(v) for v in item["vec3d"]]
    arg_msg.mat33f = [float(v) for v in item["mat33f"]]


def fill_command(msg, raw: dict, arg_cls) -> None:
    """Copy a command dict onto a CommandStamped. The caller sets the stamp."""
    msg.header.frame_id = raw["header"].get("frame_id", "")
    msg.cmd_name = raw["cmd_name"]
    msg.cmd_id = raw["cmd_id"]
    msg.cmd_src = raw["cmd_src"]
    msg.cmd_origin = raw["cmd_origin"]
    msg.subsys_name = raw["subsys_name"]
    for item in raw.get("args", []):
        arg = arg_cls()
        fill_arg(arg, item)
        msg.args.append(arg)
