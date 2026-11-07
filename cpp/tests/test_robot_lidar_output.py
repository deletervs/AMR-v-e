"""Exercise the actual LaserScan callback and production writer; offline, no ROS process."""
import argparse
import importlib.util
import json
from pathlib import Path
from test_robot_battery_output import ROOT,compile_output
from test_robot_odometry_output import block
CALLBACK=r'''
#include <cassert>
#include <memory>
namespace ros {
struct Time{std::uint64_t value{0};std::uint64_t toNSec()const{return value;}static Time now();};
std::uint64_t current=1793441000123456789ULL;Time Time::now(){return {current};}
}
namespace sensor_msgs {
struct LaserScan {
 using ConstPtr=std::shared_ptr<const LaserScan>;
 struct {ros::Time stamp{};std::uint32_t seq{0};std::string frame_id{"laser_front"};}header;
 float angle_min{0},angle_max{.2f},angle_increment{.1f},range_min{0},range_max{10},scan_time{0},time_increment{0};
 std::vector<float> ranges{0,std::numeric_limits<float>::quiet_NaN(),2};
};
}
class CallbackFixture {
public:
 CallbackFixture(amr::LidarState&s,LidarObservation&o,amr::EventLog&e):lidar_{s},lidar_observation_{o},start_{Clock::now()},events_{e}{}
 __RECEIVE_LIDAR__
 amr::LidarState&lidar_;LidarObservation&lidar_observation_;Clock::time_point start_;amr::EventLog&events_;amr::LidarSampleGate lidar_gate_{};
};
void callback_checks(){
 amr::LidarState state{};LidarObservation observation{};amr::EventLog events{};CallbackFixture fixture{state,observation,events};auto scan=std::make_shared<sensor_msgs::LaserScan>();
 fixture.receive_lidar(scan);assert(!state.reading);
 scan->header.stamp.value=ros::current+1;fixture.receive_lidar(scan);assert(!state.reading);
 scan->header.stamp.value=ros::current-1500000000ULL;fixture.receive_lidar(scan);assert(!state.reading);
 scan->header.stamp.value=ros::current;scan->ranges.clear();fixture.receive_lidar(scan);assert(!state.reading);
 scan->ranges.assign(4097,1);fixture.receive_lidar(scan);assert(!state.reading);
 scan->ranges={0,std::numeric_limits<float>::quiet_NaN(),2};fixture.receive_lidar(scan);
 assert(state.reading && state.quality==amr::DataQuality::Valid && state.reading->source_sequence==0);
 assert(state.reading->ranges_m[0]==0 && !state.reading->ranges_m[1] && state.reading->ranges_m[2]==2 && state.reading->frame_id=="laser_front");
 const auto received=state.received_at;const auto elapsed=observation.received_elapsed;
 scan->header.stamp.value--;scan->ranges[2]=9;fixture.receive_lidar(scan);assert(state.received_at==received && observation.received_elapsed==elapsed && state.reading->ranges_m[2]==2);
 scan->header.seq=1;scan->header.frame_id="bad frame";fixture.receive_lidar(scan);assert(state.received_at==received);
 scan->header.frame_id="laser_front";scan->angle_increment=0;fixture.receive_lidar(scan);assert(state.received_at==received);
 scan->angle_increment=.1f;scan->header.stamp.value--;fixture.receive_lidar(scan);assert(state.reading->source_sequence==1 && state.reading->ranges_m[2]==9);
 scan->header.seq=2;scan->header.stamp.value--;scan->ranges.assign(3,std::numeric_limits<float>::infinity());fixture.receive_lidar(scan);
 assert(state.quality==amr::DataQuality::Valid);for(const auto&r:state.reading->ranges_m)assert(!r);
}
'''
FIXTURE=r'''
int main(){
 callback_checks();amr::RobotState state{};BatteryObservation battery{};battery.supported=true;OdometryObservation odometry{};odometry.supported=true;
 amr::LidarState lidar{};LidarObservation observation{};amr::LidarSampleGate gate{};amr::LidarSample sample{};sample.geometry={0,.2,.1,0,10,0,0};sample.ranges_m={0,std::numeric_limits<double>::quiet_NaN(),2};sample.frame_id="laser_front";
 amr::EventLog events{};append_event(events,Ms{0},amr::LogLevel::Info,"lidar","OFFLINE LaserScan fixture; synthetic readings and delivery times, no robot");
 const auto origin=Clock::time_point{};constexpr std::uint64_t stamp=1793441000123456789ULL;std::uint64_t sequence=0;
 auto feed=[&](int elapsed){amr::LidarReading reading{};const auto result=gate.observe(sample,reading);
 if(amr::lidar_sample_accepted(result)){lidar.reading=std::move(reading);lidar.received_at=origin+Ms{elapsed};lidar.quality=amr::DataQuality::Valid;observation.received_elapsed=Ms{elapsed};}return result;};
 auto emit=[&](int elapsed){amr::update_system_resources_reading(state.system_resource,{25,1024,4096,35},origin+Ms{elapsed});
 amr::update_battery_reading(state.battery,11.6,origin+Ms{elapsed});battery.received_elapsed=Ms{elapsed};battery.source_stamp_ns=stamp+elapsed*1000000ULL;battery.source_time_raw=elapsed+1;
 amr::update_odometry_reading(state.odometry,{0,0,0,0,0,0},origin+Ms{elapsed});odometry.received_elapsed=Ms{elapsed};odometry.source_stamp_ns=stamp+elapsed*1000000ULL;odometry.source_sequence=elapsed;odometry.source_twist_sequence=elapsed;odometry.frame_id="odom";odometry.child_frame_id="base_link";
 amr::refresh_lidar_quality(lidar,origin+Ms{elapsed},Ms{1500});write_frame(state,Ms{elapsed},++sequence,"offline-fixture","",events,battery,odometry,lidar,observation);};
 emit(0);observation.supported=true;emit(1);
 sample.source_stamp_ns=stamp;feed(1000);emit(1000);
 sample.source_stamp_ns=stamp+1;sample.ranges_m[2]=9;feed(2000);emit(2000);emit(2499);emit(2500);
 sample.source_sequence=10;sample.source_stamp_ns=stamp+2;sample.ranges_m={1,2,3};feed(2600);emit(2600);
 sample.source_sequence=11;sample.source_stamp_ns=stamp+3;sample.geometry.angle_increment_rad=0;feed(2700);emit(2700);
 sample.geometry.angle_increment_rad=.1;sample.source_sequence=0;sample.source_stamp_ns=stamp+4;feed(2800);emit(2800);
 sample.source_stamp_ns=stamp+5;feed(2900);emit(2900);
 sample.source_sequence=1;sample.source_stamp_ns=stamp+6;feed(3000);emit(3000);
 sample.source_sequence=2;sample.source_stamp_ns=stamp-1;feed(3100);emit(3100);
 sample.source_sequence=3;sample.source_stamp_ns=stamp+7;sample.ranges_m.assign(3,std::numeric_limits<double>::infinity());feed(3200);emit(3200);
 const auto pi=std::acos(-1.0);sample.geometry={-pi,pi,2*pi/4095,0,20,0,0};sample.ranges_m.assign(4096,.12345678901234566);sample.source_sequence=4;sample.source_stamp_ns=stamp+8;feed(3300);
 for(int i=0;i<100;++i)append_event(events,Ms{3200},amr::LogLevel::Info,std::string(64,'\0'),std::string(512,'\0'));
 emit(3300);
}
'''
def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('compiler');parser.add_argument('state_library',type=Path);parser.add_argument('--configuration',default='Debug');parser.add_argument('--output',type=Path);args=parser.parse_args()
 source=(ROOT/'cpp/apps/robot_state_agent.cpp').read_text(encoding='utf-8');method=block(source,'void receive_lidar(')
 output=compile_output(args.compiler,args.state_library,args.configuration,CALLBACK.replace('__RECEIVE_LIDAR__',method)+FIXTURE);lines=output.splitlines();frames=[json.loads(line) for line in lines]
 assert len(frames)==14
 for frame in frames[:2]:assert frame['lidar']['reading'] is None and frame['lidar']['quality']=='Unknown'
 first=frames[2]['lidar'];assert first['reading']['ranges_m']==[0,None,2] and first['source_sequence']==0 and first['source_stamp_ns']=='1793441000123456789'
 assert frames[3]['lidar']==first and frames[4]['lidar']==first
 assert frames[5]['lidar']==dict(first,quality='Stale')
 assert all(frames[5][key]['quality']=='Valid' for key in ['battery','odometry','system_resources'])
 recovery=frames[6]['lidar'];assert recovery['received_elapsed_ms']==2600 and recovery['reading']['ranges_m']==[1,2,3]
 assert all(frame['lidar']==recovery for frame in frames[7:10])
 assert frames[10]['lidar']['source_sequence']==1 and frames[10]['lidar']['received_elapsed_ms']==3000
 assert int(frames[11]['lidar']['source_stamp_ns'])<int(frames[10]['lidar']['source_stamp_ns'])
 assert frames[12]['lidar']['reading']['ranges_m']==[None]*3 and frames[12]['lidar']['quality']=='Valid'
 assert len(frames[13]['lidar']['reading']['ranges_m'])==4096 and frames[13]['event_log']['truncated'] is True
 assert frames[13]['event_log']['entries'][0]['message']==(chr(0)*512)
 assert 400000<len(lines[-1].encode('utf-8'))<512*1024
 spec=importlib.util.spec_from_file_location('service',ROOT/'tools/serve_dashboard.py');service=importlib.util.module_from_spec(spec);spec.loader.exec_module(service);buffer=service.StateBuffer(expected_source='robot')
 for frame in frames:buffer.publish(frame)
 assert buffer.snapshot()['stream']['sequence']==14 and service.API_VERSION==8
 if args.output:args.output.write_text(output,encoding='utf-8')
 print('Actual LaserScan callback and 14 production-writer frames validated by API8; 4096 rays + worst escaped EventLog below 512KiB; OFFLINE only')
if __name__=='__main__':main()