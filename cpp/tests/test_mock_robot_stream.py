# 该脚本调用 --stream-test，验证跨周期状态，不等待真实的八秒

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
program = sys.argv[1]

result = subprocess.run(
    [program, "--stream-test"],
    capture_output=True,
    text=True,
    encoding="utf-8",
    timeout=5,
)
check(result.returncode == 0, "Stream test mode failed: " + result.stderr)
records = [
    json.loads(line, parse_constant=reject_constant)
    for line in result.stdout.splitlines()
]

expected_quality = [
    "Unknown", "Unknown", "Valid", "Valid",
    "Stale", "Stale", "Stale", "Stale",
    "Valid", "Valid", "Valid", "Valid",
    "Unknown", "Unknown", "Valid", "Valid", "Stale",
]
check(len(records) == len(expected_quality), "Expected 17 snapshots")

last_voltage = None

for index, (record, quality) in enumerate(zip(records, expected_quality)):
    check(record["source"] == "mock", "Unexpected data source")
    check(type(record["sim_time_ms"]) is int, "Time must be an integer")
    check(record["sim_time_ms"] == index * 500, "Unexpected simulated time")
    check(record["motion_enabled"] is False, "Motion must stay disabled")
    check(record["connected"] is (quality != "Unknown"), "Unexpected connection")
    check(record["battery"]["quality"] == quality, "Unexpected quality")
    voltage = record["battery"]["voltage_v"]

    if quality == "Unknown":
        check(voltage is None, "Reset must have no reading")
        last_voltage = None
    else:
        check(type(voltage) in (int, float), "Voltage must be a number")
        check(math.isfinite(voltage), "Voltage must be finite")

        if quality == "Stale":
            check(voltage == last_voltage, "Stale reading must be preserved")
        else:
            last_voltage = voltage

invalid = subprocess.run(
    [program, "--invalid-mode"],
    capture_output=True,
    text=True,
    encoding="utf-8",
    timeout=5,
)
check(invalid.returncode != 0, "Unknown mode must fail")
check(invalid.stdout == "", "Usage errors must not enter the JSON stream")

print("mock stream test passed")