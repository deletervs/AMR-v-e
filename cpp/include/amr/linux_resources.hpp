#pragma once

#include <cstdint>
#include <string>

// Linux /proc 文本到数值的适配层；不负责 SSH、定时或 ROS。
namespace amr
{
    struct CpuTicks
    {
        std::uint64_t total{0};
        std::uint64_t idle{0};
    };

    struct MemoryBytes
    {
        std::uint64_t used{0};
        std::uint64_t total{0};
    };

    // 失败时不修改 output。guest 计数已包含在 user/nice 中，不能再加一次。
    bool parse_cpu_line(const std::string &text, CpuTicks &output);
    bool cpu_usage(const CpuTicks &before, const CpuTicks &after, double &output);
    // Linux meminfo 的 kB 按 1024 字节转换，used = MemTotal - MemAvailable。
    bool parse_memory_info(const std::string &text, MemoryBytes &output);
}
