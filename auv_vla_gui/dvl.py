"""DVL diagnostics and acknowledged commands adapted from auv_web_gui."""

from collections import deque
import math
import threading
import time

from auv_dvl_a50_msg.msg import CommandResponse, ConfigCommand, ConfigStatus, DVL
from rclpy.qos import qos_profile_sensor_data


def validate_command(command, parameter_name="", parameter_value=""):
    if command not in {"get_config", "set_config", "calibrate_gyro", "reset_dead_reckoning"}:
        raise ValueError("지원하지 않는 DVL 명령입니다.")
    if command != "set_config":
        if parameter_name or parameter_value:
            raise ValueError("이 명령에는 설정 값을 지정할 수 없습니다.")
        return
    if parameter_name in {"acoustic_enabled", "dark_mode_enabled"}:
        if parameter_value not in {"true", "false"}:
            raise ValueError("설정 값은 true 또는 false여야 합니다.")
    elif parameter_name == "speed_of_sound":
        if not parameter_value.isdecimal() or not 1 <= int(parameter_value) <= 2147483647:
            raise ValueError("음속은 양의 정수로 입력하세요.")
    elif parameter_name == "mounting_rotation_offset":
        try:
            if not math.isfinite(float(parameter_value)):
                raise ValueError
        except ValueError:
            raise ValueError("장착 각도는 유한한 숫자로 입력하세요.") from None
    elif parameter_name == "range_mode":
        if not parameter_value.strip() or len(parameter_value) > 32 or any(ord(c) < 32 for c in parameter_value):
            raise ValueError("범위 모드를 입력하세요.")
    else:
        raise ValueError("지원하지 않는 DVL 설정입니다.")


def quality_state(msg, valid_beams):
    # Keep the existing GUI's thresholds: FOM <= .05, altitude >= .05, 4 beams.
    if not msg.velocity_valid:
        return False, "속도 무효"
    if not math.isfinite(msg.fom) or msg.fom > 0.05:
        return False, "FOM 초과 또는 무효"
    if not math.isfinite(msg.altitude) or msg.altitude < 0.05:
        return False, "고도 부족 또는 무효"
    if valid_beams < 4:
        return False, "유효 빔 부족"
    return True, "정상"


class DvlMonitor:
    def __init__(self, node):
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._stamps = deque(maxlen=120)
        self._quality = {}
        self._config = {}
        self._response = {}
        self._events = deque(maxlen=8)
        self._calibration = {"state": "idle", "message": "보정 대기"}
        self._deadline = None
        self._shutting_down = False
        self._pending_settings = deque()
        self._off_ticket = None
        self._off_ack = False
        self._off_confirmed = False
        self._off_error = ""
        self._publisher = node.create_publisher(ConfigCommand, "/dvl/config/command", 10)
        self._subscriptions = [
            node.create_subscription(DVL, "/dvl/data", self._on_data, qos_profile_sensor_data),
            node.create_subscription(ConfigStatus, "/dvl/config/status", self._on_config, 10),
            node.create_subscription(CommandResponse, "/dvl/command/response", self._on_response, 10),
        ]

    def _on_data(self, msg):
        valid_beams = sum(bool(beam.valid) for beam in msg.beams)
        good, reason = quality_state(msg, valid_beams)
        finite = lambda value: value if math.isfinite(value) else None
        with self._lock:
            self._stamps.append(time.monotonic())
            self._quality = {
                "good": good, "reason": reason, "velocity_valid": msg.velocity_valid,
                "fom": finite(msg.fom), "altitude": finite(msg.altitude),
                "valid_beams": valid_beams,
                "velocity": {axis: finite(getattr(msg.velocity, axis)) for axis in ("x", "y", "z")},
            }

    def _on_config(self, msg):
        with self._lock:
            if msg.success:
                self._config = {key: getattr(msg, key) for key in (
                    "speed_of_sound", "acoustic_enabled", "dark_mode_enabled",
                    "mounting_rotation_offset", "range_mode",
                )}
                self._config["updated_at"] = time.strftime("%H:%M:%S")
            self._record_response(msg)
            if self._off_ack and msg.success and not msg.acoustic_enabled:
                self._off_confirmed = True
                self._condition.notify_all()

    def _on_response(self, msg):
        with self._lock:
            self._expire_calibration()
            self._record_response(msg)
            if msg.response_to == "set_config":
                ticket = self._pending_settings.popleft() if self._pending_settings else None
                if self._off_ticket is not None and ticket is self._off_ticket:
                    self._off_ack = msg.success
                    self._off_error = "" if msg.success else msg.error_message or "음향 끄기 명령 거부"
                if msg.success:
                    # Read back only after the device confirms a setting change.
                    try:
                        self._publisher.publish(ConfigCommand(command="get_config"))
                        self._events.append(f"{time.strftime('%H:%M:%S')} 설정 변경 후 자동 조회")
                    except Exception as exc:
                        self._events.append(f"{time.strftime('%H:%M:%S')} 자동 설정 조회 실패: {exc}")
            if msg.response_to == "calibrate_gyro" and self._calibration["state"] == "calibrating":
                self._calibration = {
                    "state": "completed" if msg.success else "failed",
                    "message": "보정 완료 · 장치 응답 확인" if msg.success else msg.error_message or "보정 실패",
                }
                self._deadline = None
            self._condition.notify_all()

    def _record_response(self, msg):
        self._response = {
            "command": msg.response_to, "success": msg.success,
            "message": msg.error_message or ("장치 응답 성공" if msg.success else "장치 응답 실패"),
        }
        self._events.append(f"{time.strftime('%H:%M:%S')} {msg.response_to}: {self._response['message']}")

    def _expire_calibration(self):
        if self._deadline is not None and time.monotonic() >= self._deadline:
            self._calibration = {"state": "timeout", "message": "보정 결과 미확인 · 장치 응답 시간 초과"}
            self._deadline = None

    def command(self, command, parameter_name="", parameter_value=""):
        validate_command(command, parameter_name, parameter_value)
        with self._lock:
            if self._shutting_down:
                raise RuntimeError("GUI 종료 중에는 DVL 명령을 보낼 수 없습니다.")
            self._expire_calibration()
            if self._calibration["state"] == "calibrating":
                raise RuntimeError("자이로 보정 중입니다. 완료 응답을 기다리세요.")
            if self._publisher.get_subscription_count() <= 0:
                raise ConnectionError("DVL 명령 수신 노드가 없습니다. ROV/DVL을 먼저 실행하세요.")
            if command == "calibrate_gyro":
                self._calibration = {"state": "calibrating", "message": "보정 중 · 기체를 움직이지 마세요"}
                self._deadline = time.monotonic() + 20.0
            msg = ConfigCommand(command=command, parameter_name=parameter_name, parameter_value=parameter_value)
            ticket = object() if command == "set_config" else None
            if ticket is not None:
                self._pending_settings.append(ticket)
            try:
                self._publisher.publish(msg)
            except Exception:
                if ticket is not None:
                    self._pending_settings.remove(ticket)
                if command == "calibrate_gyro":
                    self._calibration = {"state": "failed", "message": "보정 명령 전송 실패"}
                    self._deadline = None
                raise
            self._response = {"command": command, "success": None, "message": "전송됨 · 장치 응답 대기"}

    def prepare_shutdown(self, timeout=5.0):
        """Disable acoustics while the driver and ROS executor are still alive."""
        with self._condition:
            self._shutting_down = True
            if self._publisher.get_subscription_count() <= 0:
                return {"success": False, "message": "DVL 수신 노드 없음 · 음향 OFF 확인 불가"}
            # A calibration may take 15 seconds on the device. Let it finish
            # before asking it to change settings (at most its existing deadline).
            self._expire_calibration()
            if self._calibration["state"] == "calibrating":
                remaining = max(0.0, self._deadline - time.monotonic())
                self._condition.wait_for(lambda: self._calibration["state"] != "calibrating", remaining)
                self._expire_calibration()
            self._off_ticket = object()
            self._off_ack = False
            self._off_confirmed = False
            self._off_error = ""
            self._pending_settings.append(self._off_ticket)
            try:
                self._publisher.publish(ConfigCommand(
                    command="set_config", parameter_name="acoustic_enabled", parameter_value="false"))
                confirmed = self._condition.wait_for(
                    lambda: self._off_confirmed or bool(self._off_error), timeout=timeout)
            except Exception as exc:
                self._off_error = f"음향 끄기 전송 실패: {exc}"
                confirmed = False
            if confirmed and self._off_confirmed:
                return {"success": True, "message": "DVL 음향 OFF · 장치 응답 및 설정 조회 확인"}
            reason = self._off_error or "장치 응답/설정 조회 시간 초과"
            return {"success": False, "message": f"DVL 음향 OFF 확인 불가 · {reason}"}

    def snapshot(self):
        with self._lock:
            self._expire_calibration()
            now = time.monotonic()
            age = now - self._stamps[-1] if self._stamps else None
            recent = [stamp for stamp in self._stamps if now - stamp <= 2.0]
            span = recent[-1] - recent[0] if len(recent) > 1 else 0.0
            health = {
                "name": "/dvl/data", "age": age, "alive": age is not None and age <= 1.0,
                "hz": (len(recent) - 1) / span if span > 0 else 0.0,
            }
            return {
                "health": health, "dvl_quality": dict(self._quality), "dvl_config": dict(self._config),
                "dvl_response": dict(self._response), "dvl_calibration": dict(self._calibration),
                "dvl_events": list(self._events),
                "dvl_command_subscribers": self._publisher.get_subscription_count(),
            }
