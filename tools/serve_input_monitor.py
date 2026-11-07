"""Loopback-only, fixed-file input monitor. No robot connections."""
import argparse
import json
import math
import queue
import secrets
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = {
    '/': ('web/input-monitor/index.html', 'text/html; charset=utf-8'),
    '/index.html': ('web/input-monitor/index.html', 'text/html; charset=utf-8'),
    '/app.mjs': ('web/input-monitor/app.mjs', 'text/javascript; charset=utf-8'),
    '/style.css': ('web/input-monitor/style.css', 'text/css; charset=utf-8'),
    '/core.mjs': ('web/teleop/core.mjs', 'text/javascript; charset=utf-8'),
}


class Preview:
    def __init__(self, path):
        self.lock = threading.Lock()
        self.sequence = 0
        self.process = subprocess.Popen([str(path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) if path else None

    def close(self):
        if self.process:
            self.process.kill() if self.process.poll() is None else None
            self.process.wait(timeout=2)
            self.process.stdin.close()
            self.process.stdout.close()
            self.process = None

    def calculate(self, payload):
        with self.lock:
            if not self.process or self.process.poll() is not None:
                raise RuntimeError('C++ preview unavailable')
            self.sequence += 1
            if self.sequence > 18446744073709551615:
                self.close()
                raise RuntimeError('request id exhausted')
            fields = ['deadman', 'forward', 'backward', 'left', 'right'] if payload['source'] == 'keys' else ['deadman', 'axis_x', 'axis_y']
            line = f"{payload['source']} {self.sequence} " + ' '.join(str(int(payload[k])) if type(payload[k]) is bool else str(payload[k]) for k in fields) + '\n'
            result_queue = queue.Queue(maxsize=1)
            process = self.process

            def exchange():
                try:
                    process.stdin.write(line.encode('ascii'))
                    process.stdin.flush()
                    result_queue.put(process.stdout.readline(4097))
                except (OSError, ValueError) as exc:
                    result_queue.put(exc)

            worker = threading.Thread(target=exchange, daemon=True)
            worker.start()
            try:
                raw = result_queue.get(timeout=0.5)
                if not isinstance(raw, bytes) or len(raw) > 4096 or not raw.endswith(b'\n'):
                    raise ValueError('invalid native response')
                result = json.loads(raw)
                if (set(result) != {'source', 'request_id', 'result', 'linear_mps', 'angular_radps', 'robot_connected', 'motion_enabled'}
                        or result['source'] != 'cpp_input_preview' or result['request_id'] != str(self.sequence)
                        or result['result'] not in ('Mapped', 'InvalidInput', 'InvalidLimits', 'InvalidAxis', 'InvalidDeadzone')
                        or result['robot_connected'] is not False or result['motion_enabled'] is not False
                        or any(type(result[k]) not in (int, float) or not math.isfinite(result[k]) for k in ('linear_mps', 'angular_radps'))
                        or abs(result['linear_mps']) > 0.25 or abs(result['angular_radps']) > 0.6):
                    raise ValueError('invalid native response')
                return result
            except Exception as exc:
                self.close()
                worker.join(timeout=1)
                raise RuntimeError('C++ preview failed; restart service to recover') from exc


def validate(payload):
    if type(payload) is not dict or payload.get('source') not in ('keys', 'pad'):
        raise ValueError('invalid source')
    flags = ['deadman', 'forward', 'backward', 'left', 'right'] if payload['source'] == 'keys' else ['deadman']
    axes = [] if payload['source'] == 'keys' else ['axis_x', 'axis_y']
    if set(payload) != {'source', *flags, *axes} or any(type(payload[k]) is not bool for k in flags):
        raise ValueError('invalid fields')
    if any(type(payload[k]) not in (int, float) or not math.isfinite(payload[k]) or abs(payload[k]) > 1 for k in axes):
        raise ValueError('invalid axes')
    return payload


class Handler(BaseHTTPRequestHandler):
    def trusted(self, write=False):
        origin = self.headers.get('Origin')
        expected = f'http://127.0.0.1:{self.server.server_port}'
        return (self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'
                and (origin == expected if write else origin in (None, expected))
                and (not write or secrets.compare_digest(self.headers.get('X-Preview-Token', ''), self.server.token)))

    def respond(self, status, content, mime='application/json; charset=utf-8', head=False):
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        if not head:
            self.wfile.write(content)

    def do_GET(self):
        self.serve(False)

    def do_HEAD(self):
        self.serve(True)

    def serve(self, head):
        if not self.trusted():
            self.respond(403, b'{}', head=head)
            return
        if self.path == '/api/capabilities':
            self.respond(200, json.dumps({'available': bool(self.server.preview.process and self.server.preview.process.poll() is None), 'token': self.server.token}).encode(), head=head)
            return
        item = FILES.get(self.path)
        if item is None:
            self.send_error(404)
            return
        content = (ROOT / item[0]).read_bytes()
        self.respond(200, content, item[1], head)

    def do_POST(self):
        if not self.trusted(True):
            self.respond(403, b'{}')
            return
        if self.path != '/api/preview':
            self.respond(404, b'{}')
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if (not 0 < length <= 512 or self.headers.get('Content-Type') != 'application/json'
                    or self.headers.get('Transfer-Encoding')):
                raise ValueError('invalid body')
            self.connection.settimeout(1)
            def unique(pairs):
                obj = {}
                for k, v in pairs:
                    if k in obj:
                        raise ValueError('duplicate field')
                    obj[k] = v
                return obj
            payload = validate(json.loads(self.rfile.read(length), object_pairs_hook=unique))
        except (ValueError, OSError):
            self.respond(400, b'{}')
            return
        try:
            result = self.server.preview.calculate(payload)
            self.respond(200, json.dumps(result).encode())
        except RuntimeError:
            self.respond(503, b'{"error":"C++ preview unavailable"}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8767)
    parser.add_argument('--cpp-preview', type=Path)
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('port must be 0..65535')
    if args.cpp_preview:
        args.cpp_preview = args.cpp_preview.resolve()
        if not args.cpp_preview.is_file() or args.cpp_preview.name.lower() not in ('input_preview', 'input_preview.exe'):
            parser.error('--cpp-preview must name an existing input_preview executable')
    with HTTPServer(('127.0.0.1', args.port), Handler) as server:
        server.preview = Preview(args.cpp_preview)
        server.token = secrets.token_urlsafe(32)
        print(f'http://127.0.0.1:{server.server_port}/', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.preview.close()


if __name__ == '__main__':
    main()
