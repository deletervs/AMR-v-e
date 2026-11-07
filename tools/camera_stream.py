"""Bounded, read-only rs-camera-v1 receiver. Pillow is required only when enabled."""
import base64
import binascii
import copy
import io
import json
import re
import subprocess
import threading
import time
import uuid

MAX_LINE = 98304
MAX_JPEG = 49152
TTL_MS = 1500
MAX_INTEGER = 9007199254740991


def require_decoder():
    try:
        from PIL import Image, ImageFile
    except ImportError as error:
        raise ValueError("camera requires Pillow in this Python environment") from error
    if ImageFile.LOAD_TRUNCATED_IMAGES:
        raise ValueError("camera requires strict Pillow truncated-image handling")
    return Image



def decode_jpeg(data):
    Image = require_decoder()
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != "JPEG" or image.size != (320, 240):
                raise ValueError("invalid decoded camera dimensions")
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
    except Exception as error:
        raise ValueError("camera JPEG decode failed") from error

def validate_frame(frame, decoder=None):
    if not isinstance(frame, dict):
        raise ValueError("camera frame must be an object")
    fixed = dict(schema="rs-camera-v1", source="robot_camera", device="/dev/video2",
                 device_name="Dabai DC1", width=320, height=240, motion_enabled=False)
    if any(type(frame.get(k)) is not type(v) or frame[k] != v for k, v in fixed.items()):
        raise ValueError("invalid camera identity or dimensions")
    for name, minimum in (("seq", 1), ("elapsed_ms", 0), ("jpeg_bytes", 1)):
        if type(frame.get(name)) is not int or not minimum <= frame[name] <= MAX_INTEGER:
            raise ValueError("invalid camera integer")
    stamp = frame.get("host_received_unix_ns")
    if (type(stamp) is not str or re.fullmatch(r"[1-9][0-9]{0,19}", stamp) is None
            or int(stamp) > 18446744073709551615):
        raise ValueError("invalid host read timestamp")
    encoded = frame.get("jpeg_b64")
    if type(encoded) is not str or len(encoded) > 65536 or frame["jpeg_bytes"] > MAX_JPEG:
        raise ValueError("oversized camera JPEG")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("invalid base64") from error
    if (len(data) != frame["jpeg_bytes"] or base64.b64encode(data).decode("ascii") != encoded
            or not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9")):
        raise ValueError("invalid JPEG length or encoding")
    (decoder or decode_jpeg)(data)
    # Return only validated protocol fields; unknown JSON values cannot reach HTTP.
    fields = set(fixed) | {"seq", "elapsed_ms", "jpeg_bytes", "jpeg_b64", "host_received_unix_ns"}
    return {name: frame[name] for name in fields}


class CameraBuffer:
    def __init__(self, enabled=False, clock=time.monotonic, decoder=None):
        self.enabled, self.clock = enabled, clock
        self.decoder = decoder
        self.lock = threading.Lock()
        self.stream_id = str(uuid.uuid4())
        self.frame = None
        self.received = None
        self.seq, self.elapsed = 0, -1
        self.terminal = None
        self.anchor_received = None
        self.anchor_elapsed = None

    def publish(self, frame):
        checked = validate_frame(frame, self.decoder)
        with self.lock:
            if not self.enabled or self.terminal:
                raise ValueError("camera stream is inactive")
            if checked["seq"] <= self.seq or checked["elapsed_ms"] <= self.elapsed:
                raise ValueError("camera sequence/time did not advance")
            now = self.clock()
            lag_ms = 0
            if self.anchor_received is not None:
                lag_ms = ((now - self.anchor_received) * 1000
                          - (checked["elapsed_ms"] - self.anchor_elapsed))
                if lag_ms >= TTL_MS:
                    raise ValueError("camera producer elapsed fell behind reception progress")
            else:
                self.anchor_received, self.anchor_elapsed = now, checked["elapsed_ms"]
            self.frame = checked
            self.seq, self.elapsed = checked["seq"], checked["elapsed_ms"]
            # Include observed transport lag in age rather than renewing a queued frame.
            self.received = now - max(0, lag_ms) / 1000

    def finish(self, error=None):
        with self.lock:
            self.terminal = "error" if error else "stopped"
            self.frame = None

    def snapshot(self):
        with self.lock:
            age = None if self.received is None else max(0, int((self.clock()-self.received)*1000))
            status = ("disabled" if not self.enabled else self.terminal or
                      ("starting" if age is None else "stale" if age >= TTL_MS else "live"))
            return {"camera_api_version": 1, "stream": {
                "id": self.stream_id, "source": "robot_camera" if self.enabled else None,
                "status": status, "sequence": self.seq, "age_ms": age, "ttl_ms": TTL_MS},
                "frame": copy.deepcopy(self.frame) if status == "live" else None}


class CameraProducer:
    def __init__(self, command, buffer, env=None):
        self.buffer = buffer
        self.closing = threading.Event()
        self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=None, env=env)
        self.thread = threading.Thread(target=self._read, name="camera-reader", daemon=True)
        self.thread.start()

    def _read(self):
        error = None
        try:
            while not self.closing.is_set():
                line = self.process.stdout.readline(MAX_LINE + 1)
                if not line:
                    break
                if len(line) > MAX_LINE or not line.endswith(b"\n"):
                    raise ValueError("oversized or truncated camera line")
                self.buffer.publish(json.loads(line.decode("utf-8")))
        except (ValueError, UnicodeError, TypeError, OverflowError):
            error = "invalid camera output"
        finally:
            # Revoke the picture at EOF/error, before waiting for process exit.
            self.buffer.finish(error)
            if self.process.poll() is None and error:
                self.process.terminate()
            try:
                code = self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                code = self.process.wait(timeout=2)
            if code:
                self.buffer.finish("camera process failed")

    def close(self):
        self.closing.set()
        self.buffer.finish()
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=2)
        self.thread.join(timeout=3)
        self.process.stdout.close()
