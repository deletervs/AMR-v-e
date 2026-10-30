#include "amr/input_mapping.hpp"
#include <cmath>
namespace amr
{
    namespace
    {
        bool limits_valid(double linear, double angular)
        {
            return std::isfinite(linear) && linear > 0 && std::isfinite(angular) && angular > 0;
        }
        double rescale(double axis, double deadzone)
        {
            const double magnitude = std::abs(axis);
            if (magnitude <= deadzone)
                return 0;
            return std::copysign((magnitude - deadzone) / (1 - deadzone), axis);
        }
    } // namespace
    InputMapResult map_virtual_input(bool forward, bool backward, bool left, bool right, bool deadman,
                                     double linear_limit, double angular_limit, VelocityTarget &target)
    {
        target = {};
        if (!limits_valid(linear_limit, angular_limit))
            return InputMapResult::InvalidLimits;
        if (deadman)
            target = {(static_cast<int>(forward) - static_cast<int>(backward)) * linear_limit,
                      (static_cast<int>(left) - static_cast<int>(right)) * angular_limit};
        return InputMapResult::Mapped;
    }
    InputMapResult map_gamepad_input(double x, double y, bool deadman, double linear_limit,
                                     double angular_limit, double deadzone, VelocityTarget &target)
    {
        target = {};
        if (!limits_valid(linear_limit, angular_limit))
            return InputMapResult::InvalidLimits;
        if (!std::isfinite(deadzone) || deadzone < 0 || deadzone >= 1)
            return InputMapResult::InvalidDeadzone;
        if (!std::isfinite(x) || !std::isfinite(y) || std::abs(x) > 1 || std::abs(y) > 1)
            return InputMapResult::InvalidAxis;
        if (deadman)
            target = {-rescale(y, deadzone) * linear_limit, -rescale(x, deadzone) * angular_limit};
        return InputMapResult::Mapped;
    }
} // namespace amr
