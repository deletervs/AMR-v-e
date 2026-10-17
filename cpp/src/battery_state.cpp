#include "amr/battery_state.hpp"
#include "amr/data_freshness.hpp" //引用 check_freshness 函数判断新鲜度
#include <stdexcept>              //引用 logic_error 报告内部逻辑错误

// 本文件实现 battery_state.hpp 中函数, 收到电压信息后, 保存电压 时间 更新 quality

namespace amr
{
    void update_battery_reading(
        BatteryState &battery, //
        double voltage_v,
        std::chrono::steady_clock::time_point received_at)
    {
        battery.voltage_v = voltage_v;
        battery.received_at = received_at;
        battery.quality = DataQuality::Valid;
    }

    BatteryRefreshResult refresh_battery_quality(
        BatteryState &battery,
        std::chrono::steady_clock::time_point now,
        std::chrono::milliseconds timeout)
    {
        // 电池状态为 fault 和 unspported, 新鲜度返回为 preserved
        if (battery.quality == DataQuality::Fault ||
            battery.quality == DataQuality::Unsupported)
        {
            return BatteryRefreshResult::Preserved;
        }

        //检测电压和接收时间是否存在, 不存在则更新电池状态为 unknown, 新鲜度返回nodata
        if (!battery.voltage_v.has_value() ||
            !battery.received_at.has_value())
        {
            battery.quality = DataQuality::Unknown;
            return BatteryRefreshResult::NoData;
        }

        // 通过 check_freshness 判断接收到的信息新鲜度, 返回电池新鲜度
        const auto result = check_freshness(
            battery.received_at.value(), now, timeout);

        switch (result)
        {
        case FreshnessResult::Fresh:
            battery.quality = DataQuality::Valid;
            return BatteryRefreshResult::Fresh;

        case FreshnessResult::Stale:
            battery.quality = DataQuality::Stale;
            return BatteryRefreshResult::Stale;

        case FreshnessResult::InvalidTimeout:
            battery.quality = DataQuality::Unknown;
            return BatteryRefreshResult::InvalidTimeout;

        case FreshnessResult::InvalidTimeOrder:
            battery.quality = DataQuality::Unknown;
            return BatteryRefreshResult::InvalidTimeOrder;
        }

        throw std::logic_error("Unhandled FreshnessResult");
    }
} // namespace amr
