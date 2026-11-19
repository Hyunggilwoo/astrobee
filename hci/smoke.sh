#!/usr/bin/env bash
# undock -> rack alpha -> return to dock, through the running crew node.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
else
  DC=(docker-compose)
fi
exec "${DC[@]}" -f "${ROOT}/docker/compose.yml" exec -T crew /opt/hci/docker/scripts/run_smoke.sh "$@"
