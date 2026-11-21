# Validation

Checked on this agent VM (Ubuntu 24.04 host, Docker 29.1.3) on 19 Nov 2026.
The simulator image is `ghcr.io/nasa/astrobee:latest-ubuntu20.04`.
NASA flight sources in git were not modified. `nav_cam_rate` is patched only inside the container.

## What passed

| Check | Result |
|---|---|
| Mock pytest (`cd hci/crew_assist && python3 -m pytest -q`) | 93 passed, 10 skipped |
| The 10 skips | Vosk model and generated voice clips are not in git. That is intentional. |
| `docker compose -f hci/docker/compose.yml config` | accepted |
| Image build `astrobee-hci:latest` | built. Apt needed a fresh ROS key; the key baked into the NASA image (`F42ED6FBAB17C654`) is expired. |
| `./hci/up.sh` with `HCI_GUI=0` | sim became healthy, crew node subscribed, web UI on port 8088, rosbridge on 9090, camera server on 8080, noVNC on 6080 |
| Smoke sortie through `/crew_assist/intent` (the same path as the buttons) | undock OK in 30.4 s; `simpleMove6DOF` to rack alpha `(11.3, -7.5, 4.85)` frame `world` OK in 40.1 s; approach `(10.34, -9.51, 4.49)` OK in 43.3 s; `dock` berth 1 OK in 93.3 s |
| Pose after the sortie | `9.832 -9.810 4.296`, against the berth pose `9.816 -9.806 4.293` |
| Nav camera | `/hw/cam_nav` is 320×240 mono8. Snapshot and the web page both showed the ISS interior. |
| Browser page | Rosbridge connected, live pose, crew-node log of the sortie, STOP button. |
| noVNC | The page connected and showed the Xvfb desktop with the instruction terminal (`hci-gui gz`, `hci-gui rviz`). |

The move to rack alpha used frame `world`. Return to dock was approach-then-dock. The log has no `autoReturn`.
`cmd_origin` stays empty because the node uses `astrobee_cmd.py`, which leaves it empty on purpose.

## What was not run, and why

- **RViz and the Gazebo client were not started.** `HCI_GUI=0` on purpose: this VM reported under 8 GB available, and the sortie needed the RAM. The noVNC desktop is up and the commands are printed in its terminal. They were not executed here.
- **Windows, Mac, and WSL2** were not used. This host is Linux. Docker Desktop behaviour is documented in `hci/README.md` and was not exercised.
- **The Vosk model was not downloaded**, and the microphone path was not run. Mock tests cover the grammar. `hci/crew_assist/scripts/download_vosk_model.sh` is the installer.
- **GitHub Actions** had not run yet at the time of this note. The workflow is `.github/workflows/hci_crew_assist.yml` (pytest on Python 3.10, plus `docker compose config`). It does not build the NASA image.
- **NASA's existing workflows** still say `runs-on: ubuntu-20.04`. They were left alone. If those jobs fail, it is the runner image, not this HCI change.

## M2 (20 Nov 2026) — ROS 2 Jazzy client

Same VM, same Noetic sim image. Jazzy client image: `ros:jazzy-ros-base` plus the HCI message package. Branch `cursor/hci-ros2-bridge-m2-0778`.

### What passed

| Check | Result |
|---|---|
| Mock pytest, including the new mapping tests | 104 passed, 10 skipped (same 10 voice skips) |
| `docker compose config` | accepted, now with `ros1_relay` and `ros2` |
| Jazzy image build | `hci_ff_msgs` compiled. Two constant names had to be `DATA_TYPE_VEC3D` and `DATA_TYPE_MAT33F` because Jazzy rejects lowercase in constant names. Values stay 6 and 7. |
| `./hci/smoke_ros2.sh` | exit 0. Driven only by `cli.py --backend ros2` inside the Jazzy container. |
| undock | OK in 30.5 s |
| `simpleMove6DOF` rack alpha `(11.3, -7.5, 4.85)` frame `world` | OK in 40.1 s |
| approach `(10.34, -9.51, 4.49)` then `dock` berth 1 | OK in 43.3 s and 93.1 s. No `autoReturn`. |
| Final `/loc/truth/pose` | `9.832 -9.810 4.296`, quaternion about `0 0 -0.007 1`. Berth is `9.816 -9.806 4.293`. |
| Bridge overhead (command one-way + ack one-way), 4 samples | mean 0.58 ms, max 0.73 ms. Command one-way mean 0.32 ms, ack one-way mean 0.26 ms. |
| `/hw/cam_nav` on ROS 2 | one frame, 320×240 `mono8` |

The executive accepted the commands, so `cmd_origin` was empty in practice: a `guest_science` origin would have been `EXEC_FAILED`.

### What was not run

- **`ros1_bridge` itself.** Upstream does not support Jazzy on Ubuntu 24.04, and a prebuilt bridge would not include `ff_msgs`. The relay is the stand-in. Humble-from-source inside the Noetic image was not attempted.
- **`/gnc/ekf`.** Truth pose is the bridged pose. The EKF state message is not forwarded.
- **RViz, the Gazebo client, Windows/Mac/WSL2, and the microphone.** Same gaps as M1. `HCI_GUI=0` for this run.
- **The full sortie was not repeated** after switching DDS discovery from the deprecated `ROS_LOCALHOST_ONLY` (used for the flight above) to `ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST`. After the switch, `/hci_bridge/ready` still came up and `/hw/cam_nav` still delivered a 320×240 `mono8` frame. The command path was not flown a second time.
- **GitHub Actions for this branch** had not finished at the time of this note.

## M3 (20 Nov 2026) — phone page

Same VM. Phone server on port 8090, ROS 1 backend, pairing code printed by the
`phone` container. Playwright Chromium, viewport 390×844.

### What passed

| Check | Result |
|---|---|
| Mock pytest including phone API tests | 111 passed, 10 skipped |
| Auth | missing or wrong pairing code is HTTP 401, including STOP |
| One controller | second paired client is view-only and gets HTTP 403 on preview |
| Read-back | "go to rack alpha" does not move until Confirm; the sheet says it will fly to rack one |
| STOP mid-flight in the sim | in-flight `simpleMove6DOF` acked `CANCELED` ("canceled due to a stop command"); `stopAllMotion` acked OK |
| Tap to publish | 2.1 ms (server entry to publish 0.5 ms) on the last of three stops |
| Dead-man | mock heartbeat 0.35 s, stop fired at a 0.351 s gap while a move was in flight |
| ROS 1 stop fallback | `Ros1RelayStop` published `stopAllMotion` through the relay TCP port; executive completed status OK (1) |
| Phone screenshots | home, confirm sheet, in flight, after stop, under `/opt/cursor/artifacts/phone-*.png` |

### What was not run

- A physical phone on Wi-Fi. The browser was headless Chromium on the same machine.
- The microphone and Vosk. The model is not installed here. The Hold to talk control is disabled unless the page is a secure context, and `/api/voice` returns 503 without the model.
- A self-signed certificate and a phone trusting it. The commands are in `M3_PHONE_WEB_CONTROL.md`. Nothing was proxied or tunneled.
- `HCI_WEB_BACKEND=ros2` as the page's motion path. The default page uses ROS 1. The fallback was tested by sending stop through the ROS 1 relay while the Jazzy relay was still running, not by killing the Jazzy relay first.
- RViz, the Gazebo client, Windows/Mac/WSL2.

## Commands Hyunggil runs locally

From a checkout of this fork, with Docker installed and at least about 8 GB of RAM for the VM:

```bash
python3 -m pip install -r hci/crew_assist/requirements-dev.txt
cd hci/crew_assist && python3 -m pytest -q

./hci/up.sh
# browser:  http://localhost:8088
# desktop:  http://localhost:6080/vnc.html?autoconnect=1&resize=scale

./hci/smoke.sh          # undock, rack alpha, return to dock, ROS 1 path
./hci/smoke_ros2.sh      # same sortie, driven from ROS 2 Jazzy
./hci/down.sh
# phone: http://<lan-ip>:8090  (code from: docker compose -f hci/docker/compose.yml logs phone)
```

Optional:

```bash
HCI_GUI=1 ./hci/up.sh    # also open RViz and the Gazebo client
hci/crew_assist/scripts/download_vosk_model.sh
python3 hci/crew_assist/cli.py --mock --yes -c undock -c "go to rack alpha" -c "return to dock"
```

On Docker Desktop, give the VM at least 8 GB. On Windows use the WSL2 backend and run `./hci/up.sh` from a directory Docker can see.
