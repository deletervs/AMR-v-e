"""Real Jetson-local timing observation. Never writes to gateway stdin."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time


def observe(binary, expected_hash, seconds):
    import fcntl
    p = None
    result = {'scope': 'real native unarmed source, not transport or motion acceptance',
              'frame_count': 0, 'diagnostic_error': None, 'expected_lifetime_exit': False,
              'max_receive_gap_ms': 0, 'max_source_gap_ms': 0}
    previous = None
    previous_received = None
    pending = bytearray()
    stderr = bytearray()
    total = 0
    try:
        path = Path(binary).resolve(strict=True)
        if path.name != 'robot_control_gateway' or not str(path).startswith('/home/handsfree/.local/share/rs-control-gateway/builds/'):
            raise ValueError('not a project-owned gateway binary')
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        if digest.hexdigest() != expected_hash:
            raise ValueError('binary hash mismatch')
        result['binary_sha256'] = expected_hash
        result['started_wall_time'] = time.time()
        p = subprocess.Popen([str(path), '--duration-seconds', str(seconds)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        result['owned_pid'] = p.pid
        streams = {p.stdout.fileno(): 'stdout', p.stderr.fileno(): 'stderr'}
        for fd in streams:
            fcntl.fcntl(fd, fcntl.F_SETFL, fcntl.fcntl(fd, fcntl.F_GETFL) | os.O_NONBLOCK)
        deadline = time.monotonic() + seconds + 7
        while streams:
            if time.monotonic() >= deadline:
                raise TimeoutError('native diagnostic deadline')
            ready, _, _ = select.select(list(streams), [], [], .1)
            for fd in ready:
                try:
                    data = os.read(fd, 8192)
                except BlockingIOError:
                    continue
                if not data:
                    if streams[fd] == 'stdout' and pending:
                        raise ValueError('unterminated final frame')
                    del streams[fd]
                    continue
                if streams[fd] == 'stderr':
                    stderr.extend(data)
                    del stderr[:-4096]
                    continue
                received = time.monotonic()
                total += len(data)
                if total > 2 * 1024 * 1024:
                    raise ValueError('cumulative stdout exceeded 2MiB')
                pending.extend(data)
                while b'\n' in pending:
                    line, _, rest = pending.partition(b'\n')
                    pending = bytearray(rest)
                    if len(line) > 8192:
                        raise ValueError('line exceeded 8192 bytes')
                    state = json.loads(line.decode('utf-8'))
                    if not isinstance(state, dict) or state.get('source') != 'robot_control_gateway' or type(state.get('api_version')) is not int or state['api_version'] != 2:
                        raise ValueError('unexpected native API')
                    if state.get('actuator_attached') is not False or state.get('motion_enabled') is not False or state.get('guard_armed') is not False:
                        raise ValueError('native must remain unarmed without actuator')
                    candidate = state.get('candidate')
                    if type(candidate) is not dict or set(candidate) != {'linear_mps', 'angular_radps'} or any(type(v) not in (int, float) or v != 0 for v in candidate.values()):
                        raise ValueError('unexpected nonzero candidate')
                    elapsed, sequence = state.get('elapsed_ms'), state.get('state_sequence')
                    if type(elapsed) is not int or type(sequence) is not int or elapsed < 0 or sequence < 1:
                        raise ValueError('invalid native time/sequence')
                    if previous:
                        if state.get('boot_id') != previous.get('boot_id') or elapsed < previous['elapsed_ms'] or sequence <= previous['state_sequence']:
                            raise ValueError('native time/sequence regression')
                        result['max_receive_gap_ms'] = max(result['max_receive_gap_ms'], (received-previous_received)*1000)
                        result['max_source_gap_ms'] = max(result['max_source_gap_ms'], elapsed-previous['elapsed_ms'])
                    else:
                        result['native_first_elapsed_ms'] = elapsed
                    previous, previous_received = state, received
                    result['frame_count'] += 1
                    result['native_last_elapsed_ms'] = elapsed
                if len(pending) > 8192:
                    raise ValueError('unterminated line exceeded bound')
        result['child_returncode'] = p.wait(timeout=2)
        result['expected_lifetime_exit'] = (result['child_returncode'] == 0 and previous is not None and previous.get('result') == 'RuntimeExpired' and previous['elapsed_ms'] >= seconds*1000)
        if not result['expected_lifetime_exit']:
            raise RuntimeError('native exited before its expected lifetime')
    except Exception as error:
        result['diagnostic_error'] = type(error).__name__ + ': ' + str(error)
    finally:
        if p is not None:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait(timeout=2)
            result['child_returncode'] = p.returncode
            for stream in (p.stdin, p.stdout, p.stderr):
                stream.close()
        result['final_state'] = previous
        result['stderr'] = stderr.decode('utf-8', errors='replace')
        result['finished_wall_time'] = time.time()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--seconds', type=int, default=75)
    args = parser.parse_args()
    if sys.platform != 'linux' or not 1 <= args.seconds <= 90 or len(args.sha256) != 64 or any(c not in '0123456789abcdef' for c in args.sha256):
        parser.error('requires Linux, 1..90 seconds and a lowercase SHA256')
    def interrupted(signum, frame):
        raise InterruptedError('operator/SSH interrupted')
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, interrupted)
    result = observe(args.binary, args.sha256, args.seconds)
    print(json.dumps(result, ensure_ascii=True), flush=True)
    return 0 if result['expected_lifetime_exit'] else 1


if __name__ == '__main__':
    sys.exit(main())