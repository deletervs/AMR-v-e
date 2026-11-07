"""Panel-owned input lifecycle. No actuator, ROS, shell or automatic reconnection."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import select
import signal
import stat
import subprocess
import threading
import time
import uuid
from .input_adapters import InputAdapterRegistry, project_binary, enumeration_id
from .input_protocol import validate
from .command_history import CommandHistory
from .execution_profile import load as load_execution_profile

OWNER = 'rs_panel_input_service'


def private_directory(path):
    path.mkdir(mode=0o700,parents=True,exist_ok=True)
    info = path.stat()
    if path.is_symlink() or path.resolve()!=path or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:
        raise ValueError('输入服务目录必须是当前用户私有目录')
    return path


def atomic_json(path, data):
    temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    fd = os.open(str(temporary),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'w') as file:
            json.dump(data,file,ensure_ascii=True,allow_nan=False)
        os.replace(str(temporary),str(path))
    finally:
        if temporary.exists():
            temporary.unlink()


def read_private_json(path):
    fd = os.open(str(path),os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600 or info.st_size>16384:
            raise ValueError('输入服务状态文件无效')
        raw = os.read(fd,16385)
        if len(raw)>16384:
            raise ValueError('输入服务状态文件过长')
        return json.loads(raw.decode('utf-8'))
    finally:
        os.close(fd)


def process_matches(value):
    try:
        pid = value['pid']
        if type(pid) is not int or pid<=1:
            return False
        proc = Path('/proc')/str(pid)
        fields = (proc/'stat').read_text().rsplit(')',1)[1].split()
        return (proc.stat().st_uid==os.getuid() and fields[19]==value['start_ticks']
            and os.readlink(str(proc/'exe'))==value['binary']
            and value['os_boot_id']==Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    except (OSError,ValueError,KeyError,TypeError):
        return False


class InputService:
    """Own one input session; adapters can expand without changing safety rules."""
    def __init__(self, gamepad_info=None, runtime=None, execution_profile=None):
        self._execution_profile=execution_profile
        self._execution_allowed=False
        self._execution_state={"state":"disabled","output_enabled":False,"last_write_ok":False,"physical_stop_confirmed":False}
        self.history = CommandHistory()
        self._lock = threading.RLock()
        self._operation = threading.Lock()
        self._scan_lock = threading.Lock()
        self._registry = InputAdapterRegistry(gamepad_info)
        self._runtime = None
        self._config_error = None
        try:
            if runtime is not None:
                if type(runtime) is not dict or set(runtime)!={'path','sha256'} or not re.fullmatch(r'[0-9a-f]{64}',runtime.get('sha256','')):
                    raise ValueError('输入运行时配置无效')
                self._runtime = {'path':project_binary(runtime['path'],'robot_usb_guard'),'sha256':runtime['sha256']}
        except (OSError,ValueError,TypeError) as error:
            self._config_error = str(error)
        self._private = private_directory(Path.home()/'.local/state/rs-input-service')
        self._observer = private_directory(Path.home()/'.local/state/rs-usb-observer')
        self._slot = os.open(str(self._private/'service.lock'),os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
        self._owns_slot = True
        try:
            fcntl.flock(self._slot,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            self._owns_slot = False
            self._config_error = '已有其它项目实例管理输入服务'
        self._latched = False
        try:
            if (self._private/'stop-state.json').exists():
                saved = read_private_json(self._private/'stop-state.json')
                if type(saved) is not dict or type(saved.get('stop_latched')) is not bool:
                    raise ValueError('停止锁存记录无效')
                self._latched = saved['stop_latched']
        except (OSError,ValueError,TypeError):
            self._latched = True  # Unknown saved stop state requires explicit physical A.
        self._keyboard_open = False
        self._keyboard_boot = None
        self._keyboard_sequence = 0
        self._generation = 0
        self._lifecycle = 'idle'
        self._message = '点击刷新设备，重新识别并建立未授权的输入会话'
        self._devices = []
        self._scanned = 0.
        self._scan_error = None
        self._current = self._last = None
        self._process = self._thread = self._stop_event = None
        self._closed = False
        if not self._runtime or self._config_error:
            self._lifecycle = 'not_configured'
            self._message = self._config_error or '输入运行时未配置，仍可查看已发现的设备'

    def _scan(self, force=False):
        with self._scan_lock:
            if not force and time.monotonic()-self._scanned<1:
                return
            try:
                devices,error = self._registry.scan(),None
            except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired) as exc:
                devices,error = [],str(exc)
            with self._lock:
                self._devices,self._scan_error = devices,error
                self._scanned = time.monotonic()

    def devices(self):
        self._scan()
        with self._lock:
            return {'input_devices_api_version':1,'source':'robot_input_devices',
                'lifecycle':self._lifecycle,'generation':self._generation,
                'devices':list(self._devices),'current_device':self._current,'last_device':self._last,
                'adapters':self._registry.descriptions(),'runtime_configured':self._runtime is not None,
                'refresh_available':self._runtime is not None and self._owns_slot and not self._closed,
                'message':self._scan_error or self._message,'actuator_attached':False,'motion_enabled':False}

    def status(self):
        """Read owned lifecycle only; no discovery, launch or authority changes."""
        with self._lock:
            running = self._process is not None and self._process.poll() is None
            return {'lifecycle': self._lifecycle, 'generation': self._generation,
                'process_running': running, 'pid': self._process.pid if running else None,
                'keyboard_channel_open': self._keyboard_open if running else False,
                'execution_profile_configured':self._execution_profile is not None,
                'execution_allowed':self._execution_allowed if running else False,
                'executor':dict(self._execution_state),
                'current_device': dict(self._current) if self._current else None,
                'stop_latched': self._latched, 'closed': self._closed,
                'refresh_available': self._runtime is not None and self._owns_slot and not self._closed,
                'message': self._message}

    def send_keyboard(self, generation, boot_id, observed_monotonic_ms, action, session_id="0", keys=None):
        """Trusted local adapter only. Enqueued is not native acceptance or movement."""
        now=int(time.monotonic()*1000)
        if type(observed_monotonic_ms) is not int or not 0<=now-observed_monotonic_ms<250:
            raise ValueError('键盘原采样已过期或来自未来')
        if action not in ('arm','keys','release','stop'):
            raise ValueError('键盘通道不允许该动作')
        if type(session_id) is not str or not re.fullmatch(r'0|[1-9][0-9]{0,19}',session_id) or int(session_id)>18446744073709551615:
            raise ValueError('控制会话无效')
        if action=='keys' and (type(keys) is not tuple or len(keys)!=5 or any(type(v) is not bool for v in keys)):
            raise ValueError('需要真实前后左右和使能键')
        if action!='keys' and keys is not None:
            raise ValueError('动作与键值不匹配')
        with self._lock:
            process=self._process
            if (type(generation) is not int or generation!=self._generation or self._closed
                or self._lifecycle!='monitoring' or process is None or process.poll() is not None
                or boot_id!=self._keyboard_boot or self._keyboard_boot is None or not self._keyboard_open):
                raise RuntimeError('键盘目标实例已改变或未就绪')
            self._keyboard_sequence+=1
            sequence=self._keyboard_sequence
            fields=[boot_id,str(sequence),str(observed_monotonic_ms),action]
            if action in ('keys','release'):
                fields.append(session_id)
            if action=='keys':
                fields.extend('1' if v else '0' for v in keys)
            payload=(' '.join(fields)+chr(10)).encode('ascii')
            # One bounded atomic pipe write; never queue/retry/retimestamp old input.
            if len(payload)>160 or os.write(process.stdin.fileno(),payload)!=len(payload):
                raise RuntimeError('键盘通道写入失败')
            self.history.enqueued(generation,boot_id,str(sequence),action)
            return {'status':'enqueued','sequence':str(sequence),'generation':generation}

    def _conflicting_observer(self):
        for path in self._observer.glob('*.json'):
            try:
                record = read_private_json(path)
                if record.get('owner')==OWNER and not process_matches(record):
                    path.unlink()  # Only our stale project markers; never signal unrelated PIDs.
                    continue
            except (OSError,ValueError,AttributeError):
                pass
            return True
        return False

    def _stop_current(self):
        self.history.finish("source_refresh_or_close")
        with self._lock:
            process,thread,event = self._process,self._thread,self._stop_event
            self._current = None
            self._keyboard_boot = None
            if event is not None:
                event.set()
        if process is not None and process.poll() is None:
            process.terminate()
        if thread is not None:
            thread.join(timeout=3)
            if thread.is_alive():
                if process is not None and process.poll() is None:
                    process.kill()
                thread.join(timeout=2)
            if thread.is_alive():
                raise RuntimeError('旧输入实例未完成清理，拒绝开启第二实例')
        with self._lock:
            self._process,self._thread,self._stop_event = None,None,None

    def refresh(self, device_id=None):
        if device_id is not None and (type(device_id) is not str or not re.fullmatch(r'[0-9a-f]{32}',device_id)):
            raise ValueError('设备实例ID无效')
        with self._operation:
            if self._closed or not self._runtime or not self._owns_slot:
                result = self.devices()
                result['refresh_result'] = 'not_configured'
                return result
            try:
                self._stop_current()
                with self._lock:
                    self._generation += 1
                    self._lifecycle = 'stopped'
                    self._message = '旧会话已结束，正在重新识别设备'
                self._scan(force=True)
                with self._lock:
                    devices,error = list(self._devices),self._scan_error
                if error:
                    raise RuntimeError(error)
                eligible = [d for d in devices if d['supported'] and (device_id is None or d['id']==device_id)]
                outcome = None
                if device_id is not None and not any(d['id']==device_id for d in devices):
                    outcome = 'not_found'
                elif not devices:
                    outcome = 'no_device'
                elif not eligible:
                    outcome = 'unsupported'
                elif len(eligible)>1:
                    outcome = 'multiple_devices'
                if outcome:
                    messages = {'not_found':'所选设备已改变，请重新选择后刷新','no_device':'未发现输入设备，插回后点击刷新',
                        'unsupported':'已发现设备，但当前适配器暂不支持其传输或语义布局','multiple_devices':'发现多个兼容设备，请选择具体手柄'}
                    with self._lock:
                        self._lifecycle = 'ambiguous' if outcome=='multiple_devices' else 'disconnected'
                        self._message = messages[outcome]
                    result = self.devices();result['refresh_result']=outcome
                    return result
                if self._conflicting_observer():
                    with self._lock:
                        self._lifecycle,self._message = 'error','外部观测仍占用状态源，请先结束该观测'
                    result=self.devices();result['refresh_result']='busy_external';return result
                device = eligible[0]
                runtime = self._runtime
                if hashlib.sha256(Path(runtime['path']).read_bytes()).hexdigest()!=runtime['sha256']:
                    raise RuntimeError('输入运行时指纹变化，拒绝启动')
                adapter = self._registry.adapter(device['adapter_id'])
                args = [runtime['path']]+adapter.launch_arguments(device)
                if self._latched:
                    args.append('--initial-stop')
                event,ready = threading.Event(),threading.Event()
                startup = {}
                with self._lock:
                    generation = self._generation
                    self._stop_event = event
                    self._current = self._last = device
                    self._lifecycle,self._message = 'starting','已重新识别，等待真实输入；控制权尚未申请'
                    thread = threading.Thread(target=self._run_input,
                        args=(args,event,ready,startup,device,generation),daemon=True)
                    self._thread=thread
                    thread.start()
                if not ready.wait(timeout=2):
                    self._stop_current()
                    raise RuntimeError('输入工作线程启动超时')
                if startup.get('error'):
                    self._stop_current()
                    raise RuntimeError(startup['error'])
                result=self.devices();result['refresh_result']='started';return result
            except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired) as error:
                with self._lock:
                    self._lifecycle,self._message = 'error',str(error)
                result=self.devices();result['refresh_result']='error';return result

    def _run_input(self, args, stop_event, ready, startup, device, generation):
        # PR_SET_PDEATHSIG follows the creating *thread*. This owner survives the HTTP request.
        process = marker = None
        observing = False
        try:
            if stop_event.is_set():
                raise RuntimeError('输入启动已取消')
            profile=load_execution_profile(self._execution_profile,self._runtime,read_private_json)
            launch=list(args)
            if profile:launch+=['--execute-driver',profile['driver_node'],'--execute-until',str(profile['expires_monotonic_ms'])]
            process = subprocess.Popen(launch+['--keyboard-stdin'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            flags=fcntl.fcntl(process.stdin.fileno(),fcntl.F_GETFL)
            fcntl.fcntl(process.stdin.fileno(),fcntl.F_SETFL,flags|os.O_NONBLOCK)
            proc = Path('/proc')/str(process.pid)
            identity = {'owner':OWNER,'pid':process.pid,
                'start_ticks':(proc/'stat').read_text().rsplit(')',1)[1].split()[19],
                'binary':self._runtime['path'],'os_boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                'phase':'panel_input','generation':generation,'device':device,'execution_allowed':profile is not None,
                'execution_until_ms':profile['expires_monotonic_ms'] if profile else None,
                'execution_driver':profile['driver_node'] if profile else None}
            marker = self._observer/(uuid.uuid4().hex+'.json')
            atomic_json(marker,identity)
            with self._lock:
                self._process=process
                self._keyboard_open=False
                self._keyboard_boot=None
                self._keyboard_sequence=0
            if stop_event.is_set():
                raise RuntimeError('输入启动已取消')
            ready.set()
            observing=True
            self._observe(process,stop_event,identity,marker,generation)
        except Exception as error:
            startup['error']=str(error)
            with self._lock:
                if generation==self._generation:
                    self._current=None
                    self._lifecycle,self._message='error',str(error)
        finally:
            if not observing and process is not None:
                if process.poll() is None:
                    process.terminate()
                    try:process.wait(timeout=2)
                    except subprocess.TimeoutExpired:process.kill();process.wait(timeout=2)
                process.stdin.close();process.stdout.close();process.stderr.close()
                if marker is not None and marker.exists():
                    marker.unlink()
            ready.set()

    def _observe(self, process, stop_event, identity, marker, generation):
        pending,errors = bytearray(),bytearray()
        last = None
        failure = None
        binding_checked = False
        try:
            streams = {process.stdout.fileno():'out',process.stderr.fileno():'err'}
            for fd in streams:
                fcntl.fcntl(fd,fcntl.F_SETFL,fcntl.fcntl(fd,fcntl.F_GETFL)|os.O_NONBLOCK)
            while streams:
                if identity.get('execution_allowed') and not stop_event.is_set():
                    try:
                        policy=load_execution_profile(self._execution_profile,self._runtime,read_private_json)
                        valid=policy and policy['expires_monotonic_ms']==identity['execution_until_ms'] and policy['driver_node']==identity['execution_driver']
                    except (OSError,ValueError,TypeError):
                        valid=False
                    if not valid:
                        stop_event.set()
                        if process.poll() is None:process.terminate()
                ready,_,_ = select.select(list(streams),[],[],.1)
                for fd in ready:
                    try:
                        data = os.read(fd,16384)
                    except BlockingIOError:
                        continue
                    if not data:
                        if streams[fd]=='out' and pending:
                            raise ValueError('输入尾帧不完整')
                        del streams[fd]
                        continue
                    if streams[fd]=='err':
                        errors.extend(data);del errors[:-4096];continue
                    pending.extend(data)
                    while b'\n' in pending:
                        line,_,tail = pending.partition(b'\n');pending=bytearray(tail)
                        if len(line)>4096:
                            raise ValueError('输入帧过长')
                        frame = validate(json.loads(line.decode('utf-8')),identity['os_boot_id'],identity.get('execution_allowed',False))
                        if last and (frame['boot_id']!=last['boot_id'] or frame['sequence']!=last['sequence']+1
                            or frame['elapsed_ms']<last['elapsed_ms']):
                            raise ValueError('输入实例时钟或序号异常')
                        if frame['running'] and frame['initialized'] and not binding_checked:
                            device=identity['device']
                            node=os.lstat(device['path'])
                            if not stat.S_ISCHR(node.st_mode) or enumeration_id(device['path'],node)!=device['id']:
                                raise RuntimeError('识别期间设备实例已改变，请重新刷新')
                            binding_checked=True
                        last=frame
                        published=dict(identity);published.update(latest=frame,received_monotonic_ms=int(time.monotonic()*1000))
                        atomic_json(marker,published)
                        with self._lock:
                            if generation==self._generation:
                                self._execution_allowed=frame["actuator_attached"]
                                self._execution_state=frame.get("executor",{"state":"disabled","output_enabled":False,"last_write_ok":False,"physical_stop_confirmed":False})
                                self.history.observe(generation,frame)
                                self._keyboard_boot=frame["boot_id"]
                                self._keyboard_open=frame.get("keyboard_channel_open",False)
                                if self._latched!=frame['stop_latched']:
                                    self._latched=frame['stop_latched']
                                    atomic_json(self._private/'stop-state.json',{'stop_latched':self._latched})
                                if frame['running'] and frame['initialized'] and not stop_event.is_set():
                                    self._lifecycle='monitoring'
                                    self._message='输入已接入；刷新不会自动申请控制权'
                    if len(pending)>4096:
                        raise ValueError('输入残帧过长')
            process.wait(timeout=2)
        except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired) as error:
            failure=str(error)
        finally:
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=2)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=2)
            process.stdin.close();process.stdout.close();process.stderr.close()
            try:
                record=read_private_json(marker)
                if record.get('owner')==OWNER and record.get('pid')==process.pid:
                    marker.unlink()
            except (OSError,ValueError,AttributeError):
                pass
            with self._lock:
                if generation==self._generation:
                    self.history.finish("source_ended")
                    self._current=None
                    if stop_event.is_set():
                        self._lifecycle,self._message='stopped','输入会话已结束；未恢复旧控制权'
                    elif failure or process.returncode:
                        self._lifecycle,self._message='error',failure or errors.decode(errors='replace')[:256] or '输入进程异常结束'
                    elif last and last['runtime_reason']=='Disconnected':
                        self._lifecycle,self._message='disconnected','手柄已断开；插回后点击刷新设备'
                    else:
                        self._lifecycle,self._message='stopped','输入会话已结束，点击刷新可重新识别'

    def stop(self):
        """Stop only this owned input session; retain refresh capability and B latch."""
        with self._operation:
            if self._closed:
                raise RuntimeError('输入服务已关闭')
            self._stop_current()
            with self._lock:
                self._generation+=1
                self._lifecycle='stopped'
                self._message='项目输入已停止；未确认整机物理停止，重新识别不会自动授权'
            return self.status()

    def close(self):
        with self._operation:
            if self._closed:
                return
            self._closed=True
            self._stop_current()
            if self._slot is not None:
                os.close(self._slot);self._slot=None