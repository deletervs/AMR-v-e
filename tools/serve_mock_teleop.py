"""M3 loopback-only offline control demo. No ROS, SSH, network or robot adapter.

Run: python tools/serve_mock_teleop.py [--port 8766] [--agent PATH]
The child is the C++ mock_teleop line protocol, never a shell command.
"""
from __future__ import annotations

import argparse
import copy
import hmac
import json
import math
from pathlib import Path
import queue
import re
import secrets
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "web" / "teleop"
MAX_BODY = 4096
MAX_FRAME = 65536
UINT64 = 2**64 - 1
SAFE_INT = 2**53 - 1


def decimal_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", value) or int(value) > UINT64:
        raise ValueError("IDs and sequence must be positive uint64 decimal strings")
    return value


def timestamp(value):
    if type(value) is not int or not 0 <= value <= SAFE_INT:
        raise ValueError("timestamp must be a nonnegative safe integer")
    return str(value)


def flag(value):
    if type(value) is not bool:
        raise ValueError("flags must be boolean")
    return "1" if value else "0"


def axis(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not -1 <= value <= 1:
        raise ValueError("axes must be finite numbers in [-1,1]")
    return str(value)


def protocol_line(body):
    """Validate the HTTP envelope; preserve caller IDs, sequence and clock exactly."""
    if not isinstance(body, dict):
        raise ValueError("JSON object required")
    action = body.get("action")
    fields = {
        "arm": {"controller_id", "source"},
        "release": {"controller_id", "boot_id", "session_id"},
        "stop": set(), "clear": set(),
        "keys": {"boot_id", "session_id", "controller_id", "sequence", "issued_ms", "valid_until_ms", "deadman", "forward", "backward", "left", "right"},
        "pad": {"boot_id", "session_id", "controller_id", "sequence", "issued_ms", "valid_until_ms", "deadman", "axis_x", "axis_y"},
    }
    if not isinstance(action, str) or action not in fields or set(body) != fields[action] | {"action"}:
        raise ValueError("unknown action, missing or extra fields")
    if action in ("stop", "clear"):
        return action
    if action == "arm":
        if body["source"] not in ("keyboard", "gamepad"):
            raise ValueError("unknown input source")
        return f"arm {decimal_id(body['controller_id'])} {body['source']}"
    if action == "release":
        return "release " + " ".join(decimal_id(body[k]) for k in ("controller_id", "boot_id", "session_id"))
    parts = [action] + [decimal_id(body[k]) for k in ("boot_id", "session_id", "controller_id", "sequence")]
    parts += [timestamp(body[k]) for k in ("issued_ms", "valid_until_ms")]
    parts += [flag(body["deadman"])]
    parts += ([flag(body[k]) for k in ("forward", "backward", "left", "right")] if action == "keys" else [axis(body[k]) for k in ("axis_x", "axis_y")])
    return " ".join(parts)


def zero_state(reason="agent_unavailable"):
    return dict(source="mock_teleop", elapsed_ms=0, boot_id="0", session_id="0", controller_id="0", sequence="0", owner="none", armed=False, stop_latched=False, neutral_confirmed=False, link_available=False, linear_mps=0, angular_radps=0, stop_reason=reason, result=reason, lease_remaining_ms=0, command_remaining_ms=0, limits=dict(linear_mps=0.25, angular_radps=0.6, command_timeout_ms=250, lease_ms=10000))


class AgentBridge:
    """One child, one response per line; the lock covers the whole transaction."""
    def __init__(self, command, response_timeout=0.75):
        self.lock = threading.Lock()
        self.closed = threading.Event()
        self.timeout = response_timeout
        self.frames = queue.Queue(maxsize=4)
        self.state = zero_state()
        self.error = None
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        self.reader = threading.Thread(target=self._read, name="mock-stdout", daemon=True)
        self.reader.start()
        self.ticker = threading.Thread(target=self._tick, name="mock-tick", daemon=True)
        try:
            self.exchange("tick")
        except RuntimeError:
            self.closed.set()
            self.process.wait(timeout=2)
            self.process.stdin.close()
            self.process.stdout.close()
            self.reader.join(timeout=1)
            raise
        self.ticker.start()

    def _read(self):
        try:
            while not self.closed.is_set():
                line = self.process.stdout.readline(MAX_FRAME + 1)
                if not line or len(line) > MAX_FRAME or not line.endswith(b"\n"):
                    self.frames.put_nowait(None)
                    return
                self.frames.put_nowait(line)
        except (OSError, ValueError, queue.Full):
            return

    def _fail(self, reason):
        self.error = reason
        self.state = zero_state(reason)
        if self.process.poll() is None:
            self.process.kill()

    def snapshot(self):
        with self.lock:
            return dict(offline=True, transport_available=self.error is None and not self.closed.is_set(), state=copy.deepcopy(self.state))

    def exchange(self, line):
        with self.lock:
            if self.error or self.closed.is_set():
                raise RuntimeError(self.error or "service_closed")
            try:
                self.process.stdin.write((line + "\n").encode("ascii"))
                self.process.stdin.flush()
                raw = self.frames.get(timeout=self.timeout)
                if raw is None:
                    raise ValueError("agent EOF or oversized frame")
                state = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
                required = zero_state()
                if not isinstance(state, dict) or not required.keys() <= state.keys() or state["source"] != "mock_teleop":
                    raise ValueError("invalid agent state")
                for key in ("boot_id", "session_id", "controller_id", "sequence"):
                    if not isinstance(state[key], str) or not re.fullmatch(r"0|[1-9][0-9]{0,19}", state[key]) or int(state[key]) > UINT64:
                        raise ValueError("invalid agent ID")
                for key in ("elapsed_ms", "lease_remaining_ms", "command_remaining_ms"):
                    timestamp(state[key])
                for key in ("linear_mps", "angular_radps"):
                    if type(state[key]) not in (int, float) or not math.isfinite(state[key]):
                        raise ValueError("invalid agent velocity")
                for key in ("armed", "stop_latched", "neutral_confirmed", "link_available"):
                    if type(state[key]) is not bool:
                        raise ValueError("invalid agent flag")
                if state["owner"] not in ("none", "keyboard", "gamepad"):
                    raise ValueError("invalid owner")
                self.state = state
                return dict(offline=True, transport_available=True, state=copy.deepcopy(state))
            except (OSError, ValueError, TypeError, KeyError, queue.Empty) as exc:
                self._fail("agent_unavailable")
                raise RuntimeError("agent_unavailable") from exc

    def _tick(self):
        while not self.closed.wait(0.05):
            try:
                self.exchange("tick")
            except RuntimeError:
                return

    def close(self):
        if self.closed.is_set():
            return
        try:
            if not self.error:
                self.exchange("stop")
                self.exchange("quit")
        except RuntimeError:
            pass
        self.closed.set()
        with self.lock:
            if self.process.poll() is None:
                try:
                    self.process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
            self.process.wait(timeout=2)
            self.state = zero_state("service_closed")
            for pipe in (self.process.stdin, self.process.stdout):
                pipe.close()
        self.reader.join(timeout=1)
        self.ticker.join(timeout=1)


class TeleopServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, port, bridge):
        self.bridge = bridge
        self.csrf = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), Handler)
        self.origin = f"http://127.0.0.1:{self.server_port}"


class Handler(BaseHTTPRequestHandler):
    server_version = "RSOffline/1"

    def log_message(self, *_):
        pass  # Input and tokens never enter request logs.

    def reply(self, code, value, mime="application/json; charset=utf-8"):
        # Closing a socket with an unread small POST body can reset the peer
        # on Windows before it receives the rejection. Drain only an
        # unambiguous, bounded body, with a short timeout; never drain a large
        # or conflicting-length request. HTTP/1.0 closes every connection.
        if self.command == "POST" and not getattr(self, "_body_consumed", False):
            sizes = self.headers.get_all("Content-Length", [])
            if sizes and len(set(sizes)) == 1 and re.fullmatch(r"[0-9]{1,5}", sizes[0]) and int(sizes[0]) <= MAX_BODY + 1:
                try:
                    self.connection.settimeout(0.2)
                    self.rfile.read(int(sizes[0]))
                except OSError:
                    pass
            self._body_consumed = True
        data = value if isinstance(value, bytes) else json.dumps(value, allow_nan=False).encode("utf-8")
        self.send_response(code)
        for key, val in {
            "Content-Type": mime, "Content-Length": str(len(data)), "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer", "Cross-Origin-Resource-Policy": "same-origin",
            "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        }.items():
            self.send_header(key, val)
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def trusted(self, post=False):
        if self.headers.get_all("Host", []) != [f"127.0.0.1:{self.server.server_port}"]:
            self.reply(403, {"error": "invalid Host"})
            return False
        origins = self.headers.get_all("Origin", [])
        if (post and origins != [self.server.origin]) or (origins and origins != [self.server.origin]):
            self.reply(403, {"error": "invalid Origin"})
            return False
        if self.headers.get("Sec-Fetch-Site") not in (None, "none", "same-origin"):
            self.reply(403, {"error": "cross-site request"})
            return False
        return True

    def do_GET(self):
        if not self.trusted():
            return
        if self.path in ("/api/session", "/api/state"):
            payload = self.server.bridge.snapshot()
            if self.path == "/api/session":
                payload["csrf"] = self.server.csrf
            self.reply(200, payload)
            return
        paths = {"/": ("index.html", "text/html"), "/index.html": ("index.html", "text/html"), "/styles.css": ("styles.css", "text/css"), "/app.js": ("app.js", "text/javascript"), "/core.mjs": ("core.mjs", "text/javascript")}
        if self.path not in paths:
            self.reply(404, {"error": "not found"})
            return
        name, mime = paths[self.path]
        self.reply(200, (ASSETS / name).read_bytes(), mime + "; charset=utf-8")

    def do_POST(self):
        if not self.trusted(post=True):
            return
        if self.path != "/api/command":
            self.reply(404, {"error": "not found"})
            return
        if self.headers.get_all("Content-Type", []) != ["application/json"] or self.headers.get("Transfer-Encoding"):
            self.reply(415, {"error": "application/json with Content-Length required"})
            return
        tokens = self.headers.get_all("X-CSRF-Token", [])
        if len(tokens) != 1 or not tokens[0].isascii() or not hmac.compare_digest(tokens[0], self.server.csrf):
            self.reply(403, {"error": "invalid CSRF token"})
            return
        sizes = self.headers.get_all("Content-Length", [])
        if len(sizes) != 1 or not re.fullmatch(r"[0-9]{1,5}", sizes[0]) or not 0 < int(sizes[0]) <= MAX_BODY:
            self.reply(413, {"error": "body size limit"})
            return
        body = None
        try:
            self.connection.settimeout(1)
            self._body_consumed = True
            raw = self.rfile.read(int(sizes[0]))
            if len(raw) != int(sizes[0]):
                raise ValueError("truncated JSON")
            def unique(pairs):
                result = {}
                for k, v in pairs:
                    if k in result:
                        raise ValueError("duplicate JSON field")
                    result[k] = v
                return result
            body = json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
            line = protocol_line(body)
        except (ValueError, UnicodeError, OSError, RecursionError):
            # An invalid speed from the current owner must revoke its session.
            # Binding all three IDs prevents malformed outsiders releasing it.
            state = self.server.bridge.snapshot()["state"]
            if isinstance(body, dict) and body.get("action") in ("keys", "pad") and state["armed"] and all(body.get(k) == state[k] for k in ("controller_id", "boot_id", "session_id")):
                try:
                    self.server.bridge.exchange(protocol_line({"action": "release", **{k: state[k] for k in ("controller_id", "boot_id", "session_id")}}))
                except RuntimeError:
                    pass
            self.reply(400, {"error": "invalid command JSON"})
            return
        try:
            self.reply(200, self.server.bridge.exchange(line))
        except RuntimeError:
            self.reply(503, self.server.bridge.snapshot())

    def do_OPTIONS(self):
        self.reply(405, {"error": "method not allowed"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--agent", type=Path, default=ROOT / ".local/build/cmake-debug/mock_teleop.exe")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("port must be 0..65535")
    if not args.agent.is_file():
        parser.error(f"C++ mock controller not found: {args.agent}")
    bridge = AgentBridge([str(args.agent.resolve())])
    try:
        server = TeleopServer(args.port, bridge)
        print(json.dumps(dict(url=server.origin, offline=True, agent_pid=bridge.process.pid)), flush=True)
        try:
            server.serve_forever(poll_interval=0.1)
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    finally:
        bridge.close()


if __name__ == "__main__":
    main()
