from pathlib import Path
import subprocess
import sys
import time

from fastapi.testclient import TestClient
import pytest

from auv_vla_gui.launch_manager import LaunchManager, ROV_DEFAULTS
from auv_vla_gui.server import create_app
from kmu26_auv_web_gui.process_manager import ManagedProcess


WEB_DIR = Path(__file__).resolve().parents[1] / "web"


class FakeRos:
    def __init__(self):
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def status(self):
        return {"mavros_state": {"connected": False}, "path": []}


class FakeProcess:
    calls = []

    def __init__(self, name, cmd, logs):
        self.cmd = cmd
        self.is_running = False
        self.process = self
        self.pid = 123
        self.return_code = None
        self.stopped = False

    def start(self):
        self.is_running = True
        self.calls.append(self.cmd)

    def stop(self):
        self.is_running = False
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
        "ros2", "launch", "hit25_auv_ros2", "rov_start.launch.py",
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
    assert all(item["running"] for item in manager.snapshot()["launches"])
    assert client.post("/api/launches/realsense/start", json={}).status_code == 409
    assert len(FakeProcess.calls) == 2
    assert client.post("/api/launches/realsense/stop").status_code == 200
    assert manager._processes["rov"].is_running
    assert not manager._processes["realsense"].is_running
    assert client.post("/api/launches/realsense/start", json={}).status_code == 200
    manager.stop_all()
    assert all(process.stopped for process in manager._processes.values())


def test_imx219_placeholder_cannot_start_a_guessed_launch(console):
    client, _, _ = console
    assert client.post("/api/launches/imx219/start", json={}).status_code == 404
    assert not FakeProcess.calls
