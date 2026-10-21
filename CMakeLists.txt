#[[
本文件负责说明：
哪些源码组成库、哪些文件生成程序、程序依赖什么，以及哪些程序属于测试。
以后新增实现文件，主要在这里维护构建关系
]]


cmake_minimum_required(VERSION 3.16) # 声明 cmake 最低版本
project(RS LANGUAGES CXX) # 项目名, 语言用 C++

# 设置默认的编译要求
set(CMAKE_CXX_STANDARD 17) 
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# 添加指定的源文件(src)到项目(amr_state), 组成静态链接库
add_library(
    amr_state STATIC # 创建名为 amr_state 的静态库
    cpp/src/battery_state.cpp
    cpp/src/data_freshness.cpp
    cpp/src/odometry_state.cpp
    cpp/src/system_resources.cpp
)

# 设置公共头文件(.hpp)的位置, 给编译器看
target_include_directories(
    amr_state PUBLIC # 设置目标, 和传播范围
    "${CMAKE_CURRENT_SOURCE_DIR}/cpp/include" # 设置位置, CMAKE_CURRENT_SOURCE_DIR 是当前 CMake 的位置. 还有一点, 这样写的位置后续引用.hpp就要 include "amr/xxx.hpp", 带上子目录
)

# 声明这个静态库的使用者的 C++ 版本要求
target_compile_features(amr_state PUBLIC cxx_std_17)

# 根据编译器选择参数: RS_WARNING_OPTIONS 和 RS_TEST_ASSERT_OPTIONS 后面要用的变量
if(MSVC) # MSVC 是微软的编译器
    set(RS_WARNING_OPTIONS /W4)
    set(RS_TEST_ASSERT_OPTIONS /UNDEBUG)
else()
    set(RS_WARNING_OPTIONS -Wall -Wextra -Wpedantic)
    set(RS_TEST_ASSERT_OPTIONS -UNDEBUG)
endif()

# 保存 RS_WARNING_OPTIONS 的参数到静态库
target_compile_options(amr_state PRIVATE ${RS_WARNING_OPTIONS})

#[[
----该部分为演示----
]]

find_package(Threads REQUIRED) # Threads 是查找系统线程支持的 CMake 模块

# 构建模拟状态演示程序
add_executable(
    mock_robot_state
    cpp/apps/mock_robot_state.cpp
)

# 使用已有的状态处理库 amr_state 及其公共头文件
target_link_libraries(mock_robot_state PRIVATE amr_state Threads::Threads)

# 为模拟程序启用编译警告
target_compile_options(mock_robot_state PRIVATE ${RS_WARNING_OPTIONS})

#[[
----该部分为测试----
]]

# 开启测试登记功能
enable_testing()

# 创建测试程序, 
foreach(test_name IN ITEMS robot_state data_freshness battery_state odometry_state system_resources)
    add_executable( # 创建程序. 目标程序命名和源码位置
        test_${test_name}
        cpp/tests/test_${test_name}.cpp
    )
    target_link_libraries(test_${test_name} PRIVATE amr_state) # 目标程序链接的静态库
    target_compile_options( # 为目标程序设置警告和断言的参数
        test_${test_name} PRIVATE
        ${RS_WARNING_OPTIONS}
        ${RS_TEST_ASSERT_OPTIONS}
    )
    add_test(NAME test_${test_name} COMMAND test_${test_name}) # 登记该程序为测试
endforeach()

find_package(Python3 3.8 REQUIRED COMPONENTS Interpreter)

add_test(
    NAME test_mock_robot_state_output
    COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/cpp/tests/test_mock_robot_state_output.py"
        "$<TARGET_FILE:mock_robot_state>"
)

add_test(
    NAME test_mock_robot_stream
    COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/cpp/tests/test_mock_robot_stream.py"
        "$<TARGET_FILE:mock_robot_state>"
)


#[[
Note: 
1. STATIC 表示静态库: 先把添加到库里的.cpp源文件编译成目标文件, 再打包供程序链接
2. 传播类型:        是否自己用      继承的程序用
    PRIVATE             1               0
    PUBLIC              1               1
    INTERFACE           0               1
3. 
]]