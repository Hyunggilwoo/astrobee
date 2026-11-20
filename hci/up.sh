#!/usr/bin/env bash
# Start the headless ISS sim, the crew_assist node, and browser viewing.
#   ./hci/up.sh
#   HCI_GUI=1 ./hci/up.sh          # also open RViz and the Gazebo client
#   ./hci/up.sh --foreground       # stay attached to the compose logs
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "${ROOT}"

if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  DC=(docker-compose)
else
  echo "Docker Compose is required. Install Docker Desktop, or the docker-compose-v2 package." >&2
  exit 1
fi

FOREGROUND=0
if [[ "${1:-}" == "--foreground" ]]; then
  FOREGROUND=1
  shift
fi

if [[ -z "${HCI_GUI+x}" ]]; then
  avail_kb="$(awk '/MemAvailable/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)"
  if [[ "${avail_kb}" -ge 8000000 ]]; then
    export HCI_GUI=1
    echo "hci: MemAvailable is ${avail_kb} kB; starting RViz and the Gazebo client (HCI_GUI=1)."
  else
    export HCI_GUI=0
    echo "hci: MemAvailable is ${avail_kb} kB, so RViz and the Gazebo client stay off (HCI_GUI=0)."
    echo "    The noVNC desktop is still there. Inside it, run:  hci-gui gz    and    hci-gui rviz"
    echo "    Force the 3D clients on with:  HCI_GUI=1 ./hci/up.sh"
  fi
fi

mkdir -p "${ROOT}/logs"

if [[ "${FOREGROUND}" == "1" ]]; then
  exec "${DC[@]}" -f "${ROOT}/docker/compose.yml" up --build "$@"
fi

"${DC[@]}" -f "${ROOT}/docker/compose.yml" up --build -d "$@"
cat <<EOF

Astrobee HCI is starting. Gazebo takes a few minutes the first time.

  Web UI     http://localhost:8088
  noVNC      http://localhost:6080/vnc.html?autoconnect=1&resize=scale
  rosbridge  ws://localhost:9090
  camera     http://localhost:8080/stream?topic=/hw/cam_nav

  Logs       ${DC[*]} -f ${ROOT}/docker/compose.yml logs -f
  Smoke      ${ROOT}/smoke.sh
  Stop       ${ROOT}/down.sh

On Docker Desktop (Windows or Mac) those URLs are on the host, not inside the VM.
Give the Docker VM at least 8 GB of RAM. WSL2: use the Docker Desktop WSL2 backend
and do not also run a second Gazebo on the Windows side.
EOF
