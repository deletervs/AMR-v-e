"""Build project-owned native control adapters on the robot; never starts them."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
from observe_usb_guard import run_robot

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    'cpp/apps/robot_control_gateway.cpp', 'cpp/apps/robot_gamepad_info.cpp',
    'cpp/apps/robot_gamepad_probe.cpp', 'cpp/apps/robot_usb_guard.cpp',
    'cpp/include/amr/linux_gamepad_reader.hpp', 'cpp/src/linux_gamepad_reader.cpp',
    'cpp/include/amr/motion_guard.hpp', 'cpp/src/motion_guard.cpp',
    'cpp/include/amr/input_mapping.hpp', 'cpp/src/input_mapping.cpp',
    'cpp/include/amr/command_dispatcher.hpp', 'cpp/src/command_dispatcher.cpp',
    'cpp/include/amr/keyboard_ingress.hpp', 'cpp/src/keyboard_ingress.cpp',
    'cpp/include/amr/single_executor.hpp', 'cpp/src/single_executor.cpp',
    'cpp/include/amr/ros_velocity_sink.hpp', 'cpp/ros/velocity_sink.cpp',
    'robot/control/CMakeLists.txt',
]

ROBOT = r"""
import hashlib,json,pathlib,subprocess
spec=SPEC
base=pathlib.Path('/home/handsfree/.local/share/rs-control-gateway/builds')
base.mkdir(parents=True,exist_ok=True)
if base.resolve()!=base:raise RuntimeError('redirected build root')
release=base/spec['bundle']
for name,text in spec['files'].items():
 path=release/name
 path.parent.mkdir(parents=True,exist_ok=True)
 if path.resolve()!=path:raise RuntimeError('redirected source path')
 if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()!=spec['hashes'][name]:
  raise RuntimeError('immutable source drift')
 if not path.exists():path.write_bytes(text.encode('utf-8'))
assert all(hashlib.sha256((release/n).read_bytes()).hexdigest()==h for n,h in spec['hashes'].items())
build=release/'build';build.mkdir(exist_ok=True)
if build.resolve()!=build:raise RuntimeError('redirected build path')
steps=[]
configure=['cmake','-DRS_BUILD_ROS_EXECUTOR='+('ON' if spec.get('with_ros') else 'OFF')]
if spec.get('with_ros'):
 configure+=['-DCMAKE_PREFIX_PATH=/opt/ros/melodic']
 if pathlib.Path('/usr/bin/python2').is_file():configure+=['-DPYTHON_EXECUTABLE=/usr/bin/python2']
commands=[configure+[str(release/'robot/control')]]
commands += [['cmake','--build','.', '--target',target,'--','-j2'] for target in ('robot_usb_guard','robot_control_gateway','robot_gamepad_info')]
for command in commands:
 p=subprocess.run(command,cwd=str(build),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,universal_newlines=True,timeout=100)
 steps.append({'exit':p.returncode,'output':p.stdout[-16000:]})
 if p.returncode:break
built=len(steps)==4 and all(step['exit']==0 for step in steps)
binary=build/'robot_usb_guard';gateway=build/'robot_control_gateway';info=build/'robot_gamepad_info'
print(json.dumps({'built':built,'binary':str(binary),
 'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest() if built else None,
 'gamepad_info':str(info),'gamepad_info_sha256':hashlib.sha256(info.read_bytes()).hexdigest() if built else None,
 'gateway':str(gateway),'gateway_sha256':hashlib.sha256(gateway.read_bytes()).hexdigest() if built else None,
 'source_sha256':spec['hashes'],'ros_executor_compiled':bool(spec.get('with_ros')),'steps':steps,'program_started':False}))
"""

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--with-ros-executor',action='store_true')
    args=parser.parse_args()
    files={n:(ROOT/n).read_text(encoding='utf-8') for n in FILES}
    hashes={n:hashlib.sha256(v.encode()).hexdigest() for n,v in files.items()}
    bundle=hashlib.sha256(json.dumps({'hashes':hashes,'with_ros':args.with_ros_executor},sort_keys=True).encode()).hexdigest()[:16]
    spec={'files':files,'hashes':hashes,'bundle':bundle,'with_ros':args.with_ros_executor}
    p=run_robot(ROBOT.replace('SPEC',ascii(spec),1),240)
    if p.returncode:
        print(p.stderr.decode(errors='replace'))
        print(p.stdout.decode(errors='replace'))
        return p.returncode
    result=json.loads(p.stdout.decode().strip())
    evidence=ROOT/'.local/robot-control/build-records'
    evidence.mkdir(parents=True,exist_ok=True)
    (evidence/(bundle+'-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')).write_text(json.dumps(result,indent=2),encoding='utf-8')
    if result['built']:
        current=ROOT/'.local/robot-control'
        current.mkdir(parents=True,exist_ok=True)
        temporary=current/'native-build.tmp'
        temporary.write_text(json.dumps(result,indent=2),encoding='utf-8')
        temporary.replace(current/'native-build.json')
    print(json.dumps(result))
    return 0 if result['built'] else 1

if __name__=='__main__':raise SystemExit(main())
