#include "amr/input_mapping.hpp"
#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>
int main()
{
    auto check = [](bool ok, const char *name)
    {if(!ok)throw std::runtime_error(name); };
    using namespace amr;
    VelocityTarget t{9, 9};
    check(map_virtual_input(true, false, true, false, true, .25, .6, t) == InputMapResult::Mapped && std::abs(t.linear_mps - .25) < 1e-12 && std::abs(t.angular_radps - .6) < 1e-12, "forward left");
    map_virtual_input(false, true, false, true, true, .25, .6, t);
    check(t.linear_mps == -.25 && t.angular_radps == -.6, "reverse right");
    map_virtual_input(true, true, true, true, true, .25, .6, t);
    check(t.linear_mps == 0 && t.angular_radps == 0, "opposites cancel");
    map_virtual_input(true, false, true, false, false, .25, .6, t);
    check(t.linear_mps == 0 && t.angular_radps == 0, "deadman release");
    check(map_virtual_input(0, 0, 0, 0, 0, 0, .6, t) == InputMapResult::InvalidLimits && t.linear_mps == 0 && t.angular_radps == 0, "zero limit");
    check(map_virtual_input(0, 0, 0, 0, 0, .25, -.6, t) == InputMapResult::InvalidLimits, "negative limit");
    check(map_virtual_input(0, 0, 0, 0, 0, std::numeric_limits<double>::quiet_NaN(), .6, t) == InputMapResult::InvalidLimits, "nan limit");
    check(map_gamepad_input(0, -1, true, .25, .6, .15, t) == InputMapResult::Mapped && t.linear_mps == .25 && t.angular_radps == 0, "forward axis");
    map_gamepad_input(1, 1, true, .25, .6, .15, t);
    check(t.linear_mps == -.25 && t.angular_radps == -.6, "reverse right axes");
    map_gamepad_input(-1, -1, true, .25, .6, .15, t);
    check(t.linear_mps == .25 && t.angular_radps == .6, "forward left axes");
    map_gamepad_input(.15, -.15, true, .25, .6, .15, t);
    check(t.linear_mps == 0 && t.angular_radps == 0, "deadzone equality");
    map_gamepad_input(.575, -.575, true, .25, .6, .15, t);
    check(std::abs(t.linear_mps - .125) < 1e-12 && std::abs(t.angular_radps + .3) < 1e-12, "rescaled half");
    map_gamepad_input(1, -1, false, .25, .6, .15, t);
    check(t.linear_mps == 0 && t.angular_radps == 0, "pad released");
    check(map_gamepad_input(1.01, 0, true, .25, .6, .15, t) == InputMapResult::InvalidAxis && t.linear_mps == 0 && t.angular_radps == 0, "out of range");
    check(map_gamepad_input(0, std::numeric_limits<double>::quiet_NaN(), true, .25, .6, .15, t) == InputMapResult::InvalidAxis, "nan axis");
    check(map_gamepad_input(std::numeric_limits<double>::infinity(), 0, true, .25, .6, .15, t) == InputMapResult::InvalidAxis, "infinite axis");
    check(map_gamepad_input(0, 0, true, .25, .6, 1, t) == InputMapResult::InvalidDeadzone, "deadzone one");
    check(map_gamepad_input(0, 0, true, .25, .6, -.1, t) == InputMapResult::InvalidDeadzone, "negative deadzone");
    check(map_gamepad_input(0, 0, true, .25, .6, std::numeric_limits<double>::quiet_NaN(), t) == InputMapResult::InvalidDeadzone, "nan deadzone");
    check(map_gamepad_input(0, 0, true, std::numeric_limits<double>::infinity(), .6, .15, t) == InputMapResult::InvalidLimits, "pad bad limit");
    map_gamepad_input(.5, -.5, true, .25, .6, 0, t);
    check(t.linear_mps == .125 && t.angular_radps == -.3, "zero deadzone");
    map_virtual_input(true, true, true, false, true, .25, .6, t);
    check(t.linear_mps == 0 && t.angular_radps == .6, "independent linear cancellation");
    map_virtual_input(true, false, true, true, true, .25, .6, t);
    check(t.linear_mps == .25 && t.angular_radps == 0, "independent turn cancellation");
    check(map_gamepad_input(0, std::numeric_limits<double>::quiet_NaN(), false, .25, .6, .15, t) == InputMapResult::InvalidAxis && t.linear_mps == 0 && t.angular_radps == 0, "invalid released axis");
    check(map_gamepad_input(0, 0, true, .25, 0, .15, t) == InputMapResult::InvalidLimits, "zero angular limit");
    check(map_virtual_input(false, false, false, false, false, .25, std::numeric_limits<double>::infinity(), t) == InputMapResult::InvalidLimits, "infinite angular limit");

    std::cout << "input mapping cases passed\n";
}
