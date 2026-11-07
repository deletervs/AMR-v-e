#pragma once
#include "amr/single_executor.hpp"
#include <string>
namespace amr {
// Caller explicitly initializes ROS and renews the graph permit outside the output thread.
class RosVelocitySink:public VelocitySink {
public: virtual void renew_exclusivity()=0;
};
std::unique_ptr<RosVelocitySink> make_ros_velocity_sink(const std::string &expected_driver_node, std::uint64_t authorized_until_ms);
}
