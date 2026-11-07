#include "amr/odometry_source.hpp"
#include "amr/odometry_state.hpp"
#include <cassert>
#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>
int main()
{
    auto check = [](bool b, const char *n)
    {if(!b)throw std::runtime_error(n); };
    double y = 123;
    const double pi = std::acos(-1.0), s = std::sqrt(.5);
    check(amr::planar_yaw(0, 0, 0, 1, y) && y == 0, "identity");
    check(amr::planar_yaw(0, 0, s, s, y) && std::abs(y - pi / 2) < 1e-12, "left quarter turn");
    check(amr::planar_yaw(0, 0, -s, s, y) && std::abs(y + pi / 2) < 1e-12, "right quarter turn");
    check(amr::planar_yaw(0, 0, 1, 0, y) && std::abs(y + pi) < 1e-12, "pi maps negative");
    check(amr::planar_yaw(0, 0, -1, 0, y) && std::abs(y + pi) < 1e-12, "negative pi");
    check(amr::planar_yaw(0, 0, -s, -s, y) && std::abs(y - pi / 2) < 1e-12, "quaternion sign equivalent");
    check(amr::planar_yaw(0, 0, s * 1.0005, s * 1.0005, y) && std::abs(y - pi / 2) < 1e-12, "small norm error corrected");
    y = 123;
    check(!amr::planar_yaw(0, 0, 0, 0, y) && y == 123, "zero quaternion atomic");
    check(!amr::planar_yaw(0, 0, 0, 2, y) && y == 123, "bad norm atomic");
    check(!amr::planar_yaw(.1, 0, 0, std::sqrt(.99), y) && y == 123, "nonplanar roll");
    check(!amr::planar_yaw(0, .1, 0, std::sqrt(.99), y) && y == 123, "nonplanar pitch");
    check(!amr::planar_yaw(std::numeric_limits<double>::quiet_NaN(), 0, 0, 1, y) && y == 123, "nan component");
    check(!amr::planar_yaw(0, std::numeric_limits<double>::infinity(), 0, 1, y) && y == 123, "inf component");
    check(!amr::planar_yaw(0, 0, 1e308, 1e308, y) && y == 123, "huge quaternion atomic");
    using namespace amr;
    OdometrySampleGate gate{};
    OdometryReading reading{9, 8, 7, 6, 5, 4};
    OdometrySample sample{};
    assert(gate.observe(sample, reading) == OdometrySampleResult::InvalidStamp && reading.x_m == 9);
    sample.source_stamp_ns = 100;
    sample.source_sequence = 0;
    sample.source_twist_sequence = 17;
    assert(gate.observe(sample, reading) == OdometrySampleResult::Accepted && reading.x_m == 0 && reading.yaw_rad == 0);
    sample.x_m = 5;
    sample.source_stamp_ns = 101;
    assert(gate.observe(sample, reading) == OdometrySampleResult::Duplicate && reading.x_m == 0);
    sample.source_sequence = 1;
    sample.source_stamp_ns = 101;
    assert(gate.observe(sample, reading) == OdometrySampleResult::Duplicate);
    sample.source_stamp_ns = 102;
    assert(gate.observe(sample, reading) == OdometrySampleResult::Accepted && reading.x_m == 5);
    sample.source_sequence = 2;
    sample.source_stamp_ns = 90;
    assert(gate.observe(sample, reading) == OdometrySampleResult::AcceptedWithClockAdjustment);
    sample.source_sequence = 0;
    sample.source_stamp_ns = 103;
    sample.x_m = 6;
    assert(gate.observe(sample, reading) == OdometrySampleResult::ResetPending && reading.x_m == 5);
    sample.source_stamp_ns = 104;
    assert(gate.observe(sample, reading) == OdometrySampleResult::Duplicate && reading.x_m == 5);
    sample.source_sequence = 1;
    sample.source_stamp_ns = 105;
    assert(gate.observe(sample, reading) == OdometrySampleResult::AcceptedAfterReset && reading.x_m == 6);
    sample.source_sequence = 2;
    sample.source_stamp_ns = 106;
    sample.qw = 0;
    assert(gate.observe(sample, reading) == OdometrySampleResult::InvalidOrientation && reading.x_m == 6);
    sample.qw = 1;
    sample.frame_id = "map";
    assert(gate.observe(sample, reading) == OdometrySampleResult::InvalidFrames);
    sample.frame_id = "odom";
    sample.angular_z_radps = std::numeric_limits<double>::infinity();
    assert(gate.observe(sample, reading) == OdometrySampleResult::InvalidReading);
    sample.angular_z_radps = 0;
    assert(gate.observe(sample, reading) == OdometrySampleResult::Accepted);
    {
        OdometrySampleGate wrap{};
        sample.source_sequence = std::numeric_limits<std::uint32_t>::max();
        sample.source_stamp_ns = 1;
        assert(odometry_sample_accepted(wrap.observe(sample, reading)));
        sample.source_sequence = 0;
        sample.source_stamp_ns = 2;
        assert(wrap.observe(sample, reading) == OdometrySampleResult::ResetPending);
        sample.source_sequence = 1;
        sample.source_stamp_ns = 3;
        assert(wrap.observe(sample, reading) == OdometrySampleResult::AcceptedAfterReset);
    }
    {
        OdometryState state{};
        OdometrySampleGate filter{};
        OdometrySample input{};
        input.source_stamp_ns = 1;
        const auto start = std::chrono::steady_clock::time_point{};
        auto feed = [&](int elapsed)
        {OdometryReading r{};auto result=filter.observe(input,r);
        if(odometry_sample_accepted(result)) update_odometry_reading(state,r,start+std::chrono::milliseconds{elapsed});
        return result; };
        feed(100);
        assert(state.reading && state.reading->x_m == 0);
        input.x_m = 999;
        input.source_stamp_ns = 2;
        assert(feed(500) == OdometrySampleResult::Duplicate);
        assert(*state.received_at == start + std::chrono::milliseconds{100});
        assert(refresh_odometry_quality(state, start + std::chrono::milliseconds{1599}, std::chrono::milliseconds{1500}) == OdometryRefreshResult::Fresh);
        assert(refresh_odometry_quality(state, start + std::chrono::milliseconds{1600}, std::chrono::milliseconds{1500}) == OdometryRefreshResult::Stale);
        input.source_sequence = 1;
        input.source_stamp_ns = 3;
        input.x_m = 1;
        feed(1700);
        assert(state.quality == DataQuality::Valid && state.reading->x_m == 1);
    }
    std::cout << "odometry source checks passed\n";
}
