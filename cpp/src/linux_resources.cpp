#include "amr/linux_resources.hpp"

#include <array>
#include <limits>
#include <sstream>

namespace
{
    bool unsigned_decimal(const std::string &text, std::uint64_t &value)
    {
        if (text.empty()) return false;
        std::uint64_t result{0};
        for (const unsigned char ch : text)
        {
            if (ch < '0' || ch > '9') return false;
            const auto digit = static_cast<std::uint64_t>(ch - '0');
            if (result > (std::numeric_limits<std::uint64_t>::max() - digit) / 10)
                return false;
            result = result * 10 + digit;
        }
        value = result;
        return true;
    }
}

namespace amr
{
    bool parse_cpu_line(const std::string &text, CpuTicks &output)
    {
        std::istringstream stream{text};
        std::string token;
        if (!(stream >> token) || token != "cpu") return false;
        std::array<std::uint64_t, 10> values{};
        std::size_t count{0};
        while (stream >> token)
        {
            if (count == values.size() || !unsigned_decimal(token, values[count]))
                return false;
            ++count;
        }
        if (count < 8) return false;
        CpuTicks candidate{};
        for (std::size_t i{0}; i < 8; ++i)
        {
            if (candidate.total > std::numeric_limits<std::uint64_t>::max() - values[i])
                return false;
            candidate.total += values[i];
        }
        // total 加法已检查过；idle 是其中两个非负项，其和不会超过 total。
        candidate.idle = values[3] + values[4];
        output = candidate;
        return true;
    }

    bool cpu_usage(const CpuTicks &before, const CpuTicks &after, double &output)
    {
        if (before.idle > before.total || after.idle > after.total ||
            after.total <= before.total || after.idle < before.idle)
            return false;
        const auto total = after.total - before.total;
        const auto idle = after.idle - before.idle;
        if (idle > total) return false;
        output = 100.0 * (static_cast<double>(total - idle) / static_cast<double>(total));
        return true;
    }

    bool parse_memory_info(const std::string &text, MemoryBytes &output)
    {
        std::istringstream lines{text};
        std::string line;
        bool found_total{false};
        bool found_available{false};
        std::uint64_t total_kib{0};
        std::uint64_t available_kib{0};
        while (std::getline(lines, line))
        {
            std::istringstream fields{line};
            std::string key, number, unit, extra;
            if (!(fields >> key)) continue;
            if (key != "MemTotal:" && key != "MemAvailable:") continue;
            std::uint64_t value{0};
            if (!(fields >> number >> unit) || unit != "kB" || fields >> extra ||
                !unsigned_decimal(number, value))
                return false;
            if (key == "MemTotal:")
            {
                if (found_total) return false;
                total_kib = value;
                found_total = true;
            }
            else
            {
                if (found_available) return false;
                available_kib = value;
                found_available = true;
            }
        }
        if (!found_total || !found_available || total_kib == 0 ||
            available_kib > total_kib ||
            total_kib > std::numeric_limits<std::uint64_t>::max() / 1024)
            return false;
        output = {(total_kib - available_kib) * 1024, total_kib * 1024};
        return true;
    }
}
