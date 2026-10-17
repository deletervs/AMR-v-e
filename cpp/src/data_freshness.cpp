#include "amr/data_freshness.hpp"

/*
本文件实现信息实时性的规则 逻辑判断

*/

namespace amr
{
    FreshnessResult check_freshness(
        std::chrono::steady_clock::time_point received_at,
        std::chrono::steady_clock::time_point now,
        std::chrono::milliseconds timeout) //函数形参
    {
        if (timeout <= std::chrono::milliseconds::zero()) //延迟规定为0ms及以下时, 参数不合理
        {
            return FreshnessResult::InvalidTimeout;
        }

        if (now < received_at) //逻辑不合理情况
        {
            return FreshnessResult::InvalidTimeOrder;
        }

        if ((now - received_at) >= timeout)
        { //正常情况: 时间差 >= 规定的延迟, 达到等待上限
            return FreshnessResult::Stale;
        }

        return FreshnessResult::Fresh;
    }
} // namespace amr
