#include "amr/linux_resources.hpp"
#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <vector>

int main()
{
    using namespace amr;
    auto check = [](bool result, const char *name) { if (!result) throw std::runtime_error(name); };

CpuTicks cpu{77,66};
check(parse_cpu_line("cpu  10 2 3 40 5 6 7 8 99 100",cpu),"parse cpu");
check(cpu.total==81 && cpu.idle==45,"exclude guest counters");
double percent=-1;
check(cpu_usage({100,60},{200,120},percent) && std::abs(percent-40.0)<1e-12,"cpu delta");
check(!cpu_usage({100,60},{100,60},percent) && percent==40,"zero interval preserves output");
check(!cpu_usage({100,60},{90,50},percent),"counter rollback");
check(!cpu_usage({100,60},{200,50},percent),"idle rollback");
check(!cpu_usage({100,60},{110,80},percent),"idle greater than delta");
check(!cpu_usage({1,2},{100,10},percent),"bad before idle");
check(!cpu_usage({1,0},{100,101},percent),"bad after idle");
for(const auto& bad: std::vector<std::string>{"cpu0 1 2 3 4 5 6 7 8","cpu 1 2 3 4 5 6 7","cpu -1 2 3 4 5 6 7 8","cpu 1x 2 3 4 5 6 7 8","cpu 18446744073709551616 0 0 0 0 0 0 0","cpu 18446744073709551615 1 0 0 0 0 0 0","cpu 1 2 3 4 5 6 7 8 0 garbage"}){
 CpuTicks saved{77,66}; check(!parse_cpu_line(bad,saved),"invalid cpu"); check(saved.total==77&&saved.idle==66,"preserve cpu");
}
MemoryBytes memory{77,66};
check(parse_memory_info("MemTotal: 1000 kB\nMemFree: 40 kB\nMemAvailable: 250 kB\n",memory),"parse memory");
check(memory.total==1024000 && memory.used==768000,"memory units");
check(parse_memory_info("MemAvailable:\t0 kB\nMemTotal: 1 kB\n",memory) && memory.used==1024 && memory.total==1024,"zero available and reversed order");
for(const auto& bad:std::vector<std::string>{"MemTotal: 1000 kB\n","MemTotal: 0 kB\nMemAvailable: 0 kB\n","MemTotal: 100 kB\nMemAvailable: 101 kB\n","MemTotal: -1 kB\nMemAvailable: 0 kB\n","MemTotal: 1 MB\nMemAvailable: 0 kB\n","MemTotal: 18446744073709551615 kB\nMemAvailable: 0 kB\n","MemTotal: 1 kB\nMemTotal: 2 kB\nMemAvailable: 0 kB\n","MemTotal: 1x kB\nMemAvailable: 0 kB\n","MemTotal: 1 kB garbage\nMemAvailable: 0 kB\n"}){
 MemoryBytes saved{77,66}; check(!parse_memory_info(bad,saved),"invalid memory"); check(saved.used==77&&saved.total==66,"preserve memory");
}

    check(parse_cpu_line("\t cpu 1 2 3 4 5 6 7 8\r",cpu) && cpu.total==36 && cpu.idle==9,"eight counters");
    check(parse_cpu_line("cpu 1 2 3 4 5 6 7 8 9",cpu) && cpu.total==36,"nine counters");
    check(!parse_cpu_line("cpu +1 0 0 0 0 0 0 0",cpu),"positive sign");
    check(!parse_cpu_line("cpu -0 0 0 0 0 0 0 0",cpu),"negative zero");
    check(parse_memory_info("MemTotal:\t1 kB\r\nMemAvailable: 0 kB",memory),"CRLF and final line");
    check(!parse_memory_info("MemTotal: 1 kB\nMemAvailable: +0 kB",memory),"memory sign");
    check(!parse_memory_info("MemTotal: 1 kB\nMemAvailable: 0 kB\nMemAvailable: 0 kB",memory),"duplicate available");
    check(cpu_usage({0,0},{std::numeric_limits<std::uint64_t>::max(),0},percent) && percent==100,"large ticks");
    std::cout << "Linux resource parsing checks passed\n";
}
