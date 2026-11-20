# Astrobee HCI

Personal, off-duty project on Hyunggil Woo's fork of [NASA Astrobee](https://github.com/nasa/astrobee).
Civilian simulation only. No military branding, and this is not a crew interface for the ISS.

The goal is to make the free-flyer interactive (voice, phone, and web control, then point-and-fly)
and easier to run (Docker first, a ROS 2 bridge later). NASA flight code is left as it is.
Everything in this folder sits beside it and talks to the existing Command API.

## What you can do today

`./hci/up.sh` starts:

- the Gazebo ISS simulation from `ghcr.io/nasa/astrobee:latest-ubuntu20.04`, headless on Xvfb
- the `crew_assist` node, which publishes `ff_msgs/CommandStamped` on `command` and waits for `mgt/ack`
- a browser page (pose, ack log, nav camera, motion buttons, a large STOP)
- a noVNC desktop for RViz and the Gazebo client

STOP calls `stopAllMotion` immediately. Return to dock flies to the approach point and only then
sends `dock`. It never sends `autoReturn`. Moves use frame `world`. `cmd_origin` is left empty,
because a `guest_science` origin gets `EXEC_FAILED` when no primary APK is running.

`./hci/up.sh` also starts the phone page on port 8090 (see
`hci/docs/M3_PHONE_WEB_CONTROL.md`) and a ROS 2 Jazzy relay. A Jazzy client drives the same sim with
`./hci/smoke_ros2.sh` or `python3 hci/crew_assist/cli.py --backend ros2`. The bridge is a
small typed relay, not `ros1_bridge`: Jazzy and Noetic cannot share one process, and
`ff_msgs` would still need a custom mapping. See `hci/docs/M2_ROS2_BRIDGE.md`.

## Milestones

| | Goal | State |
|---|---|---|
| M0 | Fork plus this baseline: crew_assist, mock pytest, CI | this change |
| M1 | Runs anywhere: one Docker command, browser view and drive, no Ubuntu 20.04 install | this change |
| M2 | ROS 1 to ROS 2 bridge, without rewriting the flight code | this change |
| M3 | Phone and web control as the main interface | this change |
| M4 | Point-and-fly: tap a camera or map point, preview, then a zone-respecting move | not started |
| M5 | Communication-delay realism (the ISS link is not modeled yet) | not started |
| M6 | Small sim user study and a write-up | not started |

M0 and M1 do not change flight behaviour. Later milestones should keep that rule:
add nodes, bridges, and clients next to the NASA packages.

## Run the tests (any OS, no ROS)

```bash
python3 -m pip install -r hci/crew_assist/requirements-dev.txt
cd hci/crew_assist && python3 -m pytest -q
```

Mock mode never imports rospy. Voice tests that need the Vosk model or generated
clips skip themselves when those files are absent.

```bash
cd hci/crew_assist
python3 cli.py --mock --yes -c undock -c "go to rack alpha" -c "return to dock"
```

## Run the simulator from a browser

Host: Docker Desktop on Windows or Mac, or Docker Engine plus the Compose v2 plugin on Linux.
About 8 GB of RAM for the sim. A GPU is optional; the stack forces software GL.
The first start pulls and builds on top of the NASA image, which is a few GB.

```bash
./hci/up.sh
```

Then open:

- http://localhost:8088 — drive the robot
- http://localhost:6080/vnc.html?autoconnect=1&resize=scale — RViz / Gazebo desktop

Other commands:

```bash
HCI_GUI=1 ./hci/up.sh     # also start RViz and the Gazebo client (needs more RAM)
./hci/up.sh --foreground  # stay attached to the logs
./hci/smoke.sh            # undock, rack alpha, return to dock
./hci/down.sh
```

`./hci/up.sh` turns the 3D clients on by itself only when the machine reports at least
8 GB available. The noVNC desktop is always started. From the terminal on that desktop:

```bash
hci-gui gz
hci-gui rviz
```

### Windows, Mac, WSL2

- Install Docker Desktop and give the Docker VM at least 8 GB RAM and 2 CPUs (4 is better).
- Use the WSL2 backend on Windows. Run `./hci/up.sh` from a checkout Docker can see
  (a WSL filesystem, or a directory you have shared). Do not start a second Gazebo on the Windows desktop.
- The published URLs are on the host (`localhost`), including from a Mac or Windows browser.
- There is no NVIDIA GPU requirement. `LIBGL_ALWAYS_SOFTWARE=1` is set on purpose.
- Ports 6080, 8080, 8088, and 9090 are the web UI, camera, and rosbridge. noVNC has no
  password and rosbridge is open. Keep them on localhost. Do not publish them on a public network.

### Typed or spoken commands without the web page

Inside the crew container, or on a sourced Noetic workspace:

```bash
python3 hci/crew_assist/cli.py --yes -c undock -c "go to mid jem" -c "return to dock"
```

Voice is offline Vosk with a closed grammar and push-to-talk. The model is not in git:

```bash
hci/crew_assist/scripts/download_vosk_model.sh
python3 hci/crew_assist/voice_input.py --print-grammar
python3 hci/crew_assist/voice_input.py --mic --ptt --execute --mock
```

Say "emergency stop". Numbered rack names are rejected by voice on purpose; say
rack alpha, bravo, or charlie. Details are in `hci/crew_assist/README.md`.

## How the container is put together

`hci/docker/compose.yml` builds `hci/docker/Dockerfile` `FROM ghcr.io/nasa/astrobee:latest-ubuntu20.04`.
The image adds Xvfb, x11vnc, noVNC, rosbridge, and web_video_server. It does not recompile Astrobee.
The ROS apt key shipped in that image is expired, so the Dockerfile downloads the current key from
`ros/rosdistro` before `apt-get`.

The sim service runs `roslaunch astrobee sim.launch dds:=false robot:=sim_pub rviz:=false gviz:=false sviz:=false`
after Xvfb is up. Before launch it sets `nav_cam_rate` to 2.0 in the container's
`ASTROBEE_CONFIG_DIR` copy only, so the nav camera publishes. The file in git is unchanged.
`hci/docker/sim_overrides/simulation.config` is the override used in the original session;
the entrypoint now patches the one line so any other keys in a newer NASA image stay.

The crew service shares the sim container's network namespace and starts `ros_node.py` once the executive is subscribed to `/command`. ROS uses `127.0.0.1`, so the two containers do not need bridge routing between them.
The web page uses roslibjs against rosbridge. Buttons publish text on `/crew_assist/intent`.
The node is the only thing that builds `CommandStamped`.

## Known limits

- Ubuntu 20.04 and ROS 1 Noetic stay inside the container. The host can be something else. That is the point of M1, not a ROS 2 port (M2).
- Gazebo without a display faults the cameras. Xvfb is required. `sviz` (the Gazebo GUI) is off unless you start it.
- `nav_cam_rate` above 0 costs CPU. Hazard and dock cameras stay at 0.
- Zones stay on. The node never sends `setCheckZones`.
- Docking is berth 1 via the approach pose in `locations.yaml`. Berth poses are not `simpleMove6DOF` targets.
- The web page asks before every motion except STOP. There is no spoken yes/no.
- ISS communication delay is not modeled.
- noVNC and rosbridge are unauthenticated.
- A real robot is out of scope. This talks to the simulator's command topic.

## Layout

```
hci/README.md                 this file
hci/up.sh  down.sh  smoke.sh
hci/crew_assist/              command node, parser, sequencer, voice, tests
hci/docker/compose.yml        sim + crew
hci/docker/Dockerfile
hci/docker/web/               static page (roslibjs)
hci/docs/VALIDATION.md        what was run, and what was not
```

NASA packages under the repo root are untouched.
