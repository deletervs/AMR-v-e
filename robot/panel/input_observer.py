"""Read only the private USB observer marker. Never signals or starts a process."""
import json
import math
import os
from pathlib import Path
import re
import stat
import threading
import time

DIRECTORY = '/home/handsfree/.local/state/rs-usb-observer'


from control.input_protocol import integer, validate


class InputObserver:
    def __init__(self):
        self.lock = threading.Lock()
        self.last = None

    def read(self):
        with self.lock:
            result = {'observer_api_version':1,'source':'robot_input_observer','fresh':False,'status':'unavailable','phase':'—','snapshot':None,'total_age_ms':None,'run_id':None}
            directory = descriptor = None
            try:
                if str(Path(DIRECTORY).resolve()) != DIRECTORY:
                    raise ValueError('观测目录不能是符号链接')
                directory = os.open(DIRECTORY, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                info = os.fstat(directory)
                if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                    raise ValueError('观测目录权限或UID不匹配')
                names = [n for n in os.listdir(directory) if n.endswith('.json')]
                if not names:
                    result.update(status='not_running',reason='未开启USB观测'); return result
                if len(names) != 1 or not re.fullmatch(r'[0-9a-f]{32}\.json', names[0]):
                    raise ValueError('拒绝多个或非法观测标记')
                descriptor = os.open(names[0], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 16384:
                    raise ValueError('观测文件权限/类型/长度不匹配')
                raw = os.read(descriptor, 16385)
                if len(raw) > 16384:
                    raise ValueError('观测文件过长')
                def unique(pairs):
                    obj = {}
                    for k,v in pairs:
                        if k in obj: raise ValueError('重复字段')
                        obj[k] = v
                    return obj
                marker = json.loads(raw.decode('utf-8'), object_pairs_hook=unique)
                boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
                if marker.get('os_boot_id') != boot or not integer(marker.get('pid')) or marker['pid'] == 0 or not str(marker.get('start_ticks','')).isdigit() or type(marker.get('binary')) is not str or not marker['binary'].startswith('/'):
                    raise ValueError('观测身份元数据无效')
                process = Path('/proc')/str(marker['pid'])
                process_stat = (process/'stat').read_text()
                if process.stat().st_uid != os.getuid() or process_stat[process_stat.rfind(')')+2:].split()[19] != str(marker['start_ticks']) or os.readlink(str(process/'exe')) != marker['binary']:
                    raise ValueError('观测进程身份不匹配')
                s = validate(marker.get('latest'), boot, marker.get('owner')=='rs_panel_input_service' and marker.get('execution_allowed') is True)
                received = marker.get('received_monotonic_ms')
                now = int(time.monotonic()*1000)
                if not integer(received) or received > now:
                    raise ValueError('接收时钟无效/来自未来')
                phase = marker.get('phase','未标记')
                if type(phase) is not str or len(phase)>256: raise ValueError('阶段无效')
                run = names[0][:-5]
                instance = '{}:{}:{}'.format(run, marker['pid'], marker['start_ticks'])
                changed = self.last is not None and self.last[0] != instance
                if self.last and self.last[0] == instance and (self.last[1] != s['boot_id'] or s['sequence'] < self.last[2] or s['elapsed_ms'] < self.last[3] or s['source_observed_ms'] < self.last[4] or received < self.last[5]):
                    raise ValueError('同一观测的boot/时钟/序号倒退')
                self.last = (instance,s['boot_id'],s['sequence'],s['elapsed_ms'],s['source_observed_ms'],received)
                age = s['source_age_ms']+max(0,now-received)
                result.update(run_id=run,instance_id=instance,phase=phase,total_age_ms=age,reason=s['runtime_reason']+' / '+s['stop_reason']+' / '+s['guard_expiry_reason'])
                if changed:
                    result.update(status='source_changed',reason='观测进程实例已切换；旧当前值已撤销')
                elif not s['running']:
                    result['status']='ended'
                elif not s['local_input_available'] or age >= 250 or not s['source_sequence']:
                    result['status']='expired'
                else:
                    result.update(status='fresh',fresh=True,snapshot=s)
                return result
            except FileNotFoundError:
                result.update(status='not_running',reason='观测目录或标记不存在')
            except (OSError, ValueError, TypeError, AttributeError, IndexError) as exc:
                result.update(status='invalid',reason=str(exc)[:256])
            finally:
                if descriptor is not None: os.close(descriptor)
                if directory is not None: os.close(directory)
            return result
