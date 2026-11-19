#!/bin/bash
# Shared ROS environment for the HCI containers. Source this; do not execute it.
# ROS setup scripts use unset variables, so nounset stays off here.
set +u
# Always source. The NASA image sets ROS_DISTRO in the image environment, but
# ROS is put on PATH by /ros_entrypoint.sh, which this image replaces.
# shellcheck disable=SC1091
source /opt/ros/noetic/setup.bash
# shellcheck disable=SC1091
source /src/astrobee/devel/setup.bash
export ASTROBEE_CONFIG_DIR="${ASTROBEE_CONFIG_DIR:-/src/astrobee/src/astrobee/config}"
export ROS_MASTER_URI="${ROS_MASTER_URI:-http://127.0.0.1:11311}"
export DISPLAY="${DISPLAY:-:99}"
export LIBGL_ALWAYS_SOFTWARE="${LIBGL_ALWAYS_SOFTWARE:-1}"
export QT_X11_NO_MITSHM="${QT_X11_NO_MITSHM:-1}"
# Advertise a numeric address. rospy prefers ROS_HOSTNAME over ROS_IP, and the
# container hostname is a bad choice here: the crew container shares the sim
# network namespace and both sides use 127.0.0.1.
if [[ -z "${ROS_IP:-}" ]]; then
  _hci_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  if [[ -n "${_hci_ip}" ]]; then
    export ROS_IP="${_hci_ip}"
  fi
  unset _hci_ip
fi
unset ROS_HOSTNAME
