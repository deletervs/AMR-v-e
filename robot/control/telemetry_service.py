"""Managed read-only native collectors. Explicit start only, no ROS/driver launch."""
import copy
import hashlib
from pathlib import Path
import re
import sys
import threading
import time
TOOLS=Path(__file__).resolve().parents[2]/'tools'
sys.path.insert(0,str(TOOLS))
from serve_dashboard import StateBuffer,StateProducer
from camera_stream import CameraBuffer,CameraProducer
from .camera_decoder import decoder

class TransportBoundStateBuffer(StateBuffer):
    def __init__(self):
        super(TransportBoundStateBuffer,self).__init__(expected_source='robot')
        self.launch=time.monotonic()
        self.transport_age_ms=0
    def publish(self,frame):
        elapsed=frame.get('elapsed_ms') if type(frame) is dict else None
        if type(elapsed) is not int or elapsed<0:
            raise ValueError('source clock')
        wall=(time.monotonic()-self.launch)*1000
        if elapsed>wall+1:
            raise ValueError('source clock from future')
        # Launch precedes native startup: this bound may expire early, never renew a backlog.
        self.transport_age_ms=max(self.transport_age_ms,max(0,wall-elapsed))
        super(TransportBoundStateBuffer,self).publish(frame)
    def snapshot(self):
        value=super(TransportBoundStateBuffer,self).snapshot()
        age=value['stream']['age_ms']
        if age is not None:
            value['stream']['age_ms']=int(age+self.transport_age_ms)
            if value['stream']['status']=='live' and value['stream']['age_ms']>=2000:
                value['stream']['status']='stale'
        return value

class TelemetryService:
    def __init__(self,spec=None):
        self.spec=spec or {}
        if type(self.spec) is not dict or set(self.spec)-{'state','camera'}:
            raise ValueError('collector configuration')
        self.operation=threading.RLock()
        self.lock=threading.Lock()
        self.stop_event=None;self.thread=None;self.producer=None
        self.state=TransportBoundStateBuffer();self.camera=CameraBuffer()
        self.lifecycle='idle' if self.spec else 'not_configured';self.message='需手动启动项目只读采集'
        self.kind=None

    def status(self):
        with self.lock:
            return {'lifecycle':self.lifecycle,'configured':bool(self.spec),'kind':self.kind,
                'running':self.thread is not None and self.thread.is_alive(),'message':self.message}

    def start(self,kind):
        if kind not in ('state','camera') or kind not in self.spec:
            raise ValueError('collector not configured')
        with self.operation:
            self._stop()
            spec=self.spec[kind]
            if type(spec) is not dict or set(spec)!={'path','sha256'} or not re.fullmatch(r'[0-9a-f]{64}',spec['sha256']):
                raise ValueError('collector spec')
            name='robot_state_agent' if kind=='state' else 'robot_camera_stream'
            path=Path(spec['path'])
            if path.name!=name or not str(path).startswith('/home/handsfree/.local/share/rs-') or path.resolve()!=path:
                raise ValueError('collector path')
            if hashlib.sha256(path.read_bytes()).hexdigest()!=spec['sha256']:raise ValueError('collector drift')
            camera_decoder=decoder() if kind=='camera' else None
            stop,ready=threading.Event(),threading.Event();error=[]
            with self.lock:
                self.kind=kind
                self.state=TransportBoundStateBuffer()
                self.camera=CameraBuffer(enabled=kind=='camera',decoder=camera_decoder)
                self.stop_event=stop;self.lifecycle='starting'
                self.thread=threading.Thread(target=self._run,args=(kind,spec,stop,ready,error),daemon=True)
                self.thread.start()
            if not ready.wait(3):self._stop();raise RuntimeError('collector start timeout')
            if error:raise RuntimeError(error[0])
            return self.status()

    def _run(self,kind,spec,stop,ready,error):
        producer=None
        try:
            command=['/usr/bin/python3',str(Path(__file__).with_name('owned_source_runner.py')),spec['path'],spec['sha256']]
            producer=StateProducer(command,self.state) if kind=='state' else CameraProducer(command,self.camera)
            with self.lock:
                self.producer=producer;self.lifecycle='running';self.message='仅本项目采集进程，不说明传感器已正常'
            ready.set()
            while not stop.wait(.2):
                if producer.process.poll() is not None:break
        except Exception as exc:
            error.append(type(exc).__name__)
            with self.lock:self.lifecycle='error';self.message=type(exc).__name__
        finally:
            unexpected=producer is not None and not stop.is_set()
            if producer is not None:producer.close()
            if unexpected:
                failure='project collector ended unexpectedly (exit {})'.format(producer.process.returncode)
                (self.state if kind=='state' else self.camera).finish(failure)
                with self.lock:self.lifecycle='error';self.message=failure
            with self.lock:
                self.producer=None
                if self.lifecycle!='error':self.lifecycle='stopped'
            ready.set()

    def _stop(self):
        with self.lock:stop,thread=self.stop_event,self.thread
        if stop:stop.set()
        if thread:
            thread.join(6)
            if thread.is_alive():raise RuntimeError('collector not reaped; refusing replacement')
        self.state.finish();self.camera.finish()
        with self.lock:self.stop_event=None;self.thread=None;self.lifecycle='stopped'

    def stop(self):
        with self.operation:self._stop();return self.status()

    def snapshot(self):
        # Source quality remains independent. Never turn an expired frame into a fresh one.
        value=self.state.snapshot()
        if value['stream']['status']!='live':value['state']=None
        return value

    def camera_snapshot(self):return self.camera.snapshot()
    def close(self):self.stop()
