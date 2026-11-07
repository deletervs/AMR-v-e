"""Run manually ON ROBOT only during the final authorized, watched stage."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
import urllib.request
import uuid

PROFILE=Path('/home/handsfree/.local/state/rs-executor/authorization.json')
def write(value):
    directory=PROFILE.parent;directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    info=directory.stat()
    if directory.is_symlink() or directory.resolve()!=directory or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:
        raise ValueError('private executor directory required')
    temp=directory/('authorization.'+uuid.uuid4().hex+'.tmp')
    fd=os.open(str(temp),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'w') as stream:json.dump(value,stream)
        os.replace(str(temp),str(PROFILE))
    finally:
        if temp.exists():temp.unlink()

def stop_input():
    path=Path('/home/handsfree/.local/state/rs-panel/server-state.json')
    if path.is_symlink() or path.stat().st_uid!=os.getuid() or path.stat().st_mode&0o077:
        raise ValueError('private panel identity required')
    state=json.loads(path.read_text())
    port=state['port']
    if type(port) is not int or not 1<=port<=65535:raise ValueError('panel port')
    origin='http://127.0.0.1:'+str(port)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    instance=json.loads(opener.open(origin+'/api/instance',timeout=2).read())
    if instance.get('instance_id')!=state['instance_id'] or instance.get('pid')!=state['pid']:
        raise ValueError('panel instance changed')
    token=json.loads(opener.open(origin+'/api/session',timeout=2).read())['csrf_token']
    request=urllib.request.Request(origin+'/api/services',
        data=b'{"service":"input","action":"stop"}',
        headers={'Origin':origin,'Content-Type':'application/json','X-Panel-Token':token},method='POST')
    result=json.loads(opener.open(request,timeout=4).read())
    if result.get('status')!='completed':raise RuntimeError('input stop not confirmed')
    return 'project_input_stop_completed'

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare-disabled','authorize','revoke'])
    parser.add_argument('--binary',type=Path)
    parser.add_argument('--driver-node')
    parser.add_argument('--duration-seconds',type=int,default=60)
    parser.add_argument('--confirm-motion-authorization',action='store_true')
    parser.add_argument('--confirm-on-floor-and-watched',action='store_true')
    args=parser.parse_args()
    value={'version':1,'enabled':False,'operator_confirmed':False,'os_boot_id':None,
        'binary_sha256':None,'expires_monotonic_ms':None,'driver_node':None}
    if args.action=='authorize':
        if not args.confirm_motion_authorization or not args.confirm_on_floor_and_watched:
            parser.error('explicit motion authorization and floor/watch confirmation required')
        path=args.binary
        if path is None or path.name!='robot_usb_guard' or path.resolve()!=path or not str(path).startswith('/home/handsfree/.local/share/rs-control-gateway/builds/'):
            parser.error('explicit project native binary required')
        info=path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o022:
            parser.error('native binary ownership/mode')
        if not args.driver_node or not re.fullmatch(r'/[A-Za-z_][A-Za-z0-9_/]{0,126}',args.driver_node) or not 1<=args.duration_seconds<=300:
            parser.error('exact vendor driver node and1..300second period required')
        value.update(enabled=True,operator_confirmed=True,
            os_boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            binary_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            expires_monotonic_ms=int(time.monotonic()*1000)+args.duration_seconds*1000,
            driver_node=args.driver_node)
    write(value)
    result={'profile_enabled':value['enabled'],'starts_runtime':False,'grants_guard_control':False,
        'physical_stop_confirmed':False}
    if args.action=='revoke':
        try:result['runtime_stop']=stop_input()
        except (OSError,ValueError,RuntimeError,KeyError):result['runtime_stop']='unconfirmed'
    print(json.dumps(result))
if __name__=='__main__':main()
