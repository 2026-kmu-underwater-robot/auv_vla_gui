import json
from concurrent.futures import ThreadPoolExecutor
import time

import pytest
from auv_dvl_a50_msg.msg import CommandResponse, ConfigStatus, DVL, DVLBeam
from sensor_msgs.msg import BatteryState, CompressedImage

from auv_vla_gui.dvl import DvlMonitor, quality_state, validate_command


class FakePublisher:
    subscribers = 1

    def __init__(self):
        self.messages = []

    def get_subscription_count(self):
        return self.subscribers

    def publish(self, msg):
        self.messages.append(msg)


class FakeNode:
    def __init__(self):
        self.publisher = FakePublisher()

    def create_publisher(self, *args):
        return self.publisher

    def create_subscription(self, *args):
        return args


@pytest.mark.parametrize("command,name,value", [
    ("set_config", "acoustic_enabled", "yes"),
    ("set_config", "dark_mode_enabled", "1"),
    ("set_config", "speed_of_sound", "0"),
    ("set_config", "speed_of_sound", "1.5"),
    ("set_config", "speed_of_sound", "999999999999999999"),
    ("set_config", "mounting_rotation_offset", "nan"),
    ("set_config", "range_mode", "auto\n"),
    ("set_config", "unknown", "true"),
    ("get_config", "acoustic_enabled", "true"),
    ("unknown", "", ""),
])
def test_invalid_dvl_commands_are_rejected(command, name, value):
    with pytest.raises(ValueError):
        validate_command(command, name, value)


def test_dvl_quality_and_freshness_follow_original_gui_thresholds(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("auv_vla_gui.dvl.time.monotonic", lambda: clock[0])
    dvl = DvlMonitor(FakeNode())
    msg = DVL(velocity_valid=True, fom=0.01, altitude=1.0,
              beams=[DVLBeam(valid=True) for _ in range(4)])
    msg.velocity.x = 0.2
    dvl._on_data(msg)
    data = dvl.snapshot()
    assert data["dvl_quality"]["good"]
    assert data["dvl_quality"]["velocity"]["x"] == 0.2
    assert data["health"]["alive"]
    assert not quality_state(msg, 3)[0]
    msg.fom = 0.1
    assert not quality_state(msg, 4)[0]
    msg.fom = 0.01
    msg.altitude = 0.01
    assert not quality_state(msg, 4)[0]
    msg.fom = float("nan")
    dvl._on_data(msg)
    json.dumps(dvl.snapshot(), allow_nan=False)
    clock[0] += 1.1
    assert not dvl.snapshot()["health"]["alive"]


def test_calibration_waits_for_matching_ack_and_blocks_other_commands(monkeypatch):
    node = FakeNode()
    dvl = DvlMonitor(node)
    dvl.command("calibrate_gyro")
    assert node.publisher.messages[-1].command == "calibrate_gyro"
    assert dvl.snapshot()["dvl_calibration"]["state"] == "calibrating"
    with pytest.raises(RuntimeError):
        dvl.command("get_config")
    dvl._on_response(CommandResponse(response_to="set_config", success=True))
    assert dvl.snapshot()["dvl_calibration"]["state"] == "calibrating"
    dvl._on_response(CommandResponse(response_to="calibrate_gyro", success=True))
    assert dvl.snapshot()["dvl_calibration"]["state"] == "completed"
    dvl.command("get_config")
    assert dvl.snapshot()["dvl_response"]["success"] is None


def test_calibration_timeout_and_failed_ack_are_not_reported_as_success(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("auv_vla_gui.dvl.time.monotonic", lambda: clock[0])
    dvl = DvlMonitor(FakeNode())
    dvl.command("calibrate_gyro")
    clock[0] += 21
    assert dvl.snapshot()["dvl_calibration"]["state"] == "timeout"
    dvl._on_response(CommandResponse(response_to="calibrate_gyro", success=True))
    assert dvl.snapshot()["dvl_calibration"]["state"] == "timeout"
    dvl.command("calibrate_gyro")
    dvl._on_response(CommandResponse(response_to="calibrate_gyro", success=False, error_message="busy"))
    assert dvl.snapshot()["dvl_calibration"]["state"] == "failed"


def test_dvl_without_driver_does_not_publish():
    node = FakeNode()
    node.publisher.subscribers = 0
    dvl = DvlMonitor(node)
    with pytest.raises(ConnectionError):
        dvl.command("get_config")
    assert not node.publisher.messages


def test_failed_config_response_preserves_last_confirmed_settings():
    dvl = DvlMonitor(FakeNode())
    dvl._on_config(ConfigStatus(response_to="get_config", success=True, acoustic_enabled=True,
                                range_mode="auto", speed_of_sound=1500))
    dvl._on_config(ConfigStatus(response_to="get_config", success=False, error_message="no TCP"))
    assert dvl.snapshot()["dvl_config"]["acoustic_enabled"]
    assert not dvl.snapshot()["dvl_response"]["success"]


@pytest.mark.parametrize("name,value", [
    ("acoustic_enabled", "false"), ("dark_mode_enabled", "true"),
    ("speed_of_sound", "1495"), ("mounting_rotation_offset", "90"), ("range_mode", "=1"),
])
def test_setting_change_refreshes_config_after_successful_device_ack(name, value):
    node = FakeNode()
    dvl = DvlMonitor(node)
    dvl.command("set_config", name, value)
    assert [msg.command for msg in node.publisher.messages] == ["set_config"]
    dvl._on_response(CommandResponse(response_to="set_config", success=False, error_message="rejected"))
    assert len(node.publisher.messages) == 1
    dvl.command("set_config", name, value)
    dvl._on_response(CommandResponse(response_to="set_config", success=True))
    assert [msg.command for msg in node.publisher.messages] == ["set_config", "set_config", "get_config"]
    dvl._on_config(ConfigStatus(response_to="get_config", success=True, acoustic_enabled=False,
                                dark_mode_enabled=True, speed_of_sound=1495, range_mode="=1"))
    assert dvl.snapshot()["dvl_config"]["speed_of_sound"] == 1495


def wait_for_messages(node, count):
    deadline = time.monotonic() + 1
    while len(node.publisher.messages) < count and time.monotonic() < deadline:
        time.sleep(.001)
    assert len(node.publisher.messages) >= count


@pytest.mark.parametrize("outcome", ["confirmed", "rejected", "no_ack", "still_on"])
def test_shutdown_disables_acoustics_and_requires_ack_and_off_readback(outcome):
    node = FakeNode()
    dvl = DvlMonitor(node)
    # Cached OFF from an earlier query must not count as shutdown confirmation.
    dvl._on_config(ConfigStatus(response_to="get_config", success=True, acoustic_enabled=False))
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(dvl.prepare_shutdown, timeout=.2)
        wait_for_messages(node, 1)
        msg = node.publisher.messages[0]
        assert (msg.command, msg.parameter_name, msg.parameter_value) == ("set_config", "acoustic_enabled", "false")
        with pytest.raises(RuntimeError, match="GUI 종료 중"):
            dvl.command("set_config", "acoustic_enabled", "true")
        if outcome != "no_ack":
            dvl._on_response(CommandResponse(response_to="set_config", success=outcome != "rejected",
                                              error_message="busy" if outcome == "rejected" else ""))
        if outcome in {"confirmed", "still_on"}:
            assert node.publisher.messages[-1].command == "get_config"
            assert not result.done()  # ACK alone does not prove the queried state.
            dvl._on_config(ConfigStatus(response_to="get_config", success=True, acoustic_enabled=outcome == "still_on"))
        assert result.result(timeout=1)["success"] == (outcome == "confirmed")


def test_shutdown_does_not_accept_ack_for_an_earlier_setting_change():
    node = FakeNode()
    dvl = DvlMonitor(node)
    dvl.command("set_config", "dark_mode_enabled", "true")
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(dvl.prepare_shutdown, timeout=.5)
        wait_for_messages(node, 2)
        dvl._on_response(CommandResponse(response_to="set_config", success=True))
        dvl._on_config(ConfigStatus(response_to="get_config", success=True, acoustic_enabled=False))
        assert not result.done()
        dvl._on_response(CommandResponse(response_to="set_config", success=True))
        dvl._on_config(ConfigStatus(response_to="get_config", success=True, acoustic_enabled=False))
        assert result.result(timeout=1)["success"]


def test_shutdown_waits_for_calibration_before_disabling_acoustics():
    node = FakeNode()
    dvl = DvlMonitor(node)
    dvl.command("calibrate_gyro")
    # Keep test failure bounded if calibration handling regresses.
    dvl._deadline = time.monotonic() + .5
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(dvl.prepare_shutdown, timeout=.5)
        deadline = time.monotonic() + .3
        while not dvl._shutting_down and time.monotonic() < deadline:
            time.sleep(.001)
        assert [msg.command for msg in node.publisher.messages] == ["calibrate_gyro"]
        dvl._on_response(CommandResponse(response_to="calibrate_gyro", success=True))
        wait_for_messages(node, 2)
        assert node.publisher.messages[1].parameter_value == "false"
        dvl._on_response(CommandResponse(response_to="set_config", success=True))
        dvl._on_config(ConfigStatus(response_to="get_config", success=True, acoustic_enabled=False))
        assert result.result(timeout=1)["success"]


def test_shutdown_without_driver_reports_unconfirmed_off():
    node = FakeNode()
    node.publisher.subscribers = 0
    result = DvlMonitor(node).prepare_shutdown(timeout=.01)
    assert not result["success"]
    assert not node.publisher.messages


def test_shutdown_publish_failure_reports_unconfirmed_off(monkeypatch):
    node = FakeNode()
    def fail(msg):
        raise RuntimeError("publisher stopped")
    monkeypatch.setattr(node.publisher, "publish", fail)
    result = DvlMonitor(node).prepare_shutdown(timeout=.01)
    assert not result["success"]
    assert "publisher stopped" in result["message"]


def test_ros_setting_refresh_and_acoustic_off_before_bridge_shutdown():
    import threading
    import rclpy
    from rclpy.context import Context
    from rclpy.executors import ExternalShutdownException, SingleThreadedExecutor
    from rclpy.node import Node
    from auv_dvl_a50_msg.msg import ConfigCommand
    from auv_vla_gui.ros_monitor import RosInterface

    # Isolate test commands from the real vehicle's ROS domain.
    driver_context = Context()
    rclpy.init(args=[], domain_id=188, context=driver_context)
    rclpy.init(args=[], domain_id=188)
    driver = Node("vla_test_dvl_driver", context=driver_context)
    ack = driver.create_publisher(CommandResponse, "/dvl/command/response", 10)
    config = driver.create_publisher(ConfigStatus, "/dvl/config/status", 10)
    state = {"acoustic_enabled": True, "dark_mode_enabled": False}
    commands = []

    def receive(msg):
        commands.append((msg.command, msg.parameter_name, msg.parameter_value))
        if msg.command == "set_config":
            state[msg.parameter_name] = msg.parameter_value == "true"
            ack.publish(CommandResponse(response_to="set_config", success=True))
        elif msg.command == "get_config":
            config.publish(ConfigStatus(response_to="get_config", success=True, **state))

    subscription = driver.create_subscription(ConfigCommand, "/dvl/config/command", receive, 10)
    executor = SingleThreadedExecutor(context=driver_context)
    executor.add_node(driver)
    def spin():
        try:
            executor.spin()
        except ExternalShutdownException:
            pass
    thread = threading.Thread(target=spin, daemon=True)
    bridge = RosInterface()
    thread.start()
    try:
        bridge.start()
        deadline = time.monotonic() + 4
        while bridge.status()["dvl_command_subscribers"] == 0 and time.monotonic() < deadline:
            time.sleep(.01)
        bridge.dvl_command("set_config", "dark_mode_enabled", "true")
        deadline = time.monotonic() + 4
        while not bridge.status()["dvl_config"].get("dark_mode_enabled") and time.monotonic() < deadline:
            time.sleep(.01)
        assert bridge.status()["dvl_config"]["dark_mode_enabled"]
        assert bridge.prepare_shutdown()["success"]
        assert bridge.node is not None  # ROS stays alive until OFF is confirmed.
        assert not bridge.status()["dvl_config"]["acoustic_enabled"]
        assert commands == [
            ("set_config", "dark_mode_enabled", "true"), ("get_config", "", ""),
            ("set_config", "acoustic_enabled", "false"), ("get_config", "", ""),
        ]
    finally:
        bridge.stop()
        executor.shutdown()
        thread.join(timeout=2)
        driver.destroy_node()
        driver_context.shutdown()


def test_camera_frames_expire_and_unknown_battery_values_are_json_safe(monkeypatch):
    import rclpy
    from sensor_msgs.msg import Image
    from auv_vla_gui.ros_monitor import RovMonitor
    for camera in ("REALSENSE", "IMX219"):
        for suffix in ("IMAGE_TOPIC", "PREVIEW_TOPIC"):
            monkeypatch.delenv(f"AUV_VLA_GUI_{camera}_{suffix}", raising=False)
    rclpy.init(args=[], domain_id=188)
    node = RovMonitor()
    try:
        assert not any(sub.msg_type is Image for sub in node.subscriptions)
        compressed = {sub.topic_name for sub in node.subscriptions if sub.msg_type is CompressedImage}
        assert compressed == {"/camera/camera/color/image_raw/compressed", "/imx219/camera0/image_raw/compressed"}
        clock = [time.monotonic()]
        monkeypatch.setattr("auv_vla_gui.ros_monitor.time.monotonic", lambda: clock[0])
        msg = CompressedImage(format="bgr8; jpeg compressed bgr8", data=b"jpeg-test")
        assert node.camera_frame("realsense") is None
        for _ in range(3):
            for camera in ("realsense", "imx219"):
                node._receive(camera + "_preview", msg)
            clock[0] += 1 / 30
        topics = node.snapshot()["topics"]
        for camera in ("realsense", "imx219"):
            assert node.camera_frame(camera) == (b"jpeg-test", "image/jpeg")
            assert topics[camera] == topics[camera + "_preview"]
            assert topics[camera]["hz"] == pytest.approx(30)
        node._receive("battery", BatteryState(voltage=16.2, current=2.4, percentage=0.75,
                                              temperature=25.0, present=True))
        assert node.snapshot()["battery"]["percentage"] == 0.75
        node._receive("battery", BatteryState(voltage=float("nan"), current=float("nan"),
                                              percentage=-1.0, temperature=float("nan")))
        assert node.snapshot()["battery"]["percentage"] is None
        json.dumps(node.snapshot(), allow_nan=False)
        stamp = node._frames["imx219"][2]
        monkeypatch.setattr("auv_vla_gui.ros_monitor.time.monotonic", lambda: stamp + 2.1)
        assert node.camera_frame("imx219") is None
        assert not node.snapshot()["topics"]["imx219"]["alive"]
    finally:
        node.destroy_node()
        rclpy.shutdown()
