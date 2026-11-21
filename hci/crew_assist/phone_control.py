"""Phone/web controller in front of the existing crew-assist session.

Motion is parsed, shown back to the operator, and queued only after Confirm.
stopAllMotion skips that read-back and the queue. If the primary backend
cannot publish the stop (the ROS 2 relay is down), a ROS 1 fallback publishes
the same command.
"""
from __future__ import annotations

import logging
import secrets
import socket
import threading
import time
from typing import Dict, Optional

import astrobee_cmd as ac
import bridge_codec as codec
from intent_parser import Intent, IntentParser, ParseError
from sequencer import Sequencer
from session import CrewSession

log = logging.getLogger("crew_assist.phone")

DEFAULT_HEARTBEAT_S = 2.0


def describe(intent: Intent) -> str:
    if intent.action == "undock":
        return "undock from the berth"
    if intent.action == "return_to_dock":
        return "fly to the dock approach, then dock (not autoReturn)"
    if intent.action == "go_to":
        name = (intent.target or "somewhere").replace("_", " ")
        return "fly to %s" % name
    if intent.action == "photograph":
        name = (intent.target or "somewhere").replace("_", " ")
        return "fly to %s and save one nav-camera picture" % name
    if intent.action == "stop":
        return "stop all motion now"
    return intent.action


def readback(heard: str, intent: Intent) -> str:
    return "Heard %s. About to %s." % (heard.strip(), describe(intent))


class FallbackBackend(ac.Backend):
    """Motion uses `primary`. stopAllMotion uses ROS 1 when primary cannot send."""

    def __init__(self, primary: ac.Backend, fallback: Optional[ac.Backend] = None):
        self.primary = primary
        self.fallback = fallback
        self.abort_event = None
        self.abort_grace_s = 10.0
        self.last_stop_via = None
        self.stop_published_at = None

    def _share_abort(self) -> None:
        for backend in (self.primary, self.fallback):
            if backend is None:
                continue
            backend.abort_event = self.abort_event
            backend.abort_grace_s = self.abort_grace_s

    def execute(self, cmd: ac.Command, timeout_s: float) -> ac.AckResult:
        self._share_abort()
        if cmd.cmd_name != ac.CMD_STOP_ALL_MOTION:
            return self.primary.execute(cmd, timeout_s)
        published = time.time()
        primary_result = None
        try:
            ready = getattr(self.primary, "ready", lambda: True)()
            if ready:
                primary_result = self.primary.execute(cmd, timeout_s)
        except Exception as exc:
            log.warning("primary stop failed: %s", exc)
            primary_result = ac.AckResult(
                cmd.cmd_id, ac.EXEC_FAILED, "not sent: %s" % exc
            )
        sent = primary_result is not None and "not sent" not in (
            primary_result.message or ""
        )
        if sent and primary_result.completed != ac.TIMEOUT:
            self.last_stop_via = "primary"
            self.stop_published_at = published
            return primary_result
        if self.fallback is None:
            self.last_stop_via = "primary"
            self.stop_published_at = published
            return primary_result or ac.AckResult(
                cmd.cmd_id, ac.EXEC_FAILED, "not sent: no stop path"
            )
        fallback_at = time.time()
        result = self.fallback.execute(cmd, timeout_s)
        self.last_stop_via = "ros1"
        self.stop_published_at = fallback_at
        return result

    def grab_image(self, topic: str, out_path: str, timeout_s: float) -> bool:
        return self.primary.grab_image(topic, out_path, timeout_s)

    def current_pose(self):
        return self.primary.current_pose()


class Ros1RelayStop(ac.Backend):
    """Publish one command by talking to the Noetic relay's TCP port.

    Used when the ROS 2 DDS relay is dead but the ROS 1 relay is still up.
    Only stopAllMotion is sent through this path.
    """

    def __init__(self, host: str = codec.BRIDGE_HOST, port: int = codec.BRIDGE_PORT):
        self.host = host
        self.port = port
        self.abort_event = None
        self.abort_grace_s = 10.0

    def execute(self, cmd: ac.Command, timeout_s: float) -> ac.AckResult:
        if cmd.cmd_name != ac.CMD_STOP_ALL_MOTION:
            return ac.AckResult(
                cmd.cmd_id, ac.EXEC_FAILED, "not sent: relay stop is stop-only"
            )
        frame = codec.encode_frame(
            {
                "op": "forward",
                "topic": codec.TOPIC_COMMAND,
                "t_send": time.time(),
                "msg": codec.command_to_dict(cmd),
            }
        )
        try:
            sock = socket.create_connection(
                (self.host, self.port), timeout=min(2.0, timeout_s)
            )
            try:
                sock.sendall(frame)
            finally:
                sock.close()
        except OSError as exc:
            return ac.AckResult(
                cmd.cmd_id, ac.EXEC_FAILED, "not sent: ros1 relay %s" % exc
            )
        return ac.AckResult(cmd.cmd_id, ac.OK, "published on ROS 1 relay")

    def grab_image(self, topic: str, out_path: str, timeout_s: float) -> bool:
        return False

    def current_pose(self):
        return None


class PhoneController:
    def __init__(
        self,
        session: CrewSession,
        parser: IntentParser,
        backend: ac.Backend,
        code: Optional[str] = None,
        heartbeat_s: float = DEFAULT_HEARTBEAT_S,
    ):
        self.session = session
        self.parser = parser
        self.backend = backend
        self.code = code or "%06d" % secrets.randbelow(1000000)
        self.heartbeat_s = heartbeat_s
        self._lock = threading.Lock()
        self.clients: Dict[str, dict] = {}
        self.controller_id: Optional[str] = None
        self.last_beat = 0.0
        self.previews: Dict[str, Intent] = {}
        self.camera_png: Optional[bytes] = None
        self.last_dead_man_s: Optional[float] = None
        self.dead_man_count = 0
        self._closed = threading.Event()
        self._watch = threading.Thread(
            target=self._watchdog, name="phone-deadman", daemon=True
        )
        self._watch.start()

    def close(self) -> None:
        self._closed.set()
        self._watch.join(1.0)
        self.session.close()

    def check_code(self, code: str) -> bool:
        if not code or len(code) != len(self.code):
            return False
        return secrets.compare_digest(code, self.code)

    def claim(self, client_id: Optional[str] = None) -> dict:
        with self._lock:
            now = time.time()
            if self.controller_id and self.controller_id != client_id:
                alive = now - self.last_beat <= self.heartbeat_s
                if alive:
                    raise PermissionError("another controller is active")
            cid = client_id or secrets.token_hex(8)
            self.clients[cid] = {"since": now}
            self.controller_id = cid
            self.last_beat = now
            return {"client_id": cid, "role": "controller"}

    def release(self, client_id: str) -> None:
        with self._lock:
            if self.controller_id == client_id:
                self.controller_id = None

    def beat(self, client_id: str) -> None:
        with self._lock:
            if client_id and client_id == self.controller_id:
                self.last_beat = time.time()

    def role_of(self, client_id: str) -> str:
        with self._lock:
            if client_id and client_id == self.controller_id:
                return "controller"
            return "viewer"

    def preview(self, client_id: str, text: str) -> dict:
        if self.role_of(client_id) != "controller":
            raise PermissionError("view only")
        try:
            intent = self.parser.parse(text)
        except ParseError as exc:
            raise ValueError(str(exc)) from exc
        if intent.action == "stop":
            raise ValueError("stop is immediate; use the STOP button")
        preview_id = secrets.token_hex(8)
        with self._lock:
            self.previews[preview_id] = intent
        return {
            "preview_id": preview_id,
            "heard": text.strip(),
            "about": describe(intent),
            "readback": readback(text, intent),
            "action": intent.action,
            "target": intent.target,
        }

    def confirm(self, client_id: str, preview_id: str) -> dict:
        if self.role_of(client_id) != "controller":
            raise PermissionError("view only")
        with self._lock:
            intent = self.previews.pop(preview_id, None)
        if intent is None:
            raise KeyError("preview expired")
        self.session.enqueue(intent)
        return {"ok": True, "action": intent.action, "target": intent.target}

    def cancel_preview(self, client_id: str, preview_id: str) -> None:
        if self.role_of(client_id) != "controller":
            raise PermissionError("view only")
        with self._lock:
            self.previews.pop(preview_id, None)

    def stop(self, t_tap: Optional[float] = None, why: str = "phone stop") -> dict:
        entered = time.time()
        result = self.session.stop(why)
        published = getattr(self.backend, "stop_published_at", None) or entered
        out = {
            "ok": True,
            "ack": result.name,
            "message": result.message,
            "via": getattr(self.backend, "last_stop_via", "primary"),
            "server_to_publish_ms": (published - entered) * 1000.0,
        }
        if t_tap is not None:
            out["tap_to_publish_ms"] = published * 1000.0 - float(t_tap)
        return out

    def status(self, client_id: str) -> dict:
        self.beat(client_id)
        pose = self.backend.current_pose()
        last = self.session.results[-1] if self.session.results else None
        lines = []
        return {
            "role": self.role_of(client_id),
            "busy": self.session.busy,
            "pose": None
            if pose is None
            else {"position": list(pose[0]), "quaternion": list(pose[1])},
            "last": None if last is None else last.summary(),
            "last_ok": None if last is None else bool(last.ok),
            "lines": lines,
            "dead_man_s": self.last_dead_man_s,
            "dead_man_count": self.dead_man_count,
        }

    def note_lines(self, lines) -> None:
        """Optional echo buffer supplied by the HTTP layer."""
        self._lines = lines

    def _watchdog(self) -> None:
        while not self._closed.wait(0.05):
            with self._lock:
                cid = self.controller_id
                beat = self.last_beat
            if not cid:
                continue
            gap = time.time() - beat
            if gap <= self.heartbeat_s:
                continue
            with self._lock:
                if self.controller_id != cid:
                    continue
                self.controller_id = None
            if self.session.busy:
                self.last_dead_man_s = gap
                self.dead_man_count += 1
                log.warning("dead-man after %.2fs", gap)
                self.stop(why="dead-man")


def make_session(backend: ac.Backend, locations: dict, image_dir: str = "images"):
    parser = IntentParser(locations)
    commander = ac.AstrobeeCommander(backend)
    sequencer = Sequencer(commander, locations, image_dir=image_dir)
    echoes = []

    def echo(msg: str) -> None:
        echoes.append(msg)
        print(msg, flush=True)

    session = CrewSession(parser, sequencer, commander, confirm=None, echo=echo)
    return parser, session, echoes
