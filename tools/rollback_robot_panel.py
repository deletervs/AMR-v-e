"""Manual final-stage rollback of this project's desktop entry; never power/motion."""
import argparse
import json
from pathlib import Path
import re
from observe_usb_guard import run_robot

ROBOT=r"""
import hashlib,json,os,pathlib,re,urllib.request,time
spec=SPEC
base=pathlib.Path('/home/handsfree/.local/share/rs-panel/releases')
release=pathlib.Path(spec['release'])
if base.resolve()!=base or release.parent!=base or not re.fullmatch('[0-9a-f]{16}',release.name) or release.resolve()!=release:
 raise RuntimeError('untrusted release')
for name,digest in spec['hashes'].items():
 relative=pathlib.PurePosixPath(name)
 if relative.is_absolute() or '..' in relative.parts:raise ValueError('manifest path')
 target=release/name
 if target.resolve()!=target or target.stat().st_uid!=os.getuid() or hashlib.sha256(target.read_bytes()).hexdigest()!=digest:
  raise RuntimeError('release source drift')
config=release/'robot/panel/config.json'
if config.is_symlink() or config.stat().st_uid!=os.getuid() or config.stat().st_mode&0o077:
 raise RuntimeError('private release config required')
value=json.loads(config.read_text())
if value.get('execution_profile') is not None:
 raise RuntimeError('rollback target must have no execution profile; choose a non-output release')
state=pathlib.Path('/home/handsfree/.local/state/rs-panel/server-state.json')
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
if state.exists():
 if state.is_symlink() or state.stat().st_uid!=os.getuid() or state.stat().st_mode&0o077:raise RuntimeError('panel state identity')
 old=json.loads(state.read_text());port=old['port']
 if type(port) is not int or not 1<=port<=65535:raise ValueError('panel port')
 origin='http://127.0.0.1:'+str(port)
 info=json.loads(opener.open(origin+'/api/instance',timeout=2).read())
 if info.get('instance_id')!=old['instance_id'] or info.get('pid')!=old['pid']:raise RuntimeError('instance changed')
 token=json.loads(opener.open(origin+'/api/session',timeout=2).read())['csrf_token']
 req=urllib.request.Request(origin+'/api/exit',data=b'{"action":"exit_panel"}',
  headers={'Origin':origin,'Content-Type':'application/json','X-Panel-Token':token},method='POST')
 if opener.open(req,timeout=3).status!=202:raise RuntimeError('exit not submitted')
 deadline=time.monotonic()+8
 while state.exists() and time.monotonic()<deadline:time.sleep(.1)
 if state.exists():raise RuntimeError('old panel exit not confirmed; shortcut not changed')
desktop=pathlib.Path('/home/handsfree/Desktop/RS-Robot-Panel.desktop')
if desktop.is_symlink() or desktop.stat().st_uid!=os.getuid() or 'X-RS-Panel=true' not in desktop.read_text():
 raise RuntimeError('unowned desktop entry')
previous=desktop.read_text()
desktop.with_name('RS-Robot-Panel.desktop.before-rollback').write_text(previous)
text='[Desktop Entry]\nType=Application\nName=RS Robot Panel\nExec=/usr/bin/python3 '+str(release/'robot/panel/start.py')+'\nPath='+str(release)+'\nIcon='+str(release/'web/robot-panel/icon.svg')+'\nTerminal=false\nX-RS-Panel=true\n'
desktop.write_text(text);os.chmod(str(desktop),0o755)
print(json.dumps({'shortcut_release':str(release),'started':False,'physical_stop_confirmed':False,'power_called':False}))
"""

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--installation-record',type=Path,required=True)
    args=p.parse_args()
    if args.installation_record.stat().st_size>65536:p.error('bounded installation record required')
    data=json.loads(args.installation_record.read_text(encoding='utf-8'))
    installed=data.get('installation',{})
    release=installed.get('release','');hashes=installed.get('source_sha256',{})
    if not re.fullmatch('/home/handsfree/.local/share/rs-panel/releases/[0-9a-f]{16}',release) or not hashes:
        p.error('explicit successful installation record required')
    if not all(type(k) is str and type(v) is str and re.fullmatch('[0-9a-f]{64}',v) for k,v in hashes.items()):
        p.error('source manifest')
    result=run_robot(ROBOT.replace('SPEC',ascii({'release':release,'hashes':hashes}),1),25)
    print(result.stdout.decode('utf-8',errors='replace'));print(result.stderr.decode('utf-8',errors='replace'))
    return result.returncode
if __name__=='__main__':raise SystemExit(main())
