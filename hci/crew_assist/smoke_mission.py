#!/usr/bin/env python3
"""Drive a running crew_assist node through the M1 sortie.

Steps, in order: undock, go to rack alpha, return to dock.

This publishes std_msgs/String on /crew_assist/intent and waits for
/crew_assist/status. It does not build CommandStamped itself, so the
crew node keeps the rules: empty cmd_origin, frame world, approach-then-dock,
and stopAllMotion on operator stop.
"""
from __future__ import annotations

import argparse

STEPS = ["undock", "go to rack alpha", "return to dock"]


def run(steps=None, timeout_s: float = 300.0, wait_status_s: float = 180.0) -> None:
    import json
    import time

    import rospy
    from std_msgs.msg import String

    steps = list(STEPS if steps is None else steps)
    rospy.init_node("hci_smoke", anonymous=True)
    pub = rospy.Publisher("/crew_assist/intent", String, queue_size=1)
    state = {"msg": None}

    def on_status(msg) -> None:
        state["msg"] = msg.data

    rospy.Subscriber("/crew_assist/status", String, on_status, queue_size=1)
    started = time.time()
    while state["msg"] is None and time.time() - started < wait_status_s and not rospy.is_shutdown():
        time.sleep(0.2)
    if state["msg"] is None:
        raise RuntimeError("no crew_assist/status (is ros_node.py running?)")

    started = time.time()
    while pub.get_num_connections() < 1 and time.time() - started < 30.0 and not rospy.is_shutdown():
        time.sleep(0.2)
    if pub.get_num_connections() < 1:
        raise RuntimeError("nothing subscribed to /crew_assist/intent")

    for step in steps:
        before = json.loads(state["msg"]).get("result_count", 0)
        print("SMOKE send %s (result_count=%s)" % (step, before), flush=True)
        pub.publish(String(data=step))
        deadline = time.time() + timeout_s
        finished = False
        ok = False
        while time.time() < deadline and not rospy.is_shutdown():
            raw = state["msg"]
            if not raw:
                time.sleep(0.3)
                continue
            status = json.loads(raw)
            if status.get("result_count", 0) > before and not status.get("busy"):
                print("SMOKE result %s" % status.get("last"), flush=True)
                finished = True
                ok = bool(status.get("last_ok"))
                break
            time.sleep(0.5)
        if not finished or not ok:
            raise RuntimeError("step failed: %s last=%s" % (step, state["msg"]))
    print("SMOKE OK", flush=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=300.0,
                        help="seconds to wait for each step")
    args = parser.parse_args(argv)
    try:
        run(timeout_s=args.timeout)
    except Exception as exc:
        print("SMOKE FAIL %s" % exc, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
