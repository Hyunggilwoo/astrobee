#!/usr/bin/env python3
"""Fly candidate standoffs, save a nav_cam frame + recorded pose at each, then dock.
Usage (in the sim container): python3 tools_survey.py candidates.yaml out_dir"""
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import astrobee_cmd as ac
import yaml
from intent_parser import Intent, load_locations
from sequencer import Sequencer

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
cands = yaml.safe_load(open(sys.argv[1]))
out = sys.argv[2]
os.makedirs(out, exist_ok=True)
locs = load_locations(os.path.join(os.path.dirname(__file__), "locations.yaml"))
be = ac.RosBackend()
cmd = ac.AstrobeeCommander(be)
seq = Sequencer(cmd, locs, image_dir=out)
import rospy
from geometry_msgs.msg import PoseStamped

results = {}
assert seq.execute(Intent("undock")).ok
for name, c in cands.items():
    r = cmd.run(ac.build_simple_move(c["pos"], c["quat"]), 240)
    rec = {"cmd_pos": c["pos"], "cmd_quat": c["quat"], "ack": r.name, "msg": r.message}
    if r.ok:
        time.sleep(2.0)
        for topic in ("loc/pose", "loc/truth/pose"):
            m = rospy.wait_for_message(topic, PoseStamped, timeout=5).pose
            rec[topic] = [
                round(m.position.x, 3),
                round(m.position.y, 3),
                round(m.position.z, 3),
                round(m.orientation.x, 4),
                round(m.orientation.y, 4),
                round(m.orientation.z, 4),
                round(m.orientation.w, 4),
            ]
        path = os.path.join(out, "%s.png" % name)
        rec["image"] = path if be.grab_image("hw/cam_nav", path, 20) else None
    results[name] = rec
    print(name, json.dumps(rec), flush=True)
r = seq.execute(Intent("return_to_dock"))
results["_return_to_dock"] = r.summary()
json.dump(results, open(os.path.join(out, "survey.json"), "w"), indent=1)
print(r.summary())
