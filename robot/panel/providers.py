"""Read-only Linux providers. No vendor control or system configuration writes."""
import json
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import threading
import time


def gamepad_path(value):
    path = Path(value)
    pattern = r'/home/handsfree/\.local/share/rs-control-gateway/builds/[A-Za-z0-9_-]+/build/robot_gamepad_info'
    if not re.fullmatch(pattern, str(path)) or str(path.resolve()) != str(path) or not path.is_file():
        raise ValueError('gamepad-info must be an existing project-owned robot_gamepad_info')
    return str(path)


def login_manager():
    import dbus
    return dbus.Interface(dbus.SystemBus().get_object('org.freedesktop.login1', '/org/freedesktop/login1'), 'org.freedesktop.login1.Manager')


class Power:
    def __init__(self):
        self.lock = threading.Lock()
        self.requested = False
        self.error = None

    def status(self):
        try:
            answer = str(login_manager().CanPowerOff(timeout=1))
            result = {'can_power_off': answer in ('yes', 'challenge'), 'reason': '可能需要 Linux 系统密码确认' if answer == 'challenge' else '请求 Linux 安全关机' if answer == 'yes' else '系统未允许关机：' + answer}
        except Exception as exc:
            result = {'can_power_off': False, 'reason': '系统关机能力读取失败：' + type(exc).__name__}
        with self.lock:
            if self.requested:
                result = {'can_power_off': False, 'reason': self.error or '安全关机请求已提交；等待系统身份验证'}
        return result

    def submit(self):
        with self.lock:
            if self.requested:
                return False
            if not self.status_unlocked_allowed():
                return False
            self.requested = True
        threading.Thread(target=self._call, daemon=True).start()
        return True

    def status_unlocked_allowed(self):
        # Does not acquire self.lock; only checks current system authorization capability.
        try:
            return str(login_manager().CanPowerOff(timeout=1)) in ('yes', 'challenge')
        except Exception:
            return False

    def _call(self):
        try:
            login_manager().PowerOff(True, timeout=120)
        except Exception as exc:
            with self.lock:
                self.error = '安全关机调用未完成：' + type(exc).__name__ + '；不自动重试'


class Collector:
    def __init__(self, gamepad_info=None, probe_ros=False, power=None, telemetry=None, input_service=None):
        self.input_service=input_service
        self.telemetry=telemetry
        self.gamepad_info = gamepad_path(gamepad_info) if gamepad_info else None
        self.probe_ros = probe_ros
        self.power = power or Power()
        self.cpu_previous = None
        self.started = time.monotonic()
        self.providers = {'system': self.system, 'network': self.network, 'gamepads': self.gamepads, 'bluetooth': self.bluetooth, 'ros': self.ros}

    def register(self, namespace, provider):
        if not re.fullmatch(r'ext_[a-z][a-z0-9_]{0,31}', namespace) or namespace in self.providers or not callable(provider):
            raise ValueError('extension namespace must be unique ext_name')
        self.providers[namespace] = provider

    def system(self):
        row = Path('/proc/stat').read_text().splitlines()[0].split()
        ticks = list(map(int, row[1:9]))
        total, idle = sum(ticks), ticks[3] + ticks[4]
        cpu = None
        if self.cpu_previous:
            dt, di = total-self.cpu_previous[0], idle-self.cpu_previous[1]
            if dt > 0 and 0 <= di <= dt:
                cpu = 100.0*(dt-di)/dt
        self.cpu_previous = (total, idle)
        memory = {}
        for line in Path('/proc/meminfo').read_text().splitlines():
            fields = line.split()
            memory[fields[0].rstrip(':')] = int(fields[1])
        percent = None
        if memory.get('MemTotal', 0) > 0 and 'MemAvailable' in memory:
            percent = 100.0*(memory['MemTotal']-memory['MemAvailable'])/memory['MemTotal']
        temp = None
        for zone in sorted(Path('/sys/class/thermal').glob('thermal_zone*')):
            try:
                kind = (zone/'type').read_text().strip().lower()
                if 'cpu' in kind or 'package' in kind:
                    raw = float((zone/'temp').read_text()) / 1000
                    if -50 <= raw <= 200:
                        temp = raw
                        break
            except (OSError, ValueError):
                continue
        return {'cpu_percent': cpu, 'memory_used_percent': percent, 'cpu_temp_c': temp}

    def network(self):
        import fcntl
        addresses = []
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
            for _, name in socket.if_nameindex():
                try:
                    raw = fcntl.ioctl(connection.fileno(), 0x8915, struct.pack('256s', name.encode()[:15]))
                    ip = socket.inet_ntoa(raw[20:24])
                    if not ip.startswith('127.') and ip not in addresses:
                        addresses.append(ip)
                except OSError:
                    continue
        return {'ipv4': addresses}

    def gamepads(self):
        if not self.gamepad_info:
            raise RuntimeError('gamepad-info not configured')
        result = subprocess.run([self.gamepad_info], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=2, check=True)
        if len(result.stdout) > 131072:
            raise ValueError('gamepad result too large')
        data = json.loads(result.stdout.decode('utf-8'))
        if data.get('source') != 'robot_gamepad_info' or data.get('motion_enabled') is not False or data.get('actuator_attached') is not False:
            raise ValueError('invalid gamepad source')
        devices = []
        if not isinstance(data.get('devices'), list) or len(data['devices']) > 32:
            raise ValueError('invalid devices')
        for device in data['devices']:
            path = device.get('path', '')
            if not re.fullmatch(r'/dev/input/js[0-9]{1,2}', path) or type(device.get('readable')) is not bool:
                raise ValueError('invalid input node')
            transport = 'unknown'
            try:
                kind = (Path('/sys/class/input')/Path(path).name/'device/id/bustype').read_text().strip()
                transport = {'0003': 'usb', '0005': 'bluetooth'}.get(kind, 'unknown')
            except OSError:
                pass
            name = device.get('name_bytes', '')
            if not isinstance(name, str):
                raise ValueError('invalid input name')
            devices.append({'path': path, 'name': name or '名称不可读', 'readable': device['readable'], 'transport': transport,
                            'axis_codes': device.get('axis_codes'), 'button_codes': device.get('button_codes')})
        return devices

    def bluetooth(self):
        import dbus
        manager = dbus.Interface(dbus.SystemBus().get_object('org.bluez', '/'), 'org.freedesktop.DBus.ObjectManager')
        objects = manager.GetManagedObjects(timeout=1)
        adapters, devices = [], []
        for interfaces in objects.values():
            if 'org.bluez.Adapter1' in interfaces:
                adapters.append(bool(interfaces['org.bluez.Adapter1'].get('Powered', False)))
            if 'org.bluez.Device1' in interfaces:
                p = interfaces['org.bluez.Device1']
                devices.append({'name': str(p.get('Name', p.get('Alias', '名称未知'))), 'connected': bool(p.get('Connected', False)), 'paired': bool(p.get('Paired', False))})
        return {'available': bool(adapters), 'powered': any(adapters) if adapters else None, 'devices': devices}

    def ros(self):
        if not self.probe_ros:
            return {'master_available': None}
        import xmlrpc.client
        class LocalTransport(xmlrpc.client.Transport):
            def make_connection(self, host):
                connection = super(LocalTransport, self).make_connection(host)
                connection.timeout = .3
                return connection
        master = xmlrpc.client.ServerProxy('http://127.0.0.1:11311', transport=LocalTransport())
        return {'master_available': master.getPid('/rs_local_panel')[0] == 1}

    def sample(self):
        defaults = {'system': {'cpu_percent': None, 'memory_used_percent': None, 'cpu_temp_c': None}, 'network': {'ipv4': []}, 'gamepads': [], 'bluetooth': {'available': False, 'powered': None, 'devices': []}, 'ros': {'master_available': None}}
        result = dict(defaults)
        errors = {}
        for name, provider in self.providers.items():
            try:
                result[name] = provider()
            except Exception as exc:
                errors[name] = type(exc).__name__ + ': ' + str(exc)[:200]
                if name not in defaults:
                    result[name] = None
        try:
            uptime = float(Path('/proc/uptime').read_text().split()[0])
        except (OSError, ValueError):
            uptime = None
            errors['uptime'] = 'unavailable'
        result.update({'panel_api_version': 1, 'source': 'robot_local_panel', 'sampled_elapsed_ms': int((time.monotonic()-self.started)*1000), 'uptime_s': uptime,
                       'battery': {'voltage_v': None, 'quality': 'Unknown'}, 'capabilities': {'battery': False, 'tasks': False},
                       'control': {'motion_enabled': False, 'actuator_attached': False}, 'power': self.power.status(), 'provider_errors': errors})
        if self.telemetry is not None:
            incoming=self.telemetry.snapshot()
            state=incoming['state']
            result['telemetry_stream']=incoming['stream']
            if state is not None:
                result['capabilities']['battery']=state.get('capabilities',{}).get('battery',False)
                battery=state.get('battery',{'voltage_v':None,'quality':'Unknown'})
                quality=battery.get('quality','Unknown')
                received=battery.get('received_elapsed_ms')
                if quality=='Valid':
                    if type(received) is not int or received>state['elapsed_ms']:
                        quality='Unknown'
                    elif incoming['stream']['age_ms']+state['elapsed_ms']-received>=1500:
                        quality='Stale'
                result['battery']={'voltage_v':battery['voltage_v'] if quality=='Valid' else None,'quality':quality}
        if self.input_service is not None:
            source=self.input_service.status()
            attached=bool(source['process_running'] and source.get('execution_allowed'))
            execution=source.get('executor',{})
            result['control']={'motion_enabled':bool(attached and execution.get('output_enabled')),
                'actuator_attached':attached}
        return result
