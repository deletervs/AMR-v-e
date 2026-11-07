#include "amr/single_executor.hpp"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <utility>
namespace amr {
SingleExecutor::SingleExecutor(std::uint64_t boot,MotionLimits limits,Clock clock,
    std::unique_ptr<VelocitySink> sink,bool enabled):
    boot_{boot},limits_{limits},clock_{std::move(clock)},sink_{std::move(sink)},enabled_{enabled} {
    if(!boot || !clock_ || !std::isfinite(limits.linear_mps) || limits.linear_mps<=0 ||
        !std::isfinite(limits.angular_radps) || limits.angular_radps<=0 ||
        !limits.command_timeout_ms || limits.lease_ms<limits.command_timeout_ms)
        throw std::invalid_argument("Invalid executor config");
    if(enabled_ && !sink_) throw std::invalid_argument("Explicit sole sink required");
    status_.state=enabled_?ExecutorState::Waiting:ExecutorState::Disabled;
    status_.output_enabled=enabled_;
    if(enabled_) worker_=std::thread(&SingleExecutor::run,this);
}
SingleExecutor::~SingleExecutor(){close();}
void SingleExecutor::offer(const MotionSnapshot &s,std::uint64_t now) {
    std::lock_guard<std::mutex> lock{mutex_};
    if(closing_ || !enabled_) return;
    if(s.boot_id!=boot_ || (have_ && now<observed_)) fault_=true;
    have_=true;observed_=now;target_={};deadline_=now;
    if(fault_) return;
    if(!s.armed || !s.link_available || s.stop_latched || !s.neutral_confirmed ||
        s.reason!=StopReason::None || !s.session_id || !s.controller_id ||
        (s.owner!=ControlSource::Keyboard && s.owner!=ControlSource::Gamepad)) return;
    if(!std::isfinite(s.target.linear_mps) || !std::isfinite(s.target.angular_radps) ||
        std::abs(s.target.linear_mps)>limits_.linear_mps ||
        std::abs(s.target.angular_radps)>limits_.angular_radps ||
        s.command_remaining_ms>limits_.command_timeout_ms || s.lease_remaining_ms>limits_.lease_ms)
    {fault_=true;return;}
    const auto remaining=std::min(s.command_remaining_ms,s.lease_remaining_ms);
    if(!remaining || now>std::numeric_limits<std::uint64_t>::max()-remaining) return;
    target_=s.target;deadline_=now+remaining;
}
ExecutorStatus SingleExecutor::status() const {
    std::lock_guard<std::mutex> lock{mutex_};return status_;
}
void SingleExecutor::run() {
    std::uint64_t previous=0;
    for(;;) {
        VelocityTarget next{};bool quit,fault;
        {
            std::lock_guard<std::mutex> lock{mutex_};
            quit=closing_;
            std::uint64_t now=0;
            try{now=clock_();}catch(...){fault_=true;}
            if(now<previous) fault_=true;
            previous=now;fault=fault_;
            if(!quit && !fault && have_ && now>=observed_ &&
                now<deadline_ && deadline_-now>20 && sink_->ready()) next=target_;
        }
        const bool ok=sink_->write(next); // No input lock; never repeated nonzero after deadline.
        {
            std::lock_guard<std::mutex> lock{mutex_};
            status_.last_write_ok=ok;
            if(!ok) fault_=true;
            status_.state=fault_?ExecutorState::Fault:
                (next.linear_mps!=0 || next.angular_radps!=0)?ExecutorState::TargetRequested:ExecutorState::ZeroRequested;
            if(fault_) status_.output_enabled=false;
            fault=fault_;
        }
        if(quit || fault) break; // No autonomous retry/recovery.
        std::unique_lock<std::mutex> lock{mutex_};
        wake_.wait_for(lock,std::chrono::milliseconds{20},[&]{return closing_;});
    }
}
void SingleExecutor::close() {
    {std::lock_guard<std::mutex> lock{mutex_};if(closing_)return;closing_=true;target_={};deadline_=0;}
    wake_.notify_all();if(worker_.joinable())worker_.join();
    std::lock_guard<std::mutex> lock{mutex_};
    status_.state=ExecutorState::Closed;status_.output_enabled=false;
}
}
