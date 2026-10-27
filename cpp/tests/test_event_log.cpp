#include "amr/event_log.hpp"

#include <cstddef>
#include <cstdint>
#include <deque>
#include <exception>
#include <functional>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

// EventLog contract tests; public names follow the existing LogEntry and result enum.
static_assert(amr::EventLog::max_entries == 100, "The agreed capacity is 100.");
static_assert(std::is_same<
                  decltype(std::declval<const amr::EventLog &>().entries()),
                  const std::deque<amr::LogEntry> &>::value,
              "entries() must provide const access without copying the queue.");
static_assert(std::is_same<
                  decltype(std::declval<const amr::EventLog &>().truncated()),
                  bool>::value,
              "truncated() must be readable on a const EventLog.");

namespace
{
    using amr::EventLog;
    using amr::LogAppendResult;
    using amr::LogEntry;
    using amr::LogLevel;

    void expect(bool condition, const std::string &message)
    {
        if (!condition)
        {
            throw std::runtime_error(message);
        }
    }

    bool same_entry(const LogEntry &left, const LogEntry &right)
    {
        return left.elapsed == right.elapsed &&
               left.level == right.level &&
               left.source == right.source &&
               left.message == right.message;
    }

    bool same_entries(const std::deque<LogEntry> &left,
                      const std::deque<LogEntry> &right)
    {
        if (left.size() != right.size())
        {
            return false;
        }
        for (std::size_t i = 0; i < left.size(); ++i)
        {
            if (!same_entry(left[i], right[i]))
            {
                return false;
            }
        }
        return true;
    }

    LogEntry event(std::int64_t elapsed = 100)
    {
        return LogEntry{elapsed, LogLevel::Info, "mock", "sample received"};
    }

    const LogEntry &first_entry(const EventLog &log)
    {
        expect(!log.entries().empty(), "expected a stored event before reading front");
        return log.entries().front();
    }

    const LogEntry &last_entry(const EventLog &log)
    {
        expect(!log.entries().empty(), "expected a stored event before reading back");
        return log.entries().back();
    }

    void expect_rejected_unchanged(EventLog &log,
                                   const LogEntry &entry,
                                   LogAppendResult expected,
                                   const std::string &label)
    {
        const auto before = log.entries();
        const bool was_truncated = log.truncated();
        expect(log.append(entry) == expected, label + ": wrong result");
        expect(same_entries(log.entries(), before), label + ": queue changed");
        expect(log.truncated() == was_truncated, label + ": truncated changed");
    }

    void fill(EventLog &log, std::size_t count)
    {
        for (std::size_t i = 0; i < count; ++i)
        {
            auto entry = event(static_cast<std::int64_t>(i));
            entry.message = "event-" + std::to_string(i);
            expect(log.append(entry) == LogAppendResult::Append,
                   "failed to prepare event-" + std::to_string(i));
            const auto expected_size = i + 1 < EventLog::max_entries
                                           ? i + 1
                                           : EventLog::max_entries;
            expect(log.entries().size() == expected_size,
                   "prepared event count wrong at event-" + std::to_string(i));
            expect(same_entry(last_entry(log), entry),
                   "prepared newest event differs at event-" + std::to_string(i));
        }
    }

    void check_all_rejections(EventLog &log)
    {
        auto invalid = event(-1);
        expect_rejected_unchanged(
            log, invalid, LogAppendResult::InvalidTime, "negative time");
        if (!log.entries().empty())
        {
            invalid = event(last_entry(log).elapsed - 1);
            expect_rejected_unchanged(
                log, invalid, LogAppendResult::InvalidTime, "backward time");
        }
        invalid = event(1000);
        invalid.level = static_cast<LogLevel>(-1);
        expect_rejected_unchanged(
            log, invalid, LogAppendResult::InvalidLevel, "unknown level");
        invalid = event(1000);
        invalid.source.clear();
        expect_rejected_unchanged(
            log, invalid, LogAppendResult::InvalidSource, "empty source");
        invalid.source.assign(65, 's');
        expect_rejected_unchanged(
            log, invalid, LogAppendResult::InvalidSource, "long source");
        invalid = event(1000);
        invalid.message.clear();
        expect_rejected_unchanged(
            log, invalid, LogAppendResult::InvalidMessage, "empty message");
        invalid.message.assign(513, 'm');
        expect_rejected_unchanged(
            log, invalid, LogAppendResult::InvalidMessage, "long message");
    }

    using TestCase = std::pair<std::string, std::function<void()>>;

    std::vector<TestCase> cases()
    {
        return {
            {"default_empty", [] {
                 const EventLog log{};
                 expect(log.entries().empty(), "new log must be empty");
                 expect(!log.truncated(), "new log must not be truncated");
             }},
            {"append_copies_all_fields", [] {
                 EventLog log{};
                 const LogEntry entry{123, LogLevel::Warning, "battery", "sample stale"};
                 expect(log.append(entry) == LogAppendResult::Append, "valid event rejected");
                 expect(log.entries().size() == 1, "expected one event");
                 expect(same_entry(first_entry(log), entry), "saved fields differ");
                 expect(!log.truncated(), "first event must not truncate");
             }},
            {"all_three_levels", [] {
                 EventLog log{};
                 for (const auto level : {LogLevel::Info, LogLevel::Warning, LogLevel::Error})
                 {
                     auto entry = event();
                     entry.level = level;
                     expect(log.append(entry) == LogAppendResult::Append, "valid level rejected");
                 }
                 expect(log.entries().size() == 3, "three events were not stored");
             }},
            {"zero_time_allowed", [] {
                 EventLog log{};
                 expect(log.append(event(0)) == LogAppendResult::Append, "time zero rejected");
                 expect(first_entry(log).elapsed == 0, "time zero changed");
             }},
            {"equal_time_stable_order", [] {
                 EventLog log{};
                 auto first = event(42);
                 first.message = "first";
                 auto second = event(42);
                 second.message = "second";
                 expect(log.append(first) == LogAppendResult::Append, "first event rejected");
                 expect(log.append(second) == LogAppendResult::Append, "equal time rejected");
                 expect(log.entries().size() == 2, "equal-time event was deduplicated");
                 expect(log.entries()[0].message == "first" &&
                            log.entries()[1].message == "second",
                        "equal-time insertion order changed");
             }},
            {"increasing_time_allowed", [] {
                 EventLog log{};
                 expect(log.append(event(5)) == LogAppendResult::Append, "first event rejected");
                 expect(log.append(event(6)) == LogAppendResult::Append, "increasing time rejected");
                 expect(last_entry(log).elapsed == 6, "last time changed");
             }},
            {"negative_time_empty_log", [] {
                 EventLog log{};
                 expect_rejected_unchanged(
                     log, event(-1), LogAppendResult::InvalidTime, "negative time");
             }},
            {"backward_time_rejected", [] {
                 EventLog log{};
                 expect(log.append(event(100)) == LogAppendResult::Append, "setup rejected");
                 expect_rejected_unchanged(
                     log, event(99), LogAppendResult::InvalidTime, "backward time");
             }},
            {"invalid_enum_values_rejected", [] {
                 EventLog log{};
                 for (const int raw : {-1, 3, 100})
                 {
                     auto entry = event();
                     entry.level = static_cast<LogLevel>(raw);
                     expect_rejected_unchanged(
                         log, entry, LogAppendResult::InvalidLevel, "invalid enum");
                 }
             }},
            {"source_empty_rejected", [] {
                 EventLog log{};
                 auto entry = event();
                 entry.source.clear();
                 expect_rejected_unchanged(
                     log, entry, LogAppendResult::InvalidSource, "empty source");
             }},
            {"source_one_and_64_bytes_allowed", [] {
                 EventLog log{};
                 for (const std::size_t length : {std::size_t{1}, std::size_t{64}})
                 {
                     auto entry = event();
                     entry.source.assign(length, 's');
                     expect(log.append(entry) == LogAppendResult::Append, "source boundary rejected");
                     expect(last_entry(log).source.size() == length, "source length changed");
                 }
             }},
            {"source_65_bytes_rejected", [] {
                 EventLog log{};
                 auto entry = event();
                 entry.source.assign(65, 's');
                 expect_rejected_unchanged(
                     log, entry, LogAppendResult::InvalidSource, "long source");
             }},
            {"message_empty_rejected", [] {
                 EventLog log{};
                 auto entry = event();
                 entry.message.clear();
                 expect_rejected_unchanged(
                     log, entry, LogAppendResult::InvalidMessage, "empty message");
             }},
            {"message_one_and_512_bytes_allowed", [] {
                 EventLog log{};
                 for (const std::size_t length : {std::size_t{1}, std::size_t{512}})
                 {
                     auto entry = event();
                     entry.message.assign(length, 'm');
                     expect(log.append(entry) == LogAppendResult::Append, "message boundary rejected");
                     expect(last_entry(log).message.size() == length, "message length changed");
                 }
             }},
            {"message_513_bytes_rejected", [] {
                 EventLog log{};
                 auto entry = event();
                 entry.message.assign(513, 'm');
                 expect_rejected_unchanged(
                     log, entry, LogAppendResult::InvalidMessage, "long message");
             }},
            {"utf8_limits_count_bytes", [] {
                 EventLog log{};
                 auto entry = event();
                 entry.source.clear();
                 for (int i = 0; i < 21; ++i)
                 {
                     entry.source += u8"\u6e90";
                 }
                 entry.source += "s"; // 21 * 3 + 1 = 64 UTF-8 bytes.
                 entry.message.clear();
                 for (int i = 0; i < 170; ++i)
                 {
                     entry.message += u8"\u6e29";
                 }
                 entry.message += "mm"; // 170 * 3 + 2 = 512 UTF-8 bytes.
                 expect(entry.source.size() == 64 && entry.message.size() == 512,
                        "test UTF-8 boundary is incorrect");
                 expect(log.append(entry) == LogAppendResult::Append, "UTF-8 byte boundary rejected");
                 entry.source += u8"\u6e90";
                 expect_rejected_unchanged(
                     log, entry, LogAppendResult::InvalidSource, "UTF-8 source above byte limit");
                 entry.source = "mock";
                 entry.message += u8"\u6e29";
                 expect_rejected_unchanged(
                     log, entry, LogAppendResult::InvalidMessage, "UTF-8 message above byte limit");
             }},
            {"priority_negative_time_before_other_errors", [] {
                 EventLog log{};
                 auto entry = event(-1);
                 entry.level = static_cast<LogLevel>(-1);
                 entry.source.clear();
                 entry.message.clear();
                 expect_rejected_unchanged(
                     log, entry, LogAppendResult::InvalidTime, "time error priority");
             }},
            {"priority_backward_time_before_level", [] {
                 EventLog log{};
                 expect(log.append(event(100)) == LogAppendResult::Append, "setup rejected");
                 auto entry = event(99);
                 entry.level = static_cast<LogLevel>(-1);
                 entry.source.clear();
                 entry.message.clear();
                 expect_rejected_unchanged(
                     log, entry, LogAppendResult::InvalidTime, "backward time priority");
             }},
            {"priority_level_before_text", [] {
                 EventLog log{};
                 auto entry = event();
                 entry.level = static_cast<LogLevel>(-1);
                 entry.source.clear();
                 entry.message.clear();
                 expect_rejected_unchanged(
                     log, entry, LogAppendResult::InvalidLevel, "level error priority");
             }},
            {"priority_source_before_message", [] {
                 EventLog log{};
                 auto entry = event();
                 entry.source.clear();
                 entry.message.clear();
                 expect_rejected_unchanged(
                     log, entry, LogAppendResult::InvalidSource, "source error priority");
             }},
            {"all_errors_preserve_partial_log", [] {
                 EventLog log{};
                 fill(log, 3);
                 check_all_rejections(log);
                 expect(log.entries().size() == 3, "partial queue length changed");
                 expect(!log.truncated(), "partial queue became truncated");
             }},
            {"exact_capacity_keeps_all_events", [] {
                 EventLog log{};
                 fill(log, EventLog::max_entries);
                 expect(log.entries().size() == 100, "capacity boundary length wrong");
                 expect(first_entry(log).elapsed == 0, "oldest event removed too early");
                 expect(last_entry(log).elapsed == 99, "last event missing");
                 expect(!log.truncated(), "exact capacity must not truncate");
             }},
            {"event_101_evicts_only_oldest", [] {
                 EventLog log{};
                 fill(log, EventLog::max_entries);
                 auto entry = event(100);
                 entry.message = "event-100";
                 expect(log.append(entry) == LogAppendResult::Append, "101st event rejected");
                 expect(log.entries().size() == 100, "101st event changed capacity");
                 expect(first_entry(log).elapsed == 1, "wrong event evicted");
                 expect(same_entry(last_entry(log), entry), "new event not stored");
                 for (std::size_t i = 0; i < log.entries().size(); ++i)
                 {
                     expect(log.entries()[i].elapsed == static_cast<std::int64_t>(i + 1),
                            "remaining event order changed");
                 }
                 expect(log.truncated(), "eviction must mark history truncated");
             }},
            {"all_errors_at_capacity_do_not_evict", [] {
                 EventLog log{};
                 fill(log, EventLog::max_entries);
                 check_all_rejections(log);
                 expect(log.entries().size() == 100 &&
                            first_entry(log).elapsed == 0,
                        "invalid event evicted oldest");
                 expect(!log.truncated(), "invalid event set truncated");
             }},
            {"all_errors_after_eviction_preserve_truncated", [] {
                 EventLog log{};
                 fill(log, EventLog::max_entries + 1);
                 expect(log.truncated(), "setup did not truncate");
                 check_all_rejections(log);
                 expect(first_entry(log).elapsed == 1, "invalid event evicted again");
                 expect(log.truncated(), "invalid event cleared truncated");
             }},
            {"repeated_evictions_keep_latest_100", [] {
                 EventLog log{};
                 fill(log, 250);
                 expect(log.entries().size() == 100, "repeated eviction broke capacity");
                 expect(first_entry(log).elapsed == 150, "wrong oldest remaining event");
                 expect(last_entry(log).elapsed == 249, "wrong newest remaining event");
                 expect(log.truncated(), "truncated must stay true");
             }},
            {"input_strings_are_owned_copies", [] {
                 EventLog log{};
                 auto entry = event(123);
                 entry.source = "original-source";
                 entry.message = "original-message";
                 const auto original = entry;
                 expect(log.append(entry) == LogAppendResult::Append, "valid event rejected");
                 entry.elapsed = 999;
                 entry.level = LogLevel::Error;
                 entry.source.assign(1000, 'x');
                 entry.message.clear();
                 expect(same_entry(first_entry(log), original),
                        "stored event depends on caller-owned input");
             }},
            {"independent_instances", [] {
                 EventLog full{};
                 fill(full, 101);
                 EventLog fresh{};
                 expect(fresh.entries().empty(), "new instance inherited entries");
                 expect(!fresh.truncated(), "new instance inherited truncation");
                 expect(fresh.append(event(0)) == LogAppendResult::Append,
                        "new instance inherited last time");
                 expect(full.entries().size() == 100 && full.truncated(),
                        "fresh instance changed existing log");
             }},
            {"int64_max_time_and_equal_time_allowed", [] {
                 EventLog log{};
                 const auto maximum = std::numeric_limits<std::int64_t>::max();
                 expect(log.append(event(maximum)) == LogAppendResult::Append, "int64 maximum time rejected");
                 expect(log.append(event(maximum)) == LogAppendResult::Append, "equal maximum time rejected");
                 expect_rejected_unchanged(
                     log, event(maximum - 1), LogAppendResult::InvalidTime, "time below maximum");
             }},
            {"rejections_do_not_advance_hidden_time", [] {
                 const std::vector<std::pair<LogEntry, LogAppendResult>> invalid_entries{
                     {LogEntry{1000, static_cast<LogLevel>(-1), "mock", "invalid"},
                      LogAppendResult::InvalidLevel},
                     {LogEntry{1000, LogLevel::Info, "", "invalid"},
                      LogAppendResult::InvalidSource},
                     {LogEntry{1000, LogLevel::Info, std::string(65, 's'), "invalid"},
                      LogAppendResult::InvalidSource},
                     {LogEntry{1000, LogLevel::Info, "mock", ""},
                      LogAppendResult::InvalidMessage},
                     {LogEntry{1000, LogLevel::Info, "mock", std::string(513, 'm')},
                      LogAppendResult::InvalidMessage}
                 };
                 for (const auto &invalid : invalid_entries)
                 {
                     for (const std::int64_t next_time : {std::int64_t{100}, std::int64_t{101}})
                     {
                         EventLog log{};
                         const auto first = event(100);
                         expect(log.append(first) == LogAppendResult::Append,
                                "initial valid event rejected");
                         expect(log.entries().size() == 1, "initial event was not stored");
                         expect_rejected_unchanged(
                             log, invalid.first, invalid.second, "invalid future event");
                         const auto next = event(next_time);
                         expect(log.append(next) == LogAppendResult::Append,
                                "rejected event advanced hidden time");
                         expect(log.entries().size() == 2,
                                "valid event after rejection was not stored exactly once");
                         expect(same_entry(first_entry(log), first) &&
                                    same_entry(last_entry(log), next),
                                "valid queue changed after rejected future event");
                         expect(!log.truncated(), "rejection changed truncation state");
                     }
                 }
             }},
            {"text_bytes_are_preserved_without_rewriting", [] {
                 EventLog log{};
                 auto entry = event();
                 entry.source = std::string{"s\0x", 3};
                 entry.message = std::string{"quote:\" slash:\\ newline:\n nul:"};
                 entry.message.push_back('\0');
                 entry.message += "<script>example</script>";
                 const auto original = entry;
                 expect(log.append(entry) == LogAppendResult::Append, "valid byte lengths rejected");
                 expect(same_entry(first_entry(log), original),
                        "core rewrote text; JSON escaping belongs to the output layer");
             }}
        };
    }
} // namespace

int main(int argc, char **argv)
{
    const auto tests = cases();
    if (argc > 2)
    {
        std::cerr << "usage: test_event_log_contract [case-name|--list]\n";
        return 2;
    }
    if (argc == 2 && std::string{argv[1]} == "--list")
    {
        for (const auto &test : tests)
        {
            std::cout << test.first << '\n';
        }
        return 0;
    }

    const std::string selected = argc == 2 ? argv[1] : "";
    std::size_t run = 0;
    std::size_t failed = 0;
    for (const auto &test : tests)
    {
        if (!selected.empty() && test.first != selected)
        {
            continue;
        }
        ++run;
        try
        {
            test.second();
        }
        catch (const std::exception &error)
        {
            ++failed;
            std::cerr << "FAIL " << test.first << ": " << error.what() << '\n';
        }
        catch (...)
        {
            ++failed;
            std::cerr << "FAIL " << test.first << ": unexpected non-standard exception\n";
        }
    }
    if (run == 0)
    {
        std::cerr << "unknown case: " << selected << '\n';
        return 2;
    }
    std::cout << "event_log: " << (run - failed) << '/' << run << " cases passed\n";
    return failed == 0 ? 0 : 1;
}
