#include "amr/motion_guard.hpp"
#include <cassert>
#include <cmath>
#include <iostream>
#include <limits>
using namespace amr;
namespace
{
    MotionCommand command(std::uint64_t session, std::uint64_t seq, std::uint64_t issued,
                          bool deadman = false, double linear = 0, double angular = 0)
    {
        return {11, session, 42, seq, ControlSource::Keyboard, issued, issued + 250, deadman, !deadman && linear == 0 && angular == 0, {linear, angular}};
    }
    std::uint64_t armed_neutral(MotionGuard &g, std::uint64_t now = 0)
    {
        auto grant = g.arm(42, ControlSource::Keyboard, now);
        assert(grant.result == MotionResult::Armed);
        assert(g.submit(command(grant.session_id, 1, now), now) == MotionResult::Accepted);
        return grant.session_id;
    }
    void zero(const MotionSnapshot &s) { assert(s.target.linear_mps == 0 && s.target.angular_radps == 0); }
} // namespace
int main()
{
    {
        MotionGuard g{11};
        auto s = g.snapshot(0);
        zero(s);
        assert(!s.armed && !s.stop_latched);
        assert(g.submit(command(1, 1, 0, true, .1), 0) == MotionResult::NotArmed);
    }
    {
        MotionGuard g{11};
        auto a = g.arm(42, ControlSource::Keyboard, 0);
        assert(g.arm(43, ControlSource::Gamepad, 1).result == MotionResult::Busy);
        assert(g.submit(command(a.session_id, 1, 2, true, .1), 2) == MotionResult::NeutralRequired);
        zero(g.snapshot(2));
        assert(!g.snapshot(2).neutral_confirmed);
        assert(g.submit(command(a.session_id, 1, 3), 3) == MotionResult::Accepted);
        assert(g.submit(command(a.session_id, 2, 4, true, .25, -.6), 4) == MotionResult::Accepted);
        assert(g.snapshot(4).target.linear_mps == .25);
        assert(g.submit(command(a.session_id, 3, 5, false, .25, .6), 5) == MotionResult::Accepted);
        zero(g.snapshot(5));
        assert(g.snapshot(5).reason == StopReason::DeadmanReleased);
    }
    {
        MotionGuard g{11};
        auto a = armed_neutral(g);
        g.submit(command(a, 2, 10, true, .1), 10);
        assert(g.submit(command(a, 2, 20, true, .2), 20) == MotionResult::InvalidSequence);
        assert(g.submit(command(a, 1, 20, true, .2), 20) == MotionResult::InvalidSequence);
        assert(g.snapshot(259).target.linear_mps == .1);
        zero(g.snapshot(260));
        assert(g.snapshot(260).reason == StopReason::CommandExpired && !g.snapshot(260).armed);
        assert(g.submit(command(a, 3, 261, true, .1), 261) == MotionResult::NotArmed);
    }
    {
        MotionGuard g{11};
        auto a = armed_neutral(g);
        auto c = command(a, 2, 1, true, .2);
        c.boot_id = 12;
        assert(g.submit(c, 1) == MotionResult::InvalidSession);
        c.boot_id = 11;
        c.controller_id = 43;
        assert(g.submit(c, 1) == MotionResult::InvalidSession);
        c.controller_id = 42;
        c.source = ControlSource::Gamepad;
        assert(g.submit(c, 1) == MotionResult::InvalidSource);
        zero(g.snapshot(250));
    }
    {
        MotionGuard g{11};
        auto a = armed_neutral(g);
        auto c = command(a, 2, 10, true, .2);
        c.issued_ms = 11;
        assert(g.submit(c, 10) == MotionResult::InvalidWindow);
        c.issued_ms = 0;
        c.valid_until_ms = 10;
        assert(g.submit(c, 10) == MotionResult::InvalidWindow);
        c.valid_until_ms = 251;
        assert(g.submit(c, 10) == MotionResult::InvalidWindow);
        assert(g.submit(command(a, 2, 50, true, .1), 50) == MotionResult::Accepted);
        c = command(a, 3, 49, true, .2);
        assert(g.submit(c, 51) == MotionResult::InvalidWindow);
        // Delivery at t=200 does not change an issued-at-100 deadline of t=350.
        assert(g.submit(command(a, 3, 100, true, .2), 200) == MotionResult::Accepted);
        assert(g.snapshot(349).target.linear_mps == .2);
        zero(g.snapshot(350));
    }
    {
        MotionGuard g{11};
        auto a = armed_neutral(g);
        auto c = command(a, 2, 1, true, .26);
        assert(g.submit(c, 1) == MotionResult::InvalidVelocity);
        zero(g.snapshot(1));
        assert(!g.snapshot(1).armed);
    }
    for (double bad : {std::numeric_limits<double>::quiet_NaN(), std::numeric_limits<double>::infinity()})
    {
        MotionGuard g{11};
        auto a = armed_neutral(g);
        assert(g.submit(command(a, 2, 1, true, bad), 1) == MotionResult::InvalidVelocity);
        zero(g.snapshot(1));
    }
    {
        MotionGuard g{11};
        auto a = g.arm(42, ControlSource::Keyboard, 0);
        auto c = command(a.session_id, 1, 0);
        c.input_neutral = false;
        assert(g.submit(c, 0) == MotionResult::NeutralRequired);
        zero(g.snapshot(0));
    }
    {
        MotionGuard g{11};
        auto old = armed_neutral(g);
        g.submit(command(old, 2, 1, true, .2), 1);
        g.stop(2);
        zero(g.snapshot(2));
        assert(g.snapshot(2).stop_latched);
        assert(g.arm(43, ControlSource::Gamepad, 3).result == MotionResult::StopLatched);
        g.clear_stop(4);
        zero(g.snapshot(4));
        assert(!g.snapshot(4).armed);
        auto next = armed_neutral(g, 5);
        assert(next != old);
        assert(g.submit(command(old, 3, 6, true, .2), 6) == MotionResult::InvalidSession);
    }
    {
        MotionGuard g{11};
        auto a = armed_neutral(g);
        g.submit(command(a, 2, 1, true, .2), 1);
        g.set_link(false, 2);
        zero(g.snapshot(2));
        assert(!g.snapshot(2).armed);
        assert(g.arm(42, ControlSource::Keyboard, 3).result == MotionResult::LinkUnavailable);
        g.set_link(true, 4);
        zero(g.snapshot(4));
        assert(!g.snapshot(4).armed);
    }
    {
        MotionGuard g{11};
        auto a = armed_neutral(g, 100);
        g.submit(command(a, 2, 101, true, .1), 101);
        zero(g.snapshot(100));
        assert(g.snapshot(100).reason == StopReason::ClockRegression);
        assert(g.arm(42, ControlSource::Keyboard, 99).result == MotionResult::ClockRegression);
    }
    {
        MotionGuard g{11, {.25, .6, 250, 500}};
        auto a = armed_neutral(g);
        g.submit(command(a, 2, 200, true, .1), 200);
        g.submit(command(a, 3, 400, true, .1), 400);
        assert(g.snapshot(499).armed);
        zero(g.snapshot(500));
        assert(g.snapshot(500).reason == StopReason::LeaseExpired);
    }
    {
        MotionGuard g{11};
        g.arm(42, ControlSource::Keyboard, 0);
        assert(g.snapshot(249).armed);
        zero(g.snapshot(250));
        assert(!g.snapshot(250).armed);
    }
    {
        MotionGuard g{11};
        auto a = armed_neutral(g);
        assert(g.release(43, 11, a, 1) == MotionResult::InvalidSession);
        assert(g.snapshot(1).armed);
        assert(g.release(42, 11, a, 2) == MotionResult::Released);
        zero(g.snapshot(2));
    }
    {
        MotionGuard old{11}, fresh{12};
        auto a = armed_neutral(old);
        auto b = fresh.arm(42, ControlSource::Keyboard, 0);
        assert(a == b.session_id);
        assert(fresh.submit(command(a, 1, 0), 0) == MotionResult::InvalidSession);
    }
    {
        const auto n = std::numeric_limits<std::uint64_t>::max();
        MotionGuard g{11};
        auto a = g.arm(42, ControlSource::Keyboard, n - 100);
        auto c = command(a.session_id, 1, 0);
        c.issued_ms = n - 100;
        c.valid_until_ms = n;
        assert(g.submit(c, n - 100) == MotionResult::Accepted);
        assert(g.snapshot(n - 1).command_remaining_ms == 1);
        zero(g.snapshot(n));
    }
    for (MotionLimits l : {MotionLimits{0, .6, 250, 1000}, MotionLimits{.25, -.6, 250, 1000},
                           MotionLimits{.25, .6, 0, 1000}, MotionLimits{.25, .6, 251, 250}})
    {
        MotionGuard g{11, l};
        assert(g.arm(42, ControlSource::Keyboard, 0).result == MotionResult::InvalidConfig);
        zero(g.snapshot(0));
    }
    {
        MotionGuard g{0};
        assert(g.arm(42, ControlSource::Keyboard, 0).result == MotionResult::InvalidConfig);
    }
    {
        MotionGuard g{11};
        assert(g.arm(0, ControlSource::Keyboard, 0).result == MotionResult::InvalidSession);
        assert(g.arm(42, ControlSource::None, 0).result == MotionResult::InvalidSource);
    }
    std::cout << "motion guard scenarios passed\n";
}
