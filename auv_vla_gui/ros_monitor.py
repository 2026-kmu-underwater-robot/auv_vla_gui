"""ROS telemetry, camera previews, and explicit DVL configuration commands."""

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
from sensor_msgs.msg import BatteryState, CompressedImage, Imu, Joy

from .dvl import DvlMonitor


def finite(value):
    return value if math.isfinite(value) else None


class RovMonitor(Node):
    def __init__(self):
        super().__init__("auv_vla_gui_monitor")
        self._lock = threading.Lock()
        self._values = {}
        self._health = {}
        self._frames = {}
        self.dvl = DvlMonitor(self)
        subscriptions = [
            ("mavros_state", "/mavros/state", State, 2.0, 20),
            ("joy", "/joy", Joy, 0.5, 20),
            ("battery", "/battery", BatteryState, 3.0, qos_profile_sensor_data),
            ("dvl", "/dvl/twist", TwistWithCovarianceStamped, 1.0, qos_profile_sensor_data),
            ("imu", "/mavros/imu/data", Imu, 1.0, qos_profile_sensor_data),
            ("depth", "/depth/pose", PoseWithCovarianceStamped, 1.0, qos_profile_sensor_data),
            ("odom", "/odometry/filtered", Odometry, 1.0, qos_profile_sensor_data),
        ]
        for camera, default in (("realsense", "/camera/camera/color/image_raw"),
                                ("imx219", "/imx219/camera0/image_raw")):
            # Retain existing topic overrides, but subscribe only to compressed
            # images for both previews and camera connection/rate indicators.
            topic = os.environ.get(f"AUV_VLA_GUI_{camera.upper()}_IMAGE_TOPIC", default)
            compressed_topic = topic if topic.endswith("/compressed") else topic + "/compressed"
            preview_topic = os.environ.get(f"AUV_VLA_GUI_{camera.upper()}_PREVIEW_TOPIC", compressed_topic)
            subscriptions.append((camera + "_preview", preview_topic, CompressedImage, 2.0, qos_profile_sensor_data))
        for key, topic, msg_type, timeout, qos in subscriptions:
            self._health[key] = {"name": topic, "timeout": timeout, "stamps": deque(maxlen=200)}
            self.create_subscription(msg_type, topic, lambda msg, key=key: self._receive(key, msg), qos)

    def _receive(self, key, msg):
        frame = None
        if key.endswith("_preview"):
            mime = "image/jpeg" if "jpeg" in msg.format.lower() or "jpg" in msg.format.lower() else "image/png" if "png" in msg.format.lower() else None
            if mime is None or not msg.data:
                return
            frame = (bytes(msg.data), mime, time.monotonic())
        with self._lock:
            self._health[key]["stamps"].append(time.monotonic())
            if frame is not None:
                self._frames[key.removesuffix("_preview")] = frame
            if key == "mavros_state":
                self._values[key] = {"connected": msg.connected, "armed": msg.armed, "mode": msg.mode}
            elif key == "joy":
                self._values[key] = {"axes": [finite(v) for v in msg.axes], "buttons": list(msg.buttons)}
            elif key == "battery":
                self._values[key] = {
                    "voltage": finite(msg.voltage), "current": finite(msg.current),
                    "temperature": finite(msg.temperature),
                    "percentage": msg.percentage if 0 <= msg.percentage <= 1 else None,
                    "present": msg.present,
                }
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
                span = recent[-1] - recent[0] if len(recent) > 1 else 0.0
                hz = (len(recent) - 1) / span if span > 0 else 0.0
                topics[key] = {"name": health["name"], "alive": age is not None and age <= health["timeout"], "age": age, "hz": hz}
            for camera in ("realsense", "imx219"):
                topics[camera] = dict(topics[camera + "_preview"])
            values = {key: dict(value) for key, value in self._values.items()}
        dvl = self.dvl.snapshot()
        topics["dvl_data"] = dvl.pop("health")
        return {"topics": topics, **values, **dvl}

    def camera_frame(self, camera):
        with self._lock:
            frame = self._frames.get(camera)
            if frame is None or time.monotonic() - frame[2] > 2.0:
                return None
            return frame[:2]


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

    def prepare_shutdown(self):
        if self.node is None:
            return {"success": False, "message": "ROS 브리지 없음 · DVL 음향 OFF 확인 불가"}
        result = self.node.dvl.prepare_shutdown()
        logger = self.node.get_logger()
        (logger.info if result["success"] else logger.warning)(result["message"])
        return result

    def status(self):
        return self.node.snapshot() if self.node else {}

    def camera_frame(self, camera):
        return self.node.camera_frame(camera) if self.node else None

    def dvl_command(self, command, parameter_name="", parameter_value=""):
        if self.node is None:
            raise ConnectionError("ROS 브리지가 실행되지 않았습니다.")
        self.node.dvl.command(command, parameter_name, parameter_value)
