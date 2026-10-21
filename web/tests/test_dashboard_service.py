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
         "battery": {"voltage_v": None, "quality": "Unknown"},
         "odometry": {"reading": None, "quality": "Unknown"},
         "system_resources": {"reading": None, "quality": "Unknown"}}
READING = {"x_m": 0.0, "y_m": 0.0, "yaw_rad": 0.0, "linear_x_mps": 0.0,
           "linear_y_mps": 0.0, "angular_z_radps": 0.0}
RESOURCES = {"cpu_usage_percent": 35.0, "memory_used_bytes": "2147483648",
             "memory_total_bytes": "8589934592", "cpu_temperature_c": 52.5}


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
        frame["odometry"] = {"reading": dict(READING), "quality": "Stale"}
        frame["system_resources"] = {"reading": dict(RESOURCES), "quality": "Valid"}
        self.buffer.publish(frame)
        self.now = 1.999
        self.assertEqual(self.buffer.snapshot()["stream"]["status"], "live")
        self.now = 2.0
        snapshot = self.buffer.snapshot()
        self.assertEqual(snapshot["stream"]["status"], "stale")
        self.assertEqual(snapshot["stream"]["age_ms"], 2000)
        self.assertEqual(snapshot["state"]["battery"], frame["battery"])
        self.assertEqual(snapshot["state"]["odometry"], frame["odometry"])
        self.assertEqual(snapshot["state"]["system_resources"], frame["system_resources"])

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

    def test_zero_and_negative_odometry_values(self):
        frame = copy.deepcopy(FRAME)
        frame["odometry"] = {"reading": dict(READING), "quality": "Valid"}
        self.buffer.publish(frame)
        self.assertEqual(self.buffer.snapshot()["state"]["odometry"]["reading"], READING)
        frame["sim_time_ms"] = 1
        frame["odometry"]["reading"]["x_m"] = -2.5
        self.buffer.publish(frame)
        self.assertEqual(self.buffer.snapshot()["state"]["odometry"]["reading"]["x_m"], -2.5)

    def test_reject_invalid_odometry_fields(self):
        invalid = [None, {}, {"quality": [], "reading": None},
                   {"quality": "Invalid", "reading": None},
                   {"quality": "Valid", "reading": []},
                   {"quality": "Valid", "reading": {"x_m": 0}}]
        for field in READING:
            for bad in (float("nan"), float("inf"), -float("inf"), True, "0", None, 10 ** 400):
                reading = dict(READING)
                reading[field] = bad
                invalid.append({"quality": "Valid", "reading": reading})
        for odometry in invalid:
            with self.subTest(odometry=odometry), self.assertRaises(ValueError):
                self.buffer.publish(dict(FRAME, odometry=odometry))
        self.assertEqual(self.buffer.snapshot()["stream"]["sequence"], 0)

    def test_odometry_quality_and_missing_sample(self):
        for quality in ("Valid", "Stale"):
            with self.subTest(quality=quality), self.assertRaises(ValueError):
                self.buffer.publish(dict(FRAME, odometry={"reading": None, "quality": quality}))
        for index, quality in enumerate(("Unknown", "Fault", "Unsupported")):
            self.buffer.publish(dict(FRAME, sim_time_ms=index,
                                     odometry={"reading": None, "quality": quality}))
            self.assertIsNone(self.buffer.snapshot()["state"]["odometry"]["reading"])

    def test_legacy_frame_requires_rebuilt_producer(self):
        legacy = copy.deepcopy(FRAME)
        del legacy["odometry"]
        with self.assertRaisesRegex(ValueError, "rebuild"):
            self.buffer.publish(legacy)

    def test_resource_valid_boundaries_and_exact_bytes(self):
        readings = [
            dict(RESOURCES, cpu_usage_percent=0, memory_used_bytes="0",
                 memory_total_bytes=str(service.UINT64_MAX), cpu_temperature_c=None),
            dict(RESOURCES, cpu_usage_percent=100, memory_used_bytes=str(service.UINT64_MAX),
                 memory_total_bytes=str(service.UINT64_MAX), cpu_temperature_c=0),
            dict(RESOURCES, memory_used_bytes=RESOURCES["memory_total_bytes"], cpu_temperature_c=-5.0),
        ]
        for index, reading in enumerate(readings):
            self.buffer.publish(dict(FRAME, sim_time_ms=index,
                                     system_resources={"reading": reading, "quality": "Valid"}))
            snapshot = self.buffer.snapshot()
            self.assertEqual(snapshot["state"]["system_resources"]["reading"], reading)
            self.assertEqual(snapshot["stream"]["status"], "live")
            snapshot["state"]["system_resources"]["reading"]["cpu_usage_percent"] = -1
            self.assertNotEqual(self.buffer.snapshot()["state"]["system_resources"]["reading"]["cpu_usage_percent"], -1)

    def test_reject_invalid_resource_fields(self):
        invalid = [None, {}, {"quality": [], "reading": None},
                   {"quality": "Invalid", "reading": None}, {"quality": "Valid", "reading": []},
                   {"quality": "Valid", "reading": {}}]
        for field, values in (
                ("cpu_usage_percent", (-0.1, 100.1, float("nan"), float("inf"),
                                       -float("inf"), True, "0", None, 10 ** 400)),
                ("cpu_temperature_c", (float("nan"), float("inf"), -float("inf"), True, "0"))):
            for value in values:
                invalid.append({"quality": "Valid", "reading": dict(RESOURCES, **{field: value})})
        for used, total in (("9", "8"), ("0", "0")):
            invalid.append({"quality": "Valid", "reading": dict(RESOURCES,
                            memory_used_bytes=used, memory_total_bytes=total)})
        missing_temperature = dict(RESOURCES)
        del missing_temperature["cpu_temperature_c"]
        invalid.append({"quality": "Valid", "reading": missing_temperature})
        for resources in invalid:
            with self.subTest(resources=resources), self.assertRaises(ValueError):
                self.buffer.publish(dict(FRAME, system_resources=resources))
        self.assertEqual(self.buffer.snapshot()["stream"]["sequence"], 0)

    def test_reject_noncanonical_resource_bytes(self):
        invalid = (0, 1.0, True, None, "", "-1", "+1", "01", "1.0", "1e3",
                   " 1", "1 ", "\u0661", str(service.UINT64_MAX + 1), "9" * 100)
        for field in ("memory_used_bytes", "memory_total_bytes"):
            for value in invalid:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.buffer.publish(dict(FRAME, system_resources={
                        "reading": dict(RESOURCES, **{field: value}), "quality": "Valid"}))
        self.assertEqual(self.buffer.snapshot()["stream"]["sequence"], 0)

    def test_resource_quality_requires_sample(self):
        for quality in ("Valid", "Stale"):
            with self.subTest(quality=quality), self.assertRaises(ValueError):
                self.buffer.publish(dict(FRAME, system_resources={"reading": None, "quality": quality}))
        for index, quality in enumerate(("Unknown", "Fault", "Unsupported")):
            self.buffer.publish(dict(FRAME, sim_time_ms=index,
                                     system_resources={"reading": None, "quality": quality}))
            self.assertIsNone(self.buffer.snapshot()["state"]["system_resources"]["reading"])

    def test_legacy_resources_require_rebuilt_producer(self):
        legacy = copy.deepcopy(FRAME)
        del legacy["system_resources"]
        with self.assertRaisesRegex(ValueError, "rebuild"):
            self.buffer.publish(legacy)

    def test_unsupported_resources_are_not_transport_loss(self):
        frame = copy.deepcopy(FRAME)
        frame["battery"] = {"voltage_v": 22.2, "quality": "Valid"}
        frame["odometry"] = {"reading": dict(READING), "quality": "Valid"}
        frame["system_resources"] = {"reading": None, "quality": "Unsupported"}
        self.buffer.publish(frame)
        snapshot = self.buffer.snapshot()
        self.assertEqual(snapshot["stream"]["status"], "live")
        self.assertEqual(snapshot["state"]["battery"], frame["battery"])
        self.assertEqual(snapshot["state"]["odometry"], frame["odometry"])
        self.assertEqual(snapshot["state"]["system_resources"], frame["system_resources"])

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
                snapshot = json.load(response)
                self.assertEqual(snapshot["api_version"], 3)
                self.assertEqual(snapshot["stream"]["sequence"], 1)
                self.assertEqual(snapshot["state"]["odometry"], FRAME["odometry"])
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
        odometry_observed = []
        resources_observed = []
        resource_independent = False
        saw_absent_temperature = saw_zero_temperature = saw_positive_temperature = saw_full_memory = False
        independent_recovery = False
        last_sequence = 0
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                snapshot = buffer.snapshot()
                if snapshot["stream"]["sequence"] > last_sequence:
                    last_sequence = snapshot["stream"]["sequence"]
                    quality = snapshot["state"]["battery"]["quality"]
                    if not observed or observed[-1] != quality:
                        observed.append(quality)
                    odometry = snapshot["state"]["odometry"]
                    if not odometry_observed or odometry_observed[-1] != odometry["quality"]:
                        odometry_observed.append(odometry["quality"])
                    independent_recovery |= quality == "Stale" and odometry["quality"] == "Valid"
                    resources = snapshot["state"]["system_resources"]
                    if not resources_observed or resources_observed[-1] != resources["quality"]:
                        resources_observed.append(resources["quality"])
                    resource_independent |= quality == "Stale" and resources["quality"] == "Valid"
                    reading = resources["reading"]
                    if reading is not None and resources["quality"] == "Valid":
                        temperature = reading["cpu_temperature_c"]
                        saw_absent_temperature |= temperature is None
                        saw_zero_temperature |= temperature == 0
                        saw_positive_temperature |= temperature is not None and temperature > 0
                        saw_full_memory |= reading["memory_used_bytes"] == reading["memory_total_bytes"]
                    if (observed == ["Unknown", "Valid", "Stale", "Valid"]
                            and odometry_observed == ["Unknown", "Valid", "Stale", "Valid"]
                            and independent_recovery
                            and resources_observed == ["Unknown", "Valid", "Stale", "Valid"]
                            and resource_independent and saw_absent_temperature and saw_zero_temperature
                            and saw_positive_temperature and saw_full_memory):
                        break
                time.sleep(0.02)
            self.assertEqual(observed, ["Unknown", "Valid", "Stale", "Valid"])
            self.assertEqual(odometry_observed, ["Unknown", "Valid", "Stale", "Valid"])
            self.assertTrue(independent_recovery)
            self.assertEqual(resources_observed, ["Unknown", "Valid", "Stale", "Valid"])
            self.assertTrue(resource_independent)
            self.assertTrue(saw_absent_temperature and saw_zero_temperature and saw_positive_temperature)
            self.assertTrue(saw_full_memory)
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
