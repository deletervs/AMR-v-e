#pragma once
#include <chrono> //标准库里处理时间点和时长的工具

/*
本文件定义信息的实时性信息
*/

namespace amr
{
    enum class FreshnessResult //规则判断函数用, 说明返回值产生的原因
    {
        Fresh,           //时间差 < 上限
        Stale,           //时间差 >= 上限
        InvalidTimeout,  //timeout 设置不合理
        InvalidTimeOrder //now < received_at
    };

    FreshnessResult check_freshness(
        std::chrono::steady_clock::time_point received_at, //收到信息的时刻
        std::chrono::steady_clock::time_point now,         //现在的时刻
        std::chrono::milliseconds timeout);                //允许的更新延迟时长(ms)
} // namespace amr
