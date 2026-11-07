#include "amr/lidar_source.hpp"
#include <cassert>
#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>
int main()
{
    const double pi = std::acos(-1.0), nan = std::numeric_limits<double>::quiet_NaN(), inf = std::numeric_limits<double>::infinity();
    auto check = [](bool b, const char *n)
    {if(!b)throw std::runtime_error(n); };
    auto valid = [](double a, double b, double inc, double lo, double hi, double scan, double step, unsigned n)
    { return amr::lidar_geometry_valid(a, b, inc, lo, hi, scan, step, n); };
    check(valid(-pi / 2, pi / 2, pi / 4, .1, 10, .4, .1, 5), "normal scan");
    check(valid(pi / 2, -pi / 2, -pi / 4, 0, 10, .4, .1, 5), "reverse increment and zero min");
    check(valid(0, 0, .01, 0, 10, 0, 0, 1), "single ray and unspecified times");
    check(valid(0, pi / 2, pi / 4, .1, 10, 0, 0, 2), "one interval endpoint convention");
    check(valid(0, 6.283185307, .001534355386, 0, 100, 0, 0, 4096), "maximum rays");
    check(!valid(0, 0, .1, 0, 10, 0, 0, 0), "empty rejected");
    check(!valid(0, 0, .1, 0, 10, 0, 0, 4097), "oversize rejected");
    check(!valid(0, 0, 0, 0, 10, 0, 0, 1), "zero increment rejected");
    check(!valid(0, 0, .1, -.1, 10, 0, 0, 1), "negative min rejected");
    check(!valid(0, 0, .1, 1, 1, 0, 0, 1), "empty range interval rejected");
    check(!valid(0, 0, .1, 2, 1, 0, 0, 1), "inverted range rejected");
    check(!valid(0, 0, .1, 0, 10, -.1, 0, 1), "negative scan time rejected");
    check(!valid(0, 0, .1, 0, 10, 0, -.1, 1), "negative increment time rejected");
    check(!valid(0, pi, pi / 4, 0, 10, 0, 0, 2), "inconsistent end angle rejected");
    check(!valid(0, 4 * pi, pi / 2, 0, 10, 0, 0, 9), "multiple revolutions rejected");
    check(!valid(0, .4, .1, 0, 10, .1, .1, 5), "acquisition duration rejected");
    check(valid(0, .4, .1, 0, 10, .399, .1, 5), "time tolerance equality");
    check(!valid(nan, 0, .1, 0, 10, 0, 0, 1) && !valid(0, inf, .1, 0, 10, 0, 0, 1) && !valid(0, 0, inf, 0, 10, 0, 0, 1), "nonfinite angle rejected");
    check(!valid(0, 0, .1, nan, 10, 0, 0, 1) && !valid(0, 0, .1, 0, inf, 0, 0, 1) && !valid(0, 0, .1, 0, 10, nan, 0, 1) && !valid(0, 0, .1, 0, 10, 0, inf, 1), "nonfinite metadata rejected");

    using namespace amr;
    LidarSample sample{};
    sample.geometry = {0, .6, .1, 1, 10, 0, 0};
    sample.ranges_m = {1, nan, inf, -inf, 0, 11, 10};
    sample.frame_id = "laser_front";
    LidarSampleGate gate{};
    LidarReading out{};
    out.frame_id = "sentinel";
    assert(gate.observe(sample, out) == LidarSampleResult::InvalidStamp && out.frame_id == "sentinel");
    sample.source_stamp_ns = 100;
    assert(gate.observe(sample, out) == LidarSampleResult::Accepted && out.source_sequence == 0);
    assert(out.ranges_m.size() == 7 && out.ranges_m[0] == 1 && out.ranges_m[6] == 10);
    for (unsigned i = 1; i < 6; ++i)
        assert(!out.ranges_m[i]);
    sample.source_stamp_ns = 101;
    sample.ranges_m[0] = 3;
    assert(gate.observe(sample, out) == LidarSampleResult::Duplicate && out.ranges_m[0] == 1);
    sample.source_sequence = 10;
    sample.source_stamp_ns = 102;
    assert(gate.observe(sample, out) == LidarSampleResult::Accepted && out.ranges_m[0] == 3);
    sample.source_sequence = 0;
    sample.source_stamp_ns = 103;
    assert(gate.observe(sample, out) == LidarSampleResult::ResetPending && out.source_sequence == 10);
    sample.source_stamp_ns = 104;
    assert(gate.observe(sample, out) == LidarSampleResult::Duplicate);
    sample.source_sequence = 1;
    sample.source_stamp_ns = 105;
    assert(gate.observe(sample, out) == LidarSampleResult::AcceptedAfterReset);
    sample.source_sequence = 2;
    sample.source_stamp_ns = 90;
    assert(gate.observe(sample, out) == LidarSampleResult::AcceptedWithClockAdjustment);
    sample.source_sequence = 3;
    sample.source_stamp_ns = 106;
    sample.frame_id = "bad frame";
    assert(gate.observe(sample, out) == LidarSampleResult::InvalidFrame && out.source_sequence == 2);
    sample.frame_id = "laser_front";
    sample.geometry.angle_increment_rad = 0;
    assert(gate.observe(sample, out) == LidarSampleResult::InvalidGeometry);
    sample.geometry = {0, .6, .1, 0, 10, 0, 0};
    sample.ranges_m.assign(7, nan);
    assert(gate.observe(sample, out) == LidarSampleResult::Accepted);
    for (const auto &r : out.ranges_m)
        assert(!r);
    sample.source_sequence = 4;
    sample.source_stamp_ns = 107;
    sample.ranges_m[0] = 0;
    assert(lidar_sample_accepted(gate.observe(sample, out)) && out.ranges_m[0] == 0);
    {
        LidarSampleGate wrap;
        sample.source_sequence = std::numeric_limits<std::uint32_t>::max();
        sample.source_stamp_ns = 1;
        assert(lidar_sample_accepted(wrap.observe(sample, out)));
        sample.source_sequence = 0;
        sample.source_stamp_ns = 2;
        assert(wrap.observe(sample, out) == LidarSampleResult::ResetPending);
        sample.source_sequence = 1;
        sample.source_stamp_ns = 3;
        assert(wrap.observe(sample, out) == LidarSampleResult::AcceptedAfterReset);
    }
    const auto origin = std::chrono::steady_clock::time_point{};
    LidarState state{};
    assert(refresh_lidar_quality(state, origin, std::chrono::milliseconds{1500}) == LidarRefreshResult::NoData);
    state.reading = out;
    state.received_at = origin;
    assert(refresh_lidar_quality(state, origin + std::chrono::milliseconds{1499}, std::chrono::milliseconds{1500}) == LidarRefreshResult::Fresh);
    assert(refresh_lidar_quality(state, origin + std::chrono::milliseconds{1500}, std::chrono::milliseconds{1500}) == LidarRefreshResult::Stale);
    assert(state.reading && state.received_at == origin);
    assert(refresh_lidar_quality(state, origin, std::chrono::milliseconds{0}) == LidarRefreshResult::InvalidTimeout);
    assert(refresh_lidar_quality(state, origin - std::chrono::milliseconds{1}, std::chrono::milliseconds{1500}) == LidarRefreshResult::InvalidTimeOrder);
    state.quality = DataQuality::Fault;
    assert(refresh_lidar_quality(state, origin, std::chrono::milliseconds{1500}) == LidarRefreshResult::Preserved);
    state.quality = DataQuality::Unsupported;
    assert(refresh_lidar_quality(state, origin, std::chrono::milliseconds{1500}) == LidarRefreshResult::Preserved);
    std::cout << "19 geometry groups, source filtering, sanitization and freshness checks passed\n";
}
