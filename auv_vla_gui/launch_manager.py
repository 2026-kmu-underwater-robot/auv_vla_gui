"""Each launch button owns one independent ROS launch process group."""

from collections import deque
from pathlib import Path
import threading

from kmu26_auv_web_gui.process_manager import ManagedProcess

ROV_DEFAULTS = {
    "fcu_url": "/dev/ttyACM0:57600", "dvl_ip": "192.168.194.95",
    "use_dvl": "true", "use_localization": "true",
}


class LaunchManager:
    def __init__(self):
        self.logs = deque(maxlen=400)
        self._lock = threading.Lock()
        self.specs = {
            "rov": {"title": "ROV", "package": "hit25_auv_ros2", "file": "rov_start.launch.py", "defaults": ROV_DEFAULTS},
            "realsense": {"title": "리얼센스", "package": "realsense2_camera", "file": "rs_launch.py", "defaults": {}},
        }
        self._processes = {}
        self._stopped = set()
        self._errors = {}
        self._args = {key: dict(spec["defaults"]) for key, spec in self.specs.items()}

    def start(self, launch_id, launch_args):
        self._check_id(launch_id)
        spec = self.specs[launch_id]
        args = self._validate_args(launch_args, spec["defaults"])
        with self._lock:
            process = self._processes.get(launch_id)
            if process and process.is_running:
                raise RuntimeError(f"{spec['title']} 런치가 이미 실행 중입니다.")
            try:
                self._check_dependencies(launch_id, args)
                if process:
                    process.stop()
                command = ["ros2", "launch", spec["package"], spec["file"]]
                command.extend(f"{key}:={value}" for key, value in args.items())
                self._args[launch_id] = args
                self._stopped.discard(launch_id)
                self._errors.pop(launch_id, None)
                process = ManagedProcess(launch_id, command, self.logs)
                self._processes[launch_id] = process
                process.start()
            except (RuntimeError, OSError) as exc:
                self._errors[launch_id] = str(exc)
                raise RuntimeError(f"{spec['title']}: {exc}") from exc

    def stop(self, launch_id):
        self._check_id(launch_id)
        with self._lock:
            process = self._processes.get(launch_id)
            if process:
                process.stop()
            self._stopped.add(launch_id)
            self._errors.pop(launch_id, None)

    def stop_all(self):
        errors = []
        for launch_id in self.specs:
            try:
                self.stop(launch_id)
            except Exception as exc:
                errors.append(str(exc))
        if errors:
            raise RuntimeError("; ".join(errors))

    def snapshot(self):
        with self._lock:
            launches = []
            for launch_id, spec in self.specs.items():
                process = self._processes.get(launch_id)
                running = bool(process and process.is_running)
                return_code = process.process.poll() if process and process.process else None
                error = self._errors.get(launch_id, "")
                state = "running" if running else "stopped"
                if error or (process and not running and launch_id not in self._stopped):
                    state = "failed" if error or return_code != 0 else "exited"
                launches.append({
                    "id": launch_id, "title": spec["title"], "package": spec["package"], "file": spec["file"],
                    "state": state, "running": running, "pid": process.process.pid if running else None,
                    "return_code": return_code, "error": error,
                    "launch_args": dict(self._args[launch_id]), "command": list(process.cmd) if process else [],
                })
            return {"launches": launches, "logs": list(self.logs)}

    def _check_dependencies(self, launch_id, args):
        from ament_index_python.packages import get_package_share_directory, PackageNotFoundError

        spec = self.specs[launch_id]
        required = {spec["package"]: "launch/" + spec["file"]}
        if launch_id == "rov":
            required["mavros"] = "launch/apm.launch"
            if args["use_dvl"] == "true":
                required["dvl_a50"] = "launch/dvl_a50.launch.py"
            if args["use_localization"] == "true":
                required["robot_localization"] = ""
        for package, relative_file in required.items():
            try:
                share = Path(get_package_share_directory(package))
            except PackageNotFoundError as exc:
                raise RuntimeError(f"의존 패키지를 찾을 수 없습니다: {package}. 설치/빌드 후 GUI를 다시 실행하세요.") from exc
            if relative_file and not (share / relative_file).is_file():
                raise RuntimeError(f"런치 파일을 찾을 수 없습니다: {package}/{relative_file}.")

    def _check_id(self, launch_id):
        if launch_id not in self.specs:
            raise KeyError(f"등록되지 않은 런치: {launch_id}")

    @staticmethod
    def _validate_args(launch_args, defaults):
        if not isinstance(launch_args, dict):
            raise ValueError("launch_args는 객체여야 합니다.")
        unknown = set(launch_args) - set(defaults)
        if unknown:
            raise ValueError(f"지원하지 않는 런치 인자: {', '.join(sorted(unknown))}")
        args = dict(defaults)
        for key, value in launch_args.items():
            if not isinstance(value, str) or not value.strip() or len(value) > 256 or any(ord(char) < 32 for char in value):
                raise ValueError(f"유효하지 않은 런치 인자: {key}")
            value = value.strip()
            if key.startswith("use_") and value not in {"true", "false"}:
                raise ValueError(f"{key}는 true 또는 false여야 합니다.")
            args[key] = value
        return args
