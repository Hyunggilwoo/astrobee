"""ROS 2 command backend. Same AstrobeeCommander contract as RosBackend.

Publishes hci_ff_msgs/CommandStamped on /command and waits for /mgt/ack.
Those topics are the ROS 2 side of the HCI relay; the Noetic executive still
sees ff_msgs. Motion is fail-closed when the relay reports that nothing on
the ROS 1 side is subscribed to /command. stopAllMotion is still sent
immediately and does not take the command lock (that lock lives in
AstrobeeCommander.stop).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import astrobee_cmd as ac
import bridge_codec as codec
import command_fields

log = logging.getLogger("crew_assist.ros2")


class Ros2Backend(ac.Backend):
    def __init__(self, transport=None, connect_timeout_s: float = 60.0):
        self.abort_event = getattr(self, "abort_event", None)
        self.abort_grace_s = 10.0
        self.transport = (
            transport if transport is not None else RclpyTransport(connect_timeout_s)
        )
        if transport is None and not self.transport.ready():
            raise RuntimeError(
                "ros2 bridge is not ready: the ROS 1 executive is not subscribed to /command"
            )

    def execute(self, cmd: ac.Command, timeout_s: float) -> ac.AckResult:
        stop = cmd.cmd_name == ac.CMD_STOP_ALL_MOTION
        if not stop and not self.transport.ready():
            return ac.AckResult(
                cmd.cmd_id, ac.EXEC_FAILED, "not sent: ros2 bridge down"
            )
        t0 = time.time()
        try:
            self.transport.publish_command(codec.command_to_dict(cmd), t0)
        except Exception as exc:
            log.warning("publish %s failed: %s", cmd.cmd_name, exc)
            return ac.AckResult(
                cmd.cmd_id, ac.EXEC_FAILED, "not sent: %s" % exc, time.time() - t0
            )

        def poll():
            ack = self.transport.poll_ack(cmd.cmd_id)
            if ack is None or int(ack["completed_status"]["status"]) == ac.NOT:
                return None
            return (
                int(ack["completed_status"]["status"]),
                ack.get("message", ""),
            )

        return ac.wait_final_ack(
            cmd.cmd_id,
            timeout_s,
            poll,
            self.transport.wait,
            stop,
            getattr(self, "abort_event", None),
            getattr(self, "abort_grace_s", 10.0),
            slice_s=0.2,
        )

    def grab_image(self, topic: str, out_path: str, timeout_s: float) -> bool:
        raw = self.transport.wait_image(topic, timeout_s)
        if raw is None:
            return False
        image = codec.image_from_dict(raw)
        ac.save_image_msg(
            image["width"],
            image["height"],
            image["encoding"],
            image["step"],
            image["data"],
            out_path,
        )
        return True

    def current_pose(self):
        return self.transport.latest_pose()


class RclpyTransport:
    """rclpy side. Imported only when the CLI is actually started with --backend ros2."""

    def __init__(self, connect_timeout_s: float):
        import rclpy
        from geometry_msgs.msg import PoseStamped
        from hci_ff_msgs.msg import AckStamped, CommandArg, CommandStamped
        from rclpy.qos import (
            DurabilityPolicy,
            HistoryPolicy,
            QoSProfile,
            ReliabilityPolicy,
        )
        from sensor_msgs.msg import Image
        from std_msgs.msg import Bool

        self._rclpy = rclpy
        self._CommandStamped = CommandStamped
        self._CommandArg = CommandArg
        if not rclpy.ok():
            rclpy.init()
        self.node = rclpy.create_node("crew_assist_ros2")
        self._qos = QoSProfile(
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
        self._acks = {}
        self._cv = threading.Condition()
        self._pose = None
        self._image = None
        self._ready = False
        self.pub = self.node.create_publisher(
            CommandStamped, codec.TOPIC_COMMAND, self._qos
        )
        self.node.create_subscription(
            AckStamped, codec.TOPIC_ACK, self._on_ack, self._qos
        )
        self.node.create_subscription(
            PoseStamped, codec.TOPIC_TRUTH, self._on_pose, self._qos
        )
        self.node.create_subscription(
            Image, codec.TOPIC_IMAGE, self._on_image, self._qos
        )
        self.node.create_subscription(
            Bool, codec.TOPIC_READY, self._on_ready, ready_qos
        )
        self._spin = threading.Thread(
            target=lambda: rclpy.spin(self.node), name="ros2-spin", daemon=True
        )
        self._spin.start()
        deadline = time.time() + connect_timeout_s
        while time.time() < deadline and not self.ready():
            time.sleep(0.05)

    def ready(self) -> bool:
        return bool(self._ready) and self.pub.get_subscription_count() > 0

    def _on_ready(self, msg) -> None:
        self._ready = bool(msg.data)

    def _on_ack(self, msg) -> None:
        with self._cv:
            self._acks[msg.cmd_id] = {
                "cmd_id": msg.cmd_id,
                "status": {"status": int(msg.status.status)},
                "completed_status": {"status": int(msg.completed_status.status)},
                "message": msg.message,
            }
            self._cv.notify_all()

    def _on_pose(self, msg) -> None:
        p, o = msg.pose.position, msg.pose.orientation
        self._pose = ([p.x, p.y, p.z], [o.x, o.y, o.z, o.w])

    def _on_image(self, msg) -> None:
        self._image = codec.image_to_dict(
            msg.width,
            msg.height,
            msg.encoding,
            msg.step,
            bytes(msg.data),
            msg.header.frame_id,
        )

    def publish_command(self, msg_dict: dict, t_send: float) -> None:
        del t_send  # the relay stamps the envelope; the ROS message stays a pure command
        message = self._CommandStamped()
        stamp = msg_dict["header"]["stamp"]
        message.header.stamp.sec = int(stamp["sec"])
        message.header.stamp.nanosec = int(stamp["nanosec"])
        command_fields.fill_command(message, msg_dict, self._CommandArg)
        self.pub.publish(message)

    def poll_ack(self, cmd_id: str) -> Optional[dict]:
        with self._cv:
            return self._acks.get(cmd_id)

    def wait(self, timeout_s: float) -> None:
        with self._cv:
            self._cv.wait(timeout_s)

    def latest_pose(self):
        return self._pose

    def wait_image(self, topic: str, timeout_s: float):
        del topic  # the relay exposes the nav camera on the fixed topic
        deadline = time.time() + timeout_s
        while self._image is None and time.time() < deadline:
            time.sleep(0.05)
        return self._image
