"""Prepare a version-checked HandsFree driver copy; never edit the installed driver."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import shutil

EXPECTED = {
    "src/hf_hw.cpp": "6fc8f8419395e2d2f004cb2fbfc766b58767a14e0ca78bb926ba1b1800538013",
    "src/hf_hw_ros.cpp": "dbf8721e49f22f132d3d218a79292c6a466cb7bec314a229be5b25d64594950b",
}

# Deliberately omit the vendor CMake execute_process(env.sh) and install commands.
BUILD_CMAKE = """cmake_minimum_required(VERSION 3.10)
project(handsfree_hw LANGUAGES CXX)
find_package(catkin REQUIRED COMPONENTS
    controller_manager geometry_msgs hardware_interface realtime_tools roscpp std_msgs handsfree_msgs)
set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
catkin_package(INCLUDE_DIRS include)
include_directories(include include/sdk/robolink include/sdk/robot_abstract ${catkin_INCLUDE_DIRS})
add_executable(handsfree_hw_node src/main.cpp src/transport_serial.cpp
    include/sdk/robolink/robolink.cpp include/sdk/robolink/state_machine.cpp
    src/hf_hw.cpp src/hf_hw_ros.cpp)
target_link_libraries(handsfree_hw_node ${catkin_LIBRARIES})
"""

def replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError("patch context is missing or ambiguous")
    return text.replace(before, after, 1)

def patch_sources(source):
    source = Path(source)
    originals = {}
    for name, expected in EXPECTED.items():
        raw = (source / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("vendor source changed; review before patching: " + name)
        originals[name] = raw.decode("utf-8").replace("\r\n", "\n")
    fixed = dict(originals)
    fixed["src/hf_hw.cpp"] = replace_once(
        fixed["src/hf_hw.cpp"],
        "                // one package ack arrived\n                ack_ready_ = true;\n                time_out_cnt_ = 0;",
        "                // An unrelated ACK must not complete this request.\n"
        "                ack_ready_ = robolink_->getReceiveRenewFlag(command) != 0;\n"
        "                if (ack_ready_) time_out_cnt_ = 0;")
    fixed["src/hf_hw.cpp"] = replace_once(
        fixed["src/hf_hw.cpp"],
        "        data = port_->readBuffer();\n        if (cicle_timer_.expires_from_now().is_negative())",
        "        // Keep the next chunk queued; a complete matching reply already won.\n"
        "        if (ack_ready_) break;\n"
        "        data = port_->readBuffer();\n        if (cicle_timer_.expires_from_now().is_negative())")
    fixed["src/hf_hw_ros.cpp"] = replace_once(
        fixed["src/hf_hw_ros.cpp"],
        "            robot_state_publisher_.publish(robot_state);",
        "            // Copy the newly decoded reply before publishing it.\n"
        "            readBufferUpdate();\n"
        "            // Host ROS time after valid reply; NOT the MCU acquisition time.\n"
        "            robot_state.header.stamp = ros::Time::now();\n"
        "            robot_state_publisher_.publish(robot_state);")
    return originals, fixed

def unified_patch(originals, fixed):
    return "".join("".join(difflib.unified_diff(
        originals[name].splitlines(keepends=True), fixed[name].splitlines(keepends=True),
        fromfile="a/" + name, tofile="b/" + name)) for name in EXPECTED)

def prepare(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination == source or source in destination.parents or destination.exists():
        raise ValueError("destination must be a new directory outside the source tree")
    originals, fixed = patch_sources(source)
    shutil.copytree(source, destination)
    for name, text in fixed.items():
        (destination / name).write_bytes(text.encode("utf-8"))
    (destination / "CMakeLists.txt").write_bytes(BUILD_CMAKE.encode("utf-8"))
    (destination / "timestamp-fix.patch").write_bytes(unified_patch(originals, fixed).encode("utf-8"))
    return {name: hashlib.sha256(text.encode("utf-8")).hexdigest() for name, text in fixed.items()}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    try:
        hashes = prepare(args.source, args.destination)
    except (ValueError, OSError) as error:
        parser.exit(1, str(error) + "\n")
    print(json.dumps({"destination": str(args.destination), "patched_sha256": hashes}))

if __name__ == "__main__":
    main()
