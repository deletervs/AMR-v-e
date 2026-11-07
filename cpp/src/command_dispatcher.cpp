#include "amr/command_dispatcher.hpp"
namespace amr
{
    CommandReply CommandDispatcher::dispatch(const ControlRequest &request, std::uint64_t now)
    {
        return std::visit([&](const auto &value) { return handle(value, now); }, request);
    }
    CommandReply CommandDispatcher::handle(const ArmControl &r, std::uint64_t now)
    {
        const auto grant = guard_.arm(r.controller_id, r.source, now);
        return {grant.result, grant.session_id};
    }
    CommandReply CommandDispatcher::handle(const MotionCommand &r, std::uint64_t now)
    {
        return {guard_.submit(r, now), 0};
    }
    CommandReply CommandDispatcher::handle(const ReleaseControl &r, std::uint64_t now)
    {
        return {guard_.release(r.controller_id, r.boot_id, r.session_id, now), 0};
    }
    CommandReply CommandDispatcher::handle(const StopControl &, std::uint64_t now)
    {
        guard_.stop(now);
        return {MotionResult::Stopped, 0};
    }
    CommandReply CommandDispatcher::handle(const ClearControl &, std::uint64_t now)
    {
        return {guard_.clear_stop(now), 0};
    }
} // namespace amr
