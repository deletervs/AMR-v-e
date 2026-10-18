"use strict";

// 页面只消费 C++ 的状态记录；不在浏览器中重算电池质量或控制机器人。
const qualityInfo = {
    Unknown: ["未知", "Unknown · 尚无数据"],
    Valid: ["有效", "Valid · 读数有效"],
    Stale: ["已过期", "Stale · 保留旧读数"],
    Fault: ["故障", "Fault · 数据故障"],
    Unsupported: ["未支持", "Unsupported · 当前不可用"]
};

const view = Object.fromEntries([
    "connected", "connection-dot", "connection-detail", "voltage", "voltage-detail",
    "quality", "quality-detail", "motion", "sim-time", "frame-count", "timeline",
    "toggle", "restart", "reload", "status", "playback-badge", "raw-state",
    "chart-line", "chart-cursor", "chart-dot", "chart-empty", "axis-max", "axis-mid",
    "time-start", "time-end", "chart-description"
].map(id => [id, document.getElementById(id)]));

const liveMode = new URLSearchParams(location.search).get("mode") === "live";
document.getElementById(liveMode ? "live-link" : "replay-link").setAttribute("aria-current", "page");
const player = { frames: [], index: 0, mode: "loading", timer: null, yMax: 25 };

function stopTimer() {
    clearTimeout(player.timer);
    player.timer = null;
}

function parseRecord(text) {
    const lines = text.split(/\r?\n/).filter(line => line.trim() !== "");
    if (lines.length === 0) throw new Error("记录文件为空");
    let previousTime = -1;
    return lines.map((line, index) => {
        let frame;
        try { frame = JSON.parse(line); }
        catch { throw new Error(`第 ${index + 1} 行不是有效 JSON`); }
        const battery = frame && frame.battery;
        if (!frame || frame.source !== "mock" || typeof frame.connected !== "boolean" ||
            frame.motion_enabled !== false || !Number.isSafeInteger(frame.sim_time_ms) ||
            frame.sim_time_ms <= previousTime || !battery ||
            !Object.keys(qualityInfo).includes(battery.quality) ||
            !(battery.voltage_v === null ||
              (typeof battery.voltage_v === "number" && Number.isFinite(battery.voltage_v)))) {
            throw new Error(`第 ${index + 1} 行不符合模拟状态格式或时间顺序`);
        }
        previousTime = frame.sim_time_ms;
        return frame;
    });
}

function renderChart() {
    const firstTime = player.frames[0].sim_time_ms;
    const duration = player.frames[player.frames.length - 1].sim_time_ms - firstTime;
    const x = frame => 16 + 608 * (frame.sim_time_ms - firstTime) / Math.max(duration, 1);
    const y = value => 150 - 130 * value / player.yMax;
    let path = "";
    let connectedSegment = false;
    let hasReading = false;
    for (const frame of player.frames.slice(0, player.index + 1)) {
        const voltage = frame.battery.voltage_v;
        if (voltage === null) { connectedSegment = false; continue; }
        path += `${connectedSegment ? " L" : " M"} ${x(frame)} ${y(voltage)}`;
        connectedSegment = true;
        hasReading = true;
    }
    const current = player.frames[player.index];
    const currentX = String(x(current));
    view["chart-line"].setAttribute("d", path);
    view["chart-cursor"].setAttribute("x1", currentX);
    view["chart-cursor"].setAttribute("x2", currentX);
    view["chart-empty"].hidden = hasReading;
    view["chart-dot"].setAttribute("visibility", current.battery.voltage_v === null ? "hidden" : "visible");
    if (current.battery.voltage_v !== null) {
        view["chart-dot"].setAttribute("cx", currentX);
        view["chart-dot"].setAttribute("cy", String(y(current.battery.voltage_v)));
        view["chart-dot"].style.fill = current.battery.quality === "Stale" ? "#e5bc76" : "#a6c8ff";
    }
    view["chart-description"].textContent = `当前模拟时间 ${current.sim_time_ms} ms，` +
        `电压 ${current.battery.voltage_v === null ? "未知" : current.battery.voltage_v + " V"}，` +
        `数据质量${qualityInfo[current.battery.quality][0]}。过期时保留旧数值。`;
}

function renderFrame() {
    const frame = player.frames[player.index];
    const battery = frame.battery;
    view.connected.textContent = frame.connected ? "已连接" : "未连接";
    view["connection-dot"].dataset.connected = String(frame.connected);
    view["connection-detail"].textContent = frame.connected ? "模拟链路已建立" : "当前模拟场景未连接";
    view.voltage.textContent = battery.voltage_v === null ? "—" : battery.voltage_v.toFixed(2);
    view["voltage-detail"].textContent = battery.quality === "Stale" ? "历史读数 · 已标记过期" :
        battery.voltage_v === null ? "尚未收到电压读数" : "模拟数值 · 单位 V";
    view.quality.textContent = qualityInfo[battery.quality][0];
    view.quality.dataset.quality = battery.quality;
    view["quality-detail"].textContent = qualityInfo[battery.quality][1];
    view.motion.textContent = "已禁用";
    view["sim-time"].textContent = (frame.sim_time_ms / 1000).toFixed(1);
    view["frame-count"].textContent = `${player.index + 1} / ${player.frames.length}`;
    view.timeline.value = String(player.index);
    view.timeline.setAttribute("aria-valuetext", `第 ${player.index + 1} 帧，${frame.sim_time_ms} ms`);
    view["raw-state"].textContent = JSON.stringify(frame, null, 2);
    renderChart();
}

function renderMode() {
    const busy = player.mode === "loading";
    const unavailable = busy || player.mode === "error";
    view.reload.disabled = busy;
    view.toggle.disabled = unavailable;
    view.restart.disabled = unavailable;
    view.timeline.disabled = unavailable;
    view.toggle.textContent = player.mode === "playing" ? "暂停回放" :
        player.mode === "finished" ? "重新播放" : "继续回放";
    const labels = { loading: "加载中", playing: "回放中", paused: "已暂停", finished: "已结束", error: "读取失败" };
    view["playback-badge"].textContent = labels[player.mode];
    view["playback-badge"].classList.toggle("badge-blue", player.mode === "playing");
    view.status.dataset.error = String(player.mode === "error");
    if (player.mode === "loading") view.status.textContent = "正在读取模拟记录……";
    if (player.mode === "playing") view.status.textContent = "按记录时间间隔回放；所有数值均为模拟数据。";
    if (player.mode === "paused") view.status.textContent = "回放已暂停，可拖动进度条检查任意一帧。";
    if (player.mode === "finished") view.status.textContent = "回放结束：画面停留在最后一帧历史记录。";
}

function scheduleNext() {
    if (player.index >= player.frames.length - 1) {
        player.mode = "finished";
        renderMode();
        return;
    }
    const interval = player.frames[player.index + 1].sim_time_ms - player.frames[player.index].sim_time_ms;
    player.timer = setTimeout(() => {
        ++player.index;
        renderFrame();
        scheduleNext();
    }, interval);
}

function play(fromStart = false) {
    stopTimer();
    if (fromStart || player.index === player.frames.length - 1) player.index = 0;
    player.mode = "playing";
    renderFrame();
    renderMode();
    scheduleNext();
}

function clearFrame() {
    for (const id of ["connected", "voltage", "motion", "sim-time"]) view[id].textContent = "—";
    view["connection-dot"].dataset.connected = "false";
    view["connection-detail"].textContent = "等待记录";
    view["voltage-detail"].textContent = "尚无可用记录";
    view.quality.textContent = "未知";
    view.quality.dataset.quality = "Unknown";
    view["quality-detail"].textContent = "Unknown · 尚无数据";
    view["frame-count"].textContent = "— / —";
    view.timeline.min = "0";
    view.timeline.max = "0";
    view.timeline.value = "0";
    view.timeline.removeAttribute("aria-valuetext");
    view["raw-state"].textContent = "尚无可用记录。";
    view["chart-line"].setAttribute("d", "");
    view["chart-dot"].setAttribute("visibility", "hidden");
    view["chart-cursor"].setAttribute("x1", "16");
    view["chart-cursor"].setAttribute("x2", "16");
    view["chart-empty"].hidden = false;
    view["time-start"].textContent = "—";
    view["time-end"].textContent = "—";
    view["chart-description"].textContent = "尚无可用电压记录。";
}

async function loadRecord() {
    if (player.mode === "loading" && view.reload.disabled) return;
    stopTimer();
    player.mode = "loading";
    player.frames = [];
    player.index = 0;
    clearFrame();
    renderMode();
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    try {
        const response = await fetch("./state.ndjson", { cache: "no-store", signal: controller.signal });
        if (!response.ok) throw new Error(`HTTP ${response.status}：无法读取 state.ndjson`);
        player.frames = parseRecord(await response.text());
        const readings = player.frames.map(frame => frame.battery.voltage_v).filter(value => value !== null);
        player.yMax = Math.max(5, Math.ceil(Math.max(0, ...readings) / 5) * 5);
        view["axis-max"].textContent = String(player.yMax);
        view["axis-mid"].textContent = String(player.yMax / 2);
        view.timeline.max = String(player.frames.length - 1);
        view["time-start"].textContent = (player.frames[0].sim_time_ms / 1000).toFixed(1) + " s";
        view["time-end"].textContent = (player.frames[player.frames.length - 1].sim_time_ms / 1000).toFixed(1) + " s";
        play(true);
    } catch (error) {
        player.frames = [];
        player.mode = "error";
        clearFrame();
        renderMode();
        view.status.textContent = error.name === "AbortError" ? "读取超时，请检查本地服务后重新加载。" :
            `加载失败：${error.message}。请检查记录文件后重新加载。`;
    } finally {
        clearTimeout(timeout);
    }
}

if (!liveMode) {
view.toggle.addEventListener("click", () => {
    if (player.mode === "playing") {
        stopTimer();
        player.mode = "paused";
        renderMode();
    } else if (player.frames.length) play();
});
view.restart.addEventListener("click", () => { if (player.frames.length) play(true); });
view.timeline.addEventListener("input", () => {
    if (!player.frames.length) return;
    stopTimer();
    player.index = Number(view.timeline.value);
    player.mode = "paused";
    renderFrame();
    renderMode();
});
view.reload.addEventListener("click", loadRecord);
window.addEventListener("pagehide", stopTimer);
loadRecord();
}
