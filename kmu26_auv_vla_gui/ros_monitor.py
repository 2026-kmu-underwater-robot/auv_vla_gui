"""Read-only ROS subscriptions; opening the GUI never publishes control input."""

from collections import deque
import math
import os
import threading
import time

from geometry_msgs.msg import PoseWithCovarianceStamped, TwistWithCovarianceStamped
from mavros_msgs.msg import State
from nav_msgs.msg import Odometry
import rclpy
from rclpy.executors import ExternalShutdownException, SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from rclpy.signals import SignalHandlerOptions
from sensor_msgs.msg import BatteryState, Image, Imu, Joy


def finite(value):
    return value if math.isfinite(value) else None


class RovMonitor(Node):
    def __init__(self):
        super().__init__("kmu26_auv_vla_gui_monitor")
        self._lock = threading.Lock()
        self._values = {}
        self._health = {}
        subscriptions = [
            ("mavros_state", "/mavros/state", State, 2.0, 20),
            ("joy", "/joy", Joy, 0.5, 20),
            ("battery", "/battery", BatteryState, 3.0, qos_profile_sensor_data),
            ("dvl", "/dvl/twist", TwistWithCovarianceStamped, 1.0, qos_profile_sensor_data),
            ("imu", "/mavros/imu/data", Imu, 1.0, qos_profile_sensor_data),
            ("depth", "/depth/pose", PoseWithCovarianceStamped, 1.0, qos_profile_sensor_data),
            ("odom", "/odometry/filtered", Odometry, 1.0, qos_profile_sensor_data),
            ("realsense", os.environ.get("KMU26_REALSENSE_IMAGE_TOPIC", "/camera/camera/color/image_raw"), Image, 2.0, qos_profile_sensor_data),
        ]
        for key, topic, msg_type, timeout, qos in subscriptions:
            self._health[key] = {"name": topic, "timeout": timeout, "stamps": deque(maxlen=200)}
            self.create_subscription(msg_type, topic, lambda msg, key=key: self._receive(key, msg), qos)

    def _receive(self, key, msg):
        with self._lock:
            self._health[key]["stamps"].append(time.monotonic())
            if key == "mavros_state":
                self._values[key] = {"connected": msg.connected, "armed": msg.armed, "mode": msg.mode}
            elif key == "joy":
                self._values[key] = {"axes": [finite(v) for v in msg.axes], "buttons": list(msg.buttons)}
            elif key == "battery":
                self._values[key] = {"voltage": finite(msg.voltage), "current": finite(msg.current), "present": msg.present}
            elif key == "depth":
                self._values[key] = {"z": finite(msg.pose.pose.position.z)}
            elif key == "odom":
                pose = msg.pose.pose
                q = pose.orientation
                yaw = math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))
                self._values["pose"] = {"x": finite(pose.position.x), "y": finite(pose.position.y), "z": finite(pose.position.z), "yaw": finite(yaw)}

    def snapshot(self):
        now = time.monotonic()
        with self._lock:
            topics = {}
            for key, health in self._health.items():
                stamps = health["stamps"]
                age = now - stamps[-1] if stamps else None
                recent = [stamp for stamp in stamps if now - stamp <= 2.0]
                hz = (len(recent) - 1) / (recent[-1] - recent[0]) if len(recent) > 1 else 0.0
                topics[key] = {"name": health["name"], "alive": age is not None and age <= health["timeout"], "age": age, "hz": hz}
            return {"topics": topics, **{key: dict(value) for key, value in self._values.items()}}


class RosInterface:
    def __init__(self):
        self.node = None
        self._executor = None
        self._thread = None

    def start(self):
        if not rclpy.ok():
            # Uvicorn owns shutdown signals so its lifespan can stop the launch first.
            rclpy.init(args=[], signal_handler_options=SignalHandlerOptions.NO)
        self.node = RovMonitor()
        self._executor = SingleThreadedExecutor()
        self._executor.add_node(self.node)
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def _spin(self):
        try:
            self._executor.spin()
        except ExternalShutdownException:
            pass

    def stop(self):
        if self._executor:
            self._executor.shutdown()
        if self._thread:
            self._thread.join(timeout=2)
        if self.node:
            self.node.destroy_node()
        self.node = None
        if rclpy.ok():
            rclpy.shutdown()

    def status(self):
        return self.node.snapshot() if self.node else {}
