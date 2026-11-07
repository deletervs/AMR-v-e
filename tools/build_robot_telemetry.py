"""Build hashed read-only collectors in project directories; never starts hardware."""
import argparse
import hashlib
import json
from pathlib import Path
from observe_usb_guard import run_robot
ROOT=Path(__file__).resolve().parents[1]
NAMES=('battery_state','battery_source','odometry_state','odometry_source','lidar_source',
    'data_freshness','system_resources','event_log','linux_resources')
FILES=['robot/telemetry/CMakeLists.txt','robot/camera/CMakeLists.txt',
    'cpp/apps/robot_state_agent.cpp','cpp/apps/robot_camera_stream.cpp','cpp/apps/robot_camera_snapshot.cpp',
    'cpp/include/amr/robot_state.hpp']
FILES += ['cpp/src/'+n+'.cpp' for n in NAMES]
FILES += ['cpp/include/amr/'+n+'.hpp' for n in NAMES]
FILES += ['cpp/tests/test_'+n+'.cpp' for n in NAMES]
REMOTE=r"""
import hashlib,json,pathlib,subprocess
spec=SPEC
base=pathlib.Path('/home/handsfree/.local/share/rs-telemetry/builds')
base.mkdir(parents=True,exist_ok=True)
if base.resolve()!=base:raise RuntimeError('redirected collector root')
release=base/spec['bundle']
for name,text in spec['files'].items():
 path=release/name;path.parent.mkdir(parents=True,exist_ok=True)
 if path.resolve()!=path:raise RuntimeError('redirected source')
 if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()!=spec['hashes'][name]:raise RuntimeError('source drift')
 if not path.exists():path.write_bytes(text.encode())
steps=[];sources={}
for kind,directory,target in [('state','telemetry','robot_state_agent'),('camera','camera','robot_camera_stream')]:
 build=release/('build-'+kind);build.mkdir(exist_ok=True)
 if build.resolve()!=build:raise RuntimeError('redirected build')
 configure=['cmake',str(release/'robot'/directory)]
 if kind=='state' and spec['with_ros']:
  prefixes=[str(p) for p in [pathlib.Path('/home/handsfree/handsfree/handsfree_ros_ws/devel'),pathlib.Path('/home/handsfree/hf_ws/devel'),pathlib.Path('/opt/ros/melodic')] if p.exists()]
  configure+=['-DCMAKE_PREFIX_PATH='+';'.join(prefixes),'-DRS_WITH_ROS_BATTERY=ON','-DRS_WITH_ROS_ODOMETRY=ON','-DRS_WITH_ROS_LIDAR=ON']
  if pathlib.Path('/usr/bin/python2').exists():configure+=['-DPYTHON_EXECUTABLE=/usr/bin/python2']
 for command in [configure,['cmake','--build','.', '--target',target,'--','-j2']]:
  p=subprocess.run(command,cwd=str(build),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,universal_newlines=True,timeout=120)
  steps.append({'kind':kind,'exit':p.returncode,'output':p.stdout[-18000:]})
  if p.returncode:break
 if steps[-1]['exit']:break
 binary=build/target;binary.chmod(0o700);sources[kind]={'path':str(binary),'sha256':hashlib.sha256(binary.read_bytes()).hexdigest()}
print(json.dumps({'built':len(sources)==2,'sources':sources,'steps':steps,'source_sha256':spec['hashes'],'collectors_started':False}))
"""
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--with-ros',action='store_true');args=p.parse_args()
 files={n:(ROOT/n).read_text(encoding='utf-8') for n in FILES}
 hashes={n:hashlib.sha256(v.encode()).hexdigest() for n,v in files.items()}
 bundle=hashlib.sha256(json.dumps({'hashes':hashes,'ros':args.with_ros},sort_keys=True).encode()).hexdigest()[:16]
 proc=run_robot(REMOTE.replace('SPEC',ascii({'files':files,'hashes':hashes,'bundle':bundle,'with_ros':args.with_ros}),1),480)
 if proc.returncode:print(proc.stdout.decode(errors='replace'));print(proc.stderr.decode(errors='replace'));return proc.returncode
 result=json.loads(proc.stdout.decode().strip());directory=ROOT/'.local/robot-telemetry';directory.mkdir(parents=True,exist_ok=True)
 (directory/'build-result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
 if result['built']:(directory/'sources-manifest.json').write_text(json.dumps(result['sources'],indent=2),encoding='utf-8')
 print(json.dumps(result));return 0 if result['built'] else 1
if __name__=='__main__':raise SystemExit(main())
