"""Thin wrapper around the Astrobee Command API (ff_msgs/CommandStamped).

Ported from nasa/astrobee management/executive/tools/simple_move.cc and
teleop_tool.cc (master bf43a42, Dec 2025). Verified against
communications/ff_msgs/msg/{CommandStamped,CommandArg,AckStamped,AckStatus,
AckCompletedStatus,CommandConstants}.msg.

Two backends share one interface:
  * RosBackend  - publishes on `command`, listens on `mgt/ack` (needs rospy +
                  ff_msgs, i.e. a sourced Astrobee workspace on Ubuntu 20.04).
  * MockBackend - pure Python, simulates acks so logic can be tested anywhere.

Python 3.8 compatible (the Astrobee container ships 3.8.10).
"""
from __future__ import annotations

import itertools
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

log = logging.getLogger("crew_assist.cmd")

# --- CommandConstants.msg (short tokens, not "Mobility.*") -----------------
CMD_UNDOCK = "undock"
CMD_DOCK = "dock"
CMD_SIMPLE_MOVE6DOF = "simpleMove6DOF"
CMD_STOP_ALL_MOTION = "stopAllMotion"
CMD_SET_CAMERA = "setCamera"
CMD_SET_CAMERA_STREAMING = "setCameraStreaming"
CMD_SET_CAMERA_RECORDING = "setCameraRecording"
# Deliberately NOT exposed: autoReturn (undertested; always berth 1).

CAMERA_NAMES = ("Science", "Navigation", "Hazard", "Dock", "Perch")

# --- CommandArg.msg data types ---------------------------------------------
DATA_TYPE_BOOL = 0
DATA_TYPE_DOUBLE = 1
DATA_TYPE_FLOAT = 2
DATA_TYPE_INT = 3
DATA_TYPE_LONGLONG = 4
DATA_TYPE_STRING = 5
DATA_TYPE_VEC3d = 6
DATA_TYPE_MAT33f = 7

# --- AckStatus.msg ----------------------------------------------------------
ACK_QUEUED, ACK_EXECUTING, ACK_REQUEUED, ACK_COMPLETED = 0, 1, 2, 3
# --- AckCompletedStatus.msg -------------------------------------------------
NOT, OK, BAD_SYNTAX, EXEC_FAILED, CANCELED = 0, 1, 2, 3, 4
COMPLETED_NAMES = {
    NOT: "NOT",
    OK: "OK",
    BAD_SYNTAX: "BAD_SYNTAX",
    EXEC_FAILED: "EXEC_FAILED",
    CANCELED: "CANCELED",
}
TIMEOUT = -1  # local pseudo-status: no final ack before the deadline

CMD_SRC = "guest_science"
# NOTE: cmd_origin is left EMPTY on purpose. executive.cc CmdCallback rejects
# cmd_origin == "guest_science" with EXEC_FAILED unless a primary APK runs.
SUBSYS_NAME = "Astrobee"


@dataclass
class Arg:
    data_type: int
    value: Any


@dataclass
class Command:
    cmd_name: str
    cmd_id: str
    args: List[Arg] = field(default_factory=list)
    cmd_src: str = CMD_SRC
    cmd_origin: str = ""
    subsys_name: str = SUBSYS_NAME


@dataclass
class AckResult:
    cmd_id: str
    completed: int  # AckCompletedStatus value or TIMEOUT
    message: str = ""
    elapsed_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.completed == OK

    @property
    def name(self) -> str:
        return (
            "TIMEOUT"
            if self.completed == TIMEOUT
            else COMPLETED_NAMES.get(self.completed, str(self.completed))
        )


# --- geometry helpers ---------------------------------------------------------
def axis_angle_to_quat(
    angle: float, axis: Sequence[float]
) -> Tuple[float, float, float, float]:
    """teleop_tool -att "angle ax ay az" -> (x, y, z, w), like Eigen::AngleAxisd.

    Eigen does not normalise the axis inside Quaterniond(AngleAxisd); we
    normalise so a sloppy axis cannot produce a non-unit quaternion.
    """
    ax, ay, az = axis
    n = math.sqrt(ax * ax + ay * ay + az * az)
    if n == 0:
        return (0.0, 0.0, 0.0, 1.0)
    s = math.sin(0.5 * angle) / n
    return (ax * s, ay * s, az * s, math.cos(0.5 * angle))


def normalize_quat(q: Sequence[float]) -> Tuple[float, float, float, float]:
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w)
    if n == 0:
        raise ValueError("zero quaternion")
    return (x / n, y / n, z / n, w / n)


# --- command builders (exact arg layout from simple_move.cc / teleop_tool.cc)
_counter = itertools.count(1)


def new_cmd_id(name: str) -> str:
    # simple_move.cc uses "guest_science" + stamp.sec; add a counter so two
    # commands in the same second never collide.
    return "%s_%s_%d_%d" % (CMD_SRC, name, int(time.time()), next(_counter))


def build_undock() -> Command:
    return Command(CMD_UNDOCK, new_cmd_id(CMD_UNDOCK))


def build_dock(berth: int = 1) -> Command:
    if berth not in (1, 2):
        raise ValueError("berth must be 1 (left) or 2 (right)")
    return Command(CMD_DOCK, new_cmd_id(CMD_DOCK), [Arg(DATA_TYPE_INT, int(berth))])


def build_stop() -> Command:
    return Command(CMD_STOP_ALL_MOTION, new_cmd_id(CMD_STOP_ALL_MOTION))


def build_simple_move(
    pos: Sequence[float], quat: Sequence[float], frame: str = "world"
) -> Command:
    """args: [0] frame string, [1] xyz vec3d, [2] tolerance vec3d (unused, 0),
    [3] mat33f with quaternion x,y,z,w in the first four slots, rest 0.
    Frame: "world" (absolute, as teleop/simple_move send) or "body" (relative).
    executive.cc maps "ISS" -> world, so "ISS" also works."""
    if len(pos) != 3:
        raise ValueError("pos needs x y z")
    q = normalize_quat(quat)
    mat = [float(q[0]), float(q[1]), float(q[2]), float(q[3]), 0.0, 0.0, 0.0, 0.0, 0.0]
    return Command(
        CMD_SIMPLE_MOVE6DOF,
        new_cmd_id(CMD_SIMPLE_MOVE6DOF),
        [
            Arg(DATA_TYPE_STRING, frame),
            Arg(DATA_TYPE_VEC3d, [float(v) for v in pos]),
            Arg(DATA_TYPE_VEC3d, [0.0, 0.0, 0.0]),
            Arg(DATA_TYPE_MAT33f, mat),
        ],
    )


def build_set_camera_streaming(camera: str, enable: bool) -> Command:
    """executive.cc SetCameraStreaming: args [string camera, bool enable]."""
    if camera not in CAMERA_NAMES:
        raise ValueError("camera must be one of %s" % (CAMERA_NAMES,))
    return Command(
        CMD_SET_CAMERA_STREAMING,
        new_cmd_id(CMD_SET_CAMERA_STREAMING),
        [Arg(DATA_TYPE_STRING, camera), Arg(DATA_TYPE_BOOL, bool(enable))],
    )


# --- backends ---------------------------------------------------------------
class Backend:
    """Interface: send a command and block until its final ack or timeout."""

    def execute(self, cmd: Command, timeout_s: float) -> AckResult:  # pragma: no cover
        raise NotImplementedError

    def grab_image(
        self, topic: str, out_path: str, timeout_s: float
    ) -> bool:  # pragma: no cover
        raise NotImplementedError

    def current_pose(self) -> Optional[Tuple[List[float], List[float]]]:
        return None


class AstrobeeCommander:
    """Sends one command at a time; on TIMEOUT it issues stopAllMotion.

    `stop()` may be called from ANY thread while `run()` is blocked waiting for
    an ack (typed "stop", Ctrl-C, voice). It sets `abort_event` so:
      * the in-flight wait ends (executive acks CANCELED, or a local CANCELED
        after `abort_grace_s` if no ack arrives), and
      * no further commands of the current sequence are sent until
        `clear_abort()` (called at the start of each new intent).
    """

    def __init__(
        self,
        backend: Backend,
        stop_timeout_s: float = 15.0,
        abort_grace_s: float = 10.0,
    ):
        self.backend = backend
        self.stop_timeout_s = stop_timeout_s
        self._lock = threading.Lock()  # serialises normal commands
        self._hist_lock = threading.Lock()
        self.abort_event = threading.Event()
        backend.abort_event = self.abort_event
        backend.abort_grace_s = abort_grace_s
        self.history: List[Tuple[Command, AckResult]] = []

    def _record(self, cmd: Command, res: AckResult) -> None:
        with self._hist_lock:
            self.history.append((cmd, res))

    def clear_abort(self) -> None:
        self.abort_event.clear()

    def run(self, cmd: Command, timeout_s: float) -> AckResult:
        with self._lock:
            if self.abort_event.is_set():
                res = AckResult(cmd.cmd_id, CANCELED, "not sent: stop requested")
                log.info("SKIP %s (stop requested)", cmd.cmd_name)
                self._record(cmd, res)
                return res
            log.info(
                "SEND %s id=%s args=%s",
                cmd.cmd_name,
                cmd.cmd_id,
                [(a.data_type, a.value) for a in cmd.args],
            )
            res = self.backend.execute(cmd, timeout_s)
            log.info(
                "ACK  %s id=%s -> %s %s (%.1fs)",
                cmd.cmd_name,
                cmd.cmd_id,
                res.name,
                res.message,
                res.elapsed_s,
            )
            self._record(cmd, res)
            if res.completed == TIMEOUT and not self.abort_event.is_set():
                log.warning("timeout on %s -> sending stopAllMotion", cmd.cmd_name)
                self._send_stop()
            return res

    def _send_stop(self) -> AckResult:
        stop = build_stop()
        log.info("SEND stopAllMotion id=%s", stop.cmd_id)
        res = self.backend.execute(stop, self.stop_timeout_s)
        log.info(
            "ACK  stopAllMotion id=%s -> %s %s (%.1fs)",
            stop.cmd_id,
            res.name,
            res.message,
            res.elapsed_s,
        )
        self._record(stop, res)
        return res

    def stop(self) -> AckResult:
        """Preempting stop: does NOT take the command lock."""
        self.abort_event.set()
        return self._send_stop()


class RosBackend(Backend):
    """Real backend. Topic names are relative so `ROS_NAMESPACE=honey` works."""

    def __init__(
        self,
        node_name: str = "crew_assist",
        connect_timeout_s: float = 10.0,
        disable_signals: bool = True,
        anonymous: bool = True,
    ):
        import rospy  # noqa: deferred so mock mode needs no ROS
        from ff_msgs.msg import AckStamped, CommandStamped

        self.rospy = rospy
        self.CommandStamped = CommandStamped
        if not rospy.core.is_initialized():
            # disable_signals stays True for the CLI, which treats Ctrl-C as
            # stopAllMotion instead of process death. The long-running ROS node
            # passes False so docker stop / Ctrl-C can shut the process down.
            rospy.init_node(
                node_name, anonymous=anonymous, disable_signals=disable_signals
            )
        self._acks: Dict[str, Any] = {}
        self._cv = threading.Condition()
        self.pub = rospy.Publisher("command", CommandStamped, queue_size=10)
        self.sub = rospy.Subscriber("mgt/ack", AckStamped, self._on_ack, queue_size=50)
        deadline = time.time() + connect_timeout_s
        while self.pub.get_num_connections() == 0 and time.time() < deadline:
            time.sleep(0.1)
        if self.pub.get_num_connections() == 0:
            raise RuntimeError("no subscriber on `command` - is the executive running?")
        self._pose = None
        try:
            from geometry_msgs.msg import PoseStamped

            rospy.Subscriber("loc/pose", PoseStamped, self._on_pose, queue_size=1)
        except Exception:  # pragma: no cover
            pass

    def _on_pose(self, msg):
        p, o = msg.pose.position, msg.pose.orientation
        self._pose = ([p.x, p.y, p.z], [o.x, o.y, o.z, o.w])

    def current_pose(self):
        return self._pose

    def _on_ack(self, ack):
        with self._cv:
            self._acks[ack.cmd_id] = ack
            self._cv.notify_all()

    def _to_msg(self, cmd: Command):
        from ff_msgs.msg import CommandArg

        m = self.CommandStamped()
        m.header.stamp = self.rospy.Time.now()
        m.cmd_name, m.cmd_id = cmd.cmd_name, cmd.cmd_id
        m.cmd_src, m.cmd_origin, m.subsys_name = (
            cmd.cmd_src,
            cmd.cmd_origin,
            cmd.subsys_name,
        )
        for a in cmd.args:
            ca = CommandArg()
            ca.data_type = a.data_type
            if a.data_type == DATA_TYPE_BOOL:
                ca.b = bool(a.value)
            elif a.data_type == DATA_TYPE_DOUBLE:
                ca.d = float(a.value)
            elif a.data_type == DATA_TYPE_FLOAT:
                ca.f = float(a.value)
            elif a.data_type == DATA_TYPE_INT:
                ca.i = int(a.value)
            elif a.data_type == DATA_TYPE_LONGLONG:
                ca.ll = int(a.value)
            elif a.data_type == DATA_TYPE_STRING:
                ca.s = str(a.value)
            elif a.data_type == DATA_TYPE_VEC3d:
                ca.vec3d = list(a.value)
            elif a.data_type == DATA_TYPE_MAT33f:
                ca.mat33f = list(a.value)
            m.args.append(ca)
        return m

    def execute(self, cmd: Command, timeout_s: float) -> AckResult:
        t0 = time.time()
        with self._cv:
            self._acks.pop(cmd.cmd_id, None)
        self.pub.publish(self._to_msg(cmd))
        if cmd.cmd_name == CMD_STOP_ALL_MOTION:
            self.stop_published_at = time.time()
        deadline = t0 + timeout_s
        last_state = None
        abort_t = None
        with self._cv:
            while True:
                ack = self._acks.get(cmd.cmd_id)
                if ack is not None:
                    state = (ack.status.status, ack.completed_status.status)
                    if state != last_state:
                        log.debug(
                            "ack %s status=%d completed=%d %s",
                            cmd.cmd_id,
                            state[0],
                            state[1],
                            ack.message,
                        )
                        last_state = state
                    # NOT (0) means still in progress; anything else is final.
                    if ack.completed_status.status != NOT:
                        return AckResult(
                            cmd.cmd_id,
                            ack.completed_status.status,
                            ack.message,
                            time.time() - t0,
                        )
                ab = getattr(self, "abort_event", None)
                if (
                    ab is not None
                    and ab.is_set()
                    and cmd.cmd_name != CMD_STOP_ALL_MOTION
                ):
                    if abort_t is None:
                        abort_t = time.time()
                    elif time.time() - abort_t > getattr(self, "abort_grace_s", 10.0):
                        return AckResult(
                            cmd.cmd_id,
                            CANCELED,
                            "no ack after stop; " "abandoned locally",
                            time.time() - t0,
                        )
                remaining = deadline - time.time()
                if remaining <= 0 or self.rospy.is_shutdown():
                    return AckResult(
                        cmd.cmd_id,
                        TIMEOUT,
                        "no final ack in %.0fs" % timeout_s,
                        time.time() - t0,
                    )
                self._cv.wait(min(remaining, 0.5))

    def grab_image(self, topic: str, out_path: str, timeout_s: float) -> bool:
        from sensor_msgs.msg import Image

        try:
            msg = self.rospy.wait_for_message(topic, Image, timeout=timeout_s)
        except Exception as e:  # ROSException on timeout
            log.warning("no image on %s within %.0fs: %s", topic, timeout_s, e)
            return False
        save_image_msg(
            msg.width, msg.height, msg.encoding, msg.step, bytes(msg.data), out_path
        )
        return True


class MockBackend(Backend):
    """Simulates the executive closely enough to exercise sequencing logic.

    Models: docked/undocked state, "can't move while docked", "can't stop while
    docked", dock only from near the approach point, configurable per-command
    failures or hangs (to test timeout -> stopAllMotion)."""

    def __init__(
        self,
        start_pose=None,
        approach_pos=(10.34, -9.51, 4.49),
        fail: Optional[Dict[str, int]] = None,
        hang: Sequence[str] = (),
        latency_s: float = 0.0,
        move_time_s: float = 0.0,
    ):
        self.docked = True
        self.move_time_s = (
            move_time_s  # >0: moves/undock/dock take this long, stop cancels
        )
        self.moving = threading.Event()
        self._cancel = threading.Event()
        self._sent_lock = threading.Lock()
        self.pos = list(start_pose or [9.816, -9.806, 4.293])
        self.quat = [0.0, 0.0, 0.0, 1.0]
        self.approach_pos = list(approach_pos)
        self.fail = dict(fail or {})
        self.hang = set(hang)
        self.latency_s = latency_s
        self.sent: List[Command] = []
        self.images: List[str] = []

    def current_pose(self):
        return (list(self.pos), list(self.quat))

    def _travel(self, cmd: Command) -> Optional[AckResult]:
        """Simulated motion time; returns CANCELED if stopAllMotion arrives."""
        if self.move_time_s <= 0:
            return None
        self._cancel.clear()
        self.moving.set()
        try:
            if self._cancel.wait(self.move_time_s):
                return AckResult(
                    cmd.cmd_id, CANCELED, "mock: canceled by stopAllMotion"
                )
        finally:
            self.moving.clear()
        return None

    def execute(self, cmd: Command, timeout_s: float) -> AckResult:
        with self._sent_lock:
            self.sent.append(cmd)
        if self.latency_s:
            time.sleep(self.latency_s)
        n = cmd.cmd_name
        if (
            n in (CMD_UNDOCK, CMD_SIMPLE_MOVE6DOF, CMD_DOCK)
            and n not in self.hang
            and n not in self.fail
        ):
            canceled = self._travel(cmd)
            if canceled is not None:
                if n == CMD_UNDOCK:
                    self.docked = False
                return canceled
        if n in self.hang:
            return AckResult(cmd.cmd_id, TIMEOUT, "mock hang", timeout_s)
        if n in self.fail:
            return AckResult(cmd.cmd_id, self.fail.pop(n), "mock injected failure")
        if n == CMD_UNDOCK:
            if not self.docked:
                return AckResult(cmd.cmd_id, EXEC_FAILED, "Astrobee is not docked.")
            self.docked = False
            self.pos = [9.816 + 0.5, -9.806, 4.293]  # roughly undock point
            return AckResult(cmd.cmd_id, OK)
        if n == CMD_STOP_ALL_MOTION:
            self._cancel.set()
            if self.docked:
                return AckResult(
                    cmd.cmd_id, EXEC_FAILED, "Astrobee cannot stop while docked."
                )
            return AckResult(cmd.cmd_id, OK)
        if n == CMD_SIMPLE_MOVE6DOF:
            if self.docked:
                return AckResult(
                    cmd.cmd_id, EXEC_FAILED, "Astrobee cannot move while docked."
                )
            if len(cmd.args) != 4 or cmd.args[3].data_type != DATA_TYPE_MAT33f:
                return AckResult(cmd.cmd_id, BAD_SYNTAX, "Malformed arguments")
            self.pos = list(cmd.args[1].value)
            self.quat = list(cmd.args[3].value[:4])
            return AckResult(cmd.cmd_id, OK)
        if n == CMD_DOCK:
            if self.docked:
                return AckResult(cmd.cmd_id, EXEC_FAILED, "Already docked.")
            d = (
                math.dist(self.pos, self.approach_pos)
                if hasattr(math, "dist")
                else math.sqrt(
                    sum((a - b) ** 2 for a, b in zip(self.pos, self.approach_pos))
                )
            )
            if d > 0.5:
                return AckResult(
                    cmd.cmd_id, EXEC_FAILED, "Not near dock approach point."
                )
            self.docked = True
            self.pos = [9.816, -9.806, 4.293]
            return AckResult(cmd.cmd_id, OK)
        if n == CMD_SET_CAMERA_STREAMING:
            return AckResult(cmd.cmd_id, OK)
        return AckResult(cmd.cmd_id, BAD_SYNTAX, "mock: unknown command %s" % n)

    def grab_image(self, topic: str, out_path: str, timeout_s: float) -> bool:
        # 8x8 gradient so the PNG path is exercised too
        w = h = 8
        data = bytes((x * 32) % 256 for _ in range(h) for x in range(w))
        save_image_msg(w, h, "mono8", w, data, out_path)
        self.images.append(out_path)
        return True


# --- tiny dependency-free PNG writer (mono8 / rgb8 / bgr8) -------------------
def png_bytes(width: int, height: int, encoding: str, step: int, data: bytes) -> bytes:
    import struct
    import zlib

    enc = encoding.lower()
    if enc in ("mono8", "8uc1"):
        ch, ctype = 1, 0
    elif enc in ("rgb8", "bgr8", "8uc3"):
        ch, ctype = 3, 2
    else:
        raise ValueError("unsupported encoding %s" % encoding)
    raw = bytearray()
    for r in range(height):
        row = data[r * step : r * step + width * ch]
        if enc == "bgr8":
            row = bytes(
                b
                for i in range(0, len(row), 3)
                for b in (row[i + 2], row[i + 1], row[i])
            )
        raw += b"\x00" + row

    def chunk(t: bytes, d: bytes) -> bytes:
        return (
            struct.pack(">I", len(d))
            + t
            + d
            + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, ctype, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )
    return png


def save_image_msg(
    width: int, height: int, encoding: str, step: int, data: bytes, out_path: str
) -> None:
    png = png_bytes(width, height, encoding, step, data)
    with open(out_path, "wb") as handle:
        handle.write(png)
