#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source_setup() {
  if [[ -f "$1" ]]; then
    set +u
    # shellcheck source=/dev/null
    source "$1"
    set -u
  fi
}

source_setup "/opt/ros/${ROS_DISTRO:-humble}/setup.bash"
for candidate in "${AUV_VLA_GUI_WORKSPACE_DIR:-${SCRIPT_DIR}/../../..}/install/setup.bash" "${SCRIPT_DIR}/../../../../setup.bash"; do
  if [[ -f "${candidate}" ]]; then
    source_setup "${candidate}"
    break
  fi
done

VLA_PYTHON="${AUV_VLA_GUI_PYTHON:-/usr/bin/python3}"
if ! command -v ros2 >/dev/null 2>&1; then
  echo "ROS 2 환경을 찾을 수 없습니다. ROS 2와 워크스페이스를 빌드하고 source하세요." >&2
  exit 1
fi
if ! ros2 pkg prefix auv_vla_gui >/dev/null 2>&1; then
  echo "auv_vla_gui가 빌드되지 않았습니다. 워크스페이스에서 colcon build --symlink-install --packages-select auv_vla_gui를 실행하세요." >&2
  exit 1
fi
if ! "${VLA_PYTHON}" -c 'import fastapi, uvicorn, websockets, rclpy, mavros_msgs, auv_web_gui, auv_vla_gui' 2>/dev/null; then
  echo "GUI 의존성이 없습니다. 패키지 README.md의 설치 및 빌드 절차를 확인하세요." >&2
  exit 1
fi
echo "KMU26 VLA GUI: http://<robot-ip>:${AUV_VLA_GUI_PORT:-8081}"
echo "웹에서 ROV 시작 버튼을 누르면 rov_start.launch.py가 실행됩니다."
exec ros2 launch auv_vla_gui gui_server.launch.py \
  host:="${AUV_VLA_GUI_HOST:-0.0.0.0}" \
  port:="${AUV_VLA_GUI_PORT:-8081}" \
  python_executable:="${VLA_PYTHON}"
