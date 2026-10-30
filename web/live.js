"use strict";

// app.js supplies shared rendering; this module only handles stream transport.
if (liveMode) {
    let generation = 0;
    let pollTimer = null;
    let request = null;
    let streamId = null;
    let sequence = 0;
    let receivedAt = null;
    let unavailableSequence = 0;
    let batterySample = null;
    const labels = { starting: "等待数据", stale: "状态流过期", stopped: "状态流停止", error: "上游异常" };

    document.getElementById("mode-badge").textContent = robotMode ? "ROBOT · 只读遥测" : "MOCK · 持续模拟";
    document.getElementById("notice-copy").textContent = robotMode ?
        "Jetson 本机 CPU、内存与可选 CPU 温度；电压按采集能力显示真实 ROS 数据或未接入。里程计和电机状态未接入；采集进程存活不代表底盘在线。此入口未开放控制。" :
        "正在接收 C++ 程序持续生成的模拟状态。实时更新仅指模拟状态流，尚未连接真实机器人。";
    if (robotMode) {
        const labels = {
            "stage-label": "M2-002 / Jetson 只读遥测", "source-label": "source: robot",
            "connection-label": "采集进程", "motion-label": "控制入口", "motion-detail": "电机状态未接入",
            "odometry-note": "里程计未接入；当前位置、朝向和速度均未知。",
            "resources-title": "Jetson 本机资源", "chart-legend": "电压未接入 / V",
            "resources-note": "真实来源：/proc/stat、/proc/meminfo；温度仅使用 type=CPU-therm 的 thermal zone。温度可未提供。",
            "events-time-note": "时间从采集进程启动开始计算。日志保存在内存中，新状态流会清空历史。",
            "live-link": "机器人遥测"
        };
        for (const [id, text] of Object.entries(labels)) document.getElementById(id).textContent = text;
        document.getElementById("live-link").setAttribute("href", "./?mode=live&source=robot");
        document.querySelector(".milestones").hidden = true;
    }
    document.getElementById("playback-title").textContent = "持续状态";
    document.querySelector(".playback-panel .eyebrow").textContent = "STATE STREAM";
    document.querySelector(".timeline-label").hidden = true;
    view.timeline.hidden = true;
    document.querySelector(".playback-buttons").hidden = true;
    view.reload.textContent = "重新连接 ↻";
    document.querySelectorAll(".record-meta code")[0].textContent = "/api/state";
    document.querySelectorAll(".record-meta code")[1].textContent = robotMode ? "Jetson / robot_state_agent" : "C++ / --stream";
    document.querySelector(".playback-time > span:last-child").textContent = "生产者运行时间 / s";

    function unavailable(message, badge) {
        unavailableSequence = Math.max(unavailableSequence, sequence);
        clearFrame({ preserveEvents: true });
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
        if (!value || value.api_version !== 6 || !stream || stream.source !== expectedSource ||
            !["mock", "robot"].includes(expectedSource) || typeof stream.id !== "string" || !stream.id ||
            !Number.isSafeInteger(stream.sequence) || stream.sequence < 0 ||
            !["starting", "live", "stale", "stopped", "error"].includes(stream.status) ||
            !(stream.age_ms === null || (Number.isSafeInteger(stream.age_ms) && stream.age_ms >= 0))) {
            throw new Error("状态服务返回了不兼容的数据");
        }
        if (stream.status === "live") {
            if (stream.sequence < 1 || stream.age_ms === null) throw new Error("状态服务缺少接收信息");
        }
        if (value.state !== null) parseRecord(JSON.stringify(value.state), expectedSource);
        else if (stream.sequence !== 0 || stream.status === "live") throw new Error("状态服务缺少样本");
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
                renderEventLog();
                streamId = stream.id;
                sequence = 0;
                receivedAt = null;
                unavailableSequence = 0;
                batterySample = null;
                player.frames = [];
            }
            if (stream.sequence < sequence) throw new Error("状态序号倒退");
            if (stream.status !== "live") {
                unavailableSequence = Math.max(unavailableSequence, stream.sequence);
                unavailable("上游状态当前不可用；不将最后一帧当作实时状态。", labels[stream.status]);
                view["raw-state"].textContent = JSON.stringify(envelope, null, 2);
            } else {
                if (stream.sequence > sequence) {
                    const previous = player.frames[player.frames.length - 1];
                    if (previous && (frameTime(envelope.state) <= frameTime(previous) ||
                        (robotMode && envelope.state.sequence <= previous.sequence))) {
                        throw new Error("采集时间或生产者序号倒退");
                    }
                    batterySample = nextBatterySample(batterySample, envelope.state);
                    sequence = stream.sequence;
                    receivedAt = performance.now() - stream.age_ms;
                    player.frames.push(envelope.state);
                    if (player.frames.length > 120) player.frames.shift();
                } else if (JSON.stringify(envelope.state) !== JSON.stringify(player.frames[player.frames.length - 1])) {
                    throw new Error("同一状态序号的样本发生变化");
                }
                const age = Math.max(stream.age_ms, performance.now() - receivedAt);
                if (sequence <= unavailableSequence || age >= 2000) {
                    unavailableSequence = Math.max(unavailableSequence, sequence);
                    throw new Error("等待新的上游样本，旧帧已不可用");
                }
                player.index = player.frames.length - 1;
                const readings = player.frames.map(frame => frame.battery.voltage_v || 0);
                player.yMax = Math.max(5, Math.ceil(Math.max(...readings) / 5) * 5);
                view["axis-max"].textContent = String(player.yMax);
                view["axis-mid"].textContent = String(player.yMax / 2);
                renderFrame();
                view["time-start"].textContent = (frameTime(player.frames[0]) / 1000).toFixed(1) + " s";
                view["time-end"].textContent = (frameTime(envelope.state) / 1000).toFixed(1) + " s";
                view["frame-count"].textContent = `已接收 ${sequence} 份`;
                view["playback-badge"].textContent = "接收中";
                view["playback-badge"].classList.add("badge-blue");
                view.status.textContent = `${robotMode ? "Jetson 只读遥测" : "持续模拟中"} · 上游读数距接收约 ${Math.round(age)} ms。传输年龄与各传感器质量分别判断。`;
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
