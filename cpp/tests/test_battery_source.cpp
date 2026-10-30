#include "amr/battery_source.hpp"
#include "amr/battery_state.hpp"
#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>
int main()
{
    using namespace amr;
    auto check = [](bool value, const char *name)
    {if(!value) throw std::runtime_error(name); };
    using R = BatterySampleResult;
    BatterySampleGate gate;
    check(gate.observe({0, 0, 0}) == R::InvalidSourceTime, "initial source zero");
    check(gate.observe({11.8, 100, 1}) == R::Accepted, "first sample");
    check(gate.observe({20, 100, 2}) == R::Duplicate, "same MCU time cannot renew voltage");
    check(gate.observe({11.7, 101, 3}) == R::Accepted, "advancing sample");
    check(gate.observe({11.6, 102, 3}) == R::Duplicate, "same stamp cannot renew");
    check(gate.observe({11.6, 102, 4}) == R::Accepted, "new stamp accepts");
    check(gate.observe({11.5, 103, 2}) == R::AcceptedWithClockAdjustment, "ROS wall clock backwards but MCU advances");
    check(gate.observe({0, 104, 5}) == R::Accepted, "real zero voltage with source time is legitimate");
    check(gate.observe({11.5, 10, 6}) == R::ResetPending, "counter rollback waits");
    check(gate.observe({11.5, 10, 7}) == R::Duplicate, "same reset sample waits");
    check(gate.observe({11.5, 11, 8}) == R::AcceptedAfterReset, "second advancing lower sample rebases");
    check(gate.observe({11.5, 12, 9}) == R::Accepted, "normal after reset");
    check(gate.observe({11.5, 5, 10}) == R::ResetPending, "another reset");
    check(gate.observe({11.5, 4, 11}) == R::ResetPending, "lower reset candidate replaces prior");
    check(gate.observe({11.5, 5, 12}) == R::AcceptedAfterReset, "new candidate confirms");
    BatterySampleGate invalid;
    check(invalid.observe({11, 100, 100}) == R::Accepted, "baseline");
    check(invalid.observe({-1, 101, 200}) == R::InvalidVoltage, "negative voltage");
    check(invalid.observe({std::numeric_limits<double>::quiet_NaN(), 101, 200}) == R::InvalidVoltage, "nan voltage");
    check(invalid.observe({std::numeric_limits<double>::infinity(), 101, 200}) == R::InvalidVoltage, "inf voltage");
    check(invalid.observe({11, -1, 200}) == R::InvalidSourceTime, "negative MCU time");
    check(invalid.observe({11, std::numeric_limits<double>::quiet_NaN(), 200}) == R::InvalidSourceTime, "nan source time");
    check(invalid.observe({11, std::numeric_limits<double>::infinity(), 200}) == R::InvalidSourceTime, "inf source time");
    check(invalid.observe({11, 101, 0}) == R::InvalidStamp, "zero stamp");
    check(invalid.observe({11, 101, 101}) == R::Accepted, "invalid attempts preserve stamp and source baseline");
    BatterySampleGate interrupted;
    check(interrupted.observe({11, 100, 1}) == R::Accepted, "reset interruption baseline");
    check(interrupted.observe({11, 10, 2}) == R::ResetPending, "reset candidate");
    check(interrupted.observe({11, 100, 3}) == R::Duplicate, "old accepted time cancels candidate");
    check(interrupted.observe({11, 11, 4}) == R::ResetPending, "cancelled candidate cannot confirm");
    check(interrupted.observe({11, 12, 5}) == R::AcceptedAfterReset, "new reset evidence");
    BatterySampleGate bypass;
    check(bypass.observe({11, 100, 1}) == R::Accepted, "bypass baseline");
    check(bypass.observe({11, 10, 2}) == R::ResetPending, "bypass candidate");
    check(bypass.observe({11, 101, 3}) == R::Accepted, "old source resumes -> normal advance");

    check(battery_stamp_recent(10, 20, 11), "recent stamp");
    check(!battery_stamp_recent(10, 20, 10), "stamp at age limit rejected");
    check(!battery_stamp_recent(0, 20, 100), "absent stamp");
    check(!battery_stamp_recent(21, 20, 100), "future stamp");
    check(!battery_stamp_recent(10, 20, 0), "zero age limit");
    BatterySampleGate integration;
    BatteryState state;
    using Clock = std::chrono::steady_clock;
    using Ms = std::chrono::milliseconds;
    const auto start = Clock::time_point{};
    auto feed = [&](BatterySample sample, int elapsed)
    {
        auto result = integration.observe(sample);
        if (battery_sample_accepted(result))
            update_battery_reading(state, sample.voltage_v, start + Ms{elapsed});
        return result;
    };
    feed({0, 0, 0}, 0);
    check(!state.voltage_v && !state.received_at, "initial sample not installed");
    feed({11.8, 100, 1}, 10);
    check(state.quality == DataQuality::Valid && state.received_at == start + Ms{10}, "new sample installed");
    feed({99, 100, 2}, 200);
    check(state.voltage_v == 11.8 && state.received_at == start + Ms{10}, "duplicate preserves voltage and received_at");
    refresh_battery_quality(state, start + Ms{310}, Ms{300});
    check(state.quality == DataQuality::Stale, "duplicate did not renew freshness");
    feed({-1, 101, 3}, 311);
    check(state.quality == DataQuality::Stale && state.received_at == start + Ms{10}, "invalid preserves old sample");
    feed({11.7, 101, 4}, 312);
    check(state.quality == DataQuality::Valid && state.received_at == start + Ms{312}, "new sample recovers");
    feed({11.6, 1, 5}, 400);
    check(state.voltage_v == 11.7 && state.received_at == start + Ms{312}, "reset pending preserves old data");
    feed({11.6, 2, 6}, 500);
    check(state.voltage_v == 11.6 && state.received_at == start + Ms{500}, "reset confirms new data");
    std::cout << "Battery source gate and freshness checks passed\n";
}
