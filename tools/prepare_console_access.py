"""Manual PC setup only: writes local credentials/certificate, never starts a service."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',required=True)
    parser.add_argument('--robot-port',required=True,type=int)
    parser.add_argument('--local-port',required=True,type=int)
    parser.add_argument('--port',default=8083,type=int)
    parser.add_argument('--directory',type=Path,default=Path(__file__).resolve().parents[1]/'.local/console-access')
    parser.add_argument('--openssl',default='openssl')
    parser.add_argument('--openssl-config',type=Path)
    args=parser.parse_args()
    host=ipaddress.ip_address(args.host)
    if host.version!=4 or not (host.is_private or host.is_loopback) or host.is_unspecified or host.is_multicast:
        parser.error('explicit private IPv4 required')
    if not all(1<=v<=65535 for v in (args.robot_port,args.local_port,args.port)):
        parser.error('ports')
    directory=args.directory.resolve();directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    token=directory/'access.secret';cert=directory/'console.crt';key=directory/'console.key';config=directory/'launch.json'
    if any(p.exists() for p in (token,cert,key,config)):
        parser.error('refusing to overwrite existing access material; use a new private directory')
    openssl=shutil.which(args.openssl)
    if not host.is_loopback and not openssl:parser.error('OpenSSL required for LAN certificate; nothing started')
    fd=os.open(str(token),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:stream.write(secrets.token_urlsafe(32))
    settings={'host':str(host),'port':args.port,'robot_port':args.robot_port,
        'local_port':args.local_port,'access_token_file':str(token),'allow_control':False,
        'ssh_forward':False,'cert':None,'key':None}
    if not host.is_loopback:
        try:
            openssl_config=args.openssl_config
            if openssl_config is None:
                candidate=Path(openssl).resolve().parent.parent/'ssl/openssl.cnf'
                if candidate.is_file():openssl_config=candidate
            config_args=['-config',str(openssl_config.resolve())] if openssl_config else []
            subprocess.run([openssl,'req','-x509','-newkey','rsa:3072','-nodes','-days','7',
                '-subj','/CN=RSConsole','-addext','subjectAltName=IP:'+str(host),
                '-keyout',str(key),'-out',str(cert)]+config_args,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
            key.chmod(0o600)
        except (OSError,subprocess.SubprocessError):
            raise RuntimeError('certificate generation failed; no service started, retain local files privately')
        settings.update(cert=str(cert),key=str(key))
    fd=os.open(str(config),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:json.dump(settings,stream,indent=2)
    print(json.dumps({'config':str(config),'starts_service':False,'credentials_printed':False}))
if __name__=='__main__':main()
