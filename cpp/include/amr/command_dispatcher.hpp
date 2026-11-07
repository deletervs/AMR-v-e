// Trusted in-process contract; transport authentication belongs to its adapter.
#pragma once
#include "amr/motion_guard.hpp"
#include <variant>
namespace amr
{
    struct ArmControl
    {
        std::uint64_t controller_id{0};
        ControlSource source{ControlSource::None};
    };
    struct ReleaseControl
    {
        std::uint64_t controller_id{0}, boot_id{0}, session_id{0};
    };
    struct StopControl {};
    struct ClearControl {};
    using ControlRequest = std::variant<ArmControl, MotionCommand, ReleaseControl, StopControl, ClearControl>;
    struct CommandReply
    {
        MotionResult result{MotionResult::NotArmed};
        std::uint64_t session_id{0}; // Nonzero only on a successful new grant.
    };
    // Called by one serialized reactor. References its existing guard, no new session state.
    class CommandDispatcher
    {
      public:
        explicit CommandDispatcher(MotionGuard &guard) : guard_{guard} {}
        CommandReply dispatch(const ControlRequest &request, std::uint64_t now_ms);
      private:
        CommandReply handle(const ArmControl &request, std::uint64_t now);
        CommandReply handle(const MotionCommand &request, std::uint64_t now);
        CommandReply handle(const ReleaseControl &request, std::uint64_t now);
        CommandReply handle(const StopControl &, std::uint64_t now);
        CommandReply handle(const ClearControl &, std::uint64_t now);
        MotionGuard &guard_;
    };
} // namespace amr
