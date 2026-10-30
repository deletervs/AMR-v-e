"""Acceptance for the local dashboard bridge; no robot or third-party Python packages."""
import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import shlex
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("dashboard_service", ROOT / "tools/serve_dashboard.py")
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)
MOCK_EXECUTABLE = service.default_executable().resolve()
FRAME = {"source": "mock", "sim_time_ms": 0, "connected": False, "motion_enabled": False,
         "battery": {"voltage_v": None, "quality": "Unknown"},
         "odometry": {"reading": None, "quality": "Unknown"},
         "system_resources": {"reading": None, "quality": "Unknown"},
         "event_log": {"entries": [], "truncated": False}}
READING = {"x_m": 0.0, "y_m": 0.0, "yaw_rad": 0.0, "linear_x_mps": 0.0,
           "linear_y_mps": 0.0, "angular_z_radps": 0.0}
RESOURCES = {"cpu_usage_percent": 35.0, "memory_used_bytes": "2147483648",
             "memory_total_bytes": "8589934592", "cpu_temperature_c": 52.5}
EVENT = {"elapsed_ms": 0, "level": "Info", "source": "mock", "message": "started"}
ROBOT_FRAME = copy.deepcopy(FRAME)
del ROBOT_FRAME["sim_time_ms"]
ROBOT_FRAME.update(source="robot", elapsed_ms=500, sequence=1, connected=True,
                   system_resources={"reading": dict(RESOURCES), "quality": "Valid"},
                   event_log={"entries": [dict(EVENT, source="robot")], "truncated": False},
                   telemetry={"hostname": "handsfree", "cpu": "/proc/stat", "memory": "/proc/meminfo",
                              "temperature": "/sys/class/thermal/thermal_zone1/temp"})


def battery_frame(sequence=1, elapsed=500, **battery_changes):
    frame = copy.deepcopy(ROBOT_FRAME)
    frame.update(sequence=sequence, elapsed_ms=elapsed, capabilities={"battery": True})
    frame["battery"] = dict(voltage_v=24.1, quality="Valid", received_elapsed_ms=400,
                            source_stamp_ns="1793200000123456789", source_time_raw=123.5)
    frame["battery"].update(battery_changes)
    return frame


class EventLogTests(unittest.TestCase):
    def frame(self, entries=None, truncated=False, time_ms=0):
        return dict(FRAME, sim_time_ms=time_ms, event_log={
            "entries": copy.deepcopy([EVENT] if entries is None else entries), "truncated": truncated})

    def test_utf8_boundaries_controls_and_equal_times(self):
        entries = [dict(EVENT, source="中" * 21 + "a", message="😀" * 128),
                   dict(EVENT, level="Warning", message='引号"\\\n\r\t\x00 <img src=x onerror=alert(1)>'),
                   dict(EVENT, level="Error", source="s" * 64, message="m" * 512)]
        service.validate_frame(self.frame(entries))
        service.validate_frame(self.frame([]))

    def test_invalid_event_objects_and_strict_truncated(self):
        for value in (None, [], {}, {"entries": [], "truncated": 0},
                      {"entries": [], "truncated": "false"},
                      {"entries": {}, "truncated": False},
                      {"entries": [EVENT] * 101, "truncated": False},
                      {"entries": [], "truncated": True}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                service.validate_frame(dict(FRAME, event_log=value))
        legacy = copy.deepcopy(FRAME)
        del legacy["event_log"]
        with self.assertRaisesRegex(ValueError, "rebuild"):
            service.validate_frame(legacy)

    def test_invalid_event_fields_do_not_publish(self):
        buffer = service.StateBuffer()
        buffer.publish(self.frame())
        before = buffer.snapshot()
        invalid = [None, [], {}, dict(EVENT, elapsed_ms=-1), dict(EVENT, elapsed_ms=True),
                   dict(EVENT, elapsed_ms=2), dict(EVENT, elapsed_ms=0.0),
                   dict(EVENT, level=[]), dict(EVENT, level="Debug")]
        for field, values in (("source", (None, "", "a" * 65, "中" * 22, "\ud800")),
                              ("message", ([], "", "a" * 513, "😀" * 129, "\udfff"))):
            invalid.extend(dict(EVENT, **{field: value}) for value in values)
        for entry in invalid:
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                frame = self.frame([entry], time_ms=1)
                service.validate_frame(frame)
            with self.assertRaises(ValueError):
                buffer.publish(frame)
        after = buffer.snapshot()
        self.assertEqual(after["state"], before["state"])
        self.assertEqual(after["stream"]["sequence"], 1)

    def test_time_order_and_javascript_integer_boundary(self):
        service.validate_frame(self.frame([dict(EVENT, elapsed_ms=service.MAX_SAFE_INTEGER)],
                                         time_ms=service.MAX_SAFE_INTEGER))
        invalid = [self.frame([dict(EVENT, elapsed_ms=2), dict(EVENT, elapsed_ms=1)], time_ms=2),
                   self.frame(time_ms=service.MAX_SAFE_INTEGER + 1),
                   self.frame(time_ms=-1), self.frame(time_ms=True)]
        for frame in invalid:
            with self.subTest(frame=frame), self.assertRaises(ValueError):
                service.validate_frame(frame)

    def test_capacity_truncation_copy_and_terminal_retention(self):
        buffer = service.StateBuffer()
        frame = self.frame([EVENT] * 100, truncated=True)
        buffer.publish(frame)
        frame["event_log"]["entries"][0]["message"] = "caller mutation"
        snapshot = buffer.snapshot()
        self.assertEqual(snapshot["state"]["event_log"]["entries"][0]["message"], "started")
        snapshot["state"]["event_log"]["entries"].clear()
        buffer.finish("bad next frame")
        stopped = buffer.snapshot()
        self.assertEqual(len(stopped["state"]["event_log"]["entries"]), 100)
        self.assertTrue(stopped["state"]["event_log"]["truncated"])
        self.assertEqual(stopped["stream"]["status"], "error")


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


class RobotContractTests(unittest.TestCase):
    def test_battery_capability_empty_and_legacy_contract(self):
        service.validate_frame(ROBOT_FRAME, "robot")
        for capability in (True, False):
            frame = battery_frame(voltage_v=None, quality="Unknown", received_elapsed_ms=None,
                                  source_stamp_ns=None, source_time_raw=None)
            frame["capabilities"]["battery"] = capability
            service.validate_frame(frame, "robot")
        for capability in (None, [], {}, True, {"battery": 1}, {"battery": "true"}, {"battery": None}):
            with self.subTest(capability=capability), self.assertRaises(ValueError):
                service.validate_frame(dict(battery_frame(), capabilities=capability), "robot")
        for changes in ({"capabilities": {"battery": False}},
                        {"battery": dict(ROBOT_FRAME["battery"], received_elapsed_ms=None)}):
            frame = copy.deepcopy(ROBOT_FRAME) if "battery" in changes else battery_frame()
            frame.update(changes)
            with self.assertRaises(ValueError):
                service.validate_frame(frame, "robot")

    def test_battery_zero_and_exact_uint64_stamps(self):
        for voltage in (0, 24.1):
            for stamp in ("1", "9007199254740993", str(service.UINT64_MAX)):
                frame = battery_frame(voltage_v=voltage, source_stamp_ns=stamp, received_elapsed_ms=0)
                buffer = service.StateBuffer(expected_source="robot")
                buffer.publish(frame)
                self.assertEqual(buffer.snapshot()["state"]["battery"], frame["battery"])

    def test_battery_invalid_or_missing_metadata(self):
        invalid = {
            "voltage_v": (-1, True, float("nan"), float("inf"), "24"),
            "received_elapsed_ms": (-1, True, 1.5, 501, None, service.MAX_SAFE_INTEGER + 1),
            "source_stamp_ns": (0, 1.0, True, None, "0", "01", "+1", "-1", "1e3", "1.0", " 1", "1 ",
                                "1\n", "\u0661", str(service.UINT64_MAX + 1)),
            "source_time_raw": (0, -1, True, None, "1", float("nan"), float("inf"), 10 ** 400),
        }
        for name, values in invalid.items():
            for value in values:
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    service.validate_frame(battery_frame(**{name: value}), "robot")
        for name in service.BATTERY_METADATA:
            frame = battery_frame()
            del frame["battery"][name]
            with self.subTest(missing=name), self.assertRaises(ValueError):
                service.validate_frame(frame, "robot")
        for quality in service.QUALITIES:
            frame = battery_frame(voltage_v=None, quality=quality, received_elapsed_ms=None,
                                  source_stamp_ns=None, source_time_raw=None)
            if quality in ("Valid", "Stale"):
                with self.assertRaises(ValueError):
                    service.validate_frame(frame, "robot")
            else:
                service.validate_frame(frame, "robot")
            frame["battery"]["source_time_raw"] = 1
            with self.assertRaises(ValueError):
                service.validate_frame(frame, "robot")

    def test_battery_progress_rejects_mutation_without_renewing_transport(self):
        now = [0.0]
        buffer = service.StateBuffer(expected_source="robot", clock=lambda: now[0])
        buffer.publish(battery_frame())
        now[0] = 1.0
        for changes in ({"received_elapsed_ms": 399}, {"voltage_v": 24.2},
                        {"source_stamp_ns": "1793200000123456790"}, {"source_time_raw": 124}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                buffer.publish(battery_frame(2, 1000, **changes))
            self.assertEqual(buffer.snapshot()["stream"]["sequence"], 1)
            self.assertEqual(buffer.snapshot()["stream"]["age_ms"], 1000)
        # An intervening empty frame cannot erase the last accepted reception time.
        buffer.publish(battery_frame(2, 1000, voltage_v=None, quality="Fault", received_elapsed_ms=None,
                                     source_stamp_ns=None, source_time_raw=None))
        with self.assertRaises(ValueError):
            buffer.publish(battery_frame(3, 1500, received_elapsed_ms=399))

    def test_battery_stale_keeps_resources_live_then_recovers_across_source_clock_reset(self):
        buffer = service.StateBuffer(expected_source="robot")
        buffer.publish(battery_frame())
        buffer.publish(battery_frame(2, 4000, quality="Stale"))
        state = buffer.snapshot()
        self.assertEqual(state["stream"]["status"], "live")
        self.assertEqual(state["state"]["battery"]["quality"], "Stale")
        self.assertEqual(state["state"]["system_resources"]["quality"], "Valid")
        buffer.publish(battery_frame(3, 4500, received_elapsed_ms=4450, source_stamp_ns="1", source_time_raw=1))
        self.assertEqual(buffer.snapshot()["state"]["battery"]["quality"], "Valid")
        fresh = service.StateBuffer(expected_source="robot")
        fresh.publish(battery_frame(elapsed=0, received_elapsed_ms=0))
        self.assertNotEqual(fresh.stream_id, buffer.stream_id)

    def test_explicit_source_and_no_mixing(self):
        with self.assertRaises(ValueError):
            service.StateBuffer(expected_source="anything")
        robot = service.StateBuffer(expected_source="robot")
        self.assertEqual(robot.snapshot()["stream"]["source"], "robot")
        self.assertEqual(robot.snapshot()["stream"]["status"], "starting")
        for buffer, first, second in ((robot, ROBOT_FRAME, FRAME),
                                      (service.StateBuffer(), FRAME, ROBOT_FRAME)):
            with self.assertRaises(ValueError):
                buffer.publish(second)
            buffer.publish(first)
            with self.assertRaises(ValueError):
                buffer.publish(second)
            self.assertEqual(buffer.snapshot()["state"], first)

    def test_time_and_sequence_are_strict_and_independent(self):
        buffer = service.StateBuffer(expected_source="robot")
        buffer.publish(ROBOT_FRAME)
        invalid = [dict(ROBOT_FRAME, sequence=2), dict(ROBOT_FRAME, elapsed_ms=501),
                   dict(ROBOT_FRAME, sequence=2, elapsed_ms=499)]
        for field in ("elapsed_ms", "sequence"):
            for value in (-1, True, 1.5, None, service.MAX_SAFE_INTEGER + 1):
                invalid.append(dict(ROBOT_FRAME, **{field: value}))
        invalid += [dict(ROBOT_FRAME, sequence=0), dict(ROBOT_FRAME, sim_time_ms=500)]
        for frame in invalid:
            with self.subTest(frame=frame), self.assertRaises(ValueError):
                buffer.publish(frame)
        next_frame = dict(ROBOT_FRAME, sequence=8, elapsed_ms=501)
        buffer.publish(next_frame)
        self.assertEqual(buffer.snapshot()["stream"]["sequence"], 2)
        self.assertEqual(buffer.snapshot()["state"]["sequence"], 8)

    def test_real_channels_stay_unknown_and_events_use_real_time(self):
        for change in ({"battery": {"voltage_v": 24, "quality": "Valid"}},
                       {"odometry": {"reading": dict(READING), "quality": "Valid"}},
                       {"event_log": {"entries": [dict(EVENT, elapsed_ms=501)], "truncated": False}},
                       {"motion_enabled": True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                service.validate_frame(dict(ROBOT_FRAME, **change), "robot")
        no_temperature = copy.deepcopy(ROBOT_FRAME)
        no_temperature["system_resources"]["reading"]["cpu_temperature_c"] = None
        service.validate_frame(no_temperature, "robot")

    def test_polling_does_not_renew_age_and_new_stream_can_restart(self):
        now = [0.0]
        buffer = service.StateBuffer(clock=lambda: now[0], expected_source="robot")
        buffer.publish(ROBOT_FRAME)
        for tick in (0.5, 1.5, 2.0):
            now[0] = tick
            snapshot = buffer.snapshot()
            self.assertEqual(snapshot["stream"]["age_ms"], int(tick * 1000))
        self.assertEqual(snapshot["stream"]["status"], "stale")
        self.assertEqual(snapshot["state"]["event_log"], ROBOT_FRAME["event_log"])
        fresh = service.StateBuffer(expected_source="robot")
        self.assertNotEqual(fresh.stream_id, buffer.stream_id)
        fresh.publish(dict(ROBOT_FRAME, elapsed_ms=0))
        self.assertEqual(fresh.snapshot()["stream"]["status"], "live")

    def test_fixed_ssh_command_quotes_remote_path_and_secures_both_hops(self):
        with tempfile.TemporaryDirectory() as directory:
            known = Path(directory) / "public known_hosts"
            known.write_text("test public host record", encoding="utf-8")
            path = "/tmp/rs 'read only'; echo x/robot_state_agent"
            command = service.robot_command("robot-user@robot-host", "llmemb-server", path, known)
            self.assertEqual(command[0:2], ["ssh", "-T"])
            self.assertEqual(command[-2], "robot-user@robot-host")
            self.assertEqual(shlex.split(command[-1]), ["exec", path])
            self.assertIn("BatchMode=yes", command)
            self.assertIn("StrictHostKeyChecking=yes", command)
            self.assertIn('UserKnownHostsFile="' + known.as_posix() + '"', command)
            proxy = next(item for item in command if item.startswith("ProxyCommand="))
            for text in ("BatchMode=yes", "StrictHostKeyChecking=yes", "NumberOfPasswordPrompts=0",
                         "ConnectionAttempts=1", known.as_posix(), "llmemb-server", "%h:%p"):
                self.assertIn(text, proxy)
            for target, jump, executable, hosts in (
                    ("-evil", "llmemb-server", path, known),
                    ("handsfree@host;id", "llmemb-server", path, known),
                    ("host", "jump -o x", path, known),
                    ("host", "jump", "robot_state_agent", known),
                    ("host", "jump", "/bin/sh", known),
                    ("host", "jump", "/tmp/robot_state_agent\n", known),
                    ("host", "jump", path, Path("relative_known_hosts"))):
                with self.subTest(target=target, path=executable), self.assertRaises(ValueError):
                    service.robot_command(target, jump, executable, hosts)

    def test_invalid_cli_is_rejected_before_ssh(self):
        for flags in (["--robot-target", "handsfree@host"], ["--robot-executable", "/tmp/robot_state_agent"],
                      ["--known-hosts", "missing"], ["--jump-host", "other"]):
            result = subprocess.run([sys.executable, str(ROOT / "tools/serve_dashboard.py")] + flags,
                                    stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 2)
            self.assertIn("error:", result.stderr)


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
                self.assertEqual(snapshot["api_version"], 6)
                self.assertEqual(snapshot["stream"]["source"], "mock")
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

    def test_robot_http_source_age_stale_and_recovery(self):
        now = [0.0]
        buffer = service.StateBuffer(clock=lambda: now[0], expected_source="robot")
        server = service.make_server(buffer, ROOT / "web", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = "http://127.0.0.1:{}/api/state".format(server.server_address[1])

        def get():
            with urllib.request.urlopen(url, timeout=2) as response:
                return json.load(response)

        try:
            self.assertEqual(get()["stream"]["status"], "starting")
            buffer.publish(ROBOT_FRAME)
            first = get()
            self.assertEqual(first["api_version"], 6)
            self.assertEqual(first["stream"]["source"], "robot")
            self.assertEqual(first["state"], ROBOT_FRAME)
            for tick in (0.5, 1.5, 2.0):
                now[0] = tick
                self.assertEqual(get()["stream"]["age_ms"], int(tick * 1000))
            self.assertEqual(get()["stream"]["status"], "stale")
            buffer.publish(dict(ROBOT_FRAME, elapsed_ms=1000, sequence=2))
            self.assertEqual(get()["stream"]["status"], "live")
            self.assertEqual(get()["stream"]["age_ms"], 0)
            buffer.finish("SSH exited with code 255")
            self.assertEqual(get()["stream"]["status"], "error")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_robot_http_preserves_battery_metadata_and_independent_quality(self):
        buffer = service.StateBuffer(expected_source="robot")
        buffer.publish(battery_frame(voltage_v=0, source_stamp_ns=str(service.UINT64_MAX)))
        server = service.make_server(buffer, ROOT / "web", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = "http://127.0.0.1:{}/api/state".format(server.server_address[1])
        try:
            for sequence, quality in ((2, "Stale"), (3, "Valid")):
                changes = {"quality": quality, "voltage_v": 0, "source_stamp_ns": str(service.UINT64_MAX)}
                if quality == "Valid":
                    changes.update(received_elapsed_ms=4500, source_stamp_ns="1", source_time_raw=1)
                buffer.publish(battery_frame(sequence, sequence * 2000, **changes))
                with urllib.request.urlopen(url, timeout=2) as response:
                    value = json.load(response)
                self.assertEqual(value["api_version"], 6)
                self.assertEqual(value["stream"]["status"], "live")
                self.assertEqual(value["state"]["battery"]["quality"], quality)
                self.assertEqual(value["state"]["system_resources"]["quality"], "Valid")
                self.assertEqual(value["state"]["battery"]["source_stamp_ns"], changes["source_stamp_ns"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


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

    def test_nonzero_exit_is_error_with_and_without_sample(self):
        for prefix in ("", "print({!r}, flush=True); ".format(json.dumps(ROBOT_FRAME))):
            buffer = service.StateBuffer(expected_source="robot")
            producer = service.StateProducer([sys.executable, "-u", "-c", prefix + "raise SystemExit(7)"], buffer)
            try:
                snapshot = self.wait_status(buffer, lambda value: value["stream"]["status"] == "error")
                self.assertIn("code 7", snapshot["stream"]["message"])
            finally:
                producer.close()
            self.assertIsNotNone(producer.process.poll())

    def test_robot_fake_child_goes_stale_recovers_and_rejects_mixed_source(self):
        buffer = service.StateBuffer(stale_after_s=0.05, expected_source="robot")
        later = dict(ROBOT_FRAME, elapsed_ms=1000, sequence=2)
        code = ("import time; print({!r}, flush=True); time.sleep(.2); "
                "print({!r}, flush=True); time.sleep(.2); print({!r}, flush=True)").format(
                    json.dumps(ROBOT_FRAME), json.dumps(later), json.dumps(FRAME))
        producer = service.StateProducer([sys.executable, "-u", "-c", code], buffer)
        try:
            self.wait_status(buffer, lambda v: v["stream"]["status"] == "live")
            self.wait_status(buffer, lambda v: v["stream"]["status"] == "stale")
            value = self.wait_status(buffer, lambda v: v["stream"]["sequence"] == 2)
            self.assertEqual(value["state"]["elapsed_ms"], 1000)
            value = self.wait_status(buffer, lambda v: v["stream"]["status"] == "error")
            self.assertEqual(value["stream"]["sequence"], 2)
            self.assertEqual(value["state"]["source"], "robot")
        finally:
            producer.close()

    def test_robot_owned_child_is_cleaned_up(self):
        buffer = service.StateBuffer(expected_source="robot")
        code = "import time; print({!r}, flush=True); time.sleep(30)".format(json.dumps(ROBOT_FRAME))
        producer = service.StateProducer([sys.executable, "-u", "-c", code], buffer)
        try:
            self.wait_status(buffer, lambda v: v["stream"]["sequence"] == 1)
        finally:
            producer.close()
        self.assertIsNotNone(producer.process.poll())
        self.assertFalse(producer._thread.is_alive())

    def test_large_escaped_history_fits_transport(self):
        buffer = service.StateBuffer()
        # Generates >64KiB from legal 512-byte control text without a huge command argument.
        code = ("import json; f=" + repr(FRAME) + "; f['event_log']="
                "{'entries':[{'elapsed_ms':0,'level':'Info','source':'s'*64,"
                "'message':'\\x00'*512} for _ in range(100)],'truncated':True}; print(json.dumps(f))")
        producer = service.MockProducer([sys.executable, "-u", "-c", code], buffer)
        try:
            value = self.wait_status(buffer, lambda v: v["stream"]["status"] in ("stopped", "error"))
            self.assertEqual(value["stream"]["status"], "stopped")
            self.assertEqual(value["stream"]["sequence"], 1)
            self.assertGreater(len(json.dumps(value["state"])), 65536)
            self.assertEqual(value["state"]["event_log"]["entries"][0]["message"], "\x00" * 512)
        finally:
            producer.close()

    def test_oversized_incomplete_and_invalid_utf8_lines(self):
        for code in ("import sys; sys.stdout.buffer.write(b'x'*(512*1024)+b'\\n')",
                     "import sys; sys.stdout.buffer.write(b'{}')",
                     "import sys; sys.stdout.buffer.write(b'\\xff\\n')"):
            buffer = service.StateBuffer()
            producer = service.MockProducer([sys.executable, "-u", "-c", code], buffer)
            try:
                value = self.wait_status(buffer, lambda v: v["stream"]["status"] == "error")
                self.assertEqual(value["stream"]["sequence"], 0)
            finally:
                producer.close()

    def test_real_cpp_full_escaped_history(self):
        buffer = service.StateBuffer()
        env = os.environ.copy()
        if os.name == "nt":
            env["PATH"] = "C:/msys64/ucrt64/bin" + os.pathsep + env.get("PATH", "")
        producer = service.MockProducer([str(MOCK_EXECUTABLE), "--event-log-test"], buffer, env)
        try:
            value = self.wait_status(buffer, lambda v: v["stream"]["status"] in ("stopped", "error"))
            self.assertEqual(value["stream"]["status"], "stopped")
            self.assertEqual(value["stream"]["sequence"], 1)
            log = value["state"]["event_log"]
            self.assertEqual(len(log["entries"]), 100)
            self.assertTrue(log["truncated"])
            text = "".join(e["message"] for e in log["entries"])
            for character in ('"', "\\", "\n", "\r", "\t", "\x00", "中"):
                self.assertIn(character, text)
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
