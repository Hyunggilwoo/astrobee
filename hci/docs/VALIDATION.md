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

## Commands Hyunggil runs locally

From a checkout of this fork, with Docker installed and at least about 8 GB of RAM for the VM:

```bash
python3 -m pip install -r hci/crew_assist/requirements-dev.txt
cd hci/crew_assist && python3 -m pytest -q

./hci/up.sh
# browser:  http://localhost:8088
# desktop:  http://localhost:6080/vnc.html?autoconnect=1&resize=scale

./hci/smoke.sh          # undock, rack alpha, return to dock
./hci/down.sh
```

Optional:

```bash
HCI_GUI=1 ./hci/up.sh    # also open RViz and the Gazebo client
hci/crew_assist/scripts/download_vosk_model.sh
python3 hci/crew_assist/cli.py --mock --yes -c undock -c "go to rack alpha" -c "return to dock"
```

On Docker Desktop, give the VM at least 8 GB. On Windows use the WSL2 backend and run `./hci/up.sh` from a directory Docker can see.
