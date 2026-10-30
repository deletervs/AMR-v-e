// Convert keyboard/pointer buttons and standard gamepad axes to SI velocity targets.
#pragma once
#include "amr/motion_guard.hpp"
namespace amr
{
    enum class InputMapResult
    {
        Mapped,
        InvalidLimits,
        InvalidAxis,
        InvalidDeadzone
    };
    InputMapResult map_virtual_input(bool forward, bool backward, bool left, bool right,
                                     bool deadman, double linear_limit, double angular_limit, VelocityTarget &target);
    // Standard left stick: +x right, +y backwards. Positive angular velocity turns left.
    InputMapResult map_gamepad_input(double axis_x, double axis_y, bool deadman,
                                     double linear_limit, double angular_limit, double deadzone, VelocityTarget &target);
} // namespace amr
