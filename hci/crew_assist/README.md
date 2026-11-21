# crew_assist — Astrobee crew-assist commands

This directory is the personal HCI prototype inside Hyunggil Woo's fork.
It lives at `hci/crew_assist/` and does not change NASA flight-software behaviour.
One-command sim + browser control is `../up.sh` (see `../README.md`).
The notes below are the original prototype write-up, including what was verified
in simulation on 18 Nov 2026.

Typed "crew" commands → Astrobee Command API (`ff_msgs/CommandStamped` on `command`,
acks on `mgt/ack`), one command at a time, ack-gated. Personal, sim-only, civilian project.

| Say / type | Commands sent |
|---|---|
| `undock` | `undock` |
| `go to <place>` | `simpleMove6DOF` (frame `world`) |
| `photograph <place>` | `simpleMove6DOF` → `setCameraStreaming(Navigation,true)` (best effort) → save one frame from `hw/cam_nav` as PNG |
| `return to dock` / `go home` / `dock` | `simpleMove6DOF` to `dock_approach` → `dock(1)` only if the approach move ack is `OK` (never `autoReturn`) |
| `stop` / Ctrl-C (typed or spoken, any time) | `stopAllMotion` immediately — preempts the move in flight and drops queued requests |

Safety rules: final ack must be `OK` (1); `NOT` (0) = still running; `BAD_SYNTAX`/`EXEC_FAILED`/`CANCELED`
abort the sequence; no final ack before the timeout → `stopAllMotion`. Berths are never `simpleMove6DOF` targets.
Poses marked `todo` are refused unless `--allow-todo`. Zones stay on (we never send `setCheckZones`).
Ambiguous words ("this panel") and unknown places are rejected, not guessed. Motion asks `execute? [y/N]` unless `--yes`.

Concurrency: typed/spoken input is read on the main thread while a worker thread runs the
sequence (`session.py`). `stop` and Ctrl-C (SIGINT) bypass the queue, send `stopAllMotion`
right away and block the rest of the current sequence (e.g. no `dock` after a stopped approach).
Ctrl-C never exits the CLI; type `quit` (or EOF) to leave.

## Files
- `astrobee_cmd.py` — command builders (arg layout ported from `simple_move.cc`/`teleop_tool.cc`), `RosBackend`, `MockBackend`, the shared ack wait, PNG writer.
- `command_fields.py` — CommandArg dict and the ROS 1 / ROS 2 message fill used by both relays.
- `hci_config.py` — environment-variable names and defaults.
- `backend_factory.py` — opens the mock, ROS 1, or ROS 2 backend for the CLI and the phone.
- `bridge_codec.py` — TCP frame codec for the ROS 1 / ROS 2 relay.
- `ros1_relay.py`, `ros2_backend.py` — Noetic relay and the Jazzy command backend.
- `intent_parser.py` — rule-based text → `{action, target}`.
- `sequencer.py` — intent → gated command sequence.
- `session.py` — shared front end: parse → confirm → queue → worker; preemptive stop.
- `cli.py` — typed loop (tty or piped stdin) or scripted `-c` commands; `--mock` for no ROS.
- `phone_control.py`, `phone_http.py` — pairing, read-back, STOP, dead-man, ROS 1 stop fallback.
- `voice_input.py` — offline Vosk speech (`--mic` or `--wav`), closed grammar, same parser/session.
- `locations.yaml` — named poses (world frame, quaternion x y z w); `zones_iss.json` — sim keep-in/keep-out boxes.
- `tools_*.py` — helpers still used for zone dumps, pose surveys, halt checks, and voice clips.
- `tests/` — pytest in mock mode. Voice tests that need the Vosk model and generated clips skip themselves when those files are absent.

The routine check is `../check.sh` (see `../README.md`).

## Verified against the source (nasa/astrobee master bf43a42)
- `cmd_name` tokens from `CommandConstants.msg`: `undock`, `dock`, `simpleMove6DOF`, `stopAllMotion`, `setCameraStreaming`.
- `simpleMove6DOF` args: `[string frame, vec3d xyz, vec3d tolerance(0,0,0), mat33f quat x,y,z,w + five zeros]`. `executive.cc` maps frame `"ISS"` → world, so `"world"` and `"ISS"` are equivalent.
- `dock`: one `INT` arg, berth 1 or 2. `setCameraStreaming`: `[STRING camera, BOOL enable]`.
- `cmd_src="guest_science"`, **`cmd_origin` left empty** — `Executive::CmdCallback` rejects `cmd_origin=="guest_science"` with EXEC_FAILED when no primary APK is running.
- Dock approach `teleop_tool -att "-3.14 0 0 1"` is axis-angle → quaternion `(0, 0, -0.9999997, 0.0007963)`.
- In Gazebo, raw `hw/cam_nav` frames only exist if `nav_cam_rate > 0` in `astrobee/config/simulation/simulation.config` (default 0.0). `setCameraStreaming` only toggles the DDS image sampler.

## Run in mock mode (any OS, Python ≥3.8, PyYAML)
```bash
cd hci/crew_assist
python3 cli.py --mock                                   # interactive
python3 cli.py --mock --yes -c undock -c "go to mid jem" -c "return to dock"
python3 -m pip install -r requirements-dev.txt && python3 -m pytest -q
```

## Run against the sim (Ubuntu 20.04 + ROS Noetic, or NASA's Docker image)
Native build: follow `INSTALL.md`, then
```bash
source $ASTROBEE_WS/devel/setup.bash
roslaunch astrobee sim.launch dds:=false robot:=sim_pub rviz:=true
# second terminal
source $ASTROBEE_WS/devel/setup.bash
python3 cli.py --log run.log                            # confirms each motion
```
The supported simulator launch is now the one-command stack in `../up.sh`
(`nav_cam_rate` patched to 2.0 inside the container, Xvfb, crew node, browser UI).
The manual `docker run` below is what was used to develop this prototype on 18 Nov 2026;
paths in it are from that session, not from this checkout.

Headless box: no GPU/display → start `Xvfb :99 -screen 0 1280x1024x24 -ac` and pass `-e DISPLAY=:99`.
Without any X display, Gazebo cameras never start and sys_monitor raises heartbeat faults (nav_cam, dock_cam, localization).

## Rack standoffs (surveyed in the sim, 18 Nov 2026)
All inside JEM keep-in box #6 (x 9.86–12.0, y −9.24…−2.75, z 3.78–5.92), ≥0.65 m from the
wall they face, body+10 cm clear of keep-outs (`tests/test_zones.py`). Nav-cam views confirm racks.

| name (spoken aliases) | target pos / quat | recorded `loc/pose` | image |
|---|---|---|---|
| `rack_one` (rack one, first rack, dock end rack) | 11.3 −7.5 4.85 / yaw 0 (faces +x) | 11.301 −7.513 4.846 | `../survey/c1_xplus_aft.png` |
| `rack_two` (rack two, second rack, opposite rack) | 10.55 −6.6 4.85 / yaw 180° (faces −x) | 10.554 −6.610 4.849 | `../survey/c2_xminus_mid.png` |
| `rack_three` (rack three, third rack, far rack) | 11.3 −4.2 4.85 / yaw 0 (faces +x) | 11.316 −4.210 4.853 | `../survey/c4_xplus_fwd.png` |

A fourth candidate looking at +z (11.0 −4.5 5.1) only showed blank stowage panels and was not kept
(`../survey/c3_zplus_fwd.png`).

## Results on the box (Docker sim; times ET)
- Basic sortie, photograph, forced-timeout runs: `logs/e2e_run{1,2,3}*.log` (all OK; timeout → stopAllMotion OK).
- **Typed stop** (`logs/stop_test_typed.log`, piped stdin): stop typed 15 s into "go to rack three" →
  move acked `CANCELED` ("canceled due to a stop command"), `stopAllMotion` OK in 1.3 s, robot still
  (<0.006 m/s) by 8 s; then return to dock OK. Profile: `logs/stop_test_typed_halt.txt`.
- **Ctrl-C / SIGINT** (`logs/stop_test_sigint.log`): SIGINT 30 s into the flight at cruise 0.20 m/s →
  `stopAllMotion` OK in 2.5 s; speed 0.084 m/s in 1–2 s, 0.013 m/s in 2–4 s, 0.000 by 10 s (≈0.33 m
  coast); CLI stayed alive; return to dock OK. Profile: `logs/stop_test_sigint_halt.txt`; poses `logs/stop_test_poses.csv`.
- **Voice mission** (`logs/voice_e2e.log`): WAVs "undock" (alan) → "photograph rack two" (lessac) →
  "return to dock" (ryan), each heard at conf 1.00, each confirmed with `y` at the prompt → all acks OK;
  image `images/rack_two_20261118_235845.png`.

## Voice
```bash
pip install vosk sounddevice pyyaml
curl -LO https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip && unzip it into ../models/
python3 voice_input.py --print-grammar                # see every accepted phrase
python3 voice_input.py --mic --execute --mock         # always-listening mic, mock robot
python3 voice_input.py --mic --ptt --execute --mock   # push-to-talk: Enter = start, Enter = stop capture
python3 voice_input.py --mic --ptt --execute          # push-to-talk against the sim (sourced workspace)
python3 voice_input.py --wav a.wav b.wav --execute    # scripted clips (mono 16-bit WAV)
python3 voice_input.py --wav a.wav b.wav --ptt --mock --execute   # PTT code path driven by WAVs (tests)
```
**Say "EMERGENCY STOP".** It is the recommended spoken stop (97 % recall on 31 synthetic voices,
vs 65 % for bare "stop"). "stop", "abort", "halt", "freeze" and "stop all motion" are still accepted,
and stop words always skip the y/N prompt. While the robot is busy, only stop words are accepted. Ctrl-C
(SIGINT) also stops.

**Racks are spoken as alpha / bravo / charlie:** "fly to rack alpha", "photograph the bravo rack", and so on.
Real-speech tests showed "three" heard as "two" (see `../voice_test/real/results.md`). Every typed rack
name ("rack one", "first rack", "opposite rack", "far rack"...) is still in the voice grammar, but only
as a *decoy*. It is recognised and then rejected with the hint "racks are 'rack alpha / bravo / charlie'
by voice", so it is never snapped onto a different rack. Typed commands keep all the old names.

Design: the closed Vosk grammar is command templates × speakable `spoken` names/aliases, plus decoys
(other ISS modules, "this panel", "open the hatch", typed-only rack names...) and `[unk]`. A result is
accepted only if it is one whole non-decoy grammar phrase, has no `[unk]`, and has confidence ≥ 0.6.
Stop words are the exception and are always honoured. Bare "dock" is excluded (noisy "stop" was heard as
"dock"). "kibo" is typed-only. Bare "this/that/here/there" decoys were removed because "that" swallowed real
"stop"s. Audio below 16 kHz is upsampled. The y/N confirmation before motion stays, and `--mic --execute`
refuses `--yes`.

Small TTS set (180 clips, 3 voices × 30 phrases × clean / white 10 dB; `../voice_test/results.json`):
grammar positives 61/63 clean, 60/63 at 10 dB; negatives 27/27; false accepts 0/90 in both. All misses
were rejections ("undock"→"dock", "go home"→"home").

Broad synthetic test (31 Piper voices, 17 conditions, 10,540 clips, `../voice_test/broad/`):

| | valid correct | rejection | stop recall | wrong actions (wrong rack target) |
|---|---|---|---|---|
| before (numbered racks) | 81.4 % | 99.94 % | 77.4 % | 32 (29) |
| after (alpha/bravo/charlie) | 82.0 % | 99.94 % | 75.6 %* | 4 (0) |

\*Piper re-synthesis is not bit-identical. On the *same* clips, the new grammar gives the same stop recall
(77.4 %).

Real human speech (Google Speech Commands v0.02, CC BY 4.0, 400 speakers per word;
`../voice_test/real/results.md`): bare "stop" recall is 92.0 % clean, 61.3 % at 10 dB and 38.5 % at 5 dB
(dataset background noise); before the decoy change it was 91.8 / 58.5 / 34.2 %. There were 0 motion false
accepts from 3,600 non-command words per condition.

## Next
1. Record ~20 real utterances per person with your own mic (`--mic --ptt`), especially "emergency stop"
   and the alpha/bravo/charlie phrases (no public real recordings of those exist), incl. noise.
2. Measure command-to-motion latency and add a demo video (weeks 5–6 of the plan).
3. Optional: a spoken "yes/no" confirmation (kept keyboard-only for now on purpose).
