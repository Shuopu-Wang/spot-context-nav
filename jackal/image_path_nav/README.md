# image_path_nav

Unobstructed-floor prototype for the AMRL Jackal: freeze a ZED image, click or draw a path, project pixels to `odom` waypoints with aligned depth, and follow with pure pursuit.

**This is not obstacle-aware navigation.** Short paths on flat, clear floors only.

## Design choices

| Piece | Choice | Why |
|-------|--------|-----|
| Camera | Existing `zed_wrapper` ZED-2i | Already on robot; RGB + registered depth |
| Pixel→3D | Small custom projector | Avoids JSK PointcloudScreenpoint install/config; same math, capture-time TF bundled |
| Follower | Custom pure pursuit | Avoids Move Base Flex stack; ordered waypoints, cancel, replace, dry-run |
| Cmd interface | `twist_mux` external `/cmd_vel` (priority 1) | Teleop/e-stop keep higher priority; never bypass mux |
| Safety gate | Spektrum DXS **D-switch → AUTONOMOUS** | Commands go to `/navigation/cmd_vel`; joystick only forwards in auto |
| UI | Flask + static HTML on localhost | SSH port-forward friendly, no heavy frontend stack |

Projection and execution are independent of the browser: planners can call ROS services/topics directly.

## Isolation (do not break other Jackal projects)

- Code and logs live under `spot-context-nav/jackal/` (not home/`/tmp` clutter). TF dumps: `jackal/logs/`.
- Does **not** change persistent robot bringup (`/etc/ros`, systemd, jackal startup).
- Does **not** modify other people's packages; only adds a symlink `~/catkin_ws/src/image_path_nav`.
- When building, prefer a one-shot whitelist, then **clear it** so others can build:

```bash
cd ~/catkin_ws
catkin_make -DCATKIN_WHITELIST_PACKAGES=image_path_nav
catkin_make -DCATKIN_WHITELIST_PACKAGES=   # clear whitelist for everyone else
```

- Default launch is dry-run. Live launch still needs Arm + Execute + Spektrum **AUTONOMOUS**.
- Live commands publish to `/navigation/cmd_vel` (not raw `/cmd_vel`), so the Spektrum auto/manual switch gates motion. Flip out of AUTONOMOUS to stop.
- Optional ZED launch (`zed2i_for_image_path.launch`) disables ZED `odom` TF so it does not fight the Jackal EKF. Do not start a second ZED if autonomy already owns the camera; stop our ZED when others need it:

```bash
rosnode kill /zed2i/zed_node /zed2i/zed2i_state_publisher
# or Ctrl-C the zed2i_for_image_path launch
```

- Stop this stack when done: `rosnode kill /image_path_nav /image_path_follower` (or Ctrl-C the launch).

## Prerequisites

- ROS Noetic, Jackal base (`jackal_node`, `twist_mux`, `jackal_velocity_controller`)
- ZED-2i via `zed_wrapper` publishing:
  - `/zed2i/zed_node/rgb/image_rect_color`
  - `/zed2i/zed_node/depth/depth_registered` (`32FC1` meters)
  - `/zed2i/zed_node/rgb/camera_info`
- TF: `odom` → `zed2i_left_camera_optical_frame` (via `base_link`)
- Odometry: `/odometry/filtered`
- Python: `flask`, `opencv-python` / system `cv2`, `numpy`, `cv_bridge`

### Workspace

Package lives in this repo and is symlinked into `~/catkin_ws`:

```bash
ln -sfn ~/spot-context-nav/jackal/image_path_nav ~/catkin_ws/src/image_path_nav
cd ~/catkin_ws
catkin_make -DCATKIN_WHITELIST_PACKAGES=image_path_nav
catkin_make -DCATKIN_WHITELIST_PACKAGES=    # important: leave whitelist empty for other projects
source ~/catkin_ws/devel/setup.bash
```

Artifacts (logs, `frames.gv`, etc.) go in `~/spot-context-nav/jackal/logs/`, not `$HOME`.

### ZED (if not already running)

Prefer not to fight Jackal EKF odometry TF:

```bash
export CUDA_HOME=/usr/local/cuda-11.7 CUDA_ROOT=/usr/local/cuda-11.7
export PATH=/usr/local/cuda-11.7/bin:$PATH
export LD_LIBRARY_PATH=/usr/local/cuda-11.7/lib64:$LD_LIBRARY_PATH
roslaunch image_path_nav zed2i_for_image_path.launch
```

If autonomy already launched ZED, reuse it — do not start a second `zed_node`.

## Launch

### Default dry-run (no live motion)

```bash
source /opt/ros/noetic/setup.bash
source ~/catkin_ws/devel/setup.bash
roslaunch image_path_nav image_path_nav.launch
```

Twists go only to `/image_path_nav/cmd_vel_dryrun`. Arming is a soft-arm for dry-run following.

### Explicit live execution enable

```bash
roslaunch image_path_nav image_path_nav_execute.launch
```

Still **starts disarmed**. Live motion requires **all** of:

1. `image_path_nav_execute.launch` (`allow_cmd_vel:=true`)
2. Arm + Execute in the UI (or ROS API)
3. Spektrum DXS **D-switch in AUTONOMOUS** (`/autonomy_arbiter/enabled == true`)

Flip the D-switch out of AUTONOMOUS (to Manual or center/Stopped) to stop. Manual stick driving still works in MANUAL mode and overrides autonomy.

## SSH port forwarding

On your laptop:

```bash
ssh -L 8080:127.0.0.1:8080 amrl_user@<jackal-host>
```

Then open http://127.0.0.1:8080/

## UI usage

1. Confirm camera / odom / tf readiness in the status bar.
2. **Capture / freeze** a frame (stores RGB + depth + intrinsics + capture-time TF).
3. Click ordered waypoints or switch to **Draw path**.
4. **Project preview** — rejected points show statuses (`invalid_depth`, `not_floor`, `gap`, …).
5. **Arm** (dry-run soft-arm, or live if execute launch).
6. **Execute** — UI heartbeats every 400 ms; closing the tab / heartbeat loss stops and disarms.
7. **Stop / disarm** cancels immediately.

## Topics / frames

| Topic | Type | Notes |
|-------|------|-------|
| `/image_path_nav/path` | `nav_msgs/Path` | Projected route in `odom` |
| `/image_path_nav/projection_markers` | `visualization_msgs/MarkerArray` | RViz |
| `/image_path_nav/status` | `image_path_nav/ExecutionStatus` | Combined readiness |
| `/image_path_nav/cmd_vel_dryrun` | `geometry_msgs/Twist` | Always safe to watch |
| `/cmd_vel` | `geometry_msgs/Twist` | Live only if `allow_cmd_vel:=true` and armed |
| `/image_path_follower/cancel` | `std_msgs/Empty` | Stop + disarm |
| `/image_path_follower/ui_execute` | `nav_msgs/Path` | UI-supervised execute |
| `/image_path_follower/ui_heartbeat` | `std_msgs/Bool` | UI liveness |

Frames: path in `odom`; camera optical `zed2i_left_camera_optical_frame`.

Verified mux (this robot):

```
e_stop lock priority 255
joy_teleop/cmd_vel      priority 10
bluetooth_teleop/cmd_vel priority 9
twist_marker_server      priority 8
cmd_vel (external)       priority 1   ← this package
```

Controller `cmd_vel_timeout`: 0.25 s (follower publishes at 20 Hz).

## Planner integration (no UI)

```bash
# 1) Freeze current camera frame
rosservice call /image_path_nav/capture "{}"

# 2) Project image-space path (pixels in original image coordinates)
rosservice call /image_path_nav/project "pixel_path:
  capture_id: '<id-from-capture>'
  image_width: 480
  image_height: 270
  u: [240, 200, 180]
  v: [200, 180, 160]
  capture_stamp: {secs: 0, nsecs: 0}
publish_result: true"

# 3) Or publish a physical path directly (skips image projection)
rostopic pub /image_path_nav/path nav_msgs/Path "...'  # frame_id: odom

# 4) Arm + follow
rosservice call /image_path_nav/set_armed "armed: true"
rosservice call /image_path_nav/follow_path "path:
  header: {frame_id: 'odom'}
  poses: [...]
execute: true"

# Stop
rostopic pub /image_path_follower/cancel std_msgs/Empty "{}"
```

`PixelPath.msg` fields: ordered `u[]`/`v[]`, `image_width`/`image_height`, `capture_id`, `capture_stamp`.

## Tests

```bash
source ~/catkin_ws/devel/setup.bash
rosrun image_path_nav run_unit_tests.py
# or:
python3 ~/spot-context-nav/jackal/image_path_nav/test/test_projection.py
```

Covers: display letterboxing, depth units/back-projection, invalid depth / floor / gap rejection, resampling endpoint preservation, steering direction, stop-at-goal, cancel, stale odom, path replacement.

## Safety notes

- Default launch never publishes live `/cmd_vel`.
- Live launch still requires explicit arm + execute.
- UI heartbeat loss during UI execution → stop + disarm.
- Process crash relies on twist_mux / controller timeouts (0.5 s / 0.25 s) to coast-stop.
- Camera mount extrinsics currently match stock ZED URDF (`cam_pos_* = 0`); refine mount params in `zed2i_for_image_path.launch` if projection ground-truth is off.
