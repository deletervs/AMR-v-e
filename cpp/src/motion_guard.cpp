#include "amr/motion_guard.hpp"
#include <cmath>
#include <limits>

namespace amr
{
    MotionGuard::MotionGuard(std::uint64_t boot, MotionLimits limits) : boot_id_{boot}, limits_{limits}
    {
        valid_config_ = boot != 0 && std::isfinite(limits.linear_mps) && limits.linear_mps > 0 &&
                        std::isfinite(limits.angular_radps) && limits.angular_radps > 0 &&
                        limits.command_timeout_ms > 0 && limits.lease_ms >= limits.command_timeout_ms;
        if (!valid_config_)
            reason_ = StopReason::InvalidConfig;
    }

    void MotionGuard::revoke(StopReason reason)
    {
        session_id_ = controller_id_ = last_sequence_ = 0;
        owner_ = ControlSource::None;
        neutral_confirmed_ = have_command_ = false;
        target_ = {};
        reason_ = reason;
    }

    bool MotionGuard::advance(std::uint64_t now)
    {
        if (have_clock_ && now < last_now_)
        {
            revoke(StopReason::ClockRegression);
            return false;
        }
        have_clock_ = true;
        last_now_ = now;
        if (session_id_)
        {
            if (now - arm_ms_ >= limits_.lease_ms)
                revoke(StopReason::LeaseExpired);
            else if (have_command_ ? now >= deadline_ms_ : now - arm_ms_ >= limits_.command_timeout_ms)
                revoke(StopReason::CommandExpired);
        }
        return true;
    }

    MotionGrant MotionGuard::arm(std::uint64_t controller, ControlSource source, std::uint64_t now)
    {
        if (!advance(now))
            return {MotionResult::ClockRegression, 0};
        if (!valid_config_)
            return {MotionResult::InvalidConfig, 0};
        if (stop_latched_)
            return {MotionResult::StopLatched, 0};
        if (!link_available_)
            return {MotionResult::LinkUnavailable, 0};
        if (source != ControlSource::Keyboard && source != ControlSource::Gamepad)
            return {MotionResult::InvalidSource, 0};
        if (controller == 0)
            return {MotionResult::InvalidSession, 0};
        if (session_id_)
            return {MotionResult::Busy, 0};
        if (counter_ == std::numeric_limits<std::uint64_t>::max())
            return {MotionResult::InvalidSession, 0};
        session_id_ = ++counter_;
        controller_id_ = controller;
        owner_ = source;
        arm_ms_ = now;
        last_issued_ms_ = now;
        last_sequence_ = 0;
        have_command_ = neutral_confirmed_ = false;
        target_ = {};
        reason_ = StopReason::WaitingForNeutral;
        return {MotionResult::Armed, session_id_};
    }

    MotionResult MotionGuard::submit(const MotionCommand &c, std::uint64_t now)
    {
        if (!advance(now))
            return MotionResult::ClockRegression;
        if (!valid_config_)
            return MotionResult::InvalidConfig;
        if (stop_latched_)
            return MotionResult::StopLatched;
        if (!link_available_)
            return MotionResult::LinkUnavailable;
        if (!session_id_)
            return MotionResult::NotArmed;
        if (c.boot_id != boot_id_ || c.session_id != session_id_ || c.controller_id != controller_id_)
            return MotionResult::InvalidSession;
        if (c.source != owner_)
            return MotionResult::InvalidSource;
        if (c.sequence == 0 || c.sequence <= last_sequence_)
            return MotionResult::InvalidSequence;
        if (c.issued_ms < arm_ms_ || c.issued_ms < last_issued_ms_ || c.issued_ms > now ||
            c.valid_until_ms <= now || c.valid_until_ms - c.issued_ms > limits_.command_timeout_ms)
            return MotionResult::InvalidWindow;
        if (!std::isfinite(c.target.linear_mps) || !std::isfinite(c.target.angular_radps) ||
            std::abs(c.target.linear_mps) > limits_.linear_mps ||
            std::abs(c.target.angular_radps) > limits_.angular_radps)
        {
            revoke(StopReason::InvalidCommand);
            return MotionResult::InvalidVelocity;
        }
        if (!neutral_confirmed_)
        {
            if (!c.input_neutral || c.deadman || c.target.linear_mps != 0 || c.target.angular_radps != 0)
                return MotionResult::NeutralRequired;
            neutral_confirmed_ = true;
        }
        last_sequence_ = c.sequence;
        last_issued_ms_ = c.issued_ms;
        deadline_ms_ = c.valid_until_ms;
        have_command_ = true;
        target_ = c.deadman ? c.target : VelocityTarget{};
        reason_ = c.deadman ? StopReason::None : StopReason::DeadmanReleased;
        return MotionResult::Accepted;
    }

    MotionResult MotionGuard::release(std::uint64_t controller, std::uint64_t boot,
                                      std::uint64_t session, std::uint64_t now)
    {
        if (!advance(now))
            return MotionResult::ClockRegression;
        if (!session_id_)
            return MotionResult::NotArmed;
        if (controller != controller_id_ || boot != boot_id_ || session != session_id_)
            return MotionResult::InvalidSession;
        revoke(StopReason::Disarmed);
        return MotionResult::Released;
    }

    void MotionGuard::stop(std::uint64_t now)
    {
        advance(now);
        stop_latched_ = true;
        revoke(StopReason::SoftwareStop);
    }

    MotionResult MotionGuard::clear_stop(std::uint64_t now)
    {
        if (!advance(now))
            return MotionResult::ClockRegression;
        stop_latched_ = false;
        revoke(link_available_ ? StopReason::Disarmed : StopReason::LinkUnavailable);
        return MotionResult::Cleared;
    }

    void MotionGuard::set_link(bool available, std::uint64_t now)
    {
        const bool time_ok = advance(now);
        if (!available)
        {
            link_available_ = false;
            revoke(StopReason::LinkUnavailable);
        }
        else if (time_ok)
        {
            link_available_ = true;
            if (!session_id_)
                reason_ = stop_latched_ ? StopReason::SoftwareStop : StopReason::Disarmed;
        }
    }

    MotionSnapshot MotionGuard::snapshot(std::uint64_t now)
    {
        advance(now);
        MotionSnapshot s{};
        s.boot_id = boot_id_;
        s.session_id = session_id_;
        s.controller_id = controller_id_;
        s.last_sequence = last_sequence_;
        s.owner = owner_;
        s.armed = session_id_ != 0;
        s.stop_latched = stop_latched_;
        s.neutral_confirmed = neutral_confirmed_;
        s.link_available = link_available_;
        s.target = target_;
        s.reason = reason_;
        if (session_id_)
        {
            s.lease_remaining_ms = limits_.lease_ms - (now - arm_ms_);
            s.command_remaining_ms = have_command_ ? deadline_ms_ - now : limits_.command_timeout_ms - (now - arm_ms_);
        }
        return s;
    }
} // namespace amr
