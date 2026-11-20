#!/bin/bash
# Jazzy container: relay, or a shell with the HCI messages sourced.
set -eo pipefail
set +u
# shellcheck disable=SC1091
source /opt/ros/jazzy/setup.bash
# shellcheck disable=SC1091
source /opt/hci_ws/install/setup.bash
set +u
export PYTHONPATH="/opt/hci/crew_assist:${PYTHONPATH:-}"
export ROS_AUTOMATIC_DISCOVERY_RANGE="${ROS_AUTOMATIC_DISCOVERY_RANGE:-LOCALHOST}"

mode="${1:-relay2}"
shift || true
case "${mode}" in
  relay2) exec python3 -u /opt/hci/ros2/scripts/ros2_relay.py "$@" ;;
  bash) exec bash "$@" ;;
  *)
    echo "hci ros2: unknown mode '${mode}'" >&2
    exit 2
    ;;
esac
