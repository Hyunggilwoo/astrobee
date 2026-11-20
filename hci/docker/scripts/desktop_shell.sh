#!/bin/bash
export PATH="/opt/hci/docker/scripts:/usr/local/bin:${PATH}"
export DISPLAY="${DISPLAY:-:99}"
cat <<'EOF'
Astrobee crew-assist desktop (Xvfb). This is a personal sim, not a flight UI.

Drive the robot in a browser:
  http://localhost:8088

This desktop, from the host:
  http://localhost:6080/vnc.html?autoconnect=1&resize=scale

Open 3D views here (they are heavy; skip them if the machine is short on RAM):
  hci-gui gz
  hci-gui rviz

STOP is the red button on the web page. It sends stopAllMotion immediately.
Return to dock flies to the approach point, then docks. It does not use autoReturn.

EOF
exec bash
