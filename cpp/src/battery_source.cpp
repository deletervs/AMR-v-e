#include "amr/battery_source.hpp"
#include <cmath>

namespace amr
{
    BatterySampleResult BatterySampleGate::observe(const BatterySample &sample)
    {
        // Validate inputs
        if (!std::isfinite(sample.voltage_v) || sample.voltage_v < 0)
        {
            return BatterySampleResult::InvalidVoltage;
        }
        if (!std::isfinite(sample.source_time_raw) || sample.source_time_raw <= 0)
        {
            return BatterySampleResult::InvalidSourceTime;
        }
        if (sample.source_stamp_ns <= 0)
        {
            return BatterySampleResult::InvalidStamp;
        }

        // Check for duplicate stamp
        if (sample.source_stamp_ns == last_seen_stamp_)
        {
            return BatterySampleResult::Duplicate;
        }

        // Remember the stamp
        const bool stamp_moved_backwards = sample.source_stamp_ns < last_seen_stamp_;
        last_seen_stamp_ = sample.source_stamp_ns;

        // First valid attempt
        if (!initialized_)
        {
            initialized_ = true;
            accepted_time_ = sample.source_time_raw;
            return BatterySampleResult::Accepted;
        }

        // Source time equals accepted time
        if (sample.source_time_raw == accepted_time_)
        {
            pending_reset_ = false;
            return BatterySampleResult::Duplicate;
        }

        // Source time greater than accepted time
        if (sample.source_time_raw > accepted_time_)
        {
            accepted_time_ = sample.source_time_raw;
            pending_reset_ = false;
            // Check if stamp moved backwards (relative to prior seen stamp)
            return stamp_moved_backwards ? BatterySampleResult::AcceptedWithClockAdjustment : BatterySampleResult::Accepted;
        }

        // Source time less than accepted time
        if (sample.source_time_raw < accepted_time_)
        {
            // If pending reset and equals candidate time
            if (pending_reset_ && sample.source_time_raw == candidate_time_)
            {
                return BatterySampleResult::Duplicate;
            }

            // If pending and source time > candidate_time_
            if (pending_reset_ && sample.source_time_raw > candidate_time_)
            {
                accepted_time_ = sample.source_time_raw;
                pending_reset_ = false;
                return BatterySampleResult::AcceptedAfterReset;
            }

            // Set new candidate time and pending reset
            candidate_time_ = sample.source_time_raw;
            pending_reset_ = true;
            return BatterySampleResult::ResetPending;
        }

        return BatterySampleResult::InvalidVoltage; // This should never be reached
    }
} // namespace amr
