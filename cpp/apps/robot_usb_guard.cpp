// Real USB -> MotionGuard policy observation. No ROS, serial or actuator linkage.
#include "amr/input_mapping.hpp"
#include "amr/command_dispatcher.hpp"
#include "amr/keyboard_ingress.hpp"
#include "amr/single_executor.hpp"
#ifdef AMR_WITH_ROS_EXECUTOR
#include "amr/ros_velocity_sink.hpp"
#include <ros/ros.h>
#endif
#include <memory>
#include <thread>
#include "amr/linux_gamepad_reader.hpp"
#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdint>
#include <fcntl.h>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <linux/joystick.h>
#include <locale>
#include <optional>
#include <poll.h>
#include <regex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <sys/ioctl.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <unistd.h>

namespace
{
    using Clock = std::chrono::steady_clock;
    volatile std::sig_atomic_t interrupted{0};
    void interrupt(int) { interrupted = 1; }
    struct Descriptor
    {
        int value{-1};
        Descriptor() = default;
        Descriptor(const Descriptor &) = delete;
        Descriptor &operator=(const Descriptor &) = delete;
        ~Descriptor() { if (value >= 0) close(value); }
    };
    enum Button : std::size_t { RB, Start, Back, Stop, Clear, Count };
    const std::array<unsigned short, Count> codes{BTN_TR, BTN_START, BTN_SELECT, BTN_EAST, BTN_SOUTH};
    struct Input
    {
        std::optional<int> x, y;
        std::array<std::optional<bool>, Count> buttons{};
        std::optional<std::uint64_t> last_event;
        std::uint64_t init_events{0}, change_events{0};
        bool initialized() const
        {
            return x && y && std::all_of(buttons.begin(), buttons.end(),
                                        [](const auto &v) { return v.has_value(); });
        }
        void clear() { x.reset(); y.reset(); for (auto &b : buttons) b.reset(); }
    };
    double normalized(int value) { return std::clamp(value / 32767.0, -1.0, 1.0); }
    bool neutral(const Input &s)
    {
        return s.initialized() && std::abs(normalized(*s.x)) <= .1 &&
               std::abs(normalized(*s.y)) <= .1 && !*s.buttons[RB];
    }
    bool other_buttons_up(const Input &s, Button except)
    {
        for (std::size_t i{0}; i < Count; ++i)
            if (i != except && (!s.buttons[i] || *s.buttons[i])) return false;
        return true;
    }
    const char *name(amr::MotionResult r)
    {
        switch (r)
        {
#define RESULT(x) case amr::MotionResult::x: return #x;
            RESULT(Accepted) RESULT(Armed) RESULT(Released) RESULT(Stopped)
            RESULT(Cleared) RESULT(LinkUpdated) RESULT(Busy) RESULT(StopLatched)
            RESULT(LinkUnavailable) RESULT(InvalidConfig) RESULT(InvalidSource)
            RESULT(InvalidSession) RESULT(InvalidSequence) RESULT(InvalidWindow)
            RESULT(InvalidVelocity) RESULT(NeutralRequired) RESULT(ClockRegression)
            RESULT(NotArmed)
#undef RESULT
        }
        return "Unknown";
    }
    const char *name(amr::StopReason r)
    {
        switch (r)
        {
#define REASON(x) case amr::StopReason::x: return #x;
            REASON(Disarmed) REASON(WaitingForNeutral) REASON(DeadmanReleased)
            REASON(None) REASON(CommandExpired) REASON(LeaseExpired)
            REASON(SoftwareStop) REASON(LinkUnavailable) REASON(InvalidCommand)
            REASON(ClockRegression) REASON(InvalidConfig)
#undef REASON
        }
        return "Unknown";
    }
    bool write_frame(const std::string &line)
    {
        if (line.size() > 4096) return false;
        const auto end = Clock::now() + std::chrono::milliseconds{20};
        std::size_t offset{0};
        while (offset < line.size())
        {
            const auto left = std::chrono::duration_cast<std::chrono::milliseconds>(end - Clock::now()).count();
            if (left <= 0) return false;
            pollfd output{STDOUT_FILENO, POLLOUT, 0};
            const int ready = poll(&output, 1, static_cast<int>(left));
            if (ready < 0 && errno == EINTR) continue;
            if (ready <= 0 || !(output.revents & POLLOUT)) return false;
            const auto size = write(STDOUT_FILENO, line.data() + offset, line.size() - offset);
            if (size < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)) continue;
            if (size <= 0) return false;
            offset += static_cast<std::size_t>(size);
        }
        return true;
    }
    struct SourceTrace
    {
        pid_t pid{-1};
        std::uint64_t sequence{0}, observed_ms{0};
        bool available{false};
        amr::KeyboardIngress *keyboard{nullptr};
        amr::SingleExecutor *executor{nullptr};
        bool execution_mode{false};
        const char *expiry_reason{"Disarmed"};
    };
    bool emit(amr::MotionGuard &guard, const Input &s, std::uint64_t now,
              std::uint64_t &sequence, const std::string &os_boot, const SourceTrace &trace,
              bool running, const char *action, const char *result, const char *reason)
    {
        const auto g = guard.snapshot(now);
        const auto execution=trace.executor?trace.executor->status():amr::ExecutorStatus{};
        const char *executor_state="disabled";
        switch(execution.state) {
            case amr::ExecutorState::Waiting:executor_state="waiting";break;
            case amr::ExecutorState::ZeroRequested:executor_state="zero_requested";break;
            case amr::ExecutorState::TargetRequested:executor_state="target_requested";break;
            case amr::ExecutorState::Fault:executor_state="fault";break;
            case amr::ExecutorState::Closed:executor_state="closed";break;
            default:break;
        }
        std::ostringstream out;
        out.imbue(std::locale::classic());
        out << std::boolalpha << std::setprecision(17)
            << "{\"source\":\"robot_usb_guard\",\"api_version\":" << (trace.execution_mode?3:2) << ",\"os_boot_id\":\"" << os_boot
            << "\",\"boot_id\":\"" << g.boot_id << "\",\"elapsed_ms\":" << now
            << ",\"sequence\":" << ++sequence
            << ",\"reader_pid\":" << trace.pid
            << ",\"source_sequence\":" << trace.sequence
            << ",\"source_observed_ms\":" << trace.observed_ms
            << ",\"source_age_ms\":" << (trace.sequence ? now-trace.observed_ms : 0)
            << ",\"guard_expiry_reason\":\"" << trace.expiry_reason << "\""
            << ",\"actuator_attached\":" << trace.execution_mode
            << ",\"motion_enabled\":" << execution.output_enabled
            << ",\"executor\":{\"state\":\"" << executor_state
            << "\",\"output_enabled\":" << execution.output_enabled
            << ",\"last_write_ok\":" << execution.last_write_ok
            << ",\"physical_stop_confirmed\":false},\"running\":" << running
            << ",\"initialized\":" << s.initialized()
            << ",\"init_events\":" << s.init_events << ",\"change_events\":" << s.change_events
            << ",\"last_event_elapsed_ms\":";
        if (s.last_event) out << *s.last_event; else out << "null";
        out << ",\"raw\":{\"x\":";
        if (s.x) out << *s.x; else out << "null";
        out << ",\"y\":";
        if (s.y) out << *s.y; else out << "null";
        const std::array<const char *, Count> labels{"rb", "start", "back", "b_stop", "a_clear"};
        for (std::size_t i{0}; i < Count; ++i)
        {
            out << ",\"" << labels[i] << "\":";
            if (s.buttons[i]) out << *s.buttons[i]; else out << "null";
        }
        out << "},\"guard_armed\":" << g.armed << ",\"stop_latched\":" << g.stop_latched
            << ",\"neutral_confirmed\":" << g.neutral_confirmed
            << ",\"local_input_available\":" << trace.available
            << ",\"control_owner\":\"" << (g.owner==amr::ControlSource::Keyboard?"keyboard":g.owner==amr::ControlSource::Gamepad?"gamepad":"none")
            << "\",\"keyboard_channel_open\":" << (trace.keyboard && trace.keyboard->connected())
            << ",\"keyboard_sequence\":\"" << (trace.keyboard?trace.keyboard->sequence():0)
            << "\",\"keyboard_result\":\"" << (trace.keyboard?name(trace.keyboard->result()):"NotArmed") << "\""
        ;
        out << ",\"keyboard_receipts\":[";
        if(trace.keyboard)
        {
            bool first=true;
            for(const auto &receipt:trace.keyboard->receipts())
            {
                if(!first) out << ',';
                first=false;
                out << "{\"id\":\"" << receipt.id << "\",\"request_sequence\":\""
                    << receipt.request_sequence << "\",\"elapsed_ms\":" << receipt.elapsed_ms
                    << ",\"result\":\"" << name(receipt.result) << "\"}";
            }
        }
        out << "]"
            << ",\"session_id\":\"" << g.session_id << "\",\"controller_id\":\"" << g.controller_id
            << "\",\"command_sequence\":\"" << g.last_sequence
            << "\",\"lease_remaining_ms\":" << g.lease_remaining_ms
            << ",\"command_remaining_ms\":" << g.command_remaining_ms
            << ",\"candidate\":{\"linear_mps\":" << g.target.linear_mps
            << ",\"angular_radps\":" << g.target.angular_radps
            << "},\"stop_reason\":\"" << name(g.reason) << "\",\"action\":\"" << action
            << "\",\"result\":\"" << result << "\",\"runtime_reason\":\"" << reason << "\"}\n";
        return write_frame(out.str());
    }
}

int main(int argc, char **argv)
{
    // Each process is a new guard epoch; OS boot identity is emitted separately.
    std::uint64_t boot{0};
    try
    {
        Descriptor random;
        random.value = open("/dev/urandom", O_RDONLY | O_CLOEXEC);
        if (random.value < 0 || read(random.value, &boot, sizeof(boot)) != sizeof(boot) || !boot)
            throw std::runtime_error("Cannot create fresh guard identity");
    }
    catch (const std::exception &error) { std::cerr << error.what() << '\n'; return 2; }
    amr::MotionGuard guard{boot};
    amr::CommandDispatcher commands{guard};
    Input input;
    const auto started = Clock::now();
    const auto elapsed = [&] {
        return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now() - started).count());
    };
    std::unique_ptr<amr::SingleExecutor> executor;
    std::uint64_t sequence{0}, command_sequence{0};
    std::string os_boot;
    SourceTrace trace;
    bool output_ready{false};
    try
    {
        if(argc<5 || argc>11 || std::string{argv[1]}!="--device" ||
            std::string{argv[3]}!="--duration-seconds")
            throw std::invalid_argument("Expected device, duration and optional private flags");
        bool keyboard_enabled=false, initial_stop=false;
        std::string execute_driver;
        std::uint64_t execute_until=0;
        for(int i=5;i<argc;++i)
        {
            const std::string flag{argv[i]};
            if(flag=="--initial-stop" && !initial_stop) initial_stop=true;
            else if(flag=="--keyboard-stdin" && !keyboard_enabled) keyboard_enabled=true;
            else if(flag=="--execute-driver" && execute_driver.empty() && i+1<argc)execute_driver=argv[++i];
            else if(flag=="--execute-until" && !execute_until && i+1<argc) {
                const std::string value{argv[++i]};
                if(!std::regex_match(value,std::regex{"[0-9]{1,20}"}))throw std::invalid_argument("Execution expiry");
                execute_until=std::stoull(value);
            } else throw std::invalid_argument("Unknown or duplicate private flag");
        }
        const std::string path{argv[2]}, duration{argv[4]};
        if (!std::regex_match(path, std::regex{"/dev/input/js[0-9]{1,2}"}) ||
            !std::regex_match(duration, std::regex{"[0-9]{1,3}"}))
            throw std::invalid_argument("Invalid device or duration");
        const auto seconds = std::stoul(duration);
        if (seconds > 600) throw std::invalid_argument("Duration outside 0..600");
        // Zero means lifetime owned by the explicitly launching panel process, never boot autostart.
        if (seconds == 0)
        {
            const auto parent = getppid();
            if (parent <= 1 || prctl(PR_SET_PDEATHSIG, SIGKILL) != 0 || getppid() != parent)
                throw std::runtime_error("Unbounded input runtime requires a live owning parent");
        }
        if (initial_stop) commands.dispatch(amr::StopControl{},elapsed());
        std::ifstream kernel_boot{"/proc/sys/kernel/random/boot_id"};
        kernel_boot >> os_boot;
        if (!std::regex_match(os_boot, std::regex{"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}"}))
            throw std::runtime_error("Cannot read OS boot identity");

        const int flags=fcntl(STDOUT_FILENO,F_GETFL);
        if(flags<0 || fcntl(STDOUT_FILENO,F_SETFL,flags|O_NONBLOCK)<0)
            throw std::runtime_error("Cannot bound stdout");
        output_ready=true;
        std::signal(SIGINT,interrupt);std::signal(SIGTERM,interrupt);
        std::signal(SIGHUP,interrupt);std::signal(SIGPIPE,SIG_IGN);
        const auto epoch=static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(started.time_since_epoch()).count())+1;
        if(execute_driver.empty()!=(!execute_until))throw std::invalid_argument("Paired execution arguments required");
        const auto absolute_now=[] {return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now().time_since_epoch()).count());};
#ifdef AMR_WITH_ROS_EXECUTOR
        amr::RosVelocitySink *ros_sink=nullptr;
#endif
        if(!execute_driver.empty()) {
            const auto now=absolute_now();
            if(execute_until<=now || execute_until-now>300000 ||
                !std::regex_match(execute_driver,std::regex{"/[A-Za-z_][A-Za-z0-9_/]{0,126}"}))
                throw std::invalid_argument("Execution authorization expired or invalid");
#ifdef AMR_WITH_ROS_EXECUTOR
            int ros_argc=0;char **ros_argv=nullptr;
            ros::init(ros_argc,ros_argv,"rs_motion_executor",ros::init_options::NoSigintHandler|ros::init_options::NoRosout);
            auto sink=amr::make_ros_velocity_sink(execute_driver,execute_until);
            const auto ready_until=Clock::now()+std::chrono::seconds{1};
            while(!sink->ready() && Clock::now()<ready_until) {
                sink->renew_exclusivity();
                if(!sink->ready())std::this_thread::sleep_for(std::chrono::milliseconds{20});
            }
            if(!sink->ready())throw std::runtime_error("Exclusive vendor output unavailable");
            ros_sink=sink.get();
            executor=std::make_unique<amr::SingleExecutor>(boot,guard.limits(),elapsed,std::move(sink),true);
            trace.execution_mode=true;
#else
            throw std::runtime_error("This binary was built without ROS execution support");
#endif
        } else executor=std::make_unique<amr::SingleExecutor>(boot,guard.limits(),elapsed);
        trace.executor=executor.get();
        const auto offer_output=[&](std::uint64_t now) {
            auto approved=guard.snapshot(now);
            if(execute_until) {
                const auto absolute=absolute_now();
                const auto remaining=absolute<execute_until?execute_until-absolute:0;
                approved.command_remaining_ms=std::min(approved.command_remaining_ms,remaining);
            }
            executor->offer(approved,now);
        };
        amr::KeyboardIngress keyboard{guard,epoch,keyboard_enabled};
        trace.keyboard=&keyboard;
        amr::io::LinuxGamepadReader reader{path,static_cast<unsigned>(seconds),started};
        trace.pid=reader.pid();
        guard.set_link(false,elapsed());
        std::uint64_t last_tick{elapsed()},last_emit{0},arm_epoch{0};
        const auto initialization_start=elapsed();
        std::uint64_t last_graph=0;
        const char *finish="RuntimeExpired";
        if(!emit(guard,input,elapsed(),sequence,os_boot,trace,true,"none","NotArmed","Observing")) return 3;
        bool done{false};
        while(!done)
        {
            const auto before=elapsed();
            if(interrupted) {finish="Interrupted";break;}
            if(seconds && before>=seconds*1000) break;
            if(before-last_tick>=150) {finish="LoopLate";break;}
            last_tick=before;
            if(execute_until && absolute_now()>=execute_until) {finish="ExecutionPermitExpired";break;}
#ifdef AMR_WITH_ROS_EXECUTOR
            if(ros_sink && before-last_graph>=100) {
                ros_sink->renew_exclusivity();last_graph=before;
            }
#endif
            if(executor->status().state==amr::ExecutorState::Fault) {finish="ExecutorFault";break;}
            offer_output(before);
            if(trace.sequence && before-trace.observed_ms>=guard.limits().command_timeout_ms)
            {finish="SourceTimeout";break;}
            if(!trace.sequence && before-initialization_start>=2000) {finish="SourceInitializationTimeout";break;}
            pollfd fd{reader.fd(),POLLIN,0};
            const auto ready=poll(&fd,1,20);
            if(ready<0 && errno==EINTR) continue;
            if(ready<0 || fd.revents&POLLNVAL) throw std::runtime_error("Source poll failed");
            unsigned drained{0};
            for(;drained<32;++drained)
            {
                amr::io::UsbSample sample{};
                if(!reader.read_sample(sample)) break;
                const auto now=elapsed();
                if(sample.magic!=0x52535553 || sample.version!=1 || sample.sequence!=trace.sequence+1 ||
                    sample.observed_ms>now || (trace.sequence && sample.observed_ms<trace.observed_ms) ||
                    sample.known_mask>255 || sample.button_mask>31 || sample.pressed_mask>31 ||
                    (sample.button_mask & ~((sample.known_mask>>2)&31)) ||
                    (sample.pressed_mask & ~((sample.known_mask>>2)&31)) ||
                    sample.x < -32768 || sample.x>32767 || sample.y < -32768 || sample.y>32767 ||
                    sample.last_event_ms>sample.observed_ms ||
                    sample.init_events<input.init_events || sample.change_events<input.change_events ||
                    sample.status>static_cast<std::uint32_t>(amr::io::UsbStatus::Error))
                    throw std::runtime_error("Invalid source packet");
                if(now-sample.observed_ms>=guard.limits().command_timeout_ms)
                {finish="StaleSource";done=true;break;}
                trace.sequence=sample.sequence;trace.observed_ms=sample.observed_ms;
                if(sample.status!=static_cast<std::uint32_t>(amr::io::UsbStatus::Observing))
                {
                    const std::array<const char *,6> reasons{"Observing","Disconnected","SourceLoopLate","RuntimeExpired","SourceInterrupted","SourceError"};
                    finish=reasons[sample.status];done=true;break;
                }
                input.clear();
                if(sample.known_mask&1) input.x=sample.x;
                if(sample.known_mask&2) input.y=sample.y;
                for(std::size_t i{0};i<Count;++i)
                    if(sample.known_mask&(1U<<(i+2))) input.buttons[i]=(sample.button_mask&(1U<<i))!=0;
                if(sample.known_mask&128) input.last_event=sample.last_event_ms; else input.last_event.reset();
                input.init_events=sample.init_events;input.change_events=sample.change_events;
                if(!trace.available) {guard.set_link(true,now);trace.available=true;}
                const auto pressed=[&](Button button) {return (sample.pressed_mask&(1U<<button))!=0;};
                const char *action="none",*result="Idle";
                auto snapshot=guard.snapshot(now);
                if(pressed(Stop)) {commands.dispatch(amr::StopControl{},now);keyboard.invalidate_before(now);action="stop";result="Stopped";}
                else if(pressed(Back))
                {
                    keyboard.invalidate_before(now);
                    action="release";result=name(commands.dispatch(amr::ReleaseControl{1,boot,snapshot.session_id},now).result);
                }
                else if(pressed(Clear))
                {
                    action="clear";
                    keyboard.invalidate_before(now);
                    result=neutral(input)&&other_buttons_up(input,Clear)&&!pressed(Start)
                        ?name(commands.dispatch(amr::ClearControl{},now).result):"NeutralRequired";
                }
                else if(pressed(Start))
                {
                    keyboard.invalidate_before(now);
                    action="arm";
                    if(neutral(input)&&other_buttons_up(input,Start))
                    {
                        const auto grant=commands.dispatch(amr::ArmControl{1,amr::ControlSource::Gamepad},now);
                        result=name(grant.result);
                        if(grant.result==amr::MotionResult::Armed) {command_sequence=0;arm_epoch=now;}
                    }
                    else result="NeutralRequired";
                }
                snapshot=guard.snapshot(now);
                if(snapshot.armed && snapshot.owner==amr::ControlSource::Gamepad && snapshot.controller_id==1 && input.initialized() && sample.observed_ms>=arm_epoch)
                {
                    amr::VelocityTarget target{};
                    if(amr::map_gamepad_input(normalized(*input.x),normalized(*input.y),*input.buttons[RB],
                        guard.limits().linear_mps,guard.limits().angular_radps,.1,target)!=amr::InputMapResult::Mapped)
                        throw std::runtime_error("Mapping rejected real sample");
                    amr::MotionCommand command{};
                    command.boot_id=boot;command.session_id=snapshot.session_id;command.controller_id=1;
                    command.source=amr::ControlSource::Gamepad;command.sequence=++command_sequence;
                    command.issued_ms=sample.observed_ms;
                    command.valid_until_ms=sample.observed_ms+guard.limits().command_timeout_ms;
                    command.deadman=*input.buttons[RB];command.input_neutral=neutral(input);command.target=target;
                    const auto submission=commands.dispatch(command,now).result;
                    if(action==std::string{"none"}) result=name(submission);
                    if(submission!=amr::MotionResult::Accepted && submission!=amr::MotionResult::NeutralRequired)
                        throw std::runtime_error("Unexpected guard rejection");
                }
                if(action!=std::string{"none"} || now-last_emit>=20)
                {
                    last_emit=now;
                    if(!emit(guard,input,now,sequence,os_boot,trace,true,action,result,"Observing"))
                    {guard.set_link(false,elapsed());return 3;}
                }
            }
            if(done) break;
            keyboard.drain(elapsed());
            const auto output_time=elapsed();
            offer_output(output_time);
            if(drained==32) {finish="SourceBacklog";break;}
            if((fd.revents&(POLLHUP|POLLERR)) && drained==0) {finish="SourceEnded";break;}
            if(elapsed()-last_emit>=100)
            {
                last_emit=elapsed();
                if(!emit(guard,input,last_emit,sequence,os_boot,trace,true,"none","Idle","Observing"))
                {guard.set_link(false,elapsed());return 3;}
            }
        }
        const auto now=elapsed();
        trace.expiry_reason=name(guard.snapshot(now).reason);
        guard.set_link(false,now);trace.available=false;input.clear();
        executor->close();
        reader.stop();
        trace.keyboard=nullptr;
        return emit(guard,input,elapsed(),sequence,os_boot,trace,false,"exit","LinkUnavailable",finish)?0:3;
    }
    catch(const std::exception &error)
    {
        trace.keyboard=nullptr;
        if(executor)executor->close();
        trace.expiry_reason=name(guard.snapshot(elapsed()).reason);
        guard.set_link(false,elapsed());trace.available=false;input.clear();
        std::cerr<<error.what()<<'\n';
        if(output_ready) emit(guard,input,elapsed(),sequence,os_boot,trace,false,"exit","Error","Error");
        return 2;
    }
}
