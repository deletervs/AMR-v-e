#pragma once // 同一个.cpp编译时，本文件多次引用时只处理一次
#include <chrono>
#include <optional> //标准库，>= C++17；提供“可能有值”的类型模板

/*
本文件定义机器人amr的类型:
1. 数据质量
2. 电池状态
3. 机器人状态 (major)
4. 里程计

*/
namespace amr
{
    enum class DataQuality // 定义数据质量，明确数据的可用性状态，方便检查；enum class 作用域枚举
    {
        Unknown,
        Valid,
        Stale,
        Fault,
        Unsupported
    };

    struct BatteryState // 电池状态
    {
        std::optional<double> voltage_v{std::nullopt}; // 电压默认没有值，设0.0的话有表示存在0电压的歧义
        DataQuality quality{DataQuality::Unknown};
        std::optional<std::chrono::steady_clock::time_point> received_at{std::nullopt}; //是否接受到电压读数
    };

        struct OdometryReading
    {
        double x_m{0.0};
        double y_m{0.0};
        double yaw_rad{0.0};
        double linear_x_mps{0.0};
        double linear_y_mps{0.0};
        double angular_z_radps{0.0};
    };

    struct OdometryState
    {
        std::optional<OdometryReading> reading{std::nullopt};
        DataQuality quality{DataQuality::Unknown};
        std::optional<std::chrono::steady_clock::time_point> received_at{std::nullopt};
    };

    struct RobotState
    {
        bool connected{false}; //初始化一般用{}
        bool motion_enabled{false};
        BatteryState battery{};
        OdometryState odometry{};
    };

} // namespace amr

/*
Note:
1. enum class 是作用域枚举，放属性的选项，struct 结构体放各种属性
2. struct 和 class 用法习惯：struct 放简单的数据，class 管理行为和规则
3. A.a 是实例化对象之后访问对象成员的，A::a 是限定命名空间或类型中的名称
4. 初始化一般用{}统一风格，发生窄化转换时也能编译报错
*/
