#include "amr/keyboard_ingress.hpp"
#include "amr/input_mapping.hpp"
#include <algorithm>
#include <cerrno>
#include <fcntl.h>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <unistd.h>
#include <vector>

namespace
{
    std::uint64_t number(const std::string &s)
    {
        if (s.empty() || s.size()>20 ||
            !std::all_of(s.begin(),s.end(),[](char c){return c>='0'&&c<='9';}))
            throw std::invalid_argument("integer");
        return std::stoull(s);
    }
    bool bit(const std::string &s)
    {
        if(s!="0"&&s!="1") throw std::invalid_argument("bit");
        return s=="1";
    }
}
namespace amr
{
    KeyboardIngress::KeyboardIngress(MotionGuard &guard, std::uint64_t epoch, bool enabled)
        : guard_{guard}, commands_{guard}, epoch_{epoch}, connected_{enabled}
    {
        if(enabled)
        {
            const auto flags=fcntl(STDIN_FILENO,F_GETFL);
            if(flags<0 || fcntl(STDIN_FILENO,F_SETFL,flags|O_NONBLOCK)<0)
                throw std::runtime_error("Cannot bound private keyboard pipe");
        }
    }
    void KeyboardIngress::disconnect(std::uint64_t now)
    {
        connected_=false;pending_.clear();
        const auto s=guard_.snapshot(now);
        if(s.owner==ControlSource::Keyboard && s.controller_id==2)
            commands_.dispatch(ReleaseControl{2,s.boot_id,s.session_id},now);
    }
    void KeyboardIngress::consume(const std::string &line, std::uint64_t now)
    {
        std::istringstream input{line};
        std::vector<std::string> t;
        std::string word;
        while(input>>word) t.push_back(word);
        if(t.size()<4) throw std::invalid_argument("header");
        const auto boot=number(t[0]), seq=number(t[1]), observed=number(t[2]);
        const auto s=guard_.snapshot(now);
        if(boot!=s.boot_id) {result_=MotionResult::InvalidSession;return;}
        request_sequence_=seq;
        if(seq==0 || seq<=sequence_) {result_=MotionResult::InvalidSequence;return;}
        // Absolute timestamps are from this Linux host's CLOCK_MONOTONIC.
        // ceil(process epoch) shortens validity by <=1ms rather than extending it.
        if(observed<epoch_ || observed-epoch_<=barrier_ || observed-epoch_>now || observed<observed_ ||
            now-(observed-epoch_)>=guard_.limits().command_timeout_ms)
        {result_=MotionResult::InvalidWindow;return;}
        sequence_=seq;observed_=observed;
        const auto issued=observed-epoch_;
        if(t[3]=="arm" && t.size()==4)
            result_=commands_.dispatch(ArmControl{2,ControlSource::Keyboard},now).result;
        else if(t[3]=="stop" && t.size()==4)
            result_=commands_.dispatch(StopControl{},now).result;
        else if(t[3]=="release" && t.size()==5)
            result_=commands_.dispatch(ReleaseControl{2,boot,number(t[4])},now).result;
        else if(t[3]=="keys" && t.size()==10)
        {
            MotionCommand c{};
            c.boot_id=boot;c.controller_id=2;c.session_id=number(t[4]);
            c.source=ControlSource::Keyboard;c.sequence=seq;
            c.issued_ms=issued;
            if(issued>std::numeric_limits<std::uint64_t>::max()-guard_.limits().command_timeout_ms)
                throw std::invalid_argument("deadline overflow");
            c.valid_until_ms=issued+guard_.limits().command_timeout_ms;
            const bool f=bit(t[5]),b=bit(t[6]),l=bit(t[7]),r=bit(t[8]);
            c.deadman=bit(t[9]);c.input_neutral=!f&&!b&&!l&&!r&&!c.deadman;
            if(map_virtual_input(f,b,l,r,c.deadman,guard_.limits().linear_mps,
                guard_.limits().angular_radps,c.target)!=InputMapResult::Mapped)
                throw std::invalid_argument("mapping");
            result_=commands_.dispatch(c,now).result;
        }
        else throw std::invalid_argument("action"); // No remote clear/link/source selection.
    }
    void KeyboardIngress::observe_session(std::uint64_t now)
    {
        const auto current=guard_.snapshot(now).session_id;
        if(current!=session_)
        {
            barrier_=now;
            session_=current;
        }
    }
    void KeyboardIngress::remember(std::uint64_t now)
    {
        receipts_.push_back({++receipt_id_,request_sequence_,now,result_});
        if(receipts_.size()>16) receipts_.pop_front();
    }
    void KeyboardIngress::drain(std::uint64_t now)
    {
        if(!connected_) return;
        observe_session(now);
        // Bound work per reactor tick. Partial messages retain their original timestamp.
        char buffer[256];
        const auto count=read(STDIN_FILENO,buffer,sizeof buffer);
        if(count<0 && (errno==EAGAIN || errno==EWOULDBLOCK || errno==EINTR)) return;
        if(count<=0) {disconnect(now);return;}
        try
        {
            for(ssize_t i=0;i<count;++i)
            {
                if(buffer[i]=='\n')
                {
                    request_sequence_=0;
                    consume(pending_,now);
                    remember(now);
                    pending_.clear();observe_session(now);
                }
                else
                {
                    if(pending_.size()>=160) throw std::invalid_argument("size");
                    if(pending_.empty()) request_sequence_=0;
                    pending_+=buffer[i];
                }
            }
        }
        catch(const std::exception &)
        {
            result_=MotionResult::InvalidWindow;
            remember(now);
            disconnect(now); // Invalid keyboard input cannot revoke a gamepad owner.
        }
    }
}
