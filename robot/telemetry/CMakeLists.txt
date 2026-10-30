cmake_minimum_required(VERSION 3.10)
project(RSTelemetry LANGUAGES CXX)
# Standalone read-only collector build for Ubuntu18.04/Jetson CMake3.10.
if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux")
    message(FATAL_ERROR "This entry point builds the Linux read-only collector; use the root project on PC")
endif()
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
get_filename_component(RS_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../.." ABSOLUTE)
find_package(Threads REQUIRED)
add_library(amr_telemetry STATIC
    ${RS_ROOT}/cpp/src/battery_state.cpp
    ${RS_ROOT}/cpp/src/battery_source.cpp
    ${RS_ROOT}/cpp/src/data_freshness.cpp
    ${RS_ROOT}/cpp/src/system_resources.cpp
    ${RS_ROOT}/cpp/src/event_log.cpp
    ${RS_ROOT}/cpp/src/linux_resources.cpp)
target_include_directories(amr_telemetry PUBLIC ${RS_ROOT}/cpp/include)
target_compile_options(amr_telemetry PRIVATE -Wall -Wextra -Wpedantic)
add_executable(robot_state_agent ${RS_ROOT}/cpp/apps/robot_state_agent.cpp)
target_link_libraries(robot_state_agent PRIVATE amr_telemetry Threads::Threads)
target_compile_options(robot_state_agent PRIVATE -Wall -Wextra -Wpedantic)
option(RS_WITH_ROS_BATTERY "Read-only ROS1 battery subscription" OFF)
if(RS_WITH_ROS_BATTERY)
    find_package(catkin REQUIRED COMPONENTS roscpp handsfree_msgs)
    target_compile_definitions(robot_state_agent PRIVATE AMR_WITH_ROS_BATTERY)
    target_include_directories(robot_state_agent PRIVATE ${catkin_INCLUDE_DIRS})
    target_link_libraries(robot_state_agent PRIVATE ${catkin_LIBRARIES})
    # SSH does not source ROS setup. DT_RPATH also resolves transitive ROS libraries.
    set_property(TARGET robot_state_agent APPEND_STRING PROPERTY LINK_FLAGS " -Wl,--disable-new-dtags")
    set_property(TARGET robot_state_agent PROPERTY BUILD_RPATH "${catkin_LIBRARY_DIRS}")
endif()
enable_testing()
foreach(name IN ITEMS battery_state battery_source data_freshness system_resources event_log linux_resources)
    add_executable(test_${name} ${RS_ROOT}/cpp/tests/test_${name}.cpp)
    target_link_libraries(test_${name} PRIVATE amr_telemetry)
    target_compile_options(test_${name} PRIVATE -Wall -Wextra -Wpedantic -UNDEBUG)
    add_test(NAME test_${name} COMMAND test_${name})
endforeach()
