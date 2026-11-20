#!/usr/bin/env python3
"""Typed crew-assist command loop.

  python3 cli.py --mock                 # no ROS needed
  python3 cli.py                        # ROS 1, against a running sim
  python3 cli.py --backend ros2         # ROS 2 Jazzy, through the HCI bridge
  python3 cli.py --mock -c "undock" -c "go to mid jem" -c "return to dock" --yes
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import astrobee_cmd as ac  # noqa: E402
from intent_parser import IntentParser, load_locations  # noqa: E402
from sequencer import Sequencer  # noqa: E402
from session import CrewSession  # noqa: E402


def _backend(args):
    if args.mock and args.backend not in (None, "mock"):
        raise SystemExit("--mock cannot be combined with --backend %s" % args.backend)
    if args.mock or args.backend == "mock":
        return ac.MockBackend(move_time_s=args.mock_move_time)
    if args.backend == "ros2":
        from ros2_backend import Ros2Backend

        return Ros2Backend()
    return ac.RosBackend()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--mock", action="store_true", help="simulate acks; no ROS")
    p.add_argument(
        "--backend",
        choices=("ros1", "ros2", "mock"),
        default=None,
        help="ros1 (default on a Noetic shell), ros2 (Jazzy bridge), or mock",
    )
    p.add_argument("--locations", default=os.path.join(HERE, "locations.yaml"))
    p.add_argument(
        "-c", "--command", action="append", help="run these commands then exit"
    )
    p.add_argument("--yes", action="store_true", help="do not ask before motion")
    p.add_argument(
        "--allow-todo", action="store_true", help="allow TODO placeholder poses"
    )
    p.add_argument("--image-dir", default=os.path.join(HERE, "images"))
    p.add_argument("--log", default=None, help="also write the log to this file")
    p.add_argument("--timeout-scale", type=float, default=1.0)
    p.add_argument(
        "--mock-move-time",
        type=float,
        default=0.0,
        help="mock only: seconds each undock/move/dock takes (stop cancels it)",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args(argv)

    locs = load_locations(a.locations)
    parser = IntentParser(locs)
    backend = _backend(a)
    # Configure our own logger AFTER rospy.init_node (rospy rewires root logging).
    lg = logging.getLogger("crew_assist")
    lg.setLevel(logging.DEBUG if a.verbose else logging.INFO)
    lg.propagate = False
    for h in list(lg.handlers):
        lg.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handlers = [logging.StreamHandler()]
    if a.log:
        handlers.append(logging.FileHandler(a.log))
    for h in handlers:
        h.setFormatter(fmt)
        lg.addHandler(h)

    commander = ac.AstrobeeCommander(backend)
    from sequencer import DEFAULT_TIMEOUTS

    seq = Sequencer(
        commander,
        locs,
        image_dir=a.image_dir,
        allow_todo=a.allow_todo,
        timeouts={k: v * a.timeout_scale for k, v in DEFAULT_TIMEOUTS.items()},
    )

    def confirm(intent) -> bool:
        if a.yes:
            return True
        return input(
            "  execute %s %s? [y/N] " % (intent.action, intent.target or "")
        ).strip().lower() in ("y", "yes")

    session = CrewSession(
        parser, seq, commander, confirm=confirm, echo=lambda m: print(m, flush=True)
    )

    if a.command:  # scripted: run each to completion, abort the script on failure
        ok = True
        for line in a.command:
            n_before = len(session.results)
            intent = session.submit(line)
            try:
                session.wait_idle()
            except KeyboardInterrupt:
                session.stop("Ctrl-C")
                ok = False
                break
            if intent is None or (
                intent.action != "stop"
                and (len(session.results) == n_before or not session.results[-1].ok)
            ):
                ok = False
                print("aborting remaining scripted commands")
                break
        session.close()
        return 0 if ok else 1

    print(
        "crew_assist (%s). Commands: undock | go to <place> | photograph <place> | "
        "return to dock | stop | quit.  Ctrl-C = stop." % ("MOCK" if a.mock else "ROS"),
        flush=True,
    )
    print("places: " + ", ".join(parser.known()), flush=True)
    while True:
        try:
            if sys.stdin.isatty():
                line = input("crew> ")
            else:
                line = sys.stdin.readline()
                if line == "":
                    raise EOFError
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower() in ("quit", "exit", "q"):
                break
            session.submit(line)
        except EOFError:
            # end of piped input: let queued work finish (Ctrl-C still stops)
            try:
                session.wait_idle()
            except KeyboardInterrupt:
                session.stop("Ctrl-C")
            break
        except KeyboardInterrupt:  # Ctrl-C = stop, never just exit
            print("", flush=True)
            session.stop("Ctrl-C")
    session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
