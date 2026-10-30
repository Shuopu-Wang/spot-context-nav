# Live execution / readiness status for the UI and planners.
string state                 # idle|preview|armed|following|waiting_deadman|stopping|error|completed
bool armed
bool allow_cmd_vel
bool camera_ready
bool odom_ready
bool tf_ready
bool capture_ready
bool deadman_required
bool deadman_held
string deadman_hint          # e.g. "hold PS4 R1 (button 5)"
string capture_id
string message
float64 path_length_m
uint32 waypoint_index
uint32 waypoint_count
float64 dist_to_waypoint_m
float64 ui_heartbeat_age_s
