import os
import sys

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    venv_python = os.path.expanduser("~/.local/share/auv_vla_gui/venv/bin/python")
    python_default = os.environ.get("AUV_VLA_GUI_PYTHON", venv_python if os.path.isfile(venv_python) else sys.executable)
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
        ),
    ])
