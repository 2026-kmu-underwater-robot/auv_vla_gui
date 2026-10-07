from pathlib import Path
import os
import signal
import subprocess
import sys
import time

from fastapi.testclient import TestClient
import pytest

from auv_vla_gui.launch_manager import LaunchManager, ROV_DEFAULTS
from auv_vla_gui.server import create_app
from auv_web_gui.process_manager import ManagedProcess


WEB_DIR = Path(__file__).resolve().parents[1] / "web"


class FakeRos:
    def __init__(self):
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def prepare_shutdown(self):
        assert self.started
        return {"success": True, "message": "test acoustic OFF"}

    def status(self):
        return {"mavros_state": {"connected": False}, "path": []}

    def camera_frame(self, camera):
        return None

    def dvl_command(self, command, parameter_name="", parameter_value=""):
        raise ConnectionError("DVL unavailable")


class FakeProcess:
    calls = []

    def __init__(self, name, cmd, logs):
        self.cmd = cmd
        self.is_running = False
        self.process = self
        self.pid = 123
        self.pgid = None
        self.return_code = None
        self.stopped = False

    def start(self):
        self.is_running = True
        self.pgid = self.pid
        self.calls.append(self.cmd)

    def stop(self):
        self.is_running = False
        self.pgid = None
        self.stopped = True
        self.return_code = -15

    def poll(self):
        return self.return_code


@pytest.fixture
def console(monkeypatch):
    FakeProcess.calls = []
    monkeypatch.setattr("auv_vla_gui.launch_manager.ManagedProcess", FakeProcess)
    monkeypatch.setattr(LaunchManager, "_check_dependencies", lambda self, launch_id, args: None)
    manager, ros = LaunchManager(), FakeRos()
    with TestClient(create_app(manager=manager, ros=ros, web_dir=WEB_DIR)) as client:
        yield client, manager, ros
    assert not ros.started
    assert all(not item["running"] for item in manager.snapshot()["launches"])


def test_console_is_idle_until_explicit_start_and_owns_one_launch(console):
    client, manager, ros = console
    assert ros.started
    assert FakeProcess.calls == []
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    response = client.post("/api/launches/rov/start", json={"launch_args": {
        "fcu_url": "udp://:14550@192.168.1.2:14555", "use_dvl": "false",
    }})
    assert response.status_code == 200
    assert FakeProcess.calls == [[
        "ros2", "launch", "auv", "rov_start.launch.py",
        "fcu_url:=udp://:14550@192.168.1.2:14555", "dvl_ip:=192.168.194.95",
        "use_dvl:=false", "use_localization:=true",
    ]]
    assert client.post("/api/launches/rov/start", json={}).status_code == 409
    assert len(FakeProcess.calls) == 1
    assert client.post("/api/launches/rov/stop").json()["launches"][0]["state"] == "stopped"
    assert client.post("/api/launches/rov/start", json={}).status_code == 200


@pytest.mark.parametrize("args", [{"use_dvl": "yes"}, {"fcu_url": ""},
                                     {"fcu_url": "abc\n"}, {"unexpected": "true"}])
def test_rejects_invalid_launch_arguments(console, args):
    client, _, _ = console
    assert client.post("/api/launches/rov/start", json={"launch_args": args}).status_code == 400
    assert FakeProcess.calls == []


def test_unknown_launch_and_malformed_body(console):
    client, _, _ = console
    assert client.post("/api/launches/unknown/start", json={}).status_code == 404
    assert client.post("/api/launches/unknown/stop").status_code == 404
    assert client.post("/api/launches/rov/start", json={"launch_args": []}).status_code == 422


def test_websocket_reports_telemetry_and_process_failure(console):
    client, manager, _ = console
    with client.websocket_connect("/ws/status") as socket:
        assert socket.receive_json()["launches"][0]["state"] == "stopped"
    client.post("/api/launches/rov/start", json={})
    manager._processes["rov"].is_running = False
    manager._processes["rov"].return_code = 1
    status = client.get("/api/status").json()
    assert status["launches"][0]["state"] == "failed"
    assert "path" not in status["ros"]


def test_spawn_error_is_reported_without_running_state(console, monkeypatch):
    def fail(self):
        raise FileNotFoundError("ros2")
    monkeypatch.setattr(FakeProcess, "start", fail)
    client, _, _ = console
    assert client.post("/api/launches/rov/start", json={}).status_code == 409
    assert client.get("/api/status").json()["launches"][0]["state"] == "failed"


def test_missing_dependencies_do_not_start_partial_vehicle_stack(console, monkeypatch):
    def missing(self, launch_id, args):
        raise RuntimeError("ROV 의존 패키지를 찾을 수 없습니다: robot_localization")
    monkeypatch.setattr(LaunchManager, "_check_dependencies", missing)
    client, _, _ = console
    response = client.post("/api/launches/rov/start", json={})
    assert response.status_code == 409
    assert "robot_localization" in response.json()["detail"]
    assert FakeProcess.calls == []


def test_process_stop_cleans_child_process_group(tmp_path):
    child_pid_file = tmp_path / "child.pid"
    script = (
        "import subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        "open(sys.argv[1],'w').write(str(child.pid)); time.sleep(60)"
    )
    manager = LaunchManager()
    manager._processes["rov"] = ManagedProcess("test", [sys.executable, "-c", script, str(child_pid_file)], manager.logs)
    try:
        manager._processes["rov"].start()
        deadline = time.monotonic() + 3
        while not child_pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert child_pid_file.exists()
        child_pid = int(child_pid_file.read_text())
        manager.stop("rov")
        assert not manager._processes["rov"].is_running
        result = subprocess.run(["ps", "-o", "stat=", "-p", str(child_pid)], capture_output=True, text=True)
        assert not result.stdout.strip() or result.stdout.strip().startswith("Z")
    finally:
        manager.stop("rov")


def test_camera_and_rov_are_independent_and_shutdown_stops_both(console):
    client, manager, _ = console
    assert client.post("/api/launches/rov/start", json={}).status_code == 200
    assert client.post("/api/launches/realsense/start", json={}).status_code == 200
    assert FakeProcess.calls[1] == ["ros2", "launch", "realsense2_camera", "rs_launch.py"]
    assert all(item["running"] for item in manager.snapshot()["launches"] if item["id"] in {"rov", "realsense"})
    assert client.post("/api/launches/realsense/start", json={}).status_code == 409
    assert len(FakeProcess.calls) == 2
    assert client.post("/api/launches/realsense/stop").status_code == 200
    assert manager._processes["rov"].is_running
    assert not manager._processes["realsense"].is_running
    assert client.post("/api/launches/realsense/start", json={}).status_code == 200
    manager.stop_all()
    assert all(process.stopped for process in manager._processes.values())


def test_single_imx219_is_independent_from_realsense_and_rov(console):
    client, manager, _ = console
    for launch_id in ("rov", "realsense", "imx219"):
        assert client.post(f"/api/launches/{launch_id}/start", json={}).status_code == 200
    assert FakeProcess.calls[-1] == [
        "ros2", "launch", "auv_imx219_camera", "single_imx219.launch.py",
    ]
    assert client.post("/api/launches/imx219/start", json={}).status_code == 409
    assert len(FakeProcess.calls) == 3
    assert client.post("/api/launches/imx219/stop").status_code == 200
    assert manager._processes["rov"].is_running
    assert manager._processes["realsense"].is_running
    assert not manager._processes["imx219"].is_running
    assert client.post("/api/launches/imx219/start", json={}).status_code == 200
    assert client.post("/api/launches/realsense/stop").status_code == 200
    assert manager._processes["imx219"].is_running
    manager.stop_all()
    assert all(process.stopped for process in manager._processes.values())


@pytest.mark.parametrize("launch_id", ["rov", "realsense", "imx219"])
def test_dedicated_stop_cleans_failed_launch_without_stopping_other_groups(console, launch_id):
    client, manager, _ = console
    for name in manager.specs:
        client.post(f"/api/launches/{name}/start", json={})
    # A launch parent may exit while children still occupy its process group.
    process = manager._processes[launch_id]
    process.is_running = False
    process.return_code = 1
    states = {item["id"]: item for item in client.get("/api/status").json()["launches"]}
    assert states[launch_id]["can_stop"]
    response = client.post(f"/api/launches/{launch_id}/stop")
    assert response.status_code == 200
    states = {item["id"]: item for item in response.json()["launches"]}
    assert states[launch_id]["state"] == "stopped"
    assert not states[launch_id]["can_stop"]
    assert all(item["running"] for name, item in states.items() if name != launch_id)


@pytest.mark.parametrize("shutdown_signal", [signal.SIGINT, signal.SIGTERM])
def test_gui_server_exit_stops_all_owned_child_nodes(tmp_path, shutdown_signal):
    script = tmp_path / "server_with_test_nodes.py"
    script.write_text('''
import sys
from pathlib import Path
from auv_vla_gui import server
from auv_vla_gui.launch_manager import LaunchManager
from auv_web_gui.process_manager import ManagedProcess

root = Path(sys.argv[1])
web = Path(sys.argv[2])
class FakeRos:
    def start(self):
        (root / "ready").touch()
    def stop(self):
        assert (root / "acoustics_off").exists()
        (root / "ros_stopped").touch()
    def prepare_shutdown(self):
        for name in ("rov", "realsense", "imx219"):
            assert Path("/proc", (root / name).read_text(), "cmdline").read_bytes()
        (root / "acoustics_off").touch()
        return {"success": True, "message": "test acoustic OFF"}
    def status(self):
        return {}

manager = LaunchManager()
node_script = "import signal,time; signal.signal(signal.SIGINT, signal.SIG_IGN); time.sleep(60)"
parent_script = "import subprocess,sys,time; child=subprocess.Popen([sys.executable,'-c',sys.argv[2]]); open(sys.argv[1],'w').write(str(child.pid)); time.sleep(60)"
for name in manager.specs:
    process = ManagedProcess(name, [sys.executable, "-c", parent_script, str(root / name), node_script], manager.logs)
    manager._processes[name] = process
    process.start()
original_create_app = server.create_app
server.create_app = lambda **kwargs: original_create_app(manager=manager, ros=FakeRos(), web_dir=web)
sys.argv = ["server", "--host", "127.0.0.1", "--port", "0"]
server.main()
assert all(not item["can_stop"] for item in manager.snapshot()["launches"])
try:
    manager.start("rov", {})
except RuntimeError as error:
    assert "GUI 종료 중" in str(error)
else:
    raise AssertionError("Shutdown allowed a new launch")
''')
    output = tmp_path / "server.log"
    children = []
    with output.open("w") as log:
        server = subprocess.Popen([sys.executable, str(script), str(tmp_path), str(WEB_DIR)],
                                  stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                files = [tmp_path / name for name in ("rov", "realsense", "imx219")]
                if (tmp_path / "ready").exists() and all(p.exists() and p.read_text() for p in files):
                    children = [int(p.read_text()) for p in files]
                    break
                assert server.poll() is None, output.read_text()
                time.sleep(.05)
            assert len(children) == 3, output.read_text()
            # Exercise the production signal handler and FastAPI lifespan.
            server.send_signal(shutdown_signal)
            assert server.wait(timeout=15) == 0, output.read_text()
            assert (tmp_path / "ros_stopped").exists()
            assert (tmp_path / "acoustics_off").exists()
            for pid in children:
                result = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
                assert not result.stdout.strip() or result.stdout.strip().startswith("Z"), output.read_text()
        finally:
            if server.poll() is None:
                server.kill()
                server.wait()
            for name in ("rov", "realsense", "imx219"):
                path = tmp_path / name
                if path.exists() and path.read_text():
                    try:
                        os.kill(int(path.read_text()), signal.SIGKILL)
                    except ProcessLookupError:
                        pass


def test_symlink_installed_web_assets_are_served(tmp_path):
    web_dir = tmp_path / "install" / "web"
    web_dir.mkdir(parents=True)
    for name in ("index.html", "app.js", "styles.css"):
        (web_dir / name).symlink_to(WEB_DIR / name)
    with TestClient(create_app(ros=FakeRos(), web_dir=web_dir)) as client:
        for route, filename in (("/", "index.html"), ("/static/app.js", "app.js"),
                                ("/static/styles.css", "styles.css")):
            response = client.get(route)
            assert response.status_code == 200
            assert response.content == (WEB_DIR / filename).read_bytes()


@pytest.mark.parametrize("off_result", [False, "exception"])
def test_shutdown_always_cleans_launches_after_acoustic_off_attempt(monkeypatch, off_result):
    monkeypatch.setattr("auv_vla_gui.launch_manager.ManagedProcess", FakeProcess)
    monkeypatch.setattr(LaunchManager, "_check_dependencies", lambda *args: None)
    manager, ros = LaunchManager(), FakeRos()
    events = []
    original_shutdown, original_stop = manager.shutdown, ros.stop

    def prepare():
        assert ros.started
        assert all(process.is_running for process in manager._processes.values())
        with pytest.raises(RuntimeError, match="GUI 종료 중"):
            manager.start("rov", {})
        events.append("acoustic_off")
        if off_result == "exception":
            raise RuntimeError("ROS send failed")
        return {"success": False, "message": "device unreachable"}

    def shutdown():
        events.append("launches_stop")
        original_shutdown()

    def stop():
        events.append("ros_stop")
        original_stop()

    monkeypatch.setattr(ros, "prepare_shutdown", prepare)
    monkeypatch.setattr(manager, "shutdown", shutdown)
    monkeypatch.setattr(ros, "stop", stop)

    def run():
        with TestClient(create_app(manager=manager, ros=ros, web_dir=WEB_DIR)):
            for name in manager.specs:
                manager.start(name, {})

    if off_result == "exception":
        with pytest.raises(RuntimeError, match="ROS send failed"):
            run()
    else:
        run()
    assert events == ["acoustic_off", "launches_stop", "ros_stop"]
    assert all(process.stopped for process in manager._processes.values())


@pytest.fixture
def ros_package_shares(tmp_path, monkeypatch):
    # Mirror the files included by this workspace's rov_start.launch.py.
    files = {
        "auv": ["launch/rov_start.launch.py", "launch/mavros_auv.launch",
                "config/mavros_auv_pluginlists.yaml", "config/auv_ekf.yaml"],
        "mavros": ["launch/node.launch", "launch/apm_config.yaml"],
        "auv_dvl_a50": ["launch/dvl_a50.launch.py"],
        "robot_localization": [],
        "realsense2_camera": ["launch/rs_launch.py"],
        "auv_imx219_camera": ["launch/single_imx219.launch.py"],
    }
    shares = {}
    for package, relative_files in files.items():
        share = tmp_path / package
        share.mkdir()
        shares[package] = share
        for relative_file in relative_files:
            path = share / relative_file
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()

    def get_share(package):
        from ament_index_python.packages import PackageNotFoundError
        if package not in shares:
            raise PackageNotFoundError(package)
        return str(shares[package])

    monkeypatch.setattr("ament_index_python.packages.get_package_share_directory", get_share)
    return shares


@pytest.mark.parametrize("use_dvl,use_localization", [
    ("true", "true"), ("false", "true"), ("true", "false"), ("false", "false"),
])
def test_workspace_dependency_check_respects_optional_sensors(
        ros_package_shares, use_dvl, use_localization):
    if use_dvl == "false":
        del ros_package_shares["auv_dvl_a50"]
    if use_localization == "false":
        del ros_package_shares["robot_localization"]
        (ros_package_shares["auv"] / "config/auv_ekf.yaml").unlink()
    LaunchManager()._check_dependencies("rov", {
        **ROV_DEFAULTS, "use_dvl": use_dvl, "use_localization": use_localization,
    })


@pytest.mark.parametrize("package,relative_file", [
    ("auv", "launch/mavros_auv.launch"),
    ("auv", "config/mavros_auv_pluginlists.yaml"),
    ("auv", "config/auv_ekf.yaml"),
    ("mavros", "launch/node.launch"),
    ("mavros", "launch/apm_config.yaml"),
    ("auv_dvl_a50", "launch/dvl_a50.launch.py"),
])
def test_missing_workspace_launch_or_config_is_reported(
        ros_package_shares, package, relative_file):
    (ros_package_shares[package] / relative_file).unlink()
    with pytest.raises(RuntimeError, match=f"{package}/{relative_file}"):
        LaunchManager()._check_dependencies("rov", ROV_DEFAULTS)


def test_realsense_dependency_check_does_not_require_vehicle(ros_package_shares):
    del ros_package_shares["auv"]
    del ros_package_shares["auv_dvl_a50"]
    LaunchManager()._check_dependencies("realsense", {})


def test_imx219_dependency_check_does_not_require_realsense_or_vehicle(ros_package_shares):
    del ros_package_shares["auv"]
    del ros_package_shares["auv_dvl_a50"]
    del ros_package_shares["realsense2_camera"]
    LaunchManager()._check_dependencies("imx219", {})


def test_missing_single_imx219_launch_is_reported(ros_package_shares):
    (ros_package_shares["auv_imx219_camera"] / "launch/single_imx219.launch.py").unlink()
    with pytest.raises(RuntimeError, match="auv_imx219_camera/launch/single_imx219.launch.py"):
        LaunchManager()._check_dependencies("imx219", {})


def test_camera_preview_returns_bytes_without_caching_and_handles_missing_frames(console, monkeypatch):
    client, _, ros = console
    assert client.get("/api/cameras/unknown/frame").status_code == 404
    assert client.get("/api/cameras/imx219/frame").status_code == 503
    monkeypatch.setattr(ros, "camera_frame", lambda camera: (b"jpeg-test", "image/jpeg"))
    response = client.get("/api/cameras/imx219/frame")
    assert response.content == b"jpeg-test"
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "no-store"


def test_dvl_command_api_forwards_valid_commands_and_reports_rejections(console, monkeypatch):
    from auv_vla_gui.dvl import validate_command
    client, _, ros = console
    assert client.post("/api/dvl/command", json={"command": "get_config"}).status_code == 503
    calls = []

    def command(name, parameter_name, parameter_value):
        validate_command(name, parameter_name, parameter_value)
        calls.append((name, parameter_name, parameter_value))

    monkeypatch.setattr(ros, "dvl_command", command)
    assert client.post("/api/dvl/command", json={
        "command": "set_config", "parameter_name": "acoustic_enabled", "parameter_value": "true",
    }).status_code == 200
    assert calls == [("set_config", "acoustic_enabled", "true")]
    assert client.post("/api/dvl/command", json={"command": "unknown"}).status_code == 400
    assert len(calls) == 1

    def calibrating(*args):
        raise RuntimeError("calibration in progress")

    monkeypatch.setattr(ros, "dvl_command", calibrating)
    assert client.post("/api/dvl/command", json={"command": "get_config"}).status_code == 409
