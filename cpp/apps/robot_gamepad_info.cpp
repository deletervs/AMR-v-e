// Robot-local joystick metadata only. No event submission, ROS or actuator access.
#include <cerrno>
#include <fcntl.h>
#include <iomanip>
#include <iostream>
#include <linux/joystick.h>
#include <sstream>
#include <string>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <unistd.h>

namespace
{
    std::string quoted(const std::string &value)
    {
        std::ostringstream out;
        out << '"';
        for (unsigned char c : value)
        {
            if (c == '"' || c == '\\')
                out << '\\' << static_cast<char>(c);
            else if (c < 32 || c >= 127)
                out << "\\u00" << std::hex << std::setw(2) << std::setfill('0') << static_cast<unsigned>(c);
            else
                out << static_cast<char>(c);
        }
        out << '"';
        return out.str();
    }
}

int main()
{
    std::cout << "{\"source\":\"robot_gamepad_info\",\"actuator_attached\":false,\"motion_enabled\":false,\"devices\":[";
    unsigned found{0};
    for (unsigned index{0}; index < 32; ++index)
    {
        const auto path = std::string{"/dev/input/js"} + std::to_string(index);
        struct stat info{};
        if (lstat(path.c_str(), &info) != 0 || !S_ISCHR(info.st_mode))
            continue;
        if (found++)
            std::cout << ',';
        std::cout << "{\"path\":" << quoted(path);
        const int fd = open(path.c_str(), O_RDONLY | O_NONBLOCK | O_CLOEXEC | O_NOFOLLOW);
        if (fd < 0)
        {
            std::cout << ",\"readable\":false,\"errno\":" << errno << '}';
            continue;
        }
        char name[256]{};
        unsigned char axes{0}, buttons{0}, axis_map[ABS_CNT]{};
        unsigned short button_map[KEY_MAX - BTN_MISC + 1]{};
        const bool ok = ioctl(fd, JSIOCGNAME(sizeof(name) - 1), name) >= 0 &&
                        ioctl(fd, JSIOCGAXES, &axes) >= 0 && ioctl(fd, JSIOCGBUTTONS, &buttons) >= 0 &&
                        ioctl(fd, JSIOCGAXMAP, axis_map) >= 0 && ioctl(fd, JSIOCGBTNMAP, button_map) >= 0;
        const int metadata_errno = ok ? 0 : errno;
        close(fd);
        std::cout << ",\"readable\":true,\"metadata_valid\":" << (ok ? "true" : "false");
        if (ok)
        {
            std::cout << ",\"name_bytes\":" << quoted(name) << ",\"axis_codes\":[";
            for (unsigned i{0}; i < axes && i < ABS_CNT; ++i)
                std::cout << (i ? "," : "") << static_cast<unsigned>(axis_map[i]);
            std::cout << "],\"button_codes\":[";
            for (unsigned i{0}; i < buttons && i < KEY_MAX - BTN_MISC + 1; ++i)
                std::cout << (i ? "," : "") << button_map[i];
            std::cout << ']';
        }
        else
            std::cout << ",\"errno\":" << metadata_errno;
        std::cout << '}';
    }
    std::cout << "],\"device_count\":" << found << ",\"scan_range\":\"js0..js31\"}\n";
    return std::cout.good() ? 0 : 1;
}