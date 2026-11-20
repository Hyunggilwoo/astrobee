#!/bin/bash
# Wait until Gazebo is publishing time, then open the client and RViz.
set -eo pipefail
# shellcheck disable=SC1091
source /opt/hci/docker/scripts/ros_env.sh
LOG_DIR="${HCI_LOG_DIR:-/hci/logs}"

echo "hci-gui: waiting for /clock"
for _ in $(seq 1 180); do
  if rostopic list 2>/dev/null | grep -qx /clock; then
    break
  fi
  sleep 2
done
sleep 20
echo "hci-gui: gazebo client"
rosrun astrobee_gazebo start_client -g libgazebo_system_plugin_client.so \
  > "${LOG_DIR}/gzclient.log" 2>&1 &
sleep 3
echo "hci-gui: rviz"
rosrun rviz rviz -d "$(rospack find astrobee)/resources/rviz/iss.rviz" \
  > "${LOG_DIR}/rviz.log" 2>&1 &
wait
