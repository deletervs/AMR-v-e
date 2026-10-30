#pragma once
#include <cstdint>

// ROS适配前的纯数据筛选；不负责ROS订阅或更改BatteryState。
namespace amr
{
    enum class BatterySampleResult
    {
        Accepted,
        AcceptedAfterReset,
        AcceptedWithClockAdjustment,
        Duplicate,
        ResetPending,
        InvalidVoltage,
        InvalidSourceTime,
        InvalidStamp
    };
    struct BatterySample
    {
        double voltage_v{0};
        double source_time_raw{0};
        std::uint64_t source_stamp_ns{0};
    };
    class BatterySampleGate
    {
      public:
        BatterySampleResult observe(const BatterySample &sample);

      private:
        bool initialized_{false};
        bool pending_reset_{false};
        double accepted_time_{0};
        double candidate_time_{0};
        std::uint64_t last_seen_stamp_{0};
    };

    constexpr bool battery_sample_accepted(BatterySampleResult result)
    {
        return result == BatterySampleResult::Accepted ||
               result == BatterySampleResult::AcceptedAfterReset ||
               result == BatterySampleResult::AcceptedWithClockAdjustment;
    }

    // 两个时刻必须来自同一个ROS时钟；用于丢弃本机积压包，不用于跨PC时钟比较。
    constexpr bool battery_stamp_recent(std::uint64_t stamp, std::uint64_t now,
                                        std::uint64_t max_age_ns)
    {
        return stamp > 0 && max_age_ns > 0 && stamp <= now && now - stamp < max_age_ns;
    }
} // namespace amr
