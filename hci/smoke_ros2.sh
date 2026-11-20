#!/usr/bin/env bash
# Drive the running sim from ROS 2: undock, rack alpha, return to dock.
# Start the stack first with ./hci/up.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
else
  DC=(docker-compose)
fi
exec "${DC[@]}" -f "${ROOT}/docker/compose.yml" exec -T ros2 /opt/hci/ros2/scripts/smoke_inside.sh "$@"
