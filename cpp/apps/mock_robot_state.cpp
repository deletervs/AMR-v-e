#include "amr/battery_state.hpp"
#include "amr/robot_state.hpp"

#include <chrono>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>

/*
本文件为应用程序入口, 调用 /src 中规则, 组成程序.
包含如下部份:
--namespace
1. quality_name 把枚举转换为文字
2. print_state_json 输出状态
3. make_stream_state 根据当前模拟时间构造一份状态
4. run_stream 在真实模式读取 steady_clock 经过时间并等待发布间隔，在快速模式直接递增虚拟时间

--func
1. run_scenario 演示四个场景、run_stream 持续/快速输出、main 选择运行模式

*/

namespace //匿名命名空间, 内部函数只供本文件使用
{
    const char *quality_name(amr::DataQuality quality)
    {
        switch (quality)
        {
        case amr::DataQuality::Unknown:
            return "Unknown";
        case amr::DataQuality::Valid:
            return "Valid";
        case amr::DataQuality::Stale:
            return "Stale";
        case amr::DataQuality::Fault:
            return "Fault";
        case amr::DataQuality::Unsupported:
            return "Unsupported";
        }

        throw std::logic_error("Unhandled DataQuality");
    }

    void print_state_json( //该函数要生成 json, 所以 cout 遵循 json 规则
        const amr::RobotState &state,
        std::chrono::milliseconds simulated_time)
    {
        std::cout << std::boolalpha
                  << "{\"source\":\"mock\",\"sim_time_ms\":"
                  << simulated_time.count()
                  << ",\"connected\":" << state.connected
                  << ",\"motion_enabled\":" << state.motion_enabled
                  << ",\"battery\":{\"voltage_v\":";
        if (state.battery.voltage_v.has_value())
        {
            std::cout << state.battery.voltage_v.value();
        }
        else
        {
            std::cout << "null";
        }

        std::cout << ",\"quality\":\""
                  << quality_name(state.battery.quality)
                  << "\"}}\n";
    }

    amr::RobotState make_stream_state(std::chrono::milliseconds elapsed)
    { //该函数根据经过时间生成模拟状态
        using Clock = std::chrono::steady_clock;
        using Ms = std::chrono::milliseconds;

        amr::RobotState state{};
        const auto phase_ms = elapsed.count() % 6000;

        // 每六秒重新演示一次，前一秒没有读数。
        if (phase_ms < 1000)
        {
            return state;
        }

        state.connected = true;
        const auto now = Clock::time_point{} + elapsed;

        if (phase_ms < 4000)
        {
            const auto received_at = now - Ms{phase_ms} + Ms{1000};
            amr::update_battery_reading(state.battery, 2.5, received_at);
        }
        else
        {
            // 恢复后，每次发布都模拟收到一份新读数。
            amr::update_battery_reading(state.battery, 22.2, now);
        }

        amr::refresh_battery_quality(state.battery, now, Ms{1000});
        return state;
    }

    int run_stream(bool fast) // run_stream 在真实模式读取 steady_clock 经过时间并等待发布间隔，在快速模式直接递增虚拟时间。

    {
        using Clock = std::chrono::steady_clock;
        using Ms = std::chrono::milliseconds;

        const auto started_at = Clock::now();
        const Ms period{500};
        int frame{0};

        while (!fast || frame < 17)
        {
            const auto elapsed = fast
                                     ? Ms{frame * period.count()}
                                     : std::chrono::duration_cast<Ms>(Clock::now() - started_at);

            const auto state = make_stream_state(elapsed);
            print_state_json(state, elapsed);
            std::cout.flush();

            if (!std::cout)
            {
                return 1;
            }

            if (fast)
            {
                ++frame;
            }
            else
            {
                std::this_thread::sleep_for(period);
            }
        }

        return 0;
    }
} // namespace

int run_scenario()
{
    using Clock = std::chrono::steady_clock;
    using Milliseconds = std::chrono::milliseconds;

    amr::RobotState state{};
    const Milliseconds timeout{1000};
    const Clock::time_point start{};

    // 启动, 尚未收到读数
    print_state_json(state, Milliseconds{0});

    // 100ms链接成功, 并收到读数
    state.connected = true;
    amr::update_battery_reading(
        state.battery, 2.5, start + Milliseconds{100});
    print_state_json(state, Milliseconds{100});

    // 到 1100 ms 时，读数年龄恰好达到超时阈值
    amr::refresh_battery_quality(
        state.battery, start + Milliseconds(1100), timeout);
    print_state_json(state, Milliseconds{1100});

    // 在 1200 ms 收到新读数，恢复有效状态
    state.connected = true;
    amr::update_battery_reading(
        state.battery, 22.2, start + Milliseconds{1200});
    print_state_json(state, Milliseconds{1200});

    return 0;
}

int main(int argc, char *argv[])
{
    if (argc == 1)
    {
        return run_scenario();
    }

    if (argc == 2)
    {
        const std::string mode{argv[1]};

        if (mode == "--stream")
        {
            return run_stream(false);
        }

        if (mode == "--stream-test")
        {
            return run_stream(true);
        }
    }

    std::cerr << "Usage: mock_robot_state [--stream|--stream-test]\n";
    return 2;
}