#pragma once
#include "amr/robot_state.hpp"
#include <chrono>

// 本文件声明怎么更新电池状态数据

namespace amr
{
    void update_battery_reading(
        BatteryState &battery, //
        double voltage_v,
        std::chrono::steady_clock::time_point received_at);

    enum class BatteryRefreshResult
    {                    //表示这次刷新得到什么结果
        NoData,          //缺少电压或者接收时间 ->Unknown
        Preserved,       //保留状态
        Fresh,           //数据新鲜 ->Valid
        Stale,           //数据超时 ->Stale
        InvalidTimeout,  //超时时长小于等于0, 数据不合理 ->Unknown
        InvalidTimeOrder //now 早于 received_at, 数据不合理 ->Unknown
    };

    BatteryRefreshResult refresh_battery_quality(
        BatteryState &battery,
        std::chrono::steady_clock::time_point now,
        std::chrono::milliseconds timeout);
} // namespace amr
