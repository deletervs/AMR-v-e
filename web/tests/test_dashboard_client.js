"use strict";

// Dependency-free protocol/lifecycle regression. Real layout is checked in Edge separately.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.resolve(__dirname, "..");
const records = fs.readFileSync(path.join(root, "state.ndjson"), "utf8").trim().split(/\r?\n/).map(JSON.parse);
const clone = value => JSON.parse(JSON.stringify(value));

class Element {
    constructor() {
        this.children = [];
        this.dataset = {};
        this.style = {};
        this.attributes = {};
        this.listeners = {};
        this.textContent = "";
        this.classList = { add() {}, remove() {}, toggle() {} };
    }
    set innerHTML(_) { throw new Error("Free text must never be interpreted as HTML"); }
    setAttribute(key, value) { this.attributes[key] = value; }
    removeAttribute(key) { delete this.attributes[key]; }
    addEventListener(key, callback) { this.listeners[key] = callback; }
    append(...nodes) { this.children.push(...nodes.flatMap(n => n.fragment ? n.children : [n])); }
    replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
    getBoundingClientRect() { return { width: 280 }; }
    getContext() {
        return { setTransform() {}, clearRect() {}, beginPath() {}, arc() {}, stroke() {},
            moveTo() {}, lineTo() {}, fillText() {}, fill() {} };
    }
}

function client(live = true, source = "mock") {
    const elements = new Map();
    const get = id => {
        if (!elements.has(id)) elements.set(id, new Element());
        return elements.get(id);
    };
    const timers = new Map();
    const requests = [];
    let timerId = 0;
    let now = 0;
    const context = vm.createContext({
        URLSearchParams, TextEncoder, AbortController,
        location: { search: live ? "?mode=live&source=" + source : "" },
        performance: { now: () => now },
        document: {
            getElementById: get, querySelector: get,
            querySelectorAll: key => [get(key + "0"), get(key + "1")],
            createElement: () => new Element(),
            createDocumentFragment: () => Object.assign(new Element(), { fragment: true })
        },
        window: { addEventListener() {} },
        setTimeout(fn, ms) { timers.set(++timerId, { fn, ms }); return timerId; },
        clearTimeout(id) { timers.delete(id); },
        fetch(_url, options) {
            return new Promise((resolve, reject) => {
                requests.push({ resolve, reject });
                options.signal.addEventListener("abort", () => reject(Object.assign(new Error("timeout"), { name: "AbortError" })));
            });
        }
    });
    vm.runInContext(fs.readFileSync(path.join(root, "lidar.js"), "utf8"), context);
    vm.runInContext(fs.readFileSync(path.join(root, "app.js"), "utf8"), context);
    if (live) vm.runInContext(fs.readFileSync(path.join(root, "live.js"), "utf8"), context);
    const settle = () => new Promise(resolve => setImmediate(resolve));
    return {
        context, get, timers,
        parse: value => context.parseRecord(JSON.stringify(value), source),
        async reply(value, status = 200) {
            const pending = requests.shift();
            assert.ok(pending, "expected a pending request");
            if (value instanceof Error) pending.reject(value);
            else pending.resolve({ ok: status === 200, status, json: async () => clone(value), text: async () => value });
            await settle();
        },
        async tick(ms = 500) {
            const item = [...timers].find(([, timer]) => timer.ms === ms);
            assert.ok(item, `expected ${ms} ms timer`);
            timers.delete(item[0]);
            now += ms;
            item[1].fn();
            await settle();
        }
    };
}

const event = { elapsed_ms: 0, level: "Info", source: "mock", message: "startup" };
function envelope(id = "session-a", sequence = 1, status = "live", state = records[2], source = state?.source || "mock") {
    return { api_version: 8, stream: { id, source, sequence, status, age_ms: status === "starting" ? null : 0 }, state: clone(state) };
}
function robot(sequence = 1, elapsed = 500) {
    const frame = clone(records[2]);
    delete frame.sim_time_ms;
    Object.assign(frame, { source: "robot", sequence, elapsed_ms: elapsed, connected: true,
        battery: { voltage_v: null, quality: "Unknown" }, odometry: { reading: null, quality: "Unknown" },
        system_resources: { reading: { cpu_usage_percent: 35, memory_used_bytes: "2147483648",
            memory_total_bytes: "8589934592", cpu_temperature_c: 52.5 }, quality: "Valid" },
        event_log: { entries: [{ elapsed_ms: 0, level: "Info", source: "robot", message: "started" }], truncated: false } });
    return frame;
}

function batteryRobot(sequence = 1, elapsed = 500, changes = {}) {
    return Object.assign(robot(sequence, elapsed), { capabilities: { battery: true },
        battery: Object.assign({ voltage_v: 24.1, quality: "Valid", received_elapsed_ms: 400,
            source_stamp_ns: "1793200000123456789", source_time_raw: 123.5 }, changes) });
}

const odomFields = ["x_m", "y_m", "yaw_rad", "linear_x_mps", "linear_y_mps", "angular_z_radps"];
const odomMetadata = ["received_elapsed_ms", "source_stamp_ns", "source_sequence", "source_twist_sequence", "frame_id", "child_frame_id"];
const emptyOdometry = () => Object.assign({ reading: null, quality: "Unknown" }, Object.fromEntries(odomMetadata.map(name => [name, null])));
function odometryRobot(sequence = 1, elapsed = 500, changes = {}) {
    const frame = batteryRobot(sequence, elapsed);
    frame.capabilities.odometry = true;
    frame.odometry = Object.assign({ reading: Object.fromEntries(odomFields.map(name => [name, 0])), quality: "Valid",
        received_elapsed_ms: 400, source_stamp_ns: "1793200000123456789", source_sequence: 0,
        source_twist_sequence: 4294967295, frame_id: "odom", child_frame_id: "base_link" }, changes);
    frame.telemetry = { odometry_pose_topic: "/handsfree/readonly_pose", odometry_twist_topic: "/handsfree/readonly_twist" };
    return frame;
}

let count = 0;
const lidarMeta = ["received_elapsed_ms", "source_stamp_ns", "source_sequence", "frame_id"];
const emptyLidar = () => Object.assign({ reading: null, quality: "Unknown" }, Object.fromEntries(lidarMeta.map(name => [name, null])));
function lidarRobot(sequence = 1, elapsed = 500, changes = {}) {
    const frame = odometryRobot(sequence, elapsed);
    frame.capabilities.lidar = true;
    frame.telemetry.lidar_topic = "/scan";
    frame.lidar = Object.assign({ quality: "Valid", received_elapsed_ms: 400,
        source_stamp_ns: "18446744073709551615", source_sequence: 0, frame_id: "laser/front-1.0",
        reading: { angle_min_rad: -Math.PI / 2, angle_max_rad: Math.PI / 2, angle_increment_rad: Math.PI / 2,
            range_min_m: 0, range_max_m: 20, scan_time_s: 0.1, time_increment_s: 0.01, ranges_m: [1, null, 0] } }, changes);
    return frame;
}
async function test(name, callback) {
    await callback();
    console.log("PASS " + name);
    ++count;
}

(async () => {
    await test("API8 lidar legacy, capabilities, endpoints, reverse angles and 4096 rays", () => {
        const c = client(true, "robot");
        c.parse(robot()); c.parse(odometryRobot());
        c.parse(Object.assign(robot(), { lidar: { reading: null, quality: "Unknown" } }));
        c.parse(lidarRobot());
        const single = lidarRobot();
        single.lidar.reading.ranges_m = [0];
        single.lidar.reading.angle_max_rad = single.lidar.reading.angle_min_rad;
        c.parse(single);
        const maximum = lidarRobot();
        Object.assign(maximum.lidar.reading, { angle_min_rad: Math.PI, angle_increment_rad: -2 * Math.PI / 4095,
            angle_max_rad: -Math.PI, time_increment_s: 0, ranges_m: Array(4096).fill(null) });
        c.parse(maximum);
        maximum.lidar.reading.angle_max_rad += maximum.lidar.reading.angle_increment_rad;
        c.parse(maximum); // Driver endpoints may differ by one interval.
        for (const capable of [true, false]) {
            const frame = lidarRobot(1, 500, emptyLidar()); frame.capabilities.lidar = capable; c.parse(frame);
        }
    });
    await test("API8 lidar rejects geometry, timing, illegal distances and incomplete metadata", () => {
        const c = client(true, "robot");
        const invalid = [];
        const geometry = { angle_min_rad: [null, true, "0", Infinity], angle_max_rad: [null, true, 100],
            angle_increment_rad: [0, true, null, 10], range_min_m: [-1, 21, true], range_max_m: [0, true],
            scan_time_s: [-1, true, 0.001], time_increment_s: [-1, true, 1],
            ranges_m: [[], Array(4097).fill(null), [true], [-1], [21], ["1"]] };
        for (const [field, values] of Object.entries(geometry)) for (const value of values) {
            const f = lidarRobot(); f.lidar.reading[field] = value; invalid.push(f);
        }
        const metadata = { received_elapsed_ms: [-1, true, null, 501, 0.5, Number.MAX_SAFE_INTEGER + 1],
            source_stamp_ns: [null, 0, "0", "01", "1\n", "18446744073709551616"],
            source_sequence: [-1, true, null, "0", 0.5, 4294967296],
            frame_id: ["", "a".repeat(65), "激光", "a b", "laser\n", "laser\r", null, true] };
        for (const [field, values] of Object.entries(metadata)) for (const value of values) invalid.push(lidarRobot(1, 500, { [field]: value }));
        for (const field of ["reading", "quality", ...lidarMeta]) { const f = lidarRobot(); delete f.lidar[field]; invalid.push(f); }
        for (const field of Object.keys(lidarRobot().lidar.reading)) { const f = lidarRobot(); delete f.lidar.reading[field]; invalid.push(f); }
        for (const field of ["battery", "odometry", "lidar"]) for (const value of [null, 1, "true"]) {
            const f = lidarRobot(); f.capabilities[field] = value; invalid.push(f);
        }
        for (const quality of ["Valid", "Stale"]) invalid.push(lidarRobot(1, 500, Object.assign(emptyLidar(), { quality })));
        for (const field of lidarMeta) invalid.push(lidarRobot(1, 500, Object.assign(emptyLidar(), { [field]: lidarRobot().lidar[field] })));
        const noZero = lidarRobot(); noZero.lidar.reading.range_min_m = 0.1; invalid.push(noZero);
        const disconnected = lidarRobot(); disconnected.capabilities.lidar = false; invalid.push(disconnected);
        const legacy = robot(); legacy.lidar = { reading: null, quality: "Fault" }; invalid.push(legacy);
        for (const value of [true, 1, {}, "/other"]) { const f = lidarRobot(); f.telemetry.lidar_topic = value; invalid.push(f); }
        const missingTopic = lidarRobot(); delete missingTopic.telemetry.lidar_topic; invalid.push(missingTopic);
        const missingTelemetry = lidarRobot(); delete missingTelemetry.telemetry; invalid.push(missingTelemetry);
        for (const f of invalid) assert.throws(() => c.parse(f), JSON.stringify(f));
        assert.throws(() => c.context.parseRecord(JSON.stringify(lidarRobot()).replace('"angle_min_rad":-1.5707963267948966', '"angle_min_rad":1e400'), "robot"));
    });
    await test("scan points preserve null indices and sensor X up Y left including legitimate zero", () => {
        const c = client(true, "robot");
        const r = lidarRobot().lidar.reading;
        const points = c.context.lidarPoints(r, 5, 280);
        assert.equal(points.length, 2);
        assert.ok(points[0].x > 140); assert.ok(Math.abs(points[0].y - 140) < 1e-9);
        assert.equal(points[1].x, 140); assert.equal(points[1].y, 140);
        r.ranges_m = [null, 1, null];
        const forward = c.context.lidarPoints(r, 5, 280)[0];
        assert.equal(forward.x, 140); assert.ok(forward.y < 140);
        r.ranges_m = [null, null, 1];
        assert.ok(c.context.lidarPoints(r, 5, 280)[0].x < 140);
        r.ranges_m = [null, 10, null]; assert.equal(c.context.lidarPoints(r, 5, 280).length, 0);
    });
    await test("lidar waiting, valid, all-null, independent stale and new sample recovery", async () => {
        const c = client(true, "robot");
        await c.reply(envelope("lidar-a", 1, "live", lidarRobot(1, 500, emptyLidar())));
        assert.match(c.get("lidar-detail").textContent, /等待首个/);
        await c.tick(); await c.reply(envelope("lidar-a", 2, "live", lidarRobot(2, 1000)));
        assert.equal(c.get("lidar-canvas").dataset.pointCount, "2");
        assert.match(c.get("lidar-sample-detail").textContent, /18446744073709551615 ns.*seq 0/);
        await c.tick(); await c.reply(envelope("lidar-a", 3, "live", lidarRobot(3, 2000, { quality: "Stale" })));
        assert.match(c.get("lidar-detail").textContent, /历史扫描.*过期/);
        assert.equal(c.get("lidar-canvas").dataset.pointCount, "2");
        for (const id of ["quality", "odom-quality", "resources-quality"]) assert.equal(c.get(id).dataset.quality, "Valid");
        const allNull = lidarRobot(4, 2500, { received_elapsed_ms: 2400, source_stamp_ns: "1", source_sequence: 1 });
        allNull.lidar.reading.ranges_m = [null, null, null];
        await c.tick(); await c.reply(envelope("lidar-a", 4, "live", allNull));
        assert.match(c.get("lidar-detail").textContent, /全部距离为 null/);
        assert.equal(c.get("lidar-canvas").dataset.pointCount, "0");
        await c.tick(); await c.reply(envelope("lidar-a", 5, "live", lidarRobot(5, 3000, { received_elapsed_ms: 2900, source_stamp_ns: "2" })));
        assert.equal(c.get("lidar-quality").dataset.quality, "Valid");
        assert.equal(c.get("lidar-canvas").dataset.pointCount, "2");
    });
    await test("lidar guards retain null baseline and reject mutation without advancing other channels", async () => {
        const mutations = [...lidarMeta.map(field => ({ [field]: field === "received_elapsed_ms" ? 399 : field === "frame_id" ? "other" : field === "source_stamp_ns" ? "1" : 1 })),
            ...Object.keys(lidarRobot().lidar.reading).map(field => ({ reading: Object.assign({}, lidarRobot().lidar.reading,
                { [field]: field === "ranges_m" ? [2, null, 0] : lidarRobot().lidar.reading[field] + 0.00001 }) }))];
        for (const change of mutations) {
            const c = client(true, "robot");
            const first = lidarRobot(); const empty = lidarRobot(2, 1000, emptyLidar());
            const bad = lidarRobot(3, 1500, change);
            bad.battery.received_elapsed_ms = 1400; bad.odometry.received_elapsed_ms = 1400;
            assert.throws(() => c.context.parseRecord([first, empty, bad].map(JSON.stringify).join("\n"), "robot"));
            await c.reply(envelope("lidar-a", 1, "live", first));
            await c.tick(); await c.reply(envelope("lidar-a", 2, "live", empty));
            await c.tick(); await c.reply(envelope("lidar-a", 3, "live", bad));
            assert.equal(c.get("lidar-canvas").dataset.pointCount, "0");
            const good = lidarRobot(4, 2000, { received_elapsed_ms: 1900, source_stamp_ns: "1", source_sequence: 0 });
            good.battery.received_elapsed_ms = 1300; good.odometry.received_elapsed_ms = 1300;
            await c.tick(); await c.reply(envelope("lidar-a", 4, "live", good));
            assert.equal(c.get("quality").dataset.quality, "Valid"); assert.equal(c.get("lidar-canvas").dataset.pointCount, "2");
        }
    });
    await test("all transport failures clear lidar and metadata, keep history and require fresh sequence", async () => {
        for (const status of ["stale", "error", "stopped", "network", "timeout"]) {
            const c = client(true, "robot"); const first = lidarRobot();
            await c.reply(envelope("lidar-a", 1, "live", first)); const rows = c.get("events-list").children;
            await c.tick();
            if (status === "timeout") { await c.tick(1500); await c.reply(new Error("discard aborted request")); }
            else await c.reply(status === "network" ? new Error("offline") : envelope("lidar-a", 1, status, first));
            assert.equal(c.get("lidar-canvas").dataset.pointCount, "0"); assert.equal(c.get("lidar-sample-detail").textContent, "");
            assert.equal(c.get("lidar-count").textContent, "— / — 有效点 / 总射线"); assert.deepEqual(c.get("events-list").children, rows);
            await c.tick(); await c.reply(envelope("lidar-a", 1, "live", first)); assert.equal(c.get("lidar-canvas").dataset.pointCount, "0");
            await c.tick(); await c.reply(envelope("lidar-a", 2, "live", lidarRobot(2, 1000, { received_elapsed_ms: 900 })));
            assert.equal(c.get("lidar-canvas").dataset.pointCount, "2");
            await c.tick(); await c.reply(envelope("lidar-b", 0, "starting", null, "robot")); assert.equal(c.get("events-list").children.length, 0);
            const restart = lidarRobot(1, 0, { received_elapsed_ms: 0, source_stamp_ns: "1" }); restart.battery.received_elapsed_ms = 0; restart.odometry.received_elapsed_ms = 0;
            await c.tick(); await c.reply(envelope("lidar-b", 1, "live", restart)); assert.equal(c.get("lidar-canvas").dataset.pointCount, "2");
        }
    });
    await test("real 17-frame replay and log contract", () => {
        const c = client();
        assert.equal(c.context.parseRecord(records.map(JSON.stringify).join("\n")).length, 17);
        assert.equal(c.parse(records[0]).length, 1);
        const frame = clone(records[0]);
        delete frame.event_log;
        assert.throws(() => c.parse(frame), /事件日志/);
    });
    await test("UTF-8 bytes, equal event times, control text and surrogate rejection", () => {
        const c = client();
        const f = clone(records[0]);
        f.event_log = { entries: [Object.assign({}, event, { source: "中".repeat(21) + "a", message: "😀".repeat(128) }), event], truncated: false };
        c.parse(f);
        for (const [field, value] of [["source", "中".repeat(22)], ["message", "😀".repeat(129)],
                                     ["source", "\ud800"], ["message", "\udfff"], ["level", ["Info"]]]) {
            const bad = clone(f);
            bad.event_log.entries[0][field] = value;
            assert.throws(() => c.parse(bad), /事件日志/);
        }
    });
    await test("strict truncation, capacity, event order and future time", () => {
        const c = client();
        const f = clone(records[2]);
        f.event_log = { entries: Array.from({ length: 100 }, () => clone(event)), truncated: true };
        c.parse(f);
        const invalid = [{ entries: [], truncated: 0 }, { entries: [], truncated: true },
            { entries: [...f.event_log.entries, event], truncated: false },
            { entries: [Object.assign({}, event, { elapsed_ms: f.sim_time_ms + 1 })], truncated: false },
            { entries: [Object.assign({}, event, { elapsed_ms: 1 }), event], truncated: false }];
        for (const log of invalid) assert.throws(() => c.parse(Object.assign({}, f, { event_log: log })), /事件日志/);
        assert.throws(() => c.parse(Object.assign({}, f, { sim_time_ms: Number.MAX_SAFE_INTEGER + 1 })));
    });
    await test("history survives every transport loss and clears current values", async () => {
        for (const status of ["stale", "error", "stopped"]) {
            const c = client();
            await c.reply(envelope());
            const rows = c.get("events-list").children;
            await c.tick();
            await c.reply(envelope("session-a", 1, status));
            assert.equal(c.get("voltage").textContent, "—");
            assert.equal(c.get("odom-x").textContent, "—");
            assert.equal(c.get("cpu-usage").textContent, "—");
            assert.deepEqual(c.get("events-list").children, rows);
            assert.match(c.get("events-badge").textContent, /已断流/);
            await c.tick();
            await c.reply(envelope("session-a", 2, "live", records[3]));
            assert.equal(c.get("events-panel").dataset.mode, "live");
        }
    });
    await test("request timeout and network failure preserve verified history and recover", async () => {
        const c = client();
        await c.reply(envelope());
        const rows = c.get("events-list").children;
        await c.tick();
        await c.tick(1500);
        assert.equal(c.get("voltage").textContent, "—");
        assert.deepEqual(c.get("events-list").children, rows);
        // The aborted fetch remains in the harness queue; discard its already rejected request.
        await c.reply(new Error("discard aborted request"));
        await c.tick();
        await c.reply(new Error("network offline"));
        assert.match(c.get("events-badge").textContent, /已断流/);
        await c.tick();
        await c.reply(envelope("session-a", 2, "live", records[3]));
        assert.equal(c.get("events-panel").dataset.mode, "live");
    });
    await test("new stream starting clears history before first live frame", async () => {
        const c = client();
        await c.reply(envelope());
        assert.ok(c.get("events-list").children.length);
        await c.tick();
        await c.reply(envelope("session-b", 0, "starting", null));
        assert.equal(c.get("events-list").children.length, 0);
        assert.equal(c.get("events-truncated").hidden, true);
        await c.tick();
        await c.reply(envelope("session-b", 1, "live", records[0]));
        assert.equal(c.get("events-list").children.length, records[0].event_log.entries.length);
    });
    await test("old API, sequence regression, time regression and malformed new history are rejected", async () => {
        const bad = [Object.assign(envelope(), { api_version: 4 }), Object.assign(envelope(), { api_version: 5 }),
                     Object.assign(envelope(), { api_version: 6 }), Object.assign(envelope(), { api_version: 7 }), envelope("session-a", 0),
                     envelope("session-a", 2, "live", records[1])];
        const malformed = envelope("session-a", 2, "live", records[3]);
        malformed.state.event_log.truncated = "false";
        bad.push(malformed);
        for (const value of bad) {
            const c = client();
            await c.reply(envelope());
            const rows = c.get("events-list").children;
            await c.tick();
            await c.reply(value);
            assert.equal(c.get("voltage").textContent, "—");
            assert.deepEqual(c.get("events-list").children, rows);
            assert.equal(c.get("status").dataset.error, "true");
        }
    });
    await test("newest first, no HTML interpretation, truncation and historical status", () => {
        const c = client();
        const entries = Array.from({ length: 100 }, (_, i) => Object.assign({}, event, { elapsed_ms: i,
            source: '<svg onload="alert(1)">', message: i + ' 引号"\\\n中文 <img src=x onerror=alert(1)>' }));
        c.context.renderEventLog({ entries, truncated: true });
        const row = c.get("events-list").children[0];
        assert.equal(row.children[3].textContent, entries[99].message);
        assert.equal(row.children[2].textContent, entries[99].source);
        assert.equal(c.get("events-truncated").hidden, false);
        c.context.clearFrame({ preserveEvents: true });
        assert.match(c.get("events-badge").textContent, /已断流/);
        assert.equal(c.get("events-list").children.length, 100);
    });
    await test("replay load, seek and restart show only selected frame history", async () => {
        const c = client(false);
        await c.reply(records.map(JSON.stringify).join("\n"));
        assert.equal(c.get("events-list").children.length, records[0].event_log.entries.length);
        c.get("timeline").value = "16";
        c.get("timeline").listeners.input();
        assert.equal(c.get("events-list").children.length, records[16].event_log.entries.length);
        assert.equal(c.get("playback-badge").textContent, "已暂停");
        c.get("restart").listeners.click();
        assert.equal(c.get("events-list").children.length, records[0].event_log.entries.length);
    });
    await test("robot format is explicit, strict, and cannot mix with mock", () => {
        const c = client(true, "robot");
        assert.equal(c.parse(robot()).length, 1);
        assert.throws(() => c.context.parseRecord(JSON.stringify(robot())));
        assert.throws(() => c.parse(records[0]));
        assert.throws(() => c.context.parseRecord(JSON.stringify(robot()), "unknown"));
        const invalid = [Object.assign(robot(), { sim_time_ms: 500 }),
            Object.assign(robot(), { elapsed_ms: -1 }), Object.assign(robot(), { elapsed_ms: true }),
            Object.assign(robot(), { sequence: 0 }), Object.assign(robot(), { sequence: true }),
            Object.assign(robot(), { motion_enabled: true }),
            Object.assign(robot(), { battery: { voltage_v: 24, quality: "Valid" } }),
            Object.assign(robot(), { odometry: records[2].odometry })];
        for (const frame of invalid) assert.throws(() => c.parse(frame));
        assert.throws(() => c.context.parseRecord([robot(), records[3]].map(JSON.stringify).join("\n"), "robot"));
        assert.throws(() => c.context.parseRecord([robot(1), robot(1, 1000)].map(JSON.stringify).join("\n"), "robot"));
        const future = robot();
        future.event_log.entries[0].elapsed_ms = 501;
        assert.throws(() => c.parse(future), /事件日志/);
    });
    await test("robot mode labels are factual while unconnected channels stay unknown", async () => {
        const c = client(true, "robot");
        await c.reply(envelope("robot-a", 1, "live", robot()));
        assert.equal(c.get("voltage").textContent, "—");
        assert.equal(c.get("odom-x").textContent, "—");
        assert.equal(c.get("cpu-usage").textContent, "35.0");
        assert.equal(c.get("cpu-temperature").textContent, "52.5");
        assert.equal(c.get("connected").textContent, "采集进程存活");
        assert.equal(c.get("motion").textContent, "未开放");
        assert.match(c.get("motion-detail").textContent, /电机状态未接入/);
        assert.match(c.get("resources-title").textContent, /Jetson/);
        assert.match(c.get("connection-detail").textContent, /底盘状态未接入/);
        for (const id of ["mode-badge", "notice-copy", "source-label", "connection-label", "connection-detail",
            "motion-label", "motion-detail", "resources-note", "resources-detail", "odometry-note", "odom-detail",
            "chart-legend", "events-time-note", "chart-description", "status", "stage-label", "live-link"]) {
            assert.doesNotMatch(c.get(id).textContent, /模拟|MOCK|已禁用/, id);
        }
    });
    await test("battery capability, empty samples, metadata and uint64 stamps are strict", () => {
        const c = client(true, "robot");
        for (const stamp of ["1", "9007199254740993", "18446744073709551615"]) {
            const parsed = c.parse(batteryRobot(1, 500, { voltage_v: 0, source_stamp_ns: stamp, received_elapsed_ms: 0 }));
            assert.equal(parsed[0].battery.source_stamp_ns, stamp);
        }
        const empty = batteryRobot(1, 500, { voltage_v: null, quality: "Unknown", received_elapsed_ms: null,
            source_stamp_ns: null, source_time_raw: null });
        c.parse(empty);
        c.parse(Object.assign(clone(empty), { capabilities: { battery: false } }));
        const invalid = [null, [], {}, true, { battery: 1 }, { battery: "true" }, { battery: null }]
            .map(capabilities => Object.assign(batteryRobot(), { capabilities }));
        invalid.push(Object.assign(batteryRobot(), { capabilities: { battery: false } }));
        invalid.push(Object.assign(robot(), { battery: Object.assign({}, robot().battery, { received_elapsed_ms: null }) }));
        const fields = {
            voltage_v: [-1, true, "24"], received_elapsed_ms: [-1, true, 1.5, 501, null, Number.MAX_SAFE_INTEGER + 1],
            source_stamp_ns: [0, 1, true, null, "0", "01", "+1", "-1", "1e3", "1.0", " 1", "1 ", "1\n", "\u0661", "18446744073709551616"],
            source_time_raw: [0, -1, true, null, "1"]
        };
        for (const [name, values] of Object.entries(fields)) {
            for (const value of values) invalid.push(batteryRobot(1, 500, { [name]: value }));
        }
        for (const name of ["received_elapsed_ms", "source_stamp_ns", "source_time_raw"]) {
            const missing = batteryRobot();
            delete missing.battery[name];
            invalid.push(missing);
            const partial = clone(empty);
            partial.battery[name] = batteryRobot().battery[name];
            invalid.push(partial);
        }
        for (const quality of ["Valid", "Stale"]) {
            const unusable = clone(empty);
            unusable.battery.quality = quality;
            invalid.push(unusable);
        }
        for (const value of invalid) assert.throws(() => c.parse(value), JSON.stringify(value));
        for (const value of [NaN, Infinity, -Infinity]) {
            // JSON.stringify converts these to null; test the wire spelling directly.
            const text = JSON.stringify(batteryRobot()).replace('"voltage_v":24.1', '"voltage_v":1e400');
            assert.throws(() => c.context.parseRecord(text, "robot"));
            assert.throws(() => c.parse(batteryRobot(1, 500, { source_time_raw: value })));
        }
    });
    await test("real battery waiting, valid zero, stale and recovery keep CPU live and clocks separate", async () => {
        const c = client(true, "robot");
        const empty = batteryRobot(1, 500, { voltage_v: null, quality: "Unknown", received_elapsed_ms: null,
            source_stamp_ns: null, source_time_raw: null });
        await c.reply(envelope("battery-a", 1, "live", empty));
        assert.match(c.get("voltage-detail").textContent, /等待首个有效电压/);
        assert.equal(c.get("battery-sample-detail").hidden, true);
        await c.tick();
        const valid = batteryRobot(2, 1000, { voltage_v: 0, source_stamp_ns: "18446744073709551615" });
        await c.reply(envelope("battery-a", 2, "live", valid));
        assert.equal(c.get("voltage").textContent, "0.00");
        assert.match(c.get("voltage-detail").textContent, /真实 ROS/);
        assert.match(c.get("battery-sample-detail").textContent, /18446744073709551615 ns/);
        assert.match(c.get("battery-sample-detail").textContent, /MCU.*单位待核验/);
        await c.tick();
        await c.reply(envelope("battery-a", 3, "live", batteryRobot(3, 4000, Object.assign({}, valid.battery, { quality: "Stale" }))));
        assert.equal(c.get("quality").dataset.quality, "Stale");
        assert.equal(c.get("voltage").textContent, "0.00");
        assert.match(c.get("voltage-detail").textContent, /历史.*过期/);
        assert.equal(c.get("cpu-usage").textContent, "35.0");
        assert.equal(c.get("resources-quality").dataset.quality, "Valid");
        assert.equal(c.get("status").dataset.error, "false");
        await c.tick();
        await c.reply(envelope("battery-a", 4, "live", batteryRobot(4, 4500,
            { received_elapsed_ms: 4450, source_stamp_ns: "1", source_time_raw: 1 })));
        assert.equal(c.get("quality").dataset.quality, "Valid");
        assert.equal(c.get("voltage").textContent, "24.10");
        assert.equal(c.get("odom-x").textContent, "—");
    });
    await test("battery progress rejects regression and same reception mutation in replay and live", async () => {
        for (const changes of [{ received_elapsed_ms: 399 }, { voltage_v: 24.2 },
            { source_stamp_ns: "1793200000123456790" }, { source_time_raw: 124 }]) {
            const first = batteryRobot();
            const next = batteryRobot(2, 1000, changes);
            const c = client(true, "robot");
            assert.throws(() => c.context.parseRecord([first, next].map(JSON.stringify).join("\n"), "robot"));
            await c.reply(envelope("battery-a", 1, "live", first));
            const rows = c.get("events-list").children;
            await c.tick();
            await c.reply(envelope("battery-a", 2, "live", next));
            assert.equal(c.get("voltage").textContent, "—");
            assert.equal(c.get("cpu-usage").textContent, "—");
            assert.deepEqual(c.get("events-list").children, rows);
            assert.match(c.get("status").textContent, /电池接收时间/);
            await c.tick();
            await c.reply(envelope("battery-a", 3, "live", batteryRobot(3, 1500, { received_elapsed_ms: 1400 })));
            assert.equal(c.get("voltage").textContent, "24.10");
        }
        const c = client(true, "robot");
        const empty = batteryRobot(2, 1000, { voltage_v: null, quality: "Fault", received_elapsed_ms: null,
            source_stamp_ns: null, source_time_raw: null });
        const older = batteryRobot(3, 1500, { received_elapsed_ms: 399 });
        assert.throws(() => c.context.parseRecord([batteryRobot(), empty, older].map(JSON.stringify).join("\n"), "robot"));
        await c.reply(envelope("battery-a", 1, "live", batteryRobot()));
        await c.tick();
        await c.reply(envelope("battery-a", 2, "live", empty));
        await c.tick();
        await c.reply(envelope("battery-a", 3, "live", older));
        assert.equal(c.get("voltage").textContent, "—");
        assert.equal(c.get("status").dataset.error, "true");
    });
    await test("battery transport loss clears values and metadata, new stream resets sample guard", async () => {
        for (const status of ["stale", "stopped", "error", "network"]) {
            const c = client(true, "robot");
            const frame = batteryRobot();
            await c.reply(envelope("battery-a", 1, "live", frame));
            await c.tick();
            await c.reply(status === "network" ? new Error("offline") : envelope("battery-a", 1, status, frame));
            assert.equal(c.get("voltage").textContent, "—");
            assert.equal(c.get("battery-sample-detail").textContent, "");
            assert.equal(c.get("battery-sample-detail").hidden, true);
            await c.tick();
            await c.reply(envelope("battery-a", 1, "live", frame));
            assert.equal(c.get("voltage").textContent, "—");
            await c.tick();
            await c.reply(envelope("battery-b", 0, "starting", null, "robot"));
            assert.equal(c.get("events-list").children.length, 0);
            assert.equal(c.get("chart-line").attributes.d, "");
            await c.tick();
            await c.reply(envelope("battery-b", 1, "live", batteryRobot(1, 0,
                { received_elapsed_ms: 0, source_stamp_ns: "1", source_time_raw: 1 })));
            assert.equal(c.get("voltage").textContent, "24.10");
        }
    });
    await test("odometry capabilities, complete samples and exact integer metadata", () => {
        const c = client(true, "robot");
        c.parse(robot());
        c.parse(batteryRobot());
        for (const stamp of ["1", "9007199254740993", "18446744073709551615"]) {
            const frame = odometryRobot(1, 500, { received_elapsed_ms: 0, source_stamp_ns: stamp });
            c.parse(frame);
            frame.odometry.source_sequence = 4294967295;
            frame.odometry.source_twist_sequence = 0;
            frame.odometry.reading.x_m = -2.5;
            frame.odometry.reading.yaw_rad = -3.14;
            frame.odometry.reading.linear_y_mps = -0.5;
            frame.telemetry = { odometry_pose_topic: null, odometry_twist_topic: null };
            c.parse(frame);
        }
        c.parse(odometryRobot(1, Number.MAX_SAFE_INTEGER, { received_elapsed_ms: Number.MAX_SAFE_INTEGER }));
        for (const capability of [true, false]) {
            const frame = odometryRobot(1, 500, emptyOdometry());
            frame.capabilities.odometry = capability;
            c.parse(frame);
        }
        const invalid = [];
        for (const capability of [null, 0, 1, "true", [], {}]) {
            const frame = odometryRobot(1, 500, emptyOdometry());
            frame.capabilities.odometry = capability;
            invalid.push(frame);
        }
        const disconnected = odometryRobot();
        disconnected.capabilities.odometry = false;
        invalid.push(disconnected);
        const fields = {
            received_elapsed_ms: [-1, true, 1.5, 501, null, Number.MAX_SAFE_INTEGER + 1],
            source_stamp_ns: [0, 1, true, null, "0", "01", "+1", "-1", "1e3", "1.0", " 1", "1 ", "1\n", "\u0661", "18446744073709551616"],
            source_sequence: [-1, true, 1.5, null, "0", 4294967296],
            source_twist_sequence: [-1, true, 1.5, null, "0", 4294967296],
            frame_id: [null, "map", "base_link", 0], child_frame_id: [null, "odom", "map", 0]
        };
        for (const [name, values] of Object.entries(fields)) {
            for (const value of values) invalid.push(odometryRobot(1, 500, { [name]: value }));
        }
        for (const name of odomMetadata) {
            const missing = odometryRobot();
            delete missing.odometry[name];
            invalid.push(missing);
            invalid.push(odometryRobot(1, 500, Object.assign(emptyOdometry(), { [name]: odometryRobot().odometry[name] })));
        }
        for (const quality of ["Valid", "Stale"]) invalid.push(odometryRobot(1, 500, Object.assign(emptyOdometry(), { quality })));
        for (const name of odomFields) {
            const missing = odometryRobot();
            delete missing.odometry.reading[name];
            invalid.push(missing);
            for (const value of [null, true, "0", NaN, Infinity]) {
                const frame = odometryRobot();
                frame.odometry.reading[name] = value;
                invalid.push(frame);
            }
        }
        for (const name of ["odometry_pose_topic", "odometry_twist_topic"]) {
            for (const value of [0, true, [], {}]) {
                const frame = odometryRobot();
                frame.telemetry[name] = value;
                invalid.push(frame);
            }
        }
        for (const frame of invalid) assert.throws(() => c.parse(frame), JSON.stringify(frame));
        assert.throws(() => c.context.parseRecord(JSON.stringify(odometryRobot()).replace('"x_m":0', '"x_m":1e400'), "robot"));
    });
    await test("odometry waiting, valid zero, stale and reset recovery leave voltage and CPU valid", async () => {
        const c = client(true, "robot");
        await c.reply(envelope("odom-a", 1, "live", odometryRobot(1, 500, emptyOdometry())));
        assert.match(c.get("odom-detail").textContent, /等待首个有效里程计/);
        assert.equal(c.get("odom-sample-detail").hidden, true);
        await c.tick();
        const valid = odometryRobot(2, 1000, { source_stamp_ns: "18446744073709551615" });
        await c.reply(envelope("odom-a", 2, "live", valid));
        for (const id of ["odom-x", "odom-y", "odom-vx", "odom-vy", "odom-wz"]) assert.equal(c.get(id).textContent, "0.000");
        assert.equal(c.get("odom-yaw").textContent, "0.0");
        assert.match(c.get("odom-detail").textContent, /真实 ROS/);
        assert.match(c.get("odom-sample-detail").textContent, /18446744073709551615 ns/);
        assert.match(c.get("odom-sample-detail").textContent, /pose seq 0.*twist seq 4294967295/);
        assert.match(c.get("odometry-note").textContent, /odom.*base_link.*本地里程计.*不是全局定位/);
        await c.tick();
        await c.reply(envelope("odom-a", 3, "live", odometryRobot(3, 2500, Object.assign({}, valid.odometry, { quality: "Stale" }))));
        assert.equal(c.get("odom-quality").dataset.quality, "Stale");
        assert.match(c.get("odom-detail").textContent, /历史.*过期/);
        assert.equal(c.get("voltage").textContent, "24.10");
        assert.equal(c.get("quality").dataset.quality, "Valid");
        assert.equal(c.get("resources-quality").dataset.quality, "Valid");
        assert.equal(c.get("cpu-usage").textContent, "35.0");
        assert.equal(c.get("status").dataset.error, "false");
        await c.tick();
        await c.reply(envelope("odom-a", 4, "live", odometryRobot(4, 3000,
            { received_elapsed_ms: 2900, source_stamp_ns: "1", source_sequence: 0, source_twist_sequence: 0 })));
        assert.equal(c.get("odom-quality").dataset.quality, "Valid");
        assert.match(c.get("odom-sample-detail").textContent, /ROS 源时间 1 ns/);
    });
    await test("odometry reception guard survives empty frames and rejects each changed field", async () => {
        const changes = [{ received_elapsed_ms: 399 }, { source_stamp_ns: "1" }, { source_sequence: 1 }, { source_twist_sequence: 0 },
            ...odomFields.map(name => ({ reading: Object.assign({}, odometryRobot().odometry.reading, { [name]: 0.25 }) }))];
        for (const change of changes) {
            const c = client(true, "robot");
            const first = odometryRobot();
            const empty = odometryRobot(2, 1000, emptyOdometry());
            const next = odometryRobot(3, 1500, change);
            assert.throws(() => c.context.parseRecord([first, empty, next].map(JSON.stringify).join("\n"), "robot"));
            await c.reply(envelope("odom-a", 1, "live", first));
            const rows = c.get("events-list").children;
            await c.tick();
            await c.reply(envelope("odom-a", 2, "live", empty));
            await c.tick();
            await c.reply(envelope("odom-a", 3, "live", next));
            assert.equal(c.get("odom-x").textContent, "—");
            assert.equal(c.get("voltage").textContent, "—");
            assert.equal(c.get("odom-sample-detail").textContent, "");
            assert.deepEqual(c.get("events-list").children, rows);
            assert.match(c.get("status").textContent, /里程计接收时间/);
            await c.tick();
            await c.reply(envelope("odom-a", 4, "live", odometryRobot(4, 2000, { received_elapsed_ms: 1900 })));
            assert.equal(c.get("odom-quality").dataset.quality, "Valid");
        }
    });
    await test("rejected odometry never advances battery progress guard", async () => {
        const c = client(true, "robot");
        await c.reply(envelope("odom-a", 1, "live", odometryRobot()));
        await c.tick();
        const bad = odometryRobot(2, 1000, { source_sequence: 1 });
        bad.battery.received_elapsed_ms = 900;
        await c.reply(envelope("odom-a", 2, "live", bad));
        assert.match(c.get("status").textContent, /里程计接收时间/);
        await c.tick();
        const good = odometryRobot(3, 1500, { received_elapsed_ms: 1400 });
        good.battery.received_elapsed_ms = 800;
        await c.reply(envelope("odom-a", 3, "live", good));
        assert.equal(c.get("voltage").textContent, "24.10");
        assert.equal(c.get("odom-quality").dataset.quality, "Valid");
    });
    await test("odometry clears on transport loss, keeps history and requires new sequence", async () => {
        for (const status of ["stale", "stopped", "error", "network", "timeout"]) {
            const c = client(true, "robot");
            const first = odometryRobot();
            await c.reply(envelope("odom-a", 1, "live", first));
            const rows = c.get("events-list").children;
            await c.tick();
            if (status === "timeout") {
                await c.tick(1500);
                await c.reply(new Error("discard aborted request"));
            } else await c.reply(status === "network" ? new Error("offline") : envelope("odom-a", 1, status, first));
            assert.equal(c.get("odom-x").textContent, "—");
            assert.equal(c.get("odom-sample-detail").hidden, true);
            assert.equal(c.get("odom-sample-detail").textContent, "");
            assert.equal(c.get("cpu-usage").textContent, "—");
            assert.deepEqual(c.get("events-list").children, rows);
            await c.tick();
            await c.reply(envelope("odom-a", 1, "live", first));
            assert.equal(c.get("odom-x").textContent, "—");
            await c.tick();
            await c.reply(envelope("odom-a", 2, "live", odometryRobot(2, 1000, { received_elapsed_ms: 900 })));
            assert.equal(c.get("odom-x").textContent, "0.000");
            await c.tick();
            await c.reply(envelope("odom-b", 0, "starting", null, "robot"));
            assert.equal(c.get("events-list").children.length, 0);
            await c.tick();
            const restarted = odometryRobot(1, 0, { received_elapsed_ms: 0, source_stamp_ns: "1" });
            restarted.battery.received_elapsed_ms = 0;
            await c.reply(envelope("odom-b", 1, "live", restarted));
            assert.equal(c.get("odom-x").textContent, "0.000");
        }
    });
    await test("robot sources, producer sequence, elapsed time and duplicate mutation reject", async () => {
        const bad = [envelope("robot-a", 2, "live", robot(1, 1000)),
            envelope("robot-a", 2, "live", robot(2, 499)), envelope("robot-a", 2, "live", robot(2, 500)),
            envelope("robot-a", 1, "live", robot(2, 1000)), envelope("robot-a", 2, "live", records[3]),
            envelope("robot-a", 2, "live", robot(2, 1000), "mock"),
            envelope("robot-a", 2, "live", robot(2, 1000), "unknown")];
        for (const value of bad) {
            const c = client(true, "robot");
            await c.reply(envelope("robot-a", 1, "live", robot()));
            const rows = c.get("events-list").children;
            await c.tick();
            await c.reply(value);
            assert.equal(c.get("cpu-usage").textContent, "—");
            assert.deepEqual(c.get("events-list").children, rows);
            assert.equal(c.get("status").dataset.error, "true");
        }
        const defaultMock = client();
        await defaultMock.reply(envelope("robot-a", 1, "live", robot()));
        assert.equal(defaultMock.get("cpu-usage").textContent, "—");
        for (const source of ["unknown", ""]) {
            const invalidSource = client(true, source);
            await invalidSource.reply(envelope());
            assert.equal(invalidSource.get("cpu-usage").textContent, "—");
            assert.equal(invalidSource.get("status").dataset.error, "true");
        }
    });
    await test("duplicate HTTP polls never refresh upstream age", async () => {
        const c = client(true, "robot");
        const value = envelope("robot-a", 1, "live", robot());
        await c.reply(value);
        const rows = c.get("events-list").children;
        for (let tick = 1; tick <= 4; ++tick) {
            await c.tick();
            await c.reply(value); // Even a falsely unchanged age_ms=0 cannot renew the browser clock.
            assert.equal(c.get("cpu-usage").textContent, tick < 4 ? "35.0" : "—");
        }
        assert.deepEqual(c.get("events-list").children, rows);
        await c.tick();
        await c.reply(envelope("robot-a", 2, "live", robot(2, 1000)));
        assert.equal(c.get("cpu-usage").textContent, "35.0");
        const old = envelope("robot-a", 3, "live", robot(3, 1500));
        old.stream.age_ms = 2000;
        await c.tick();
        await c.reply(old);
        assert.equal(c.get("cpu-usage").textContent, "—");
    });
    await test("robot loss clears current values, requires new sample, and new stream resets", async () => {
        for (const status of ["stale", "stopped", "error"]) {
            const c = client(true, "robot");
            await c.reply(envelope("robot-a", 1, "live", robot()));
            const rows = c.get("events-list").children;
            await c.tick();
            await c.reply(envelope("robot-a", 1, status, robot()));
            assert.equal(c.get("cpu-usage").textContent, "—");
            assert.equal(c.get("cpu-temperature").textContent, "—");
            assert.deepEqual(c.get("events-list").children, rows);
            await c.tick();
            await c.reply(envelope("robot-a", 1, "live", robot()));
            assert.equal(c.get("cpu-usage").textContent, "—");
            await c.tick();
            await c.reply(envelope("robot-b", 0, "starting", null, "robot"));
            assert.equal(c.get("events-list").children.length, 0);
            await c.tick();
            await c.reply(envelope("robot-b", 1, "live", robot(1, 0)));
            assert.equal(c.get("cpu-usage").textContent, "35.0");
            assert.equal(c.get("events-panel").dataset.mode, "live");
        }
    });
    console.log(`${count}/${count} client protocol/lifecycle tests passed`);
})().catch(error => { console.error(error); process.exitCode = 1; });
