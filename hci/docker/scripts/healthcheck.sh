#!/bin/bash
# Healthy once the executive is listening on /command. roscore alone is not enough.
# shellcheck disable=SC1091
source /opt/hci/docker/scripts/ros_env.sh
rostopic info /command 2>/dev/null | awk '
  BEGIN { s = 0 }
  /^Subscribers:/ { s = 1 }
  s && /^[[:space:]]+\*/ { found = 1 }
  END { exit found ? 0 : 1 }
'
