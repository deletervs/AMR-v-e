#include "amr/data_freshness.hpp"
#include <cassert>
#include <chrono>
#include <iostream>

/*
本文件测试data_freshness数据新鲜度逻辑

*/

int main()
{
    using Clock = std::chrono::steady_clock;
    using Ms = std::chrono::milliseconds;
    using Result = amr::FreshnessResult;

    const Clock::time_point received_at{};
    const Ms timeout{2000};

    //case 1: 刚接收数据时, 未到上限
    assert(amr::check_freshness(received_at, received_at, timeout) == Result::Fresh);
    assert(amr::check_freshness(received_at, received_at + Ms{1999}, timeout) == Result::Fresh);

    //case 2: 刚好到/超过上限, 不行
    assert(amr::check_freshness(received_at, received_at + Ms{2000}, timeout) == Result::Stale);
    assert(amr::check_freshness(received_at, received_at + Ms{2001}, timeout) == Result::Stale);

    //case 3:  参数设置不合理, timeout <= 0 或 now < received_at
    assert(amr::check_freshness(received_at, received_at, Ms{0}) == Result::InvalidTimeout);
    assert(amr::check_freshness(received_at, received_at, Ms{-1}) == Result::InvalidTimeout);
    assert(amr::check_freshness(received_at, received_at - Ms{1}, timeout) == Result::InvalidTimeOrder);

    std::cout << "data freshness test passed\n";

    return 0;
}

/*
Note: 
1. using 是对类型起别称, 方便

*/