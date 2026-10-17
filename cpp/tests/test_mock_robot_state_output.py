# 本文件用于外部验收, 检查 json 输出是否合法:
# 1. 启动已经编译好的模拟程序，获取其输出。
# 2. 用 JSON 解析器读取四行文字。
# 3. 检查字段类型、状态顺序、旧读数保留和运动禁用

import json
import math
import subprocess
import sys


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def reject_constant(value):
    raise ValueError("Invalid JSON number: " + value)


check(len(sys.argv) == 2, "Expected the mock program path")

result = subprocess.run(
    [sys.argv[1]],
    capture_output=True,
    text=True,
    encoding="utf-8",
    timeout=5,
)
check(result.returncode == 0, "Mock program failed: " + result.stderr)

lines = result.stdout.splitlines()
check(len(lines) == 4, "Expected four JSON snapshots")
records = [
    json.loads(line, parse_constant=reject_constant)
    for line in lines
]

expected = [
    (0, "Unknown", False),
    (100, "Valid", True),
    (1100, "Stale", True),
    (1200, "Valid", True),
]

for record, (time_ms, quality, connected) in zip(records, expected):
    check(isinstance(record, dict), "Snapshot must be an object")
    check(record["source"] == "mock", "Unexpected data source")
    check(type(record["sim_time_ms"]) is int, "Time must be an integer")
    check(record["sim_time_ms"] == time_ms, "Unexpected simulated time")
    check(record["connected"] is connected, "Unexpected connection state")
    check(record["motion_enabled"] is False, "Motion must stay disabled")
    check(record["battery"]["quality"] == quality, "Unexpected quality")

check(records[0]["battery"]["voltage_v"] is None, "Startup voltage must be null")

for record in records[1:]:
    voltage = record["battery"]["voltage_v"]
    check(type(voltage) in (int, float), "Voltage must be a number")
    check(math.isfinite(voltage), "Voltage must be finite")

check(
    records[1]["battery"]["voltage_v"]
    == records[2]["battery"]["voltage_v"],
    "Stale snapshot must preserve the last voltage",
)

print("mock output test passed")