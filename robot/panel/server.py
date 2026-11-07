"""Loopback-only local panel. Importing does not start providers or power actions."""
import argparse
import json
import os
from pathlib import Path
import secrets
import signal
import sys
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from providers import Collector
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from input_observer import InputObserver
from system_status import snapshot as system_snapshot
from control.input_service import InputService
from control.telemetry_service import TelemetryService
from keyboard_controller import KeyboardController, KeyboardError
import project_services

class LocalServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


DASHBOARD = {'/dashboard/':('index.html','text/html'), '/dashboard/index.html':('index.html','text/html'),
    '/styles.css':('styles.css','text/css'),'/app.js':('app.js','text/javascript'),
    '/live.js':('live.js','text/javascript'),'/lidar.js':('lidar.js','text/javascript'),'/camera.js':('camera.js','text/javascript')}

for _name in ('styles.css','app.js','live.js','lidar.js','camera.js','execution-banner.js'):
    DASHBOARD['/dashboard/'+_name]=(_name,'text/css' if _name.endswith('.css') else 'text/javascript')

ASSETS = {'/': ('index.html', 'text/html'), '/index.html': ('index.html', 'text/html'), '/app.mjs': ('app.mjs', 'text/javascript'), '/pages.mjs': ('pages.mjs', 'text/javascript'), '/usb-observer.mjs': ('usb-observer.mjs', 'text/javascript'), '/project-services.mjs': ('project-services.mjs', 'text/javascript'), '/control-history.mjs': ('control-history.mjs', 'text/javascript'), '/keyboard-control.mjs': ('keyboard-control.mjs', 'text/javascript'), '/style.css': ('style.css', 'text/css')}


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate field')
        result[key] = value
    return result


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Never log tokens or request bodies.

    def setup(self):
        super(Handler, self).setup()
        self.connection.settimeout(2)

    def trusted(self, post=False):
        origin = 'http://127.0.0.1:' + str(self.server.server_port)
        return (self.client_address[0] == '127.0.0.1' and self.headers.get_all('Host') == [origin[7:]]
                and (not post or self.headers.get_all('Origin') == [origin]))

    def respond(self, code, value, mime='application/json'):
        body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(code)
        for key, val in [('Content-Type', mime+'; charset=utf-8'), ('Content-Length', str(len(body))), ('Cache-Control', 'no-store'), ('X-Content-Type-Options', 'nosniff'), ('X-Frame-Options', 'SAMEORIGIN'), ('Cross-Origin-Resource-Policy', 'same-origin'), ('Content-Security-Policy', "default-src 'self'; connect-src 'self'; img-src 'self' data: blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'self'; form-action 'none'")]:
            self.send_header(key, val)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.trusted():
            self.respond(403, {}); return
        if self.path in ('/api/state','/dashboard/api/state'):
            self.respond(200,self.server.telemetry.snapshot()); return
        if self.path in ('/api/camera','/dashboard/api/camera'):
            self.respond(200,self.server.camera.camera_snapshot()); return
        dashboard_path=self.path.split('?',1)[0]
        if dashboard_path in DASHBOARD:
            name,mime=DASHBOARD[dashboard_path]
            self.respond(200,(self.server.assets.parent/name).read_bytes(),mime); return
        if self.path == '/api/instance':
            self.respond(200, {'source': 'robot_local_panel', 'instance_id': self.server.instance, 'pid': os.getpid()}); return
        if self.path == '/api/session':
            self.respond(200, {'instance_id': self.server.instance, 'csrf_token': self.server.token}); return
        if self.path == '/api/input-observer':
            self.respond(200, self.server.input_observer.read()); return
        if self.path == '/api/input-devices':
            try:
                self.respond(200, self.server.input_service.devices())
            except Exception as exc:
                self.respond(503, {'error': 'input devices unavailable: '+type(exc).__name__})
            return
        if self.path == '/api/services':
            self.respond(200,project_services.snapshot(self.server)); return
        if self.path == '/api/control-events':
            data=self.server.input_service.history.snapshot()
            data['instance_id']=self.server.instance
            self.respond(200,data); return
        if self.path == '/api/keyboard':
            self.respond(200, self.server.keyboard.context(system_snapshot(self.server))); return
        if self.path == '/api/system':
            self.respond(200, system_snapshot(self.server)); return
        if self.path == '/api/panel':
            with self.server.data_lock:
                data = self.server.data
            if data is None:
                self.respond(503, {'error': 'initial sample pending'}); return
            data = dict(data)
            data['csrf_token'] = self.server.token
            # Pending/failed power calls are visible without changing sample time.
            with self.server.collector.power.lock:
                if self.server.collector.power.requested:
                    data['power'] = {'can_power_off': False, 'reason': self.server.collector.power.error or '请求已提交到系统；等待身份验证及安全关机'}
            self.respond(200, data); return
        if self.path not in ASSETS:
            self.respond(404, {}); return
        filename, mime = ASSETS[self.path]
        self.respond(200, (self.server.assets/filename).read_bytes(), mime)

    def do_POST(self):
        if self.path not in ('/api/power-off', '/api/exit', '/api/input-refresh', '/api/keyboard', '/api/services'):
            self.respond(404, {}); return
        supplied = self.headers.get_all('X-Panel-Token') or []
        if (not self.trusted(True) or len(supplied) != 1 or not secrets.compare_digest(supplied[0].encode('utf-8'), self.server.token.encode('ascii'))):
            self.respond(403, {}); return
        try:
            lengths = self.headers.get_all('Content-Length') or []
            if len(lengths) != 1 or not lengths[0].isdigit() or not 0 < int(lengths[0]) <= (512 if self.path == '/api/keyboard' else 128) or self.headers.get_all('Content-Type') != ['application/json'] or self.headers.get('Transfer-Encoding'):
                raise ValueError('body')
            payload = json.loads(self.rfile.read(int(lengths[0])).decode('utf-8'), object_pairs_hook=unique)
            if self.path == '/api/services':
                if type(payload) is not dict or set(payload)!={'service','action'} or payload['service'] not in ('input','telemetry','camera') or payload['action'] not in ('start','stop'):
                    raise ValueError('service/action')
            elif self.path == '/api/keyboard':
                KeyboardController.validate(payload)
            elif self.path == '/api/input-refresh':
                if type(payload) is not dict or set(payload) not in ({'action'}, {'action','device_id'}) or payload.get('action') != 'refresh_input':
                    raise ValueError('refresh action')
                device_id = payload.get('device_id')
                if device_id is not None and (type(device_id) is not str or not re.fullmatch(r'[0-9a-f]{32}',device_id)):
                    raise ValueError('device id')
            else:
                expected = 'exit_panel' if self.path == '/api/exit' else 'power_off'
                if type(payload) is not dict or payload != {'action': expected}:
                    raise ValueError('action')
        except (ValueError, OSError):
            self.respond(400, {}); return
        if self.path == '/api/services':
            try:
                self.respond(200,project_services.perform(self.server,payload))
            except project_services.ServiceError as exc:
                self.respond(exc.code,{'error':exc.message})
            except (OSError,RuntimeError,ValueError) as exc:
                self.respond(503,{'error':'项目服务操作失败：'+type(exc).__name__})
            return
        if self.path == '/api/keyboard':
            try:
                code,value=self.server.keyboard.perform(payload,system_snapshot(self.server))
                self.respond(code,value)
            except KeyboardError as exc:
                self.respond(exc.code,{'error':exc.message})
            return
        if self.path == '/api/input-refresh':
            if not self.server.input_refresh_lock.acquire(False):
                self.respond(409, {'error': 'input refresh already in progress'}); return
            try:
                self.respond(200, self.server.input_service.refresh(device_id))
            except Exception as exc:
                self.respond(503, {'error': 'input refresh failed: '+type(exc).__name__})
            finally:
                self.server.input_refresh_lock.release()
            return
        if self.path == '/api/exit':
            self.respond(202, {'status': 'panel_exit_requested'})
            self.server.request_exit()
            return
        with self.server.data_lock:
            allowed = self.server.data is not None and time.monotonic()-self.server.sample_received < 3 and self.server.data['power']['can_power_off']
        if not allowed or not self.server.collector.power.submit():
            self.respond(409, {'error': 'power request unavailable or already submitted'}); return
        self.respond(202, {'status': 'submitted_to_system', 'reason': '等待系统身份验证及安全关机；不代表已关闭'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--state-file', type=Path)
    parser.add_argument('--instance-id', default=secrets.token_hex(16))
    parser.add_argument('--gamepad-info')
    parser.add_argument('--input-runtime')
    parser.add_argument('--input-runtime-sha256')
    parser.add_argument('--probe-ros', action='store_true')
    parser.add_argument('--sources-config',type=Path)
    parser.add_argument('--execution-profile',type=Path)
    args = parser.parse_args()
    if bool(args.input_runtime) != bool(args.input_runtime_sha256) or (args.input_runtime_sha256 and not re.fullmatch(r'[0-9a-fA-F]{64}',args.input_runtime_sha256)):
        parser.error('input-runtime and its SHA256 must be supplied together')
    if not 0 <= args.port <= 65535 or not args.instance_id.isalnum() or len(args.instance_id) > 64:
        parser.error('invalid port or instance id')
    assets = Path(__file__).resolve().parents[2]/'web/robot-panel'
    if not all((assets/name).is_file() for name, _ in ASSETS.values()):
        parser.error('panel assets missing')
    sources={}
    if args.sources_config:
        if args.sources_config.is_symlink() or args.sources_config.stat().st_size>4096 or args.sources_config.stat().st_uid!=os.getuid() or args.sources_config.stat().st_mode&0o077:
            parser.error('sources-config must be private project JSON')
        sources=json.loads(args.sources_config.read_text())
        if type(sources) is not dict or set(sources)-{'state','camera'}:
            parser.error('unsupported collector configuration')
    telemetry=TelemetryService({'state':sources['state']} if 'state' in sources else None)
    camera=TelemetryService({'camera':sources['camera']} if 'camera' in sources else None)
    collector = Collector(args.gamepad_info, args.probe_ros, telemetry=telemetry)
    stopped = threading.Event()
    with LocalServer(('127.0.0.1', args.port), Handler) as server:
        server.assets, server.instance, server.collector = assets, args.instance_id, collector
        server.token, server.data, server.sample_received = secrets.token_urlsafe(32), None, 0
        server.data_lock = threading.Lock()
        server.os_boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        server.input_observer = InputObserver()
        server.input_service = InputService(args.gamepad_info, {'path':args.input_runtime,'sha256':args.input_runtime_sha256.lower()} if args.input_runtime else None,args.execution_profile)
        collector.input_service=server.input_service
        server.telemetry,server.camera=telemetry,camera
        server.keyboard = KeyboardController(server.input_service)
        server.input_refresh_lock = threading.Lock()

        def collect():
            while not stopped.is_set():
                started = time.monotonic()
                try:
                    data = collector.sample()
                    with server.data_lock:
                        server.data, server.sample_received = data, time.monotonic()
                except Exception:
                    pass  # Old sample clock does not advance; browser expires it.
                stopped.wait(max(0, 1-(time.monotonic()-started)))

        worker = threading.Thread(target=collect, daemon=True)
        server.collector_thread = worker
        worker.start()
        if args.state_file:
            path = args.state_file
            if path.is_symlink() or not path.parent.is_dir() or path.parent.stat().st_uid != os.getuid() or path.parent.stat().st_mode & 0o077:
                parser.error('state-file requires a private current-user directory')
            temporary = path.with_name(path.name+'.'+args.instance_id+'.tmp')
            descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w') as stream:
                json.dump({'pid': os.getpid(), 'port': server.server_port, 'instance_id': server.instance, 'uid': os.getuid()}, stream)
            os.replace(str(temporary), str(path))
        def shutdown(signum, frame):
            stopped.set()
            threading.Thread(target=server.shutdown, daemon=True).start()
        server.request_exit = lambda: shutdown(None, None)
        signal.signal(signal.SIGTERM, shutdown)
        signal.signal(signal.SIGINT, shutdown)
        print('http://127.0.0.1:{}/'.format(server.server_port), flush=True)
        try:
            server.serve_forever(poll_interval=.2)
        finally:
            stopped.set()
            try:
                server.input_service.close()
            except Exception as exc:
                print('input service cleanup failed: '+type(exc).__name__, file=sys.stderr)
            server.telemetry.close()
            server.camera.close()
            worker.join(timeout=3)
            if args.state_file:
                try:
                    current = json.loads(args.state_file.read_text())
                    if current.get('instance_id') == server.instance:
                        args.state_file.unlink()
                except (OSError, ValueError):
                    pass


if __name__ == '__main__':
    main()
