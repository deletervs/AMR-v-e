#include "amr/odometry_state.hpp"
#include "amr/data_freshness.hpp"

#include <cmath>
#include <stdexcept>

/*
本文件实现 odometry_state.hpp 的核心规则
*/

namespace amr
{
    OdometryUpdateResult update_odometry_reading( // 检查并保存传入的里程计读数
        OdometryState &odometry,
        const OdometryReading &reading,
        std::chrono::steady_clock::time_point received_at)
    {
        if (!std::isfinite(reading.x_m) ||
            !std::isfinite(reading.y_m) ||
            !std::isfinite(reading.yaw_rad) ||
            !std::isfinite(reading.linear_x_mps) ||
            !std::isfinite(reading.linear_y_mps) ||
            !std::isfinite(reading.angular_z_radps))
        {
            return OdometryUpdateResult::InvalidReading;
        }

        odometry.reading = reading;
        odometry.received_at = received_at;
        odometry.quality = DataQuality::Valid;

        return OdometryUpdateResult::Updated;
    };

    OdometryRefreshResult refresh_odometry_quality( // 重新判断已有样本是否可用。只更新 quality，保留读数和接收时间
        OdometryState &odometry,
        std::chrono::steady_clock::time_point now,
        std::chrono::milliseconds timeout)
    {
        // 1. 先判断 odometry 的三个属性: quality reading received_at 是否有效
        if (odometry.quality == DataQuality::Fault ||
            odometry.quality == DataQuality::Unsupported)
        {
            return OdometryRefreshResult::Preserved;
        };

        if (!odometry.reading.has_value() ||
            !odometry.received_at.has_value())
        {
            odometry.quality = DataQuality::Unknown;
            return OdometryRefreshResult::NoData;
        };

        // 2. 开始处理新鲜度的二次包装返回, 和 battery_state.cpp 一样, 调用 check_freshness 函数
        const auto result = check_freshness(
            odometry.received_at.value(),
            now,
            timeout);

        switch (result)
        {
        case FreshnessResult::Fresh:
            odometry.quality = DataQuality::Valid;
            return OdometryRefreshResult::Fresh;

        case FreshnessResult::Stale:
            odometry.quality = DataQuality::Stale;
            return OdometryRefreshResult::Stale;

        case FreshnessResult::InvalidTimeout:
            odometry.quality = DataQuality::Unknown;
            return OdometryRefreshResult::InvalidTimeout;

        case FreshnessResult::InvalidTimeOrder:
            odometry.quality = DataQuality::Unknown;
            return OdometryRefreshResult::InvalidTimeOrder;
        }

        throw std::logic_error("Unhandled FreshnessResult");
    }
} // namespace amr
