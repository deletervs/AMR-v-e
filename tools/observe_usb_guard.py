"""Manually observe real robot-local USB/MotionGuard, without an actuator."""
import argparse
import datetime
import json
from pathlib import Path
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]
ACTIVE = ROOT/".local/learning-checks/M4-010/active-observation.json"
ROBOT = r"""
import fcntl,hashlib,json,os,pathlib,select,subprocess,time
spec=SPEC
binary=pathlib.Path(spec['binary'])
if hashlib.sha256(binary.read_bytes()).hexdigest()!=spec['sha256']:
 raise RuntimeError('native binary hash mismatch')
devices=[]
for node in pathlib.Path('/sys/class/input').glob('js*/device'):
 try:
  if (node/'id/bustype').read_text().strip()=='0003':
   devices.append('/dev/input/'+node.parent.name)
 except OSError:pass
if len(devices)!=1:raise RuntimeError('Expected exactly one USB joystick; found '+str(len(devices)))
state=pathlib.Path('/home/handsfree/.local/state/rs-usb-observer')
if state.is_symlink():raise RuntimeError('observer state is a symlink')
state.mkdir(mode=0o700,parents=True,exist_ok=True)
if state.stat().st_uid!=os.getuid() or state.stat().st_mode&0o077:raise RuntimeError('observer state permissions')
marker=state/(spec['run_id']+'.json')
p=None;frames=[];err=bytearray();pending=bytearray();fault=None
try:
 p=subprocess.Popen([str(binary),'--device',devices[0],'--duration-seconds',str(spec['seconds'])],
  stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 identity={'pid':p.pid,'start_ticks':pathlib.Path('/proc/'+str(p.pid)+'/stat').read_text().rsplit(')',1)[1].split()[19],
  'os_boot_id':pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'binary':str(binary),'phase':'manual'}
 fd=os.open(str(marker),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as file:json.dump(identity,file)
 streams={p.stdout.fileno():'out',p.stderr.fileno():'err'}
 for fd in streams:fcntl.fcntl(fd,fcntl.F_SETFL,fcntl.fcntl(fd,fcntl.F_GETFL)|os.O_NONBLOCK)
 last_marker_update=0
 end=time.monotonic()+spec['seconds']+10
 while streams:
  if time.monotonic()>end:raise TimeoutError('real USB observation deadline')
  ready,_,_=select.select(list(streams),[],[],.1)
  for fd in ready:
   try:data=os.read(fd,16384)
   except BlockingIOError:continue
   if not data:
    if streams[fd]=='out' and pending:raise ValueError('partial final frame')
    del streams[fd];continue
   if streams[fd]=='err':err.extend(data);del err[:-4096];continue
   pending.extend(data)
   while b'\n' in pending:
    line,_,tail=pending.partition(b'\n');pending=bytearray(tail)
    if len(line)>4096:raise ValueError('oversized frame')
    v=json.loads(line.decode('utf-8'))
    if v.get('source')!='robot_usb_guard' or v.get('api_version')!=2:
     raise ValueError('unexpected source/version')
    if v.get('motion_enabled') is not False or v.get('actuator_attached') is not False:
     raise ValueError('unexpected control capability')
    if frames and (v['sequence']!=frames[-1]['sequence']+1 or v['elapsed_ms']<frames[-1]['elapsed_ms']
     or v['boot_id']!=frames[-1]['boot_id'] or v['os_boot_id']!=frames[-1]['os_boot_id']):
     raise ValueError('identity/sequence/clock regression')
    frames.append(v)
    if time.monotonic()-last_marker_update>=.015 or v['action'] not in ('none','exit'):
     last_marker_update=time.monotonic()
     latest=dict(identity);latest['latest']=v;latest['received_monotonic_ms']=int(time.monotonic()*1000)
     temporary=marker.with_suffix('.tmp')
     fd_marker=os.open(str(temporary),os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
     with os.fdopen(fd_marker,'w') as file:json.dump(latest,file)
     os.replace(str(temporary),str(marker))
    if len(frames)>spec['seconds']*100+100:raise ValueError('frame count bound')
   if len(pending)>4096:raise ValueError('pending frame bound')
 p.wait(timeout=2)
except Exception as e:fault=type(e).__name__+': '+str(e)
finally:
 if p is not None:
  if p.poll() is None:
   p.terminate()
   try:p.wait(timeout=2)
   except subprocess.TimeoutExpired:p.kill();p.wait(timeout=2)
  p.stdout.close();p.stderr.close()
 if marker.exists():marker.unlink()
print(json.dumps({'scope':'actual local USB and MotionGuard candidate only; no actuator',
 'device':devices[0],'binary':str(binary),'frames':frames,'error':fault,
 'exit':p.returncode if p else None,'stderr':err.decode(errors='replace'),
 'owned_process_exited':p is not None and p.poll() is not None}))
"""


def run_robot(robot, timeout):
    args_robot = ['ssh','-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
        '-o','HostKeyAlgorithms=ssh-ed25519',
        '-o','UserKnownHostsFile=/wuhongwei/yq/RS/.dev-access/robot_known_hosts',
        '-o','ProxyCommand=none','-o','ProxyJump=none',
        '-o','ConnectTimeout=5','handsfree@10.10.218.121','python3','-']
    server = ("import subprocess; p=subprocess.run("+ascii(args_robot)+",input="+ascii(robot)+
        ".encode('ascii'),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout="+str(timeout)+
        "); print(p.stdout.decode('utf-8')); print(p.stderr.decode('utf-8')); raise SystemExit(p.returncode)")
    return subprocess.run(['ssh','-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
        '-o','ConnectTimeout=7','llmemb-server','python3','-'],
        input=server.encode('ascii'),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout+15)


def stop_observation():
    active=json.loads(ACTIVE.read_text(encoding='utf-8'))
    run_id=active['run_id']
    if len(run_id)!=32 or any(v not in '0123456789abcdef' for v in run_id):raise ValueError('invalid run identity')
    robot=r"""
import hashlib,json,os,pathlib,signal
spec=SPEC
marker=pathlib.Path('/home/handsfree/.local/state/rs-usb-observer')/(spec['run_id']+'.json')
if marker.is_symlink() or not marker.is_file() or marker.stat().st_uid!=os.getuid():
 raise RuntimeError('No owned active observation marker')
identity=json.loads(marker.read_text())
pid=identity['pid'];proc=pathlib.Path('/proc')/str(pid)
if type(pid) is not int or pid<=1 or proc.stat().st_uid!=os.getuid():raise RuntimeError('wrong process owner')
if identity['os_boot_id']!=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip():raise RuntimeError('boot changed')
if proc.joinpath('stat').read_text().rsplit(')',1)[1].split()[19]!=identity['start_ticks']:raise RuntimeError('PID reused')
exe=os.readlink(str(proc/'exe'))
if exe!=spec['binary'] or exe!=identity['binary']:raise RuntimeError('unexpected executable')
if hashlib.sha256(pathlib.Path(exe).read_bytes()).hexdigest()!=spec['sha256']:raise RuntimeError('executable hash changed')
cmd=(proc/'cmdline').read_bytes().split(b'\0')
if len(cmd)<5 or cmd[1]!=b'--device' or cmd[3]!=b'--duration-seconds':raise RuntimeError('unexpected arguments')
os.kill(pid,signal.SIGINT)
print(json.dumps({'stop_requested':True,'run_id':spec['run_id'],'scope':'only owned no-actuator observer'}))
""".replace('SPEC',ascii(active),1)
    result=run_robot(robot,15)
    print(result.stdout.decode(errors='replace'))
    if result.returncode:raise RuntimeError(result.stderr.decode(errors='replace'))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=300)
    parser.add_argument('--stop', action='store_true')
    parser.add_argument('--label', choices=['passive', 'manual'])
    args = parser.parse_args()
    if args.stop:
        return stop_observation()
    if args.label is None:
        parser.error('--label is required when starting')
    if not 1 <= args.seconds <= 600:
        parser.error('--seconds must be 1..600')
    folder = ROOT/'.local/learning-checks/M4-010'
    manifest = json.loads((folder/'native-build.json').read_text(encoding='utf-8-sig'))
    if manifest.get('built') is not True:
        raise RuntimeError('No successful native build manifest')
    binary = manifest['binary']
    if not binary.startswith('/home/handsfree/.local/share/rs-control-gateway/builds/') or not binary.endswith('/build/robot_usb_guard'):
        raise ValueError('Unexpected native program path')
    spec = {'binary':binary, 'sha256':manifest['binary_sha256'], 'seconds':args.seconds, 'run_id':uuid.uuid4().hex}
    if ACTIVE.exists():
        raise RuntimeError('An observation is already marked active; inspect it before starting another')
    with ACTIVE.open('x',encoding='utf-8') as file:
        json.dump(spec,file)
    robot = ROBOT.replace('SPEC', ascii(spec), 1)
    try:
        proc=run_robot(robot,args.seconds+20)
    finally:
        if ACTIVE.exists() and json.loads(ACTIVE.read_text())['run_id']==spec['run_id']:
            ACTIVE.unlink()
    report = {'outer_exit':proc.returncode, 'outer_stderr':proc.stderr.decode(errors='replace')}
    try:
        report['observation'] = json.loads(proc.stdout)
    except (ValueError, UnicodeDecodeError):
        report['invalid_output'] = proc.stdout[-4096:].decode(errors='replace')
    folder.mkdir(parents=True, exist_ok=True)
    path = folder/(args.label+'-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
    path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    data = report.get('observation', {})
    frames = data.get('frames', [])
    summary = {'evidence':str(path), 'outer_exit':proc.returncode,
        'frames':len(frames), 'initialized_frames':sum(v['initialized'] for v in frames),
        'actions':[(v['elapsed_ms'],v['action'],v['result']) for v in frames if v['action']!='none'],
        'guard_ever_armed':any(v['guard_armed'] for v in frames),
        'nonzero_candidate_frames':sum(any(t!=0 for t in v['candidate'].values()) for v in frames),
        'final':frames[-1] if frames else None, 'error':data.get('error', report.get('invalid_output')),
        'stderr':data.get('stderr', report['outer_stderr']),
        'owned_process_exited':data.get('owned_process_exited',False)}
    path.with_name(path.stem+'-summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary))
    return 0 if proc.returncode==0 and data.get('exit')==0 and frames and data.get('error') is None else 1


if __name__ == '__main__':
    raise SystemExit(main())