"""Canonical no-actuator native input protocol; no I/O or startup."""
import math
import re


def integer(v):
    return type(v) is int and 0 <= v <= 9007199254740991


def uid(v):
    return type(v) is str and re.fullmatch(r'0|[1-9][0-9]{0,19}', v) is not None and int(v) <= 18446744073709551615


def validate(s, boot, allow_execution=False):
    if type(s) is not dict or s.get('source') != 'robot_usb_guard' or type(s.get('api_version')) is not int or s['api_version'] not in (2,3) or s.get('os_boot_id') != boot:
        raise ValueError('原生来源/系统boot不匹配')
    for key in ('elapsed_ms','sequence','reader_pid','source_sequence','source_observed_ms','source_age_ms','init_events','change_events','lease_remaining_ms','command_remaining_ms'):
        if not integer(s.get(key)):
            raise ValueError('原生时钟或序号无效')
    if s['source_observed_ms'] > s['elapsed_ms'] or (s['source_sequence'] and s['source_age_ms'] != s['elapsed_ms']-s['source_observed_ms']):
        raise ValueError('源时钟关系无效')
    if s.get('last_event_elapsed_ms') is not None and (not integer(s['last_event_elapsed_ms']) or s['last_event_elapsed_ms'] > s['elapsed_ms']):
        raise ValueError('事件时间无效')
    for key in ('boot_id','session_id','controller_id','command_sequence'):
        if not uid(s.get(key)):
            raise ValueError('原生ID无效')
    for key in ('actuator_attached','motion_enabled','running','initialized','guard_armed','stop_latched','neutral_confirmed','local_input_available'):
        if type(s.get(key)) is not bool:
            raise ValueError('原生布尔字段无效')
    if s['api_version']==2 and (s['actuator_attached'] or s['motion_enabled']):
        raise ValueError('API2不允许执行器')
    if s['api_version']==3 and (allow_execution is not True or s['actuator_attached'] is not True):
        raise ValueError('未获得本机执行授权')
    if 'executor' in s:
        value=s['executor']
        if (type(value) is not dict or set(value)!={'state','output_enabled','last_write_ok','physical_stop_confirmed'}
            or value['state'] not in ('disabled','waiting','zero_requested','target_requested','fault','closed')
            or any(type(value[k]) is not bool for k in ('output_enabled','last_write_ok','physical_stop_confirmed'))
            or value['physical_stop_confirmed'] or value['output_enabled']!=s['motion_enabled']):
            raise ValueError('执行状态字段不一致')
    elif s['api_version']==3:
        raise ValueError('缺少执行状态')
    raw = s.get('raw')
    if type(raw) is not dict or set(raw) != {'x','y','rb','start','back','b_stop','a_clear'}:
        raise ValueError('原始输入字段无效')
    for k in ('x','y'):
        if raw[k] is not None and (type(raw[k]) is not int or not -32768 <= raw[k] <= 32767):
            raise ValueError('原始轴范围无效')
    if any(raw[k] is not None and type(raw[k]) is not bool for k in ('rb','start','back','b_stop','a_clear')):
        raise ValueError('原始按钮无效')
    c = s.get('candidate')
    if type(c) is not dict or set(c) != {'linear_mps','angular_radps'} or any(type(v) not in (int,float) or not math.isfinite(v) for v in c.values()) or abs(c['linear_mps']) > .25 or abs(c['angular_radps']) > .6:
        raise ValueError('候选值无效')
    for k in ('stop_reason','guard_expiry_reason','runtime_reason','action','result'):
        if type(s.get(k)) is not str or len(s[k]) > 256:
            raise ValueError('原因字段无效')
    if 'control_owner' in s:
        if (s['control_owner'] not in ('none','keyboard','gamepad')
            or type(s.get('keyboard_channel_open')) is not bool
            or not uid(s.get('keyboard_sequence')) or type(s.get('keyboard_result')) is not str
            or len(s['keyboard_result'])>256):
            raise ValueError('共享控制来源元数据无效')
    receipts=s.get('keyboard_receipts',[])
    if type(receipts) is not list or len(receipts)>16:
        raise ValueError('原生回执列表无效')
    previous=0
    for receipt in receipts:
        if (type(receipt) is not dict or set(receipt)!={'id','request_sequence','elapsed_ms','result'}
            or not uid(receipt['id']) or int(receipt['id'])<=previous
            or not uid(receipt['request_sequence']) or not integer(receipt['elapsed_ms'])
            or receipt['elapsed_ms']>s['elapsed_ms']
            or type(receipt['result']) is not str or len(receipt['result'])>64):
            raise ValueError('原生回执字段无效')
        previous=int(receipt['id'])
    return s
