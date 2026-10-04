# KMU26 VLA ROV GUI

저장소와 ROS 2 패키지, Python 모듈 이름은 모두 `auv_vla_gui`입니다.
아래 실행 예제와 같은 폴더 구조로 받으려면 워크스페이스 루트에서 실행하세요.

```bash
git clone https://github.com/2026-kmu-underwater-robot/auv_vla_gui.git src/auv_vla_gui
```

기존 `kmu26_auv_web_gui` 패키지의 프로세스 관리 코드를 사용하므로 해당 패키지도
워크스페이스에 있어야 합니다.

기존 웹 GUI와 같은 FastAPI + ROS 2 브리지 방식의 별도 GUI입니다.
**버튼 하나가 ROS 런치 하나를 소유합니다.** 실행 영역에는 ROV, 리얼센스 카메라,
IMX219 카메라 버튼을 나란히 표시합니다.

- `ROV 시작` → `ros2 launch hit25_auv_ros2 rov_start.launch.py`
- 같은 버튼이 `ROV 정지`로 바뀌며 해당 런치와 하위 프로세스를 정지합니다.
- 리얼센스는 `ros2 launch realsense2_camera rs_launch.py` 하나를 독립적으로 시작·정지합니다.
- IMX219는 런치를 연결하지 않은 준비 버튼입니다. 실행 요청을 보내지 않습니다.
- ROV와 리얼센스는 함께 실행할 수 있으며 한 버튼을 정지해도 다른 런치는 유지됩니다.
- 상태 도트는 런치 실행과 실제 FCU 연결/카메라 영상 수신이 모두 확인되면 초록입니다.
- FCU 주소, DVL IP, DVL 사용, 위치 추정 사용은 서버 기본 설정으로 실행합니다.
- MAVROS 연결/모드/ARM 상태, 조이스틱, DVL, IMU, 수심, 위치 추정,
  배터리와 런치 로그를 실시간 표시합니다.
- 서버 시작 시에는 ROS 상태 브리지만 시작합니다. ROV는 버튼을 눌러 실행합니다.
- 서버 종료 시 이 GUI에서 실행한 모든 런치를 정리합니다.
- 기체 상태는 작은 요약 카드, 로그는 화면 폭의 약 절반으로 나란히 표시합니다.

리얼센스 런치는 [최신 기존 GUI의 실행 코드](https://github.com/ranbier/kmu26_auv_web_gui/blob/a3d3864d46ae1dbf991db598fc90e2fed05fdcbd/kmu26_auv_web_gui/process_manager.py)를 참고했습니다.
`realsense2_camera`가 설치/source되어 있어야 실행할 수 있습니다. 현재 PC에는 해당 드라이버가 없습니다.
기본 영상 상태 토픽은 `/camera/camera/color/image_raw`이고,
실제 토픽이 다르면 `AUV_VLA_GUI_REALSENSE_IMAGE_TOPIC` 환경 변수로 바꿀 수 있습니다.

## 설치 및 빌드

```bash
cd /home/kuuve/auv_ros2
source /opt/ros/humble/setup.bash
python3 -m venv --system-site-packages ~/.local/share/auv_vla_gui/venv
~/.local/share/auv_vla_gui/venv/bin/python -m pip install fastapi uvicorn websockets
colcon build --base-paths src --build-base build_vla_gui \
  --install-base install_vla_gui --symlink-install \
  --packages-ignore mavros_msgs \
  --packages-select dvl_msgs hit25_auv_ros2 kmu26_auv_web_gui auv_vla_gui \
  --cmake-args -DBUILD_TESTING=OFF
source install_vla_gui/setup.bash
```

기존 워크스페이스의 절대 경로가 남은 설치물과 구분하기 위해 별도 빌드/설치 디렉터리를 사용합니다.
실제 ROV 실행에는 MAVROS 및 기존 ROV의 의존 패키지가 필요합니다.
기본값인 위치 추정을 사용하려면 `robot_localization`이 설치되어 있어야 하고,
DVL을 사용하려면 `dvl_a50`을 빌드/source해야 합니다.
실행 스크립트는 이 워크스페이스의 기존 `install/dvl_a50`이 있으면 함께 source합니다.
실행 전 필요한 패키지와 런치 파일을 검사하므로 의존성이 빠지면 오류를 표시합니다.
이 PC에서는 `robot_localization`을 추가 설치해야 기본 위치 추정을 실행할 수 있습니다.
새로 DVL을 빌드할 때는 `nlohmann-json3-dev`도 필요합니다.

```bash
sudo apt install ros-humble-robot-localization nlohmann-json3-dev
source /opt/ros/humble/setup.bash
source install_vla_gui/setup.bash
colcon build --base-paths src --build-base build_vla_gui \
  --install-base install_vla_gui --symlink-install \
  --packages-ignore mavros_msgs --packages-select dvl_a50 \
  --cmake-args -DBUILD_TESTING=OFF
source install_vla_gui/setup.bash
```

소스 폴더 이름은 `kmu26_auv`지만 기존 ROV의 ROS 패키지 이름은 `hit25_auv_ros2`입니다.

## 실행

로봇 PC에서:

```bash
./src/auv_vla_gui/scripts/start_auv_vla_gui.sh
```

브라우저에서 `http://<robot-ip>:8081`에 접속합니다. 로봇 PC 자체에서는
`http://localhost:8081`을 사용합니다. 기존 GUI의 기본 포트는 8080입니다.

포트 변경:

```bash
AUV_VLA_GUI_PORT=8091 ./src/auv_vla_gui/scripts/start_auv_vla_gui.sh
```

ROS 실행도 지원합니다. `ros2 run`은 ROS 실행 환경의 Python에 웹 의존성이 필요합니다.
`gui_server.launch.py`는 위에서 만든 가상환경 Python을 자동으로 사용하며,
`python_executable` 런치 인자로 변경할 수 있습니다.

```bash
ros2 run auv_vla_gui server --host 0.0.0.0 --port 8081
ros2 launch auv_vla_gui gui_server.launch.py port:=8081
```

ROV 제어는 기존 `/joy` → `joy2mavros` 경로를 사용합니다. 조이스틱을 연결한 PC의
`joy_node`가 같은 ROS_DOMAIN_ID와 DDS 네트워크에서 `/joy`를 발행해야 합니다.
ROV 시작 버튼은 ARM이나 모드 변경을 자동 요청하지 않습니다.

수심은 기존 `/depth/pose`의 ENU Z 값에 부호를 반전해 아래 방향을 양수로 표시합니다.
토픽 수신이 오래되거나 서버 연결이 끊기면 해당 수치는 `—`로 표시합니다.
기존 GUI 등에서 동일한 ROV 런치를 동시에 실행하지 마세요.

## 개발 검증

```bash
source /opt/ros/humble/setup.bash
source install_vla_gui/setup.bash
~/.local/share/auv_vla_gui/venv/bin/python -m pip install 'pytest>=7,<9' httpx
PYTHONPATH=src/auv_vla_gui:src/kmu26_auv_web_gui \
  ~/.local/share/auv_vla_gui/venv/bin/python -m pytest src/auv_vla_gui/test -q
```

테스트는 가짜 ROS 브리지와 테스트용 프로세스를 사용해 실제 기체를 실행하지 않습니다.
