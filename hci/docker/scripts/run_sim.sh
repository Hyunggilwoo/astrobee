#!/bin/bash
# Headless ISS sim + browser viewing. Gazebo needs a display or its cameras
# fault, so Xvfb is started before roslaunch. RViz and the Gazebo client are
# optional (HCI_GUI=1) because they are heavy; the noVNC desktop can start
# them later with `hci-gui`.
set -eo pipefail

LOG_DIR="${HCI_LOG_DIR:-/hci/logs}"
mkdir -p "${LOG_DIR}"
exec > >(tee "${LOG_DIR}/entrypoint-sim.log") 2>&1

echo "hci: sim entry $(date -Is)"

python3 /opt/hci/docker/scripts/patch_nav_cam.py \
  "${ASTROBEE_CONFIG_DIR}/simulation/simulation.config" \
  "${HCI_NAV_CAM_RATE:-2.0}"

cleanup() {
  local pids
  pids="$(jobs -p || true)"
  if [[ -n "${pids}" ]]; then
    # shellcheck disable=SC2086
    kill ${pids} 2>/dev/null || true
  fi
}
trap cleanup TERM INT

Xvfb :99 -screen 0 1280x1024x24 -ac +extension GLX +render -noreset \
  > "${LOG_DIR}/xvfb.log" 2>&1 &
export DISPLAY=:99

ready=0
for _ in $(seq 1 50); do
  if xdpyinfo -display :99 >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 0.1
done
if [[ "${ready}" != "1" ]]; then
  echo "hci: Xvfb did not come up; see ${LOG_DIR}/xvfb.log" >&2
  exit 1
fi

openbox --sm-disable > "${LOG_DIR}/openbox.log" 2>&1 &
/opt/hci/docker/scripts/desktop_banner.sh > "${LOG_DIR}/desktop.log" 2>&1 &

# VNC listens on localhost only. noVNC is the published port.
x11vnc -display :99 -nopw -forever -shared -rfbport 5900 -localhost \
  -o "${LOG_DIR}/x11vnc.log" >/dev/null 2>&1 &

NOVNC_WEB="/usr/share/novnc"
if [[ ! -d "${NOVNC_WEB}" ]]; then
  echo "hci: noVNC web root ${NOVNC_WEB} is missing" >&2
  exit 1
fi
if command -v websockify >/dev/null 2>&1; then
  websockify --web="${NOVNC_WEB}" 6080 localhost:5900 > "${LOG_DIR}/novnc.log" 2>&1 &
else
  python3 -m websockify --web="${NOVNC_WEB}" 6080 localhost:5900 > "${LOG_DIR}/novnc.log" 2>&1 &
fi

python3 -m http.server 8088 --bind 0.0.0.0 --directory /opt/hci/docker/web \
  > "${LOG_DIR}/web.log" 2>&1 &

echo "hci: launching ISS sim (dds off, robot sim_pub, no rviz/gzclient yet)"
stdbuf -oL -eL roslaunch astrobee sim.launch dds:=false robot:=sim_pub rviz:=false gviz:=false sviz:=false output:=screen \
  > "${LOG_DIR}/sim.log" 2>&1 &
SIM_PID=$!

echo "hci: waiting for roscore"
for _ in $(seq 1 180); do
  if rosparam list >/dev/null 2>&1; then
    echo "hci: roscore is up"
    break
  fi
  if ! kill -0 "${SIM_PID}" 2>/dev/null; then
    echo "hci: sim exited before roscore" >&2
    tail -80 "${LOG_DIR}/sim.log" >&2 || true
    exit 1
  fi
  sleep 2
done

# Browser bridge. Failures are logged; the sim itself keeps running.
stdbuf -oL -eL roslaunch rosbridge_server rosbridge_websocket.launch port:=9090 address:=0.0.0.0 \
  > "${LOG_DIR}/rosbridge.log" 2>&1 &
stdbuf -oL -eL rosrun web_video_server web_video_server _port:=8080 _address:=0.0.0.0 \
  > "${LOG_DIR}/webvideo.log" 2>&1 &

if [[ "${HCI_GUI:-0}" == "1" ]]; then
  echo "hci: HCI_GUI=1, starting RViz and the Gazebo client when /clock exists"
  /opt/hci/docker/scripts/start_guis.sh > "${LOG_DIR}/guis.log" 2>&1 &
else
  echo "hci: HCI_GUI=0, 3D clients stay off. On the noVNC desktop run: hci-gui gz"
fi

echo "hci: web UI :8088  rosbridge :9090  video :8080  noVNC :6080  sim pid ${SIM_PID}"
set +e
wait "${SIM_PID}"
status=$?
set -e
echo "hci: sim exited ${status}"
cleanup
exit "${status}"
