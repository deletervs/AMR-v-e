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

expected_odometry = [
    "Unknown", "Unknown", "Valid", "Valid", "Stale", "Stale",
    "Valid", "Valid", "Valid", "Valid", "Valid", "Valid",
    "Unknown", "Unknown", "Valid", "Valid", "Stale",
]
fields = ("x_m", "y_m", "yaw_rad", "linear_x_mps", "linear_y_mps", "angular_z_radps")
last_reading = None
for record, quality in zip(records, expected_odometry):
    odometry = record["odometry"]
    check(odometry["quality"] == quality, "Unexpected odometry quality")
    reading = odometry["reading"]
    if quality == "Unknown":
        check(reading is None, "Odometry reset must have no sample")
        last_reading = None
        continue
    check(isinstance(reading, dict), "Odometry sample must be complete")
    for name in fields:
        check(type(reading[name]) in (int, float) and math.isfinite(reading[name]),
              "Odometry fields must be finite numbers")
    if quality == "Stale":
        check(reading == last_reading, "Stale odometry must preserve its previous sample")
    else:
        last_reading = reading
    phase = record["sim_time_ms"] % 6000
    if phase < 3000:
        check(all(reading[name] == 0 for name in fields), "Initial sample must be valid zeros")
    else:
        yaw = (phase - 3000) / 1000.0 * 0.2
        values = (1.25 * math.sin(yaw), 1.25 * (1 - math.cos(yaw)), yaw, 0.25, 0.0, 0.2)
        for name, value in zip(fields, values):
            check(math.isclose(reading[name], value, rel_tol=1e-12, abs_tol=1e-12),
                  "Unexpected arc sample: " + name)
check(records[6]["battery"]["quality"] == "Stale"
      and records[6]["odometry"]["quality"] == "Valid",
      "Battery and odometry must have independent recovery times")

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