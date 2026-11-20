# Field-compatible with nasa/astrobee ff_msgs/CommandArg (Apache-2.0).
# HCI-owned ROS 2 copy for the bridge. Not NASA flight code.

uint8 DATA_TYPE_BOOL=0
uint8 DATA_TYPE_DOUBLE=1
uint8 DATA_TYPE_FLOAT=2
uint8 DATA_TYPE_INT=3
uint8 DATA_TYPE_LONGLONG=4
uint8 DATA_TYPE_STRING=5
# ROS 2 IDL constant names cannot contain a lowercase letter.
# NASA's names are DATA_TYPE_VEC3d and DATA_TYPE_MAT33f. The values are unchanged.
uint8 DATA_TYPE_VEC3D=6
uint8 DATA_TYPE_MAT33F=7

uint8 data_type
bool b
float64 d
float32 f
int32 i
int64 ll
string s
float64[3] vec3d
float32[9] mat33f
