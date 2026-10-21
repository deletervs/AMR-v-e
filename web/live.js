"use strict";

// app.js supplies shared rendering; this module only handles stream transport.
if (liveMode) {
    let generation = 0;
    let pollTimer = null;
    let request = null;
    let streamId = null;
    let sequence = 0;
    const labels = { starting: "等待数据", stale: "状态流过期", stopped: "状态流停止", error: "上游异常" };

    document.getElementById("mode-badge").textContent = "MOCK · 持续模拟";
    document.getElementById("notice-copy").textContent = "正在接收 C++ 程序持续生成的模拟状态。实时更新仅指模拟状态流，尚未连接真实机器人。";
    document.getElementById("playback-title").textContent = "持续状态";
    document.querySelector(".playback-panel .eyebrow").textContent = "STATE STREAM";
    document.querySelector(".timeline-label").hidden = true;
    view.timeline.hidden = true;
    document.querySelector(".playback-buttons").hidden = true;
    view.reload.textContent = "重新连接 ↻";
    document.querySelectorAll(".record-meta code")[0].textContent = "/api/state";
    document.querySelectorAll(".record-meta code")[1].textContent = "C++ / --stream";
    document.querySelector(".playback-time > span:last-child").textContent = "生产者运行时间 / s";

    function unavailable(message, badge) {
        clearFrame();
        view["playback-badge"].textContent = badge;
        view["playback-badge"].classList.remove("badge-blue");
        view.status.textContent = message;
        view.status.dataset.error = "true";
        view.reload.disabled = false;
        view["connection-detail"].textContent = "当前状态不可用";
        view["quality-detail"].textContent = "上游不可用 · 不显示旧帧为有效";
        view["odom-detail"].textContent = "当前状态不可用 · 等待新数据";
        view["resources-detail"].textContent = "当前状态不可用 · 等待新数据";
    }

    function validateEnvelope(value) {
        const stream = value && value.stream;
        if (!value || value.api_version !== 3 || !stream || typeof stream.id !== "string" ||
            !Number.isSafeInteger(stream.sequence) || stream.sequence < 0 ||
            !["starting", "live", "stale", "stopped", "error"].includes(stream.status) ||
            !(stream.age_ms === null || (Number.isSafeInteger(stream.age_ms) && stream.age_ms >= 0))) {
            throw new Error("状态服务返回了不兼容的数据");
        }
        if (stream.status === "live") {
            if (stream.sequence < 1 || stream.age_ms === null) throw new Error("状态服务缺少接收信息");
            parseRecord(JSON.stringify(value.state));
        }
        return stream;
    }

    async function poll(token) {
        if (token !== generation) return;
        const controller = new AbortController();
        request = controller;
        const timeout = setTimeout(() => controller.abort(), 1500);
        try {
            const response = await fetch("./api/state", { cache: "no-store", signal: controller.signal });
            if (!response.ok) throw new Error(`HTTP ${response.status}，请确认启动了持续状态服务`);
            const envelope = await response.json();
            const stream = validateEnvelope(envelope);
            if (token !== generation) return;
            if (stream.id !== streamId) {
                streamId = stream.id;
                sequence = 0;
                player.frames = [];
            }
            if (stream.status !== "live") {
                unavailable("上游状态当前不可用；不将最后一帧当作实时状态。", labels[stream.status]);
                view["raw-state"].textContent = JSON.stringify(envelope, null, 2);
            } else {
                if (stream.sequence < sequence) throw new Error("状态序号倒退");
                if (stream.sequence > sequence) {
                    if (player.frames.length &&
                        envelope.state.sim_time_ms <= player.frames[player.frames.length - 1].sim_time_ms) {
                        throw new Error("模拟时间倒退");
                    }
                    sequence = stream.sequence;
                    player.frames.push(envelope.state);
                    if (player.frames.length > 120) player.frames.shift();
                }
                player.index = player.frames.length - 1;
                const readings = player.frames.map(frame => frame.battery.voltage_v || 0);
                player.yMax = Math.max(5, Math.ceil(Math.max(...readings) / 5) * 5);
                view["axis-max"].textContent = String(player.yMax);
                view["axis-mid"].textContent = String(player.yMax / 2);
                renderFrame();
                view["time-start"].textContent = (player.frames[0].sim_time_ms / 1000).toFixed(1) + " s";
                view["time-end"].textContent = (envelope.state.sim_time_ms / 1000).toFixed(1) + " s";
                view["frame-count"].textContent = `已接收 ${sequence} 份`;
                view["playback-badge"].textContent = "接收中";
                view["playback-badge"].classList.add("badge-blue");
                view.status.textContent = `持续模拟中 · 上游读数距接收约 ${stream.age_ms} ms。传输年龄与各传感器质量分别判断。`;
                view.status.dataset.error = "false";
                view["raw-state"].textContent = JSON.stringify(envelope, null, 2);
                view.reload.disabled = false;
            }
        } catch (error) {
            if (token !== generation) return;
            unavailable(error.name === "AbortError" ? "连接超时，将继续尝试；当前状态不可用。" :
                `连接失败：${error.message}。将继续尝试。`, "连接中断");
        } finally {
            clearTimeout(timeout);
            if (token === generation) {
                request = null;
                pollTimer = setTimeout(() => poll(token), 500);
            }
        }
    }

    function stop() {
        ++generation;
        clearTimeout(pollTimer);
        if (request) request.abort();
    }
    function connect() {
        stop();
        unavailable("正在连接本机持续状态服务……", "连接中");
        poll(generation);
    }
    view.reload.addEventListener("click", connect);
    window.addEventListener("pagehide", stop);
    window.addEventListener("pageshow", event => { if (event.persisted) connect(); });
    connect();
}
