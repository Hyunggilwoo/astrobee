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
# The Jazzy container cannot see the Noetic dock topic. Ask the crew
# container, which is already on that ROS graph, which steps are legal.
# Explicit arguments still override that plan.
if [[ $# -gt 0 ]]; then
  STEPS=("$@")
else
  mapfile -t STEPS < <(
    "${DC[@]}" -f "${ROOT}/docker/compose.yml" exec -T crew \
      /opt/hci/docker/scripts/run_smoke.sh --print-steps | sed -n 's/^STEP //p'
  )
fi
if [[ ${#STEPS[@]} -eq 0 ]]; then
  echo "hci: dock state did not settle; not starting the ROS 2 sortie" >&2
  exit 1
fi
exec "${DC[@]}" -f "${ROOT}/docker/compose.yml" exec -T ros2 \
  /opt/hci/ros2/scripts/smoke_inside.sh "${STEPS[@]}"
