"""Compile the agent's actual portable writer and replay recorded values with virtual delivery times."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = r'''
int main()
{
    amr::RobotState state{};
    state.connected=true;
    BatteryObservation observation{};
    amr::EventLog events{};
    append_event(events,Ms{0},amr::LogLevel::Info,"robot","Offline fixture: recorded values, virtual delivery times");
    amr::BatterySampleGate gate;
    const auto origin=Clock::time_point{};
    std::uint64_t sequence=0;
    auto feed=[&](amr::BatterySample sample,int received_ms) {
        const auto result=gate.observe(sample);
        if(amr::battery_sample_accepted(result)){
            amr::update_battery_reading(state.battery,sample.voltage_v,origin+Ms{received_ms});
            observation.received_elapsed=Ms{received_ms};
            observation.source_stamp_ns=sample.source_stamp_ns;
            observation.source_time_raw=sample.source_time_raw;
        }
        return result;
    };
    auto emit=[&](int elapsed_ms) {
        amr::update_system_resources_reading(state.system_resource,
            {37.0,1024,4096,35.0},origin+Ms{elapsed_ms});
        amr::refresh_battery_quality(state.battery,origin+Ms{elapsed_ms},Ms{3000});
        write_frame(state,Ms{elapsed_ms},++sequence,"offline-fixture","",events,observation);
    };
    // Voltage/source values and the first two stamps were captured on 2026-10-29.
    constexpr std::uint64_t first_stamp=1793272965371653056ULL;
    constexpr std::uint64_t second_stamp=1793272966369064375ULL;
    emit(0); // resource-only compatibility
    observation.supported=true;
    emit(1); // ROS capability exists, still waiting
    feed({0,0,0},500);emit(500);
    feed({11.725240707397461,3453555712.0,first_stamp},1000);emit(1000);
    feed({99.0,3453555712.0,first_stamp+1000000},2000);emit(2000);
    emit(3999);emit(4000);
    feed({11.716343879699707,3454567936.0,second_stamp},4500);emit(4500);
    feed({-1,3454568192.0,second_stamp+1000000},5000);emit(5000);
    feed({11.6,1,second_stamp+1000000000},5500);emit(5500);
    feed({11.6,2,second_stamp+2000000000},6000);emit(6000);
    feed({11.5,3,second_stamp-1000000000},6500);emit(6500);
    feed({0,4,second_stamp+3000000000},7000);emit(7000);
}
'''

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("compiler")
    parser.add_argument("state_library",type=Path)
    parser.add_argument("--output",type=Path)
    parser.add_argument("--configuration",default="Debug")
    args=parser.parse_args()
    source=(ROOT/"cpp/apps/robot_state_agent.cpp").read_text(encoding="utf-8")
    # Only namespace functions are exercised; Linux main and ROS code are not compiled.
    prefix=source[:source.index("int main(")].replace("#include <unistd.h>","")
    code=prefix+'\n#include "amr/battery_source.hpp"\n'+FIXTURE
    compiler_name=Path(args.compiler).name.lower()
    env=dict(os.environ)
    if os.name=="nt":
        env["PATH"]=str(Path(args.compiler).parent)+os.pathsep+env.get("PATH","")
    with tempfile.TemporaryDirectory(prefix="rs-battery-output-") as temp:
        folder=Path(temp);cpp=folder/"fixture.cpp";cpp.write_text(code,encoding="utf-8")
        binary=folder/("fixture.exe" if os.name=="nt" else "fixture")
        if compiler_name in ("cl","cl.exe","clang-cl","clang-cl.exe"):
            command=[args.compiler,"/nologo","/std:c++17","/EHsc","/MDd" if args.configuration.lower()=="debug" else "/MD","/I"+str(ROOT/"cpp/include"),str(cpp),str(args.state_library.resolve()),"/Fe:"+str(binary)]
        else:
            command=[args.compiler,"-std=c++17","-I",str(ROOT/"cpp/include"),str(cpp),str(args.state_library.resolve()),"-o",str(binary)]
        built=subprocess.run(command,cwd=folder,env=env,capture_output=True,text=True,timeout=30)
        if built.returncode:raise RuntimeError(built.stdout+built.stderr)
        run=subprocess.run([str(binary)],env=env,capture_output=True,text=True,timeout=5)
        assert run.returncode==0,run.stderr
        lines=run.stdout.splitlines();frames=[json.loads(x) for x in lines]
    assert len(frames)==13
    assert [x["elapsed_ms"] for x in frames]==[0,1,500,1000,2000,3999,4000,4500,5000,5500,6000,6500,7000]
    assert all(x["source"]=="robot" and x["motion_enabled"] is False for x in frames)
    assert frames[0]["capabilities"]=={"battery":False}
    assert all(x["battery"]["voltage_v"] is None for x in frames[:3])
    first=frames[3]["battery"]
    assert first["quality"]=="Valid" and first["received_elapsed_ms"]==1000
    assert first["source_stamp_ns"]=="1793272965371653056"
    assert frames[4]["battery"]==first and frames[5]["battery"]==first
    assert frames[6]["battery"]==dict(first,quality="Stale")
    assert frames[6]["system_resources"]["quality"]=="Valid"
    assert frames[7]["battery"]["quality"]=="Valid" and frames[7]["battery"]["received_elapsed_ms"]==4500
    assert frames[8]["battery"]==frames[7]["battery"] and frames[9]["battery"]==frames[7]["battery"]
    assert frames[10]["battery"]["received_elapsed_ms"]==6000 and frames[10]["battery"]["source_time_raw"]==2
    assert int(frames[11]["battery"]["source_stamp_ns"])<int(frames[10]["battery"]["source_stamp_ns"])
    assert frames[11]["battery"]["received_elapsed_ms"]==6500
    assert frames[12]["battery"]["voltage_v"]==0 and frames[12]["battery"]["quality"]=="Valid"
    spec=importlib.util.spec_from_file_location("service",ROOT/"tools/serve_dashboard.py")
    service=importlib.util.module_from_spec(spec);spec.loader.exec_module(service)
    buffer=service.StateBuffer(expected_source="robot")
    for frame in frames:buffer.publish(frame)
    assert buffer.snapshot()["stream"]["sequence"]==13
    assert service.API_VERSION==6
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(run.stdout,encoding="utf-8")
    print("13 C++ output/quality frames validated by API6; offline replay only, no ROS execution")

if __name__=="__main__":
    main()
