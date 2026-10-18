#include "amr/odometry_state.hpp"

#include <array>
#include <cassert>
#include <chrono>
#include <iostream>
#include <limits>

namespace
{
    void assert_reading(const amr::OdometryReading &actual,
                        const amr::OdometryReading &expected)
    {
        assert(actual.x_m == expected.x_m);
        assert(actual.y_m == expected.y_m);
        assert(actual.yaw_rad == expected.yaw_rad);
        assert(actual.linear_x_mps == expected.linear_x_mps);
        assert(actual.linear_y_mps == expected.linear_y_mps);
        assert(actual.angular_z_radps == expected.angular_z_radps);
    }
}

int main()
{
    using Clock = std::chrono::steady_clock;
    using Ms = std::chrono::milliseconds;
    using Update = amr::OdometryUpdateResult;
    using Refresh = amr::OdometryRefreshResult;
    using Quality = amr::DataQuality;

    const Clock::time_point start{};
    const Ms timeout{1000};
    amr::RobotState robot{};
    assert(!robot.odometry.reading.has_value());
    assert(!robot.odometry.received_at.has_value());
    assert(robot.odometry.quality == Quality::Unknown);
    assert(amr::refresh_odometry_quality(robot.odometry, start, timeout) == Refresh::NoData);
    assert(!robot.odometry.reading.has_value());
    assert(!robot.odometry.received_at.has_value());
    assert(robot.odometry.quality == Quality::Unknown);

    // 原点与静止的零值是一份有效读数，不等同于没有读数。
    const amr::OdometryReading zero{};
    assert(amr::update_odometry_reading(robot.odometry, zero, start) == Update::Updated);
    assert_reading(robot.odometry.reading.value(), zero);
    assert(robot.odometry.received_at == start);
    assert(robot.odometry.quality == Quality::Valid);

    const amr::OdometryReading first{1.0, 2.0, 0.5, 0.25, 0.125, 0.0625};
    const auto received = start + Ms{500};
    assert(amr::update_odometry_reading(robot.odometry, first, received) == Update::Updated);
    assert_reading(robot.odometry.reading.value(), first);
    assert(robot.odometry.received_at == received);

    assert(amr::refresh_odometry_quality(robot.odometry, received + Ms{999}, timeout) == Refresh::Fresh);
    assert(robot.odometry.quality == Quality::Valid);
    assert(amr::refresh_odometry_quality(robot.odometry, received + Ms{1000}, timeout) == Refresh::Stale);
    assert(robot.odometry.quality == Quality::Stale);
    assert_reading(robot.odometry.reading.value(), first);
    assert(robot.odometry.received_at == received);

    const amr::OdometryReading second{-1.0, -2.0, -0.5, -0.25, -0.125, -0.0625};
    const auto next_received = received + Ms{1500};
    assert(amr::update_odometry_reading(robot.odometry, second, next_received) == Update::Updated);
    assert_reading(robot.odometry.reading.value(), second);
    assert(robot.odometry.received_at == next_received);
    assert(robot.odometry.quality == Quality::Valid);

    // 六个字段各自验证 NaN、正无穷和负无穷。
    const std::array<double, 3> invalid_numbers{
        std::numeric_limits<double>::quiet_NaN(),
        std::numeric_limits<double>::infinity(),
        -std::numeric_limits<double>::infinity()};
    robot.odometry.quality = Quality::Stale;
    for (const double invalid_number : invalid_numbers)
    {
        std::array<amr::OdometryReading, 6> invalid{second, second, second, second, second, second};
        invalid[0].x_m = invalid_number;
        invalid[1].y_m = invalid_number;
        invalid[2].yaw_rad = invalid_number;
        invalid[3].linear_x_mps = invalid_number;
        invalid[4].linear_y_mps = invalid_number;
        invalid[5].angular_z_radps = invalid_number;
        for (const auto &bad : invalid)
        {
            assert(amr::update_odometry_reading(robot.odometry, bad, next_received + Ms{500}) == Update::InvalidReading);
            assert_reading(robot.odometry.reading.value(), second);
            assert(robot.odometry.received_at == next_received);
            assert(robot.odometry.quality == Quality::Stale);
        }
    }

    amr::OdometryState empty{};
    auto bad_on_empty = second;
    bad_on_empty.x_m = invalid_numbers[0];
    assert(amr::update_odometry_reading(empty, bad_on_empty, start) == Update::InvalidReading);
    assert(!empty.reading.has_value());
    assert(!empty.received_at.has_value());
    assert(empty.quality == Quality::Unknown);

    // 缺数据优先于时间参数错误；保留已有的半份状态供诊断。
    amr::OdometryState missing_time{};
    missing_time.reading = first;
    missing_time.quality = Quality::Valid;
    assert(amr::refresh_odometry_quality(missing_time, start, Ms{0}) == Refresh::NoData);
    assert(missing_time.quality == Quality::Unknown);
    assert_reading(missing_time.reading.value(), first);
    assert(!missing_time.received_at.has_value());

    amr::OdometryState missing_reading{};
    missing_reading.received_at = start;
    missing_reading.quality = Quality::Stale;
    assert(amr::refresh_odometry_quality(missing_reading, start, timeout) == Refresh::NoData);
    assert(missing_reading.quality == Quality::Unknown);
    assert(!missing_reading.reading.has_value());
    assert(missing_reading.received_at == start);

    // 参数错误不清掉读数，也不更新它的接收时间。
    assert(amr::refresh_odometry_quality(robot.odometry, next_received, Ms{0}) == Refresh::InvalidTimeout);
    assert(robot.odometry.quality == Quality::Unknown);
    assert(amr::refresh_odometry_quality(robot.odometry, next_received, Ms{-1}) == Refresh::InvalidTimeout);
    assert(amr::refresh_odometry_quality(robot.odometry, next_received - Ms{1}, timeout) == Refresh::InvalidTimeOrder);
    assert(robot.odometry.quality == Quality::Unknown);
    assert_reading(robot.odometry.reading.value(), second);
    assert(robot.odometry.received_at == next_received);

    // 时间倒序与无效超时同时出现时，超时错误优先。
    assert(amr::refresh_odometry_quality(robot.odometry, next_received - Ms{1}, Ms{0}) == Refresh::InvalidTimeout);
    // 恢复合法时间参数后，已有样本可以重新判断为新鲜。
    assert(amr::refresh_odometry_quality(robot.odometry, next_received, timeout) == Refresh::Fresh);
    assert(robot.odometry.quality == Quality::Valid);
    assert_reading(robot.odometry.reading.value(), second);
    assert(robot.odometry.received_at == next_received);

    // Fault/Unsupported 的优先级最高，即使缺数据或参数错误也保留。
    robot.odometry.quality = Quality::Fault;
    assert(amr::refresh_odometry_quality(robot.odometry, start, Ms{0}) == Refresh::Preserved);
    assert(robot.odometry.quality == Quality::Fault);
    assert_reading(robot.odometry.reading.value(), second);
    assert(robot.odometry.received_at == next_received);
    amr::OdometryState unsupported{};
    unsupported.quality = Quality::Unsupported;
    assert(amr::refresh_odometry_quality(unsupported, start, Ms{0}) == Refresh::Preserved);
    assert(unsupported.quality == Quality::Unsupported);
    assert(!unsupported.reading.has_value());
    assert(!unsupported.received_at.has_value());

    std::cout << "odometry state test passed\n";
}
