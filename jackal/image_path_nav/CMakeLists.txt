cmake_minimum_required(VERSION 3.0.2)
project(image_path_nav)

find_package(catkin REQUIRED COMPONENTS
  rospy
  std_msgs
  std_srvs
  geometry_msgs
  nav_msgs
  sensor_msgs
  message_generation
)

catkin_python_setup()

add_message_files(
  FILES
  PixelPath.msg
  ExecutionStatus.msg
)

add_service_files(
  FILES
  CaptureFrame.srv
  ProjectPixelPath.srv
  SetArmed.srv
  FollowPath.srv
)

generate_messages(
  DEPENDENCIES
  std_msgs
  geometry_msgs
  nav_msgs
)

catkin_package(
  CATKIN_DEPENDS
    message_runtime
    std_msgs
    geometry_msgs
    nav_msgs
    sensor_msgs
    rospy
)

catkin_install_python(PROGRAMS
  scripts/image_path_nav_node.py
  scripts/path_follower_node.py
  scripts/run_unit_tests.py
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
)

install(DIRECTORY launch config web
  DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION}
)
