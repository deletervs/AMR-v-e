#pragma once

#include "amr/robot_state.hpp"
#include <chrono>

/*
本文件为里程计 odometry 公共接口, 规定调用者可以怎样更新里程计、怎样刷新质量，以及如何判断操作结果
*/

namespace amr
{
    enum class OdometryUpdateResult
    {
        Updated,
        InvalidReading
    };

    OdometryUpdateResult update_odometry_reading(
        OdometryState &odometry,
        const OdometryReading &reading,
        std::chrono::steady_clock::time_point received_at);

    enum class OdometryRefreshResult
    {
        NoData,
        Preserved,
        Fresh,
        Stale,
        InvalidTimeout,
        InvalidTimeOrder
    };

    OdometryRefreshResult refresh_odometry_quality(
        OdometryState &odometry,
        std::chrono::steady_clock::time_point now,
        std::chrono::milliseconds timeout);
} // namespace amr
