#!/usr/bin/env python3
"""Log loc/truth/pose at ~10 Hz to CSV (wall time, x, y, z). Needs ROS. Usage: tools_pose_logger.py out.csv"""
import sys, time
import rospy
from geometry_msgs.msg import PoseStamped
rospy.init_node("pose_logger", anonymous=True)
f = open(sys.argv[1], "w", buffering=1)
f.write("wall_time,x,y,z\n")
last = [0.0]
def cb(m):
    t = time.time()
    if t - last[0] >= 0.1:
        last[0] = t
        p = m.pose.position
        f.write("%.3f,%.4f,%.4f,%.4f\n" % (t, p.x, p.y, p.z))
rospy.Subscriber("loc/truth/pose", PoseStamped, cb)
rospy.spin()
