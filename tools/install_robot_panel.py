"""Install only project-owned panel files and a desktop launcher. No autostart."""
import argparse
import ast
import hashlib
import json
import re
from pathlib import Path
import subprocess
import datetime

ROBOT = r'''
import os,json,pathlib,subprocess,time,hashlib,ast
spec=SPEC
home=pathlib.Path('/home/handsfree');base=home/'.local/share/rs-panel/releases';release=base/spec['bundle']
base.mkdir(parents=True,exist_ok=True)
if base.is_symlink() or str(base.resolve())!=str(base):raise RuntimeError('unsafe project release path')
for name,text in spec['files'].items():
 p=release/name;p.parent.mkdir(parents=True,exist_ok=True)
 if p.is_symlink():raise RuntimeError('refusing existing symlink')
 if p.exists() and hashlib.sha256(p.read_bytes()).hexdigest()!=spec['hashes'][name]:raise RuntimeError('existing release drift; not overwritten')
 if not p.exists():p.write_bytes(text.encode())
assert all(hashlib.sha256((release/n).read_bytes()).hexdigest()==h for n,h in spec['hashes'].items())
for name in spec['files']:
 if name.endswith('.py'):ast.parse((release/name).read_text())
config=release/'robot/panel/config.json';wanted=json.dumps({'gamepad_info':spec['gamepad_info'],'probe_ros':False,'input_runtime':spec['input_runtime'],'telemetry_sources':spec.get('telemetry_sources'),'execution_profile':spec.get('execution_profile')},sort_keys=True)
if config.exists() and config.read_text()!=wanted:raise RuntimeError('existing release config drift')
config.write_text(wanted);os.chmod(str(config),0o600)
desktop=home/'Desktop/RS-Robot-Panel.desktop'
if desktop.is_symlink():raise RuntimeError('desktop shortcut is a symlink')
if desktop.exists() and 'X-RS-Panel=true' not in desktop.read_text():raise RuntimeError('unowned existing desktop shortcut')
text='[Desktop Entry]\nType=Application\nVersion=1.0\nName=机器人状态面板\nComment=本机状态、手柄信息与分页显示\nExec=/usr/bin/python3 '+str(release/'robot/panel/start.py')+'\nPath='+str(release)+'\nIcon='+str(release/'web/robot-panel/icon.svg')+'\nTerminal=false\nStartupNotify=true\nCategories=Utility;\nX-RS-Panel=true\n'
if desktop.exists() and desktop.read_text()!=text:desktop.with_suffix('.desktop.previous').write_text(desktop.read_text())
desktop.write_text(text);os.chmod(str(desktop),0o755)
env=os.environ.copy();env['DISPLAY']=':0';env['XAUTHORITY']=str(home/'.Xauthority');env['XDG_RUNTIME_DIR']='/run/user/'+str(os.getuid())
# Reuse only selected desktop-context variables of this same user's session.
for proc in pathlib.Path('/proc').iterdir():
 if not proc.name.isdigit():continue
 try:
  if proc.stat().st_uid!=os.getuid() or (proc/'comm').read_text().strip() not in ('gnome-session-b','unity-panel-ser','gnome-shell'):continue
  for item in (proc/'environ').read_bytes().split(b'\0'):
   key,sep,value=item.partition(b'=')
   if sep and key.decode(errors='ignore') in ('DBUS_SESSION_BUS_ADDRESS','XDG_SESSION_ID','XDG_RUNTIME_DIR','DISPLAY','XAUTHORITY'):env[key.decode()]=value.decode()
  break
 except (OSError,ValueError):pass
trust=subprocess.run(['gio','set','-t','string',str(desktop),'metadata::trusted','true'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=8)
result={'release':str(release),'desktop':str(desktop),'source_sha256':spec['hashes'],'trust_metadata_exit':trust.returncode,'trust_metadata_error':trust.stderr[:300],'motion_enabled':False,'auto_start_installed':False,'shutdown_executed':False,'input_runtime':spec['input_runtime']}
if spec['launch']:
 runtime=home/'.local/state/rs-panel';runtime.mkdir(mode=0o700,parents=True,exist_ok=True)
 if runtime.is_symlink() or runtime.stat().st_mode&0o077:raise RuntimeError('unsafe panel runtime directory')
 with open(str(runtime/'desktop-launch.log'),'ab') as log:
  p=subprocess.Popen(['/usr/bin/python3',str(release/'robot/panel/start.py')],env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
 result['launcher_pid']=p.pid
 deadline=time.monotonic()+15
 while time.monotonic()<deadline:
  if p.poll() is not None:result['launcher_exit']=p.returncode;break
  state=runtime/'server-state.json'
  if state.exists():
   try:
    d=json.loads(state.read_text());result['state']=d
    import urllib.request
    url='http://127.0.0.1:'+str(d['port'])+'/'
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));v=json.loads(opener.open(url+'api/instance',timeout=1).read())
    if v.get('instance_id')==d.get('instance_id') and v.get('pid')==d.get('pid'):result['url']=url;break
   except (ValueError,OSError):pass
  time.sleep(.2)
print(json.dumps(result,ensure_ascii=True))
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--launch',action='store_true')
    parser.add_argument('--gamepad-info',help='Explicit project-owned robot_gamepad_info path; defaults to native build manifest')
    parser.add_argument('--sources-manifest',type=Path)
    parser.add_argument('--execution-profile-path',choices=['/home/handsfree/.local/state/rs-executor/authorization.json'])
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    paths=['web/robot-panel/'+n for n in ('index.html','app.mjs','style.css','pages.mjs','usb-observer.mjs','keyboard-control.mjs','control-history.mjs','project-services.mjs','README.md','icon.svg')]+['robot/panel/'+n for n in ('server.py','providers.py','start.py','input_observer.py','system_status.py','keyboard_controller.py','project_services.py','README.md')]+['robot/control/'+n for n in ('__init__.py','input_adapters.py','input_service.py','input_protocol.py','command_history.py','telemetry_service.py','camera_decoder.py','strict_jpeg_decoder.py','owned_source_runner.py','execution_profile.py')]
    paths += ['tools/serve_dashboard.py','tools/camera_stream.py','tools/execution_authorization.py']+['web/'+n for n in ('index.html','styles.css','app.js','live.js','lidar.js','camera.js','execution-banner.js')]
    files={n:(root/n).read_text(encoding='utf-8') for n in paths}
    for n in paths:
        if n.endswith('.py'):ast.parse(files[n],feature_version=(3,6))
    hashes={n:hashlib.sha256(code.encode()).hexdigest() for n,code in files.items()}
    build_record=root/'.local/robot-control/native-build.json'
    if not build_record.exists():
        build_record=root/'.local/learning-checks/M4-011/native-build.json'
    runtime=json.loads(build_record.read_text(encoding='utf-8'))
    if runtime.get('built') is not True:raise RuntimeError('input runtime has not been built')
    info=args.gamepad_info or runtime.get('gamepad_info')
    if not info:
        legacy=root/'.local/learning-checks/M4-007/usb-fixed-info-20261103.json'
        if legacy.exists():
            info=json.loads(legacy.read_text(encoding='utf-8'))['robot']['binary']
    if not info:
        parser.error('gamepad_info missing; rebuild with build_robot_control.py or pass --gamepad-info')
    from pathlib import PurePosixPath
    candidate=PurePosixPath(info)
    base=PurePosixPath('/home/handsfree/.local/share/rs-control-gateway/builds')
    if (candidate.name!='robot_gamepad_info' or candidate.parent.name!='build' or
        candidate.parent.parent.parent!=base or
        not re.fullmatch('[0-9a-f]{16}',candidate.parent.parent.name) or
        str(candidate)!=info):
        parser.error('gamepad_info must be an explicit project build binary')
    configuration={'gamepad_info':info,'probe_ros':False,'input_runtime':{'path':runtime['binary'],'sha256':runtime['binary_sha256']}}
    if args.execution_profile_path:
        configuration['execution_profile']=args.execution_profile_path
    if args.sources_manifest:
        configuration['telemetry_sources']=json.loads(args.sources_manifest.read_text(encoding='utf-8'))
    bundle=hashlib.sha256(json.dumps({'sources':hashes,'config':configuration},sort_keys=True).encode()).hexdigest()[:16]
    spec={'files':files,'hashes':hashes,'bundle':bundle,'gamepad_info':configuration['gamepad_info'],'input_runtime':configuration['input_runtime'],'telemetry_sources':configuration.get('telemetry_sources'),'execution_profile':configuration.get('execution_profile'),'launch':args.launch}
    robot=ROBOT.replace('SPEC',ascii(spec),1).encode('ascii','backslashreplace').decode('ascii')
    server="import subprocess; p=subprocess.run(['ssh','-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','HostKeyAlgorithms=ssh-ed25519','-o','UserKnownHostsFile=/wuhongwei/yq/RS/.dev-access/robot_known_hosts','-o','ConnectTimeout=5','handsfree@10.10.218.121','python3','-'],input="+repr(robot)+".encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=45); print(p.stdout.decode('utf-8')); print(p.stderr.decode('utf-8')); raise SystemExit(p.returncode)"
    p=subprocess.run(['ssh','-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=7','llmemb-server','python3','-'],input=server.encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=55)
    raw=p.stdout.decode('utf-8',errors='replace').strip()
    result={'outer_ssh_exit':p.returncode,'stderr':p.stderr.decode('utf-8',errors='replace')}
    try:result['installation']=json.loads(raw)
    except ValueError:result['output_error']=raw[-4000:]
    folder=root/'.local/learning-checks/M4-007';folder.mkdir(parents=True,exist_ok=True)
    stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    (folder/('panel-install-'+stamp+'.json')).write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True))
    if p.returncode:
        return p.returncode
    if 'installation' not in result or (args.launch and not result['installation'].get('url')):
        return 1
    return 0


if __name__=='__main__':raise SystemExit(main())