#!/usr/bin/env python3
"""Send one stopAllMotion through the ROS 1 relay and print the executive ack."""
import time

import astrobee_cmd as ac
import rospy
from ff_msgs.msg import AckStamped
from phone_control import Ros1RelayStop


def main() -> int:
    rospy.init_node("hci_fallback_probe", anonymous=True)
    got = {}

    def on_ack(msg) -> None:
        got[msg.cmd_id] = (int(msg.completed_status.status), msg.message)

    rospy.Subscriber("mgt/ack", AckStamped, on_ack)
    stop = ac.build_stop()
    sent = Ros1RelayStop().execute(stop, 2.0)
    print("relay", sent.name, sent.message, stop.cmd_id, flush=True)
    deadline = time.time() + 8
    while time.time() < deadline:
        ack = got.get(stop.cmd_id)
        if ack is not None and ack[0] != 0:
            break
        time.sleep(0.05)
    print("executive", got.get(stop.cmd_id), flush=True)
    ack = got.get(stop.cmd_id)
    return 0 if ack is not None and ack[0] != 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
