"use strict";

// Independent read-only transport; API8 does not imply camera freshness.
(() => {
    const panel = document.getElementById("camera-panel"), image = document.getElementById("camera-image");
    const status = document.getElementById("camera-status"), detail = document.getElementById("camera-detail");
    const metadata = document.getElementById("camera-metadata");
    const query = new URLSearchParams(location.search);
    if (!panel || query.get("mode") !== "live" || query.get("source") !== "robot") return;
    let generation = 0, revision = 0, timer, expiry, request;
    let streamId = null, sequence = 0, elapsed = -1, blocked = 0, receivedAt = null;
    let objectURL = null, pendingURL = null, lastFrame = null;
    const ttl = 1500;
    const labels = { disabled: "未启用", starting: "等待首帧", stale: "图像过期", stopped: "相机流已结束", error: "相机流异常" };
    function revoke() {
        ++revision;
        clearTimeout(expiry);
        image.hidden = true;
        image.removeAttribute("src");
        for (const url of [objectURL, pendingURL]) if (url) URL.revokeObjectURL(url);
        objectURL = pendingURL = null;
        metadata.textContent = "";
        metadata.hidden = true;
    }
    function unavailable(label, message) {
        blocked = Math.max(blocked, sequence);
        revoke();
        panel.dataset.status = "unavailable";
        status.textContent = label;
        detail.textContent = message;
    }
    function validate(value) {
        const s = value && value.stream;
        if (!value || value.camera_api_version !== 1 || !s || typeof s.id !== "string" ||
            !/^[0-9a-f-]{36}$/.test(s.id) || s.ttl_ms !== ttl ||
            !["disabled", "starting", "live", "stale", "stopped", "error"].includes(s.status) ||
            !(s.source === "robot_camera" || (s.source === null && s.status === "disabled")) ||
            !Number.isSafeInteger(s.sequence) || s.sequence < 0 ||
            !(s.age_ms === null || (Number.isSafeInteger(s.age_ms) && s.age_ms >= 0))) throw new Error("相机服务格式不兼容");
        if (s.status !== "live") {
            if (value.frame !== null) throw new Error("失效相机不得返回图像");
            return s;
        }
        const f = value.frame;
        if (!f || s.sequence < 1 || s.age_ms === null || s.age_ms >= ttl ||
            f.schema !== "rs-camera-v1" || f.source !== "robot_camera" || f.device !== "/dev/video2" ||
            f.device_name !== "Dabai DC1" || f.motion_enabled !== false || f.seq !== s.sequence ||
            !Number.isSafeInteger(f.elapsed_ms) || f.elapsed_ms < 0 || f.width !== 320 || f.height !== 240 ||
            !Number.isSafeInteger(f.jpeg_bytes) || f.jpeg_bytes < 1 || f.jpeg_bytes > 49152 ||
            typeof f.jpeg_b64 !== "string" || f.jpeg_b64.length > 65536 ||
            !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(f.jpeg_b64) ||
            typeof f.host_received_unix_ns !== "string" || !/^[1-9][0-9]{0,19}$/.test(f.host_received_unix_ns) ||
            BigInt(f.host_received_unix_ns) > 18446744073709551615n) throw new Error("相机图像或元数据无效");
        return s;
    }
    async function poll(token) {
        if (token !== generation) return;
        const controller = new AbortController();
        request = controller;
        const started = performance.now();
        const timeout = setTimeout(() => controller.abort(), ttl);
        try {
            const response = await fetch("./api/camera", { cache: "no-store", signal: controller.signal });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const value = await response.json();
            if (token !== generation) return;
            const s = validate(value);
            if (s.id !== streamId) {
                revoke(); streamId = s.id; sequence = blocked = 0; elapsed = -1; receivedAt = lastFrame = null;
            }
            if (s.sequence < sequence) throw new Error("相机序号倒退");
            if (s.status !== "live") {
                blocked = Math.max(blocked, s.sequence);
                unavailable(labels[s.status], "无当前有效图像；等待新帧。其它遥测通道独立更新。");
                return;
            }
            const frame = value.frame, serialized = JSON.stringify(frame);
            if (s.sequence === sequence) {
                if (serialized !== lastFrame) throw new Error("同一相机帧被修改");
            } else {
                if (frame.elapsed_ms <= elapsed) throw new Error("相机时间未前进");
                sequence = s.sequence; elapsed = frame.elapsed_ms;
                receivedAt = started - s.age_ms; lastFrame = serialized;
            }
            const age = Math.max(s.age_ms + performance.now() - started, performance.now() - receivedAt);
            if (sequence <= blocked || age >= ttl) throw new Error("图像过期，等待新帧");
            if (objectURL && image.dataset.sequence === String(sequence)) return;
            const bytes = Uint8Array.from(atob(frame.jpeg_b64), c => c.charCodeAt(0));
            if (bytes.length !== frame.jpeg_bytes || btoa(String.fromCharCode(...bytes)) !== frame.jpeg_b64) throw new Error("相机图像长度不符");
            revoke();
            const version = revision;
            const url = URL.createObjectURL(new Blob([bytes], { type: "image/jpeg" }));
            pendingURL = url;
            const decoded = new Image(); decoded.src = url;
            expiry = setTimeout(() => unavailable("图像过期", "解码期间已过期；等待新帧。"), Math.max(0, ttl - (performance.now() - receivedAt)));
            await decoded.decode();
            if (token !== generation || version !== revision) return;
            if (decoded.naturalWidth !== 320 || decoded.naturalHeight !== 240 || performance.now() - receivedAt >= ttl) throw new Error("图像解码失败或已过期");
            image.src = url; image.dataset.sequence = String(sequence);
            objectURL = url; pendingURL = null; image.hidden = false;
            panel.dataset.status = "live";
            status.textContent = "接收有效 · 只读";
            detail.textContent = "Dabai DC1 · 320 × 240 · 约每 500 ms 一帧 · 时效独立判断";
            metadata.textContent = `帧 ${sequence} · 生产者 ${elapsed} ms · 主机 read 后时间 ${frame.host_received_unix_ns} ns（非曝光时间）`;
            metadata.hidden = false;
            clearTimeout(expiry);
            expiry = setTimeout(() => unavailable("图像过期", "1500 ms 内无新图；已撤下旧图。"), Math.max(0, ttl - (performance.now() - receivedAt)));
        } catch (error) {
            if (token === generation) unavailable("图像不可用", error.name === "AbortError" ? "相机请求超时，等待新帧。" : `${error.message}；等待新帧。`);
        } finally {
            clearTimeout(timeout);
            if (token === generation) { request = null; timer = setTimeout(() => poll(token), 250); }
        }
    }
    function stop() {
        ++generation; clearTimeout(timer);
        if (request) request.abort();
        request = null;
        unavailable("已暂停", "页面已离开；返回后等待新帧。");
    }
    function connect() { stop(); unavailable("等待相机", "正在读取本机相机接口……"); poll(generation); }
    image.addEventListener("error", () => unavailable("解码失败", "浏览器无法显示图像；等待新帧。"));
    window.addEventListener("pagehide", stop);
    window.addEventListener("pageshow", event => { if (event.persisted) connect(); });
    connect();
})();
