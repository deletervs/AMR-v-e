"""Local, read-only bridge from the C++ mock producer to the dashboard (Python 3.8+)."""
import argparse
import copy
import functools
import json
import math
import os
from pathlib import Path
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[1]
QUALITIES = {"Unknown", "Valid", "Stale", "Fault", "Unsupported"}
MAX_LINE = 65536


def validate_frame(frame):
    battery = frame.get("battery") if isinstance(frame, dict) else None
    if (not isinstance(frame, dict) or frame.get("source") != "mock"
            or type(frame.get("connected")) is not bool
            or frame.get("motion_enabled") is not False
            or type(frame.get("sim_time_ms")) is not int or frame["sim_time_ms"] < 0
            or not isinstance(battery, dict) or type(battery.get("quality")) is not str
            or battery["quality"] not in QUALITIES
            or "voltage_v" not in battery):
        raise ValueError("invalid mock state fields")
    voltage = battery["voltage_v"]
    if voltage is not None and (type(voltage) not in (int, float) or not math.isfinite(voltage)):
        raise ValueError("invalid voltage")


class StateBuffer:
    """One process-local clock and sequence; sensor quality stays owned by C++."""
    def __init__(self, stale_after_s=2.0, clock=time.monotonic):
        if not math.isfinite(stale_after_s) or stale_after_s <= 0:
            raise ValueError("stale threshold must be positive")
        self._lock = threading.Lock()
        self._clock = clock
        self._started = clock()
        self._stale_after = stale_after_s
        self._received = None
        self._state = None
        self._sequence = 0
        self._terminal = None
        self._message = None
        self.stream_id = uuid.uuid4().hex

    def publish(self, frame):
        validate_frame(frame)
        json.dumps(frame, allow_nan=False)
        with self._lock:
            if self._terminal is not None:
                raise ValueError("producer already finished")
            if self._state is not None and frame["sim_time_ms"] <= self._state["sim_time_ms"]:
                raise ValueError("mock time must increase")
            self._state = copy.deepcopy(frame)
            self._sequence += 1
            self._received = self._clock()

    def finish(self, error=None):
        with self._lock:
            self._terminal = "error" if error else "stopped"
            self._message = error or "mock producer stopped"

    def snapshot(self):
        with self._lock:
            now = self._clock()
            age_s = max(0.0, now - (self._received if self._received is not None else self._started))
            status = self._terminal or ("stale" if age_s >= self._stale_after
                                       else "live" if self._state is not None else "starting")
            return {"api_version": 1, "stream": {
                "id": self.stream_id, "status": status, "sequence": self._sequence,
                "age_ms": int(age_s * 1000) if self._received is not None else None,
                "message": self._message
            }, "state": copy.deepcopy(self._state)}


class MockProducer:
    def __init__(self, command, buffer, env=None):
        self.buffer = buffer
        self._closing = threading.Event()
        self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=None, env=env)
        self._thread = threading.Thread(target=self._read, name="mock-state-reader", daemon=True)
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
                self.buffer.finish(error)
                if self.process.poll() is None:
                    self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)

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


def make_server(buffer, web_root, port=8081):
    # Exact allowlist: do not serve the repository, credentials, or directory listings.
    assets = {"/": ("index.html", "text/html; charset=utf-8"),
              "/index.html": ("index.html", "text/html; charset=utf-8"),
              "/styles.css": ("styles.css", "text/css; charset=utf-8"),
              "/app.js": ("app.js", "text/javascript; charset=utf-8"),
              "/live.js": ("live.js", "text/javascript; charset=utf-8"),
              "/state.ndjson": ("state.ndjson", "application/x-ndjson; charset=utf-8")}

    class Handler(BaseHTTPRequestHandler):
        def _serve(self, head=False):
            path = urlsplit(self.path).path
            if path == "/api/state":
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
            self.send_error(405, "read-only mock service")

        def log_message(self, format, *args):
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def default_executable():
    candidates = [ROOT / ".local/build/cmake-debug/mock_robot_state.exe",
                  ROOT / ".local/build/linux-debug/mock_robot_state"]
    return next((path for path in candidates if path.is_file()), candidates[0])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8081, help="local HTTP port (default: 8081)")
    parser.add_argument("--mock-executable", type=Path, default=default_executable(),
                        help="built mock_robot_state executable")
    args = parser.parse_args()
    executable = args.mock_executable.resolve()
    if not executable.is_file():
        parser.error("mock executable is missing; build C++ first or pass --mock-executable")
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    buffer = StateBuffer()
    server = None
    producer = None
    try:
        # Bind first: an occupied port must not leave a producer running.
        server = make_server(buffer, ROOT / "web", args.port)
        env = os.environ.copy()
        runtime = Path("C:/msys64/ucrt64/bin")
        if os.name == "nt" and runtime.is_dir():
            env["PATH"] = str(runtime) + os.pathsep + env.get("PATH", "")
        producer = MockProducer([str(executable), "--stream"], buffer, env)
        print("Continuous mock: http://127.0.0.1:{}/?mode=live".format(args.port), flush=True)
        print("Replay: http://127.0.0.1:{}/  | Ctrl+C stops the server and its mock child".format(args.port), flush=True)
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    except OSError as error:
        parser.exit(1, "Cannot start local dashboard: {}\n".format(error))
    finally:
        if producer is not None:
            producer.close()
        if server is not None:
            server.server_close()


if __name__ == "__main__":
    main()
