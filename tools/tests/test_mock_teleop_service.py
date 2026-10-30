"""Offline HTTP/protocol/process checks. Real integration uses MOCK_TELEOP_AGENT."""
import copy
import http.client
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("teleop_service", ROOT / "tools/serve_mock_teleop.py")
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


class RecordingBridge:
    def __init__(self):
        self.lines = []
        self.state = service.zero_state()

    def snapshot(self):
        return dict(offline=True, transport_available=True, state=copy.deepcopy(self.state))

    def exchange(self, line):
        self.lines.append(line)
        return self.snapshot()


def packet(action="keys", **changes):
    body = dict(action=action, boot_id="18446744073709551615", session_id="9007199254740993", controller_id="9007199254740997", sequence="9007199254740999", issued_ms=100, valid_until_ms=350, deadman=True)
    body.update(dict(forward=True, backward=False, left=False, right=False) if action == "keys" else dict(axis_x=.5, axis_y=-1))
    body.update(changes)
    return body


class ProtocolTests(unittest.TestCase):
    def test_ids_and_time_preserved(self):
        self.assertEqual(service.protocol_line(packet()), "keys 18446744073709551615 9007199254740993 9007199254740997 9007199254740999 100 350 1 1 0 0 0")

    def test_pad_and_release(self):
        self.assertTrue(service.protocol_line(packet("pad")).endswith("1 0.5 -1"))
        self.assertEqual(service.protocol_line(dict(action="release", controller_id="1", boot_id="2", session_id="3")), "release 1 2 3")

    def test_bad_ids(self):
        for value in [0, 1, "0", "01", "-1", "18446744073709551616", "1\nstop", "1e3"]:
            with self.subTest(value=value), self.assertRaises(ValueError): service.protocol_line(packet(controller_id=value))

    def test_flags_and_axes(self):
        for value in [1, 0, None, "true"]:
            with self.subTest(value=value), self.assertRaises(ValueError): service.protocol_line(packet(deadman=value))
        for value in [float("nan"), float("inf"), -1.01, 1.01, True, "0"]:
            with self.subTest(value=value), self.assertRaises(ValueError): service.protocol_line(packet("pad", axis_x=value))

    def test_invalid_times(self):
        for value in [-1, 2**53, 1.5, True, "100"]:
            with self.subTest(value=value), self.assertRaises(ValueError): service.protocol_line(packet(issued_ms=value))

    def test_clock_policy_is_cpp_responsibility(self):
        self.assertIn("9999 1", service.protocol_line(packet(issued_ms=9999, valid_until_ms=1)))

    def test_exact_schema_and_no_hardware_actions(self):
        for body in [[], {}, dict(action="link", available=True), dict(action="quit"), dict(action="stop", shell="x"), dict(action="arm", controller_id="1", source="ros")]:
            with self.subTest(body=body), self.assertRaises(ValueError): service.protocol_line(body)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.bridge = RecordingBridge()
        self.server = service.TeleopServer(0, self.bridge)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.worker.join()

    def call(self, path="/api/command", body=None, headers=None, method="POST", raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        values = {"Origin": self.server.origin, "Content-Type": "application/json", "X-CSRF-Token": self.server.csrf}
        values.update(headers or {})
        encoded = raw if raw is not None else json.dumps(body or {"action": "stop"})
        connection.request(method, path, encoded if method == "POST" else None, values)
        response = connection.getresponse(); result = (response.status, response.read(), dict(response.getheaders())); connection.close()
        return result

    def test_loopback_only(self): self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_session_and_security_headers(self):
        code, raw, headers = self.call("/api/session", method="GET")
        self.assertEqual(code, 200); self.assertEqual(json.loads(raw)["csrf"], self.server.csrf)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Cache-Control"], "no-store"); self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_post_and_last_seen_clock(self):
        self.assertEqual(self.call(body=packet())[0], 200)
        self.assertEqual(self.bridge.lines, [service.protocol_line(packet())])

    def test_host_origin_csrf_fetch_site(self):
        for headers in [{"Host": "localhost"}, {"Host": "evil.test"}, {"Origin": "null"}, {"Origin": "https://evil.test"}, {"Origin": ""}, {"X-CSRF-Token": ""}, {"X-CSRF-Token": "\u00e9"}, {"Sec-Fetch-Site": "cross-site"}]:
            with self.subTest(headers=headers): self.assertEqual(self.call(headers=headers)[0], 403)
        self.assertEqual(self.bridge.lines, [])

    def test_cross_origin_get_rejected(self): self.assertEqual(self.call("/api/session", method="GET", headers={"Origin": "http://evil.test"})[0], 403)

    def test_types_lengths_transfer(self):
        self.assertEqual(self.call(headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.call(headers={"Transfer-Encoding": "chunked"})[0], 415)
        self.assertEqual(self.call(raw="x" * 4097)[0], 413)
        self.assertEqual(self.call(raw="")[0], 413)

    def test_json_malformed_duplicate_nonfinite(self):
        for raw in ['{', '[]', '{"action":"stop","action":"clear"}', '{"action":"pad","axis_x":NaN}', '{"action":"stop","x":1}']:
            with self.subTest(raw=raw): self.assertEqual(self.call(raw=raw)[0], 400)
        self.assertEqual(self.bridge.lines, [])

    def test_static_allowlist_and_traversal(self):
        for path in ["/", "/index.html", "/app.js", "/core.mjs", "/styles.css"]: self.assertEqual(self.call(path, method="GET")[0], 200)
        for path in ["/../AGENTS.md", "/%2e%2e/AGENTS.md", "/.local/secrets", "/api/state?x=1", "/tests/test_core.mjs"]: self.assertEqual(self.call(path, method="GET")[0], 404)

    def test_unsupported_routes_and_methods(self):
        self.assertEqual(self.call("/api/real-control")[0], 404)
        self.assertEqual(self.call(method="OPTIONS")[0], 405)

    def test_invalid_owner_speed_revokes_only_matching_session(self):
        self.bridge.state.update(armed=True, controller_id="9007199254740997", boot_id="18446744073709551615", session_id="9007199254740993")
        self.assertEqual(self.call(body=packet("pad", axis_x=2))[0], 400)
        self.assertEqual(self.bridge.lines, ["release 9007199254740997 18446744073709551615 9007199254740993"])
        self.bridge.lines.clear()
        self.assertEqual(self.call(body=packet("pad", axis_x=2, controller_id="3"))[0], 400)
        self.assertEqual(self.bridge.lines, [])


    def test_duplicate_security_headers_rejected(self):
        for field, value in [("Host", f"127.0.0.1:{self.server.server_port}"), ("Origin", self.server.origin), ("Content-Length", "17")]:
            with self.subTest(field=field):
                connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
                connection.putrequest("POST", "/api/command")
                connection.putheader("Origin", self.server.origin)
                connection.putheader("Content-Type", "application/json")
                connection.putheader("X-CSRF-Token", self.server.csrf)
                connection.putheader("Content-Length", "17")
                connection.putheader(field, value)
                connection.endheaders(b'{"action":"stop"}')
                response = connection.getresponse(); self.assertIn(response.status, (403, 413)); response.read(); connection.close()
        self.assertEqual(self.bridge.lines, [])


class ChildTests(unittest.TestCase):
    def child(self, mode):
        # Subprocess fixtures are deliberately not acceptance evidence for C++.
        state = service.zero_state()
        source = f"import sys,json,time\ns={state!r}\nfor line in sys.stdin:\n if line.strip()=='quit':\n  print(json.dumps(s),flush=True); break\n if line.strip()=='boom': sys.exit(3)\n if line.strip()=='hang': time.sleep(5)\n if line.strip()=='bad': print('{{}}',flush=True); continue\n if line.strip()=='huge': print('x'*65537,flush=True); continue\n s['elapsed_ms']+=1\n print(json.dumps(s),flush=True)\n"
        return service.AgentBridge([sys.executable, "-u", "-c", source], response_timeout=.2)

    def test_ticker_and_shutdown_reap(self):
        bridge = self.child("ok")
        try:
            before = bridge.snapshot()["state"]["elapsed_ms"]; time.sleep(.13)
            self.assertGreater(bridge.snapshot()["state"]["elapsed_ms"], before)
        finally: bridge.close()
        self.assertIsNotNone(bridge.process.poll()); self.assertEqual(bridge.snapshot()["state"]["linear_mps"], 0)

    def test_faults_zero_and_reap(self):
        for mode in ["boom", "hang", "bad", "huge"]:
            with self.subTest(mode=mode):
                bridge = self.child(mode)
                try:
                    with self.assertRaises(RuntimeError): bridge.exchange(mode)
                    state = bridge.snapshot(); self.assertFalse(state["transport_available"]); self.assertFalse(state["state"]["armed"]); self.assertEqual(state["state"]["linear_mps"], 0)
                finally: bridge.close()

    def test_concurrent_transactions_remain_serial(self):
        bridge = self.child("ok")
        try:
            values = []
            threads = [threading.Thread(target=lambda: values.append(bridge.exchange("tick")["state"]["elapsed_ms"])) for _ in range(20)]
            for t in threads: t.start()
            for t in threads: t.join()
            self.assertEqual(len(set(values)), 20)
        finally: bridge.close()


AGENT = Path(os.environ.get("MOCK_TELEOP_AGENT", ROOT / ".local/build/cmake-debug/mock_teleop.exe"))


@unittest.skipUnless(AGENT.is_file(), "C++ mock_teleop binary not built yet")
class RealAgentTests(unittest.TestCase):
    def setUp(self): self.bridge = service.AgentBridge([str(AGENT)])
    def tearDown(self): self.bridge.close()
    def send(self, line): return self.bridge.exchange(line)["state"]
    def arm(self, controller="9007199254740997", source="keyboard"):
        return self.send(f"arm {controller} {source}")
    def command(self, state, sequence, flags="0 0 0 0 0", issued=None):
        stamp = state["elapsed_ms"] if issued is None else issued
        return self.send(f"keys {state['boot_id']} {state['session_id']} {state['controller_id']} {sequence} {stamp} {stamp+250} {flags}")

    def after_deadline(self, issued):
        # Windows scheduling is not a realtime guarantee. Observe the first
        # periodic agent sample past its own deadline, rather than assuming
        # Python sleep(.3) means that a tick has already run.
        limit = time.monotonic() + 1.5
        while time.monotonic() < limit:
            state = self.bridge.snapshot()["state"]
            if state["elapsed_ms"] >= issued + 250:
                return state
            time.sleep(.01)
        self.fail("periodic agent ticks did not advance past command deadline")

    def test_neutral_enable_release_and_second_owner(self):
        s = self.arm(); self.assertTrue(s["armed"]); self.assertEqual(s["controller_id"], "9007199254740997")
        other = self.arm("99"); self.assertEqual(other["controller_id"], s["controller_id"])
        s = self.command(s, 1); self.assertTrue(s["neutral_confirmed"])
        s = self.command(s, 2, "1 1 0 0 0"); self.assertGreater(s["linear_mps"], 0)
        s = self.command(s, 3, "0 1 0 0 0"); self.assertEqual(s["linear_mps"], 0)
        s = self.send(f"release {s['controller_id']} {s['boot_id']} {s['session_id']}"); self.assertFalse(s["armed"])

    def test_expired_clock_is_not_restamped_and_tick_stops(self):
        s = self.command(self.arm(), 1); s = self.command(s, 2, "1 1 0 0 0")
        s2 = self.after_deadline(s["elapsed_ms"]); self.assertEqual(s2["linear_mps"], 0); self.assertFalse(s2["armed"])
        s3 = self.command(s, 3, "1 1 0 0 0"); self.assertEqual(s3["linear_mps"], 0); self.assertFalse(s3["armed"])

    def test_stop_clear_requires_new_arm(self):
        self.arm(); s = self.send("stop"); self.assertTrue(s["stop_latched"]); self.assertFalse(s["armed"])
        self.assertFalse(self.arm()["armed"]); s = self.send("clear"); self.assertFalse(s["stop_latched"]); self.assertFalse(s["armed"])
        self.assertTrue(self.arm()["armed"])


    def test_old_session_boot_and_duplicate_sequence_do_not_take_control(self):
        old = self.command(self.arm(), 1)
        self.send(f"release {old['controller_id']} {old['boot_id']} {old['session_id']}")
        s = self.command(self.arm(), 1)
        invalid = self.command(old, 2, "1 1 0 0 0")
        self.assertEqual(invalid["session_id"], s["session_id"]); self.assertEqual(invalid["linear_mps"], 0)
        invalid = self.command(dict(s, boot_id="0"), 2, "1 1 0 0 0")
        self.assertEqual(invalid["linear_mps"], 0); self.assertEqual(invalid["sequence"], "1")
        duplicate = self.command(s, 1, "1 1 0 0 0")
        self.assertEqual(duplicate["linear_mps"], 0); self.assertEqual(duplicate["sequence"], "1")

    def test_future_time_does_not_renew_or_move(self):
        s = self.command(self.arm(), 1)
        rejected = self.command(s, 2, "1 1 0 0 0", issued=s["elapsed_ms"]+10000)
        self.assertEqual(rejected["linear_mps"], 0); self.assertEqual(rejected["sequence"], "1")
        self.assertFalse(self.after_deadline(s["elapsed_ms"])["armed"])


if __name__ == "__main__": unittest.main(verbosity=2)
