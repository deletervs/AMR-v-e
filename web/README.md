# RS 模拟状态仪表盘

用户编写核心 C++；AI 负责模拟接入、网页、只读 HTTP 桥接、测试与文档。当前支持电池、里程计与 CPU/内存/温度，全部来自模拟程序。

## 运行

在仓库根目录先构建并通过测试，再启动：

```powershell
python tools/serve_dashboard.py
```

打开 [持续模拟](http://127.0.0.1:8081/?mode=live) 或 [离线回放](http://127.0.0.1:8081/)。
VS Code 已有个人任务时，选择“RS: 持续模拟仪表盘”；Ctrl+Shift+B 运行正式 CTest。
Ctrl+C 停止服务及它自己启动的模拟子进程。Windows 重新编译前应先停止运行中的模拟程序，避免 exe 被占用。

服务仅监听 127.0.0.1，默认端口8081，所有客户端共用一个 C++ 生产者。可用 --port 指定端口、--mock-executable 指定程序：

```bash
python3 tools/serve_dashboard.py --mock-executable .local/build/linux-debug/mock_robot_state
```

该命令形式在上一轮 Linux 基线验证过；本轮资源版本的同提交 Linux 验收尚待完成。

只看回放也可执行：

```powershell
python -m http.server 8080 --bind 127.0.0.1 --directory web
```

然后打开 [静态回放](http://127.0.0.1:8080/)。静态入口不提供持续接口。不要直接双击 HTML；无 npm、pip 包、CDN、遥测或外部字体依赖。

## 文件与数据流

| 文件 | 职责 |
| --- | --- |
| index.html / styles.css | PC与手机宽度的卡片、图形和布局 |
| app.js | 状态校验、共用显示、单位换算和回放控制 |
| live.js | 持续轮询、连接状态与失败恢复 |
| state.ndjson | C++ --stream-test 实际生成的17帧记录 |
| ../tools/serve_dashboard.py | 管理一个模拟子进程，提供只读快照 |
| tests/test_dashboard_service.py | 协议、生命周期与实际 C++ 接入验收 |

```text
C++ 状态核心 -> mock_robot_state --stream -> stdout -> StateBuffer
  -> GET /api/state -> live.js -> 状态卡片与曲线
C++ --stream-test -> state.ndjson -> app.js -> 离线回放
```

C++ 决定每份样本的质量。网页只换算显示单位，不用网页时间重算传感器时效。
位置/朝向相对局部 odom 原点；速度采用车体坐标。yaw 同时显示度和原始弧度。
系统资源显示整机 CPU 百分比、已用/总内存、内存百分比、可选温度，以及独立样本质量。

## 接口与字段

本轮接口升级为 **api_version=3**。更新后先停止旧服务、重新构建，再启动；网页拒绝旧接口与缺字段旧帧。

GET /api/state 返回 stream 与 state。stream.status 为 starting/live/stale/stopped/error；HTTP 200 不等于上游可用。
stream.id 标识服务会话，sequence 为已接收有效帧数，age_ms 来自服务本机单调时钟。
2秒没有新帧时传输过期；生产者退出/错误时立即不可用。页面清空所有当前值，API保留末帧仅供诊断。
每次请求结束约500ms后继续轮询，超时1500ms；持续页面最多保留120份曲线样本。接口仅允许固定资源与只读请求。

帧保留 source=mock、严格递增的 sim_time_ms、布尔 connected、motion_enabled=false、battery 和 odometry。
odometry.reading 为 null 或包含 x_m/y_m/yaw_rad/linear_x_mps/linear_y_mps/angular_z_radps 六个有限数字的完整对象。
新增资源字段示例：

```json
"system_resources": {
  "reading": {
    "cpu_usage_percent": 35,
    "memory_used_bytes": "2147483648",
    "memory_total_bytes": "8589934592",
    "cpu_temperature_c": 52.5
  },
  "quality": "Valid"
}
```

资源约束：

- CPU是有限数字，范围0到100，包含两端；布尔值不当成数字。
- 内存使用规范的十进制字符串保存uint64字节数，避免JavaScript数字精度损失；无符号、无多余前导零，最大18446744073709551615。总量大于0，用量不超过总量。页面用BigInt校验和计算比例，再换算IEC单位。
- 温度字段必须存在，可为null；提供时必须有限。0和负数允许；null显示“未提供”，不是0摄氏度。
- quality沿用Unknown/Valid/Stale/Fault/Unsupported。Valid/Stale必须有整份reading；温度仍可为null。没有整份样本显示“—”。
- Stale保留旧数值并标记历史；单项资源Unsupported不会让其它传感器变成不可用。传输失效则清空全部当前值。

## 模拟场景与回放

每6秒循环：0秒无数据，1秒收到样本，2秒过期，3秒里程计恢复，3.5秒资源恢复，4秒电池恢复。
资源初始为0% CPU、0已用内存、未提供温度；3.5秒演示100% CPU、用满内存、0摄氏度；4秒演示有效样本未提供温度；4.5秒后提供普通温度读数。
这模拟不同通道独立恢复，不读取PC或服务器的真实资源。

回放支持暂停、滑块/键盘逐帧、重播和原始JSON。更新记录前先构建，再执行：

```powershell
$env:Path = "C:\msys64\ucrt64\bin;" + $env:Path
.\.local\build\cmake-debug\mock_robot_state.exe --stream-test | Set-Content -Encoding utf8 web\state.ndjson
```

记录应为17行；页面兼容UTF-8 BOM。回放结束后明确停在历史记录。

## 验收范围

```powershell
python web/tests/test_dashboard_service.py
```

服务测试独立于7项CTest；可用 --mock-executable 选择构建产物。
2026-10-21 Windows当前工作区：CTest7/7、服务21/21通过；Edge155实际检查17帧、资源边界/缺值/单位、两客户端、旧协议拒绝、断流/超时恢复与1440/768/390/320宽度，截图已检查，测试进程已清理。
本轮资源版本的Linux同提交验收仍待完成；旧af360ea基线的Linux6/6和服务15/15不能替代本轮验证。
真实手机/局域网、真实设备、日志和远程认证尚未验收。
