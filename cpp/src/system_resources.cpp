#include "amr/system_resources.hpp"
#include "amr/data_freshness.hpp"

#include <cmath>
#include <stdexcept>

/*
本文件实现 system_resources.hpp 的核心规则
*/

namespace amr
{
    SystemResourcesUpdateResult update_system_resources_reading( // 接收 写入
        SystemResourcesState &resources,
        const SystemResourcesReading &reading,
        std::chrono::steady_clock::time_point received_at)
    {
        // 先检查 reading 的所有属性: 普通数值要检查合法性，温度则允许为空。
        if (!std::isfinite(reading.cpu_usage_percent) ||
            !(reading.cpu_usage_percent >= 0.0) ||
            !(reading.cpu_usage_percent <= 100.0))
        {
            return SystemResourcesUpdateResult::InvalidCpuUsage;
        }

        if (!std::isfinite(reading.memory_total_bytes) ||
            !std::isfinite(reading.memory_used_bytes) ||
            !(reading.memory_used_bytes <= reading.memory_total_bytes) ||
            !(reading.memory_total_bytes > 0))
        {
            return SystemResourcesUpdateResult::InvalidMemoryUsage;
        }

        if (reading.cpu_temperature_c.has_value() &&
            !std::isfinite(reading.cpu_temperature_c.value()))
        {
            return SystemResourcesUpdateResult::InvalidTemperature;
        }

        // 都合法就赋值
        resources.reading = reading;
        resources.received_at = received_at;
        resources.quality = DataQuality::Valid;

        return SystemResourcesUpdateResult::Update;
    }

    SystemResourcesRefreshResult refresh_system_resources_quality( // 根据时间更新状态
        SystemResourcesState &resources,
        std::chrono::steady_clock::time_point now,
        std::chrono::milliseconds timeout)
    {
        // 1. 判断 resources 三个属性是否有效
        if (resources.quality == DataQuality::Fault ||
            resources.quality == DataQuality::Unsupported)
        {
            return SystemResourcesRefreshResult::Preserved;
        }

        if (!resources.reading.has_value() ||
            !resources.received_at.has_value())
        {
            resources.quality = DataQuality::Unknown;
            return SystemResourcesRefreshResult::NoData;
        }

        // 2. 处理新鲜度返回
        const auto result = check_freshness(
            resources.received_at.value(),
            now,
            timeout);

        switch (result)
        {
        case FreshnessResult::Fresh:
            resources.quality = DataQuality::Valid;
            return SystemResourcesRefreshResult::Fresh;

        case FreshnessResult::Stale:
            resources.quality = DataQuality::Stale;
            return SystemResourcesRefreshResult::Stale;

        case FreshnessResult::InvalidTimeout:
            resources.quality = DataQuality::Unknown;
            return SystemResourcesRefreshResult::InvalidTimeout;

        case FreshnessResult::InvalidTimeOrder:
            resources.quality = DataQuality::Unknown;
            return SystemResourcesRefreshResult::InvalidTimeOrder;
        };

        throw std::logic_error("Unhandled FreshnessResult");
    }
} // namespace amr
