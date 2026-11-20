#!/bin/bash
# ROS 1 side of the Jazzy bridge. The entrypoint has already sourced Noetic.
set -eo pipefail
LOG_DIR="${HCI_LOG_DIR:-/hci/logs}"
mkdir -p "${LOG_DIR}"
echo "hci: starting ros1 relay"
exec python3 -u /opt/hci/crew_assist/ros1_relay.py
