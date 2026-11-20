#!/usr/bin/env python3
"""ROS 1 end of the HCI bridge. Runs next to the Noetic sim.

Forwards /mgt/ack, /loc/truth/pose, /loc/pose, and /hw/cam_nav to the Jazzy
relay, and publishes /command messages that arrive from it. The executive is
unchanged. This process is the only new ROS 1 node.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import time

import bridge_codec as codec

log = logging.getLogger("hci.ros1_relay")
HOST = os.environ.get("HCI_BRIDGE_HOST", codec.BRIDGE_HOST)
PORT = int(os.environ.get("HCI_BRIDGE_PORT", str(codec.BRIDGE_PORT)))


class Relay:
    def __init__(self) -> None:
        import rospy
        from ff_msgs.msg import AckStamped, CommandArg, CommandStamped
        from geometry_msgs.msg import PoseStamped
        from sensor_msgs.msg import Image

        self.rospy = rospy
        self.CommandStamped = CommandStamped
        self.CommandArg = CommandArg
        if not rospy.core.is_initialized():
            rospy.init_node("hci_ros1_relay", anonymous=False, disable_signals=False)
        self.pub = rospy.Publisher("command", CommandStamped, queue_size=10)
        self._clients = []
        self._lock = threading.Lock()
        self._next_pose = 0.0
        rospy.Subscriber("mgt/ack", AckStamped, self._on_ack, queue_size=50)
        rospy.Subscriber(
            "loc/truth/pose",
            PoseStamped,
            self._on_pose,
            callback_args=codec.TOPIC_TRUTH,
            queue_size=1,
        )
        rospy.Subscriber(
            "loc/pose",
            PoseStamped,
            self._on_pose,
            callback_args=codec.TOPIC_POSE,
            queue_size=1,
        )
        rospy.Subscriber("hw/cam_nav", Image, self._on_image, queue_size=1)
        self._status_timer = rospy.Timer(rospy.Duration(1.0), self._status)

    def _send(self, obj: dict) -> None:
        frame = codec.encode_frame(obj)
        with self._lock:
            dead = []
            for sock in self._clients:
                try:
                    sock.sendall(frame)
                except OSError:
                    dead.append(sock)
            for sock in dead:
                self._clients.remove(sock)
                sock.close()

    def _on_ack(self, msg) -> None:
        self._send(
            {
                "op": "forward",
                "topic": codec.TOPIC_ACK,
                "t_send": time.time(),
                "msg": {
                    "header": {
                        "stamp": {
                            "sec": int(msg.header.stamp.secs),
                            "nanosec": int(msg.header.stamp.nsecs),
                        },
                        "frame_id": msg.header.frame_id,
                    },
                    "cmd_id": msg.cmd_id,
                    "status": {"status": int(msg.status.status)},
                    "completed_status": {"status": int(msg.completed_status.status)},
                    "message": msg.message,
                },
            }
        )

    def _on_pose(self, msg, topic: str) -> None:
        now = time.time()
        if topic == codec.TOPIC_TRUTH:
            if now < self._next_pose:
                return
            self._next_pose = now + 0.2
        elif now < getattr(self, "_next_loc", 0.0):
            return
        else:
            self._next_loc = now + 0.2
        p, o = msg.pose.position, msg.pose.orientation
        self._send(
            {
                "op": "forward",
                "topic": topic,
                "t_send": now,
                "msg": codec.pose_to_dict(
                    [p.x, p.y, p.z], [o.x, o.y, o.z, o.w], msg.header.frame_id
                ),
            }
        )

    def _on_image(self, msg) -> None:
        self._send(
            {
                "op": "forward",
                "topic": codec.TOPIC_IMAGE,
                "t_send": time.time(),
                "msg": codec.image_to_dict(
                    msg.width,
                    msg.height,
                    msg.encoding,
                    msg.step,
                    bytes(msg.data),
                    msg.header.frame_id,
                ),
            }
        )

    def _status(self, _evt=None) -> None:
        self._send(
            {
                "op": "status",
                "t_send": time.time(),
                "command_subscribers": int(self.pub.get_num_connections()),
            }
        )

    def handle_client(self, sock: socket.socket) -> None:
        buf = codec.FrameBuffer()
        try:
            while not self.rospy.is_shutdown():
                data = sock.recv(65536)
                if not data:
                    break
                for frame in buf.feed(data):
                    self._on_frame(frame)
        except (OSError, ValueError) as exc:
            log.warning("client closed: %s", exc)
        finally:
            with self._lock:
                if sock in self._clients:
                    self._clients.remove(sock)
            sock.close()

    def _on_frame(self, frame: dict) -> None:
        if frame.get("op") != "forward" or frame.get("topic") != codec.TOPIC_COMMAND:
            return
        raw = frame["msg"]
        t_send = float(frame.get("t_send", time.time()))
        msg = self.CommandStamped()
        stamp = raw["header"]["stamp"]
        msg.header.stamp = self.rospy.Time(int(stamp["sec"]), int(stamp["nanosec"]))
        msg.header.frame_id = raw["header"].get("frame_id", "")
        msg.cmd_name = raw["cmd_name"]
        msg.cmd_id = raw["cmd_id"]
        msg.cmd_src = raw["cmd_src"]
        msg.cmd_origin = raw["cmd_origin"]
        msg.subsys_name = raw["subsys_name"]
        for item in raw.get("args", []):
            arg = self.CommandArg()
            arg.data_type = int(item["data_type"])
            arg.b = bool(item["b"])
            arg.d = float(item["d"])
            arg.f = float(item["f"])
            arg.i = int(item["i"])
            arg.ll = int(item["ll"])
            arg.s = str(item["s"])
            arg.vec3d = [float(v) for v in item["vec3d"]]
            arg.mat33f = [float(v) for v in item["mat33f"]]
            msg.args.append(arg)
        self.pub.publish(msg)
        self._send(
            {
                "op": "stat",
                "cmd_id": raw["cmd_id"],
                "cmd_bridge_ms": (time.time() - t_send) * 1000.0,
            }
        )
        log.info(
            "bridged %s id=%s origin=%r frame-arg-present=%s",
            raw["cmd_name"],
            raw["cmd_id"],
            raw["cmd_origin"],
            bool(raw.get("args")),
        )

    def serve(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((HOST, PORT))
        server.listen(2)
        server.settimeout(1.0)
        log.info("listening on %s:%d", HOST, PORT)
        while not self.rospy.is_shutdown():
            try:
                sock, _addr = server.accept()
            except socket.timeout:
                continue
            # accept() copies the listening socket's 1s timeout. A quiet second
            # is not a dead client; pose and camera traffic can hitch.
            sock.settimeout(None)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            with self._lock:
                self._clients.append(sock)
            threading.Thread(
                target=self.handle_client, args=(sock,), daemon=True
            ).start()
            self._status()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    relay = Relay()
    try:
        relay.serve()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
