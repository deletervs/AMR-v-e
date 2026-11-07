# RS 只读状态仪表盘

用户编写核心 C++；AI 负责接入、网页、只读 HTTP 桥接、测试与文档。默认仍为模拟程序；显式机器人来源读取 Jetson 本机 CPU/内存/可选 CPU 温度，并按订阅能力接入 ROS 电压、本地里程计与 M2-004 局部雷达扫描（雷达实机首读另验）。电机状态尚未接入。网页暂作主线的简单验证入口。

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

本轮接口升级为 **api_version=8**。更新后先停止旧服务再启动；网页拒绝 API 7 及更早版本。

GET /api/state 返回 stream 与 state。stream.status 为 starting/live/stale/stopped/error；HTTP 200 不等于上游可用。
stream.id 标识服务会话，stream.source 为显式选择的 mock 或 robot，stream.sequence 为已接收有效帧数，age_ms 来自服务本机单调时钟。
2秒没有新帧时传输过期；生产者退出/错误时立即不可用。页面清空所有当前值，保留已验证事件并标记“历史 · 已断流”；新 stream.id（包括 starting）清空旧历史。API保留末帧仅供诊断。
每次请求结束约500ms后继续轮询，超时1500ms；持续页面最多保留120份曲线样本。重复轮询不刷新服务接收时间，客户端也用单调时间计算同一帧的年龄；达到2秒就清空当前值。断流后同一旧序号不能恢复显示，必须收到新样本或新 stream.id。接口仅允许固定资源与只读请求。

模拟帧保留 source=mock、严格递增的 sim_time_ms、布尔 connected、motion_enabled=false、battery 和 odometry。
机器人帧使用 source=robot、非负且严格递增的 elapsed_ms、正整数且严格递增的 sequence；不允许 sim_time_ms。机器人 sequence 是采集端序号，独立于服务接收计数。服务默认 expected_source=mock，机器人必须显式选择，不接受未知或同流混合来源。
机器人未声明 odometry 能力时，odometry 必须精确为 `{reading:null,quality:"Unknown"}`；不得用假值填充未接入通道。可附带 telemetry 元数据说明 hostname、CPU/内存/温度的真实读取路径，原样保留在原始 JSON。

新机器人生产者总发 `capabilities:{battery:bool,odometry:bool,lidar:bool}`，三项均为严格布尔值。未带 capabilities 的旧 M2-001 帧仍可用，但 battery 必须精确为 `{voltage_v:null,quality:"Unknown"}`，显示“电压未接入”。旧 M2-002 只有 battery 能力的帧仍可用，里程计显示“未接入”。模拟帧合同与17帧回放保持原样。

有 capabilities 时 battery 包含 `voltage_v`、`quality`、`received_elapsed_ms`、`source_stamp_ns`、`source_time_raw`。battery 能力为 false 时只能 Unknown/null，三项元数据都必须存在且为 null。能力为 true 而暂无电压时，元数据也全部为 null，quality 不能是 Valid/Stale；Unknown 显示“等待首个有效电压”。有电压时：

- voltage_v 是有限非负数，0 V 允许；质量仍由 C++ 决定，Stale 保留历史值，不影响有效资源读数。
- received_elapsed_ms 是采集程序 steady_clock 接收到最近一次有效电压样本的相对启动毫秒数，为非负 JS 安全整数且不晚于帧 elapsed_ms；重复打印旧电压不续期。
- source_stamp_ns 是 1..18446744073709551615 的规范无符号十进制字符串，校验与显示保持完整精度。它表示 ROS 驱动主机接收/解码后的 ROS 时间，不能视为 MCU 采样时间，也不用于跨时钟判断新鲜度。
- source_time_raw 是有限正数，原样展示 MCU 原始计时，单位尚未核验。
- 同一流的收包时间不得回退；同一收包时间的电压与三项元数据不得突变，质量可变化。收到新样本时允许 ROS 时钟回拨和 MCU 计时重置。空样本不会清除这项防回退记录；新 stream.id 会清空。

电压3秒无新样本时，由 C++ 标记 Stale；状态流仍可每500ms持续。服务/网页只独立判断整体传输是否过期。电压卡片显示本机接收时间、完整 ROS 时间字符串和 MCU 原始计时；整流断开时清空当前数值及元数据，保留已验证事件历史和末帧原始 JSON 供诊断。
声明 odometry 能力后，odometry 包含 `reading`、`quality` 与六项元数据 `received_elapsed_ms`、`source_stamp_ns`、`source_sequence`、`source_twist_sequence`、`frame_id`、`child_frame_id`：

- reading 为 null 或包含 x_m/y_m/yaw_rad/linear_x_mps/linear_y_mps/angular_z_radps 六个有限数字的完整对象。0 位置、0 速度为有效值。
- capability=false 只能 Unknown/null，全部元数据必须存在且为 null。capability=true 暂无样本时元数据也全部为 null，quality 不能 Valid/Stale；Unknown 显示“等待首个有效里程计”。
- 有 reading 时所有元数据必须完整。received_elapsed_ms 是 0..frame.elapsed_ms 的安全整数；source_stamp_ns 是 1..UINT64MAX 的规范十进制字符串，显示时不转为浮点数。两个 source sequence 是 0..UINT32MAX 整数，0 有效，且 pose 与 twist 的序号不要求相同。
- frame_id 固定 odom，child_frame_id 固定 base_link。位姿在 odom、速度在 base_link；本地里程计不是全局定位。C++ 仅输出源时间戳精确配对的完整样本，网页不配对 ROS 消息。
- 同流接收时间不回退，同次接收的六字段与元数据不能变化，quality 可变为 Stale。空样本仍保留最后一次校验基准；新 stream.id 重置。新接收样本允许 ROS 时钟回拨和两个源序号重启/回绕。
- 里程计由 C++ 独立判定时效（1500ms 等号过期），网页只显示质量；里程计 Stale 保留历史值，电压和 CPU 仍可 Valid。整个状态流不可用时清空里程计数值及元数据，保留已验证事件历史，恢复要求新的状态序号。
- telemetry 可带 odometry_pose_topic、odometry_twist_topic，各为字符串或 null。当前来源分别是 `/handsfree/readonly_pose`（geometry_msgs/PoseStamped）和 `/handsfree/readonly_twist`（geometry_msgs/TwistStamped）。源时间戳及两个 seq 完整展示，窄屏可换行。

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

2026-10-30 M2-003 API7 网页适配离线验收：服务52/52、客户端23/23组通过。实际 headless Edge 156.0.4314.50 对明确标注 OFFLINE 的人工合成 API7 夹具完成19项检查：等待→有效0值→里程计 Stale 且电压/CPU Valid→恢复，完整 uint64 时间戳及两个 uint32 源序号，390/320px 无页面或元数据横向溢出，整流 stale/stopped/error/请求失败/超时清当前数值和元数据、保留已验证历史，新状态序号恢复与新 stream.id 重置。自有浏览器与 loopback 夹具进程已退出、端口已关闭。首次浏览器验收的等待条件过早读取上一帧，已修正并保留该失败记录；没有因此修改生产逻辑。本网页任务未连接 SSH/ROS/机器人，不能作为实机里程计或运动验证。截图、夹具、SHA256、命令与限制见 `.local/learning-checks/M2-003/web-result.json`。


## M2-004 局部雷达扫描（API8）

`lidar.js` 负责雷达验证与 Canvas。旧 robot 无 lidar capability 时允许缺 lidar 或严格 `{reading:null,quality:"Unknown"}`；mock 与17帧回放保留原合同，不填造雷达点。能力声明表示订阅能力，不能证明硬件健康。

新 robot 的 lidar 必须有 reading、quality、received_elapsed_ms、source_stamp_ns、source_sequence、frame_id。无 reading 时元数据全 null，质量不能 Valid/Stale；cap=false 时还必须 Unknown。有扫描时 received_elapsed_ms 是不晚于 elapsed_ms 的非负安全整数，stamp 是非零规范 uint64 十进制字符串，seq 是 uint32（0 合法），frame_id 为1..64个 ASCII 字母/数字/下划线/横线/斜线/点。新 robot 必须输出 telemetry.lidar_topic，只能 /scan 或 null；legacy 可缺。

reading 包含 angle_min_rad、angle_max_rad、angle_increment_rad、range_min_m、range_max_m、scan_time_s、time_increment_s 和 ranges_m。全部几何数值有限，角增量非零且允许负向，距离 min>=0、max>min，时间非负。1..4096个距离只允许合法闭区间内的有限数或 null；0 仅在 min=0 时合法。跨度不超过 2π+0.001，末角容差为 max(0.0001,abs(increment)*1.05)；两时间均正时 time_increment*(N-1)<=scan_time+0.001。

同流接收时间不能倒退，同次收包的几何、距离和来源元数据不能更改，quality 可以改变。空样本保留校验基准；新 stream.id 重置。电池、里程计、雷达进度全部验证后一起提交，拒绝一项不会污染其他基准。新收包允许来源时间及计数重置。

画面以传感器局部 X 向上、Y 向左显示离散点，null 保留索引且不绘点、不连线或填充未知区域。未接入、等待首帧、格式有效但全 null、独立 Stale 历史扫描分别显示。Stale 保留历史点，不因其他通道刷新而续期；整体传输失效或请求失败清点和元数据，保留事件，新序号才可恢复。2/5/10/20 m 仅改变显示半径；超范围点裁去，计数仍是整帧有效点/总射线。画布适配实际宽度和 DPR，每次绘制重置像素，避免积累旧点。未做 TF、地图、障碍或安全判断。

离线合成 API8 Edge 验收证据另写 `.local/learning-checks/M2-004/web-result.json`；OFFLINE 截图不能作为实机证据。机器人实读和 native ROS 编译由主线验收。

2026-10-31 M2-004 API8 网页离线验收：服务59/59、客户端29/29组通过；实际 headless Edge 156.0.4314.50 的 OFFLINE 合成夹具38项断言通过，覆盖局部坐标像素、独立 Stale、全 null、4096反向射线、显示半径、DPR2、390/320px、非法帧原子拒绝、断流清点保事件与新流恢复。初次误选旧 mock 的2项失败和受限 sandbox 的 EPERM 已记录；复验全部通过，自有浏览器、夹具进程及 loopback 端口已清理。截图与准确命令见 `.local/learning-checks/M2-004/web-result.json`。本网页任务未连接机器人、SSH、ROS或模型，实机与 native ROS 编译不标通过。
## M2-005 独立只读相机

相机默认关闭，`/api/state` 继续使用 API8，四个遥测通道不依赖相机。
`/api/camera` 使用 `camera_api_version:1`，包含 `stream={id,source,status,sequence,age_ms,ttl_ms}`
及 `frame`。id 是每次服务启动的新 UUID；状态为 disabled/starting/live/stale/stopped/error。
仅 live 返回 frame；其它状态为 null。source 启用时为 robot_camera，关闭时为 null。
本机单调接收年龄 age_ms 的 TTL 为 1500 ms，等号过期；主机 read 后墙钟时间仅作元数据。

相机需当前 Python 安装 `tools/camera-requirements.txt` 的 Pillow；缺失会在启动 SSH 前拒绝启用。
本机 bundled Python 已有 Pillow 12.3.0，当前 Conda Python 没有。选择已有解码库的解释器即可。
启用参数加在已有只读 robot 命令上（仅在已获准启动真实相机时执行，路径由主线提供）：

```powershell
python tools/serve_dashboard.py --robot-target USER@HOST --robot-executable /ABS/PATH/robot_state_agent --known-hosts C:/ABS/PATH/known_hosts --robot-camera /ABS/PATH/robot_camera_stream
```

`--robot-camera` 必须与显式 robot 模式同用；仅接受绝对路径、安全 ASCII 路径段和固定文件名
robot_camera_stream，不接收设备、任意命令、shell 参数或运动输入。独立 SSH 子进程采用已有固定
jump/known-hosts 配置，不重试或自动重启；C++ 默认每 500 ms 一帧、60秒正常结束。
页面 URL 为 `/?mode=live&source=robot`，网页本身不启动相机；结束后显式重开服务才启动新相机流。
只读相机单通道可将 `--robot-executable ...` 替换为 `--camera-only`；此模式不启动
robot_state_agent或ROS，原四传感器无数据，不填充假值。仍需robot-target、known-hosts和robot-camera。

协议固定 rs-camera-v1 / robot_camera / Dabai DC1 / /dev/video2 / 320×240 / motion_enabled:false。
seq 为正安全整数，elapsed_ms 为非负安全整数，两者严格前进；首帧 seq 可因晚接收大于1。
host_received_unix_ns 是非零规范 uint64 十进制字符串。单行含换行不超过98304字节，JPEG不超过49152字节。
标准 base64 严格解码并核对长度，Pillow验证 JPEG格式、320×240尺寸及完整解码，拒绝截断图。
坏帧或EOF立即撤图并结束该接收流；stderr不混stdout。新服务UUID不继承旧图或校验基准。
源 elapsed 相对首帧与本机单调接收进度落后达到1500ms时拒绝该流，避免中途网络/接收暂停后
积压旧帧仅靠递增序号续期。初帧之前的传输延迟无法用这两个未同步时钟证明；这是接收时效检查，
不是曝光新鲜度、光学内容变化或传感器无重复帧的证明。

浏览器串行轮询、独立TTL截止定时器和异步解码代次检查防止旧回包/旧解码恢复过期图；
请求失败、超时、过期及浏览器解码失败清除图像和元数据，并要求新序号恢复。
页面离开中止请求、清定时器并回收objectURL，返回后重新等待新帧；模拟回放不请求相机接口。

2026-10-31 用户改为实机优先。本次不新增/运行合成夹具或烟雾测试；
语法、已有真实静态JPEG解码、后续实际图流浏览器结果分别记录于
`.local/learning-checks/M2-005/web-result.json`；历史静态图不作为实时证据。

2026-10-31 真实相机网页验收完成：实际 Edge 156.0.4314.50 解码真实 320×240 图流，
390/320 CSS宽度无溢出，真实生产者 SIGSTOP→TTL撤图→SIGCONT新帧恢复、页面离开资源清理、
60秒自然EOF撤图通过。最终代码另经8秒真实短流验证，修正相机卡片内边距；无pageerror。
真实PC接收暂停2秒后，源elapsed进度检查拒绝积压帧，状态error且不返回旧图。
各轮前后fuser均确认video2空闲，浏览器与自有loopback服务已关闭。仅相机通道，四传感器保持未知；
没有启动ROS或运动。按用户新规则未新增/运行合成夹具和烟雾测试，原测试保留。
初帧之前传输延迟、曝光时刻和光学重复帧仍不可证明；未实测物理手机或相机硬件拔插。

2026-10-31 真实相机网页验收完成：实际 Edge 156.0.4314.50 解码真实 320×240 图流，
390/320 CSS宽度无溢出，真实生产者 SIGSTOP→TTL撤图→SIGCONT新帧恢复、页面离开资源清理、
60秒自然EOF撤图通过。最终代码另经8秒真实短流验证，修正相机卡片内边距；无pageerror。
真实PC接收暂停2秒后，源elapsed进度检查拒绝积压帧，状态error且不返回旧图。
各轮前后fuser均确认video2空闲，浏览器与自有loopback服务已关闭。仅相机通道，四传感器保持未知；
没有启动ROS或运动。按用户新规则未新增/运行合成夹具和烟雾测试，原测试保留。
初帧之前传输延迟、曝光时刻和光学重复帧仍不可证明；未实测物理手机或相机硬件拔插。


2026-11-01：五路同屏已通过真实短时静止联调，四路Valid与camera live同时有效10条，未执行运动或模拟流程。可选`--ssh-bind-address 本机物理IPv4`仅给中转SSH绑定来源，用于代理虚拟网卡干扰场景；不改变全局网络。实际源IP随网络改变，不能照抄固定地址。验收报告在.local/learning-checks/M2-006/browser-result.json；手机宽度验证不代表物理手机联网通过。
