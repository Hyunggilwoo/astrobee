#!/bin/bash
# undock -> rack alpha -> return to dock, driven by the ROS 2 CLI.
set -eo pipefail
set +u
# shellcheck disable=SC1091
source /opt/ros/jazzy/setup.bash
# shellcheck disable=SC1091
source /opt/hci_ws/install/setup.bash
set +u
export PYTHONPATH="/opt/hci/crew_assist:${PYTHONPATH:-}"
export ROS_AUTOMATIC_DISCOVERY_RANGE="${ROS_AUTOMATIC_DISCOVERY_RANGE:-LOCALHOST}"
LOG_DIR="${HCI_LOG_DIR:-/hci/logs}"
mkdir -p "${LOG_DIR}"

python3 -u /opt/hci/ros2/scripts/wait_ready.py 180
: > "${HCI_BRIDGE_LOG:-${LOG_DIR}/bridge_latency.jsonl}"

if [[ $# -eq 0 ]]; then
  set -- undock "go to rack alpha" "return to dock"
fi
args=()
for step in "$@"; do
  args+=(-c "${step}")
done

set +e
python3 -u /opt/hci/crew_assist/cli.py --backend ros2 --yes \
  "${args[@]}" \
  --log "${LOG_DIR}/ros2_smoke.log"
rc=$?
set -e
# A successful sortie can still abort while rclpy tears down. The log is the
# sortie result; a destructor abort is not a failed flight.
if [[ "${rc}" -ne 0 ]] && grep -q "OK   return_to_dock" "${LOG_DIR}/ros2_smoke.log"; then
  echo "hci: ros2 client aborted during shutdown after the sortie completed" >&2
  rc=0
fi
python3 -u /opt/hci/ros2/scripts/report_pose.py || true
exit "${rc}"
