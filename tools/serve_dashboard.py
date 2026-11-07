"""Local, read-only bridge from a C++ telemetry producer (Python 3.8+)."""
import argparse
import ipaddress
import copy
import functools
import json
import math
import os
import re
import shlex
from pathlib import Path, PurePosixPath
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler
try:
    from http.server import ThreadingHTTPServer
except ImportError:  # Python3.6 robot-side consumers import only buffer/validation.
    from http.server import HTTPServer
    from socketserver import ThreadingMixIn
    class ThreadingHTTPServer(ThreadingMixIn,HTTPServer):
        daemon_threads=True
from urllib.parse import urlsplit
import uuid
import sys

# Support both direct CLI execution and consumers loading this module by path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from camera_stream import CameraBuffer, CameraProducer, require_decoder

ROOT = Path(__file__).resolve().parents[1]
QUALITIES = {"Unknown", "Valid", "Stale", "Fault", "Unsupported"}
MAX_LINE = 512 * 1024
API_VERSION = 8
MAX_SAFE_INTEGER = 9007199254740991
LOG_LIMIT = 100
LOG_LEVELS = {"Info", "Warning", "Error"}
UINT64_MAX = 18446744073709551615
ODOMETRY_FIELDS = ("x_m", "y_m", "yaw_rad", "linear_x_mps", "linear_y_mps", "angular_z_radps")
BATTERY_METADATA = ("received_elapsed_ms", "source_stamp_ns", "source_time_raw")
ODOMETRY_METADATA = ("received_elapsed_ms", "source_stamp_ns", "source_sequence",
                     "source_twist_sequence", "frame_id", "child_frame_id")
UINT32_MAX = 4294967295
LIDAR_FIELDS = ("angle_min_rad", "angle_max_rad", "angle_increment_rad", "range_min_m",
                "range_max_m", "scan_time_s", "time_increment_s")
LIDAR_METADATA = ("received_elapsed_ms", "source_stamp_ns", "source_sequence", "frame_id")


def validate_robot_lidar(frame):
    capabilities = frame.get("capabilities", {})
    lidar = frame.get("lidar")
    if "lidar" not in capabilities:
        if "lidar" in frame and lidar != {"reading": None, "quality": "Unknown"}:
            raise ValueError("legacy robot lidar must be absent or exactly Unknown/null")
        return
    if any(type(capabilities.get(name)) is not bool for name in ("battery", "odometry", "lidar")):
        raise ValueError("API8 robot capabilities require three booleans")
    telemetry = frame.get("telemetry")
    if not isinstance(telemetry, dict) or "lidar_topic" not in telemetry:
        raise ValueError("API8 robot requires lidar telemetry topic")
    if (not isinstance(lidar, dict) or "reading" not in lidar
            or type(lidar.get("quality")) is not str or lidar["quality"] not in QUALITIES
            or any(name not in lidar for name in LIDAR_METADATA)):
        raise ValueError("missing or invalid lidar fields")
    reading = lidar["reading"]
    if not capabilities["lidar"] and (reading is not None or lidar["quality"] != "Unknown"):
        raise ValueError("unconnected lidar must be Unknown/null")
    if reading is None:
        if (lidar["quality"] in ("Valid", "Stale")
                or any(lidar[name] is not None for name in LIDAR_METADATA)):
            raise ValueError("missing lidar requires null metadata and unusable quality")
        return
    if (not isinstance(reading, dict) or any(not finite_number(reading.get(name)) for name in LIDAR_FIELDS)
            or type(reading.get("ranges_m")) is not list or not 1 <= len(reading["ranges_m"]) <= 4096):
        raise ValueError("invalid lidar geometry or ray count")
    count = len(reading["ranges_m"])
    increment = reading["angle_increment_rad"]
    span = (count - 1) * increment
    end = reading["angle_min_rad"] + span
    if (increment == 0 or reading["range_min_m"] < 0 or reading["range_max_m"] <= reading["range_min_m"]
            or reading["scan_time_s"] < 0 or reading["time_increment_s"] < 0
            or not finite_number(end) or abs(span) > 2 * math.pi + 0.001
            or abs(end - reading["angle_max_rad"]) > max(0.0001, abs(increment) * 1.05)
            or (reading["scan_time_s"] > 0 and reading["time_increment_s"] > 0
                and reading["time_increment_s"] * (count - 1) > reading["scan_time_s"] + 0.001)):
        raise ValueError("inconsistent lidar geometry or timing")
    if any(value is not None and (not finite_number(value)
           or not reading["range_min_m"] <= value <= reading["range_max_m"]) for value in reading["ranges_m"]):
        raise ValueError("lidar ranges must be legal finite distances or null")
    received = lidar["received_elapsed_ms"]
    if type(received) is not int or not 0 <= received <= frame["elapsed_ms"]:
        raise ValueError("invalid lidar reception time")
    if uint64_bytes(lidar["source_stamp_ns"], "lidar source stamp") == 0:
        raise ValueError("lidar source stamp must be positive")
    if type(lidar["source_sequence"]) is not int or not 0 <= lidar["source_sequence"] <= UINT32_MAX:
        raise ValueError("invalid lidar source sequence")
    if type(lidar["frame_id"]) is not str or re.fullmatch(r"[A-Za-z0-9_/.-]{1,64}", lidar["frame_id"]) is None:
        raise ValueError("invalid lidar frame id")


def next_lidar_sample(previous, frame):
    lidar = frame.get("lidar")
    if frame["source"] != "robot" or lidar is None or lidar["reading"] is None:
        return previous
    if previous is not None:
        received = lidar["received_elapsed_ms"]
        if received < previous["received_elapsed_ms"]:
            raise ValueError("lidar reception time must not regress")
        if received == previous["received_elapsed_ms"] and (
                any(lidar[name] != previous[name] for name in LIDAR_METADATA)
                or lidar["reading"] != previous["reading"]):
            raise ValueError("same lidar reception time must retain its sample")
    return lidar


def finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def uint64_bytes(value, label="resource bytes"):
    if type(value) is not str or re.fullmatch(r"(?:0|[1-9][0-9]{0,19})", value) is None:
        raise ValueError(label + " must be canonical unsigned decimal strings")
    number = int(value)
    if number > UINT64_MAX:
        raise ValueError(label + " exceeds uint64")
    return number


def utf8_length(value):
    if type(value) is not str:
        raise ValueError("event text must be a string")
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError:
        raise ValueError("event text must be valid UTF-8")


def validate_event_log(events, frame_time):
    if (not isinstance(events, dict) or type(events.get("truncated")) is not bool
            or type(events.get("entries")) is not list or len(events["entries"]) > LOG_LIMIT):
        raise ValueError("invalid event log; rebuild the mock producer")
    entries = events["entries"]
    if events["truncated"] and len(entries) != LOG_LIMIT:
        raise ValueError("truncated history must retain the latest 100 events")
    previous = -1
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("invalid event entry")
        elapsed = entry.get("elapsed_ms")
        if (type(elapsed) is not int or not previous <= elapsed <= frame_time
                or elapsed < 0 or elapsed > MAX_SAFE_INTEGER):
            raise ValueError("invalid event time")
        if type(entry.get("level")) is not str or entry["level"] not in LOG_LEVELS:
            raise ValueError("invalid event level")
        if not 1 <= utf8_length(entry.get("source")) <= 64:
            raise ValueError("invalid event source length")
        if not 1 <= utf8_length(entry.get("message")) <= 512:
            raise ValueError("invalid event message length")
        previous = elapsed


def frame_time(frame):
    return frame["elapsed_ms" if frame["source"] == "robot" else "sim_time_ms"]


def validate_robot_battery(frame):
    battery = frame["battery"]
    if "capabilities" not in frame:
        if battery != {"voltage_v": None, "quality": "Unknown"}:
            raise ValueError("legacy robot battery must be exactly Unknown/null")
        return
    capabilities = frame["capabilities"]
    if not isinstance(capabilities, dict) or type(capabilities.get("battery")) is not bool:
        raise ValueError("invalid robot battery capability")
    if any(name not in battery for name in BATTERY_METADATA):
        raise ValueError("missing battery metadata")
    voltage = battery["voltage_v"]
    if not capabilities["battery"]:
        if voltage is not None or battery["quality"] != "Unknown":
            raise ValueError("unconnected battery must be Unknown/null")
    if voltage is None:
        if (battery["quality"] in ("Valid", "Stale")
                or any(battery[name] is not None for name in BATTERY_METADATA)):
            raise ValueError("missing voltage requires null metadata and unusable quality")
        return
    if not finite_number(voltage) or voltage < 0:
        raise ValueError("robot voltage must be finite and nonnegative")
    received = battery["received_elapsed_ms"]
    if type(received) is not int or not 0 <= received <= frame["elapsed_ms"]:
        raise ValueError("invalid battery reception time")
    if uint64_bytes(battery["source_stamp_ns"], "battery source stamp") == 0:
        raise ValueError("battery source stamp must be positive")
    if not finite_number(battery["source_time_raw"]) or battery["source_time_raw"] <= 0:
        raise ValueError("battery raw MCU time must be finite and positive")


def next_battery_sample(previous, frame):
    """Keep the last accepted sample across empty frames; clocks are separate domains."""
    battery = frame["battery"]
    if frame["source"] != "robot" or battery["voltage_v"] is None:
        return previous
    if previous is not None:
        received = battery["received_elapsed_ms"]
        if received < previous["received_elapsed_ms"]:
            raise ValueError("battery reception time must not regress")
        if received == previous["received_elapsed_ms"] and any(
                battery[name] != previous[name] for name in ("voltage_v",) + BATTERY_METADATA):
            raise ValueError("same battery reception time must retain its sample")
    return battery


def validate_robot_odometry(frame):
    odometry = frame.get("odometry")
    capabilities = frame.get("capabilities", {})
    if "odometry" not in capabilities:
        if odometry != {"reading": None, "quality": "Unknown"}:
            raise ValueError("legacy robot odometry must be exactly Unknown/null")
        return
    if type(capabilities["odometry"]) is not bool:
        raise ValueError("invalid robot odometry capability")
    if not isinstance(odometry, dict) or any(name not in odometry for name in ODOMETRY_METADATA):
        raise ValueError("missing odometry metadata")
    reading = odometry.get("reading")
    if not capabilities["odometry"] and (reading is not None or odometry.get("quality") != "Unknown"):
        raise ValueError("unconnected odometry must be Unknown/null")
    if reading is None:
        if (odometry.get("quality") in ("Valid", "Stale")
                or any(odometry[name] is not None for name in ODOMETRY_METADATA)):
            raise ValueError("missing odometry requires null metadata and unusable quality")
        return
    received = odometry["received_elapsed_ms"]
    if type(received) is not int or not 0 <= received <= frame["elapsed_ms"]:
        raise ValueError("invalid odometry reception time")
    if uint64_bytes(odometry["source_stamp_ns"], "odometry source stamp") == 0:
        raise ValueError("odometry source stamp must be positive")
    for name in ("source_sequence", "source_twist_sequence"):
        if type(odometry[name]) is not int or not 0 <= odometry[name] <= UINT32_MAX:
            raise ValueError("invalid odometry source sequence")
    if odometry["frame_id"] != "odom" or odometry["child_frame_id"] != "base_link":
        raise ValueError("invalid odometry frames")


def next_odometry_sample(previous, frame):
    """Reception time is monotonic; ROS time and publisher counters may restart."""
    odometry = frame["odometry"]
    if frame["source"] != "robot" or odometry["reading"] is None:
        return previous
    if previous is not None:
        received = odometry["received_elapsed_ms"]
        if received < previous["received_elapsed_ms"]:
            raise ValueError("odometry reception time must not regress")
        if received == previous["received_elapsed_ms"] and (
                any(odometry[name] != previous[name] for name in ODOMETRY_METADATA)
                or any(odometry["reading"][name] != previous["reading"][name] for name in ODOMETRY_FIELDS)):
            raise ValueError("same odometry reception time must retain its sample")
    return odometry


def validate_frame(frame, expected_source="mock"):
    if expected_source not in ("mock", "robot"):
        raise ValueError("unknown expected source")
    battery = frame.get("battery") if isinstance(frame, dict) else None
    time_field = "elapsed_ms" if expected_source == "robot" else "sim_time_ms"
    other_time = "sim_time_ms" if expected_source == "robot" else "elapsed_ms"
    if (not isinstance(frame, dict) or frame.get("source") != expected_source
            or type(frame.get("connected")) is not bool
            or frame.get("motion_enabled") is not False
            or type(frame.get(time_field)) is not int
            or not 0 <= frame[time_field] <= MAX_SAFE_INTEGER or other_time in frame
            or not isinstance(battery, dict) or type(battery.get("quality")) is not str
            or battery["quality"] not in QUALITIES
            or "voltage_v" not in battery):
        raise ValueError("invalid {} state fields".format(expected_source))
    if expected_source == "robot":
        if (type(frame.get("sequence")) is not int
                or not 1 <= frame["sequence"] <= MAX_SAFE_INTEGER):
            raise ValueError("invalid robot sequence")
        validate_robot_battery(frame)
        validate_robot_odometry(frame)
        validate_robot_lidar(frame)
        telemetry = frame.get("telemetry")
        if isinstance(telemetry, dict):
            if "lidar_topic" in telemetry and telemetry["lidar_topic"] not in (None, "/scan"):
                raise ValueError("invalid lidar telemetry topic")
            for name in ("odometry_pose_topic", "odometry_twist_topic"):
                if name in telemetry and telemetry[name] is not None and type(telemetry[name]) is not str:
                    raise ValueError("invalid odometry telemetry topic")
    validate_event_log(frame.get("event_log"), frame[time_field])
    voltage = battery["voltage_v"]
    if voltage is not None and not finite_number(voltage):
        raise ValueError("invalid voltage")
    odometry = frame.get("odometry")
    if (not isinstance(odometry, dict) or type(odometry.get("quality")) is not str
            or odometry["quality"] not in QUALITIES or "reading" not in odometry):
        raise ValueError("invalid odometry fields; rebuild the mock producer")
    reading = odometry["reading"]
    if reading is None:
        if odometry["quality"] in ("Valid", "Stale"):
            raise ValueError("usable odometry quality requires a reading")
    elif (not isinstance(reading, dict)
          or any(not finite_number(reading.get(name)) for name in ODOMETRY_FIELDS)):
        raise ValueError("odometry must contain six finite numbers")
    resources = frame.get("system_resources")
    if (not isinstance(resources, dict) or type(resources.get("quality")) is not str
            or resources["quality"] not in QUALITIES or "reading" not in resources):
        raise ValueError("invalid resource fields; rebuild the mock producer")
    reading = resources["reading"]
    if reading is None:
        if resources["quality"] in ("Valid", "Stale"):
            raise ValueError("usable resource quality requires a reading")
        return
    if not isinstance(reading, dict):
        raise ValueError("resource reading must be a complete object")
    cpu = reading.get("cpu_usage_percent")
    if not finite_number(cpu) or not 0 <= cpu <= 100:
        raise ValueError("resource CPU must be finite and within 0..100")
    used = uint64_bytes(reading.get("memory_used_bytes"))
    total = uint64_bytes(reading.get("memory_total_bytes"))
    if total == 0 or used > total:
        raise ValueError("invalid resource memory usage")
    if "cpu_temperature_c" not in reading:
        raise ValueError("resource temperature must be present or null")
    temperature = reading["cpu_temperature_c"]
    if temperature is not None and not finite_number(temperature):
        raise ValueError("invalid resource temperature")


class StateBuffer:
    """One process-local clock and sequence; sensor quality stays owned by C++."""
    def __init__(self, stale_after_s=2.0, clock=time.monotonic, expected_source="mock"):
        if not math.isfinite(stale_after_s) or stale_after_s <= 0:
            raise ValueError("stale threshold must be positive")
        if expected_source not in ("mock", "robot"):
            raise ValueError("unknown expected source")
        self.expected_source = expected_source
        self._lock = threading.Lock()
        self._clock = clock
        self._started = clock()
        self._stale_after = stale_after_s
        self._received = None
        self._state = None
        self._battery_sample = None
        self._odometry_sample = None
        self._lidar_sample = None
        self._sequence = 0
        self._terminal = None
        self._message = None
        self.stream_id = uuid.uuid4().hex

    def publish(self, frame):
        validate_frame(frame, self.expected_source)
        json.dumps(frame, allow_nan=False)
        with self._lock:
            if self._terminal is not None:
                raise ValueError("producer already finished")
            if self._state is not None:
                if frame_time(frame) <= frame_time(self._state):
                    raise ValueError("producer time must increase")
                if (self.expected_source == "robot"
                        and frame["sequence"] <= self._state["sequence"]):
                    raise ValueError("robot sequence must increase")
            battery_sample = next_battery_sample(self._battery_sample, frame)
            odometry_sample = next_odometry_sample(self._odometry_sample, frame)
            lidar_sample = next_lidar_sample(self._lidar_sample, frame)
            self._state = copy.deepcopy(frame)
            self._battery_sample = copy.deepcopy(battery_sample)
            self._odometry_sample = copy.deepcopy(odometry_sample)
            self._lidar_sample = copy.deepcopy(lidar_sample)
            self._sequence += 1
            self._received = self._clock()

    def finish(self, error=None):
        with self._lock:
            self._terminal = "error" if error else "stopped"
            self._message = error or "{} producer stopped".format(self.expected_source)

    def snapshot(self):
        with self._lock:
            now = self._clock()
            age_s = max(0.0, now - (self._received if self._received is not None else self._started))
            status = self._terminal or ("stale" if age_s >= self._stale_after
                                       else "live" if self._state is not None else "starting")
            return {"api_version": API_VERSION, "stream": {
                "id": self.stream_id, "source": self.expected_source,
                "status": status, "sequence": self._sequence,
                "age_ms": int(age_s * 1000) if self._received is not None else None,
                "message": self._message
            }, "state": copy.deepcopy(self._state)}


class StateProducer:
    def __init__(self, command, buffer, env=None):
        self.buffer = buffer
        self._closing = threading.Event()
        self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=None, env=env)
        self._thread = threading.Thread(target=self._read, name="state-reader", daemon=True)
        self._thread.start()

    def _read(self):
        error = None
        try:
            while not self._closing.is_set():
                line = self.process.stdout.readline(MAX_LINE + 1)
                if not line:
                    break
                if len(line) > MAX_LINE or not line.endswith(b"\n"):
                    raise ValueError("oversized or incomplete state line")
                self.buffer.publish(json.loads(line.decode("utf-8")))
        except (ValueError, UnicodeError, TypeError, OverflowError):
            error = "invalid producer output"
        finally:
            if not self._closing.is_set():
                if error and self.process.poll() is None:
                    self.process.terminate()
                try:
                    returncode = self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    returncode = self.process.wait(timeout=2)
                    error = error or "producer closed stdout without exiting"
                if returncode != 0:
                    error = error or "producer exited with code {}".format(returncode)
                self.buffer.finish(error)

    def close(self):
        self._closing.set()
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=2)
        self._thread.join(timeout=2)
        self.process.stdout.close()


# Preserve the existing consumer import while both modes use the same reader.
MockProducer = StateProducer


def make_server(buffer, web_root, port=8081, camera=None):
    camera = camera if camera is not None else CameraBuffer()
    # Exact allowlist: do not serve the repository, credentials, or directory listings.
    assets = {"/": ("index.html", "text/html; charset=utf-8"),
              "/index.html": ("index.html", "text/html; charset=utf-8"),
              "/styles.css": ("styles.css", "text/css; charset=utf-8"),
              "/app.js": ("app.js", "text/javascript; charset=utf-8"),
              "/live.js": ("live.js", "text/javascript; charset=utf-8"),
              "/lidar.js": ("lidar.js", "text/javascript; charset=utf-8"),
              "/camera.js": ("camera.js", "text/javascript; charset=utf-8"),
              "/execution-banner.js": ("execution-banner.js", "text/javascript; charset=utf-8"),
              "/state.ndjson": ("state.ndjson", "application/x-ndjson; charset=utf-8")}

    class Handler(BaseHTTPRequestHandler):
        def _serve(self, head=False):
            path = urlsplit(self.path).path
            if path == "/api/camera":
                body = json.dumps(camera.snapshot(), allow_nan=False, separators=(",", ":")).encode()
                content_type = "application/json; charset=utf-8"
            elif path == "/api/state":
                body = json.dumps(buffer.snapshot(), allow_nan=False, separators=(",", ":")).encode()
                content_type = "application/json; charset=utf-8"
            elif path in assets:
                name, content_type = assets[path]
                try:
                    body = (web_root / name).read_bytes()
                except OSError:
                    self.send_error(404)
                    return
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if not head:
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def do_GET(self):
            self._serve()

        def do_HEAD(self):
            self._serve(head=True)

        def do_POST(self):
            self.send_error(405, "read-only telemetry service")

        def log_message(self, format, *args):
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def default_executable():
    candidates = [ROOT / ".local/build/cmake-debug/mock_robot_state.exe",
                  ROOT / ".local/build/linux-debug/mock_robot_state"]
    return next((path for path in candidates if path.is_file()), candidates[0])


def robot_command(target, jump_host, executable, known_hosts, bind_address=None):
    """Build only the read-only collector command; no remote shell input is accepted."""
    host_pattern = r"(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9_.-]*"
    if (not isinstance(target, str) or re.fullmatch(host_pattern, target) is None
            or not isinstance(jump_host, str) or re.fullmatch(host_pattern, jump_host) is None):
        raise ValueError("target and jump host must be an SSH host or user@host")
    if (not isinstance(executable, str) or not executable.startswith("/")
            or PurePosixPath(executable).name != "robot_state_agent"
            or any(ord(char) < 32 or ord(char) == 127 for char in executable)):
        raise ValueError("robot executable must be an explicit absolute path ending in robot_state_agent")
    known_hosts = Path(known_hosts)
    try:
        available = known_hosts.is_absolute() and known_hosts.is_file()
    except OSError:
        available = False
    if not available:
        raise ValueError("known-hosts must be an explicit existing absolute file")
    # OpenSSH expands a local ProxyCommand. Keep its file argument safe on Windows too.
    if re.search(r'["&|<>^%!\x00-\x1f\x7f]', str(known_hosts)):
        raise ValueError("known-hosts path contains unsupported shell characters")
    # Quote for OpenSSH's option parser as well as the outer process argument parser.
    hosts_option = 'UserKnownHostsFile="' + known_hosts.as_posix() + '"'
    options = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
               "-o", hosts_option,
               "-o", "ConnectTimeout=8", "-o", "ConnectionAttempts=1",
               "-o", "NumberOfPasswordPrompts=0", "-o", "ServerAliveInterval=5",
               "-o", "ServerAliveCountMax=2"]
    # Explicit options also apply to the jump, which -J alone does not guarantee.
    binding = ["-b", str(ipaddress.IPv4Address(bind_address))] if bind_address else []
    proxy = ["ssh", "-T"] + binding + options + ["-W", "%h:%p", jump_host]
    proxy_text = subprocess.list2cmdline(proxy) if os.name == "nt" else " ".join(map(shlex.quote, proxy))
    return (["ssh", "-T"] + options + ["-o", "ProxyCommand=" + proxy_text, target,
            "exec " + shlex.quote(executable)])


def robot_camera_command(target, jump_host, executable, known_hosts, bind_address=None):
    # Deliberately narrower than shell quoting: no arguments, whitespace or expansions.
    if (type(executable) is not str
            or re.fullmatch(r"/(?:[A-Za-z0-9_-]+/)*robot_camera_stream", executable) is None):
        raise ValueError("camera path must be absolute safe components ending in robot_camera_stream")
    if known_hosts is None:
        raise ValueError("camera requires --known-hosts")
    command = robot_command(target, jump_host,
                            str(PurePosixPath(executable).with_name("robot_state_agent")), known_hosts, bind_address)
    command[-1] = "exec " + executable
    return command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8081, help="local HTTP port (default: 8081)")
    parser.add_argument("--mock-executable", type=Path,
                        help="built mock_robot_state executable")
    parser.add_argument("--robot-target", help="explicit robot SSH host or user@host")
    parser.add_argument("--jump-host", default="llmemb-server", help="SSH jump host (default: llmemb-server)")
    parser.add_argument("--robot-executable", help="explicit absolute robot_state_agent path on robot")
    parser.add_argument("--robot-camera", help="opt in: absolute robot_camera_stream executable on robot")
    parser.add_argument("--camera-only", action="store_true", help="explicit camera session; leave all API8 telemetry unknown")
    parser.add_argument("--known-hosts", type=Path, help="explicit absolute PC public known_hosts file")
    parser.add_argument("--ssh-bind-address", help="optional physical IPv4 source for the SSH jump only")
    args = parser.parse_args()
    if args.ssh_bind_address and args.robot_target is None:
        parser.error("--ssh-bind-address requires --robot-target")
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    source = "robot" if args.robot_target is not None else "mock"
    camera_command = None
    if args.camera_only and (source != "robot" or args.robot_camera is None or args.robot_executable is not None):
        parser.error("--camera-only requires --robot-target and --robot-camera, without --robot-executable")
    if args.robot_camera is not None:
        if source != "robot":
            parser.error("--robot-camera requires explicit --robot-target")
        try:
            require_decoder()
            camera_command = robot_camera_command(args.robot_target, args.jump_host,
                                                   args.robot_camera, args.known_hosts, args.ssh_bind_address)
        except (ValueError, TypeError) as error:
            parser.error(str(error))
    if source == "robot":
        if args.mock_executable is not None or (args.robot_executable is None and not args.camera_only) or args.known_hosts is None:
            parser.error("robot mode requires --robot-executable and --known-hosts; do not combine --mock-executable")
        try:
            command = None if args.camera_only else robot_command(args.robot_target, args.jump_host, args.robot_executable, args.known_hosts, args.ssh_bind_address)
        except ValueError as error:
            parser.error(str(error))
    else:
        if args.robot_executable is not None or args.known_hosts is not None or args.jump_host != "llmemb-server":
            parser.error("robot options require --robot-target")
        executable = (args.mock_executable or default_executable()).resolve()
        if not executable.is_file():
            parser.error("mock executable is missing; build C++ first or pass --mock-executable")
        command = [str(executable), "--stream"]
    buffer = StateBuffer(expected_source=source)
    camera = CameraBuffer(enabled=camera_command is not None)
    server = None
    producer = None
    camera_producer = None
    try:
        # Bind first: an occupied port must not leave a producer running.
        server = make_server(buffer, ROOT / "web", args.port, camera=camera)
        env = os.environ.copy()
        runtime = Path("C:/msys64/ucrt64/bin")
        if os.name == "nt" and runtime.is_dir():
            env["PATH"] = str(runtime) + os.pathsep + env.get("PATH", "")
        if command is not None:
            producer = StateProducer(command, buffer, env)
        if camera_command is not None:
            camera_producer = CameraProducer(camera_command, camera, env)
        print("Continuous {}: http://127.0.0.1:{}/?mode=live&source={}".format(source, args.port, source), flush=True)
        print("Mock replay: http://127.0.0.1:{}/  | Ctrl+C stops the server and its {} child".format(args.port, source), flush=True)
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    except OSError as error:
        parser.exit(1, "Cannot start local dashboard: {}\n".format(error))
    finally:
        if camera_producer is not None:
            camera_producer.close()
        if producer is not None:
            producer.close()
        if server is not None:
            server.server_close()


if __name__ == "__main__":
    main()
