#!/bin/bash
# One-shot: undock, rack alpha, return to dock, through the crew node.
set -eo pipefail
# shellcheck disable=SC1091
source /opt/hci/docker/scripts/ros_env.sh
exec python3 -u /opt/hci/crew_assist/smoke_mission.py "$@"
