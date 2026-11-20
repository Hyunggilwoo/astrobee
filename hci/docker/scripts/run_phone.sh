#!/bin/bash
# Phone UI on the sim network. Pairing code is printed here and written to the log dir.
set -eo pipefail
LOG_DIR="${HCI_LOG_DIR:-/hci/logs}"
mkdir -p "${LOG_DIR}"
echo "hci: waiting for the executive before the phone UI"
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
  echo "hci: phone UI gave up waiting for /command" >&2
  exit 1
fi
export PYTHONPATH="/opt/hci/crew_assist${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -u /opt/hci/crew_assist/phone_http.py --backend "${HCI_WEB_BACKEND:-ros1}"
