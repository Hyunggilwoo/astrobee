#!/bin/bash
# HCI container entrypoint. Sources the NASA image's ROS workspace, then
# starts the sim, the crew_assist node, or a one-shot smoke mission.
# NASA flight code is not modified on the host.
set -eo pipefail

# shellcheck disable=SC1091
source /opt/hci/docker/scripts/ros_env.sh

mode="${1:-sim}"
shift || true
echo "hci: mode=${mode} ROS_MASTER_URI=${ROS_MASTER_URI} ROS_IP=${ROS_IP:-unset} ROS_HOSTNAME=${ROS_HOSTNAME:-unset}"
case "${mode}" in
  sim) exec /opt/hci/docker/scripts/run_sim.sh "$@" ;;
  crew) exec /opt/hci/docker/scripts/run_crew.sh "$@" ;;
  smoke) exec /opt/hci/docker/scripts/run_smoke.sh "$@" ;;
  relay1) exec /opt/hci/docker/scripts/run_relay.sh "$@" ;;
  phone) exec /opt/hci/docker/scripts/run_phone.sh "$@" ;;
  bash) exec bash "$@" ;;
  *)
    echo "hci: unknown mode '${mode}' (expected sim, crew, relay1, smoke, or bash)" >&2
    exit 2
    ;;
esac
