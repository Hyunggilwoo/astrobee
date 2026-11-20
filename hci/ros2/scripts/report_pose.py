#!/usr/bin/env python3
"""Print one truth pose (falling back to loc/pose) and the bridge latency summary."""
from __future__ import annotations

import os
import sys
import time

import bridge_codec as codec


def _echo_pose(node, rclpy, pose_cls, topic: str, timeout_s: float = 10.0):
    got = {"msg": None}

    def on_pose(msg) -> None:
        got["msg"] = msg

    sub = node.create_subscription(pose_cls, topic, on_pose, 10)
    deadline = time.time() + timeout_s
    while got["msg"] is None and time.time() < deadline and rclpy.ok():
        rclpy.spin_once(node, timeout_sec=0.2)
    node.destroy_subscription(sub)
    return got["msg"]


def main() -> int:
    path = os.environ.get("HCI_BRIDGE_LOG", "/hci/logs/bridge_latency.jsonl")
    summary = codec.summarize_latency(codec.loads_latency_file(path))
    print(codec.format_latency(summary), flush=True)

    import rclpy
    from geometry_msgs.msg import PoseStamped

    rclpy.init()
    node = rclpy.create_node("hci_report_pose")
    msg = _echo_pose(node, rclpy, PoseStamped, codec.TOPIC_TRUTH, 8.0)
    source = codec.TOPIC_TRUTH
    if msg is None:
        msg = _echo_pose(node, rclpy, PoseStamped, codec.TOPIC_POSE, 8.0)
        source = codec.TOPIC_POSE
    if msg is None:
        print(
            "final pose: no message on %s or %s"
            % (codec.TOPIC_TRUTH, codec.TOPIC_POSE),
            flush=True,
        )
        return 1
    p, o = msg.pose.position, msg.pose.orientation
    print(
        "final pose %s: %.3f %.3f %.3f  quat %.4f %.4f %.4f %.4f"
        % (source, p.x, p.y, p.z, o.x, o.y, o.z, o.w),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
