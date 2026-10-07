import os
import sys

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, Shutdown
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    python_default = os.environ.get("AUV_VLA_GUI_PYTHON", sys.executable)
    return LaunchDescription([
        DeclareLaunchArgument("host", default_value="0.0.0.0"),
        DeclareLaunchArgument("port", default_value="8081"),
        DeclareLaunchArgument("python_executable", default_value=python_default),
        Node(
            package="auv_vla_gui", executable="server",
            name="auv_vla_gui_server", output="screen",
            prefix=LaunchConfiguration("python_executable"),
            arguments=["--host", LaunchConfiguration("host"),
                       "--port", LaunchConfiguration("port")],
            # Give the server time to stop all child launch groups before ROS
            # launch escalates its shutdown signal.
            sigterm_timeout="45", sigkill_timeout="5",
            on_exit=Shutdown(reason="VLA GUI server exited"),
        ),
    ])
