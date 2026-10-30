// ROS 电压消息诊断探针：只订阅，不创建运动发布者；还不是仪表盘状态适配器。
#include <ros/ros.h>
#include <handsfree_msgs/robot_state.h>

#include <chrono>
#include <cmath>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <limits>

namespace
{
    class BatteryProbe
    {
    public:
        void receive(const handsfree_msgs::robot_state::ConstPtr &message)
        {
            const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - started_).count();
            std::cout << std::setprecision(std::numeric_limits<double>::max_digits10)
                      << "{\"topic\":\"/handsfree/robot_state\",\"sequence\":" << ++count_
                      << ",\"received_elapsed_ms\":" << elapsed
                      << ",\"header_seq\":" << message->header.seq
                      << ",\"header_stamp_sec\":" << message->header.stamp.sec
                      << ",\"header_stamp_nsec\":" << message->header.stamp.nsec
                      << ",\"battery_voltage_raw\":";
            number_or_null(message->battery_voltage);
            std::cout << ",\"system_time_raw\":";
            number_or_null(message->system_time);
            std::cout << "}\n" << std::flush;
            if (count_ >= 5 || !std::cout) ros::shutdown();
        }

        std::uint64_t count() const { return count_; }

    private:
        static void number_or_null(double value)
        {
            if (std::isfinite(value)) std::cout << value;
            else std::cout << "null";
        }
        std::chrono::steady_clock::time_point started_{std::chrono::steady_clock::now()};
        std::uint64_t count_{0};
    };
}

int main(int argc, char **argv)
{
    // NoRosout 避免注册日志发布者；探针只有数据订阅和本机退出计时器。
    ros::init(argc, argv, "rs_battery_probe", ros::init_options::NoRosout);
    ros::NodeHandle node;
    BatteryProbe probe;
    const auto subscriber = node.subscribe("/handsfree/robot_state", 1, &BatteryProbe::receive, &probe);
    const auto timeout = node.createWallTimer(ros::WallDuration{30.0},
        [](const ros::WallTimerEvent &) {
            std::cerr << "battery probe deadline reached\n";
            ros::shutdown();
        }, true);
    ros::spin();
    return probe.count() == 5 ? 0 : 1;
}
