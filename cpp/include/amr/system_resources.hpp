#pragma once

#include "amr/robot_state.hpp"
#include <chrono>

/*
本文件为硬件状态 (cpu, 内存, 温度) 公共接口
*/

namespace amr
{
    enum class SystemResourcesUpdateResult
    {
        Update,
        InvalidCpuUsage,
        InvalidMemoryUsage,
        InvalidTemperature
    };

    SystemResourcesUpdateResult update_system_resources_reading(
        SystemResourcesState &resources,
        const SystemResourcesReading &reading,
        std::chrono::steady_clock::time_point received_at);

    enum class SystemResourcesRefreshResult
    {
        NoData,
        Preserved,
        Fresh,
        Stale,
        InvalidTimeout,
        InvalidTimeOrder
    };

    SystemResourcesRefreshResult refresh_system_resources_quality(
        SystemResourcesState &resources,
        std::chrono::steady_clock::time_point now,
        std::chrono::milliseconds timeout);
} // namespace amr
