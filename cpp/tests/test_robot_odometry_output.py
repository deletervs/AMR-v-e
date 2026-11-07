"""Compile the actual collector pair callback/writer and validate offline frames via API8."""
import argparse
import importlib.util
import json
from pathlib import Path
from test_robot_battery_output import ROOT,compile_output
PAIR=r'''
#include "amr/odometry_source.hpp"
#include <cassert>
#include <memory>
namespace ros {
struct Time {std::uint64_t value{0};std::uint64_t toNSec()const{return value;}bool operator!=(const Time &b)const{return value!=b.value;}static Time now();};
std::uint64_t current=1793376000000000000ULL;
Time Time::now(){return {current};}
}
namespace geometry_msgs {
struct Axis{double x{0},y{0},z{0};};struct Quaternion{double x{0},y{0},z{0},w{1};};
struct Header{ros::Time stamp{};std::uint32_t seq{0};std::string frame_id;};
struct PoseStamped{using ConstPtr=std::shared_ptr<const PoseStamped>;Header header;struct {Axis position;Quaternion orientation;}pose;};
struct TwistStamped{using ConstPtr=std::shared_ptr<const TwistStamped>;Header header;struct{Axis linear,angular;}twist;};
}
class PairFixture {
public:
 PairFixture(amr::RobotState&s,OdometryObservation&o,amr::EventLog&e):state_{s},odometry_{o},start_{Clock::now()},events_{e}{}
 __RECEIVE_PAIR__
 amr::RobotState& state_;OdometryObservation& odometry_;Clock::time_point start_;amr::EventLog& events_;
 amr::OdometrySampleGate odometry_gate_{};geometry_msgs::PoseStamped::ConstPtr pose_{};geometry_msgs::TwistStamped::ConstPtr twist_{};
};
void pair_callback_checks(){
 amr::RobotState state{};OdometryObservation observation{};amr::EventLog events{};PairFixture fixture{state,observation,events};
 auto pose=std::make_shared<geometry_msgs::PoseStamped>();auto twist=std::make_shared<geometry_msgs::TwistStamped>();
 auto set=[&](std::uint64_t stamp,std::uint32_t seq){pose->header={ros::Time{stamp},seq,"odom"};twist->header={ros::Time{stamp},seq+7,"base_link"};fixture.pose_=pose;fixture.twist_=twist;};
 set(ros::current,0);fixture.twist_.reset();fixture.receive_pair();assert(!state.odometry.reading);
 fixture.twist_=twist;twist->header.stamp.value++;fixture.receive_pair();assert(!state.odometry.reading);
 twist->header.stamp.value--;fixture.receive_pair();assert(state.odometry.reading && state.odometry.reading->x_m==0);
 assert(observation.source_sequence==0 && observation.source_twist_sequence==7);
 assert(!fixture.pose_ && !fixture.twist_);const auto initial=observation.source_stamp_ns;
 set(ros::current,1);pose->pose.position.x=999;fixture.receive_pair();assert(observation.source_stamp_ns==initial && state.odometry.reading->x_m==0);
 set(0,1);fixture.receive_pair();assert(observation.source_stamp_ns==initial);
 set(ros::current+1,1);fixture.receive_pair();assert(observation.source_stamp_ns==initial);
 set(ros::current-1500000000ULL,1);fixture.receive_pair();assert(observation.source_stamp_ns==initial);
 set(ros::current-1,1);pose->header.frame_id="map";fixture.receive_pair();assert(observation.source_stamp_ns==initial);
 set(ros::current-1,1);pose->pose.orientation.w=0;fixture.receive_pair();assert(observation.source_stamp_ns==initial);
 set(ros::current-1,1);pose->pose.orientation={0,0,std::sqrt(.5),std::sqrt(.5)};pose->pose.position={1.2,-3,0};twist->twist.linear={.2,-.1,0};twist->twist.angular.z=.3;
 fixture.receive_pair();assert(observation.source_stamp_ns==ros::current-1 && observation.source_sequence==1);
 const auto&r=*state.odometry.reading;assert(r.x_m==1.2 && r.y_m==-3 && r.linear_x_mps==.2 && r.linear_y_mps==-.1 && r.angular_z_radps==.3 && std::abs(r.yaw_rad-std::acos(-1.0)/2)<1e-12);
}
'''
FIXTURE=r'''
int main(){
 pair_callback_checks();
 amr::RobotState state{};BatteryObservation battery{};battery.supported=true;OdometryObservation odometry{};amr::EventLog events{};
 append_event(events,Ms{0},amr::LogLevel::Info,"robot","Offline M2-003 fixture: synthetic pose/twist and virtual delivery times");
 amr::OdometrySampleGate gate{};amr::OdometrySample sample{};const auto origin=Clock::time_point{};std::uint64_t sequence=0;
 constexpr std::uint64_t stamp=1793376000123456789ULL;
 auto feed=[&](int elapsed){amr::OdometryReading reading{};const auto result=gate.observe(sample,reading);
 if(amr::odometry_sample_accepted(result)){amr::update_odometry_reading(state.odometry,reading,origin+Ms{elapsed});odometry.received_elapsed=Ms{elapsed};odometry.source_stamp_ns=sample.source_stamp_ns;odometry.source_sequence=sample.source_sequence;odometry.source_twist_sequence=sample.source_twist_sequence;odometry.frame_id=sample.frame_id;odometry.child_frame_id=sample.child_frame_id;}return result;};
 auto emit=[&](int elapsed){amr::update_system_resources_reading(state.system_resource,{25,1024,4096,35},origin+Ms{elapsed});
 amr::update_battery_reading(state.battery,11.6,origin+Ms{elapsed});battery.received_elapsed=Ms{elapsed};battery.source_stamp_ns=stamp+elapsed*1000000ULL;battery.source_time_raw=1+elapsed;
 amr::refresh_odometry_quality(state.odometry,origin+Ms{elapsed},Ms{1500});write_frame(state,Ms{elapsed},++sequence,"offline-fixture","",events,battery,odometry);};
 emit(0);odometry.supported=true;emit(1);feed(500);emit(500);
 sample.source_stamp_ns=stamp;sample.source_sequence=0;sample.source_twist_sequence=7;feed(1000);emit(1000);
 sample.source_stamp_ns=stamp+1;sample.x_m=999;feed(2000);emit(2000);emit(2499);emit(2500);
 sample.source_sequence=10;sample.source_twist_sequence=17;sample.source_stamp_ns=stamp+2;sample.x_m=1.2;sample.y_m=-3;sample.qz=std::sqrt(.5);sample.qw=std::sqrt(.5);sample.linear_x_mps=.2;sample.linear_y_mps=-.1;sample.angular_z_radps=.3;feed(2600);emit(2600);
 sample.source_stamp_ns=stamp+3;sample.source_sequence=11;sample.qw=sample.qz=0;feed(2700);emit(2700);sample.qw=1;
 sample.source_stamp_ns=stamp+4;sample.source_sequence=0;feed(2800);emit(2800);
 sample.source_stamp_ns=stamp+5;feed(2900);emit(2900);
 sample.source_stamp_ns=stamp+6;sample.source_sequence=1;feed(3000);emit(3000);
 sample.source_stamp_ns=stamp-1;sample.source_sequence=2;feed(3100);emit(3100);
 sample={};sample.source_stamp_ns=stamp+7;sample.source_sequence=3;feed(3200);emit(3200);
}
'''
def block(source,marker):
 start=source.index(marker);opened=source.index('{',start);depth=0
 for i in range(opened,len(source)):
  if source[i]=='{':depth+=1
  elif source[i]=='}':
   depth-=1
   if not depth:return source[start:i+1]
 raise AssertionError('incomplete method')
def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('compiler');parser.add_argument('state_library',type=Path);parser.add_argument('--configuration',default='Debug');parser.add_argument('--output',type=Path);args=parser.parse_args()
 method=block((ROOT/'cpp/apps/robot_state_agent.cpp').read_text(encoding='utf-8'),'void receive_pair()')
 output=compile_output(args.compiler,args.state_library,args.configuration,PAIR.replace('__RECEIVE_PAIR__',method)+FIXTURE);frames=[json.loads(line) for line in output.splitlines()]
 assert len(frames)==14
 assert frames[0]['capabilities']=={'battery':True,'odometry':False,'lidar':False}
 for frame in frames[:3]:
  o=frame['odometry'];assert o['reading'] is None and o['quality']=='Unknown';assert all(o[key] is None for key in o if key not in ('reading','quality'))
 first=frames[3]['odometry'];assert first['source_sequence']==0 and first['source_twist_sequence']==7 and first['received_elapsed_ms']==1000
 assert first['source_stamp_ns']=='1793376000123456789' and all(value==0 for value in first['reading'].values())
 assert frames[4]['odometry']==first and frames[5]['odometry']==first
 assert frames[6]['odometry']==dict(first,quality='Stale') and frames[6]['battery']['quality']==frames[6]['system_resources']['quality']=='Valid'
 recovery=frames[7]['odometry'];assert recovery['quality']=='Valid' and recovery['received_elapsed_ms']==2600 and abs(recovery['reading']['yaw_rad']-1.5707963267948966)<1e-12
 assert all(frame['odometry']==recovery for frame in frames[8:11])
 assert frames[11]['odometry']['received_elapsed_ms']==3000 and frames[11]['odometry']['source_sequence']==1
 assert int(frames[12]['odometry']['source_stamp_ns'])<int(frames[11]['odometry']['source_stamp_ns'])
 assert all(value==0 for value in frames[13]['odometry']['reading'].values())
 spec=importlib.util.spec_from_file_location('service',ROOT/'tools/serve_dashboard.py');service=importlib.util.module_from_spec(spec);spec.loader.exec_module(service);buffer=service.StateBuffer(expected_source='robot')
 for frame in frames:buffer.publish(frame)
 assert buffer.snapshot()['stream']['sequence']==14 and service.API_VERSION==8
 if args.output:args.output.write_text(output,encoding='utf-8')
 print('Actual ROS-pair callback invariants and 14 actual-writer frames passed API8; offline only, no ROS execution')
if __name__=='__main__':main()