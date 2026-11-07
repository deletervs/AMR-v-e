#include "amr/odometry_source.hpp"
#include <cmath>

namespace amr
{
    bool planar_yaw(double qx, double qy, double qz, double qw, double &yaw)
    {
        // Check for finite components
        if (!std::isfinite(qx) || !std::isfinite(qy) || !std::isfinite(qz) || !std::isfinite(qw))
        {
            return false;
        }

        // Compute norm using nested std::hypot
        double norm = std::hypot(qx, std::hypot(qy, std::hypot(qz, qw)));

        // Check if norm is valid (not zero and finite)
        if (norm <= 0 || !std::isfinite(norm))
        {
            return false;
        }

        // Normalize components
        double nx = qx / norm;
        double ny = qy / norm;
        double nz = qz / norm;
        double nw = qw / norm;

        // Check if norm is close to 1
        if (std::abs(norm - 1.0) > 0.001)
        {
            return false;
        }

        // Check if qx and qy are close to zero for planar input
        if (std::abs(nx) > 1e-6 || std::abs(ny) > 1e-6)
        {
            return false;
        }

        // Compute yaw using the formula
        double numerator = 2 * (nw * nz + nx * ny);
        double denominator = 1 - 2 * (ny * ny + nz * nz);
        double computed_yaw = std::atan2(numerator, denominator);

        const double pi = std::acos(-1.0);
        // Normalize yaw to [-pi, pi).
        while (computed_yaw >= pi)
        {
            computed_yaw -= 2 * pi;
        }
        while (computed_yaw < -pi)
        {
            computed_yaw += 2 * pi;
        }

        // Check if result is finite
        if (!std::isfinite(computed_yaw))
        {
            return false;
        }

        // Set the output yaw
        yaw = computed_yaw;
        return true;
    }
} // namespace amr

namespace amr
{
    OdometrySampleResult OdometrySampleGate::observe(const OdometrySample &s, OdometryReading &out)
    {
        if (s.frame_id != "odom" || s.child_frame_id != "base_link")
            return OdometrySampleResult::InvalidFrames;
        if (!s.source_stamp_ns)
            return OdometrySampleResult::InvalidStamp;
        if (!std::isfinite(s.x_m) || !std::isfinite(s.y_m) ||
            !std::isfinite(s.linear_x_mps) || !std::isfinite(s.linear_y_mps) || !std::isfinite(s.angular_z_radps))
            return OdometrySampleResult::InvalidReading;
        double yaw{0};
        if (!planar_yaw(s.qx, s.qy, s.qz, s.qw, yaw))
            return OdometrySampleResult::InvalidOrientation;
        if (s.source_stamp_ns == last_seen_stamp_)
            return OdometrySampleResult::Duplicate;
        const bool clock_backwards = s.source_stamp_ns < last_seen_stamp_;
        last_seen_stamp_ = s.source_stamp_ns;
        OdometrySampleResult result{OdometrySampleResult::Accepted};
        if (!initialized_)
        {
            initialized_ = true;
            accepted_sequence_ = s.source_sequence;
        }
        else if (s.source_sequence == accepted_sequence_)
        {
            pending_reset_ = false;
            return OdometrySampleResult::Duplicate;
        }
        else if (s.source_sequence > accepted_sequence_)
        {
            accepted_sequence_ = s.source_sequence;
            pending_reset_ = false;
            if (clock_backwards)
                result = OdometrySampleResult::AcceptedWithClockAdjustment;
        }
        else
        {
            if (pending_reset_ && s.source_sequence == candidate_sequence_)
                return OdometrySampleResult::Duplicate;
            if (pending_reset_ && s.source_sequence > candidate_sequence_)
            {
                accepted_sequence_ = s.source_sequence;
                pending_reset_ = false;
                result = OdometrySampleResult::AcceptedAfterReset;
            }
            else
            {
                candidate_sequence_ = s.source_sequence;
                pending_reset_ = true;
                return OdometrySampleResult::ResetPending;
            }
        }
        out = {s.x_m, s.y_m, yaw, s.linear_x_mps, s.linear_y_mps, s.angular_z_radps};
        return result;
    }
} // namespace amr
