#include "amr/system_resources.hpp"

#include <array>
#include <cassert>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <limits>
#include <optional>

namespace
{
    void assert_same(const amr::SystemResourcesState &actual,
                     const amr::SystemResourcesState &expected)
    {
        assert(actual.quality == expected.quality);
        assert(actual.received_at == expected.received_at);
        assert(actual.reading.has_value() == expected.reading.has_value());
        if (expected.reading.has_value())
        {
            const auto &a = actual.reading.value();
            const auto &b = expected.reading.value();
            assert(a.cpu_usage_percent == b.cpu_usage_percent);
            assert(a.memory_used_bytes == b.memory_used_bytes);
            assert(a.memory_total_bytes == b.memory_total_bytes);
            assert(a.cpu_temperature_c == b.cpu_temperature_c);
        }
    }
}

int main()
{
    using Clock = std::chrono::steady_clock;
    using Ms = std::chrono::milliseconds;
    using Update = amr::SystemResourcesUpdateResult;
    using Refresh = amr::SystemResourcesRefreshResult;
    using Quality = amr::DataQuality;

    const Clock::time_point start{};
    const Ms timeout{1000};
    amr::RobotState robot{};
    auto &state = robot.system_resource;
    assert(!state.reading.has_value());
    assert(!state.received_at.has_value());
    assert(state.quality == Quality::Unknown);
    const auto empty = state;
    assert(amr::refresh_system_resources_quality(state, start, timeout) == Refresh::NoData);
    assert_same(state, empty);

    const amr::SystemResourcesReading first{25.0, 2147483648ULL, 8589934592ULL, 52.5};
    assert(amr::update_system_resources_reading(state, first, start) == Update::Update);
    assert(state.reading->cpu_usage_percent == 25.0);
    assert(state.reading->memory_used_bytes == 2147483648ULL);
    assert(state.reading->memory_total_bytes == 8589934592ULL);
    assert(state.reading->cpu_temperature_c == 52.5);
    assert(state.received_at == start);
    assert(state.quality == Quality::Valid);
    const auto saved = state;

    assert(amr::refresh_system_resources_quality(state, start + Ms{999}, timeout) == Refresh::Fresh);
    assert_same(state, saved);
    assert(amr::refresh_system_resources_quality(state, start + Ms{1000}, timeout) == Refresh::Stale);
    auto stale = saved;
    stale.quality = Quality::Stale;
    assert_same(state, stale);

    // CPU百分比和内存用量可为零；未提供温度仍是一份有效资源样本。
    const amr::SystemResourcesReading zero{0.0, 0, 8589934592ULL, std::nullopt};
    assert(amr::update_system_resources_reading(state, zero, start + Ms{1100}) == Update::Update);
    assert(state.quality == Quality::Valid);
    assert(state.reading->cpu_usage_percent == 0.0);
    assert(state.reading->memory_used_bytes == 0);
    assert(!state.reading->cpu_temperature_c.has_value());
    const auto without_temperature = state;
    assert(amr::refresh_system_resources_quality(state, start + Ms{1101}, timeout) == Refresh::Fresh);
    assert_same(state, without_temperature);

    // 100%和全部内存已用是合法边界；0摄氏度有值。
    const amr::SystemResourcesReading full{100.0, 8589934592ULL, 8589934592ULL, 0.0};
    assert(amr::update_system_resources_reading(state, full, start) == Update::Update);
    assert(state.reading->cpu_temperature_c.has_value());
    assert(state.reading->cpu_temperature_c.value() == 0.0);
    auto cold = full;
    cold.cpu_temperature_c = -5.0;
    assert(amr::update_system_resources_reading(state, cold, start) == Update::Update);
    assert(state.reading->cpu_temperature_c == -5.0);

    // 核心只比较字节值，不通过相加或百分比计算引入整数溢出。
    const auto max_bytes = std::numeric_limits<std::uint64_t>::max();
    const amr::SystemResourcesReading large{50.0, max_bytes, max_bytes, std::nullopt};
    assert(amr::update_system_resources_reading(state, large, start) == Update::Update);
    assert(state.reading->memory_total_bytes == max_bytes);

    const std::array<double, 5> bad_cpu{
        -0.1, 100.1, std::numeric_limits<double>::quiet_NaN(),
        std::numeric_limits<double>::infinity(), -std::numeric_limits<double>::infinity()};
    state.quality = Quality::Stale;
    const auto before_invalid = state;
    for (const auto value : bad_cpu)
    {
        auto bad = first;
        bad.cpu_usage_percent = value;
        assert(amr::update_system_resources_reading(state, bad, start + Ms{5000}) == Update::InvalidCpuUsage);
        assert_same(state, before_invalid);
    }
    for (const auto total : {std::uint64_t{0}, std::uint64_t{1024}})
    {
        auto bad = first;
        bad.memory_total_bytes = total;
        assert(amr::update_system_resources_reading(state, bad, start + Ms{5000}) == Update::InvalidMemoryUsage);
        assert_same(state, before_invalid);
    }
    for (const auto value : {std::numeric_limits<double>::quiet_NaN(),
                             std::numeric_limits<double>::infinity(),
                             -std::numeric_limits<double>::infinity()})
    {
        auto bad = first;
        bad.cpu_temperature_c = value;
        assert(amr::update_system_resources_reading(state, bad, start + Ms{5000}) == Update::InvalidTemperature);
        assert_same(state, before_invalid);
    }

    // 同时有多个错误时按CPU、内存、温度顺序报告；错误样本不刷新接收时间。
    auto bad = first;
    bad.cpu_usage_percent = -1.0;
    bad.memory_total_bytes = 0;
    bad.cpu_temperature_c = std::numeric_limits<double>::infinity();
    assert(amr::update_system_resources_reading(state, bad, start) == Update::InvalidCpuUsage);
    assert_same(state, before_invalid);
    bad.cpu_usage_percent = 25.0;
    assert(amr::update_system_resources_reading(state, bad, start) == Update::InvalidMemoryUsage);
    assert_same(state, before_invalid);
    bad.memory_total_bytes = 8589934592ULL;
    assert(amr::update_system_resources_reading(state, bad, start) == Update::InvalidTemperature);
    assert_same(state, before_invalid);
    amr::SystemResourcesState empty_invalid{};
    assert(amr::update_system_resources_reading(empty_invalid, amr::SystemResourcesReading{}, start) == Update::InvalidMemoryUsage);
    assert_same(empty_invalid, empty);

    // 时效错误保留样本和时间；参数恢复后可以重新判为新鲜。
    assert(amr::refresh_system_resources_quality(state, start - Ms{1}, Ms{0}) == Refresh::InvalidTimeout);
    auto unknown = before_invalid;
    unknown.quality = Quality::Unknown;
    assert_same(state, unknown);
    assert(amr::refresh_system_resources_quality(state, start, Ms{-1}) == Refresh::InvalidTimeout);
    assert(amr::refresh_system_resources_quality(state, start - Ms{1}, timeout) == Refresh::InvalidTimeOrder);
    assert_same(state, unknown);
    assert(amr::refresh_system_resources_quality(state, start, timeout) == Refresh::Fresh);
    auto fresh = before_invalid;
    fresh.quality = Quality::Valid;
    assert_same(state, fresh);

    amr::SystemResourcesState missing_time{};
    missing_time.reading = first;
    missing_time.quality = Quality::Valid;
    assert(amr::refresh_system_resources_quality(missing_time, start, Ms{0}) == Refresh::NoData);
    assert(missing_time.quality == Quality::Unknown);
    assert(missing_time.reading->cpu_usage_percent == first.cpu_usage_percent);
    assert(!missing_time.received_at.has_value());
    amr::SystemResourcesState missing_reading{};
    missing_reading.received_at = start;
    missing_reading.quality = Quality::Stale;
    assert(amr::refresh_system_resources_quality(missing_reading, start, timeout) == Refresh::NoData);
    assert(missing_reading.quality == Quality::Unknown);
    assert(!missing_reading.reading.has_value());
    assert(missing_reading.received_at == start);

    for (const auto quality : {Quality::Fault, Quality::Unsupported})
    {
        auto preserved = saved;
        preserved.quality = quality;
        state = preserved;
        assert(amr::refresh_system_resources_quality(state, start - Ms{1}, Ms{0}) == Refresh::Preserved);
        assert_same(state, preserved);
        auto invalid = first;
        invalid.cpu_usage_percent = 101.0;
        assert(amr::update_system_resources_reading(state, invalid, start + Ms{1}) == Update::InvalidCpuUsage);
        assert_same(state, preserved);
        assert(amr::update_system_resources_reading(state, first, start + Ms{1}) == Update::Update);
        assert(state.quality == Quality::Valid);
        assert(state.received_at == start + Ms{1});
        amr::SystemResourcesState missing{};
        missing.quality = quality;
        const auto original = missing;
        assert(amr::refresh_system_resources_quality(missing, start, Ms{0}) == Refresh::Preserved);
        assert_same(missing, original);
    }

    std::cout << "system resources test passed\n";
}
