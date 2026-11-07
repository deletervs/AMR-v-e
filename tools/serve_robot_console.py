"""Manual private-network console proxy. No autostart, arbitrary URLs or power route."""
import argparse
import hashlib
import hmac
import http.client
import ipaddress
import json
from pathlib import Path
import secrets
import socket
import ssl
import subprocess
import threading
import time
from http.cookies import SimpleCookie,CookieError
from http.server import BaseHTTPRequestHandler,HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs

GET_PATHS={'/','/index.html','/app.mjs','/pages.mjs','/usb-observer.mjs','/keyboard-control.mjs',
    '/control-history.mjs','/project-services.mjs','/style.css','/dashboard/','/dashboard/index.html',
    '/styles.css','/app.js','/live.js','/lidar.js','/camera.js','/execution-banner.js','/api/instance','/api/session',
    '/api/panel','/api/input-observer','/api/input-devices','/api/system','/api/keyboard','/api/control-events',
    '/api/services','/api/state','/api/camera'}
GET_PATHS.update('/dashboard/'+name for name in ('styles.css','app.js','live.js','lidar.js','camera.js','execution-banner.js','api/state','api/camera'))
POST_PATHS={'/api/keyboard','/api/input-refresh','/api/services','/api/exit'}
LOGIN=b"""<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>RS console access</title><h1>RS robot console</h1><form method="post" action="/bridge/login">
<label>Access code <input type="password" name="code" required autocomplete="off"></label>
<button type="submit">Open console</button></form></html>"""

class Server(ThreadingMixIn,HTTPServer):
    daemon_threads=True

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def setup(self):
        super(Handler,self).setup();self.connection.settimeout(3)
    def send(self,code,body,mime='application/json',extra=()):
        if not isinstance(body,bytes):body=json.dumps(body,ensure_ascii=False,allow_nan=False).encode()
        self.send_response(code)
        for k,v in [('Content-Type',mime),('Content-Length',str(len(body))),('Cache-Control','no-store'),
            ('X-Content-Type-Options','nosniff'),('X-Frame-Options','DENY'),
            ('Content-Security-Policy',"default-src 'self'; connect-src 'self'; img-src 'self' data: blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")]+list(extra):
            self.send_header(k,v)
        self.end_headers();self.wfile.write(body)
    def trusted(self,post=False):
        return self.headers.get_all('Host')==[self.server.authority] and (
            not post or self.headers.get_all('Origin')==[self.server.origin])
    def authorized(self):
        raw=self.headers.get_all('Cookie') or []
        if len(raw)>1 or sum(map(len,raw))>4096:return False
        try:
            cookie=SimpleCookie();cookie.load(raw[0] if raw else '')
            token=cookie['rs_console_access'].value
        except (KeyError,ValueError,CookieError):return False
        with self.server.auth_lock:
            now=time.monotonic()
            self.server.sessions={k:v for k,v in self.server.sessions.items() if v>now}
            return token in self.server.sessions
    def do_GET(self):
        if not self.trusted():self.send(403,{});return
        if self.path=='/bridge/login':self.send(200,LOGIN,'text/html; charset=utf-8');return
        if not self.authorized():
            self.send(303,b'','text/plain',[('Location','/bridge/login')]);return
        if self.path=='/bridge/status':
            self.send(200,{'bridge_api_version':1,'control_allowed':self.server.control,
                'power_allowed':False,'upstream':'robot_loopback','motion_authorized_by_bridge':False});return
        path=self.path.split('?',1)[0]
        if path not in GET_PATHS:self.send(404,{});return
        if path=='/api/session' and not self.server.control:self.send(403,{'error':'read-only connection'});return
        # Only the known dashboard query is supported; no URL/path proxy parameters.
        if '?' in self.path and self.path!='/dashboard/?mode=live&source=robot':
            self.send(400,{});return
        self.proxy('GET')
    def do_POST(self):
        if not self.trusted(True):self.send(403,{});return
        sizes=self.headers.get_all('Content-Length') or []
        if len(sizes)!=1 or not sizes[0].isdigit() or not 0<int(sizes[0])<=512 or self.headers.get('Transfer-Encoding'):
            self.send(400,{});return
        body=self.rfile.read(int(sizes[0]))
        if self.path=='/bridge/login':
            if self.headers.get_all('Content-Type')!=['application/x-www-form-urlencoded']:
                self.send(400,{});return
            try:
                data=parse_qs(body.decode('utf-8'),strict_parsing=True)
                if set(data)!={'code'} or len(data['code'])!=1:raise ValueError()
                valid=hmac.compare_digest(data['code'][0].encode(),self.server.access)
            except (ValueError,UnicodeError):valid=False
            if not valid:self.send(403,{});return
            token=secrets.token_hex(32)
            with self.server.auth_lock:
                now=time.monotonic()
                self.server.sessions={k:v for k,v in self.server.sessions.items() if v>now}
                if len(self.server.sessions)>=32:self.send(503,{});return
                self.server.sessions[token]=now+3600
            flags='; HttpOnly; SameSite=Strict; Path=/'
            if self.server.tls:flags+='; Secure'
            self.send(303,b'','text/plain',[('Set-Cookie','rs_console_access='+token+flags),('Location','/')]);return
        if not self.authorized() or not self.server.control:self.send(403,{});return
        if self.path not in POST_PATHS:self.send(403,{});return
        if self.headers.get_all('Content-Type')!=['application/json']:self.send(400,{});return
        token=self.headers.get_all('X-Panel-Token') or []
        if len(token)!=1 or len(token[0])>128:self.send(403,{});return
        self.proxy('POST',body,token[0])
    def proxy(self,method,body=None,token=None):
        upstream=http.client.HTTPConnection('127.0.0.1',self.server.local_port,timeout=2)
        authority='127.0.0.1:'+str(self.server.robot_port)
        headers={'Host':authority,'Cache-Control':'no-store'}
        if method=='POST':headers.update({'Origin':'http://'+authority,'Content-Type':'application/json','X-Panel-Token':token})
        try:
            upstream.request(method,self.path,body=body,headers=headers)
            response=upstream.getresponse();data=response.read(2*1024*1024+1)
            if len(data)>2*1024*1024:raise ValueError('response limit')
            mime=response.getheader('Content-Type','application/octet-stream')
            if method=='GET' and not self.server.control and mime.startswith('application/json'):
                value=json.loads(data)
                value.pop("csrf_token",None)
                if self.path=='/api/keyboard':
                    value['available']=False;value['reason']='当前连接仅监控，无控制权限'
                elif self.path=='/api/input-devices':value['refresh_available']=False
                elif self.path=='/api/services':
                    for service in value.get('services',[]):service['actions']=[]
                elif self.path=='/api/system':
                    for name in ('refresh_input','exit_panel'):
                        if name in value.get('capabilities',{}):value['capabilities'][name]['allowed']=False
                data=json.dumps(value,ensure_ascii=False,allow_nan=False).encode()
            self.send(response.status,data,mime)
        except (OSError,ValueError,http.client.HTTPException):
            self.send(502,{'error':'robot link unavailable; no request was retried'})
        finally:upstream.close()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',default='127.0.0.1');parser.add_argument('--port',type=int,default=8083)
    parser.add_argument('--robot-port',type=int,required=True)
    parser.add_argument('--local-port',type=int,required=True)
    parser.add_argument('--access-token-file',type=Path,required=True)
    parser.add_argument('--cert',type=Path);parser.add_argument('--key',type=Path)
    parser.add_argument('--allow-control',action='store_true')
    parser.add_argument('--ssh-forward',action='store_true')
    args=parser.parse_args()
    address=ipaddress.ip_address(args.host)
    if address.version!=4 or address.is_unspecified or address.is_multicast or not (address.is_private or address.is_loopback):
        parser.error('explicit private IPv4 bind required')
    if not all(1<=v<=65535 for v in (args.port,args.robot_port,args.local_port)):
        parser.error('ports must be1..65535')
    tls=bool(args.cert and args.key)
    if bool(args.cert)!=bool(args.key) or (not address.is_loopback and not tls):
        parser.error('LAN/mobile access requires an explicit TLS certificate and private key')
    if args.access_token_file.is_symlink() or args.access_token_file.stat().st_size>256:
        parser.error('access token file must be a bounded local file')
    access=args.access_token_file.read_bytes().strip()
    if len(access)<32:parser.error('access token must have at least32 random bytes/characters')
    forwarding=None
    try:
        if args.ssh_forward:
            proxy='ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes -W %h:%p llmemb-server'
            forwarding=subprocess.Popen(['ssh','-N','-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
                '-o','HostKeyAlgorithms=ssh-ed25519','-o','ExitOnForwardFailure=yes','-o','ConnectTimeout=7',
                '-o','ProxyCommand='+proxy,'-L','127.0.0.1:'+str(args.local_port)+':127.0.0.1:'+str(args.robot_port),
                'handsfree@10.10.218.121'],stdin=subprocess.DEVNULL)
        with Server((args.host,args.port),Handler) as server:
            server.authority=args.host+':'+str(args.port);server.tls=tls
            server.origin=('https://' if tls else 'http://')+server.authority
            server.local_port=args.local_port;server.robot_port=args.robot_port
            server.control=args.allow_control;server.access=access
            server.auth_lock=threading.Lock();server.sessions={}
            if tls:
                context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                context.load_cert_chain(str(args.cert),str(args.key))
                server.socket=context.wrap_socket(server.socket,server_side=True)
            print(server.origin+'/bridge/login',flush=True) # Never print credentials.
            server.serve_forever(.2)
    except KeyboardInterrupt:pass
    finally:
        if forwarding:
            forwarding.terminate()
            try:forwarding.wait(3)
            except subprocess.TimeoutExpired:forwarding.kill();forwarding.wait(3)

if __name__=='__main__':main()
