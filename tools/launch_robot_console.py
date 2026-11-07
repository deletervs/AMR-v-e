"""Manual saved-config launcher for the local PC/mobile console proxy."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True)
    args=p.parse_args()
    if args.config.is_symlink() or args.config.stat().st_size>4096: p.error('bounded private launch configuration required')
    value=json.loads(args.config.read_text(encoding='utf-8'))
    expected={'host','port','robot_port','local_port','access_token_file','allow_control','ssh_forward','cert','key'}
    if type(value) is not dict or set(value)!=expected or any(type(value[k]) is not bool for k in ('allow_control','ssh_forward')):
        p.error('unsupported console configuration')
    command=[sys.executable,str(Path(__file__).with_name('serve_robot_console.py')),
        '--host',value['host'],'--port',str(value['port']),'--robot-port',str(value['robot_port']),
        '--local-port',str(value['local_port']),'--access-token-file',value['access_token_file']]
    for key,flag in (('allow_control','--allow-control'),('ssh_forward','--ssh-forward')):
        if value[key]:command.append(flag)
    if value['cert'] is not None and value['key'] is not None:command+=['--cert',value['cert'],'--key',value['key']]
    # Foreground child; no boot registration, keepalive or retry loop.
    process=subprocess.Popen(command)
    try:return process.wait()
    except KeyboardInterrupt:
        process.terminate()
        try:process.wait(4)
        except subprocess.TimeoutExpired:process.kill();process.wait(2)
        return 130
if __name__=='__main__':raise SystemExit(main())
