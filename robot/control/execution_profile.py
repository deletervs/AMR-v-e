"""Read a local operator-authored short permit; never create or renew one."""
import os
from pathlib import Path
import re
import time
PROFILE=Path('/home/handsfree/.local/state/rs-executor/authorization.json')
def load(path,runtime,read_json):
    if path is None:return None
    path=Path(path)
    if path!=PROFILE or path.resolve()!=path:
        raise ValueError('execution profile must use fixed private project path')
    value=read_json(path)
    fields={'version','enabled','operator_confirmed','os_boot_id','binary_sha256','expires_monotonic_ms','driver_node'}
    if type(value) is not dict or set(value)!=fields or type(value['version']) is not int or value['version']!=1 or type(value['enabled']) is not bool:
        raise ValueError('execution profile fields')
    if not value['enabled']:return None
    now=int(time.monotonic()*1000)
    if (value['operator_confirmed'] is not True or value['binary_sha256']!=runtime['sha256']
        or value['os_boot_id']!=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        or type(value['expires_monotonic_ms']) is not int
        or not now<value['expires_monotonic_ms']<=now+300000
        or type(value['driver_node']) is not str
        or not re.fullmatch(r'/[A-Za-z_][A-Za-z0-9_/]{0,126}',value['driver_node'])):
        raise ValueError('execution permit expired, unconfirmed or bound to another boot/program')
    return value
