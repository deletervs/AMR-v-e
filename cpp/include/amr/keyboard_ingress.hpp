// Private parent-pipe ingress. No sockets, external authentication or actuator.
#pragma once
#include "amr/command_dispatcher.hpp"
#include <string>
#include <deque>
namespace amr
{
    struct KeyboardReceipt
    {
        std::uint64_t id, request_sequence, elapsed_ms;
        MotionResult result;
    };
    class KeyboardIngress
    {
      public:
        KeyboardIngress(MotionGuard &guard, std::uint64_t epoch_ceiling_ms, bool enabled);
        void drain(std::uint64_t now_ms);
        void invalidate_before(std::uint64_t now) { barrier_=now; }
        bool connected() const { return connected_; }
        std::uint64_t sequence() const { return sequence_; }
        const std::deque<KeyboardReceipt> &receipts() const { return receipts_; }
        MotionResult result() const { return result_; }
      private:
        void remember(std::uint64_t now);
        void disconnect(std::uint64_t now);
        void observe_session(std::uint64_t now);
        void consume(const std::string &line, std::uint64_t now);
        MotionGuard &guard_;
        CommandDispatcher commands_;
        std::uint64_t epoch_, sequence_{0}, observed_{0}, barrier_{0}, session_{0};
        bool connected_{false};
        std::string pending_;
        std::deque<KeyboardReceipt> receipts_;
        std::uint64_t receipt_id_{0}, request_sequence_{0};
        MotionResult result_{MotionResult::NotArmed};
    };
}
