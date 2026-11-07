#pragma once
#include "amr/motion_guard.hpp"
#include <condition_variable>
#include <functional>
#include <memory>
#include <mutex>
#include <thread>
namespace amr {
class VelocitySink {
public:
    virtual ~VelocitySink() = default;
    virtual bool ready() const noexcept = 0;
    virtual bool write(const VelocityTarget &) noexcept = 0;
};
enum class ExecutorState { Disabled, Waiting, ZeroRequested, TargetRequested, Fault, Closed };
struct ExecutorStatus {
    ExecutorState state{ExecutorState::Disabled};
    bool output_enabled{false}, last_write_ok{false}, physical_stop_confirmed{false};
};
// One thread owns all writes. Caller provides this guard's original monotonic clock.
class SingleExecutor {
public:
    using Clock=std::function<std::uint64_t()>;
    SingleExecutor(std::uint64_t boot,MotionLimits limits,Clock clock,
        std::unique_ptr<VelocitySink> sink={},bool enabled=false);
    ~SingleExecutor();
    SingleExecutor(const SingleExecutor &)=delete;
    SingleExecutor &operator=(const SingleExecutor &)=delete;
    void offer(const MotionSnapshot &,std::uint64_t observed_ms);
    ExecutorStatus status() const;
    void close();
private:
    void run();
    std::uint64_t boot_,deadline_{0},observed_{0};
    MotionLimits limits_;Clock clock_;std::unique_ptr<VelocitySink> sink_;
    bool enabled_,closing_{false},have_{false},fault_{false};
    VelocityTarget target_{};ExecutorStatus status_{};
    mutable std::mutex mutex_;std::condition_variable wake_;std::thread worker_;
};
}
