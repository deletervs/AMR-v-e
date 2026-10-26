#pragma once

#include <cstddef>
#include <cstdint>
#include <deque>
#include <string>

/*
本文件定义 事件日志功能 的枚举类和函数
*/

namespace amr
{
    enum class LogLevel // 日志的等级
    {
        Info,
        Warning,
        Error
    };

    struct LogEntry // 日志入口
    {
        std::int64_t elapsed{0};             // 当前进程开始至今的时间(ms)
        amr::LogLevel level{LogLevel::Info}; // 事件等级
        std::string source{};                // 来源
        std::string message{};               // 事件内容
    };

    enum class LogAppendResult //
    {
        Append,
        InvalidTime,
        InvalidLevel,
        InvalidSource,
        InvalidMessage
    };

} // namespace amr
