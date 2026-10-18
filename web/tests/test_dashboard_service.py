"""Acceptance for the local dashboard bridge; no robot or third-party Python packages."""
import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("dashboard_service", ROOT / "tools/serve_dashboard.py")
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)
FRAME = {"source": "mock", "sim_time_ms": 0, "connected": False, "motion_enabled": False,
         "battery": {"voltage_v": None, "quality": "Unknown"}}


class BufferTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.buffer = service.StateBuffer(clock=lambda: self.now)

    def test_starting_and_empty_timeout(self):
        self.assertEqual(self.buffer.snapshot()["stream"]["status"], "starting")
        self.assertIsNone(self.buffer.snapshot()["state"])
        self.now = 2.0
        self.assertEqual(self.buffer.snapshot()["stream"]["status"], "stale")

    def test_transport_expiry_preserves_sensor_quality(self):
        frame = copy.deepcopy(FRAME)
        frame["battery"] = {"voltage_v": 22.2, "quality": "Valid"}
        self.buffer.publish(frame)
        self.now = 1.999
        self.assertEqual(self.buffer.snapshot()["stream"]["status"], "live")
        self.now = 2.0
        snapshot = self.buffer.snapshot()
        self.assertEqual(snapshot["stream"]["status"], "stale")
        self.assertEqual(snapshot["stream"]["age_ms"], 2000)
        self.assertEqual(snapshot["state"]["battery"], frame["battery"])

    def test_sequence_copy_and_generation(self):
        self.buffer.publish(FRAME)
        snapshot = self.buffer.snapshot()
        snapshot["state"]["connected"] = True
        self.assertFalse(self.buffer.snapshot()["state"]["connected"])
        self.assertEqual(self.buffer.snapshot()["stream"]["sequence"], 1)
        self.assertNotEqual(self.buffer.stream_id, service.StateBuffer().stream_id)

    def test_reject_bad_frames(self):
        for changes in ({"source": "real"}, {"motion_enabled": True}, {"sim_time_ms": True},
                        {"connected": 1}, {"battery": {"quality": [], "voltage_v": None}},
                        {"battery": {"quality": "Valid", "voltage_v": float("nan")}},
                        {"battery": {"quality": "Valid", "voltage_v": True}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.buffer.publish(dict(FRAME, **changes))
        self.assertEqual(self.buffer.snapshot()["stream"]["sequence"], 0)

    def test_nonincreasing_time_rejected(self):
        self.buffer.publish(FRAME)
        with self.assertRaises(ValueError):
            self.buffer.publish(FRAME)
        self.assertEqual(self.buffer.snapshot()["stream"]["sequence"], 1)

    def test_terminal_states_and_invalid_threshold(self):
        self.buffer.publish(FRAME)
        self.buffer.finish()
        self.assertEqual(self.buffer.snapshot()["stream"]["status"], "stopped")
        self.buffer.finish("failed")
        self.assertEqual(self.buffer.snapshot()["stream"]["status"], "error")
        for threshold in (0, -1, float("nan")):
            with self.assertRaises(ValueError):
                service.StateBuffer(threshold)


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.buffer = service.StateBuffer()
        self.buffer.publish(FRAME)
        self.server = service.make_server(self.buffer, ROOT / "web", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:{}/".format(self.server.server_address[1])

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_shared_snapshot_and_static_assets(self):
        for _ in range(2):
            with urllib.request.urlopen(self.base + "api/state", timeout=2) as response:
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertEqual(json.load(response)["stream"]["sequence"], 1)
        for asset in ("", "app.js", "live.js", "styles.css", "state.ndjson"):
            with urllib.request.urlopen(self.base + asset, timeout=2) as response:
                self.assertEqual(response.status, 200)
                self.assertTrue(response.read())

    def test_no_repository_files_or_commands(self):
        for path in (".git/config", "../README.md", "README.md", "tools/ssh_manager.py"):
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(self.base + path, timeout=2)
            self.assertEqual(caught.exception.code, 404)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(urllib.request.Request(self.base + "api/state", data=b"{}", method="POST"), timeout=2)
        self.assertEqual(caught.exception.code, 405)


class ProducerTests(unittest.TestCase):
    def wait_status(self, buffer, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            snapshot = buffer.snapshot()
            if predicate(snapshot):
                return snapshot
            time.sleep(0.02)
        self.fail("Timed out waiting for producer state")

    def test_owned_child_is_cleaned_up(self):
        buffer = service.StateBuffer()
        code = "import time; print({!r}, flush=True); time.sleep(30)".format(json.dumps(FRAME))
        producer = service.MockProducer([sys.executable, "-u", "-c", code], buffer)
        try:
            self.wait_status(buffer, lambda value: value["stream"]["sequence"] == 1)
        finally:
            producer.close()
        self.assertIsNotNone(producer.process.poll())
        self.assertFalse(producer._thread.is_alive())

    def test_bad_output_is_reported(self):
        buffer = service.StateBuffer()
        producer = service.MockProducer([sys.executable, "-u", "-c", "print('null voltage')"], buffer)
        try:
            self.wait_status(buffer, lambda value: value["stream"]["status"] == "error")
        finally:
            producer.close()

    def test_real_cpp_unknown_valid_stale_recovery(self):
        buffer = service.StateBuffer()
        env = os.environ.copy()
        if os.name == "nt":
            env["PATH"] = "C:/msys64/ucrt64/bin" + os.pathsep + env.get("PATH", "")
        producer = service.MockProducer([str(MOCK_EXECUTABLE), "--stream"], buffer, env)
        observed = []
        last_sequence = 0
        try:
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                snapshot = buffer.snapshot()
                if snapshot["stream"]["sequence"] > last_sequence:
                    last_sequence = snapshot["stream"]["sequence"]
                    quality = snapshot["state"]["battery"]["quality"]
                    if not observed or observed[-1] != quality:
                        observed.append(quality)
                    if observed == ["Unknown", "Valid", "Stale", "Valid"]:
                        break
                time.sleep(0.02)
            self.assertEqual(observed, ["Unknown", "Valid", "Stale", "Valid"])
        finally:
            producer.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock-executable", type=Path, default=service.default_executable())
    options, remaining = parser.parse_known_args()
    MOCK_EXECUTABLE = options.mock_executable.resolve()
    if not MOCK_EXECUTABLE.is_file():
        parser.error("Build mock_robot_state first or pass --mock-executable")
    unittest.main(argv=[sys.argv[0]] + remaining)
