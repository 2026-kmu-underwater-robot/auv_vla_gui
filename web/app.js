"use strict";

const $ = (id) => document.getElementById(id);
const topicNames = {mavros_state: "MAVROS", joy: "조이스틱", battery: "배터리", dvl: "DVL 속도", imu: "IMU", depth: "수심", odom: "위치 추정"};
const topicLabels = {mavros_state: "MAV", joy: "JOY", battery: "BAT", dvl: "DVL", imu: "IMU", depth: "DEPTH", odom: "ODOM"};
const controls = {
  rov: {form: "launch-form", button: "launch-button", label: "button-label", dot: "button-status", name: "ROV"},
  realsense: {form: "realsense-form", button: "realsense-button", label: "realsense-label", dot: "realsense-status", name: "리얼센스 카메라"},
};
let launches = {};
let online = false;
let rosStatus = {};
const pending = new Map();
let lastLogText = "";
let lastReceived = 0;

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
    $(control.button).disabled = !online || Boolean(action) || !launch;
    $(control.button).classList.toggle("stop", running);
    $(control.label).textContent = action ? `${control.name} ${action === "start" ? "시작" : "정지"} 중…` : `${control.name} ${running ? "정지" : "시작"}`;
    const connected = id === "rov" ? rosStatus.topics?.mavros_state?.alive && rosStatus.mavros_state?.connected : rosStatus.topics?.realsense?.alive;
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
    ["mavros-connected", "mode", "armed", "depth", "voltage", "yaw"].forEach((id) => { $(id).textContent = "—"; $(id).classList.remove("good", "warning"); });
    Object.keys(topicNames).forEach((key) => {
      const pill = $(`pill-${key}`);
      pill.className = "pill";
      pill.textContent = `${topicLabels[key]} OFF`;
      pill.title = `${topicNames[key]} · 서버 연결 끊김`;
    });
    $("position").textContent = "X —   Y —   Z —";
    $("joy").textContent = "연결 끊김";
  }
  updateButtons();
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
  $("voltage").textContent = fresh("battery") ? number(ros.battery?.voltage, 1) : "—";
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

Object.entries(controls).forEach(([id, control]) => {
  $(control.form).addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!online || pending.has(id) || !launches[id]) return;
    const action = launches[id].running ? "stop" : "start";
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
  });
});

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
