# Jackal

Robot-side packages for the AMRL Jackal. Keep runtime artifacts here (especially under `logs/`), not in `$HOME`.

## image_path_nav

Browser + ROS prototype: click/draw a path on a frozen ZED image, project to `odom` waypoints with depth, follow with pure pursuit.

See **[image_path_nav/README.md](image_path_nav/README.md)** for launch commands, SSH forwarding, planner API, isolation notes, and the **R1 deadman**.

```bash
# After catkin build (symlinked into ~/catkin_ws/src):
source ~/catkin_ws/devel/setup.bash
roslaunch image_path_nav image_path_nav.launch          # dry-run (default)
# roslaunch image_path_nav image_path_nav_execute.launch  # live cmd_vel; still needs Arm+Execute+hold R1
```

This stack is additive (namespaced topics/services). It does not change persistent Jackal bringup. Stop it when others need the ZED or a clean `/cmd_vel`.