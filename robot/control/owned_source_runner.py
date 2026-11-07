"""Exec only hashed project-owned read-only collectors with parent-death cleanup."""
import ctypes
import hashlib
import os
from pathlib import Path
import signal
import sys
def main():
    if len(sys.argv)!=3:raise ValueError('source path/hash')
    path=Path(sys.argv[1]);fingerprint=sys.argv[2]
    if (path.name not in ('robot_state_agent','robot_camera_stream')
        or not str(path).startswith('/home/handsfree/.local/share/rs-')
        or path.resolve()!=path or not path.is_file()
        or path.stat().st_uid!=os.getuid() or path.stat().st_mode&0o022
        or hashlib.sha256(path.read_bytes()).hexdigest()!=fingerprint):
        raise ValueError('untrusted collector')
    parent=os.getppid()
    if parent<=1 or ctypes.CDLL(None,use_errno=True).prctl(1,signal.SIGKILL)!=0 or os.getppid()!=parent:
        raise RuntimeError('source owner unavailable')
    arguments=[str(path)]+(['--continuous'] if path.name=='robot_camera_stream' else [])
    os.execv(str(path),arguments)
if __name__=='__main__':main()
