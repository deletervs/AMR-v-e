// Pure C++ input mapping preview. No ROS, hardware, network or motion output.
#include "amr/input_mapping.hpp"
#include <charconv>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <locale>
#include <sstream>
#include <string>

namespace
{
    bool flag(int n) { return n == 0 || n == 1; }
    const char *label(amr::InputMapResult value)
    {
        switch (value)
        {
        case amr::InputMapResult::Mapped:
            return "Mapped";
        case amr::InputMapResult::InvalidLimits:
            return "InvalidLimits";
        case amr::InputMapResult::InvalidAxis:
            return "InvalidAxis";
        case amr::InputMapResult::InvalidDeadzone:
            return "InvalidDeadzone";
        }
        return "InvalidInput";
    }
    void process(const std::string &line)
    {
        std::istringstream input{line};
        input.imbue(std::locale::classic());
        std::string mode, token;
        std::uint64_t id{0};
        int deadman{-1};
        amr::VelocityTarget target{};
        const char *result = "InvalidInput";
        if (input >> mode >> token >> deadman)
        {
            const auto parsed = std::from_chars(token.data(), token.data() + token.size(), id);
            const bool valid_id = parsed.ec == std::errc{} && parsed.ptr == token.data() + token.size() && id != 0;
            if (!valid_id)
                id = 0;
            if (valid_id && flag(deadman))
            {
                if (mode == "keys")
                {
                    int forward{-1}, backward{-1}, left{-1}, right{-1};
                    if ((input >> forward >> backward >> left >> right) && flag(forward) && flag(backward) && flag(left) && flag(right))
                    {
                        input >> std::ws;
                        if (input.eof())
                            result = label(amr::map_virtual_input(forward, backward, left, right, deadman, .25, .6, target));
                    }
                }
                else if (mode == "pad")
                {
                    double x{0}, y{0};
                    if (input >> x >> y)
                    {
                        input >> std::ws;
                        if (input.eof())
                            result = label(amr::map_gamepad_input(x, y, deadman, .25, .6, .15, target));
                    }
                }
            }
        }
        std::cout << std::setprecision(17) << "{\"source\":\"cpp_input_preview\",\"request_id\":\"" << id
                  << "\",\"result\":\"" << result << "\",\"linear_mps\":" << target.linear_mps
                  << ",\"angular_radps\":" << target.angular_radps
                  << ",\"robot_connected\":false,\"motion_enabled\":false}\n"
                  << std::flush;
    }
} // namespace
int main(int argc, char **)
{
    if (argc != 1)
        return 2;
    std::cout.imbue(std::locale::classic());
    std::string line;
    char ch;
    while (std::cin.get(ch))
    {
        if (ch == '\n')
        {
            process(line);
            line.clear();
            if (!std::cout)
                return 3;
        }
        else if (ch != '\r')
        {
            if (line.size() >= 256)
                return 2;
            line += ch;
        }
    }
    // Unterminated requests are not processed.
    return line.empty() ? 0 : 2;
}