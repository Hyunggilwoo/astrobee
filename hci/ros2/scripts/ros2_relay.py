#!/usr/bin/env python3
"""ROS 2 Jazzy end of the HCI bridge.

Connects to the Noetic relay over localhost TCP and exposes:
  /command              hci_ff_msgs/CommandStamped   (this side publishes into ROS 1)
  /mgt/ack              hci_ff_msgs/AckStamped
  /loc/truth/pose       geometry_msgs/PoseStamped
  /loc/pose             geometry_msgs/PoseStamped
  /hw/cam_nav           sensor_msgs/Image
  /hci_bridge/ready     std_msgs/Bool   (latched; true when the executive is listening)
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time

import bridge_codec as codec

log = logging.getLogger("hci.ros2_relay")
LOG_PATH = os.environ.get("HCI_BRIDGE_LOG", "/hci/logs/bridge_latency.jsonl")


class Ros2Relay:
    def __init__(self) -> None:
        import rclpy
        from geometry_msgs.msg import PoseStamped
        from hci_ff_msgs.msg import (
            AckCompletedStatus,
            AckStamped,
            AckStatus,
            CommandStamped,
        )
        from rclpy.qos import (
            DurabilityPolicy,
            HistoryPolicy,
            QoSProfile,
            ReliabilityPolicy,
        )
        from sensor_msgs.msg import Image
        from std_msgs.msg import Bool

        self.rclpy = rclpy
        self.AckStamped = AckStamped
        self.AckStatus = AckStatus
        self.AckCompletedStatus = AckCompletedStatus
        self.PoseStamped = PoseStamped
        self.Image = Image
        if not rclpy.ok():
            rclpy.init()
        self.node = rclpy.create_node("hci_ros2_relay")
        qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10,
        )
        ready_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self.ack_pub = self.node.create_publisher(AckStamped, codec.TOPIC_ACK, qos)
        self.truth_pub = self.node.create_publisher(PoseStamped, codec.TOPIC_TRUTH, qos)
        self.pose_pub = self.node.create_publisher(PoseStamped, codec.TOPIC_POSE, qos)
        self.image_pub = self.node.create_publisher(Image, codec.TOPIC_IMAGE, qos)
        self.ready_pub = self.node.create_publisher(Bool, codec.TOPIC_READY, ready_qos)
        self.node.create_subscription(
            CommandStamped, codec.TOPIC_COMMAND, self._on_command, qos
        )
        self._sock = None
        self._send_lock = threading.Lock()
        self._buf = codec.FrameBuffer()
        self._pending = {}
        self._lock = threading.Lock()
        self._ready = False
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

    def _publish_ready(self, ready: bool) -> None:
        from std_msgs.msg import Bool

        if ready != self._ready:
            log.info("bridge ready=%s", ready)
        self._ready = ready
        self.ready_pub.publish(Bool(data=ready))

    def _on_command(self, msg) -> None:
        raw = {
            "header": {
                "stamp": {
                    "sec": int(msg.header.stamp.sec),
                    "nanosec": int(msg.header.stamp.nanosec),
                },
                "frame_id": msg.header.frame_id,
            },
            "cmd_name": msg.cmd_name,
            "cmd_id": msg.cmd_id,
            "cmd_src": msg.cmd_src,
            "cmd_origin": msg.cmd_origin,
            "subsys_name": msg.subsys_name,
            "args": [_arg_dict(arg) for arg in msg.args],
        }
        now = time.time()
        with self._lock:
            self._pending[msg.cmd_id] = {
                "t_ros2": now,
                "cmd_name": msg.cmd_name,
                "cmd_bridge_ms": None,
            }
        self._send(
            {"op": "forward", "topic": codec.TOPIC_COMMAND, "t_send": now, "msg": raw}
        )

    def _send(self, obj: dict) -> None:
        frame = codec.encode_frame(obj)
        with self._send_lock:
            sock = self._sock
            if sock is None:
                log.warning("drop %s; ros1 relay is down", obj.get("op"))
                return
            try:
                sock.sendall(frame)
            except OSError as exc:
                log.warning("send failed: %s", exc)
                self._sock = None

    def _handle(self, frame: dict) -> None:
        op = frame.get("op")
        if op == "status":
            self._publish_ready(int(frame.get("command_subscribers", 0)) > 0)
            return
        if op == "stat":
            with self._lock:
                slot = self._pending.get(frame.get("cmd_id"))
                if slot is not None:
                    slot["cmd_bridge_ms"] = float(frame["cmd_bridge_ms"])
            return
        if op != "forward":
            return
        topic = frame.get("topic")
        if topic == codec.TOPIC_ACK:
            self._on_ack_frame(frame)
        elif topic == codec.TOPIC_TRUTH:
            self.truth_pub.publish(_pose_msg(self.PoseStamped, frame["msg"]))
        elif topic == codec.TOPIC_POSE:
            self.pose_pub.publish(_pose_msg(self.PoseStamped, frame["msg"]))
        elif topic == codec.TOPIC_IMAGE:
            self.image_pub.publish(_image_msg(self.Image, frame["msg"]))

    def _on_ack_frame(self, frame: dict) -> None:
        msg = frame["msg"]
        ros = self.AckStamped()
        stamp = msg["header"]["stamp"]
        ros.header.stamp.sec = int(stamp["sec"])
        ros.header.stamp.nanosec = int(stamp["nanosec"])
        ros.header.frame_id = msg["header"].get("frame_id", "")
        ros.cmd_id = msg["cmd_id"]
        ros.status.status = int(msg["status"]["status"])
        ros.completed_status.status = int(msg["completed_status"]["status"])
        ros.message = msg.get("message", "")
        self.ack_pub.publish(ros)
        ack_ms = (time.time() - float(frame.get("t_send", time.time()))) * 1000.0
        with self._lock:
            slot = self._pending.pop(msg["cmd_id"], None)
        if slot is None or slot["cmd_bridge_ms"] is None:
            return
        if int(msg["completed_status"]["status"]) == 0:
            # NOT means still running. Put the slot back so the final ack can pair.
            with self._lock:
                self._pending[msg["cmd_id"]] = slot
            return
        wall_ms = (time.time() - slot["t_ros2"]) * 1000.0
        row = {
            "cmd_id": msg["cmd_id"],
            "cmd_name": slot["cmd_name"],
            "cmd_bridge_ms": slot["cmd_bridge_ms"],
            "ack_bridge_ms": ack_ms,
            "round_trip_bridge_ms": slot["cmd_bridge_ms"] + ack_ms,
            "wall_ms": wall_ms,
        }
        with open(LOG_PATH, "a") as handle:
            handle.write(json.dumps(row) + "\n")
        log.info(
            "ack %s bridge round-trip %.2f ms, wall %.1f s",
            slot["cmd_name"],
            row["round_trip_bridge_ms"],
            wall_ms / 1000.0,
        )

    def run(self) -> None:
        host = os.environ.get("HCI_BRIDGE_HOST", codec.BRIDGE_HOST)
        port = int(os.environ.get("HCI_BRIDGE_PORT", str(codec.BRIDGE_PORT)))
        spin = threading.Thread(target=lambda: self.rclpy.spin(self.node), daemon=True)
        spin.start()
        while self.rclpy.ok():
            try:
                sock = socket.create_connection((host, port), timeout=5)
            except OSError:
                self._publish_ready(False)
                time.sleep(1.0)
                continue
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(1.0)
            self._buf = codec.FrameBuffer()
            with self._send_lock:
                self._sock = sock
            log.info("connected to ros1 relay %s:%d", host, port)
            try:
                while self.rclpy.ok():
                    try:
                        data = sock.recv(65536)
                    except socket.timeout:
                        continue
                    if not data:
                        break
                    for frame in self._buf.feed(data):
                        self._handle(frame)
            except (OSError, ValueError) as exc:
                log.warning("relay link dropped: %s", exc)
            finally:
                with self._send_lock:
                    self._sock = None
                sock.close()
                self._publish_ready(False)
                time.sleep(1.0)


def _arg_dict(arg) -> dict:
    return {
        "data_type": int(arg.data_type),
        "b": bool(arg.b),
        "d": float(arg.d),
        "f": float(arg.f),
        "i": int(arg.i),
        "ll": int(arg.ll),
        "s": str(arg.s),
        "vec3d": [float(v) for v in arg.vec3d],
        "mat33f": [float(v) for v in arg.mat33f],
    }


def _pose_msg(cls, raw: dict):
    msg = cls()
    stamp = raw["header"]["stamp"]
    msg.header.stamp.sec = int(stamp["sec"])
    msg.header.stamp.nanosec = int(stamp["nanosec"])
    msg.header.frame_id = raw["header"].get("frame_id", "")
    pos = raw["pose"]["position"]
    quat = raw["pose"]["orientation"]
    msg.pose.position.x = float(pos["x"])
    msg.pose.position.y = float(pos["y"])
    msg.pose.position.z = float(pos["z"])
    msg.pose.orientation.x = float(quat["x"])
    msg.pose.orientation.y = float(quat["y"])
    msg.pose.orientation.z = float(quat["z"])
    msg.pose.orientation.w = float(quat["w"])
    return msg


def _image_msg(cls, raw: dict):
    msg = cls()
    image = codec.image_from_dict(raw)
    msg.header.frame_id = raw["header"].get("frame_id", "")
    msg.height = image["height"]
    msg.width = image["width"]
    msg.encoding = image["encoding"]
    msg.is_bigendian = False
    msg.step = image["step"]
    msg.data = image["data"]
    return msg


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    relay = Ros2Relay()
    try:
        relay.run()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
