// Real USB input -> existing mathematical mapping only; no control/actuator code.
#include "amr/input_mapping.hpp"
#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <locale>
#include <csignal>
#include <cstdint>
#include <fcntl.h>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <linux/joystick.h>
#include <optional>
#include <poll.h>
#include <regex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <unistd.h>

namespace
{
    using Clock = std::chrono::steady_clock;
    volatile std::sig_atomic_t interrupted{0};
    void interrupt(int) { interrupted = 1; }
    struct Descriptor
    {
        int value{-1};
        ~Descriptor() { if (value >= 0) close(value); }
    };
    struct State
    {
        std::optional<int> x, y;
        std::optional<bool> rb;
        std::optional<std::uint64_t> last_event;
        std::uint64_t sequence{0}, init_events{0}, change_events{0};
        bool neutral{false};
        void clear() { x.reset(); y.reset(); rb.reset(); }
        bool initialized() const { return x.has_value() && y.has_value() && rb.has_value(); }
    };
    double normalized(int value) { return std::clamp(value / 32767.0, -1.0, 1.0); }
    bool write_frame(const std::string &line)
    {
        if (line.size() > 2048) return false;
        const auto end = Clock::now() + std::chrono::milliseconds{20};
        std::size_t offset{0};
        while (offset < line.size())
        {
            const auto left = std::chrono::duration_cast<std::chrono::milliseconds>(end - Clock::now()).count();
            if (left <= 0) return false;
            pollfd output{STDOUT_FILENO, POLLOUT, 0};
            const int ready = poll(&output, 1, static_cast<int>(left));
            if (ready < 0 && errno == EINTR) continue;
            if (ready <= 0 || !(output.revents & POLLOUT)) return false;
            const auto size = write(STDOUT_FILENO, line.data() + offset, line.size() - offset);
            if (size < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)) continue;
            if (size <= 0) return false;
            offset += static_cast<std::size_t>(size);
        }
        return true;
    }
    bool emit(State &s, std::uint64_t elapsed, bool present, bool running, const char *reason)
    {
        std::ostringstream out;
        out.imbue(std::locale::classic());
        out << std::boolalpha << std::setprecision(17)
            << "{\"source\":\"robot_gamepad_probe\",\"elapsed_ms\":" << elapsed
            << ",\"sequence\":" << ++s.sequence << ",\"actuator_attached\":false,\"motion_enabled\":false"
            << ",\"device_present\":" << present << ",\"running\":" << running
            << ",\"initialized\":" << s.initialized() << ",\"neutral_observed\":" << s.neutral
            << ",\"init_events\":" << s.init_events << ",\"change_events\":" << s.change_events
            << ",\"last_event_elapsed_ms\":";
        if (s.last_event) out << *s.last_event; else out << "null";
        out << ",\"raw\":{\"x\":";
        if (s.x) out << *s.x; else out << "null";
        out << ",\"y\":";
        if (s.y) out << *s.y; else out << "null";
        out << ",\"rb\":";
        if (s.rb) out << *s.rb; else out << "null";
        out << "},\"preview\":";
        if (running && s.initialized())
        {
            amr::VelocityTarget target{};
            if (amr::map_gamepad_input(normalized(*s.x), normalized(*s.y), *s.rb, .25, .6, .1, target) != amr::InputMapResult::Mapped)
                return false;
            out << "{\"linear_mps\":" << target.linear_mps << ",\"angular_radps\":" << target.angular_radps << '}';
        }
        else out << "null";
        out << ",\"reason\":\"" << reason << "\"}\n";
        return write_frame(out.str());
    }
}
int main(int argc, char **argv)
{
    State state;
    const auto started = Clock::now();
    const auto elapsed = [&] { return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now() - started).count()); };
    try
    {
        if (argc != 5 || std::string{argv[1]} != "--device" || std::string{argv[3]} != "--duration-seconds")
            throw std::invalid_argument("Expected --device /dev/input/jsN --duration-seconds 1..120");
        const std::string path{argv[2]}, duration{argv[4]};
        if (!std::regex_match(path, std::regex{"/dev/input/js[0-9]{1,2}"}) || !std::regex_match(duration, std::regex{"[0-9]{1,3}"}))
            throw std::invalid_argument("Invalid device or duration");
        const auto seconds = std::stoul(duration);
        if (seconds < 1 || seconds > 120) throw std::invalid_argument("Duration outside 1..120");
        std::ifstream bus{"/sys/class/input/" + path.substr(11) + "/device/id/bustype"};
        std::string kind;
        if (!(bus >> kind) || kind != "0003") throw std::runtime_error("Explicit device must be USB");
        Descriptor device;
        device.value = open(path.c_str(), O_RDONLY | O_NONBLOCK | O_CLOEXEC | O_NOFOLLOW);
        if (device.value < 0) throw std::runtime_error("Cannot open selected device");
        struct stat identity{}, current{};
        if (fstat(device.value, &identity) != 0 || !S_ISCHR(identity.st_mode) || lstat(path.c_str(), &current) != 0 ||
            !S_ISCHR(current.st_mode) || identity.st_ino != current.st_ino || identity.st_rdev != current.st_rdev)
            throw std::runtime_error("Device identity mismatch");
        unsigned char axes{0}, buttons{0}, axis_map[ABS_CNT]{};
        unsigned short button_map[KEY_MAX - BTN_MISC + 1]{};
        if (ioctl(device.value, JSIOCGAXES, &axes) < 0 || ioctl(device.value, JSIOCGBUTTONS, &buttons) < 0 ||
            axes > ABS_CNT || ioctl(device.value, JSIOCGAXMAP, axis_map) < 0 ||
            ioctl(device.value, JSIOCGBTNMAP, button_map) < 0)
            throw std::runtime_error("Invalid joystick metadata");
        int x_index{-1}, y_index{-1}, rb_index{-1};
        for (int i{0}; i < axes; ++i)
        {
            if (axis_map[i] == ABS_X && x_index < 0) x_index = i;
            if (axis_map[i] == ABS_Y && y_index < 0) y_index = i;
        }
        for (int i{0}; i < buttons; ++i) if (button_map[i] == BTN_TR && rb_index < 0) rb_index = i;
        if (x_index < 0 || y_index < 0 || rb_index < 0) throw std::runtime_error("Missing ABS_X/ABS_Y/BTN_TR semantics");
        const int flags = fcntl(STDOUT_FILENO, F_GETFL);
        if (flags < 0 || fcntl(STDOUT_FILENO, F_SETFL, flags | O_NONBLOCK) < 0) throw std::runtime_error("Cannot bound stdout");
        std::signal(SIGINT, interrupt); std::signal(SIGTERM, interrupt); std::signal(SIGHUP, interrupt); std::signal(SIGPIPE, SIG_IGN);
        std::uint64_t last_emit{0};
        if (!emit(state, elapsed(), true, true, "Observing")) return 3;
        bool present{true};
        const char *reason = "RuntimeExpired";
        while (true)
        {
            const auto now = elapsed();
            if (interrupted) { reason = "Interrupted"; break; }
            if (now >= seconds * 1000) break;
            if (lstat(path.c_str(), &current) != 0 || !S_ISCHR(current.st_mode) || current.st_ino != identity.st_ino || current.st_rdev != identity.st_rdev)
            { present = false; reason = "Disconnected"; break; }
            pollfd input{device.value, POLLIN, 0};
            const int ready = poll(&input, 1, 20);
            if (ready < 0 && errno == EINTR) continue;
            if (ready < 0) throw std::runtime_error("Input poll failed");
            if (input.revents & (POLLERR | POLLHUP | POLLNVAL)) { present = false; reason = "Disconnected"; break; }
            if (input.revents & POLLIN)
            {
                for (unsigned drained{0}; drained < 64; ++drained)
                {
                    js_event event{};
                    const auto bytes = read(device.value, &event, sizeof(event));
                    if (bytes < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) break;
                    if (bytes < 0 && errno == EINTR) continue;
                    if (bytes == 0 || (bytes < 0 && (errno == ENODEV || errno == EIO)))
                    { present = false; reason = "Disconnected"; break; }
                    if (bytes != static_cast<ssize_t>(sizeof(event))) throw std::runtime_error("Invalid event frame");
                    const unsigned type = event.type & ~JS_EVENT_INIT;
                    if (type != JS_EVENT_AXIS && type != JS_EVENT_BUTTON) throw std::runtime_error("Invalid event type");
                    if ((type == JS_EVENT_AXIS && event.number >= axes) || (type == JS_EVENT_BUTTON && (event.number >= buttons || (event.value != 0 && event.value != 1))))
                        throw std::runtime_error("Invalid event index/value");
                    state.last_event = elapsed();
                    if (event.type & JS_EVENT_INIT) ++state.init_events; else ++state.change_events;
                    if (type == JS_EVENT_AXIS && event.number == x_index) state.x = event.value;
                    if (type == JS_EVENT_AXIS && event.number == y_index) state.y = event.value;
                    if (type == JS_EVENT_BUTTON && event.number == rb_index) state.rb = event.value != 0;
                    if (state.initialized() && std::abs(normalized(*state.x)) <= .1 && std::abs(normalized(*state.y)) <= .1 && !*state.rb)
                        state.neutral = true;
                }
            }
            if (!present) break;
            if (elapsed() - last_emit >= 100)
            {
                last_emit = elapsed();
                if (!emit(state, last_emit, true, true, "Observing")) return 3;
            }
        }
        state.clear();
        return emit(state, elapsed(), present, false, reason) ? 0 : 3;
    }
    catch (const std::exception &error)
    {
        state.clear();
        std::cerr << error.what() << '\n';
        emit(state, elapsed(), false, false, "Error");
        return 2;
    }
}