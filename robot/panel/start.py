"""Manual desktop launcher. Single current-user instance; no autostart setup."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import re
import subprocess
import sys
import time
import urllib.request
from providers import gamepad_path


def stop_owned_group(process):
    """Only for children launched here with start_new_session=True."""
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        deadline = time.monotonic()+3
        while time.monotonic() < deadline:
            process.poll()  # Reap the direct child while descendants finish.
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                break
            time.sleep(.05)
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gamepad-info')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    config_path = Path(__file__).resolve().with_name('config.json')
    config = {}
    if config_path.exists():
        if config_path.stat().st_size > 4096:
            parser.error('config too large')
        config = json.loads(config_path.read_text())
        if type(config) is not dict or set(config)-{'gamepad_info', 'probe_ros', 'input_runtime', 'telemetry_sources', 'execution_profile'} or type(config.get('probe_ros', False)) is not bool:
            parser.error('unsupported config fields')
    runtime = config.get('input_runtime')
    if runtime is not None and (type(runtime) is not dict or set(runtime) != {'path','sha256'} or type(runtime['path']) is not str or type(runtime['sha256']) is not str or not re.fullmatch(r'[0-9a-fA-F]{64}',runtime['sha256'])):
        parser.error('input_runtime must be null or {path,sha256}')
    executable = args.gamepad_info or config.get('gamepad_info')
    if executable:
        executable = gamepad_path(executable)
    browser = shutil.which('chromium-browser')
    if not browser:
        parser.error('existing chromium-browser not found; nothing installed')
    private = Path.home()/'.local/state/rs-panel'
    private.mkdir(mode=0o700, parents=True, exist_ok=True)
    if private.is_symlink() or private.resolve() != private or private.stat().st_uid != os.getuid() or private.stat().st_mode & 0o077:
        parser.error('panel state directory must be private, owned and not symlinked')
    os.umask(0o077)
    lock = os.open(str(private/'launcher.lock'), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(lock)
        print('本机面板已在运行；不创建第二实例。请返回现有面板。')
        return 0
    server = None
    chromium = None
    instance = secrets.token_hex(16)
    state_file = private/'server-state.json'
    log_path = private/'panel.log'
    if log_path.is_symlink() or state_file.is_symlink() or (private/'browser-profile').is_symlink():
        parser.error('refusing symlinked panel files')
    # Bounded startup logs, no token or credential output.
    if log_path.exists() and log_path.stat().st_size > 1048576:
        log_path.replace(private/'panel.previous.log')
    descriptor = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, 'a') as log:
            command = [sys.executable, str(root/'robot/panel/server.py'), '--port', '0', '--state-file', str(state_file), '--instance-id', instance]
            if executable:
                command += ['--gamepad-info', executable]
            if runtime is not None:
                command += ['--input-runtime',runtime['path'],'--input-runtime-sha256',runtime['sha256']]
            if config.get('execution_profile') is not None:
                if config['execution_profile']!='/home/handsfree/.local/state/rs-executor/authorization.json':
                    raise ValueError('fixed local execution profile required')
                command+=['--execution-profile',config['execution_profile']]
            if config.get('telemetry_sources') is not None:
                source_path=private/'sources-config.json'
                raw=json.dumps(config['telemetry_sources'],allow_nan=False)
                fd=os.open(str(source_path),os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
                with os.fdopen(fd,'w') as source_file: source_file.write(raw)
                command+=['--sources-config',str(source_path)]
            if config.get('probe_ros', False):
                command.append('--probe-ros')
            server = subprocess.Popen(command, stdout=log, stderr=log, start_new_session=True)
            def interrupted(signum, frame):
                raise KeyboardInterrupt()
            signal.signal(signal.SIGTERM, interrupted)
            signal.signal(signal.SIGINT, interrupted)
            deadline = time.monotonic()+12
            url = None
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    raise RuntimeError('panel server exited; see private panel.log')
                try:
                    if state_file.stat().st_uid != os.getuid() or state_file.stat().st_mode & 0o077:
                        raise RuntimeError('untrusted state file')
                    state = json.loads(state_file.read_text())
                    if state.get('pid') != server.pid or state.get('instance_id') != instance or state.get('uid') != os.getuid() or type(state.get('port')) is not int or not 1 <= state['port'] <= 65535:
                        raise ValueError('state not current instance')
                    url = 'http://127.0.0.1:{}/'.format(state['port'])
                    response = json.loads(opener.open(url+'api/instance', timeout=1).read().decode())
                    if response != {'source': 'robot_local_panel', 'instance_id': instance, 'pid': server.pid}:
                        raise ValueError('instance mismatch')
                    break
                except (OSError, ValueError):
                    url = None
                    time.sleep(.1)
            if url is None:
                raise RuntimeError('panel instance verification timeout')
            chromium = subprocess.Popen([browser, '--app='+url, '--start-fullscreen', '--no-first-run', '--disable-gpu', '--user-data-dir='+str(private/'browser-profile')], stdout=log, stderr=log, start_new_session=True)
            opened = time.monotonic()
            exited_via_server = False
            while chromium.poll() is None:
                if server.poll() is not None:
                    if server.returncode != 0:
                        raise RuntimeError('panel server exited unexpectedly')
                    exited_via_server = True
                    break
                time.sleep(.25)
            if not exited_via_server and time.monotonic()-opened < 2:
                raise RuntimeError('browser exited immediately; check private panel.log')
    except KeyboardInterrupt:
        pass
    finally:
        try:
            stop_owned_group(chromium)
        finally:
            try:
                stop_owned_group(server)
            finally:
                try:
                    current = json.loads(state_file.read_text())
                    if server is not None and current.get('instance_id') == instance and current.get('pid') == server.pid:
                        state_file.unlink()
                except (OSError, ValueError):
                    pass
                os.close(lock)
    return 0


if __name__ == '__main__':
    sys.exit(main())
