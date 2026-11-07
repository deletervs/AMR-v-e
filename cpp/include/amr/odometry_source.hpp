#pragma once
#include "amr/robot_state.hpp"
#include <cstdint>
#include <string>

namespace amr
{
    // Raw planar pose and body-frame twist from one matching stamped ROS pair.
    struct OdometrySample
    {
        double x_m{0}, y_m{0};
        double qx{0}, qy{0}, qz{0}, qw{1};
        double linear_x_mps{0}, linear_y_mps{0}, angular_z_radps{0};
        std::uint64_t source_stamp_ns{0};
        std::uint32_t source_sequence{0}, source_twist_sequence{0};
        std::string frame_id{"odom"}, child_frame_id{"base_link"};
    };
    enum class OdometrySampleResult
    {
        Accepted,
        AcceptedAfterReset,
        AcceptedWithClockAdjustment,
        Duplicate,
        ResetPending,
        InvalidReading,
        InvalidOrientation,
        InvalidStamp,
        InvalidFrames
    };
    // Accept only near-unit planar quaternions; failure leaves yaw unchanged.
    bool planar_yaw(double qx, double qy, double qz, double qw, double &yaw);
    class OdometrySampleGate
    {
      public:
        // Failure/duplicate/reset-pending leaves the caller's reading unchanged.
        OdometrySampleResult observe(const OdometrySample &sample, OdometryReading &reading);

      private:
        bool initialized_{false}, pending_reset_{false};
        std::uint32_t accepted_sequence_{0}, candidate_sequence_{0};
        std::uint64_t last_seen_stamp_{0};
    };
    constexpr bool odometry_sample_accepted(OdometrySampleResult r)
    {
        return r == OdometrySampleResult::Accepted || r == OdometrySampleResult::AcceptedAfterReset ||
               r == OdometrySampleResult::AcceptedWithClockAdjustment;
    }
} // namespace amr
