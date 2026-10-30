"""Exercise the actual vendor wait/publication code with controlled packet streams."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("robot_time_fix", ROOT / "tools/prepare_robot_timestamp_fix.py")
fix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fix)

def block(text, marker):
    start = text.index(marker)
    opened = text.index("{", start)
    depth = 0
    for index in range(opened, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    raise ValueError("incomplete source block")

PREFIX = r"""
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
using Command = int;
using Buffer = std::vector<unsigned char>;
const int GET_SYSTEM_INFO=1;
bool expired=false;
namespace boost {
namespace posix_time { int millisec(int value){return value;} }
namespace asio {
struct duration { bool is_negative() const { return expired; } };
struct deadline_timer {
    explicit deadline_timer(int){}
    void expires_from_now(int){expired=false;}
    duration expires_from_now(){return {};}
};
}}
void usleep(unsigned){}
struct Port {
    int io=0, reads=0;
    bool expire_second=false;
    std::vector<Buffer> chunks;
    int* getIOinstace(){return &io;}
    Buffer readBuffer(){
        ++reads;
        if(reads>=4 || (expire_second&&reads>=2)) expired=true;
        return reads<=static_cast<int>(chunks.size())?chunks[reads-1]:Buffer{};
    }
};
struct Link {
    int flags[4]{};
    double voltage=0;
    bool byteAnalysisCall(unsigned char byte){
        if(byte>=4) return false;
        flags[byte]=1;
        if(byte==1) voltage=11.8;
        return true;
    }
    int getReceiveRenewFlag(int command){return flags[command];}
};
struct HF_HW {
    Port owned_port;
    Link owned_link;
    Port* port_=&owned_port;
    Link* robolink_=&owned_link;
    int robolink_command_set_[4]{0,1,0,0};
    int robolink_freq_[4]{0,1,0,0};
    int time_out_=500, time_out_cnt_=7;
    bool ack_ready_=false;
    void sendCommand(int command){robolink_->flags[command]=0;}
    bool updateCommand(const Command&,int);
};
__WAIT__
namespace ros {
struct Duration { double toSec()const{return 0;} };
struct Time {
    int value=0;
    static Time now(){static int tick=100;return Time{++tick};}
    Duration operator-(const Time&)const{return {};}
};
}
struct Header { ros::Time stamp; };
struct State { Header header; double battery_voltage=0; };
struct Publisher {
    std::vector<State> messages;
    void publish(const State& value){messages.push_back(value);}
};
struct HardwareReply {
    bool success=false;
    bool updateCommand(int,int){return success;}
};
Publisher publication(bool success,double reading){
    State robot_state;
    HardwareReply hf_hw_{success};
    Publisher robot_state_publisher_;
    int count=0;
    ros::Time currentTime=ros::Time::now();
    auto readBufferUpdate=[&](){robot_state.battery_voltage=reading;};
    __PUBLISH__
    readBufferUpdate();
    return robot_state_publisher_;
}
int main(){
    auto check=[](bool condition,const char* name){if(!condition)throw std::runtime_error(name);};
    try {
        {HF_HW d;d.owned_port.chunks={{1},{2}};
         check(d.updateCommand(GET_SYSTEM_INFO,0),"matching reply accepted");
         check(d.owned_port.reads==1,"next unread chunk preserved");}
        {HF_HW d;d.owned_port.chunks={{0},{1}};
         check(d.updateCommand(GET_SYSTEM_INFO,0)&&d.owned_link.voltage==11.8,"handshake cannot complete GET");}
        {HF_HW d;d.owned_port.chunks={{2},{1}};
         check(d.updateCommand(GET_SYSTEM_INFO,0)&&d.owned_link.voltage==11.8,"SET ack cannot complete GET");}
        {HF_HW d;d.owned_port.chunks={{1,2}};
         check(d.updateCommand(GET_SYSTEM_INFO,0)&&d.owned_link.voltage==11.8,"mixed frame chunk");}
        {HF_HW d;d.owned_port.chunks={{2}};
         check(!d.updateCommand(GET_SYSTEM_INFO,0),"unrelated-only stream times out");
         check(d.time_out_cnt_==8,"unrelated response does not reset timeout count");}
        {HF_HW d;d.owned_port.chunks={{99}};
         check(!d.updateCommand(GET_SYSTEM_INFO,0),"invalid packet times out");}
        {HF_HW d;
         check(!d.updateCommand(GET_SYSTEM_INFO,1)&&d.owned_port.reads==0,"unscheduled request does not read");}
        {HF_HW d;d.robolink_command_set_[1]=0;
         check(!d.updateCommand(GET_SYSTEM_INFO,0)&&d.owned_port.reads==0,"disabled request does not read");}
        {HF_HW d;d.owned_port.chunks={{1}};d.owned_port.expire_second=true;
         check(d.updateCommand(GET_SYSTEM_INFO,0),"extra read cannot turn complete reply into timeout");}
        {auto p=publication(true,12.25);
         check(p.messages.size()==1&&p.messages[0].battery_voltage==12.25,"publish freshly decoded value");
         check(p.messages[0].header.stamp.value>0,"published reply has host receipt stamp");}
        {auto p=publication(false,12.25);
         check(p.messages.empty(),"failed request publishes nothing");}
        {auto p=publication(true,11.5);
         check(p.messages.size()==1&&p.messages[0].battery_voltage==11.5,"next sample has no cache lag");}
        std::cout<<"12 transport/publication scenarios passed\n";
        return 0;
    }catch(const std::exception& error){std::cerr<<error.what()<<"\n";return 1;}
}
"""

def run(compiler, folder, name, sources):
    wait = block(sources["src/hf_hw.cpp"], "bool HF_HW::updateCommand(")
    publish = block(sources["src/hf_hw_ros.cpp"], "if (hf_hw_.updateCommand(GET_SYSTEM_INFO, count))")
    path = folder / (name + ".cpp")
    path.write_text(PREFIX.replace("__WAIT__", wait).replace("__PUBLISH__", publish), encoding="utf-8")
    binary = folder / (name + (".exe" if os.name == "nt" else ""))
    env = dict(os.environ)
    if os.name == "nt":
        env["PATH"] = str(Path(compiler).parent) + os.pathsep + env.get("PATH", "")
    result = subprocess.run([compiler, "-std=c++17", str(path), "-o", str(binary)],
                            capture_output=True, text=True, timeout=30, env=env)
    if result.returncode:
        raise RuntimeError("fixture compilation failed: " + result.stderr[-3000:])
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=5, env=env)
    return {"exit": result.returncode, "stdout": result.stdout, "stderr": result.stderr}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vendor-source", type=Path, default=ROOT / ".local/learning-checks/M2-002/timestamp-fix/vendor-original")
    parser.add_argument("--compiler", default=shutil.which("g++") or "C:/msys64/ucrt64/bin/g++.exe")
    parser.add_argument("--report", type=Path, default=ROOT / ".local/learning-checks/M2-002/timestamp-fix/regression.json")
    args = parser.parse_args()
    original, fixed = fix.patch_sources(args.vendor_source)
    assert fix.unified_patch(original, fixed) == (ROOT / "robot/patches/handsfree_hw_timestamp.patch").read_text(encoding="utf-8")
    result = {}
    with tempfile.TemporaryDirectory(prefix="rs-time-regression-") as temp:
        folder = Path(temp)
        result["fixed"] = run(args.compiler, folder, "fixed", fixed)
        assert result["fixed"]["exit"] == 0, result
        variants = {
            "old_wait": dict(fixed, **{"src/hf_hw.cpp": original["src/hf_hw.cpp"]}),
            "old_publication": dict(fixed, **{"src/hf_hw_ros.cpp": original["src/hf_hw_ros.cpp"]}),
            "missing_stamp": {name: text.replace("            robot_state.header.stamp = ros::Time::now();", "") for name, text in fixed.items()},
            "extra_read": {name: text.replace("        if (ack_ready_) break;\n", "") for name, text in fixed.items()},
        }
        for name, sources in variants.items():
            result[name] = run(args.compiler, folder, name, sources)
            assert result[name]["exit"] != 0, "regression not caught: " + name
        # Preparation must reject changed input and any attempt to overwrite a source.
        try:
            fix.prepare(args.vendor_source, args.vendor_source)
        except ValueError:
            result["overwrite_guard"] = "passed"
        else:
            raise AssertionError("source overwrite was permitted")
        altered = folder / "changed"
        shutil.copytree(args.vendor_source, altered)
        with (altered / "src/hf_hw.cpp").open("ab") as output:
            output.write(b"\n// source drift\n")
        try:
            fix.patch_sources(altered)
        except ValueError:
            result["source_hash_guard"] = "passed"
        else:
            raise AssertionError("changed source was permitted")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))

if __name__ == "__main__":
    main()
