"""Trusted input adapter registry. Device discovery never starts an input session."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess

REQUIRED_AXES = {0, 1}  # Linux ABS_X / ABS_Y, independent of array indices.
REQUIRED_BUTTONS = {311, 315, 314, 305, 304}  # RB, Start, Back, B, A.


def project_binary(value, name):
    path = Path(value)
    pattern = r'/home/handsfree/\.local/share/rs-control-gateway/builds/[A-Za-z0-9_-]+/build/' + re.escape(name)
    if not re.fullmatch(pattern, str(path)) or str(path.resolve()) != str(path) or not path.is_file():
        raise ValueError('Input executable must be a fixed project-owned build: '+name)
    return str(path)


def enumeration_id(path, info):
    identity = '{}:{}:{}:{}'.format(path,info.st_dev,info.st_ino,info.st_rdev)
    return hashlib.sha256(identity.encode()).hexdigest()[:32]


def hex_attribute(directory, name):
    try:
        value = (directory/'id'/name).read_text().strip().lower()
        return value if re.fullmatch(r'[0-9a-f]{4}', value) else None
    except OSError:
        return None


class LinuxJoydevUsbAdapter:
    """Interface: describe(), accepts(device), launch_arguments(device)."""
    adapter_id = 'linux_joydev_usb'

    def describe(self):
        return {'id':self.adapter_id,'label':'Linux joydev USB语义适配','transport':'usb','implemented':True}

    def accepts(self, device):
        return (device['transport']=='usb' and device['readable'] and
            REQUIRED_AXES.issubset(device['_axes']) and REQUIRED_BUTTONS.issubset(device['_buttons']))

    def launch_arguments(self, device):
        if device.get('adapter_id') != self.adapter_id or not device['supported']:
            raise ValueError('Device does not match this trusted adapter')
        return ['--device', device['path'], '--duration-seconds', '0']


class InputAdapterRegistry:
    def __init__(self, gamepad_info=None):
        self.gamepad_info = project_binary(gamepad_info, 'robot_gamepad_info') if gamepad_info else None
        # Future transports register another trusted adapter here; no URL or model code loading.
        self.adapters = [LinuxJoydevUsbAdapter()]

    def descriptions(self):
        return [a.describe() for a in self.adapters]+[
            {'id':'bluetooth_reserved','label':'蓝牙输入（预留，未接入）','transport':'bluetooth','implemented':False}]

    def adapter(self, adapter_id):
        return next((a for a in self.adapters if a.adapter_id==adapter_id),None)

    def scan(self):
        if not self.gamepad_info:
            raise RuntimeError('未配置原生设备识别器')
        p = subprocess.run([self.gamepad_info], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=2)
        if p.returncode or len(p.stdout)>32768:
            raise RuntimeError('设备识别器异常')
        data = json.loads(p.stdout.decode('utf-8'))
        if (data.get('source')!='robot_gamepad_info' or data.get('actuator_attached') is not False
            or data.get('motion_enabled') is not False or type(data.get('devices')) is not list or len(data['devices'])>32):
            raise ValueError('设备识别协议无效')
        result = []
        for native in data['devices']:
            path = native.get('path','')
            if not re.fullmatch(r'/dev/input/js[0-9]{1,2}',path):
                raise ValueError('设备节点无效')
            try:
                info = os.lstat(path)
                if not stat.S_ISCHR(info.st_mode):
                    continue
                directory = Path('/sys/class/input')/Path(path).name/'device'
                bus = hex_attribute(directory,'bustype')
                transport = {'0003':'usb','0005':'bluetooth'}.get(bus,'unknown')
                axes,buttons = native.get('axis_codes',[]),native.get('button_codes',[])
                if (type(axes) is not list or type(buttons) is not list or len(axes)>64 or len(buttons)>768
                    or any(type(x) is not int or not 0<=x<=65535 for x in axes+buttons)):
                    raise ValueError('轴/按键语义码无效')
                name = native.get('name_bytes','名称不可读')
                if type(name) is not str or len(name)>1024:
                    raise ValueError('设备名称无效')
                # This ID identifies an enumeration instance, not a permanent device identity.
                device = {'id':enumeration_id(path,info),
                    'name':name,'path':path,'transport':transport,
                    'vendor_id':hex_attribute(directory,'vendor'),'product_id':hex_attribute(directory,'product'),
                    'version':hex_attribute(directory,'version'),'axis_count':len(axes) if native.get('metadata_valid') is True else None,'button_count':len(buttons) if native.get('metadata_valid') is True else None,
                    'adapter_id':None,'supported':False,'readable':native.get('readable') is True and native.get('metadata_valid') is True,
                    'reason':'暂未适配此输入布局','_axes':axes,'_buttons':buttons}
                for adapter in self.adapters:
                    if adapter.accepts(device):
                        device.update(adapter_id=adapter.adapter_id,supported=True,
                            reason='语义布局兼容；具体型号仍以实际验证为准')
                        break
                if transport=='bluetooth':
                    device['reason']='蓝牙适配预留；节点存在不代表已配对或已连接'
                if not device['readable']:
                    device['reason']='输入节点不可读或元数据无效'
                device.pop('_axes')
                device.pop('_buttons')
                result.append(device)
            except FileNotFoundError:
                continue  # A device removed during this enumeration is not current.
        return result