"""Turns an Intent into a gated sequence of Command API calls.

Rules (from the plan):
  * one command at a time; next step only after final ack == OK
  * BAD_SYNTAX / EXEC_FAILED / CANCELED / TIMEOUT abort the sequence
    (TIMEOUT also triggers stopAllMotion inside AstrobeeCommander)
  * return_to_dock = move to approach point, then dock - never autoReturn
  * photograph = move to standoff, enable camera streaming (best effort),
    save one sensor_msgs/Image frame
  * berth poses are never targets of simpleMove6DOF; `todo` poses are refused
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import astrobee_cmd as ac
from intent_parser import Intent

log = logging.getLogger("crew_assist.seq")

DEFAULT_TIMEOUTS = {
    ac.CMD_UNDOCK: 180.0,
    ac.CMD_DOCK: 240.0,
    ac.CMD_SIMPLE_MOVE6DOF: 240.0,
    ac.CMD_STOP_ALL_MOTION: 15.0,
    ac.CMD_SET_CAMERA_STREAMING: 15.0,
}


@dataclass
class StepLog:
    cmd_name: str
    status: str
    message: str = ""
    elapsed_s: float = 0.0


@dataclass
class SequenceResult:
    intent: Intent
    ok: bool
    steps: List[StepLog] = field(default_factory=list)
    error: str = ""
    image_path: Optional[str] = None

    def summary(self) -> str:
        s = " -> ".join("%s:%s" % (st.cmd_name, st.status) for st in self.steps) or "(no commands)"
        tail = " | image=%s" % self.image_path if self.image_path else ""
        return "%s %s [%s]%s%s" % ("OK  " if self.ok else "FAIL", self.intent.action, s,
                                  "" if self.ok else " | " + self.error, tail)


class Sequencer:
    def __init__(self, commander: ac.AstrobeeCommander, locations: Dict[str, dict],
                 image_dir: str = "images", timeouts: Optional[Dict[str, float]] = None,
                 allow_todo: bool = False, frame: str = "world",
                 image_timeout_s: float = 20.0, camera_name: str = "Navigation"):
        self.cmd = commander
        self.locations = locations
        self.image_dir = image_dir
        self.timeouts = dict(DEFAULT_TIMEOUTS, **(timeouts or {}))
        self.allow_todo = allow_todo
        self.frame = frame
        self.image_timeout_s = image_timeout_s
        self.camera_name = camera_name

    # -- helpers ---------------------------------------------------------------
    def _run(self, res: SequenceResult, command: ac.Command) -> bool:
        ack = self.cmd.run(command, self.timeouts[command.cmd_name])
        res.steps.append(StepLog(command.cmd_name, ack.name, ack.message, ack.elapsed_s))
        if not ack.ok:
            res.ok = False
            res.error = "%s %s %s" % (command.cmd_name, ack.name, ack.message)
            if self.cmd.abort_event.is_set():
                res.error += " (stopped by operator)"
        return ack.ok

    def _check_target(self, name: str) -> dict:
        loc = self.locations[name]
        if not loc.get("goto_allowed", True):
            raise ValueError("'%s' is a berth; say 'return to dock' instead" % name)
        if loc.get("todo") and not self.allow_todo:
            raise ValueError("'%s' is a TODO placeholder pose; record it with "
                             "teleop_tool -get_pose first (or pass --allow-todo)" % name)
        if loc.get("verify"):
            log.warning("location '%s' is marked verify - check it in your sim", name)
        return loc

    def _move(self, res: SequenceResult, loc: dict) -> bool:
        return self._run(res, ac.build_simple_move(loc["pos"], loc["quat"], self.frame))

    def _approach(self) -> dict:
        for loc in self.locations.values():
            if "berth" in loc:
                return loc
        raise ValueError("no location with a 'berth' key (dock approach) in locations.yaml")

    # -- entry point -------------------------------------------------------------
    def execute(self, intent: Intent) -> SequenceResult:
        res = SequenceResult(intent, ok=True)
        if intent.action != "stop":
            self.cmd.clear_abort()   # a new request re-arms the sequencer
        try:
            a = intent.action
            if a == "stop":
                ack = self.cmd.stop()
                res.steps.append(StepLog(ac.CMD_STOP_ALL_MOTION, ack.name, ack.message, ack.elapsed_s))
                res.ok, res.error = ack.ok, ("" if ack.ok else ack.message)
            elif a == "undock":
                self._run(res, ac.build_undock())
            elif a == "go_to":
                self._move(res, self._check_target(intent.target))
            elif a == "return_to_dock":
                appr = self._approach()
                if self._move(res, appr):           # dock only after approach OK
                    self._run(res, ac.build_dock(int(appr["berth"])))
            elif a == "photograph":
                loc = self._check_target(intent.target)
                if self._move(res, loc):
                    self._photo(res, intent.target, loc)
            else:
                raise ValueError("unsupported action %s" % a)
        except ValueError as e:
            res.ok, res.error = False, str(e)
        log.info(res.summary())
        return res

    def _photo(self, res: SequenceResult, name: str, loc: dict) -> None:
        topic = loc.get("camera", "hw/cam_nav")
        cam = {"hw/cam_sci": "Science", "hw/cam_nav": "Navigation",
               "hw/cam_dock": "Dock"}.get(topic, self.camera_name)
        # Best effort: in the Gazebo sim this toggles the DDS image sampler and may
        # EXEC_FAILED; raw frames on hw/cam_* depend on <cam>_rate in
        # simulation.config instead. So a failure here is logged, not fatal.
        ack = self.cmd.run(ac.build_set_camera_streaming(cam, True),
                           self.timeouts[ac.CMD_SET_CAMERA_STREAMING])
        res.steps.append(StepLog(ac.CMD_SET_CAMERA_STREAMING, ack.name, ack.message, ack.elapsed_s))
        if self.cmd.abort_event.is_set():
            res.ok, res.error = False, "stopped by operator"
            return
        if not ack.ok:
            log.warning("camera streaming enable not OK (%s %s); trying topic anyway",
                        ack.name, ack.message)
        os.makedirs(self.image_dir, exist_ok=True)
        path = os.path.join(self.image_dir, "%s_%s.png" % (name, time.strftime("%Y%m%d_%H%M%S")))
        if self.cmd.backend.grab_image(topic, path, self.image_timeout_s):
            res.image_path = path
            res.steps.append(StepLog("grab_image", "OK", topic))
        else:
            res.ok = False
            res.error = ("no image on %s - in the sim set nav_cam_rate > 0 in "
                         "astrobee/config/simulation/simulation.config" % topic)
            res.steps.append(StepLog("grab_image", "TIMEOUT", topic))
