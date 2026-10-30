# RS 只读状态仪表盘

用户编写核心 C++；AI 负责接入、网页、只读 HTTP 桥接、测试与文档。默认仍为模拟程序；显式机器人来源读取 Jetson 本机 CPU/内存/可选 CPU 温度，M2-002 按能力接入真实 ROS 电压。里程计和电机状态尚未接入。网页暂作主线的简单验证入口。

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

2026-10-21已在Ubuntu20.04/GCC9.3/Python3.8的同提交ca9259d副本验证该启动方式。

只看回放也可执行：

```powershell
python -m http.server 8080 --bind 127.0.0.1 --directory web
```

然后打开 [静态回放](http://127.0.0.1:8080/)。静态入口不提供持续接口。不要直接双击 HTML；无 npm、pip 包、CDN、遥测或外部字体依赖。

### 机器人只读采集

主线完成机器人端编译并给出实际路径后，手动启动：

```powershell
python tools/serve_dashboard.py --robot-target robot-user@robot-host --jump-host robot-jump --robot-executable /tmp/REPLACE_WITH_VERIFIED_DIRECTORY/robot_state_agent --known-hosts PATH_TO_VERIFIED_KNOWN_HOSTS --port 8081
```

示例中的主机、账号、跳板和 known_hosts 均为占位值，需替换为现场核验的配置；`/tmp/REPLACE_WITH_VERIFIED_DIRECTORY/robot_state_agent` 也必须替换为已构建产物的实际路径。
打开 [机器人遥测](http://127.0.0.1:8081/?mode=live&source=robot)。`source` 缺省为 `mock`，页面与服务来源不一致会拒绝显示。
回放入口始终加载本地模拟记录，机器人模式请使用上述显式链接。

机器人程序路径必须显式给出、为绝对路径且文件名为 `robot_state_agent`。只执行该采集程序，不接收远程命令或额外参数。
桥接使用固定 OpenSSH 参数列表，stdin 为 DEVNULL，stdout 为 NDJSON，stderr 保留终端诊断；两跳均使用 BatchMode、严格主机检查、显式公开 known_hosts 和单次连接。固定的本地 ProxyCommand 让这些选项也应用于跳板；远程路径按 POSIX shell 规则引用。
不安装、部署、启动驱动或重试认证。`connected` 只表示采集进程存活；`motion_enabled=false` 只表示此入口无运动能力，不能证明电机禁用。
Ctrl+C 清理本服务拥有的本地 SSH 子进程；没有自动重连 SSH 生产者。采集进程恢复后重新启动服务会生成新 stream.id。

## 文件与数据流

| 文件 | 职责 |
| --- | --- |
| index.html / styles.css | PC与手机宽度的卡片、图形和布局 |
| app.js | 状态校验、共用显示、单位换算和回放控制 |
| live.js | 持续轮询、连接状态与失败恢复 |
| state.ndjson | C++ --stream-test 实际生成的17帧记录 |
| ../tools/serve_dashboard.py | 管理一个显式来源的子进程，提供只读快照 |
| tests/test_dashboard_service.py | 协议、生命周期与实际 C++ 接入验收 |

```text
C++ 状态核心 -> mock_robot_state --stream -> stdout -> StateBuffer
  -> GET /api/state -> live.js -> 状态卡片与曲线
Jetson robot_state_agent -> SSH 经跳板 -> stdout -> StateBuffer(expected_source=robot)
  -> GET /api/state -> live.js (?mode=live&source=robot)
C++ --stream-test -> state.ndjson -> app.js -> 离线回放
```

C++ 决定每份样本的质量。网页只换算显示单位，不用网页时间重算传感器时效。
位置/朝向相对局部 odom 原点；速度采用车体坐标。yaw 同时显示度和原始弧度。
系统资源显示整机 CPU 百分比、已用/总内存、内存百分比、可选温度，以及独立样本质量。

## 接口与字段

本轮接口升级为 **api_version=6**。更新后先停止旧服务再启动；网页拒绝 API 5 及更早版本。

GET /api/state 返回 stream 与 state。stream.status 为 starting/live/stale/stopped/error；HTTP 200 不等于上游可用。
stream.id 标识服务会话，stream.source 为显式选择的 mock 或 robot，stream.sequence 为已接收有效帧数，age_ms 来自服务本机单调时钟。
2秒没有新帧时传输过期；生产者退出/错误时立即不可用。页面清空所有当前值，保留已验证事件并标记“历史 · 已断流”；新 stream.id（包括 starting）清空旧历史。API保留末帧仅供诊断。
每次请求结束约500ms后继续轮询，超时1500ms；持续页面最多保留120份曲线样本。重复轮询不刷新服务接收时间，客户端也用单调时间计算同一帧的年龄；达到2秒就清空当前值。断流后同一旧序号不能恢复显示，必须收到新样本或新 stream.id。接口仅允许固定资源与只读请求。

模拟帧保留 source=mock、严格递增的 sim_time_ms、布尔 connected、motion_enabled=false、battery 和 odometry。
机器人帧使用 source=robot、非负且严格递增的 elapsed_ms、正整数且严格递增的 sequence；不允许 sim_time_ms。机器人 sequence 是采集端序号，独立于服务接收计数。服务默认 expected_source=mock，机器人必须显式选择，不接受未知或同流混合来源。
机器人 odometry 固定为 `{reading:null,quality:"Unknown"}`；不得用假值填充未接入通道。可附带 telemetry 元数据说明 hostname、CPU/内存/温度的真实读取路径，原样保留在原始 JSON。

新机器人生产者总发 `capabilities:{battery:bool}`，battery 为严格布尔值。未带 capabilities 的旧 M2-001 帧仍可用，但 battery 必须精确为 `{voltage_v:null,quality:"Unknown"}`，显示“电压未接入”。模拟帧合同与17帧回放保持原样。

有 capabilities 时 battery 包含 `voltage_v`、`quality`、`received_elapsed_ms`、`source_stamp_ns`、`source_time_raw`。battery 能力为 false 时只能 Unknown/null，三项元数据都必须存在且为 null。能力为 true 而暂无电压时，元数据也全部为 null，quality 不能是 Valid/Stale；Unknown 显示“等待首个有效电压”。有电压时：

- voltage_v 是有限非负数，0 V 允许；质量仍由 C++ 决定，Stale 保留历史值，不影响有效资源读数。
- received_elapsed_ms 是采集程序 steady_clock 接收到最近一次有效电压样本的相对启动毫秒数，为非负 JS 安全整数且不晚于帧 elapsed_ms；重复打印旧电压不续期。
- source_stamp_ns 是 1..18446744073709551615 的规范无符号十进制字符串，校验与显示保持完整精度。它表示 ROS 驱动主机接收/解码后的 ROS 时间，不能视为 MCU 采样时间，也不用于跨时钟判断新鲜度。
- source_time_raw 是有限正数，原样展示 MCU 原始计时，单位尚未核验。
- 同一流的收包时间不得回退；同一收包时间的电压与三项元数据不得突变，质量可变化。收到新样本时允许 ROS 时钟回拨和 MCU 计时重置。空样本不会清除这项防回退记录；新 stream.id 会清空。

电压3秒无新样本时，由 C++ 标记 Stale；状态流仍可每500ms持续。服务/网页只独立判断整体传输是否过期。电压卡片显示本机接收时间、完整 ROS 时间字符串和 MCU 原始计时；整流断开时清空当前数值及元数据，保留已验证事件历史和末帧原始 JSON 供诊断。
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

事件字段为 `event_log={entries:[{elapsed_ms,level,source,message}],truncated}`。每帧包含完整的最近100条，按旧到新；页面反向显示。时间与帧时间同基准（模拟 sim_time_ms / 机器人 elapsed_ms），为非负安全整数，可相等、不倒退、不在未来。等级为Info/Warning/Error；来源1..64、消息1..512 UTF-8字节，拒绝孤立代理项；truncated必须为bool，为true时保留100条。历史不套用DataQuality或TTL，全部自由文本使用textContent。生产者单行上限512KiB，覆盖转义后超过64KiB的合法帧。

## 模拟场景与回放

每6秒循环：0秒无数据，1秒收到样本，2秒过期，3秒里程计恢复，3.5秒资源恢复，4秒电池恢复。
资源初始为0% CPU、0已用内存、未提供温度；3.5秒演示100% CPU、用满内存、0摄氏度；4秒演示有效样本未提供温度；4.5秒后提供普通温度读数。
这模拟不同通道独立恢复，不读取PC或服务器的真实资源。事件仅在启动、连接和质量变化时追加；6秒场景重置保留历史，进程重启清空。

回放支持暂停、滑块/键盘逐帧、重播和原始JSON。更新记录前先构建，再执行：

```powershell
$env:Path = "C:\msys64\ucrt64\bin;" + $env:Path
.\.local\build\cmake-debug\mock_robot_state.exe --stream-test | Set-Content -Encoding utf8 web\state.ndjson
```

记录应为17行；页面兼容UTF-8 BOM。回放结束后明确停在历史记录。

## 验收范围

```powershell
python web/tests/test_dashboard_service.py
node web/tests/test_dashboard_client.js
```

服务和客户端测试独立于CTest；可用 --mock-executable 选择构建产物。
2026-10-27 Windows：服务29/29、客户端9/9组通过。真实C++生成17帧回放及267193字节容量/转义帧；Edge确认回放、持续流、100条截断、中文/引号/换行/HTML纯文本、断流保留及新会话starting清除。实测1440/768/390/320 CSS宽度；修正320宽度滚动条引发的横向溢出。用户要求从简，本轮作为主线验证收尾；细节和截图见本机 `.local/learning-checks/M1-005C-03/integration-20261027/web/web-result.json`。
本轮未实测Linux、物理手机、局域网、真实设备与远程认证；旧API拒绝、超时恢复等由自动回归验证，不额外声称浏览器实测。

2026-10-29 M2-001 网页适配离线验收：服务39/39、客户端14/14组通过（包含原29项/9组）；覆盖真实/模拟来源隔离、时间和序号回退、重复轮询不续期、断流清值与保留历史、新流重置、非零退出error及自有子进程清理。固定SSH参数通过离线 `ssh -G` 解析检查。本网页任务未运行机器人、未占用8081、未进行新的浏览器视觉或远程清理实测；主线负责真机最终联调。交接见 `.local/learning-checks/M2-001/web-result.json`。

2026-10-29 M2-002 网页适配离线验收：服务45/45、客户端18/18组通过；增加电池能力、完整元数据、uint64时间字符串、0 V、接收时间防回退与同次收包防篡改、资源独立有效、源时钟重置恢复及新流重置。网页任务未运行 ROS/机器人，也未做新的 Edge 视觉或真机联调；主线继续完成这些验收。交接见 `.local/learning-checks/M2-002/dashboard/web-result.json`。
