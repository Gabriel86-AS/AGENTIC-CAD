# Linux checks for exported robot packages (P0 T0.7; later L5/L6 gates):
# check_urdf, gz sdf -p (Gazebo Harmonic), pinocchio. ROS 2 Jazzy on Ubuntu 24.04.
#   docker build -t agenticcad-ros:jazzy docker/ros-tools
FROM osrf/ros:jazzy-desktop-full
RUN apt-get update \
 && apt-get install -y --no-install-recommends ros-jazzy-pinocchio liburdfdom-tools \
 && rm -rf /var/lib/apt/lists/*
