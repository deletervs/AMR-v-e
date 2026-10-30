"use strict";

// 页面消费 C++ 给出的数据质量；单位换算仅用于显示。
const qualityInfo = {
    Unknown: ["未知", "Unknown · 尚无数据"],
    Valid: ["有效", "Valid · 读数有效"],
    Stale: ["已过期", "Stale · 保留旧读数"],
    Fault: ["故障", "Fault · 数据故障"],
    Unsupported: ["未支持", "Unsupported · 当前不可用"]
};

const view = Object.fromEntries([
    "connected", "connection-dot", "connection-detail", "voltage", "voltage-detail", "battery-sample-detail",
    "quality", "quality-detail", "motion", "sim-time", "frame-count", "timeline",
    "toggle", "restart", "reload", "status", "playback-badge", "raw-state",
    "chart-line", "chart-cursor", "chart-dot", "chart-empty", "axis-max", "axis-mid",
    "time-start", "time-end", "chart-description", "odometry-panel", "odom-quality",
    "odom-detail", "odom-x", "odom-y", "odom-yaw", "odom-vx", "odom-vy", "odom-wz", "odom-yaw-raw",
    "resources-panel", "resources-quality", "resources-detail", "cpu-usage", "cpu-meter",
    "memory-used", "memory-unit", "memory-detail", "memory-meter", "cpu-temperature",
    "temperature-unit", "temperature-detail", "events-panel", "events-badge",
    "events-list", "events-empty", "events-note", "events-truncated"
].map(id => [id, document.getElementById(id)]));

const liveMode = new URLSearchParams(location.search).get("mode") === "live";
const expectedSource = new URLSearchParams(location.search).get("source") ?? "mock";
const robotMode = liveMode && expectedSource === "robot";
const frameTime = frame => frame[frame.source === "robot" ? "elapsed_ms" : "sim_time_ms"];
document.getElementById(liveMode ? "live-link" : "replay-link").setAttribute("aria-current", "page");
const player = { frames: [], index: 0, mode: "loading", timer: null, yMax: 25 };

function stopTimer() {
    clearTimeout(player.timer);
    player.timer = null;
}

const odometryFields = ["x_m", "y_m", "yaw_rad", "linear_x_mps", "linear_y_mps", "angular_z_radps"];

function validOdometry(odometry) {
    if (!odometry || !Object.keys(qualityInfo).includes(odometry.quality) ||
        !Object.prototype.hasOwnProperty.call(odometry, "reading")) return false;
    if (odometry.reading === null) return !["Valid", "Stale"].includes(odometry.quality);
    return typeof odometry.reading === "object" && !Array.isArray(odometry.reading) &&
        odometryFields.every(name => typeof odometry.reading[name] === "number" &&
            Number.isFinite(odometry.reading[name]));
}

function renderOdometry(odometry = null) {
    const quality = odometry ? odometry.quality : "Unknown";
    const reading = odometry ? odometry.reading : null;
    view["odometry-panel"].dataset.quality = quality;
    view["odom-quality"].dataset.quality = quality;
    view["odom-quality"].textContent = qualityInfo[quality][0] + " · " + quality;
    const metrics = { "odom-x": "x_m", "odom-y": "y_m", "odom-vx": "linear_x_mps",
        "odom-vy": "linear_y_mps", "odom-wz": "angular_z_radps" };
    for (const [id, field] of Object.entries(metrics)) {
        view[id].textContent = reading === null ? "—" : reading[field].toFixed(3);
    }
    const degrees = reading === null ? null : reading.yaw_rad * (180 / Math.PI);
    view["odom-yaw"].textContent = degrees === null ? "—" :
        Number.isFinite(degrees) ? degrees.toFixed(1) : "超出显示范围";
    view["odom-yaw-raw"].textContent = reading === null ? "原始 yaw：— rad" :
        `原始 yaw：${reading.yaw_rad.toFixed(3)} rad`;
    view["odom-detail"].textContent = quality === "Stale" ? "历史读数 · 已过期，等待新样本" :
        quality === "Fault" ? "故障 · 保留样本仅供诊断" :
        quality === "Unsupported" ? "当前设备不支持里程计" :
        quality === "Unknown" ? (reading === null ? (robotMode ? "里程计未接入" : "尚未收到里程计读数") : "质量未知 · 保留样本仅供诊断") :
        "模拟读数有效 · 位姿与速度来自同一份样本";
}


const maxUint64 = 18446744073709551615n;

function uint64Bytes(value) {
    if (typeof value !== "string" || value !== value.trim() || !/^(0|[1-9][0-9]{0,19})$/.test(value)) return null;
    const number = BigInt(value);
    return number <= maxUint64 ? number : null;
}

const batteryMetadata = ["received_elapsed_ms", "source_stamp_ns", "source_time_raw"];

function validRobotBattery(frame) {
    const battery = frame.battery;
    if (!Object.prototype.hasOwnProperty.call(frame, "capabilities")) {
        return Object.keys(battery).length === 2 && battery.voltage_v === null && battery.quality === "Unknown";
    }
    const capabilities = frame.capabilities;
    if (!capabilities || typeof capabilities !== "object" || Array.isArray(capabilities) ||
        typeof capabilities.battery !== "boolean" ||
        !batteryMetadata.every(name => Object.prototype.hasOwnProperty.call(battery, name))) return false;
    if (!capabilities.battery && (battery.voltage_v !== null || battery.quality !== "Unknown")) return false;
    if (battery.voltage_v === null) {
        return !["Valid", "Stale"].includes(battery.quality) && batteryMetadata.every(name => battery[name] === null);
    }
    const stamp = uint64Bytes(battery.source_stamp_ns);
    return typeof battery.voltage_v === "number" && Number.isFinite(battery.voltage_v) && battery.voltage_v >= 0 &&
        Number.isSafeInteger(battery.received_elapsed_ms) && battery.received_elapsed_ms >= 0 &&
        battery.received_elapsed_ms <= frame.elapsed_ms && stamp !== null && stamp > 0n &&
        typeof battery.source_time_raw === "number" && Number.isFinite(battery.source_time_raw) && battery.source_time_raw > 0;
}

function nextBatterySample(previous, frame) {
    const battery = frame.battery;
    if (frame.source !== "robot" || battery.voltage_v === null) return previous;
    if (previous && (battery.received_elapsed_ms < previous.received_elapsed_ms ||
        (battery.received_elapsed_ms === previous.received_elapsed_ms &&
         ["voltage_v", ...batteryMetadata].some(name => battery[name] !== previous[name])))) {
        throw new Error("电池接收时间倒退或同次收包内容发生变化");
    }
    return battery;
}

function validResources(resources) {
    if (!resources || !Object.keys(qualityInfo).includes(resources.quality) ||
        !Object.prototype.hasOwnProperty.call(resources, "reading")) return false;
    const reading = resources.reading;
    if (reading === null) return !["Valid", "Stale"].includes(resources.quality);
    if (typeof reading !== "object" || Array.isArray(reading) ||
        typeof reading.cpu_usage_percent !== "number" || !Number.isFinite(reading.cpu_usage_percent) ||
        reading.cpu_usage_percent < 0 || reading.cpu_usage_percent > 100 ||
        !Object.prototype.hasOwnProperty.call(reading, "cpu_temperature_c") ||
        !(reading.cpu_temperature_c === null ||
          (typeof reading.cpu_temperature_c === "number" && Number.isFinite(reading.cpu_temperature_c)))) return false;
    const used = uint64Bytes(reading.memory_used_bytes);
    const total = uint64Bytes(reading.memory_total_bytes);
    return used !== null && total !== null && total > 0n && used <= total;
}

function memoryDisplay(used, total) {
    const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB"];
    let divisor = 1n;
    let unit = 0;
    while (unit < units.length - 1 && total >= divisor * 1024n) {
        divisor *= 1024n;
        ++unit;
    }
    const fixed = bytes => {
        const scaled = (bytes * 100n + divisor / 2n) / divisor;
        return String(scaled / 100n) + "." + String(scaled % 100n).padStart(2, "0");
    };
    return { used: fixed(used), total: fixed(total), unit: units[unit],
        percent: Number((used * 1000n + total / 2n) / total) / 10 };
}

function renderResources(resources = null) {
    const quality = resources ? resources.quality : "Unknown";
    const reading = resources ? resources.reading : null;
    view["resources-panel"].dataset.quality = quality;
    view["resources-quality"].dataset.quality = quality;
    view["resources-quality"].textContent = qualityInfo[quality][0] + " · " + quality;
    view["cpu-usage"].textContent = reading === null ? "—" : reading.cpu_usage_percent.toFixed(1);
    view["cpu-meter"].style.width = reading === null ? "0%" : reading.cpu_usage_percent + "%";
    if (reading === null) {
        view["memory-used"].textContent = "—";
        view["memory-unit"].textContent = "GiB";
        view["memory-detail"].textContent = "总量 — · 已用 —";
        view["memory-meter"].style.width = "0%";
    } else {
        const memory = memoryDisplay(uint64Bytes(reading.memory_used_bytes), uint64Bytes(reading.memory_total_bytes));
        view["memory-used"].textContent = memory.used;
        view["memory-unit"].textContent = memory.unit;
        view["memory-detail"].textContent = "总量 " + memory.total + " " + memory.unit + " · 已用 " + memory.percent.toFixed(1) + "%";
        view["memory-meter"].style.width = memory.percent + "%";
    }
    const temperature = reading === null ? null : reading.cpu_temperature_c;
    view["cpu-temperature"].textContent = reading === null ? "—" :
        temperature === null ? "未提供" : temperature.toFixed(1);
    view["temperature-unit"].hidden = reading !== null && temperature === null;
    view["temperature-detail"].textContent = reading === null ? "尚未收到资源样本" :
        temperature === null ? "样本未提供温度" : "温度读数 · 摄氏度";
    view["resources-detail"].textContent = quality === "Stale" ? "历史读数 · 已过期，等待新样本" :
        quality === "Fault" ? "资源状态故障 · 保留样本仅供诊断" :
        quality === "Unsupported" ? "当前设备不支持资源监测" :
        quality === "Unknown" ? (reading === null ? "尚未收到资源读数" : "质量未知 · 保留样本仅供诊断") :
        robotMode ? "Jetson 本机读数有效 · CPU / 内存 / 可选 CPU 温度" :
        temperature === null ? "模拟读数有效 · 样本未提供温度" : "模拟读数有效 · CPU、内存与温度来自同一份样本";
}

const eventLevels = { Info: "信息", Warning: "警告", Error: "错误" };
const eventSources = { mock: "模拟程序", robot: "机器人采集进程", connection: "连接", battery: "电池",
    odometry: "里程计", system_resources: "系统资源" };
const eventEncoder = new TextEncoder();
let eventHistory = null;
let eventSignature = null;

function eventTextLength(value) {
    if (typeof value !== "string") return -1;
    for (const character of value) {
        const point = character.codePointAt(0);
        if (point >= 0xd800 && point <= 0xdfff) return -1;
    }
    return eventEncoder.encode(value).length;
}

function validEventLog(events, frameTime) {
    if (!events || typeof events !== "object" || Array.isArray(events) ||
        typeof events.truncated !== "boolean" || !Array.isArray(events.entries) ||
        events.entries.length > 100 || (events.truncated && events.entries.length !== 100)) return false;
    let previous = -1;
    return events.entries.every(entry => {
        if (!entry || typeof entry !== "object" || Array.isArray(entry) ||
            !Number.isSafeInteger(entry.elapsed_ms) || entry.elapsed_ms < 0 ||
            entry.elapsed_ms < previous || entry.elapsed_ms > frameTime ||
            typeof entry.level !== "string" ||
            !Object.prototype.hasOwnProperty.call(eventLevels, entry.level)) return false;
        const sourceLength = eventTextLength(entry.source);
        const messageLength = eventTextLength(entry.message);
        if (sourceLength < 1 || sourceLength > 64 || messageLength < 1 || messageLength > 512) return false;
        previous = entry.elapsed_ms;
        return true;
    });
}

function renderEventLog(events = null) {
    eventHistory = events;
    const signature = JSON.stringify(events);
    if (signature !== eventSignature) {
        const rows = document.createDocumentFragment();
        for (const entry of events ? events.entries.slice().reverse() : []) {
            const row = document.createElement("li");
            row.className = "event-row";
            const time = document.createElement("span");
            time.className = "event-time";
            time.textContent = "T+" + (entry.elapsed_ms / 1000).toFixed(3) + " s";
            const level = document.createElement("span");
            level.className = "event-level";
            level.dataset.level = entry.level;
            level.textContent = eventLevels[entry.level];
            const source = document.createElement("span");
            source.className = "event-source";
            source.textContent = Object.prototype.hasOwnProperty.call(eventSources, entry.source) ?
                eventSources[entry.source] : entry.source;
            source.title = entry.source;
            const message = document.createElement("p");
            message.className = "event-message";
            message.textContent = entry.message;
            row.append(time, level, source, message);
            rows.append(row);
        }
        view["events-list"].replaceChildren(rows);
        eventSignature = signature;
    }
    view["events-empty"].hidden = events !== null && events.entries.length > 0;
    view["events-truncated"].hidden = events === null || !events.truncated;
    view["events-panel"].dataset.mode = events === null ? "empty" : liveMode ? "live" : "replay";
    view["events-badge"].textContent = events === null ? "等待记录" : liveMode ? "持续记录" : "回放历史";
    view["events-note"].textContent = events === null ? "尚未收到事件历史。" :
        "当前保留 " + events.entries.length + " / 100 条 · 最新事件在上方" +
        (liveMode ? " · 仅记录状态变化" : " · 仅显示当前回放位置之前的事件");
}

function markEventHistoryUnavailable() {
    view["events-panel"].dataset.mode = "historical";
    view["events-badge"].textContent = eventHistory === null ? "等待记录" : "历史 · 已断流";
    view["events-note"].textContent = eventHistory === null ? "当前状态流不可用，尚无事件历史。" :
        "保留上次收到的历史事件 · 当前状态流不可用，历史恢复消息不代表当前健康。";
}

function parseRecord(text, source = "mock") {
    if (!["mock", "robot"].includes(source)) throw new Error("未知状态来源");
    const lines = text.split(/\r?\n/).filter(line => line.trim() !== "");
    if (lines.length === 0) throw new Error("记录文件为空");
    let previousTime = -1;
    let previousSequence = 0;
    let batterySample = null;
    return lines.map((line, index) => {
        let frame;
        try { frame = JSON.parse(line); }
        catch { throw new Error(`第 ${index + 1} 行不是有效 JSON`); }
        const battery = frame && frame.battery;
        const time = frame && frameTime(frame);
        if (!frame || frame.source !== source || typeof frame.connected !== "boolean" ||
            frame.motion_enabled !== false || !Number.isSafeInteger(time) ||
            time < 0 || time <= previousTime || !battery ||
            Object.prototype.hasOwnProperty.call(frame, source === "robot" ? "sim_time_ms" : "elapsed_ms") ||
            !Object.keys(qualityInfo).includes(battery.quality) ||
            !(battery.voltage_v === null ||
              (typeof battery.voltage_v === "number" && Number.isFinite(battery.voltage_v)))) {
            throw new Error(`第 ${index + 1} 行不符合状态来源、格式或时间顺序`);
        }
        if (source === "robot") {
            if (!Number.isSafeInteger(frame.sequence) || frame.sequence <= previousSequence ||
                !validRobotBattery(frame) ||
                !frame.odometry || frame.odometry.reading !== null || frame.odometry.quality !== "Unknown") {
                throw new Error(`第 ${index + 1} 行机器人序号或未接入通道不符合约定`);
            }
            previousSequence = frame.sequence;
            batterySample = nextBatterySample(batterySample, frame);
        }
        if (!validOdometry(frame.odometry)) throw new Error(`第 ${index + 1} 行里程计格式不兼容，请重新构建模拟程序并生成记录`);
        if (!validResources(frame.system_resources)) throw new Error(`第 ${index + 1} 行资源状态格式不兼容，请重新构建模拟程序并生成记录`);
        if (!validEventLog(frame.event_log, time)) throw new Error(`第 ${index + 1} 行事件日志格式不兼容，请重新构建并生成记录`);
        previousTime = time;
        return frame;
    });
}

function renderChart() {
    const firstTime = frameTime(player.frames[0]);
    const duration = frameTime(player.frames[player.frames.length - 1]) - firstTime;
    const x = frame => 16 + 608 * (frameTime(frame) - firstTime) / Math.max(duration, 1);
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
    view["chart-description"].textContent = `当前${robotMode ? "采集进程运行" : "模拟"}时间 ${frameTime(current)} ms，` +
        `电压 ${current.battery.voltage_v === null ? "未知" : current.battery.voltage_v + " V"}，` +
        `数据质量${qualityInfo[current.battery.quality][0]}。过期时保留旧数值。`;
}

function renderFrame() {
    const frame = player.frames[player.index];
    const battery = frame.battery;
    renderOdometry(frame.odometry);
    renderResources(frame.system_resources);
    renderEventLog(frame.event_log);
    view.connected.textContent = robotMode ? (frame.connected ? "采集进程存活" : "采集不可用") : frame.connected ? "已连接" : "未连接";
    view["connection-dot"].dataset.connected = String(frame.connected);
    view["connection-detail"].textContent = robotMode ? "仅表示采集进程 · 底盘状态未接入" :
        frame.connected ? "模拟链路已建立" : "当前模拟场景未连接";
    view.voltage.textContent = battery.voltage_v === null ? "—" : battery.voltage_v.toFixed(2);
    view["voltage-detail"].textContent = battery.quality === "Stale" ? "历史读数 · 已标记过期" :
        battery.quality === "Fault" ? "故障 · 保留样本仅供诊断" :
        battery.quality === "Unsupported" ? "当前设备不支持电压采集" :
        battery.voltage_v === null ? (robotMode ? (frame.capabilities?.battery ? "等待首个有效电压" : "电压未接入") : "尚未收到电压读数") :
        battery.quality === "Unknown" ? "质量未知 · 保留样本仅供诊断" :
        robotMode ? "来自真实 ROS · 单位 V" : "模拟数值 · 单位 V";
    view["battery-sample-detail"].hidden = !robotMode || battery.voltage_v === null;
    view["battery-sample-detail"].textContent = robotMode && battery.voltage_v !== null ?
        `本机接收 T+${battery.received_elapsed_ms} ms · ROS 驱动接收时间 ${battery.source_stamp_ns} ns · MCU 原始计时 ${battery.source_time_raw}（单位待核验）` : "";
    if (robotMode) document.getElementById("chart-legend").textContent = frame.capabilities?.battery ? "ROS 电压 / V" : "电压未接入 / V";
    view.quality.textContent = qualityInfo[battery.quality][0];
    view.quality.dataset.quality = battery.quality;
    view["quality-detail"].textContent = qualityInfo[battery.quality][1];
    view.motion.textContent = robotMode ? "未开放" : "已禁用";
    view["sim-time"].textContent = (frameTime(frame) / 1000).toFixed(1);
    view["frame-count"].textContent = `${player.index + 1} / ${player.frames.length}`;
    view.timeline.value = String(player.index);
    view.timeline.setAttribute("aria-valuetext", `第 ${player.index + 1} 帧，${frameTime(frame)} ms`);
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

function clearFrame({ preserveEvents = false } = {}) {
    if (preserveEvents) markEventHistoryUnavailable();
    else renderEventLog();
    renderOdometry();
    renderResources();
    for (const id of ["connected", "voltage", "motion", "sim-time"]) view[id].textContent = "—";
    view["connection-dot"].dataset.connected = "false";
    view["connection-detail"].textContent = "等待记录";
    view["voltage-detail"].textContent = "尚无可用记录";
    view["battery-sample-detail"].textContent = "";
    view["battery-sample-detail"].hidden = true;
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
