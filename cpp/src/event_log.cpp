#include "amr/event_log.hpp"

namespace amr
{
    LogAppendResult EventLog::append(const LogEntry &entry)
    {
        // 先校验所有输入；错误不能改变已有历史。
        if (entry.elapsed < 0 ||
            (!entries_.empty() && entry.elapsed < entries_.back().elapsed))
        {
            return LogAppendResult::InvalidTime;
        }

        switch (entry.level)
        {
        case LogLevel::Info:
        case LogLevel::Warning:
        case LogLevel::Error:
            break;
        default:
            return LogAppendResult::InvalidLevel;
        }

        if (entry.source.empty() || entry.source.size() > 64)
        {
            return LogAppendResult::InvalidSource;
        }

        if (entry.message.empty() || entry.message.size() > 512)
        {
            return LogAppendResult::InvalidMessage;
        }

        // 先复制新事件，再淘汰旧事件；复制失败时不会提前丢失历史。
        entries_.push_back(entry);
        if (entries_.size() > max_entries)
        {
            entries_.pop_front();
            truncated_ = true;
        }

        return LogAppendResult::Append;
    }

    const std::deque<LogEntry> &EventLog::entries() const
    {
        return entries_;
    }

    bool EventLog::truncated() const
    {
        return truncated_;
    }
} // namespace amr
