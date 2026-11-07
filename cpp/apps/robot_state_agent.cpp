// 机器人端只读入口：读取 Jetson 的 Linux 状态，不创建 ROS 发布者或运动接口。
#include "amr/battery_state.hpp"
#include "amr/event_log.hpp"
#include "amr/lidar_source.hpp"
#include "amr/odometry_state.hpp"
#if defined(AMR_WITH_ROS_BATTERY) || defined(AMR_WITH_ROS_ODOMETRY) || defined(AMR_WITH_ROS_LIDAR)
#define AMR_WITH_ROS_TELEMETRY
#include <memory>
#include <ros/master.h>
#include <ros/ros.h>
#ifdef AMR_WITH_ROS_BATTERY
#include "amr/battery_source.hpp"
#include <handsfree_msgs/robot_state.h>
#endif
#ifdef AMR_WITH_ROS_ODOMETRY
#include "amr/odometry_source.hpp"
#include <geometry_msgs/PoseStamped.h>
#include <geometry_msgs/TwistStamped.h>
#endif
#ifdef AMR_WITH_ROS_LIDAR
#include <sensor_msgs/LaserScan.h>
#endif
#endif
#include "amr/linux_resources.hpp"
#include "amr/system_resources.hpp"

#include <chrono>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <limits>
#include <optional>
#include <sstream>
#include <string>
#include <thread>
#include <unistd.h>
#include <utility>

namespace
{
    using Clock = std::chrono::steady_clock;
    using Ms = std::chrono::milliseconds;

    struct BatteryObservation
    {
        bool supported{false};
        std::optional<Ms> received_elapsed{};
        std::optional<std::uint64_t> source_stamp_ns{};
        std::optional<double> source_time_raw{};
    };

    struct OdometryObservation
    {
        bool supported{false};
        std::optional<Ms> received_elapsed{};
        std::optional<std::uint64_t> source_stamp_ns{};
        std::optional<std::uint32_t> source_sequence{}, source_twist_sequence{};
        std::optional<std::string> frame_id{}, child_frame_id{};
    };

    struct LidarObservation
    {
        bool supported{false};
        std::optional<Ms> received_elapsed{};
    };

    std::ostream *json_output{&std::cout};
    std::ostream &protocol_output() { return *json_output; }

    std::string read_text(const std::string &path)
    {
        std::ifstream input{path};
        return {std::istreambuf_iterator<char>{input}, std::istreambuf_iterator<char>{}};
    }

    bool read_cpu(amr::CpuTicks &ticks)
    {
        std::ifstream input{"/proc/stat"};
        std::string line;
        return std::getline(input, line) && amr::parse_cpu_line(line, ticks);
    }

    std::string cpu_temperature_path()
    {
        for (int i{0}; i < 64; ++i)
        {
            const auto zone = std::string{"/sys/class/thermal/thermal_zone"} + std::to_string(i);
            std::ifstream input{zone + "/type"};
            std::string type;
            if (std::getline(input, type) && (type == "CPU-therm" || type == "cpu-thermal"))
                return zone + "/temp";
        }
        return {};
    }

    std::optional<double> read_temperature(const std::string &path)
    {
        if (path.empty())
            return std::nullopt;
        std::istringstream input{read_text(path)};
        double milli_c{0.0};
        std::string extra;
        if (!(input >> milli_c) || input >> extra || !std::isfinite(milli_c))
            return std::nullopt;
        return milli_c / 1000.0;
    }

    const char *quality_name(amr::DataQuality value)
    {
        switch (value)
        {
        case amr::DataQuality::Unknown:
            return "Unknown";
        case amr::DataQuality::Valid:
            return "Valid";
        case amr::DataQuality::Stale:
            return "Stale";
        case amr::DataQuality::Fault:
            return "Fault";
        case amr::DataQuality::Unsupported:
            return "Unsupported";
        }
        return "Unknown";
    }

    void json_string(const std::string &text)
    {
        constexpr char hex[] = "0123456789abcdef";
        protocol_output() << '"';
        for (const unsigned char ch : text)
        {
            if (ch == '"' || ch == '\\')
                protocol_output() << '\\' << static_cast<char>(ch);
            else if (ch < 0x20)
                protocol_output() << "\\u00" << hex[ch >> 4] << hex[ch & 15];
            else
                protocol_output() << static_cast<char>(ch);
        }
        protocol_output() << '"';
    }

    void append_event(amr::EventLog &events, Ms elapsed, amr::LogLevel level,
                      const std::string &source, const std::string &message)
    {
        if (events.append({elapsed.count(), level, source, message}) != amr::LogAppendResult::Append)
            throw std::runtime_error("internal telemetry event rejected");
    }

#ifdef AMR_WITH_ROS_TELEMETRY
    class RosTelemetrySubscription
    {
      public:
        RosTelemetrySubscription(amr::RobotState &state, BatteryObservation &battery,
                                 OdometryObservation &odometry, amr::LidarState &lidar, LidarObservation &lidar_observation, Clock::time_point start, amr::EventLog &events)
            : state_{state}, battery_{battery}, odometry_{odometry}, lidar_{lidar}, lidar_observation_{lidar_observation}, start_{start}, events_{events}
        {
            ros::M_string remappings;
            remappings["__master"] = "http://127.0.0.1:11321";
            remappings["__hostname"] = "127.0.0.1";
            ros::init(remappings, "rs_readonly_state_agent",
                      ros::init_options::NoRosout | ros::init_options::NoSigintHandler);
            ros::master::setRetryTimeout(ros::WallDuration{0.5});
#ifdef AMR_WITH_ROS_BATTERY
            battery_.supported = true;
            append_event(events_, Ms{0}, amr::LogLevel::Info, "battery", "ROS battery adapter enabled");
#endif
#ifdef AMR_WITH_ROS_ODOMETRY
            odometry_.supported = true;
            append_event(events_, Ms{0}, amr::LogLevel::Info, "odometry", "ROS matched pose/twist adapter enabled");
#endif
#ifdef AMR_WITH_ROS_LIDAR
            lidar_observation_.supported = true;
            append_event(events_, Ms{0}, amr::LogLevel::Info, "lidar", "Read-only LaserScan adapter enabled");
#endif
        }
        void poll()
        {
            const auto now = Clock::now();
            if (!node_ && (!last_attempt_ || now - *last_attempt_ >= std::chrono::seconds{2}))
            {
                last_attempt_ = now;
                if (ros::master::check())
                {
                    node_ = std::make_unique<ros::NodeHandle>();
#ifdef AMR_WITH_ROS_BATTERY
                    battery_subscriber_ = node_->subscribe("/handsfree/robot_state", 1,
                                                           &RosTelemetrySubscription::receive_battery, this);
#endif
#ifdef AMR_WITH_ROS_ODOMETRY
                    pose_subscriber_ = node_->subscribe("/handsfree/readonly_pose", 1,
                                                        &RosTelemetrySubscription::receive_pose, this);
                    twist_subscriber_ = node_->subscribe("/handsfree/readonly_twist", 1,
                                                         &RosTelemetrySubscription::receive_twist, this);
#endif
#ifdef AMR_WITH_ROS_LIDAR
                    lidar_subscriber_ = node_->subscribe("/scan", 1, &RosTelemetrySubscription::receive_lidar, this);
#endif
                }
            }
            if (node_ && ros::ok())
                ros::spinOnce();
        }

      private:
#ifdef AMR_WITH_ROS_BATTERY
        void receive_battery(const handsfree_msgs::robot_state::ConstPtr &message)
        {
            const auto stamp = message->header.stamp.toNSec();
            if (!amr::battery_stamp_recent(stamp, ros::Time::now().toNSec(), 3000000000ULL))
                return;
            const amr::BatterySample sample{message->battery_voltage, message->system_time, stamp};
            const auto result = battery_gate_.observe(sample);
            if (!amr::battery_sample_accepted(result))
                return;
            const auto now = Clock::now();
            const auto elapsed = std::chrono::duration_cast<Ms>(now - start_);
            amr::update_battery_reading(state_.battery, sample.voltage_v, now);
            battery_.received_elapsed = elapsed;
            battery_.source_stamp_ns = stamp;
            battery_.source_time_raw = sample.source_time_raw;
            if (result == amr::BatterySampleResult::AcceptedAfterReset)
                append_event(events_, elapsed, amr::LogLevel::Warning, "battery",
                             "MCU source counter rebased after two advancing samples");
            else if (result == amr::BatterySampleResult::AcceptedWithClockAdjustment)
                append_event(events_, elapsed, amr::LogLevel::Warning, "battery",
                             "ROS clock moved backwards; MCU source still advances");
        }
        amr::BatterySampleGate battery_gate_{};
        ros::Subscriber battery_subscriber_{};
#endif
#ifdef AMR_WITH_ROS_ODOMETRY
        void receive_pose(const geometry_msgs::PoseStamped::ConstPtr &message)
        {
            pose_ = message;
            receive_pair();
        }
        void receive_twist(const geometry_msgs::TwistStamped::ConstPtr &message)
        {
            twist_ = message;
            receive_pair();
        }
        void receive_pair()
        {
            if (!pose_ || !twist_ || pose_->header.stamp != twist_->header.stamp)
                return;
            const auto pose = pose_;
            const auto twist = twist_;
            pose_.reset();
            twist_.reset();
            const auto stamp = pose->header.stamp.toNSec();
            const auto ros_now = ros::Time::now().toNSec();
            if (!stamp || stamp > ros_now || ros_now - stamp >= 1500000000ULL)
                return;
            amr::OdometrySample sample{};
            sample.x_m = pose->pose.position.x;
            sample.y_m = pose->pose.position.y;
            sample.qx = pose->pose.orientation.x;
            sample.qy = pose->pose.orientation.y;
            sample.qz = pose->pose.orientation.z;
            sample.qw = pose->pose.orientation.w;
            sample.linear_x_mps = twist->twist.linear.x;
            sample.linear_y_mps = twist->twist.linear.y;
            sample.angular_z_radps = twist->twist.angular.z;
            sample.source_stamp_ns = stamp;
            sample.source_sequence = pose->header.seq;
            sample.source_twist_sequence = twist->header.seq;
            sample.frame_id = pose->header.frame_id;
            sample.child_frame_id = twist->header.frame_id;
            amr::OdometryReading reading{};
            const auto result = odometry_gate_.observe(sample, reading);
            if (!amr::odometry_sample_accepted(result))
                return;
            const auto now = Clock::now();
            const auto elapsed = std::chrono::duration_cast<Ms>(now - start_);
            amr::update_odometry_reading(state_.odometry, reading, now);
            odometry_.received_elapsed = elapsed;
            odometry_.source_stamp_ns = stamp;
            odometry_.source_sequence = sample.source_sequence;
            odometry_.source_twist_sequence = sample.source_twist_sequence;
            odometry_.frame_id = sample.frame_id;
            odometry_.child_frame_id = sample.child_frame_id;
            if (result == amr::OdometrySampleResult::AcceptedAfterReset)
                append_event(events_, elapsed, amr::LogLevel::Warning, "odometry", "ROS publisher counter rebased after two advancing pairs");
            else if (result == amr::OdometrySampleResult::AcceptedWithClockAdjustment)
                append_event(events_, elapsed, amr::LogLevel::Warning, "odometry", "ROS clock moved backwards; pose sequence still advances");
        }
        amr::OdometrySampleGate odometry_gate_{};
        ros::Subscriber pose_subscriber_{}, twist_subscriber_{};
        geometry_msgs::PoseStamped::ConstPtr pose_{};
        geometry_msgs::TwistStamped::ConstPtr twist_{};
#endif
#ifdef AMR_WITH_ROS_LIDAR
        void receive_lidar(const sensor_msgs::LaserScan::ConstPtr &message)
        {
            const auto stamp = message->header.stamp.toNSec();
            const auto ros_now = ros::Time::now().toNSec();
            if (!stamp || stamp > ros_now || ros_now - stamp >= 1500000000ULL || message->ranges.empty() || message->ranges.size() > 4096)
                return;
            amr::LidarSample sample{};
            sample.geometry = {message->angle_min, message->angle_max, message->angle_increment, message->range_min, message->range_max, message->scan_time, message->time_increment};
            sample.ranges_m.assign(message->ranges.begin(), message->ranges.end());
            sample.source_stamp_ns = stamp;
            sample.source_sequence = message->header.seq;
            sample.frame_id = message->header.frame_id;
            amr::LidarReading reading{};
            const auto result = lidar_gate_.observe(sample, reading);
            if (!amr::lidar_sample_accepted(result))
                return;
            const auto now = Clock::now();
            const auto elapsed = std::chrono::duration_cast<Ms>(now - start_);
            lidar_.reading = std::move(reading);
            lidar_.received_at = now;
            lidar_.quality = amr::DataQuality::Valid;
            lidar_observation_.received_elapsed = elapsed;
            if (result == amr::LidarSampleResult::AcceptedAfterReset)
                append_event(events_, elapsed, amr::LogLevel::Warning, "lidar", "ROS scan counter rebased after two advancing samples");
            else if (result == amr::LidarSampleResult::AcceptedWithClockAdjustment)
                append_event(events_, elapsed, amr::LogLevel::Warning, "lidar", "ROS clock moved backwards; scan sequence still advances");
        }
        amr::LidarSampleGate lidar_gate_{};
        ros::Subscriber lidar_subscriber_{};
#endif
        amr::RobotState &state_;
        BatteryObservation &battery_;
        OdometryObservation &odometry_;
        amr::LidarState &lidar_;
        LidarObservation &lidar_observation_;
        Clock::time_point start_;
        amr::EventLog &events_;
        std::unique_ptr<ros::NodeHandle> node_{};
        std::optional<Clock::time_point> last_attempt_{};
    };
#endif

    void write_frame(const amr::RobotState &state, Ms elapsed, std::uint64_t sequence,
                     const std::string &hostname, const std::string &temperature_path,
                     const amr::EventLog &events, const BatteryObservation &observation,
                     const OdometryObservation &odometry = OdometryObservation{},
                     const amr::LidarState &lidar = amr::LidarState{}, const LidarObservation &lidar_observation = LidarObservation{})
    {
        protocol_output() << std::boolalpha << std::setprecision(17)
                          << "{\"source\":\"robot\",\"elapsed_ms\":" << elapsed.count()
                          << ",\"sequence\":" << sequence
                          << ",\"connected\":true,\"motion_enabled\":false"
                          << ",\"capabilities\":{\"battery\":" << observation.supported
                          << ",\"odometry\":" << odometry.supported << ",\"lidar\":" << lidar_observation.supported << "}"
                          << ",\"battery\":{\"voltage_v\":";
        if (state.battery.voltage_v)
            protocol_output() << *state.battery.voltage_v;
        else
            protocol_output() << "null";
        protocol_output() << ",\"quality\":\"" << quality_name(state.battery.quality)
                          << "\",\"received_elapsed_ms\":";
        if (observation.received_elapsed)
            protocol_output() << observation.received_elapsed->count();
        else
            protocol_output() << "null";
        protocol_output() << ",\"source_stamp_ns\":";
        if (observation.source_stamp_ns)
            json_string(std::to_string(*observation.source_stamp_ns));
        else
            protocol_output() << "null";
        protocol_output() << ",\"source_time_raw\":";
        if (observation.source_time_raw)
            protocol_output() << *observation.source_time_raw;
        else
            protocol_output() << "null";
        protocol_output() << "},\"odometry\":{\"reading\":";
        if (state.odometry.reading)
        {
            const auto &r = *state.odometry.reading;
            protocol_output() << "{\"x_m\":" << r.x_m << ",\"y_m\":" << r.y_m
                              << ",\"yaw_rad\":" << r.yaw_rad << ",\"linear_x_mps\":" << r.linear_x_mps
                              << ",\"linear_y_mps\":" << r.linear_y_mps << ",\"angular_z_radps\":" << r.angular_z_radps << '}';
        }
        else
            protocol_output() << "null";
        protocol_output() << ",\"quality\":\"" << quality_name(state.odometry.quality) << "\",\"received_elapsed_ms\":";
        if (odometry.received_elapsed)
            protocol_output() << odometry.received_elapsed->count();
        else
            protocol_output() << "null";
        protocol_output() << ",\"source_stamp_ns\":";
        if (odometry.source_stamp_ns)
            json_string(std::to_string(*odometry.source_stamp_ns));
        else
            protocol_output() << "null";
        protocol_output() << ",\"source_sequence\":";
        if (odometry.source_sequence)
            protocol_output() << *odometry.source_sequence;
        else
            protocol_output() << "null";
        protocol_output() << ",\"source_twist_sequence\":";
        if (odometry.source_twist_sequence)
            protocol_output() << *odometry.source_twist_sequence;
        else
            protocol_output() << "null";
        protocol_output() << ",\"frame_id\":";
        if (odometry.frame_id)
            json_string(*odometry.frame_id);
        else
            protocol_output() << "null";
        protocol_output() << ",\"child_frame_id\":";
        if (odometry.child_frame_id)
            json_string(*odometry.child_frame_id);
        else
            protocol_output() << "null";
        protocol_output() << "},\"system_resources\":{\"quality\":\"" << quality_name(state.system_resource.quality)
                          << "\",\"reading\":";
        if (state.system_resource.reading)
        {
            const auto &reading = *state.system_resource.reading;
            protocol_output() << "{\"cpu_usage_percent\":" << reading.cpu_usage_percent
                              << ",\"memory_used_bytes\":\"" << reading.memory_used_bytes
                              << "\",\"memory_total_bytes\":\"" << reading.memory_total_bytes
                              << "\",\"cpu_temperature_c\":";
            if (reading.cpu_temperature_c)
                protocol_output() << *reading.cpu_temperature_c;
            else
                protocol_output() << "null";
            protocol_output() << '}';
        }
        else
            protocol_output() << "null";
        protocol_output() << "},\"lidar\":{\"reading\":";
        if (lidar.reading)
        {
            const auto &r = *lidar.reading;
            const auto &g = r.geometry;
            protocol_output() << "{\"angle_min_rad\":" << g.angle_min_rad << ",\"angle_max_rad\":" << g.angle_max_rad << ",\"angle_increment_rad\":" << g.angle_increment_rad << ",\"range_min_m\":" << g.range_min_m << ",\"range_max_m\":" << g.range_max_m << ",\"scan_time_s\":" << g.scan_time_s << ",\"time_increment_s\":" << g.time_increment_s << ",\"ranges_m\":[";
            bool first_range = true;
            for (const auto &range : r.ranges_m)
            {
                if (!first_range)
                    protocol_output() << ',';
                first_range = false;
                if (range)
                    protocol_output() << *range;
                else
                    protocol_output() << "null";
            }
            protocol_output() << "]}";
        }
        else
            protocol_output() << "null";
        protocol_output() << ",\"quality\":\"" << quality_name(lidar.quality) << "\",\"received_elapsed_ms\":";
        if (lidar_observation.received_elapsed)
            protocol_output() << lidar_observation.received_elapsed->count();
        else
            protocol_output() << "null";
        protocol_output() << ",\"source_stamp_ns\":";
        if (lidar.reading)
            json_string(std::to_string(lidar.reading->source_stamp_ns));
        else
            protocol_output() << "null";
        protocol_output() << ",\"source_sequence\":";
        if (lidar.reading)
            protocol_output() << lidar.reading->source_sequence;
        else
            protocol_output() << "null";
        protocol_output() << ",\"frame_id\":";
        if (lidar.reading)
            json_string(lidar.reading->frame_id);
        else
            protocol_output() << "null";
        protocol_output() << "},\"telemetry\":{\"hostname\":";
        json_string(hostname);
        protocol_output() << ",\"cpu\":\"/proc/stat\",\"memory\":\"/proc/meminfo\",\"temperature\":";
        if (temperature_path.empty())
            protocol_output() << "null";
        else
            json_string(temperature_path);
        protocol_output() << ",\"battery_topic\":";
        if (observation.supported)
            json_string("/handsfree/robot_state");
        else
            protocol_output() << "null";
        protocol_output() << ",\"odometry_pose_topic\":";
        if (odometry.supported)
            json_string("/handsfree/readonly_pose");
        else
            protocol_output() << "null";
        protocol_output() << ",\"odometry_twist_topic\":";
        if (odometry.supported)
            json_string("/handsfree/readonly_twist");
        else
            protocol_output() << "null";
        protocol_output() << ",\"lidar_topic\":";
        if (lidar_observation.supported)
            json_string("/scan");
        else
            protocol_output() << "null";
        protocol_output() << "},\"event_log\":{\"entries\":[";
        bool first{true};
        for (const auto &entry : events.entries())
        {
            if (!first)
                protocol_output() << ',';
            first = false;
            protocol_output() << "{\"elapsed_ms\":" << entry.elapsed << ",\"level\":";
            json_string(entry.level == amr::LogLevel::Info ? "Info" : entry.level == amr::LogLevel::Warning ? "Warning"
                                                                                                            : "Error");
            protocol_output() << ",\"source\":";
            json_string(entry.source);
            protocol_output() << ",\"message\":";
            json_string(entry.message);
            protocol_output() << '}';
        }
        protocol_output() << "],\"truncated\":" << events.truncated() << "}}\n"
                          << std::flush;
    }
} // namespace

int main(int argc, char **argv)
{
#ifdef AMR_WITH_ROS_TELEMETRY
    // Third-party ROS logs use stderr; the saved stdout pipe carries JSON only.
    std::ofstream protocol_file{"/dev/stdout"};
    if (!protocol_file || dup2(STDERR_FILENO, STDOUT_FILENO) < 0)
    {
        std::cerr << "cannot isolate telemetry output\n";
        return 1;
    }
    json_output = &protocol_file;
#endif

    // --samples N 仅限制本次采集数量，便于现场验证后自动退出；默认持续采集。
    std::uint64_t limit{0};
    if (argc != 1)
    {
        if (argc != 3 || std::string{argv[1]} != "--samples")
        {
            std::cerr << "usage: robot_state_agent [--samples 1..86400]\n";
            return 2;
        }
        const std::string count{argv[2]};
        if (count.empty() || count.find_first_not_of("0123456789") != std::string::npos)
            return 2;
        try
        {
            limit = std::stoull(count);
        }
        catch (const std::exception &)
        {
            return 2;
        }
        if (limit == 0 || limit > 86400)
            return 2;
    }

    char host[256]{};
    if (gethostname(host, sizeof(host) - 1) != 0)
    {
        std::cerr << "cannot identify telemetry host\n";
        return 1;
    }
    const auto temperature_path = cpu_temperature_path();
    const auto start = Clock::now();
    auto deadline = start;
    amr::RobotState state{};
    state.connected = true;
    amr::EventLog events{};
    append_event(events, Ms{0}, amr::LogLevel::Info, "robot", "Read-only Linux telemetry started");
    BatteryObservation observation{};
    OdometryObservation odometry{};
    amr::LidarState lidar{};
    LidarObservation lidar_observation{};
#ifdef AMR_WITH_ROS_TELEMETRY
    RosTelemetrySubscription ros_telemetry{state, observation, odometry, lidar, lidar_observation, start, events};
#endif
    auto previous_battery_quality = state.battery.quality;
    auto previous_odometry_quality = state.odometry.quality;
    auto previous_lidar_quality = lidar.quality;
    amr::CpuTicks previous{};
    bool have_previous = read_cpu(previous);
    std::uint64_t sequence{0};

    while (limit == 0 || sequence < limit)
    {
        deadline += Ms{500};
        std::this_thread::sleep_until(deadline);
#ifdef AMR_WITH_ROS_TELEMETRY
        ros_telemetry.poll();
#endif
        const auto now = Clock::now();
        const auto elapsed = std::chrono::duration_cast<Ms>(now - start);
        const auto before_quality = state.system_resource.quality;
        amr::CpuTicks current{};
        amr::MemoryBytes memory{};
        const bool have_current = read_cpu(current);
        double percent{0.0};
        if (have_current && have_previous && amr::cpu_usage(previous, current, percent) &&
            amr::parse_memory_info(read_text("/proc/meminfo"), memory))
        {
            amr::update_system_resources_reading(state.system_resource,
                                                 {percent, memory.used, memory.total, read_temperature(temperature_path)}, now);
        }
        have_previous = have_current;
        if (have_current)
            previous = current;
        amr::refresh_system_resources_quality(state.system_resource, now, Ms{1500});
        if (state.system_resource.quality != before_quality)
        {
            append_event(events, elapsed,
                         state.system_resource.quality == amr::DataQuality::Valid ? amr::LogLevel::Info : amr::LogLevel::Warning,
                         "system_resources", std::string{quality_name(before_quality)} + " -> " + quality_name(state.system_resource.quality));
        }
        amr::refresh_battery_quality(state.battery, now, Ms{3000});
        if (state.battery.quality != previous_battery_quality)
        {
            append_event(events, elapsed,
                         state.battery.quality == amr::DataQuality::Valid ? amr::LogLevel::Info : amr::LogLevel::Warning,
                         "battery", std::string{quality_name(previous_battery_quality)} + " -> " + quality_name(state.battery.quality));
            previous_battery_quality = state.battery.quality;
        }
        amr::refresh_odometry_quality(state.odometry, now, Ms{1500});
        if (state.odometry.quality != previous_odometry_quality)
        {
            append_event(events, elapsed,
                         state.odometry.quality == amr::DataQuality::Valid ? amr::LogLevel::Info : amr::LogLevel::Warning,
                         "odometry", std::string{quality_name(previous_odometry_quality)} + " -> " + quality_name(state.odometry.quality));
            previous_odometry_quality = state.odometry.quality;
        }
        amr::refresh_lidar_quality(lidar, now, Ms{1500});
        if (lidar.quality != previous_lidar_quality)
        {
            append_event(events, elapsed, lidar.quality == amr::DataQuality::Valid ? amr::LogLevel::Info : amr::LogLevel::Warning, "lidar", std::string{quality_name(previous_lidar_quality)} + " -> " + quality_name(lidar.quality));
            previous_lidar_quality = lidar.quality;
        }
        write_frame(state, elapsed, ++sequence, host, temperature_path, events, observation, odometry, lidar, lidar_observation);
        if (!protocol_output())
            return 1;
        // 长时间阻塞后不追发积压帧；下一份状态仍等待一个采样间隔。
        if (Clock::now() > deadline + Ms{500})
            deadline = Clock::now();
    }
}
