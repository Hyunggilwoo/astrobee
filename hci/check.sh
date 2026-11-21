#!/usr/bin/env bash
# Routine health check for the HCI tree.
#
#   ./hci/check.sh           lint, syntax, mock pytest
#   ./hci/check.sh --sim     also ROS 1 smoke, ROS 2 smoke, and the phone e2e
#
# --sim expects Docker and a lot of RAM. It starts ./hci/up.sh if port 8090
# is not already answering, then leaves the stack up.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

SIM=0
if [[ "${1:-}" == "--sim" ]]; then
  SIM=1
elif [[ -n "${1:-}" ]]; then
  echo "usage: ./hci/check.sh [--sim]" >&2
  exit 2
fi

pass=0
fail=0
results=()

run_step() {
  local name="$1"
  shift
  echo
  echo "== ${name} =="
  if "$@"; then
    echo "PASS  ${name}"
    results+=("PASS  ${name}")
    pass=$((pass + 1))
  else
    echo "FAIL  ${name}"
    results+=("FAIL  ${name}")
    fail=$((fail + 1))
  fi
}

run_step "shell syntax" bash -c '
  bash -n hci/up.sh hci/down.sh hci/smoke.sh hci/smoke_ros2.sh hci/check.sh \
    hci/crew_assist/scripts/download_vosk_model.sh
  bash -n hci/docker/scripts/*.sh hci/ros2/scripts/*.sh
'

run_step "python syntax" python3 -m compileall -q \
  hci/crew_assist hci/docker/scripts/patch_nav_cam.py hci/ros2/scripts hci/e2e

run_step "ruff unused imports" python3 -m ruff check --config hci/ruff.toml \
  hci/crew_assist hci/docker/scripts/patch_nav_cam.py hci/ros2/scripts hci/e2e

run_step "pytest" bash -c 'cd hci/crew_assist && python3 -m pytest -q'

if [[ "${SIM}" == "1" ]]; then
  run_step "sim stack" bash -c '
    if curl -sf --max-time 3 http://127.0.0.1:8090/api/health >/dev/null; then
      echo "phone already up"
      exit 0
    fi
    HCI_GUI=0 ./hci/up.sh
    deadline=$((SECONDS + 600))
    until curl -sf --max-time 3 http://127.0.0.1:8090/api/health >/dev/null; do
      if (( SECONDS > deadline )); then
        echo "phone UI did not become healthy" >&2
        exit 1
      fi
      sleep 5
    done
  '
  run_step "ROS 1 smoke" ./hci/smoke.sh
  run_step "ROS 2 smoke" ./hci/smoke_ros2.sh
  run_step "phone e2e" python3 hci/e2e/phone_sim.py
fi

echo
echo "======== hci check ========"
for line in "${results[@]}"; do
  echo "${line}"
done
echo "---------------------------"
echo "${pass} passed, ${fail} failed"
if [[ "${fail}" -ne 0 ]]; then
  exit 1
fi
exit 0
