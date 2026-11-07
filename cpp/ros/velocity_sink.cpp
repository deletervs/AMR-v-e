#include "amr/ros_velocity_sink.hpp"
#include <ros/ros.h>
#include <ros/master.h>
#include <geometry_msgs/Twist.h>
#include <XmlRpcValue.h>
#include <atomic>
#include <chrono>
#include <cerrno>
#include <cstdlib>
#include <fcntl.h>
#include <stdexcept>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>
namespace {
std::uint64_t now_ms(){return std::chrono::duration_cast<std::chrono::milliseconds>(
    std::chrono::steady_clock::now().time_since_epoch()).count();}
struct ProjectLock {
    int fd{-1};
    ProjectLock() {
        const char *d="/home/handsfree/.local/state/rs-executor";
        if(mkdir(d,0700)!=0 && errno!=EEXIST)throw std::runtime_error("Executor directory");
        struct stat s{};
        if(lstat(d,&s)!=0 || !S_ISDIR(s.st_mode) || s.st_uid!=getuid() || (s.st_mode&0777)!=0700)
            throw std::runtime_error("Executor directory identity");
        char *resolved=realpath(d,nullptr);
        const bool same=resolved && std::string{resolved}==d;free(resolved);
        if(!same)throw std::runtime_error("Redirected executor directory");
        fd=open("/home/handsfree/.local/state/rs-executor/output.lock",O_CREAT|O_RDWR|O_NOFOLLOW|O_CLOEXEC,0600);
        if(fd<0)throw std::runtime_error("Executor lock");
        if(fstat(fd,&s)!=0 || !S_ISREG(s.st_mode) || s.st_uid!=getuid() ||
            (s.st_mode&0777)!=0600 || flock(fd,LOCK_EX|LOCK_NB)!=0) {
            close(fd);fd=-1;throw std::runtime_error("Executor output busy or unsafe");
        }
    }
    ~ProjectLock(){if(fd>=0)close(fd);}
};
class Sink final:public amr::RosVelocitySink {
    ProjectLock lock_;ros::NodeHandle node_;ros::Publisher publisher_;
    std::string driver_;std::uint64_t authorized_until_;std::atomic<std::uint64_t> permit_until_{0};
    bool graph(bool advertised) {
        XmlRpc::XmlRpcValue args,result,payload;args[0]=ros::this_node::getName();
        if(!ros::master::execute("getSystemState",args,result,payload,false) ||
            payload.getType()!=XmlRpc::XmlRpcValue::TypeArray || payload.size()!=3)return false;
        bool driver=false,self=false;
        for(int kind=0;kind<2;++kind) {
            if(payload[kind].getType()!=XmlRpc::XmlRpcValue::TypeArray)return false;
            for(int i=0;i<payload[kind].size();++i) {
                auto &row=payload[kind][i];
                if(row.getType()!=XmlRpc::XmlRpcValue::TypeArray || row.size()!=2 ||
                    row[0].getType()!=XmlRpc::XmlRpcValue::TypeString ||
                    row[1].getType()!=XmlRpc::XmlRpcValue::TypeArray)return false;
                if(static_cast<std::string>(row[0])!="/cmd_vel")continue;
                for(int j=0;j<row[1].size();++j) {
                    if(row[1][j].getType()!=XmlRpc::XmlRpcValue::TypeString)return false;
                    const std::string name=static_cast<std::string>(row[1][j]);
                    if(kind==0) {
                        if(!advertised || name!=ros::this_node::getName())return false;
                        self=true;
                    }else {if(name!=driver_)return false;driver=true;}
                }
            }
        }
        return driver && (!advertised || self);
    }
public:
    explicit Sink(std::string driver,std::uint64_t until):driver_{std::move(driver)},authorized_until_{until} {
        if(driver_.empty() || driver_[0]!='/')throw std::invalid_argument("Explicit vendor driver node");
        if(!graph(false))throw std::runtime_error("Missing driver or competing velocity publisher");
        publisher_=node_.advertise<geometry_msgs::Twist>("/cmd_vel",1,false);
    }
    void renew_exclusivity() override {
        const auto started=now_ms();
        bool exclusive=false;
        try {exclusive=ros::ok() && graph(true);} catch(...) {exclusive=false;}
        permit_until_.store(exclusive?started+250:0); // Previous lease expires while a slow query is outstanding.
    }
    bool ready() const noexcept override {
        try {return now_ms()<authorized_until_ && ros::ok() && now_ms()<permit_until_.load() && publisher_.getNumSubscribers()==1;}
        catch(...) {return false;}
    }
    bool write(const amr::VelocityTarget &target) noexcept override {
        try {
            const auto now=now_ms();
            if(!ros::ok() || now>=permit_until_.load() || publisher_.getNumSubscribers()!=1)return false;
            if((target.linear_mps!=0 || target.angular_radps!=0) && now>=authorized_until_)return false;
            // A final zero may follow permit expiry while graph ownership is still fresh.
            geometry_msgs::Twist m;m.linear.x=target.linear_mps;m.angular.z=target.angular_radps;
            publisher_.publish(m);return true; // ROS enqueue, not measured motor feedback.
        }catch(...){return false;}
    }
};
}
namespace amr {
std::unique_ptr<RosVelocitySink> make_ros_velocity_sink(const std::string &driver,std::uint64_t until) {
    if(!ros::isInitialized())throw std::invalid_argument("Explicit ROS initialization required");
    return std::unique_ptr<RosVelocitySink>{new Sink{driver,until}};
}
}
