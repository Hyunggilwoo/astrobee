# Field-compatible with nasa/astrobee ff_msgs/CommandStamped (Apache-2.0).
# HCI-owned ROS 2 copy for the bridge. Not NASA flight code.

std_msgs/Header header
string cmd_name
string cmd_id
string cmd_src
string cmd_origin
string subsys_name
hci_ff_msgs/CommandArg[] args
