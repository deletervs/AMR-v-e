// Secondary-development input process. No ROS, actuator or kernel/driver changes.
#include "amr/linux_gamepad_reader.hpp"
#include <algorithm>
#include <array>
#include <cerrno>
#include <csignal>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <linux/joystick.h>
#include <optional>
#include <poll.h>
#include <stdexcept>
#include <sys/ioctl.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <type_traits>
#include <unistd.h>
namespace
{
    using Clock = std::chrono::steady_clock;
    volatile std::sig_atomic_t interrupted{0};
    void interrupt(int) { interrupted = 1; }
    struct Descriptor
    {
        int value{-1};
        Descriptor() = default;
        Descriptor(const Descriptor &) = delete;
        Descriptor &operator=(const Descriptor &) = delete;
        ~Descriptor() { if (value >= 0) close(value); }
    };
    enum Button : std::size_t { RB, Start, Back, Stop, Clear, Count };
    const std::array<unsigned short, Count> codes{BTN_TR, BTN_START, BTN_SELECT, BTN_EAST, BTN_SOUTH};
    struct Input
    {
        std::optional<int> x, y;
        std::array<std::optional<bool>, Count> buttons{};
        std::optional<std::uint64_t> last_event;
        std::uint64_t init_events{0}, change_events{0};
        bool initialized() const
        {
            return x && y && std::all_of(buttons.begin(), buttons.end(),
                                        [](const auto &v) { return v.has_value(); });
        }
        void clear() { x.reset(); y.reset(); for (auto &b : buttons) b.reset(); }
    };

    bool same_device(int fd, const std::string &path, const struct stat &identity)
    {
        struct stat current{}, opened{};
        return fstat(fd, &opened) == 0 && lstat(path.c_str(), &current) == 0 &&
               S_ISCHR(current.st_mode) && S_ISCHR(opened.st_mode) &&
               current.st_dev == identity.st_dev && current.st_ino == identity.st_ino &&
               current.st_rdev == identity.st_rdev && opened.st_ino == identity.st_ino &&
               opened.st_rdev == identity.st_rdev;
    }
    int collect(const std::string &path, unsigned seconds, Clock::time_point epoch, int output)
    {
        Input input;
        const auto elapsed = [&] { return static_cast<std::uint64_t>(
            std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now()-epoch).count()); };
        std::uint64_t sequence{0};
        const auto emit = [&](amr::io::UsbStatus status, std::uint32_t pressed) {
            amr::io::UsbSample sample{};
            sample.sequence=++sequence; sample.observed_ms=elapsed();
            sample.init_events=input.init_events; sample.change_events=input.change_events;
            if (input.x) { sample.known_mask|=1; sample.x=*input.x; }
            if (input.y) { sample.known_mask|=2; sample.y=*input.y; }
            for (std::size_t i{0};i<Count;++i)
                if (input.buttons[i])
                {
                    sample.known_mask|=1U<<(i+2);
                    if (*input.buttons[i]) sample.button_mask|=1U<<i;
                }
            if(input.last_event) { sample.known_mask|=128; sample.last_event_ms=*input.last_event; }
            sample.pressed_mask=pressed; sample.status=static_cast<std::uint32_t>(status);
            const auto size=write(output,&sample,sizeof(sample));
            return size==static_cast<ssize_t>(sizeof(sample));
        };
        try
        {
            std::ifstream bus{"/sys/class/input/" + path.substr(11) + "/device/id/bustype"};
            std::string kind;
            if (!(bus >> kind) || kind != "0003") throw std::runtime_error("Explicit device must be USB");
            Descriptor device;
            device.value = open(path.c_str(), O_RDONLY | O_NONBLOCK | O_CLOEXEC | O_NOFOLLOW);
            struct stat identity{};
            if (device.value < 0 || fstat(device.value, &identity) != 0 ||
                !same_device(device.value, path, identity)) throw std::runtime_error("Invalid USB device identity");
            unsigned char axes{0}, buttons{0}, axis_map[ABS_CNT]{};
            unsigned short button_map[KEY_MAX - BTN_MISC + 1]{};
            if (ioctl(device.value, JSIOCGAXES, &axes) < 0 || ioctl(device.value, JSIOCGBUTTONS, &buttons) < 0 ||
                axes > ABS_CNT || ioctl(device.value, JSIOCGAXMAP, axis_map) < 0 ||
                ioctl(device.value, JSIOCGBTNMAP, button_map) < 0)
                throw std::runtime_error("Invalid joystick metadata");
            int x_index{-1}, y_index{-1};
            std::array<int, Count> indexes{};
            indexes.fill(-1);
            for (int i{0}; i < axes; ++i)
            {
                if (axis_map[i] == ABS_X && x_index < 0) x_index = i;
                if (axis_map[i] == ABS_Y && y_index < 0) y_index = i;
            }
            for (int i{0}; i < buttons; ++i)
                for (std::size_t j{0}; j < Count; ++j)
                    if (button_map[i] == codes[j] && indexes[j] < 0) indexes[j] = i;
            if (x_index < 0 || y_index < 0 || std::any_of(indexes.begin(), indexes.end(), [](int v) { return v < 0; }))
                { throw std::runtime_error("Missing required semantic axes/buttons"); }

            std::signal(SIGINT,interrupt);std::signal(SIGTERM,interrupt);
            std::signal(SIGHUP,interrupt);std::signal(SIGPIPE,SIG_IGN);
            std::uint64_t last_tick{elapsed()},last_emit{0};
            auto finish=amr::io::UsbStatus::RuntimeExpired;
            if(!emit(amr::io::UsbStatus::Observing,0)) return 3;
            while(true)
            {
                const auto before=elapsed();
                if(interrupted) { finish=amr::io::UsbStatus::Interrupted;break; }
                if(seconds && before>=seconds*1000ULL) break;
                if(before-last_tick>=150) { finish=amr::io::UsbStatus::LoopLate;break; }
                last_tick=before;
                if (!same_device(device.value, path, identity)) { finish = amr::io::UsbStatus::Disconnected; break; }
                pollfd fd{device.value, POLLIN, 0};
                const int ready = poll(&fd, 1, 20);
                if (ready < 0 && errno == EINTR) continue;
                if (ready < 0) throw std::runtime_error("Input poll failed");
                if (fd.revents & (POLLERR | POLLHUP | POLLNVAL)) { finish = amr::io::UsbStatus::Disconnected; break; }
                std::array<bool, Count> pressed{};
                bool disconnected{false};
                if (fd.revents & POLLIN)
                {
                    for (unsigned drained{0}; drained < 64; ++drained)
                    {
                        js_event event{};
                        const auto bytes = read(device.value, &event, sizeof(event));
                        if (bytes < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) break;
                        if (bytes < 0 && errno == EINTR) continue;
                        if (bytes == 0 || (bytes < 0 && (errno == ENODEV || errno == EIO))) { disconnected = true; break; }
                        if (bytes != static_cast<ssize_t>(sizeof(event))) throw std::runtime_error("Invalid event frame");
                        const unsigned type = event.type & ~JS_EVENT_INIT;
                        if (type != JS_EVENT_AXIS && type != JS_EVENT_BUTTON) throw std::runtime_error("Invalid event type");
                        if ((type == JS_EVENT_AXIS && event.number >= axes) ||
                            (type == JS_EVENT_BUTTON && (event.number >= buttons || (event.value != 0 && event.value != 1))))
                            throw std::runtime_error("Invalid event index/value");
                        input.last_event = elapsed();
                        const bool initial = (event.type & JS_EVENT_INIT) != 0;
                        if (initial) ++input.init_events; else ++input.change_events;
                        if (type == JS_EVENT_AXIS && event.number == x_index) input.x = event.value;
                        if (type == JS_EVENT_AXIS && event.number == y_index) input.y = event.value;
                        if (type == JS_EVENT_BUTTON)
                            for (std::size_t i{0}; i < Count; ++i)
                                if (event.number == indexes[i])
                                {
                                    const bool value = event.value != 0;
                                    // Initialization can stop, but can never arm, release or clear.
                                    if (value && ((i == Stop) || (!initial && input.buttons[i] && !*input.buttons[i])))
                                        pressed[i] = true;
                                    input.buttons[i] = value;
                                }
                    }
                    pollfd backlog{device.value, POLLIN, 0};
                    if (poll(&backlog, 1, 0) < 0) throw std::runtime_error("Backlog check failed");
                    if (backlog.revents & POLLIN) { finish = amr::io::UsbStatus::Error; break; }
                    if (backlog.revents & (POLLERR | POLLHUP | POLLNVAL)) disconnected = true;
                }
                if (disconnected || !same_device(device.value, path, identity)) { finish = amr::io::UsbStatus::Disconnected; break; }

                const auto now=elapsed();
                if(now-before>=150) { finish=amr::io::UsbStatus::LoopLate;break; }
                std::uint32_t mask{0};
                for(std::size_t i{0};i<Count;++i) if(pressed[i]) mask|=1U<<i;
                if(mask || now-last_emit>=20)
                {
                    last_emit=now;
                    if(!emit(amr::io::UsbStatus::Observing,mask)) return 3;
                }
            }
            input.clear();
            return emit(finish,0)?0:3;
        }
        catch(const std::exception &error)
        {
            std::cerr<<error.what()<<'\n';input.clear();emit(amr::io::UsbStatus::Error,0);return 2;
        }
    }
    // An unreaped owned child cannot be reused as another PID. No other reaper exists.
    void stop_owned_child(pid_t &pid) noexcept
    {
        if(pid<=1) return;
        int status{0};
        const auto initial=waitpid(pid,&status,WNOHANG);
        if(initial==pid || (initial<0 && errno==ECHILD)) { pid=-1;return; }
        if(initial<0 && errno!=EINTR) return;
        kill(pid,SIGCONT);kill(pid,SIGTERM);
        const auto end=Clock::now()+std::chrono::milliseconds{200};
        while(Clock::now()<end)
        {
            const auto result=waitpid(pid,&status,WNOHANG);
            if(result==pid || (result<0 && errno==ECHILD)) {pid=-1;return;}
            if(result<0 && errno!=EINTR) break;
            usleep(1000);
        }
        kill(pid,SIGKILL);
        while(waitpid(pid,&status,0)<0 && errno==EINTR) {}
        pid=-1;
    }
}
namespace amr::io
{
    static_assert(std::is_trivially_copyable<UsbSample>::value,"private pipe requires trivial packets");
    static_assert(sizeof(UsbSample)<=512,"single atomic pipe write");
    LinuxGamepadReader::LinuxGamepadReader(const std::string &path,unsigned seconds,Clock::time_point epoch)
    {
        int descriptors[2]{-1,-1};
        if(pipe2(descriptors,O_NONBLOCK|O_CLOEXEC)!=0) throw std::runtime_error("Cannot create source pipe");
        const auto parent=getpid();
        child_=fork();
        if(child_<0) {close(descriptors[0]);close(descriptors[1]);throw std::runtime_error("Cannot fork source");}
        if(child_==0)
        {
            close(descriptors[0]);close(STDOUT_FILENO);
            if(prctl(PR_SET_PDEATHSIG,SIGKILL)!=0 || getppid()!=parent) _exit(2);
            const auto code=collect(path,seconds,epoch,descriptors[1]);
            close(descriptors[1]);_exit(code);
        }
        close(descriptors[1]);read_fd_=descriptors[0];
    }
    LinuxGamepadReader::~LinuxGamepadReader() {stop();}
    void LinuxGamepadReader::stop() noexcept
    {
        if(read_fd_>=0) {close(read_fd_);read_fd_=-1;}
        stop_owned_child(child_);
    }
    bool LinuxGamepadReader::read_sample(UsbSample &sample)
    {
        while(received_<sizeof(pending_))
        {
            const auto n=read(read_fd_,reinterpret_cast<char *>(&pending_)+received_,sizeof(pending_)-received_);
            if(n<0 && (errno==EAGAIN || errno==EWOULDBLOCK)) return false;
            if(n<0 && errno==EINTR) continue;
            if(n<=0) throw std::runtime_error("USB source pipe ended");
            received_+=static_cast<unsigned>(n);
        }
        sample=pending_;received_=0;return true;
    }
}
