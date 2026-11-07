// Read-only V4L2 NDJSON camera source. No ROS, stdin commands or motion interface.
#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <mutex>
#include <opencv2/core.hpp>
#include <opencv2/imgcodecs.hpp>
#include <opencv2/imgproc.hpp>
#include <opencv2/videoio.hpp>
#include <string>
#include <thread>
#include <vector>

// OpenCV3.2 read/open may block. The OS closes this process's descriptors on exit.
class Deadline
{
    std::mutex mutex_;
    std::condition_variable cv_;
    bool done_{false};
    std::chrono::steady_clock::time_point expires_;
    std::thread thread_;

  public:
    explicit Deadline(int seconds) : expires_(std::chrono::steady_clock::now() + std::chrono::seconds{seconds}),
                                    thread_([this]
                                            {
        std::unique_lock<std::mutex> lock{mutex_};
        while (!done_)
        {
            cv_.wait_until(lock, expires_);
            if (!done_ && std::chrono::steady_clock::now() >= expires_)
                std::_Exit(8);
        }
    }) {}
    void progress()
    {
        {
            std::lock_guard<std::mutex> lock{mutex_};
            expires_ = std::chrono::steady_clock::now() + std::chrono::seconds{5};
        }
        cv_.notify_one();
    }
    ~Deadline()
    {
        {
            std::lock_guard<std::mutex> lock{mutex_};
            done_ = true;
        }
        cv_.notify_one();
        thread_.join();
    }
};

std::string base64(const std::vector<unsigned char> &data)
{
    static constexpr char alphabet[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string out;
    out.reserve((data.size() + 2) / 3 * 4);
    for (std::size_t i = 0; i < data.size(); i += 3)
    {
        const unsigned a = data[i];
        const unsigned b = i + 1 < data.size() ? data[i + 1] : 0;
        const unsigned c = i + 2 < data.size() ? data[i + 2] : 0;
        out += alphabet[a >> 2];
        out += alphabet[((a & 3) << 4) | (b >> 4)];
        out += i + 1 < data.size() ? alphabet[((b & 15) << 2) | (c >> 6)] : '=';
        out += i + 2 < data.size() ? alphabet[c & 63] : '=';
    }
    return out;
}

int main(int argc, char **argv)
{
    int seconds{60};
    const bool continuous = argc == 2 && std::string{argv[1]} == "--continuous";
    if (argc != 1 && !continuous)
    {
        if (argc != 3 || std::string{argv[1]} != "--duration-seconds")
            return 2;
        const std::string value{argv[2]};
        if (value.empty() || value.size() > 2 || value.find_first_not_of("0123456789") != std::string::npos)
            return 2;
        seconds = std::stoi(value);
        if (seconds < 1 || seconds > 60)
            return 2;
    }
    std::ifstream device_name{"/sys/class/video4linux/video2/name"};
    std::string name;
    std::getline(device_name, name);
    if (name != "Dabai DC1")
    {
        std::cerr << "verified camera unavailable\n";
        return 3;
    }
    Deadline deadline{continuous ? 15 : seconds + 5};
    try
    {
        cv::VideoCapture capture{cv::CAP_V4L2 + 2};
        if (!capture.isOpened())
            return 3;
        capture.set(cv::CAP_PROP_FRAME_WIDTH, 640);
        capture.set(cv::CAP_PROP_FRAME_HEIGHT, 480);
        cv::Mat frame;
        for (int i = 0; i < 8; ++i)
            if (!capture.read(frame) || frame.empty())
                return 4;
        const auto start = std::chrono::steady_clock::now();
        auto next_emit = start;
        unsigned sequence{0};
        while (continuous || std::chrono::steady_clock::now() - start < std::chrono::seconds{seconds})
        {
            // Drain continuously; sleeping between reads can return queued old frames.
            if (!capture.read(frame) || frame.empty())
                return 4;
            const auto now = std::chrono::steady_clock::now();
            if (continuous)
                deadline.progress();
            if (!continuous && now - start >= std::chrono::seconds{seconds})
                break;
            if (now < next_emit)
                continue;
            const auto received = std::chrono::system_clock::now();
            if (frame.cols < 1 || frame.rows < 1 || frame.cols > 1920 || frame.rows > 1080 || frame.depth() != CV_8U)
            {
                std::cerr << "unsupported camera dimensions or depth\n";
                return 4;
            }
            cv::Mat bgr;
            if (frame.channels() == 3)
                bgr = frame;
            else if (frame.channels() == 1)
                cv::cvtColor(frame, bgr, cv::COLOR_GRAY2BGR);
            else if (frame.channels() == 4)
                cv::cvtColor(frame, bgr, cv::COLOR_BGRA2BGR);
            else
            {
                std::cerr << "unsupported camera channels\n";
                return 4;
            }
            cv::Mat preview;
            cv::resize(bgr, preview, cv::Size{320, 240}, 0, 0, cv::INTER_AREA);
            std::vector<unsigned char> jpeg;
            int used_quality{0};
            for (const int quality : {65, 45, 25, 10})
            {
                if (cv::imencode(".jpg", preview, jpeg, {cv::IMWRITE_JPEG_QUALITY, quality}) && jpeg.size() <= 48 * 1024)
                {
                    used_quality = quality;
                    break;
                }
            }
            if (!used_quality || jpeg.size() < 4 || jpeg.front() != 0xff || jpeg[1] != 0xd8 || jpeg[jpeg.size() - 2] != 0xff || jpeg.back() != 0xd9)
            {
                std::cerr << "JPEG encoding failed or exceeded 48KiB\n";
                return 5;
            }

            const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(now - start).count();
            const auto unix_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(received.time_since_epoch()).count();
            std::cout << "{\"schema\":\"rs-camera-v1\",\"source\":\"robot_camera\",\"device\":\"/dev/video2\",\"device_name\":\"Dabai DC1\",\"seq\":" << ++sequence
                      << ",\"elapsed_ms\":" << elapsed << ",\"width\":320,\"height\":240,\"jpeg_bytes\":" << jpeg.size()
                      << ",\"host_received_unix_ns\":\"" << unix_ns << "\",\"timestamp_basis\":\"host after successful read; not sensor acquisition\",\"motion_enabled\":false,\"jpeg_b64\":\"" << base64(jpeg) << "\"}\n"
                      << std::flush;
            if (!std::cout)
                return 6;
            next_emit = now + std::chrono::milliseconds{500};
        }
        capture.release();
        return 0;
    }
    catch (const cv::Exception &error)
    {
        std::cerr << error.what() << '\n';
        return 7;
    }
}
