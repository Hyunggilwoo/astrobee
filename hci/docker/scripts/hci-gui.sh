#!/bin/bash
# Start a 3D view on the noVNC desktop. Safe to run more than once.
set -eo pipefail
# shellcheck disable=SC1091
source /opt/hci/docker/scripts/ros_env.sh

cmd="${1:-}"
case "${cmd}" in
  gz|gazebo)
    exec rosrun astrobee_gazebo start_client -g libgazebo_system_plugin_client.so
    ;;
  rviz)
    exec rosrun rviz rviz -d "$(rospack find astrobee)/resources/rviz/iss.rviz"
    ;;
  *)
    echo "usage: hci-gui gz | hci-gui rviz" >&2
    exit 2
    ;;
esac
