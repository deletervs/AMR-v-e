// Pure stdin/JSON simulator: no robot transport, sockets or ROS.
#include "amr/input_mapping.hpp"
#include "amr/motion_guard.hpp"
#include <chrono>
#include <cmath>
#include <iomanip>
#include <iostream>
#include <random>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
using namespace amr;
namespace
{
    using Clock = std::chrono::steady_clock;
    std::uint64_t integer(const std::string &v)
    {
        if (v.empty() || v.find_first_not_of("0123456789") != std::string::npos)
            throw std::invalid_argument("uint64");
        return std::stoull(v);
    }
    bool flag(const std::string &v)
    {
        if (v != "0" && v != "1")
            throw std::invalid_argument("flag");
        return v == "1";
    }
    double axis(const std::string &v)
    {
        std::size_t n = 0;
        double x = std::stod(v, &n);
        if (n != v.size() || !std::isfinite(x))
            throw std::invalid_argument("axis");
        return x;
    }
    const char *name(ControlSource s) { return s == ControlSource::Keyboard ? "keyboard" : s == ControlSource::Gamepad ? "gamepad"
                                                                                                                       : "none"; }
    const char *name(MotionResult r)
    {
        switch (r)
        {
#define RS_RESULT(x)      \
    case MotionResult::x: \
        return #x;
            RS_RESULT(Accepted)
            RS_RESULT(Armed) RS_RESULT(Released) RS_RESULT(Stopped) RS_RESULT(Cleared)
                RS_RESULT(LinkUpdated) RS_RESULT(Busy) RS_RESULT(StopLatched) RS_RESULT(LinkUnavailable)
                    RS_RESULT(InvalidConfig) RS_RESULT(InvalidSource) RS_RESULT(InvalidSession) RS_RESULT(InvalidSequence)
                        RS_RESULT(InvalidWindow) RS_RESULT(InvalidVelocity) RS_RESULT(NeutralRequired) RS_RESULT(ClockRegression)
                            RS_RESULT(NotArmed)
#undef RS_RESULT
        }
        return "InvalidInput";
    }
    const char *name(StopReason r)
    {
        switch (r)
        {
#define RS_REASON(x)    \
    case StopReason::x: \
        return #x;
            RS_REASON(Disarmed)
            RS_REASON(WaitingForNeutral) RS_REASON(DeadmanReleased) RS_REASON(None)
                RS_REASON(CommandExpired) RS_REASON(LeaseExpired) RS_REASON(SoftwareStop) RS_REASON(LinkUnavailable)
                    RS_REASON(InvalidCommand) RS_REASON(ClockRegression) RS_REASON(InvalidConfig)
#undef RS_REASON
        }
        return "Disarmed";
    }
    void write_state(MotionGuard &g, std::uint64_t now, const std::string &result)
    {
        auto s = g.snapshot(now);
        const auto &l = g.limits();
        std::cout << std::boolalpha << std::setprecision(15)
                  << "{\"source\":\"mock_teleop\",\"elapsed_ms\":" << now
                  << ",\"boot_id\":\"" << s.boot_id << "\",\"session_id\":\"" << s.session_id
                  << "\",\"controller_id\":\"" << s.controller_id << "\",\"sequence\":\"" << s.last_sequence
                  << "\",\"owner\":\"" << name(s.owner) << "\",\"armed\":" << s.armed
                  << ",\"stop_latched\":" << s.stop_latched << ",\"neutral_confirmed\":" << s.neutral_confirmed
                  << ",\"link_available\":" << s.link_available << ",\"linear_mps\":" << s.target.linear_mps
                  << ",\"angular_radps\":" << s.target.angular_radps << ",\"stop_reason\":\"" << name(s.reason)
                  << "\",\"result\":\"" << result << "\",\"lease_remaining_ms\":" << s.lease_remaining_ms
                  << ",\"command_remaining_ms\":" << s.command_remaining_ms << ",\"limits\":{\"linear_mps\":" << l.linear_mps
                  << ",\"angular_radps\":" << l.angular_radps << ",\"command_timeout_ms\":" << l.command_timeout_ms
                  << ",\"lease_ms\":" << l.lease_ms << "}}\n"
                  << std::flush;
    }
} // namespace
int main(int argc, char **)
{
    if (argc != 1)
    {
        std::cerr << "usage: mock_teleop (stdin protocol)\n";
        return 2;
    }
    std::random_device random;
    std::uint64_t boot = (static_cast<std::uint64_t>(random()) << 32) ^ random();
    if (!boot)
        boot = 1;
    MotionGuard guard{boot};
    auto start = Clock::now();
    std::string line;
    while (std::getline(std::cin, line))
    {
        const auto now = static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now() - start).count());
        std::string result = "Accepted";
        bool quit = false;
        try
        {
            if (line.size() > 1024)
                throw std::invalid_argument("size");
            std::istringstream in{line};
            std::vector<std::string> t;
            std::string w;
            while (in >> w)
                t.push_back(w);
            if (t.empty())
                throw std::invalid_argument("empty");
            if (t[0] == "tick" && t.size() == 1)
            {
            }
            else if (t[0] == "arm" && t.size() == 3)
                result = name(guard.arm(integer(t[1]), t[2] == "keyboard" ? ControlSource::Keyboard : t[2] == "gamepad" ? ControlSource::Gamepad
                                                                                                                        : ControlSource::None,
                                        now)
                                  .result);
            else if (t[0] == "release" && t.size() == 4)
                result = name(guard.release(integer(t[1]), integer(t[2]), integer(t[3]), now));
            else if (t[0] == "stop" && t.size() == 1)
            {
                guard.stop(now);
                result = "Stopped";
            }
            else if (t[0] == "clear" && t.size() == 1)
                result = name(guard.clear_stop(now));
            else if (t[0] == "link" && t.size() == 2)
            {
                guard.set_link(flag(t[1]), now);
                result = "LinkUpdated";
            }
            else if (t[0] == "quit" && t.size() == 1)
            {
                guard.stop(now);
                result = "Stopped";
                quit = true;
            }
            else if ((t[0] == "keys" && t.size() == 12) || (t[0] == "pad" && t.size() == 10))
            {
                MotionCommand c{};
                c.boot_id = integer(t[1]);
                c.session_id = integer(t[2]);
                c.controller_id = integer(t[3]);
                c.sequence = integer(t[4]);
                c.issued_ms = integer(t[5]);
                c.valid_until_ms = integer(t[6]);
                c.deadman = flag(t[7]);
                const auto &l = guard.limits();
                InputMapResult mapped;
                if (t[0] == "keys")
                {
                    c.source = ControlSource::Keyboard;
                    const bool f = flag(t[8]), b = flag(t[9]), left = flag(t[10]), r = flag(t[11]);
                    c.input_neutral = !c.deadman && !f && !b && !left && !r;
                    mapped = map_virtual_input(f, b, left, r, c.deadman, l.linear_mps, l.angular_radps, c.target);
                }
                else
                {
                    c.source = ControlSource::Gamepad;
                    const double x = axis(t[8]), y = axis(t[9]);
                    c.input_neutral = !c.deadman && std::abs(x) <= .15 && std::abs(y) <= .15;
                    mapped = map_gamepad_input(x, y, c.deadman, l.linear_mps, l.angular_radps, .15, c.target);
                }
                if (mapped != InputMapResult::Mapped)
                    throw std::invalid_argument("mapping");
                result = name(guard.submit(c, now));
            }
            else
                throw std::invalid_argument("protocol");
        }
        catch (const std::exception &)
        {
            guard.stop(now);
            result = "InvalidInput";
        }
        write_state(guard, now, result);
        if (!std::cout)
            return 1;
        if (quit)
            return 0;
    }
    guard.stop(static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now() - start).count()));
}
