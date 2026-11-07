"""Project adapter for the robot's existing Python 2 Pillow; no installation."""
from pathlib import Path
import subprocess
import sys

PYTHON = '/usr/bin/python2'
HELPER = Path(__file__).with_name('strict_jpeg_decoder.py')

def _run(data=None):
    command = [PYTHON, '-E', '-s', str(HELPER)]
    if data is None:
        command.append('--check')
    try:
        result = subprocess.run(command, input=data or b'', stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=2)
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError('camera decoder unavailable') from error
    if result.returncode:
        raise ValueError('camera JPEG decode failed')

def decoder():
    from camera_stream import require_decoder
    try:
        require_decoder()
        return None
    except ValueError:
        if sys.platform != 'linux' or not str(HELPER.resolve()).startswith('/home/handsfree/.local/share/rs-panel/releases/'):
            raise
        _run()
        return _run
