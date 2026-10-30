// Offline motion policy. No ROS, sockets, actuator calls or real motor feedback.
#pragma once
#include <cstdint>

namespace amr
{
    enum class ControlSource
    {
        None,
        Keyboard,
        Gamepad
    };
    enum class MotionResult
    {
        Accepted,
        Armed,
        Released,
        Stopped,
        Cleared,
        LinkUpdated,
        Busy,
        StopLatched,
        LinkUnavailable,
        InvalidConfig,
        InvalidSource,
        InvalidSession,
        InvalidSequence,
        InvalidWindow,
        InvalidVelocity,
        NeutralRequired,
        ClockRegression,
        NotArmed
    };
    enum class StopReason
    {
        Disarmed,
        WaitingForNeutral,
        DeadmanReleased,
        None,
        CommandExpired,
        LeaseExpired,
        SoftwareStop,
        LinkUnavailable,
        InvalidCommand,
        ClockRegression,
        InvalidConfig
    };
    struct VelocityTarget
    {
        double linear_mps{0.0};
        double angular_radps{0.0};
    };
    struct MotionLimits
    {
        double linear_mps{0.25};
        double angular_radps{0.6};
        std::uint64_t command_timeout_ms{250};
        std::uint64_t lease_ms{10000};
    };
    struct MotionCommand
    {
        std::uint64_t boot_id{0}, session_id{0}, controller_id{0}, sequence{0};
        ControlSource source{ControlSource::None};
        // Both fields refer to this guard's monotonic clock, never a browser wall clock.
        std::uint64_t issued_ms{0}, valid_until_ms{0};
        bool deadman{false};
        bool input_neutral{false}; // Adapter must inspect raw keys/axes before deadman zeroing.
        VelocityTarget target{};
    };
    struct MotionGrant
    {
        MotionResult result{MotionResult::NotArmed};
        std::uint64_t session_id{0};
    };
    struct MotionSnapshot
    {
        std::uint64_t boot_id{0}, session_id{0}, controller_id{0}, last_sequence{0};
        ControlSource owner{ControlSource::None};
        bool armed{false}, stop_latched{false}, neutral_confirmed{false}, link_available{true};
        VelocityTarget target{};
        StopReason reason{StopReason::Disarmed};
        std::uint64_t lease_remaining_ms{0}, command_remaining_ms{0};
    };
    class MotionGuard
    {
      public:
        explicit MotionGuard(std::uint64_t boot_id, MotionLimits limits = {});
        MotionGrant arm(std::uint64_t controller, ControlSource source, std::uint64_t now_ms);
        MotionResult submit(const MotionCommand &command, std::uint64_t now_ms);
        MotionResult release(std::uint64_t controller, std::uint64_t boot,
                             std::uint64_t session, std::uint64_t now_ms);
        void stop(std::uint64_t now_ms);
        MotionResult clear_stop(std::uint64_t now_ms);
        void set_link(bool available, std::uint64_t now_ms);
        MotionSnapshot snapshot(std::uint64_t now_ms);
        const MotionLimits &limits() const { return limits_; }

      private:
        bool advance(std::uint64_t now_ms);
        void revoke(StopReason reason);
        std::uint64_t boot_id_, counter_{0}, session_id_{0}, controller_id_{0};
        std::uint64_t arm_ms_{0}, last_now_{0}, deadline_ms_{0}, last_issued_ms_{0}, last_sequence_{0};
        MotionLimits limits_;
        ControlSource owner_{ControlSource::None};
        bool valid_config_{false}, have_clock_{false}, stop_latched_{false};
        bool link_available_{true}, neutral_confirmed_{false}, have_command_{false};
        VelocityTarget target_{};
        StopReason reason_{StopReason::Disarmed};
    };
} // namespace amr
