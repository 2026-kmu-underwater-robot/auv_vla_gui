"use strict";

const $ = (id) => document.getElementById(id);
const topicNames = {mavros_state: "MAVROS", joy: "조이스틱", battery: "배터리", dvl: "DVL 속도", imu: "IMU", depth: "수심", odom: "위치 추정"};
const topicLabels = {mavros_state: "MAV", joy: "JOY", battery: "BAT", dvl: "DVL", imu: "IMU", depth: "DEPTH", odom: "ODOM"};
const controls = {
  rov: {form: "launch-form", button: "launch-button", label: "button-label", dot: "button-status", name: "ROV"},
  realsense: {form: "realsense-form", button: "realsense-button", label: "realsense-label", dot: "realsense-status", name: "리얼센스 카메라"},
  imx219: {form: "imx219-form", button: "imx219-button", label: "imx219-label", dot: "imx219-status", name: "IMX219 카메라"},
};
let launches = {};
let online = false;
let rosStatus = {};
const pending = new Map();
let lastLogText = "";
let lastReceived = 0;
let dvlBusy = false;
let dvlError = "";
const previews = new Map(["realsense", "imx219"].map((id) => [id, {busy: false, url: null, generation: 0}]));

Object.entries(topicNames).forEach(([key, label]) => {
  const pill = document.createElement("span");
  pill.id = `pill-${key}`;
  pill.className = "pill";
  pill.textContent = `${topicLabels[key]} OFF`;
  pill.title = `${label} · 수신 대기`;
  $("topics").append(pill);
});

function number(value, digits = 2) {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "—";
}

function updateButtons() {
  Object.entries(controls).forEach(([id, control]) => {
    const launch = launches[id];
    const running = Boolean(launch?.running);
    const action = pending.get(id);
    const canStop = Boolean(launch?.can_stop);
    $(control.button).disabled = !online || Boolean(action) || !launch || running || canStop;
    $(control.label).textContent = `${control.name} ${action === "start" ? "시작 중…" : running ? "실행 중" : "시작"}`;
    $(id + "-stop").disabled = !online || Boolean(action) || !canStop;
    $(id + "-stop").textContent = `${launch?.title || control.name} 노드 ${action === "stop" ? "종료 중…" : "종료"}`;
    const connected = id === "rov" ? rosStatus.topics?.mavros_state?.alive && rosStatus.mavros_state?.connected : rosStatus.topics?.[id]?.alive;
    const ready = Boolean(online && running && connected);
    $(control.dot).classList.toggle("ready", ready);
    const status = !online ? "서버 연결 끊김" : ready ? `${control.name} 정상 수신` : running ? `${control.name} ${id === "rov" ? "FCU 연결" : "영상 수신"} 대기` : launch?.state === "failed" ? `${control.name} 실행 실패` : `${control.name} 정지`;
    $(control.dot).title = status;
    $(control.button).setAttribute("aria-description", status);
  });
}

function connection(connected) {
  online = connected;
  $("connection").textContent = connected ? "● 서버 연결됨" : "● 서버 연결 끊김 · 재연결 중";
  $("connection").className = `connection ${connected ? "online" : "offline"}`;
  if (!connected) {
    ["mavros-connected", "mode", "armed", "depth", "voltage", "yaw", "battery-current", "battery-soc", "battery-temperature", "dvl-fom", "dvl-altitude", "dvl-beams", "dvl-config", "dvl-mounting", "dvl-config-time"].forEach((id) => { $(id).textContent = "—"; $(id).classList.remove("good", "warning"); });
    Object.keys(topicNames).forEach((key) => {
      const pill = $(`pill-${key}`);
      pill.className = "pill";
      pill.textContent = `${topicLabels[key]} OFF`;
      pill.title = `${topicNames[key]} · 서버 연결 끊김`;
    });
    $("position").textContent = "X —   Y —   Z —";
    $("joy").textContent = "연결 끊김";
    $("dvl-velocity").textContent = "X — Y — Z —";
    $("dvl-quality").textContent = "연결 끊김";
    $("dvl-quality").className = "camera-state";
    $("dvl-calibration").textContent = "연결 끊김";
    $("dvl-response").textContent = "서버 연결 끊김";
    previews.forEach((_, id) => hidePreview(id, "서버 연결 끊김"));
  }
  updateButtons();
  updateDvlButtons();
}

function render(data) {
  lastReceived = Date.now();
  launches = Object.fromEntries(data.launches.map((item) => [item.id, item]));
  rosStatus = data.ros || {};
  connection(true);
  const failed = data.launches.find((item) => item.state === "failed");
  if (failed) {
    $("error").textContent = failed.error || `${failed.title} 실행 실패 · 종료 코드 ${failed.return_code}`;
    $("error").hidden = false;
  }
  const ros = data.ros || {};
  const topics = ros.topics || {};
  const fresh = (key) => Boolean(topics[key]?.alive);
  const mav = ros.mavros_state || {};
  $("mavros-connected").textContent = fresh("mavros_state") ? (mav.connected ? "연결됨" : "FCU 미연결") : "상태 대기";
  $("mavros-connected").className = fresh("mavros_state") && mav.connected ? "good" : "";
  $("mode").textContent = fresh("mavros_state") ? (mav.mode || "—") : "—";
  $("armed").textContent = fresh("mavros_state") ? (mav.armed ? "ARMED" : "DISARMED") : "—";
  $("armed").className = fresh("mavros_state") && mav.armed ? "warning" : "";
  $("depth").textContent = fresh("depth") && typeof ros.depth?.z === "number" ? number(-ros.depth.z) : "—";
  const battery = fresh("battery") && ros.battery?.present !== false ? ros.battery || {} : {};
  $("voltage").textContent = number(battery.voltage, 1);
  $("battery-current").textContent = number(battery.current, 1);
  $("battery-soc").textContent = typeof battery.percentage === "number" ? number(battery.percentage * 100, 0) : "—";
  $("battery-temperature").textContent = number(battery.temperature, 1);
  $("yaw").textContent = fresh("odom") && typeof ros.pose?.yaw === "number" ? number(ros.pose.yaw * 180 / Math.PI, 1) : "—";
  $("position").textContent = fresh("odom") ? `X ${number(ros.pose?.x)}   Y ${number(ros.pose?.y)}   Z ${number(ros.pose?.z)} m` : "X —   Y —   Z —";
  Object.keys(topicNames).forEach((key) => {
    const health = topics[key];
    const alive = Boolean(health?.alive);
    const pill = $(`pill-${key}`);
    pill.className = `pill ${alive ? "alive" : health?.age != null ? "stale" : ""}`;
    pill.textContent = `${topicLabels[key]} ${alive ? "ON" : "OFF"}`;
    const detail = alive ? `${number(health.hz, 1)} Hz` : health?.age != null ? `${number(health.age, 1)}s 전 수신` : "수신 대기";
    pill.title = `${topicNames[key]} · ${health?.name || ""} · ${detail}`;
  });
  $("joy").textContent = fresh("joy") ? (ros.joy?.axes || []).map((axis) => number(axis, 2)).join(" / ") || "축 입력 없음" : "입력 대기";
  renderDvl(ros);
  previews.forEach((_, id) => {
    if (!fresh(`${id}_preview`)) hidePreview(id, "영상 수신 대기");
    else if (!$(id + "-preview").hidden) updatePreviewStatus(id);
  });
  const logText = (data.logs || []).join("\n");
  if (logText !== lastLogText) {
    const logs = $("logs");
    const atBottom = logs.scrollHeight - logs.scrollTop - logs.clientHeight < 35;
    logs.textContent = logText || "아직 실행된 런치가 없습니다.";
    if (atBottom) logs.scrollTop = logs.scrollHeight;
    lastLogText = logText;
  }
  $("updated").textContent = `마지막 수신 ${new Date().toLocaleTimeString("ko-KR")}`;
}

async function launchAction(id, action) {
    if (!online || pending.has(id) || !launches[id]) return;
    if (action === "start" && (launches[id].running || launches[id].can_stop)) return;
    if (action === "stop" && !launches[id].can_stop) return;
    pending.set(id, action);
    $("error").hidden = true;
    updateButtons();
    try {
      const response = await fetch(`/api/launches/${id}/${action}`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({})});
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "런치 요청을 처리하지 못했습니다.");
      render(result);
    } catch (error) {
      $("error").textContent = error.message;
      $("error").hidden = false;
    } finally {
      pending.delete(id);
      updateButtons();
    }
}

Object.entries(controls).forEach(([id, control]) => {
  $(control.form).addEventListener("submit", (event) => {
    event.preventDefault();
    launchAction(id, "start");
  });
  $(id + "-stop").addEventListener("click", () => launchAction(id, "stop"));
});

function updateDvlButtons() {
  const disabled = !online || dvlBusy || !(rosStatus.dvl_command_subscribers > 0) || rosStatus.dvl_calibration?.state === "calibrating";
  document.querySelectorAll("[data-dvl-command], #dvl-apply").forEach((button) => { button.disabled = disabled; });
}

function renderDvl(ros) {
  const alive = Boolean(ros.topics?.dvl_data?.alive);
  const quality = alive ? ros.dvl_quality || {} : {};
  $("dvl-quality").textContent = alive ? quality.reason || "확인 중" : "수신 대기";
  $("dvl-quality").className = `camera-state ${alive ? quality.good ? "good" : "warning" : ""}`;
  $("dvl-fom").textContent = number(quality.fom, 3);
  $("dvl-altitude").textContent = number(quality.altitude);
  $("dvl-beams").textContent = typeof quality.valid_beams === "number" ? `${quality.valid_beams} / 4` : "—";
  const velocity = quality.velocity || {};
  $("dvl-velocity").textContent = `X ${number(velocity.x)}  Y ${number(velocity.y)}  Z ${number(velocity.z)}`;
  const config = ros.dvl_config || {};
  $("dvl-config").textContent = config.updated_at ? `음향 ${config.acoustic_enabled ? "ON" : "OFF"} · 다크 ${config.dark_mode_enabled ? "ON" : "OFF"} · 범위 ${config.range_mode}` : "설정 조회 대기";
  $("dvl-mounting").textContent = config.updated_at ? `${number(config.speed_of_sound, 0)} m/s · ${number(config.mounting_rotation_offset, 1)}°` : "—";
  $("dvl-config-time").textContent = config.updated_at || "—";
  $("dvl-calibration").textContent = ros.dvl_calibration?.message || "보정 대기";
  const response = ros.dvl_response || {};
  $("dvl-response").textContent = dvlError || (response.command ? `${response.command}: ${response.message}` : ros.dvl_command_subscribers > 0 ? "명령 대기" : "DVL 실행 대기");
  $("dvl-response").className = `command-status ${dvlError || response.success === false ? "warning" : ""}`;
  $("dvl-events").textContent = (ros.dvl_events || []).join("\n") || "장치 응답 대기";
  updateDvlButtons();
}

async function sendDvlCommand(command, parameter_name = "", parameter_value = "") {
  if (!online || dvlBusy) return;
  dvlBusy = true;
  dvlError = "";
  updateDvlButtons();
  try {
    const response = await fetch("/api/dvl/command", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({command, parameter_name, parameter_value})});
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "DVL 명령을 처리하지 못했습니다.");
    render(result);
  } catch (error) {
    dvlError = error.message;
    renderDvl(rosStatus);
  } finally {
    dvlBusy = false;
    updateDvlButtons();
  }
}

document.querySelectorAll("[data-dvl-command]").forEach((button) => {
  button.addEventListener("click", () => sendDvlCommand(button.dataset.dvlCommand, button.dataset.dvlParam || "", button.dataset.dvlValue || ""));
});
$("dvl-setting-form").addEventListener("submit", (event) => {
  event.preventDefault();
  sendDvlCommand("set_config", $("dvl-parameter").value, $("dvl-value").value.trim());
});
$("dvl-parameter").addEventListener("change", () => {
  const key = $("dvl-parameter").value;
  $("dvl-value").value = rosStatus.dvl_config?.[key] ?? {speed_of_sound: "1500", mounting_rotation_offset: "0", range_mode: "auto"}[key];
});

function hidePreview(id, message) {
  const state = previews.get(id);
  state.generation += 1;
  if (state.url) URL.revokeObjectURL(state.url);
  state.url = null;
  $(id + "-preview").hidden = true;
  $(id + "-preview").removeAttribute("src");
  $(id + "-preview-empty").hidden = false;
  $(id + "-preview-empty").textContent = message;
  $(id + "-preview-state").textContent = message;
}

function updatePreviewStatus(id) {
  const health = rosStatus.topics?.[`${id}_preview`];
  $(id + "-preview-state").textContent = `영상 수신 중 · ${number(health?.hz, 1)} Hz`;
}

async function refreshPreview(id) {
  const state = previews.get(id);
  if (document.hidden || !online || !rosStatus.topics?.[`${id}_preview`]?.alive || state.busy) return;
  state.busy = true;
  const generation = state.generation;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 2500);
  try {
    const response = await fetch(`/api/cameras/${id}/frame`, {cache: "no-store", signal: controller.signal});
    if (!response.ok) throw new Error("영상 수신 대기");
    const blob = await response.blob();
    if (!online || generation !== state.generation) return;
    const url = URL.createObjectURL(blob);
    if (state.url) URL.revokeObjectURL(state.url);
    state.url = url;
    $(id + "-preview").src = url;
    $(id + "-preview").hidden = false;
    $(id + "-preview-empty").hidden = true;
    updatePreviewStatus(id);
  } catch {
    if (generation === state.generation) hidePreview(id, "영상 수신 대기");
  } finally {
    clearTimeout(timeout);
    state.busy = false;
  }
}
setInterval(() => previews.forEach((_, id) => refreshPreview(id)), 200);

function connect() {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${location.host}/ws/status`);
  socket.onmessage = (event) => {
    try { render(JSON.parse(event.data)); } catch { connection(false); }
  };
  socket.onerror = () => socket.close();
  socket.onclose = () => { connection(false); setTimeout(connect, 1500); };
  const watchdog = setInterval(() => {
    if (online && Date.now() - lastReceived > 4000) { connection(false); socket.close(); }
  }, 1000);
  socket.addEventListener("close", () => clearInterval(watchdog));
}
connect();
