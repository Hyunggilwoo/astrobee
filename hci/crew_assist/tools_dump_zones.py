#!/usr/bin/env python3
"""Dump the sim's keep-in/keep-out zones (mob/get_zones) to zones_iss.json. Needs ROS."""
import json, os, sys
import rospy
from ff_msgs.srv import GetZones
rospy.init_node("dump_zones", anonymous=True)
rospy.wait_for_service("/mob/get_zones", 20)
r = rospy.ServiceProxy("/mob/get_zones", GetZones)()
out = [{"name": z.name, "index": z.index, "type": {0: "keepout", 1: "keepin", 2: "clutter"}[z.type],
        "min": [z.min.x, z.min.y, z.min.z], "max": [z.max.x, z.max.y, z.max.z]} for z in r.zones]
path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "zones_iss.json")
json.dump({"source": "mob/get_zones (astrobee/resources/zones/iss.bin)", "zones": out}, open(path, "w"), indent=1)
print("wrote %d zones to %s" % (len(out), path))
