#!/usr/bin/env python3
"""Block until the bridge says the ROS 1 executive is listening on /command."""
from __future__ import annotations

import sys
import time

import bridge_codec as codec


def main() -> int:
    import rclpy
    from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
    from std_msgs.msg import Bool

    timeout_s = float(sys.argv[1]) if len(sys.argv) > 1 else 120.0
    rclpy.init()
    node = rclpy.create_node("hci_wait_bridge")
    qos = QoSProfile(
        reliability=ReliabilityPolicy.RELIABLE,
        durability=DurabilityPolicy.TRANSIENT_LOCAL,
        history=HistoryPolicy.KEEP_LAST,
        depth=1,
    )
    state = {"ok": False}

    def on_ready(msg) -> None:
        if msg.data:
            state["ok"] = True

    node.create_subscription(Bool, codec.TOPIC_READY, on_ready, qos)
    deadline = time.time() + timeout_s
    while time.time() < deadline and not state["ok"] and rclpy.ok():
        rclpy.spin_once(node, timeout_sec=0.2)
    node.destroy_node()
    if not state["ok"]:
        print("ros2 bridge did not become ready within %.0fs" % timeout_s, flush=True)
        return 1
    print("ros2 bridge ready", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
