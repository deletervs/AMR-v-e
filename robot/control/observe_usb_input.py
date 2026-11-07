"""Bounded observations of real USB input processes; never inject device events."""
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import select
import signal
import subprocess
import time


def usb_devices():
    result = []
    for node in Path('/sys/class/input').glob('js*/device'):
        try:
            if (node/'id/bustype').read_text().strip() == '0003':
                result.append('/dev/input/'+node.parent.name)
        except OSError:
            pass
    return sorted(result)


def identity(pid, parent=None, binary=None):
    proc = Path('/proc')/str(pid)
    if type(pid) is not int or pid <= 1 or proc.stat().st_uid != os.getuid():
        raise RuntimeError('Unexpected process owner')
    fields = (proc/'stat').read_text().rsplit(')', 1)[1].split()
    executable = os.readlink(str(proc/'exe'))
    if parent is not None and int(fields[1]) != parent:
        raise RuntimeError('Not the observed guard child')
    if binary is not None and executable != binary:
        raise RuntimeError('Unexpected process executable')
    return {'pid':pid, 'start_ticks':fields[19], 'binary':executable,
        'os_boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip()}


def capture(spec, stage, device, deadline):
    binary = spec['binary']
    if hashlib.sha256(Path(binary).read_bytes()).hexdigest() != spec['sha256']:
        raise RuntimeError('Native executable changed')
    directory = Path('/home/handsfree/.local/state/rs-usb-observer')
    if directory.is_symlink():
        raise RuntimeError('Observer directory is a symlink')
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o077:
        raise RuntimeError('Observer directory permissions')
    marker = directory/(spec['run_id']+'.json')
    seconds = min(600, max(1, int(math.ceil(deadline-time.monotonic()))))
    p = None
    frames, actions, paused = [], [], None
    err, pending = bytearray(), bytearray()
    fault, pid_identity = None, None
    last_marker_update = 0.
    try:
        p = subprocess.Popen([binary,'--device',device,'--duration-seconds',str(seconds)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        pid_identity = identity(p.pid, binary=binary)
        pid_identity['phase'] = stage
        fd = os.open(str(marker), os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as file:
            json.dump(pid_identity, file)
        streams = {p.stdout.fileno():'out', p.stderr.fileno():'err'}
        for fd in streams:
            fcntl.fcntl(fd, fcntl.F_SETFL, fcntl.fcntl(fd, fcntl.F_GETFL)|os.O_NONBLOCK)
        while streams:
            if time.monotonic() >= deadline:
                raise TimeoutError('Actual USB group deadline')
            ready, _, _ = select.select(list(streams), [], [], .05)
            for fd in ready:
                try:
                    data = os.read(fd, 16384)
                except BlockingIOError:
                    continue
                if not data:
                    if streams[fd] == 'out' and pending:
                        raise ValueError('Partial final frame')
                    del streams[fd]
                    continue
                if streams[fd] == 'err':
                    err.extend(data)
                    del err[:-4096]
                    continue
                pending.extend(data)
                while b'\n' in pending:
                    line, _, tail = pending.partition(b'\n')
                    pending = bytearray(tail)
                    if len(line) > 4096:
                        raise ValueError('Frame too long')
                    v = json.loads(line.decode())
                    if (v.get('source') != 'robot_usb_guard' or v.get('api_version') != 2
                        or v.get('actuator_attached') is not False or v.get('motion_enabled') is not False):
                        raise ValueError('Source/capability mismatch')
                    if frames and (v['sequence'] != frames[-1]['sequence']+1
                        or v['elapsed_ms'] < frames[-1]['elapsed_ms']
                        or v['boot_id'] != frames[-1]['boot_id'] or v['os_boot_id'] != frames[-1]['os_boot_id']):
                        raise ValueError('Identity/clock/sequence mismatch')
                    frames.append(v)
                    if time.monotonic()-last_marker_update >= .015 or v['action'] not in ('none','exit'):
                        last_marker_update = time.monotonic()
                        latest = dict(pid_identity)
                        latest['latest'] = v
                        latest['received_monotonic_ms'] = int(time.monotonic()*1000)
                        temporary = marker.with_suffix('.tmp')
                        fd_marker = os.open(str(temporary),os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
                        with os.fdopen(fd_marker,'w') as file:
                            json.dump(latest,file)
                        os.replace(str(temporary),str(marker))
                    if len(frames) > spec['seconds']*100+100:
                        raise ValueError('Frame count bound')
                    nonzero = v['guard_armed'] and any(t != 0 for t in v['candidate'].values())
                    if stage == 'source-timeout' and nonzero and paused is None:
                        # Pause only the actual reader forked by this exact observed guard.
                        child = identity(v['reader_pid'], parent=p.pid, binary=binary)
                        if hashlib.sha256(Path(child['binary']).read_bytes()).hexdigest() != spec['sha256']:
                            raise RuntimeError('Reader executable changed')
                        os.kill(child['pid'], signal.SIGSTOP)
                        paused = child
                        actions.append({'action':'pause_owned_real_reader','at_elapsed_ms':v['elapsed_ms'],
                            'last_sample_ms':v['source_observed_ms'],'identity':child})
                    if stage == 'reconnected' and nonzero and not actions:
                        actions.append({'action':'finish_after_explicit_real_rearm','at_elapsed_ms':v['elapsed_ms']})
                        p.send_signal(signal.SIGINT)
                if len(pending) > 4096:
                    raise ValueError('Pending frame bound')
        p.wait(timeout=2)
    except Exception as error:
        fault = type(error).__name__+': '+str(error)
    finally:
        if p is not None:
            if p.poll() is None:
                p.send_signal(signal.SIGINT)
            try:
                tail_out, tail_err = p.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                p.kill()
                tail_out, tail_err = p.communicate(timeout=2)
            err.extend(tail_err)
            del err[:-4096]
            pending.extend(tail_out)
            # Preserve real shutdown frames emitted after a deadline or manual exit.
            for line in pending.splitlines():
                if not line:
                    continue
                try:
                    v = json.loads(line.decode())
                    if (v.get('source') != 'robot_usb_guard' or v.get('api_version') != 2
                        or v.get('actuator_attached') is not False or v.get('motion_enabled') is not False
                        or (frames and (v['sequence'] != frames[-1]['sequence']+1
                            or v['boot_id'] != frames[-1]['boot_id'] or v['elapsed_ms'] < frames[-1]['elapsed_ms']))):
                        raise ValueError('Invalid real shutdown frame')
                    frames.append(v)
                except (ValueError,UnicodeError) as error:
                    if fault is None:
                        fault = 'Shutdown frame: '+str(error)
            p.stdout.close()
            p.stderr.close()
        if marker.exists():
            marker.unlink()
    remaining = []
    if paused is not None:
        try:
            if identity(paused['pid']) == paused:
                remaining.append(paused['pid'])
        except (OSError, RuntimeError, ValueError):
            pass
    return {'stage':stage,'device':device,'parent_identity':pid_identity,'frames':frames,'actions':actions,
        'error':fault,'stderr':err.decode(errors='replace'),'exit':p.returncode if p else None,
        'owned_guard_exited':p is not None and p.poll() is not None,'paused_reader_remaining':remaining}


def run_group(spec):
    deadline = time.monotonic()+spec['seconds']
    stages = []
    fault = None
    try:
        devices = usb_devices()
        if len(devices) != 1:
            raise RuntimeError('Expected one real USB joystick')
        stage = capture(spec, 'source-timeout', devices[0], deadline)
        stages.append(stage)
        frames = stage['frames']
        if (stage['error'] or not frames or frames[-1]['runtime_reason'] != 'SourceTimeout'
            or frames[-1]['guard_expiry_reason'] != 'CommandExpired' or not stage['actions']):
            raise RuntimeError('Actual source timeout not established; stopped group')
        devices = usb_devices()
        if len(devices) != 1:
            raise RuntimeError('USB missing before unplug phase')
        stage = capture(spec, 'unplug', devices[0], deadline)
        stages.append(stage)
        if stage['error'] or not stage['frames'] or stage['frames'][-1]['runtime_reason'] != 'Disconnected':
            raise RuntimeError('Actual unplug not established; stopped group')
        while time.monotonic() < deadline:
            devices = usb_devices()
            if len(devices) == 1:
                break
            if len(devices) > 1:
                raise RuntimeError('Multiple USB devices after reconnect')
            time.sleep(.05)
        else:
            raise TimeoutError('Waiting for actual USB reconnection')
        stage = capture(spec, 'reconnected', devices[0], deadline)
        stages.append(stage)
        if stage['error'] or not stage['actions']:
            raise RuntimeError('Actual fresh manual rearm not established')
    except Exception as error:
        fault = type(error).__name__+': '+str(error)
    return {'scope':'actual USB source pause, physical unplug/replug and new manual sessions; no actuator',
        'run_id':spec['run_id'],'stages':stages,'error':fault}