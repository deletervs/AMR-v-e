#include "amr/robot_state.hpp"
#include <cassert> //提供assert(), 判断括号内的条件真假
#include <chrono>
#include <iostream> //std::cin 和 std::cout

int main()
{
    amr::RobotState state{}; //实例化

    //检查几个默认状态
    assert(!state.connected);
    assert(!state.motion_enabled);
    assert(!state.battery.voltage_v.has_value());
    assert(!state.battery.received_at.has_value());
    assert(state.battery.quality == amr::DataQuality::Unknown);

    //写入模拟电压,读取
    state.battery.voltage_v = 12.0;
    state.battery.quality = amr::DataQuality::Valid;

    const std::chrono::steady_clock::time_point received_at{};
    state.battery.received_at = received_at;

    assert(state.battery.voltage_v.has_value());
    assert(state.battery.voltage_v == 12.0);
    assert(state.battery.quality == amr::DataQuality::Valid);
    assert(state.battery.received_at.has_value());
    assert(state.battery.received_at == received_at);

    assert(!state.motion_enabled);

    std::cout << "robot state test passed\n";

    return 0;
}