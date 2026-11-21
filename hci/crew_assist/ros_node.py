#!/usr/bin/env python3
"""ROS front end for the same crew-assist session the CLI uses.

Subscribes to ``crew_assist/intent`` (std_msgs/String) and runs the text
through IntentParser -> Sequencer. Publishes a JSON snapshot on
``crew_assist/status`` (latched). The browser buttons publish the same
strings the CLI accepts ("undock", "go to rack alpha", "return to dock",
"stop"). Stop is preemptive: it sends stopAllMotion and drops the queue.

  python3 ros_node.py --mock --yes -c undock -c "go to rack alpha" -c "return to dock"
  python3 ros_node.py                 # against a running sim

Command messages are still built in astrobee_cmd.py: cmd_origin stays empty,
the frame is world, and return-to-dock is approach-then-dock (never autoReturn).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import astrobee_cmd as ac  # noqa: E402
import hci_config  # noqa: E402
from intent_parser import IntentParser, load_locations  # noqa: E402
from sequencer import DEFAULT_TIMEOUTS, Sequencer  # noqa: E402
from session import CrewSession  # noqa: E402


class IntentBridge:
    """Thread-safe status snapshot around a CrewSession."""

    def __init__(self, max_lines: int = 40):
        self.session: Optional[CrewSession] = None
        self.max_lines = max_lines
        self.lines = []
        self._lock = threading.Lock()

    def echo(self, msg: str) -> None:
        print(msg, flush=True)
        with self._lock:
            self.lines.append(str(msg))
            del self.lines[: -self.max_lines]

    def status_dict(self) -> dict:
        session = self.session
        results = session.results if session is not None else []
        last = results[-1] if results else None
        with self._lock:
            lines = list(self.lines)
        return {
            "busy": bool(session.busy) if session is not None else False,
            "result_count": len(results),
            "last_ok": None if last is None else bool(last.ok),
            "last": "" if last is None else last.summary(),
            "lines": lines,
        }

    def handle_text(self, text: str):
        text = (text or "").strip()
        if not text or text.startswith("#"):
            return None
        if self.session is None:
            raise RuntimeError("session is not attached")
        return self.session.submit(text)


def make_runtime(
    locations: str,
    mock: bool,
    allow_todo: bool,
    image_dir: str,
    timeout_scale: float,
    mock_move_time: float,
):
    locs = load_locations(locations)
    parser = IntentParser(locs)
    if mock:
        backend = ac.MockBackend(move_time_s=mock_move_time)
    else:
        backend = ac.RosBackend(
            node_name="crew_assist",
            connect_timeout_s=60.0,
            disable_signals=False,
            anonymous=False,
        )
    commander = ac.AstrobeeCommander(backend)
    seq = Sequencer(
        commander,
        locs,
        image_dir=image_dir,
        allow_todo=allow_todo,
        timeouts={k: v * timeout_scale for k, v in DEFAULT_TIMEOUTS.items()},
    )
    bridge = IntentBridge()
    session = CrewSession(parser, seq, commander, confirm=None, echo=bridge.echo)
    bridge.session = session
    return session, bridge


def _configure_logging(verbose: bool, log_path: Optional[str]) -> None:
    lg = logging.getLogger("crew_assist")
    lg.setLevel(logging.DEBUG if verbose else logging.INFO)
    lg.propagate = False
    for handler in list(lg.handlers):
        lg.removeHandler(handler)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handlers = [logging.StreamHandler()]
    if log_path:
        handlers.append(logging.FileHandler(log_path))
    for handler in handlers:
        handler.setFormatter(fmt)
        lg.addHandler(handler)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--mock", action="store_true", help="simulate acks; no ROS")
    p.add_argument("--locations", default=os.path.join(HERE, "locations.yaml"))
    p.add_argument(
        "-c", "--command", action="append", help="mock/script: run these, then exit"
    )
    p.add_argument(
        "--yes",
        action="store_true",
        help="accepted for CLI compatibility; the node never prompts",
    )
    p.add_argument("--allow-todo", action="store_true")
    p.add_argument(
        "--image-dir",
        default=hci_config.image_dir(os.path.join(HERE, "images")),
    )
    p.add_argument("--log", default=None)
    p.add_argument("--timeout-scale", type=float, default=1.0)
    p.add_argument("--mock-move-time", type=float, default=0.0)
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    session, bridge = make_runtime(
        args.locations,
        args.mock,
        args.allow_todo,
        args.image_dir,
        args.timeout_scale,
        args.mock_move_time,
    )
    _configure_logging(args.verbose, args.log)

    if args.mock or args.command:
        ok = True
        for line in args.command or []:
            before = len(session.results)
            intent = session.submit(line)
            try:
                session.wait_idle()
            except KeyboardInterrupt:
                session.stop("Ctrl-C")
                ok = False
                break
            if intent is None or (
                intent.action != "stop"
                and (len(session.results) == before or not session.results[-1].ok)
            ):
                ok = False
                print("aborting remaining scripted commands", flush=True)
                break
        if not args.mock:
            # A real ROS node with -c still exits after the script. Callers that
            # want a long-running bridge omit -c.
            session.close()
            return 0 if ok else 1
        session.close()
        return 0 if ok else 1

    import rospy
    from std_msgs.msg import String

    pub = rospy.Publisher("crew_assist/status", String, queue_size=1, latch=True)

    def publish(_evt=None) -> None:
        pub.publish(String(data=json.dumps(bridge.status_dict())))

    def on_intent(msg) -> None:
        bridge.handle_text(msg.data)
        publish()

    rospy.Subscriber("crew_assist/intent", String, on_intent, queue_size=10)
    rospy.Timer(rospy.Duration(0.5), publish)
    rospy.on_shutdown(lambda: session.stop("node shutdown"))
    publish()
    print(
        "crew_assist ROS node. intent topic: crew_assist/intent  status: crew_assist/status",
        flush=True,
    )
    rospy.spin()
    session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
