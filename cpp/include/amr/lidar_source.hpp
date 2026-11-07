#pragma once
#include "amr/data_freshness.hpp"
#include "amr/robot_state.hpp"
#include <cstdint>
#include <optional>
#include <string>
#include <vector>
namespace amr
{
    struct LidarGeometry
    {
        double angle_min_rad{0}, angle_max_rad{0}, angle_increment_rad{0};
        double range_min_m{0}, range_max_m{0}, scan_time_s{0}, time_increment_s{0};
    };
    bool lidar_geometry_valid(double angle_min, double angle_max, double increment, double range_min, double range_max, double scan_time, double time_increment, unsigned count);
    struct LidarSample
    {
        LidarGeometry geometry{};
        std::vector<double> ranges_m{};
        std::uint64_t source_stamp_ns{0};
        std::uint32_t source_sequence{0};
        std::string frame_id{};
    };
    struct LidarReading
    {
        LidarGeometry geometry{};
        std::vector<std::optional<double>> ranges_m{};
        std::uint64_t source_stamp_ns{0};
        std::uint32_t source_sequence{0};
        std::string frame_id{};
    };
    struct LidarState
    {
        std::optional<LidarReading> reading{};
        std::optional<std::chrono::steady_clock::time_point> received_at{};
        DataQuality quality{DataQuality::Unknown};
    };
    enum class LidarSampleResult
    {
        Accepted,
        AcceptedAfterReset,
        AcceptedWithClockAdjustment,
        Duplicate,
        ResetPending,
        InvalidGeometry,
        InvalidStamp,
        InvalidFrame
    };
    class LidarSampleGate
    {
      public:
        // Invalid/duplicate/pending input leaves the previous output unchanged.
        LidarSampleResult observe(const LidarSample &sample, LidarReading &out);

      private:
        bool initialized_{false}, pending_reset_{false};
        std::uint32_t accepted_sequence_{0}, candidate_sequence_{0};
        std::uint64_t last_seen_stamp_{0};
    };
    constexpr bool lidar_sample_accepted(LidarSampleResult r) { return r == LidarSampleResult::Accepted || r == LidarSampleResult::AcceptedAfterReset || r == LidarSampleResult::AcceptedWithClockAdjustment; }
    enum class LidarRefreshResult
    {
        NoData,
        Preserved,
        Fresh,
        Stale,
        InvalidTimeout,
        InvalidTimeOrder
    };
    LidarRefreshResult refresh_lidar_quality(LidarState &state, std::chrono::steady_clock::time_point now, std::chrono::milliseconds timeout);
} // namespace amr