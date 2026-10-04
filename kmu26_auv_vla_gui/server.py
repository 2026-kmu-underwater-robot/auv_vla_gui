"""Web server: explicit launch actions and read-only ROV telemetry."""

import argparse
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import uvicorn

from .launch_manager import LaunchManager


class StartRequest(BaseModel):
    launch_args: dict[str, str] = Field(default_factory=dict)


def create_app(*, manager=None, ros=None, web_dir=None):
    manager = manager if manager is not None else LaunchManager()
    if ros is None:
        from .ros_monitor import RosInterface
        ros = RosInterface()
    if web_dir is None:
        from ament_index_python.packages import get_package_share_directory
        web_dir = Path(get_package_share_directory("kmu26_auv_vla_gui")) / "web"
    web_dir = Path(web_dir)

    @asynccontextmanager
    async def lifespan(app):
        try:
            ros.start()
            yield
        finally:
            try:
                # Finish process cleanup even if ROS launch cancels server shutdown.
                manager.stop_all()
            finally:
                ros.stop()

    app = FastAPI(title="KMU26 VLA · ROV Console", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=web_dir), name="static")

    def snapshot():
        telemetry = ros.status()
        # The console needs current telemetry, not the accumulated localization path.
        telemetry.pop("path", None)
        return {**manager.snapshot(), "ros": telemetry}

    @app.get("/")
    def index():
        return FileResponse(web_dir / "index.html")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return Response(status_code=204)

    @app.get("/api/status")
    def status():
        return snapshot()

    async def launch_action(action, *args):
        try:
            await asyncio.to_thread(action, *args)
        except KeyError as exc:
            raise HTTPException(404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return await asyncio.to_thread(snapshot)

    @app.post("/api/launches/{launch_id}/start")
    async def start(launch_id: str, body: StartRequest):
        return await launch_action(manager.start, launch_id, body.launch_args)

    @app.post("/api/launches/{launch_id}/stop")
    async def stop(launch_id: str):
        return await launch_action(manager.stop, launch_id)

    @app.websocket("/ws/status")
    async def status_ws(websocket: WebSocket):
        await websocket.accept()
        try:
            while True:
                await websocket.send_json(await asyncio.to_thread(snapshot))
                await asyncio.sleep(0.5)
        except (WebSocketDisconnect, RuntimeError):
            pass

    return app


def main():
    parser = argparse.ArgumentParser(description="KMU26 VLA ROV web console")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--web-dir", type=Path)
    # launch_ros adds --ros-args/remapping flags to console executables.
    from rclpy.utilities import remove_ros_args
    args = parser.parse_args(remove_ros_args()[1:])
    class ConsoleServer(uvicorn.Server):
        def handle_exit(self, sig, frame):
            # ROS launch can forward SIGINT after the terminal already delivered it.
            self.should_exit = True

    config = uvicorn.Config(create_app(web_dir=args.web_dir), host=args.host, port=args.port)
    ConsoleServer(config).run()


if __name__ == "__main__":
    main()
