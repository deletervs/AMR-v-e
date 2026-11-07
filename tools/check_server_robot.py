"""Manual, bounded PC -> server -> robot connectivity observation. No motion."""
import argparse
import json
from pathlib import Path
import subprocess

REMOTE = r'''
import os,json,pathlib,subprocess,tempfile,time,select,fcntl
spec=SPEC
base=pathlib.Path('/wuhongwei/yq/RS/.dev-access')
if base.is_symlink():raise RuntimeError('project access directory is a symlink')
base.mkdir(mode=0o700,parents=True,exist_ok=True)
known=base/'robot_known_hosts'
if known.is_symlink():raise RuntimeError('project trust file is a symlink')
if known.exists():
 if known.read_text()!=spec['public_line']:raise RuntimeError('existing project robot trust differs; no overwrite')
else:
 fd=os.open(str(known),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as f:f.write(spec['public_line'])
robot="""import time,json,pathlib
boot=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
start=time.monotonic();seq=0
while True:
 now=time.monotonic();seq+=1
 print(json.dumps({'source':'rs_connection_observer','boot_id':boot,'sequence':seq,'elapsed_ms':round((now-start)*1000)}),flush=True)
 if now-start>=SECONDS:break
 time.sleep(.1)
""".replace('SECONDS',str(spec['seconds']))
import shlex
args=['ssh','-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','HostKeyAlgorithms=ssh-ed25519','-o','UserKnownHostsFile='+str(known),'-o','ProxyCommand=none','-o','ProxyJump=none','-o','ConnectTimeout=5','-o','ConnectionAttempts=1','handsfree@10.10.218.121','python3 -u -c '+shlex.quote(robot)]
p=None;count=0;last=None;last_received=None;max_receive_gap=0;max_source_gap=0;errors=bytearray();pending=bytearray();fault=None;started=time.monotonic()
try:
 p=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 streams={p.stdout.fileno():'out',p.stderr.fileno():'err'}
 for fd in streams:fcntl.fcntl(fd,fcntl.F_SETFL,fcntl.fcntl(fd,fcntl.F_GETFL)|os.O_NONBLOCK)
 while streams:
  if time.monotonic()-started>spec['seconds']+12:raise TimeoutError('connection observation deadline')
  ready,_,_=select.select(list(streams),[],[],.1)
  for fd in ready:
   try:data=os.read(fd,8192)
   except BlockingIOError:continue
   if not data:
    if streams[fd]=='out' and pending:raise ValueError('partial final frame')
    del streams[fd];continue
   if streams[fd]=='err':errors.extend(data);del errors[:-2048];continue
   received=time.monotonic();pending.extend(data)
   while b'\n' in pending:
    line,_,tail=pending.partition(b'\n');pending=bytearray(tail)
    if len(line)>1024:raise ValueError('frame too long')
    frame=json.loads(line.decode('utf-8'))
    if frame.get('source')!='rs_connection_observer' or type(frame.get('sequence')) is not int or type(frame.get('elapsed_ms')) is not int:raise ValueError('unexpected observation frame')
    if last:
     if frame['boot_id']!=last['boot_id'] or frame['sequence']!=last['sequence']+1 or frame['elapsed_ms']<last['elapsed_ms']:raise ValueError('boot or sequence changed')
     max_receive_gap=max(max_receive_gap,(received-last_received)*1000);max_source_gap=max(max_source_gap,frame['elapsed_ms']-last['elapsed_ms'])
    last=frame;last_received=received;count+=1
    if count>spec['seconds']*20+100:raise ValueError('frame count bound')
   if len(pending)>1024:raise ValueError('unterminated frame bound')
 p.wait(timeout=2)
except Exception as e:fault=type(e).__name__+': '+str(e)
finally:
 if p is not None:
  if p.poll() is None:
   p.terminate()
   try:p.wait(timeout=2)
   except subprocess.TimeoutExpired:p.kill();p.wait(timeout=2)
  p.stdout.close();p.stderr.close()
complete=fault is None and p is not None and p.returncode==0 and last is not None and last['elapsed_ms']>=spec['seconds']*1000
print(json.dumps({'scope':'server-origin real SSH connectivity only; no telemetry or motion acceptance','completed':complete,'requested_seconds':spec['seconds'],'frames':count,'max_receive_gap_ms':max_receive_gap,'max_source_gap_ms':max_source_gap,'last_frame':last,'ssh_exit':p.returncode if p else None,'error':fault,'stderr':errors.decode('utf-8',errors='replace'),'project_known_hosts':str(known),'auto_reconnect':False,'started_at_epoch':time.time()-(time.monotonic()-started)}))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=60)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 120:
        parser.error('--seconds must be 1..120')
    hosts = Path.home()/'.ssh/known_hosts'
    lookup = subprocess.run(['ssh-keygen','-F','10.10.218.121','-f',str(hosts)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,check=True)
    keys = [line.split() for line in lookup.stdout.splitlines() if line and not line.startswith('#')]
    key = next((row for row in keys if len(row)>=3 and row[1]=='ssh-ed25519'),None)
    if key is None:
        raise RuntimeError('no previously trusted ED25519 robot key; refusing new trust')
    spec = {'public_line':'10.10.218.121 '+key[1]+' '+key[2]+'\n','seconds':args.seconds}
    script = REMOTE.replace('SPEC',repr(spec),1)
    result = subprocess.run(['ssh','-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=7','llmemb-server','python3','-'],input=script.encode('utf-8'),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=args.seconds+30)
    report = {'outer_ssh_exit':result.returncode,'stderr':result.stderr.decode('utf-8',errors='replace')}
    if result.stdout:
        report['observation'] = json.loads(result.stdout)
    folder = Path(__file__).resolve().parents[1]/'.local/learning-checks/M4-005'
    folder.mkdir(parents=True,exist_ok=True)
    import datetime
    name = datetime.datetime.now().strftime('server-link-%Y%m%d-%H%M%S.json')
    (folder/name).write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True))
    return 0 if result.returncode==0 and report.get('observation',{}).get('completed') else 1


if __name__=='__main__':
    raise SystemExit(main())