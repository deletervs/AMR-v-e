"""Run one grouped real USB timeout/unplug/replug observation, without motors."""
import argparse
import ast
import datetime
import json
from pathlib import Path
import uuid
from observe_usb_guard import ACTIVE, ROOT, run_robot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=300)
    args = parser.parse_args()
    if not 60 <= args.seconds <= 600:
        parser.error('--seconds must be 60..600')
    folder = ROOT/'.local/learning-checks/M4-010'
    manifest = json.loads((folder/'native-build.json').read_text())
    binary = manifest['binary']
    if not manifest.get('built') or not binary.startswith('/home/handsfree/.local/share/rs-control-gateway/builds/') or not binary.endswith('/build/robot_usb_guard'):
        raise ValueError('Unexpected build manifest')
    spec = {'binary':binary,'sha256':manifest['binary_sha256'],'seconds':args.seconds,'run_id':uuid.uuid4().hex}
    remote = (ROOT/'robot/control/observe_usb_input.py').read_text(encoding='utf-8')
    ast.parse(remote)
    robot = remote+'\nprint(json.dumps(run_group('+ascii(spec)+')))\n'
    with ACTIVE.open('x', encoding='utf-8') as file:
        json.dump(spec,file)
    try:
        proc = run_robot(robot,args.seconds+20)
    finally:
        if ACTIVE.exists() and json.loads(ACTIVE.read_text())['run_id']==spec['run_id']:
            ACTIVE.unlink()
    report = {'outer_exit':proc.returncode,'outer_stderr':proc.stderr.decode(errors='replace')}
    try:
        report['observation'] = json.loads(proc.stdout)
    except ValueError:
        report['invalid_output'] = proc.stdout[-4096:].decode(errors='replace')
    path = folder/('real-failure-group-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
    path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    result = report.get('observation',{})
    summary = {'record':str(path),'outer_exit':proc.returncode,'error':result.get('error',report.get('invalid_output')),
        'stages':[{'stage':v['stage'],'device':v['device'],'frames':len(v['frames']),
            'actions':v['actions'],'final':v['frames'][-1] if v['frames'] else None,
            'error':v['error'],'stderr':v['stderr'],'exit':v['exit'],
            'owned_guard_exited':v['owned_guard_exited'],'paused_reader_remaining':v['paused_reader_remaining']}
            for v in result.get('stages',[])]}
    path.with_name(path.stem+'-summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary))
    return 0 if proc.returncode==0 and result.get('error') is None and len(result.get('stages',[]))==3 else 1


if __name__ == '__main__':
    raise SystemExit(main())