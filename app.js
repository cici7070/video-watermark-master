"use strict";

const state = {
  jobId: "", token: "", preview: null, width: 0, height: 0,
  boxes: [], selected: -1, pollId: null, downloadUrl: "",
  isProcessing: false, isResetting: false, downloadStarted: false
};

const $ = (id) => document.getElementById(id);
const canvas = $("editor");
const context = canvas.getContext("2d");
const image = new Image();
let drag = null;

function api(path, options = {}) {
  let headers = {...(options.headers || {})};
  if (state.token) headers = {...headers, "X-Job-Token": state.token};
  return fetch(path, {...options, headers}).then(async (response) => {
    if (!response.ok) {
      const body = await response.json().catch(() => ({error: {message: "連線失敗，請稍後再試。"}}));
      throw new Error(body.error?.message || "操作失敗，請稍後再試。");
    }
    return response;
  });
}

function showError(error) {
  $("toast").textContent = error.message || "操作失敗，請稍後再試。";
  $("toast").classList.add("is-visible");
  window.setTimeout(() => $("toast").classList.remove("is-visible"), 5000);
}

function setWorkflowStep(step) {
  document.querySelectorAll("[data-workflow-step]").forEach((item) => {
    const isCurrent = Number(item.dataset.workflowStep) === step;
    item.classList.toggle("is-current", isCurrent);
    if (isCurrent) {
      item.setAttribute("aria-current", "step");
    } else {
      item.removeAttribute("aria-current");
    }
  });
}

function clampBox(box) {
  box.width = Math.max(8, Math.min(state.width, Math.round(Number(box.width) || 8)));
  box.height = Math.max(8, Math.min(state.height, Math.round(Number(box.height) || 8)));
  box.x = Math.max(0, Math.min(state.width - box.width, Math.round(Number(box.x) || 0)));
  box.y = Math.max(0, Math.min(state.height - box.height, Math.round(Number(box.y) || 0)));
}

function draw() {
  if (!image.naturalWidth || !state.width || !state.height) return;
  canvas.width = image.naturalWidth;
  canvas.height = image.naturalHeight;
  context.drawImage(image, 0, 0, canvas.width, canvas.height);
  const sx = canvas.width / state.width;
  const sy = canvas.height / state.height;
  state.boxes.forEach((box, index) => {
    context.strokeStyle = index === state.selected ? "#f0a91c" : "#29c6c8";
    context.lineWidth = 3;
    context.fillStyle = "rgba(41, 198, 200, 0.16)";
    context.fillRect(box.x * sx, box.y * sy, box.width * sx, box.height * sy);
    context.strokeRect(box.x * sx, box.y * sy, box.width * sx, box.height * sy);
  });
}

function syncBoxInputs(row, box) {
  for (const key of ["x", "y", "width", "height"]) {
    const input = row.querySelector(`input[data-key="${key}"]`);
    if (input) input.value = box[key];
  }
}

function setProcessing(isProcessing) {
  state.isProcessing = isProcessing;
  for (const id of ["detect-button", "add-button", "delete-button", "rights", "process-button"]) {
    $(id).disabled = isProcessing;
  }
  renderFields();
}

function renderFields() {
  $("box-fields").replaceChildren(...state.boxes.map((box, index) => {
    const row = document.createElement("fieldset");
    row.className = "box-row";
    const legend = document.createElement("legend");
    legend.textContent = `範圍 ${index + 1}`;
    row.append(legend);
    for (const key of ["x", "y", "width", "height"]) {
      const label = document.createElement("label");
      label.textContent = ({x: "X", y: "Y", width: "寬", height: "高"})[key];
      const input = document.createElement("input");
      input.type = "number";
      input.min = "0";
      input.dataset.key = key;
      input.value = box[key];
      input.addEventListener("input", () => {
        box[key] = input.value;
        clampBox(box);
        syncBoxInputs(row, box);
        state.selected = index;
        draw();
      });
      label.append(input);
      row.append(label);
    }
    row.addEventListener("click", () => { state.selected = index; draw(); });
    return row;
  }));
  $("process-button").disabled = state.isProcessing || !($("rights").checked && state.boxes.length);
}

async function upload(file) {
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  const response = await api("/api/jobs", {method: "POST", body: form});
  const body = await response.json();
  Object.assign(state, {
    jobId: body.job_id, token: body.job_token, width: body.video.width,
    height: body.video.height, preview: body.preview, boxes: [], selected: -1
  });
  $("video-meta").textContent = `${body.video.width}×${body.video.height}・${body.video.duration_seconds} 秒・${body.video.has_audio ? "有音訊" : "無音訊"}`;
  image.onload = draw;
  image.src = body.preview;
  $("edit-step").classList.remove("is-hidden");
  setWorkflowStep(2);
  await detect();
}

async function detect() {
  const response = await api(`/api/jobs/${state.jobId}/detect`, {method: "POST"});
  const body = await response.json();
  state.boxes = body.boxes.map(({x, y, width, height}) => ({x, y, width, height}));
  state.boxes.forEach(clampBox);
  state.selected = state.boxes.length ? 0 : -1;
  renderFields();
  draw();
}

async function startProcess() {
  if (state.isProcessing) return;
  setProcessing(true);
  try {
    const response = await api(`/api/jobs/${state.jobId}/process`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({boxes: state.boxes, rights_confirmed: $("rights").checked})
    });
    await response.json();
    $("progress-step").classList.remove("is-hidden");
    $("progress-reset-button").classList.remove("is-hidden");
    setWorkflowStep(3);
    schedulePoll(0);
  } catch (error) {
    setProcessing(false);
    throw error;
  }
}

function stopPolling() {
  if (state.pollId !== null) window.clearTimeout(state.pollId);
  state.pollId = null;
}

function schedulePoll(delay = 1200) {
  stopPolling();
  if (!state.isProcessing || state.isResetting) return;
  state.pollId = window.setTimeout(() => {
    state.pollId = null;
    poll().catch(handleProcessError);
  }, delay);
}

function handleProcessError(error) {
  stopPolling();
  $("live-status").textContent = "暫時無法取得處理狀態，請重新開始。";
  $("progress-reset-button").classList.remove("is-hidden");
  showError(error);
}

async function poll() {
  if (!state.isProcessing || state.isResetting) return;
  const response = await api(`/api/jobs/${state.jobId}/status`);
  const body = await response.json();
  $("live-status").textContent = body.message;
  $("progress").value = body.progress;
  if (body.state === "completed") {
    stopPolling();
    if (state.downloadUrl || state.downloadStarted) return;
    state.downloadStarted = true;
    try {
      const videoResponse = await api(`/api/jobs/${state.jobId}/download`);
      const blob = await videoResponse.blob();
      state.downloadUrl = URL.createObjectURL(blob);
      $("result-video").src = state.downloadUrl;
      $("download-button").href = state.downloadUrl;
      $("result-step").classList.remove("is-hidden");
      $("progress-reset-button").classList.add("is-hidden");
      setWorkflowStep(4);
    } catch (error) {
      state.downloadStarted = false;
      throw error;
    }
  } else if (body.state === "failed") {
    stopPolling();
    throw new Error(body.error || "影片處理失敗，請重新嘗試。");
  } else {
    schedulePoll();
  }
}

function addBox() {
  if (state.boxes.length >= 3) return showError(new Error("最多可選擇 3 個範圍。"));
  state.boxes.push({x: Math.round(state.width * 0.7), y: Math.round(state.height * 0.75), width: Math.round(state.width * 0.2), height: Math.round(state.height * 0.12)});
  clampBox(state.boxes.at(-1));
  state.selected = state.boxes.length - 1;
  renderFields();
  draw();
}

function removeBox() {
  if (state.selected < 0) return;
  state.boxes.splice(state.selected, 1);
  state.selected = state.boxes.length ? 0 : -1;
  renderFields();
  draw();
}

function sourcePoint(event) {
  const rect = canvas.getBoundingClientRect();
  return {x: (event.clientX - rect.left) / rect.width * state.width, y: (event.clientY - rect.top) / rect.height * state.height};
}

function selectedAt(point) {
  for (let index = state.boxes.length - 1; index >= 0; index -= 1) {
    const box = state.boxes[index];
    if (point.x >= box.x && point.x <= box.x + box.width && point.y >= box.y && point.y <= box.y + box.height) return index;
  }
  return -1;
}

canvas.addEventListener("pointerdown", (event) => {
  const point = sourcePoint(event);
  const index = selectedAt(point);
  if (index < 0) return;
  state.selected = index;
  const box = state.boxes[index];
  const handleX = 14 / canvas.clientWidth * state.width;
  const handleY = 14 / canvas.clientHeight * state.height;
  drag = {mode: Math.abs(point.x - (box.x + box.width)) <= handleX && Math.abs(point.y - (box.y + box.height)) <= handleY ? "resize" : "move", startX: point.x, startY: point.y, box: {...box}};
  canvas.setPointerCapture(event.pointerId);
  draw();
});

canvas.addEventListener("pointermove", (event) => {
  if (!drag || state.selected < 0) return;
  const point = sourcePoint(event);
  const box = state.boxes[state.selected];
  if (drag.mode === "resize") {
    box.width = drag.box.width + point.x - drag.startX;
    box.height = drag.box.height + point.y - drag.startY;
  } else {
    box.x = drag.box.x + point.x - drag.startX;
    box.y = drag.box.y + point.y - drag.startY;
  }
  clampBox(box);
  draw();
});

function finishPointer(event) {
  if (!drag) return;
  if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
  drag = null;
  renderFields();
  draw();
}

canvas.addEventListener("pointerup", finishPointer);
canvas.addEventListener("pointercancel", finishPointer);

async function reset() {
  if (state.isResetting) return;
  state.isResetting = true;
  stopPolling();
  $("reset-button").disabled = true;
  $("progress-reset-button").disabled = true;
  try {
    if (state.jobId) {
      await api(`/api/jobs/${state.jobId}`, {method: "DELETE"});
    }
  } catch (_error) {
    // 工作可能已過期；頁面仍可安全重設，伺服器會依 TTL 清理暫存檔。
  } finally {
    if (state.downloadUrl) URL.revokeObjectURL(state.downloadUrl);
    window.location.reload();
  }
}

$("file-input").addEventListener("change", (event) => upload(event.target.files[0]).catch(showError));
$("detect-button").addEventListener("click", () => detect().catch(showError));
$("add-button").addEventListener("click", addBox);
$("delete-button").addEventListener("click", removeBox);
$("rights").addEventListener("change", renderFields);
$("process-button").addEventListener("click", () => startProcess().catch(showError));
$("reset-button").addEventListener("click", () => reset().catch(showError));
$("progress-reset-button").addEventListener("click", () => reset().catch(showError));

for (const name of ["dragenter", "dragover"]) {
  $("dropzone").addEventListener(name, (event) => { event.preventDefault(); $("dropzone").classList.add("is-dragging"); });
}
for (const name of ["dragleave", "drop"]) {
  $("dropzone").addEventListener(name, (event) => { event.preventDefault(); $("dropzone").classList.remove("is-dragging"); });
}
$("dropzone").addEventListener("drop", (event) => upload(event.dataTransfer.files[0]).catch(showError));
