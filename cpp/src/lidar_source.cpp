#include "amr/lidar_source.hpp"
#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <utility>

namespace amr
{
    bool lidar_geometry_valid(double angle_min, double angle_max, double increment, double range_min, double range_max, double scan_time, double time_increment, unsigned count)
    {
        // 检查count是否在有效范围内
        if (count == 0 || count > 4096)
        {
            return false;
        }

        // 检查所有参数是否有限
        if (!std::isfinite(angle_min) || !std::isfinite(angle_max) ||
            !std::isfinite(increment) || !std::isfinite(range_min) ||
            !std::isfinite(range_max) || !std::isfinite(scan_time) ||
            !std::isfinite(time_increment))
        {
            return false;
        }

        // 检查increment不为0
        if (increment == 0.0)
        {
            return false;
        }

        // 检查range_min和range_max
        if (range_min < 0.0 || range_max <= range_min)
        {
            return false;
        }

        // 检查scan_time和time_increment非负
        if (scan_time < 0.0 || time_increment < 0.0)
        {
            return false;
        }

        // 计算span和expected
        double span = (count - 1) * increment;
        double expected = angle_min + span;

        // 检查span和expected是否有限
        if (!std::isfinite(span) || !std::isfinite(expected))
        {
            return false;
        }

        // 检查span的绝对值限制
        double pi = std::acos(-1.0);
        if (std::abs(span) > 2.0 * pi + 0.001)
        {
            return false;
        }

        // 检查expected与angle_max的一致性
        if (std::abs(expected - angle_max) > std::max(0.0001, std::abs(increment) * 1.05))
        {
            return false;
        }

        // 检查时间约束
        if (scan_time > 0.0 && time_increment > 0.0)
        {
            if (time_increment * (count - 1) > scan_time + 0.001)
            {
                return false;
            }
        }

        return true;
    }
} // namespace amr
namespace amr
{
    LidarSampleResult LidarSampleGate::observe(const LidarSample &s, LidarReading &out)
    {
        if (s.frame_id.empty() || s.frame_id.size() > 64 || std::any_of(s.frame_id.begin(), s.frame_id.end(), [](unsigned char c)
                                                                        { return !((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '/' || c == '-' || c == '.'); }))
            return LidarSampleResult::InvalidFrame;
        if (!s.source_stamp_ns)
            return LidarSampleResult::InvalidStamp;
        const auto &g = s.geometry;
        if (s.ranges_m.empty() || s.ranges_m.size() > 4096 || !lidar_geometry_valid(g.angle_min_rad, g.angle_max_rad, g.angle_increment_rad, g.range_min_m, g.range_max_m, g.scan_time_s, g.time_increment_s, static_cast<unsigned>(s.ranges_m.size())))
            return LidarSampleResult::InvalidGeometry;
        if (s.source_stamp_ns == last_seen_stamp_)
            return LidarSampleResult::Duplicate;
        const bool clock_backwards = s.source_stamp_ns < last_seen_stamp_;
        last_seen_stamp_ = s.source_stamp_ns;
        LidarSampleResult result{LidarSampleResult::Accepted};
        if (!initialized_)
        {
            initialized_ = true;
            accepted_sequence_ = s.source_sequence;
        }
        else if (s.source_sequence == accepted_sequence_)
        {
            pending_reset_ = false;
            return LidarSampleResult::Duplicate;
        }
        else if (s.source_sequence > accepted_sequence_)
        {
            accepted_sequence_ = s.source_sequence;
            pending_reset_ = false;
            if (clock_backwards)
                result = LidarSampleResult::AcceptedWithClockAdjustment;
        }
        else
        {
            if (pending_reset_ && s.source_sequence == candidate_sequence_)
                return LidarSampleResult::Duplicate;
            if (pending_reset_ && s.source_sequence > candidate_sequence_)
            {
                accepted_sequence_ = s.source_sequence;
                pending_reset_ = false;
                result = LidarSampleResult::AcceptedAfterReset;
            }
            else
            {
                candidate_sequence_ = s.source_sequence;
                pending_reset_ = true;
                return LidarSampleResult::ResetPending;
            }
        }
        LidarReading accepted{};
        accepted.geometry = g;
        accepted.source_stamp_ns = s.source_stamp_ns;
        accepted.source_sequence = s.source_sequence;
        accepted.frame_id = s.frame_id;
        accepted.ranges_m.reserve(s.ranges_m.size());
        for (double range : s.ranges_m)
        {
            if (std::isfinite(range) && range >= g.range_min_m && range <= g.range_max_m)
                accepted.ranges_m.emplace_back(range);
            else
                accepted.ranges_m.emplace_back(std::nullopt);
        }
        out = std::move(accepted);
        return result;
    }

    LidarRefreshResult refresh_lidar_quality(LidarState &state, std::chrono::steady_clock::time_point now, std::chrono::milliseconds timeout)
    {
        if (state.quality == DataQuality::Fault || state.quality == DataQuality::Unsupported)
            return LidarRefreshResult::Preserved;
        if (!state.reading || !state.received_at)
        {
            state.quality = DataQuality::Unknown;
            return LidarRefreshResult::NoData;
        }
        switch (check_freshness(*state.received_at, now, timeout))
        {
        case FreshnessResult::Fresh:
            state.quality = DataQuality::Valid;
            return LidarRefreshResult::Fresh;
        case FreshnessResult::Stale:
            state.quality = DataQuality::Stale;
            return LidarRefreshResult::Stale;
        case FreshnessResult::InvalidTimeout:
            state.quality = DataQuality::Unknown;
            return LidarRefreshResult::InvalidTimeout;
        case FreshnessResult::InvalidTimeOrder:
            state.quality = DataQuality::Unknown;
            return LidarRefreshResult::InvalidTimeOrder;
        }
        throw std::logic_error("Unhandled freshness result");
    }
} // namespace amr
