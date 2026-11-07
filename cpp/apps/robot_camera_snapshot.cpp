// Read-only native camera probe. No ROS publisher, command input or motion interface.
#include <chrono>
#include <fstream>
#include <iostream>
#include <opencv2/core.hpp>
#include <opencv2/imgcodecs.hpp>
#include <opencv2/imgproc.hpp>
#include <opencv2/videoio.hpp>
#include <string>
#include <vector>

int main(int argc, char **argv)
{
    if (argc != 3 || std::string{argv[1]} != "--output")
    {
        std::cerr << "usage: robot_camera_snapshot --output /absolute/path/snapshot.jpg\n";
        return 2;
    }
    const std::string output{argv[2]};
    if (output.empty() || output[0] != '/' || output.size() < 5 || output.substr(output.size() - 4) != ".jpg")
    {
        std::cerr << "output must be an absolute .jpg path\n";
        return 2;
    }
    std::ifstream device_name{"/sys/class/video4linux/video2/name"};
    std::string name;
    std::getline(device_name, name);
    if (name != "Dabai DC1")
    {
        std::cerr << "/dev/video2 is not the verified Dabai DC1 camera\n";
        return 3;
    }
    try
    {
        // OpenCV 3.2 selects V4L2 through its backend index offset.
        cv::VideoCapture capture{cv::CAP_V4L2 + 2};
        if (!capture.isOpened())
        {
            std::cerr << "cannot open verified camera\n";
            return 3;
        }
        const bool width_requested = capture.set(cv::CAP_PROP_FRAME_WIDTH, 640);
        const bool height_requested = capture.set(cv::CAP_PROP_FRAME_HEIGHT, 480);
        cv::Mat frame;
        for (int i = 0; i < 8; ++i)
        {
            if (!capture.read(frame) || frame.empty())
            {
                std::cerr << "camera returned no image\n";
                return 4;
            }
        }
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
        capture.release();
        std::ofstream file{output, std::ios::binary | std::ios::trunc};
        file.write(reinterpret_cast<const char *>(jpeg.data()), static_cast<std::streamsize>(jpeg.size()));
        file.close();
        if (!file)
        {
            std::cerr << "cannot save snapshot\n";
            return 6;
        }
        const auto unix_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(received.time_since_epoch()).count();
        std::cout << std::boolalpha
                  << "{\"source\":\"robot_camera\",\"device\":\"/dev/video2\",\"device_name\":\"Dabai DC1\",\"backend\":\"V4L2\",\"opencv\":\"" << CV_VERSION
                  << "\",\"input_width\":" << frame.cols << ",\"input_height\":" << frame.rows << ",\"input_channels\":" << frame.channels()
                  << ",\"requested_640x480_accepted\":" << (width_requested && height_requested)
                  << ",\"width\":320,\"height\":240,\"jpeg_bytes\":" << jpeg.size() << ",\"jpeg_quality\":" << used_quality
                  << ",\"host_received_unix_ns\":\"" << unix_ns << "\",\"timestamp_basis\":\"host after successful read; not sensor acquisition\",\"motion_enabled\":false}\n";
        return 0;
    }
    catch (const cv::Exception &error)
    {
        std::cerr << error.what() << '\n';
        return 7;
    }
}