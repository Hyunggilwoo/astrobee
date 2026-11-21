"""Open the motion backend the CLI, the ROS node, and the phone server share.

Stop fallback to the ROS 1 relay is a phone-only choice. The CLI does not
wrap itself in that fallback, so a ros2 CLI failure still fails closed inside
Ros2Backend.
"""
from __future__ import annotations

import astrobee_cmd as ac


def open_backend(
    mode: str,
    mock_move_time: float = 0.0,
    node_name: str = "crew_assist",
    disable_signals: bool = True,
    anonymous: bool = True,
):
    if mode == "mock":
        return ac.MockBackend(move_time_s=mock_move_time)
    if mode == "ros2":
        from ros2_backend import Ros2Backend

        return Ros2Backend()
    if mode != "ros1":
        raise ValueError("unknown backend %s" % mode)
    return ac.RosBackend(
        node_name=node_name,
        disable_signals=disable_signals,
        anonymous=anonymous,
    )
