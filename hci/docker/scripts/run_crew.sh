#!/bin/bash
# Wait until the executive is subscribed on /command, then run the crew node.
set -eo pipefail

LOG_DIR="${HCI_LOG_DIR:-/hci/logs}"
IMAGE_DIR="${HCI_IMAGE_DIR:-${LOG_DIR}/images}"
mkdir -p "${LOG_DIR}" "${IMAGE_DIR}"

echo "hci: waiting for a subscriber on /command"
ready=0
for _ in $(seq 1 180); do
  if rostopic info /command 2>/dev/null | awk '
      BEGIN { s = 0 }
      /^Subscribers:/ { s = 1 }
      s && /^[[:space:]]+\*/ { found = 1 }
      END { exit found ? 0 : 1 }
    '; then
    ready=1
    break
  fi
  sleep 2
done
if [[ "${ready}" != "1" ]]; then
  echo "hci: executive never subscribed to /command" >&2
  exit 1
fi

echo "hci: starting crew_assist node"
exec python3 -u /opt/hci/crew_assist/ros_node.py --yes \
  --log "${LOG_DIR}/crew.log" \
  --image-dir "${IMAGE_DIR}"
