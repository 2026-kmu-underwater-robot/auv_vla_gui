# KMU26 VLA ROV GUI

ROS 2 Humble 기반의 별도 웹 GUI입니다. 현재 워크스페이스는
`/home/auv/catkin_ws`이며, `auv_web_gui`의 프로세스 관리 코드를 사용합니다.

## 설치 및 빌드

현재 워크스페이스의 `auv`, `auv_dvl_a50`, `auv_dvl_a50_msg`, `auv_web_gui`,
`mavros`, `realsense2_camera`, `auv_imx219_camera`와 시스템의 `robot_localization`을 사용합니다.
기존 패키지가 빌드되어 있다면 VLA GUI만 추가 빌드하면 됩니다.

```bash
cd /home/auv/catkin_ws
source /opt/ros/humble/setup.bash
source install/setup.bash
colcon build --symlink-install --packages-select auv_vla_gui
source install/setup.bash
```

새 환경에서는 ROS Python에 웹 의존성을 설치하고 관련 패키지도 빌드하세요.

```bash
sudo apt install python3-fastapi python3-uvicorn python3-websockets \
  ros-humble-robot-localization nlohmann-json3-dev
colcon build --symlink-install \
  --packages-select auv_dvl_a50_msg auv_dvl_a50 auv auv_web_gui auv_vla_gui \
  --cmake-args -DBUILD_TESTING=OFF
source install/setup.bash
```

위 빌드는 MAVROS와 리얼센스가 이미 설치/source된 환경을 전제로 합니다.

## ROS 실행

```bash
cd /home/auv/catkin_ws
source /opt/ros/humble/setup.bash
source install/setup.bash
ros2 launch auv_vla_gui gui_server.launch.py
```

브라우저에서 `http://localhost:8081`에 접속합니다. 다른 PC에서는
`http://<robot-ip>:8081`을 사용합니다. 기존 웹 GUI도 기본 포트가 8081이므로
함께 실행할 때는 한쪽 포트를 변경하세요.

호스트/포트 변경:

```bash
ros2 launch auv_vla_gui gui_server.launch.py host:=127.0.0.1 port:=8091
```

기본적으로 ROS 런치를 실행한 Python을 사용합니다. 다른 Python을 쓰려면
`python_executable:=/절대/경로/python` 또는 `AUV_VLA_GUI_PYTHON`을 지정하세요.
해당 Python에서 ROS 메시지와 웹 의존성을 모두 import할 수 있어야 합니다.

편의 스크립트도 같은 ROS 런치를 실행합니다. ROS Humble과 현재 워크스페이스의
`install/setup.bash`를 자동으로 source하고 기본적으로 `/usr/bin/python3`을 사용합니다.

```bash
./src/auv_vla_gui/scripts/start_auv_vla_gui.sh
# 포트 변경
AUV_VLA_GUI_PORT=8091 ./src/auv_vla_gui/scripts/start_auv_vla_gui.sh
```

서버만 직접 실행할 수도 있습니다.

```bash
ros2 run auv_vla_gui server --host 0.0.0.0 --port 8081
```

## 버튼과 ROS 상태

**버튼 하나가 ROS 런치 하나를 소유합니다.**

- `ROV 시작` → `ros2 launch auv rov_start.launch.py`
- `ROV 노드 종료` → MAVROS, DVL, 위치 추정, 배터리 등 해당 런치의 하위 노드 종료
- 리얼센스 → `ros2 launch realsense2_camera rs_launch.py` 독립 시작·정지
- IMX219 → `ros2 launch auv_imx219_camera single_imx219.launch.py` 한 대 시작·정지

ROV, 리얼센스, IMX219는 함께 실행할 수 있으며, 한 버튼을 정지해도 다른 런치는 유지됩니다.
각 시작 버튼 아래에 별도의 `노드 종료` 버튼이 있습니다. 실행 중에는 시작 버튼이
비활성화되고, 종료 버튼으로 해당 런치와 하위 노드를 정리한 뒤 다시 시작할 수 있습니다.
런치 부모가 먼저 종료되어도 남은 프로세스 그룹을 종료 버튼으로 정리할 수 있습니다.
서버 시작 시에는 ROS 상태 브리지만 시작하고, ROV는 버튼을 눌러 실행합니다.
서버를 `Ctrl+C` 또는 `SIGTERM`으로 종료하면 이 GUI에서 실행한 세 런치를 함께
종료하기 전에 DVL에 `acoustic_enabled=false`를 전송합니다. 장치 ACK를 받은 뒤
설정을 조회해 음향 OFF를 확인하고 노드 종료를 진행합니다. 자이로 보정 중이면
먼저 보정 응답/기존 제한 시간(최대 20초)을 기다리고, 음향 OFF 확인에는 최대
5초를 기다립니다. DVL이 연결되지 않았거나 응답이 없으면 OFF 확인 불가를
로그에 남기고 노드 정리는 계속합니다. 하위 노드까지 정리한 뒤 ROS 상태 브리지를 종료합니다. 종료 중에는
새로운 시작 요청을 받지 않습니다. 브라우저 탭을 닫으면 서버는 계속 실행되므로
전체 종료 시에는 GUI를 실행한 터미널에서 `Ctrl+C`를 누르세요.

ROV 기본 설정은 `fcu_url:=/dev/ttyACM0:57600`, `dvl_ip:=192.168.194.95`,
`use_dvl:=true`, `use_localization:=true`입니다. 실행 전에 `auv`의 ROV/MAVROS
런치와 설정 파일, MAVROS, DVL 및 위치 추정 패키지를 검사합니다.

MAVROS 연결/모드/ARM, 조이스틱, DVL, IMU, 수심, 위치 추정, 배터리와 런치 로그를
표시합니다. 상태 도트는 런치 실행과 실제 FCU 연결/카메라 영상 수신이 모두
확인되면 초록입니다. 수심은 `/depth/pose`의 ENU Z 값에 부호를 반전해 표시합니다.
토픽 수신이 오래되거나 서버 연결이 끊기면 해당 수치는 `—`로 표시합니다.

리얼센스의 영상·상태 토픽 기본값은 `/camera/camera/color/image_raw/compressed`입니다.

IMX219는 현재 연결된 `sensor_id=0` 한 대를 기본 1280×720, 30fps로 실행합니다.
영상·상태 토픽은 `/imx219/camera0/image_raw/compressed`입니다.
카메라 패키지를 변경한 뒤에는 다음과 같이 함께 빌드하세요.

```bash
colcon build --symlink-install --packages-select auv_imx219_camera auv_vla_gui
source install/setup.bash
```

## 영상, DVL, 배터리

리얼센스와 IMX219 영상을 두 개의 미리보기 창으로 표시합니다. ROS의 기존
`sensor_msgs/CompressedImage` 토픽을 사용하고, 브라우저 미리보기는 최대 5fps로
갱신합니다. 카메라의 ROS 발행 프레임레이트는 변경하지 않습니다.
두 카메라 모두 raw `sensor_msgs/Image`는 구독하지 않습니다. 각 미리보기의
`영상 수신 중 · 30.0 Hz` 표시는 최근 2초 동안 서버가 받은 compressed 토픽의
실제 수신 주파수이며, 브라우저 화면 갱신 주파수와는 별개입니다.
2초 이상 새 영상이 없거나 서버 연결이 끊기면 이전 영상을 숨깁니다.
미리보기 토픽은 `AUV_VLA_GUI_REALSENSE_PREVIEW_TOPIC`,
`AUV_VLA_GUI_IMX219_PREVIEW_TOPIC`으로 바꿀 수 있습니다. 기존 `*_IMAGE_TOPIC`
환경 변수도 지원하며, raw 기본 경로를 지정하면 `/compressed`를 붙이고 이미
`/compressed`로 끝나는 경로는 그대로 사용합니다. `*_PREVIEW_TOPIC`이 우선합니다.
카메라 드라이버의 `compressed_image_transport`가 필요합니다.

DVL 진단은 `/dvl/data`에서 속도 XYZ, FOM, 고도, 유효 빔 수를 읽습니다.
기존 GUI와 같은 품질 기준(속도 유효, FOM ≤ 0.05, 고도 ≥ 0.05m, 유효 빔 4개)을
사용하며, 1초 이상 수신이 없으면 진단 수치를 대기 상태로 바꿉니다.

DVL 설정 카드에서 설정 조회, 음향/다크 On·Off, 범위 Auto/1, 음속·장착 각도·범위
설정, 자이로 보정, DR 초기화를 요청할 수 있습니다. 명령은 기존
`/dvl/config/command`로 전송하고 `/dvl/config/status`, `/dvl/command/response`에서
장치 응답을 표시합니다. 설정 조회 값에는 마지막 확인 시각을 함께 표시합니다.
설정 변경(`set_config`)이 성공했다는 장치 응답을 받으면 자동으로 `get_config`를
요청해 표시 값을 갱신합니다. 변경 실패 시에는 마지막으로 확인한 설정을 유지합니다.
자이로 보정 중에는 다른 명령을 막고, 20초 안에 해당 명령의 ACK가 없으면
완료로 표시하지 않고 결과 미확인 상태로 바꿉니다.

배터리 카드는 `/battery`의 전압, 전류, 잔량(%), 온도를 표시합니다.
미수신·오래된 값·NaN 및 알 수 없는 잔량은 `—`로 표시합니다.

ROV 제어는 기존 `/joy` → `joy2mavros` 경로를 사용합니다. 조이스틱을 연결한 PC의
`joy_node`가 같은 `ROS_DOMAIN_ID`와 DDS 네트워크에서 `/joy`를 발행해야 합니다.
ROV 시작 버튼은 ARM이나 모드 변경을 자동 요청하지 않습니다.
기존 GUI 등에서 동일한 ROV 런치를 동시에 실행하지 마세요.

## 개발 검증

ROS 환경을 source한 뒤 테스트 의존성을 설치하고 실행합니다.

```bash
cd /home/auv/catkin_ws
source /opt/ros/humble/setup.bash
source install/setup.bash
python3 -m pip install --user 'pytest>=7,<9' httpx
python3 -m pytest src/auv_vla_gui/test -v
```

ROS 빌드 도구로도 같은 테스트를 실행할 수 있습니다.

```bash
colcon test --packages-select auv_vla_gui --event-handlers console_direct+
colcon test-result --test-result-base build/auv_vla_gui --verbose
```

빌드 전 소스를 직접 테스트하려면:

```bash
PYTHONPATH=src/auv_vla_gui:src/auv_web_gui \
  python3 -m pytest src/auv_vla_gui/test -v
```

테스트는 가짜 ROS 브리지와 테스트용 프로세스를 사용해 실제 기체를 실행하지 않습니다.
ROS 구독 연결은 서버 실행 후 `ros2 node info /auv_vla_gui_monitor`로 확인할 수 있습니다.
브라우저 및 `/api/status`에서 실행 전 상태를 확인한 뒤, 실제 장치가 연결된 환경에서
각 버튼과 센서 수신을 확인하세요.
