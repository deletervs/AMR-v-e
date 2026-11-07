// Linux-only real input acquisition. Internal fork/pipe packets never leave this host.
#pragma once
#include <chrono>
#include <cstdint>
#include <string>
#include <sys/types.h>

namespace amr::io
{
    enum class UsbStatus : std::uint32_t { Observing, Disconnected, LoopLate, RuntimeExpired, Interrupted, Error };
    struct UsbSample
    {
        std::uint64_t magic{0x52535553}, version{1}, sequence{0}, observed_ms{0};
        std::uint64_t last_event_ms{0}, init_events{0}, change_events{0};
        std::int32_t x{0}, y{0};
        std::uint32_t known_mask{0}, button_mask{0}, pressed_mask{0}, status{0};
    };
    class LinuxGamepadReader
    {
      public:
        LinuxGamepadReader(const std::string &path, unsigned seconds,
                           std::chrono::steady_clock::time_point epoch);
        ~LinuxGamepadReader();
        LinuxGamepadReader(const LinuxGamepadReader &) = delete;
        LinuxGamepadReader &operator=(const LinuxGamepadReader &) = delete;
        int fd() const { return read_fd_; }
        pid_t pid() const { return child_; }
        bool read_sample(UsbSample &sample); // false only means EAGAIN, EOF throws.
        void stop() noexcept;
      private:
        int read_fd_{-1};
        pid_t child_{-1};
        unsigned received_{0};
        UsbSample pending_{};
    };
}