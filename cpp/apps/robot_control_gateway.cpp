// Robot-local guard runtime. No actuator adapter, ROS publisher or hardware writes.
#include "amr/input_mapping.hpp"
#include "amr/motion_guard.hpp"
#include "amr/command_dispatcher.hpp"
#include <cerrno>
#include <chrono>
#include <cmath>
#include <csignal>
#include <fcntl.h>
#include <iomanip>
#include <locale>
#include <poll.h>
#include <random>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unistd.h>
#include <vector>
using namespace amr;
namespace
{
    using Clock = std::chrono::steady_clock;
    volatile std::sig_atomic_t interrupted{0};
    void interrupt(int) { interrupted = 1; }
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
            RS_RESULT(Armed)
            RS_RESULT(Released)
            RS_RESULT(Stopped) RS_RESULT(Cleared)
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
            RS_REASON(WaitingForNeutral)
            RS_REASON(DeadmanReleased)
            RS_REASON(None)
                RS_REASON(CommandExpired) RS_REASON(LeaseExpired) RS_REASON(SoftwareStop) RS_REASON(LinkUnavailable)
                    RS_REASON(InvalidCommand) RS_REASON(ClockRegression) RS_REASON(InvalidConfig)
#undef RS_REASON
        }
        return "Disarmed";
    }

    std::string state(MotionGuard &guard, std::uint64_t now, const std::string &result, std::uint64_t sequence, std::uint64_t request_id)
    {
        const auto s = guard.snapshot(now);
        const auto &l = guard.limits();
        std::ostringstream out;
        out.imbue(std::locale::classic());
        out << std::boolalpha << std::setprecision(17)
            << "{\"source\":\"robot_control_gateway\",\"api_version\":2,\"elapsed_ms\":" << now
            << ",\"request_id\":\"" << request_id << "\""
            << ",\"state_sequence\":" << sequence << ",\"actuator_attached\":false,\"motion_enabled\":false"
            << ",\"boot_id\":\"" << s.boot_id << "\",\"session_id\":\"" << s.session_id
            << "\",\"controller_id\":\"" << s.controller_id << "\",\"command_sequence\":\"" << s.last_sequence
            << "\",\"owner\":\"" << name(s.owner) << "\",\"guard_armed\":" << s.armed
            << ",\"stop_latched\":" << s.stop_latched << ",\"neutral_confirmed\":" << s.neutral_confirmed
            << ",\"transport_available\":" << s.link_available << ",\"candidate\":{\"linear_mps\":" << s.target.linear_mps
            << ",\"angular_radps\":" << s.target.angular_radps << "},\"stop_reason\":\"" << name(s.reason)
            << "\",\"result\":\"" << result << "\",\"lease_remaining_ms\":" << s.lease_remaining_ms
            << ",\"command_remaining_ms\":" << s.command_remaining_ms
            << ",\"limits\":{\"candidate_linear_mps\":" << l.linear_mps << ",\"candidate_angular_radps\":" << l.angular_radps
            << ",\"command_timeout_ms\":" << l.command_timeout_ms << ",\"lease_ms\":" << l.lease_ms << "}}\n";
        return out.str();
    }
    bool write_state(const std::string &value)
    {
        const auto deadline = Clock::now() + std::chrono::milliseconds{20};
        std::size_t offset{0};
        while (offset < value.size())
        {
            const auto left = std::chrono::duration_cast<std::chrono::milliseconds>(deadline - Clock::now()).count();
            if (left <= 0)
                return false;
            pollfd output{STDOUT_FILENO, POLLOUT, 0};
            const int ready = poll(&output, 1, static_cast<int>(left));
            if (ready < 0 && errno == EINTR)
                continue;
            if (ready <= 0 || !(output.revents & POLLOUT))
                return false;
            const auto count = write(STDOUT_FILENO, value.data() + offset, value.size() - offset);
            if (count < 0 && (errno == EAGAIN || errno == EINTR))
                continue;
            if (count <= 0)
                return false;
            offset += static_cast<std::size_t>(count);
        }
        return true;
    }
    bool command(MotionGuard &guard, const std::string &line, std::uint64_t now, std::string &result, std::uint64_t &request_id)
    {
        amr::CommandDispatcher commands{guard};
        bool quit{false};
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
            if (t[0] == "request")
            {
                if (t.size() < 3)
                    throw std::invalid_argument("request");
                request_id = integer(t[1]);
                if (request_id == 0)
                    throw std::invalid_argument("request_id");
                t.erase(t.begin(), t.begin() + 2);
            }
            if (t[0] == "status" && t.size() == 1)
            {
            }
            else if (t[0] == "arm" && t.size() == 3)
                result = name(commands.dispatch(amr::ArmControl{integer(t[1]),
                    t[2] == "keyboard" ? ControlSource::Keyboard : t[2] == "gamepad"
                        ? ControlSource::Gamepad : ControlSource::None}, now).result);
            else if (t[0] == "release" && t.size() == 4)
                result = name(commands.dispatch(amr::ReleaseControl{integer(t[1]), integer(t[2]), integer(t[3])}, now).result);
            else if (t[0] == "stop" && t.size() == 1)
            {
                commands.dispatch(amr::StopControl{}, now);
                result = "Stopped";
            }
            else if (t[0] == "clear" && t.size() == 1)
                result = name(commands.dispatch(amr::ClearControl{}, now).result);
            else if (t[0] == "quit" && t.size() == 1)
            {
                commands.dispatch(amr::StopControl{}, now);
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
                result = name(commands.dispatch(c, now).result);
            }
            else
                throw std::invalid_argument("protocol");
        }
        catch (const std::exception &)
        {
            commands.dispatch(amr::StopControl{}, now);
            result = "InvalidInput";
        }

        return quit;
    }
} // namespace
int main(int argc, char **argv)
{
    unsigned seconds{60};
    try
    {
        if (argc != 1)
        {
            if (argc != 3 || std::string{argv[1]} != "--duration-seconds")
                return 2;
            const auto value = integer(argv[2]);
            if (value < 1 || value > 600)
                return 2;
            seconds = static_cast<unsigned>(value);
        }
        const int input_flags = fcntl(STDIN_FILENO, F_GETFL);
        const int output_flags = fcntl(STDOUT_FILENO, F_GETFL);
        if (input_flags < 0 || output_flags < 0 || fcntl(STDIN_FILENO, F_SETFL, input_flags | O_NONBLOCK) < 0 ||
            fcntl(STDOUT_FILENO, F_SETFL, output_flags | O_NONBLOCK) < 0)
            return 3;
        std::signal(SIGINT, interrupt);
        std::signal(SIGTERM, interrupt);
        std::signal(SIGHUP, interrupt);
        std::signal(SIGPIPE, SIG_IGN);
        std::random_device random;
        auto boot = (static_cast<std::uint64_t>(random()) << 32) ^ random();
        if (!boot)
            boot = 1;
        MotionGuard guard{boot};
        const auto started = Clock::now();
        const auto now_ms = [&]
        { return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now() - started).count()); };
        std::uint64_t sequence{0}, last_report{0};
        auto emit = [&](const std::string &result, std::uint64_t request_id = 0)
        { return write_state(state(guard, now_ms(), result, ++sequence, request_id)); };
        if (!emit("Started"))
            return 3;
        std::string line;
        bool finished{false};
        while (!finished)
        {
            const auto now = now_ms();
            guard.snapshot(now); // independent 20ms reactor tick, never caller-driven time.
            if (interrupted || now >= static_cast<std::uint64_t>(seconds) * 1000)
            {
                guard.stop(now);
                emit(interrupted ? "Interrupted" : "RuntimeExpired");
                break;
            }
            if (now - last_report >= 50)
            {
                if (!emit("Status"))
                {
                    guard.stop(now_ms());
                    return 3;
                }
                last_report = now;
            }
            pollfd input{STDIN_FILENO, POLLIN, 0};
            const int ready = poll(&input, 1, 20);
            if (ready < 0 && errno == EINTR)
                continue;
            if (ready < 0 || (input.revents & (POLLERR | POLLNVAL)))
            {
                guard.stop(now_ms());
                return 3;
            }
            if (ready > 0 && (input.revents & (POLLIN | POLLHUP)))
            {
                char buffer[256];
                const auto count = read(STDIN_FILENO, buffer, sizeof buffer);
                if (count == 0)
                {
                    guard.set_link(false, now_ms());
                    guard.stop(now_ms());
                    emit("InputClosed");
                    break;
                }
                if (count < 0)
                {
                    if (errno == EINTR || errno == EAGAIN)
                        continue;
                    guard.stop(now_ms());
                    return 3;
                }
                for (ssize_t i = 0; i < count; ++i)
                {
                    if (buffer[i] == '\n')
                    {
                        std::string result = "Accepted";
                        std::uint64_t request_id{0};
                        finished = command(guard, line, now_ms(), result, request_id);
                        line.clear();
                        if (!emit(result, request_id))
                        {
                            guard.stop(now_ms());
                            return 3;
                        }
                        if (finished)
                            break;
                    }
                    else
                    {
                        if (line.size() >= 1024)
                        {
                            guard.stop(now_ms());
                            emit("InvalidInput");
                            return 2;
                        }
                        line += buffer[i];
                    }
                }
            }
        }
        return 0;
    }
    catch (...)
    {
        return 3;
    }
}
