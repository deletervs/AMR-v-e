#include "amr/battery_state.hpp"
#include <cassert>
#include <chrono>
#include <iostream>

/*
本文件测试 battery_state.cpp 文件中函数是否能更新旧数据

*/

int main()
{
    using Clock = std::chrono::steady_clock;
    using Ms = std::chrono::milliseconds;

    amr::RobotState state{};
    const Clock::time_point first_received_at{};

    //第一次接收数据, 确认电压 收到时间 quality是否正确
    amr::update_battery_reading(state.battery, 12.0, first_received_at);

    assert(state.battery.voltage_v.has_value());
    assert(state.battery.voltage_v == 12.0);
    assert(state.battery.received_at.has_value());
    assert(state.battery.received_at == first_received_at);
    assert(state.battery.quality == amr::DataQuality::Valid);

    //模拟旧数据过期
    state.battery.quality = amr::DataQuality::Stale;
    const Clock::time_point next_received_at = first_received_at + Ms{3000};

    amr::update_battery_reading(state.battery, 12.5, next_received_at);

    assert(state.battery.voltage_v == 12.5);
    assert(state.battery.received_at == next_received_at);
    assert(state.battery.quality == amr::DataQuality::Valid);
    assert(!state.motion_enabled);

    //验证 接收-过期-接收新读数 的过程
    using Refresh = amr::BatteryRefreshResult;
    using Quality = amr::DataQuality;
    const Ms timeout{2000};

    // 完整读数：未过期 -> 达到超时 -> 收到新读数
    amr::RobotState monitored{};
    amr::update_battery_reading(monitored.battery, 12.0, first_received_at);

    auto result = amr::refresh_battery_quality(
        monitored.battery, first_received_at + Ms{1000}, timeout);
    assert(result == Refresh::Fresh);
    assert(monitored.battery.quality == Quality::Valid);
    assert(monitored.battery.voltage_v == 12.0);
    assert(monitored.battery.received_at == first_received_at);

    result = amr::refresh_battery_quality(
        monitored.battery, first_received_at + Ms{2000}, timeout);
    assert(result == Refresh::Stale);
    assert(monitored.battery.quality == Quality::Stale);
    assert(monitored.battery.voltage_v == 12.0);
    assert(monitored.battery.received_at == first_received_at);

    amr::update_battery_reading(monitored.battery, 12.5, next_received_at);
    result = amr::refresh_battery_quality(
        monitored.battery, next_received_at + Ms{1}, timeout);
    assert(result == Refresh::Fresh);
    assert(monitored.battery.quality == Quality::Valid);
    assert(monitored.battery.voltage_v == 12.5);
    assert(monitored.battery.received_at == next_received_at);

    // 参数错误：保留读数和时间，但无法确认数据时效
    result = amr::refresh_battery_quality(
        monitored.battery, next_received_at, Ms{0});
    assert(result == Refresh::InvalidTimeout);
    assert(monitored.battery.quality == Quality::Unknown);
    assert(monitored.battery.voltage_v == 12.5);
    assert(monitored.battery.received_at == next_received_at);

    result = amr::refresh_battery_quality(
        monitored.battery, next_received_at - Ms{1}, timeout);
    assert(result == Refresh::InvalidTimeOrder);
    assert(monitored.battery.quality == Quality::Unknown);
    assert(monitored.battery.voltage_v == 12.5);
    assert(monitored.battery.received_at == next_received_at);
    assert(!monitored.motion_enabled);

    // 缺接收时间；缺数据判断优先于时间参数检查
    amr::BatteryState missing_time{};
    missing_time.voltage_v = 12.0;
    missing_time.quality = Quality::Valid;
    result = amr::refresh_battery_quality(
        missing_time, first_received_at, Ms{0});
    assert(result == Refresh::NoData);
    assert(missing_time.quality == Quality::Unknown);
    assert(missing_time.voltage_v == 12.0);
    assert(!missing_time.received_at.has_value());

    // 缺电压
    amr::BatteryState missing_voltage{};
    missing_voltage.received_at = first_received_at;
    missing_voltage.quality = Quality::Stale;
    result = amr::refresh_battery_quality(
        missing_voltage, first_received_at, timeout);
    assert(result == Refresh::NoData);
    assert(missing_voltage.quality == Quality::Unknown);
    assert(!missing_voltage.voltage_v.has_value());
    assert(missing_voltage.received_at == first_received_at);

    // 故障状态优先保留，即使时间参数不合法
    monitored.battery.quality = Quality::Fault;
    result = amr::refresh_battery_quality(
        monitored.battery, next_received_at - Ms{1}, Ms{0});
    assert(result == Refresh::Preserved);
    assert(monitored.battery.quality == Quality::Fault);
    assert(monitored.battery.voltage_v == 12.5);
    assert(monitored.battery.received_at == next_received_at);

    // 不支持的状态优先保留，即使没有读数
    amr::BatteryState unsupported{};
    unsupported.quality = Quality::Unsupported;
    result = amr::refresh_battery_quality(
        unsupported, first_received_at, timeout);
    assert(result == Refresh::Preserved);
    assert(unsupported.quality == Quality::Unsupported);
    assert(!unsupported.voltage_v.has_value());
    assert(!unsupported.received_at.has_value());

    std::cout << "battery state test passed\n";

    return 0;
}