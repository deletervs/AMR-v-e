"""Explicit one-shot SSH policy console. No actuator integration or auto reconnect."""
import argparse
import ipaddress
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path, PurePosixPath
import queue
import re
import secrets
import subprocess
import threading
import time
from serve_dashboard import robot_command

ROOT = Path(__file__).resolve().parents[1]
FILES = {'/': ('index.html', 'text/html'), '/app.mjs': ('app.mjs', 'text/javascript'), '/style.css': ('style.css', 'text/css')}
MAX = 18446744073709551615


def uid(v):
    return type(v) is str and re.fullmatch(r'0|[1-9][0-9]{0,19}', v) is not None and int(v) <= MAX


def number(v):
    return type(v) in (int, float) and math.isfinite(v)


def unique(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError('duplicate')
        out[k] = v
    return out


def validate(s):
    ids = ('request_id', 'boot_id', 'session_id', 'controller_id', 'command_sequence')
    ints = ('elapsed_ms', 'state_sequence', 'lease_remaining_ms', 'command_remaining_ms')
    flags = ('actuator_attached', 'motion_enabled', 'guard_armed', 'stop_latched', 'neutral_confirmed', 'transport_available')
    fields = {'source', 'api_version', 'owner', 'candidate', 'stop_reason', 'result', 'limits', *ids, *ints, *flags}
    if type(s) is not dict or set(s) != fields:
        raise ValueError('state fields')
    if s['source'] != 'robot_control_gateway' or type(s['api_version']) is not int or s['api_version'] != 2:
        raise ValueError('API')
    if any(not uid(s[k]) for k in ids) or any(type(s[k]) is not int or not 0 <= s[k] <= 9007199254740991 for k in ints):
        raise ValueError('IDs/time')
    if any(type(s[k]) is not bool for k in flags) or s['actuator_attached'] or s['motion_enabled']:
        raise ValueError('actuator forbidden')
    if s['owner'] not in ('none', 'keyboard', 'gamepad') or s['stop_reason'] not in ('Disarmed','WaitingForNeutral','DeadmanReleased','None','CommandExpired','LeaseExpired','SoftwareStop','LinkUnavailable','InvalidCommand','ClockRegression','InvalidConfig'):
        raise ValueError('policy enum')
    if s['result'] not in ('Started','Status','Accepted','Armed','Released','Stopped','Cleared','LinkUpdated','Busy','StopLatched','LinkUnavailable','InvalidConfig','InvalidSource','InvalidSession','InvalidSequence','InvalidWindow','InvalidVelocity','NeutralRequired','ClockRegression','NotArmed','InvalidInput','InputClosed','Interrupted','RuntimeExpired'):
        raise ValueError('result')
    c = s['candidate']
    if type(c) is not dict or set(c) != {'linear_mps', 'angular_radps'} or any(not number(v) for v in c.values()) or abs(c['linear_mps']) > .25 or abs(c['angular_radps']) > .6:
        raise ValueError('candidate')
    limits = s['limits']
    if type(limits) is not dict or limits != {'candidate_linear_mps': .25, 'candidate_angular_radps': .6, 'command_timeout_ms': 250, 'lease_ms': 10000} or any(not number(v) for v in limits.values()):
        raise ValueError('limits')
    return s


class Gateway:
    def __init__(self, command):
        self.lock = threading.RLock()
        self.transaction = threading.Lock()
        self.state = None
        self.received = 0
        self.progress = 0
        self.started = time.monotonic()
        self.closed = False
        self.error = '等待网关初始化'
        self.observed = deque(maxlen=32)
        self.request = 0
        self.pending = None
        self.acks = queue.Queue(maxsize=1)
        self.anchor = None
        self.handshake_sent = None
        self.handshake_started = False
        self.last_metadata = None
        self.failure_detail = None
        self.diagnostics = deque(maxlen=8)
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        threading.Thread(target=self.read_errors, daemon=True).start()
        threading.Thread(target=self.read, daemon=True).start()
        threading.Thread(target=self.watch, daemon=True).start()

    def read_errors(self):
        while True:
            line=self.process.stderr.readline(1024)
            if not line:
                return
            self.diagnostics.append(line.decode('utf-8',errors='replace').strip())

    def close(self, reason='连接已关闭'):
        with self.lock:
            if self.closed:
                return
            self.failure_detail = {'source_age_upper_ms':self.source_age(), 'receive_progress_age_ms':(time.monotonic()-(self.progress or self.started))*1000,'process_returncode_before_close':self.process.poll()}
            self.closed = True
            self.error = reason
            self.state = None
            self.observed.clear()
            if self.process.poll() is None:
                self.process.terminate()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=1)
            try:
                self.process.stdin.close()
            except OSError:
                pass

    def watch(self):
        while not self.closed:
            time.sleep(.05)
            with self.lock:
                age = time.monotonic() - (self.progress or self.started)
                # State validity remains 250ms; temporary stale data is not a dead SSH.
                # Keep reading the same stream so only source-clock catch-up can recover.
                expired = age > (2 if self.received else 30)
            if expired:
                self.close('网关接收进度超时；需手动重启服务')

    def source_age(self):
        if not self.anchor or not self.state:
            return 1000000
        sent, elapsed = self.anchor
        return max(0, (time.monotonic()-sent)*1000-(self.state['elapsed_ms']-elapsed))

    def handshake(self):
        with self.transaction:
            try:
                with self.lock:
                    self.request += 1
                    self.pending = str(self.request)
                    self.handshake_sent = time.monotonic()
                    self.process.stdin.write(f'request {self.request} status\n'.encode())
                    self.process.stdin.flush()
                self.acks.get(timeout=.25)
            except Exception:
                self.close('只读时钟握手失败；需手动重启')

    def read(self):
        try:
            while not self.closed:
                raw = self.process.stdout.readline(8193)
                if not raw or len(raw) > 8192 or not raw.endswith(b'\n'):
                    raise ValueError('EOF/line bound')
                s = validate(json.loads(raw, object_pairs_hook=unique))
                with self.lock:
                    self.last_metadata = {'elapsed_ms':s['elapsed_ms'],'state_sequence':s['state_sequence'],'request_id':s['request_id']}
                    old = self.state
                    if old and (s['boot_id'] != old['boot_id'] or s['state_sequence'] <= old['state_sequence'] or s['elapsed_ms'] < old['elapsed_ms']):
                        raise ValueError('regression')
                    if s['request_id'] != '0':
                        if s['request_id'] != self.pending:
                            raise ValueError('unmatched ACK')
                        self.pending = None
                        if self.anchor is None:
                            if self.handshake_sent is None or time.monotonic()-self.handshake_sent > .25:
                                raise ValueError('clock handshake RTT')
                            self.anchor = (self.handshake_sent, s['elapsed_ms'])
                        self.acks.put_nowait(s)
                    self.state = s
                    self.received = time.monotonic()
                    if not old or s['elapsed_ms'] > old['elapsed_ms']:
                        self.progress = self.received
                    if not self.handshake_started:
                        self.handshake_started = True
                        threading.Thread(target=self.handshake, daemon=True).start()
        except Exception:
            self.close('网关断开或协议异常；禁止自动重连')

    def snapshot(self):
        with self.lock:
            age = time.monotonic() - self.progress
            source_age = self.source_age()
            s = self.state if not self.closed and age <= .25 and source_age < 250 else None
            if s:
                self.observed.append((s['state_sequence'], s['elapsed_ms'], s['boot_id'], s['session_id'], time.monotonic()))
            error = self.error if self.closed or self.anchor is None else '状态已过期；保持原连接读取，需手动重新取会话'
            return {'state': s, 'age_ms': max(round(age * 1000), source_age) if s else None, 'error': None if s else error, 'diagnostics':list(self.diagnostics) if not s else [], 'last_metadata':self.last_metadata, 'failure_detail':self.failure_detail}

    def observe_ack(self, s):
        with self.lock:
            self.observed.append((s['state_sequence'],s['elapsed_ms'],s['boot_id'],s['session_id'],time.monotonic()))

    def send(self, p, controller):
        if type(p) is not dict or type(p.get('action')) is not str:
            raise ValueError('action')
        action = p['action']
        with self.transaction:
            with self.lock:
                s = self.state
                if self.closed or not s or time.monotonic() - self.progress > .25 or self.source_age() >= 250:
                    raise RuntimeError('state unavailable')
                if action in ('stop', 'clear'):
                    if set(p) != {'action'}:
                        raise ValueError('fields')
                    command = action
                elif action == 'arm':
                    if set(p) != {'action', 'boot_id'} or p['boot_id'] != s['boot_id']:
                        raise ValueError('boot')
                    command = f'arm {controller} keyboard'
                elif action == 'release':
                    if set(p) != {'action','boot_id','session_id'} or p['boot_id'] != s['boot_id'] or p['session_id'] != s['session_id'] or s['controller_id'] != controller:
                        raise ValueError('session')
                    command = f"release {controller} {p['boot_id']} {p['session_id']}"
                elif action == 'keys':
                    flags = ('deadman','forward','backward','left','right')
                    if set(p) != {'action','boot_id','session_id','sequence','issued_ms','observed_sequence',*flags} or any(type(p[k]) is not bool for k in flags) or not uid(p['sequence']):
                        raise ValueError('keys fields')
                    if any(type(p[k]) is not int or not 0 <= p[k] <= 9007199254740741 for k in ('issued_ms','observed_sequence')):
                        raise ValueError('clock')
                    if p['boot_id'] != s['boot_id'] or p['session_id'] != s['session_id'] or s['controller_id'] != controller or not s['guard_armed'] or s['owner'] != 'keyboard':
                        raise ValueError('ownership')
                    if not any(seq == p['observed_sequence'] and elapsed == p['issued_ms'] and boot == p['boot_id'] and session == p['session_id'] and time.monotonic()-rx <= .25 for seq,elapsed,boot,session,rx in self.observed):
                        raise ValueError('unobserved clock')
                    command = f"keys {p['boot_id']} {p['session_id']} {controller} {p['sequence']} {p['issued_ms']} {p['issued_ms']+250} " + ' '.join(str(int(p[k])) for k in flags)
                else:
                    raise ValueError('action')
                self.request += 1
                if self.request > MAX:
                    raise RuntimeError('request exhausted')
                self.pending = str(self.request)
                line = f'request {self.request} {command}\n'.encode('ascii')
            # Dedicated writer keeps even pipe backpressure inside the transaction deadline.
            def write():
                try:
                    self.process.stdin.write(line)
                    self.process.stdin.flush()
                except (OSError, ValueError):
                    self.close('SSH 写入失败')
            threading.Thread(target=write, daemon=True).start()
            try:
                return self.acks.get(timeout=.5)
            except queue.Empty:
                self.close('ACK 超时；旧连接已关闭')
                raise RuntimeError('ACK timeout')


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(2)

    def trusted(self, post=False):
        expected = f'http://127.0.0.1:{self.server.server_port}'
        return (self.headers.get('Host') == expected[7:] and self.headers.get('Origin') in ((expected,) if post else (None, expected))
                and (not post or self.headers.get('X-Guard-Token','') in self.server.sessions))

    def respond(self, status, body, mime='application/json'):
        self.send_response(status)
        self.send_header('Content-Type', mime+'; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.trusted():
            self.respond(403,b'{}'); return
        if self.path == '/api/session':
            with self.server.session_lock:
                if len(self.server.sessions) >= 32:
                    self.respond(429,b'{}'); return
                token=secrets.token_urlsafe(32)
                controller=str(secrets.randbelow(MAX)+1)
                self.server.sessions[token]=controller
            self.respond(200,json.dumps({'token':token,'controller_id':controller}).encode()); return
        if self.path == '/api/state':
            result = self.server.gateway.snapshot()
            self.respond(200,json.dumps(result).encode()); return
        if self.path not in FILES:
            self.respond(404,b'{}'); return
        name,mime=FILES[self.path]
        self.respond(200,(ROOT/'web/guard-console'/name).read_bytes(),mime)

    def do_POST(self):
        if not self.trusted(True):
            self.respond(403,b'{}'); return
        if self.path != '/api/command':
            self.respond(404,b'{}'); return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0 < length <= 1024 or self.headers.get('Content-Type') != 'application/json' or self.headers.get('Transfer-Encoding'):
                raise ValueError('body')
            payload=json.loads(self.rfile.read(length),object_pairs_hook=unique)
            ack=self.server.gateway.send(payload,self.server.sessions[self.headers['X-Guard-Token']])
            self.server.gateway.observe_ack(ack)
            self.respond(200,json.dumps({'ack':ack}).encode())
        except (ValueError,TypeError,KeyError,OverflowError):
            self.respond(400,b'{}')
        except (RuntimeError,OSError):
            self.respond(503,b'{}')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--robot-target',required=True)
    p.add_argument('--jump-host',default='llmemb-server')
    p.add_argument('--known-hosts',type=Path,required=True)
    p.add_argument('--ssh-bind-address')
    p.add_argument('--direct',action='store_true',help='connect directly; bypass jump and configured proxies')
    p.add_argument('--ssh-pty',action='store_true',help='direct only: raw, echo-free PTY with explicit client IPQoS=none')
    p.add_argument('--host-key-alias',help='direct only: existing trusted hostname/IP for host identity verification')
    p.add_argument('--gateway',required=True)
    p.add_argument('--port',type=int,default=0)
    p.add_argument('--gateway-duration-seconds',type=int,default=120,help='one-shot gateway lifetime, 1..600; command/lease windows unchanged')
    a=p.parse_args()
    if not 1 <= a.gateway_duration_seconds <= 600:
        p.error('--gateway-duration-seconds must be 1..600')
    if not 0 <= a.port <= 65535 or not re.fullmatch(r'/[A-Za-z0-9_./-]+/robot_control_gateway',a.gateway) or '..' in PurePosixPath(a.gateway).parts:
        p.error('invalid port/gateway path')
    if a.ssh_pty and not a.direct:
        p.error('--ssh-pty requires --direct')
    if a.host_key_alias is not None and (not a.direct or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,252}',a.host_key_alias)):
        p.error('--host-key-alias requires --direct and a safe hostname or IPv4 address')
    try:
        command=robot_command(a.robot_target,a.jump_host,'/tmp/robot_state_agent',a.known_hosts,None if a.direct else a.ssh_bind_address)
        if a.direct:
            # Reuse validated fixed SSH options; remove only the generated
            # ProxyCommand pair and tail, then explicitly disable config proxies.
            command=command[:-4]+['-o','ProxyCommand=none','-o','ProxyJump=none']
            if a.ssh_bind_address:
                command+=['-b',str(ipaddress.IPv4Address(a.ssh_bind_address))]
            if a.host_key_alias:
                command+=['-o','HostKeyAlias='+a.host_key_alias]
            command+=[a.robot_target,'']
    except ValueError as exc:
        p.error(str(exc))
    if a.ssh_pty:
        if command[1] != '-T':
            p.error('unexpected SSH command layout')
        command[1]='-tt'
        command[-2:-2]=['-o','IPQoS=none']
    command[-1]=('stty raw -echo && ' if a.ssh_pty else '')+'exec '+a.gateway+' --duration-seconds '+str(a.gateway_duration_seconds)
    with ThreadingHTTPServer(('127.0.0.1',a.port),Handler) as server:
        server.sessions={}
        server.session_lock=threading.Lock()
        server.gateway=Gateway(command)
        print(f'http://127.0.0.1:{server.server_port}/',flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.gateway.close()


if __name__ == '__main__':
    main()
