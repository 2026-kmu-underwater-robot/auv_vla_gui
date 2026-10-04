#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

source_setup() {
  if [[ -f "$1" ]]; then
    set +u
    # shellcheck source=/dev/null
    source "$1"
    set -u
  fi
}

source_setup "/opt/ros/${ROS_DISTRO:-humble}/setup.bash"
# Reuse the existing DVL runtime when it is already installed in this workspace.
DVL_PREFIX="${KMU26_WORKSPACE_DIR:-${SCRIPT_DIR}/../../..}/install/dvl_a50"
if [[ -f "${DVL_PREFIX}/share/dvl_a50/local_setup.bash" ]]; then
  COLCON_CURRENT_PREFIX="$(cd "${DVL_PREFIX}" && pwd)" source_setup "${DVL_PREFIX}/share/dvl_a50/local_setup.bash"
fi
for candidate in "${KMU26_WORKSPACE_DIR:-${SCRIPT_DIR}/../../..}/install_vla_gui/setup.bash" "${KMU26_WORKSPACE_DIR:-${SCRIPT_DIR}/../../..}/install/setup.bash" "${SCRIPT_DIR}/../../../../setup.bash"; do
  if [[ -f "${candidate}" ]]; then
    source_setup "${candidate}"
    break
  fi
done

VLA_PYTHON="${KMU26_VLA_GUI_PYTHON:-${HOME}/.local/share/kmu26_auv_vla_gui/venv/bin/python}"
if [[ ! -x "${VLA_PYTHON}" ]]; then
  VLA_PYTHON="python3"
fi
if ! command -v ros2 >/dev/null 2>&1; then
  echo "ROS 2 환경을 찾을 수 없습니다. ROS 2와 워크스페이스를 빌드하고 source하세요." >&2
  exit 1
fi
if ! "${VLA_PYTHON}" -c 'import fastapi, uvicorn, websockets, rclpy, mavros_msgs' 2>/dev/null; then
  echo "GUI 의존성이 없습니다. 패키지 README.md의 설치 및 빌드 절차를 확인하세요." >&2
  exit 1
fi

if [[ -d "${PACKAGE_DIR}/kmu26_auv_vla_gui" ]]; then
  export PYTHONPATH="${PACKAGE_DIR}:${PYTHONPATH:-}"
fi
echo "KMU26 VLA GUI: http://<robot-ip>:${KMU26_VLA_GUI_PORT:-8081}"
echo "웹에서 ROV 시작 버튼을 누르면 rov_start.launch.py가 실행됩니다."
exec "${VLA_PYTHON}" -m kmu26_auv_vla_gui.server \
  --host "${KMU26_VLA_GUI_HOST:-0.0.0.0}" \
  --port "${KMU26_VLA_GUI_PORT:-8081}" \
  --web-dir "${PACKAGE_DIR}/web"
