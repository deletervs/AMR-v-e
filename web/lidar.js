"use strict";

// API8 sensor-local scan. No TF, map, obstacle classification or device commands.
const lidarFields = ["angle_min_rad", "angle_max_rad", "angle_increment_rad", "range_min_m",
    "range_max_m", "scan_time_s", "time_increment_s"];
const lidarMetadata = ["received_elapsed_ms", "source_stamp_ns", "source_sequence", "frame_id"];
const lidarView = Object.fromEntries(["lidar-panel", "lidar-quality", "lidar-detail", "lidar-count",
    "lidar-sample-detail", "lidar-canvas", "lidar-range", "lidar-empty"].map(id => [id, document.getElementById(id)]));
let displayedLidar = null;

function validRobotLidar(frame) {
    const capabilities = frame.capabilities;
    const lidar = frame.lidar;
    if (!capabilities || !Object.prototype.hasOwnProperty.call(capabilities, "lidar")) {
        return !Object.prototype.hasOwnProperty.call(frame, "lidar") ||
            (lidar && Object.keys(lidar).length === 2 && lidar.reading === null && lidar.quality === "Unknown");
    }
    if (!["battery", "odometry", "lidar"].every(name => typeof capabilities[name] === "boolean") ||
        !frame.telemetry || !Object.prototype.hasOwnProperty.call(frame.telemetry, "lidar_topic") ||
        !lidar || typeof lidar !== "object" || Array.isArray(lidar) ||
        !Object.keys(qualityInfo).includes(lidar.quality) ||
        !["reading", ...lidarMetadata].every(name => Object.prototype.hasOwnProperty.call(lidar, name))) return false;
    if (!capabilities.lidar && (lidar.reading !== null || lidar.quality !== "Unknown")) return false;
    if (lidar.reading === null) return !["Valid", "Stale"].includes(lidar.quality) && lidarMetadata.every(name => lidar[name] === null);
    const r = lidar.reading;
    if (!r || typeof r !== "object" || Array.isArray(r) ||
        !lidarFields.every(name => typeof r[name] === "number" && Number.isFinite(r[name])) ||
        !Array.isArray(r.ranges_m) || r.ranges_m.length < 1 || r.ranges_m.length > 4096) return false;
    const span = (r.ranges_m.length - 1) * r.angle_increment_rad;
    const end = r.angle_min_rad + span;
    if (r.angle_increment_rad === 0 || r.range_min_m < 0 || r.range_max_m <= r.range_min_m ||
        r.scan_time_s < 0 || r.time_increment_s < 0 || !Number.isFinite(end) || Math.abs(span) > 2 * Math.PI + 0.001 ||
        Math.abs(end - r.angle_max_rad) > Math.max(0.0001, Math.abs(r.angle_increment_rad) * 1.05) ||
        (r.scan_time_s > 0 && r.time_increment_s > 0 && r.time_increment_s * (r.ranges_m.length - 1) > r.scan_time_s + 0.001) ||
        !r.ranges_m.every(value => value === null || (typeof value === "number" && Number.isFinite(value) &&
            value >= r.range_min_m && value <= r.range_max_m))) return false;
    const stamp = uint64Bytes(lidar.source_stamp_ns);
    return Number.isSafeInteger(lidar.received_elapsed_ms) && lidar.received_elapsed_ms >= 0 &&
        lidar.received_elapsed_ms <= frame.elapsed_ms && stamp !== null && stamp > 0n &&
        Number.isInteger(lidar.source_sequence) && lidar.source_sequence >= 0 && lidar.source_sequence <= 4294967295 &&
        typeof lidar.frame_id === "string" && lidar.frame_id.length >= 1 && lidar.frame_id.length <= 64 &&
        !/[^A-Za-z0-9_/.-]/.test(lidar.frame_id);
}

function nextLidarSample(previous, frame) {
    const lidar = frame.lidar;
    if (frame.source !== "robot" || !lidar || lidar.reading === null) return previous;
    if (previous && (lidar.received_elapsed_ms < previous.received_elapsed_ms ||
        (lidar.received_elapsed_ms === previous.received_elapsed_ms &&
         (lidarMetadata.some(name => lidar[name] !== previous[name]) ||
          lidarFields.some(name => lidar.reading[name] !== previous.reading[name]) ||
          lidar.reading.ranges_m.length !== previous.reading.ranges_m.length ||
          lidar.reading.ranges_m.some((value, i) => value !== previous.reading.ranges_m[i]))))) {
        throw new Error("雷达接收时间倒退或同次收包内容发生变化");
    }
    return lidar;
}

// Canvas right = -sensor Y; Canvas down = -sensor X. Null never becomes zero.
function lidarPoints(reading, range, size) {
    const radius = Math.max(0, size / 2 - 28);
    return reading.ranges_m.flatMap((distance, i) => {
        if (distance === null || distance > range) return [];
        const angle = reading.angle_min_rad + i * reading.angle_increment_rad;
        return [{ x: size / 2 - distance * Math.sin(angle) * radius / range,
            y: size / 2 - distance * Math.cos(angle) * radius / range }];
    });
}

function drawLidar() {
    const canvas = lidarView["lidar-canvas"];
    const size = Math.max(1, canvas.getBoundingClientRect().width);
    const dpr = window.devicePixelRatio || 1;
    // Reset backing store on every draw: resize, new samples and clearing cannot accumulate points.
    canvas.width = Math.round(size * dpr);
    canvas.height = Math.round(size * dpr);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size, size);
    const center = size / 2;
    const radius = Math.max(0, center - 28);
    const range = Number(lidarView["lidar-range"].value) || 5;
    ctx.strokeStyle = "#2e3a4d";
    ctx.lineWidth = 1;
    for (const scale of [0.5, 1]) {
        ctx.beginPath(); ctx.arc(center, center, radius * scale, 0, 2 * Math.PI); ctx.stroke();
    }
    ctx.beginPath(); ctx.moveTo(center, 22); ctx.lineTo(center, size - 22);
    ctx.moveTo(22, center); ctx.lineTo(size - 22, center); ctx.stroke();
    ctx.font = "11px Segoe UI, sans-serif";
    ctx.fillStyle = "#9caac1"; ctx.textAlign = "center";
    ctx.fillText("X ↑", center, 15); ctx.fillText("Y ←", 23, center - 8);
    ctx.fillText(`${range} m`, center, size - 7);
    const points = displayedLidar?.reading ? lidarPoints(displayedLidar.reading, range, size) : [];
    ctx.fillStyle = displayedLidar?.quality === "Valid" ? "#87dec7" : "#e5bc76";
    for (const point of points) {
        ctx.beginPath(); ctx.arc(point.x, point.y, 2, 0, 2 * Math.PI); ctx.fill();
    }
    canvas.dataset.pointCount = String(points.length);
}

function renderLidar(lidar = null, capable = false, unavailable = false) {
    displayedLidar = lidar;
    const quality = lidar?.quality || "Unknown";
    const reading = lidar?.reading || null;
    const validCount = reading ? reading.ranges_m.filter(value => value !== null).length : 0;
    lidarView["lidar-panel"].dataset.quality = quality;
    lidarView["lidar-quality"].dataset.quality = quality;
    lidarView["lidar-quality"].textContent = qualityInfo[quality][0] + " · " + quality;
    lidarView["lidar-count"].textContent = reading ? `${validCount} / ${reading.ranges_m.length} 有效点 / 总射线` : "— / — 有效点 / 总射线";
    const detail = unavailable ? "当前状态不可用 · 等待新数据" :
        quality === "Stale" ? "历史扫描 · 已过期，等待新的雷达样本" :
        quality === "Fault" ? "雷达故障 · 历史样本仅供诊断" :
        quality === "Unsupported" ? "当前不支持雷达扫描" :
        !reading ? (capable ? "等待首个有效扫描" : "雷达未接入") :
        quality === "Unknown" ? "质量未知 · 历史样本仅供诊断" :
        validCount === 0 ? "扫描格式有效 · 全部距离为 null，无可用距离" : "扫描有效 · 仅显示有效距离点";
    lidarView["lidar-detail"].textContent = detail;
    lidarView["lidar-empty"].hidden = reading !== null && validCount > 0;
    lidarView["lidar-empty"].textContent = detail;
    lidarView["lidar-sample-detail"].hidden = reading === null;
    lidarView["lidar-sample-detail"].textContent = reading === null ? "" :
        `frame ${lidar.frame_id} · 本机接收 T+${lidar.received_elapsed_ms} ms · ROS 源时间 ${lidar.source_stamp_ns} ns · seq ${lidar.source_sequence}`;
    drawLidar();
}

lidarView["lidar-range"].addEventListener("change", drawLidar);
window.addEventListener("resize", drawLidar);
if (typeof ResizeObserver !== "undefined") new ResizeObserver(drawLidar).observe(lidarView["lidar-canvas"]);
