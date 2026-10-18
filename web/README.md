# RS 模拟状态仪表盘

分工：用户编写关键核心 C++ 功能；AI 负责网页、只读 HTTP 桥接、联调和文档。支持两种模式：离线记录回放（M1-005A）和 C++ 持续模拟状态（M1-005B）。均未连接真实机器人。

## 一键运行

本机 VS Code：Ctrl+Shift+P -> 任务: 运行任务 -> **RS: 持续模拟仪表盘**。此任务先构建现有 C++，再启动本机服务。打开 http://127.0.0.1:8081/?mode=live 。页面左上可切换离线回放。

Ctrl+Shift+B 继续执行原 C++ 构建测试。网页任务手动启动，没有 SSH、设备操作、自动开机启动或后台保活。

终端等价命令，在仓库根目录执行：

```powershell
python tools/serve_dashboard.py
```

服务仅绑定 127.0.0.1，默认端口 8081；复用已构建的 mock_robot_state --stream 子进程。所有客户端共用该进程，HTTP 接口只读。Ctrl+C 同时停止服务和它自己启动的模拟进程。

可选参数：--port 指定端口；--mock-executable 指定已构建的模拟程序路径。例如 Linux 可执行：

```bash
python3 tools/serve_dashboard.py --mock-executable .local/build/linux-debug/mock_robot_state
```

上述参数方式已在 Ubuntu 20.04/GCC 9.3/Python 3.8.10 的独立同提交副本通过 CLI 实测。

只看离线记录也可执行：

```powershell
python -m http.server 8080 --bind 127.0.0.1 --directory web
```

打开 http://127.0.0.1:8080/ 。-m 运行标准库模块；8080 是端口；--bind 127.0.0.1 只监听本机；--directory web 指定静态文件根目录。该静态入口不提供 /api/state，持续模式应使用前述桥接服务。不要直接双击 HTML 文件。两种方式均无需 npm、pip 包或前端构建；仅用于本机开发预览。

## 文件与数据流

| 文件 | 职责 |
| --- | --- |
| index.html / styles.css | 页面结构与 PC/手机宽度布局 |
| app.js | 共用卡片和图形显示，以及离线记录回放 |
| live.js | 持续模式的只读轮询、连接状态和失败恢复 |
| state.ndjson | C++ --stream-test 实际生成的 17 帧确定性模拟记录 |
| ../tools/serve_dashboard.py | 管理一个 C++ 模拟子进程，把逐行 JSON 转为本机 HTTP 状态快照 |
| tests/test_dashboard_service.py | 服务契约、只读接口范围、生命周期与实际 C++ 接入测试 |

```text
C++ mock_robot_state --stream -> stdout 管道 -> StateBuffer
  -> GET /api/state -> live.js -> 卡片与曲线
C++ --stream-test -> state.ndjson -> app.js -> 离线回放
```

C++ 负责状态规则、电池与里程计质量。网页不使用页面刷新时间重算传感器质量；连接、电池与里程计质量分别显示，过期读数保持可见并标记 Stale。持续页面最多保留 120 份收到的状态用于曲线，不是持久历史数据库。

## 持续接口约定

GET /api/state 返回 api_version=2、stream 元数据和最后一份 state。接口正常响应为 HTTP 200；上游健康通过 stream.status 表示。

- stream.id 为本次服务的随机会话标识；服务重启后改变，页面清空之前会话的曲线和序号。
- sequence 为已接收的有效帧数量，严格递增；客户端重复读取不会增加它。
- age_ms 是服务本机单调时钟计算的接收年龄，不传输或比较不同主机的 steady_clock 时间点。
- status 为 starting、live、stale、stopped 或 error。两秒没有新帧即过期；生产者退出/错误立即变为不可用。
- state 可以为 null；上游失效时 API 保留最后记录供诊断，但页面清空当前状态卡片并明确报错，不把历史 Valid 帧显示为当前有效。
- 页面每轮请求完成约 500 ms 后继续读取，请求超时 1500 ms；失败后继续只读重试。不会自动重启生产者或恢复运动。
- 仅允许固定网页资源及 /api/state；没有仓库目录浏览、shell、运动、电源或重启入口。POST 被拒绝。

## 帧契约与回放

一行一个 JSON 对象：source=mock；connected 为布尔值；motion_enabled=false；sim_time_ms 为从零或正数开始且严格递增的整数；battery.voltage_v 为有限数字或 null；battery.quality 为 Unknown、Valid、Stale、Fault、Unsupported。

没有电压显示 —，不显示成 0 V。离线模式按相邻记录时间差回放，支持暂停、键盘/滑块逐帧查看、重播和原始 JSON；结束时明确停在历史记录。加载失败会清空旧值并支持重试。全站本地资源，没有 CDN、外部字体或遥测上报。

更新记录前先用 Ctrl+Shift+B 构建并通过 C++ 测试，再执行：

```powershell
$env:Path = "C:\msys64\ucrt64\bin;" + $env:Path
.\.local\build\cmake-debug\mock_robot_state.exe --stream-test | Set-Content -Encoding utf8 web\state.ndjson
```

第一行只修改当前终端的 DLL 搜索路径；第二行的 | 把输出交给 Set-Content 写入文件，-Encoding utf8 指定编码。页面兼容 UTF-8 BOM。该操作替换旧记录，提交前核对为 17 行模拟数据。

## 验收

服务的可重复测试（先确保 C++ 已构建）：

```powershell
python web/tests/test_dashboard_service.py
```

可用 --mock-executable 指定测试使用的 C++ 程序。此脚本独立运行，独立于当前六项 CTest 登记。

上一轮基线（2026-10-18）Windows 本机：11 项服务测试通过；Edge 浏览器验证两客户端、实际 C++ 电池正常/过期/恢复、上游进程退出、传输超时与自动重连，原离线模式回归通过。1440/768/390/320 像素无横向溢出，桌面和手机宽度截图已检查。临时测试进程和服务已结束。

代码验收基线：1ba367de2b695b1dfd0b8d70e1cd542ad7a8c7ec。2026-10-18，Linux 独立副本配置构建与 CTest 5/5、服务测试 11/11 通过，源码前后干净；实际 HTTP CLI 的两客户端、9 份新帧和正常/过期/恢复通过，SIGINT 退出后监听与模拟子进程均清理。Windows 原验收的 7 个源码指纹与该提交一致。后续验收记录更新只涉及 README 文档。真实手机、局域网、Linux 浏览器、远程认证、真实设备与完整属性尚待对应任务，手机宽度测试不等于手机联网验收。

## M1-005C 里程计接入（2026-10-18）

当前工作区已完成：C++ 核心规则 -> JSON -> HTTP -> 离线/持续网页。接口升级为 api_version=2，网页拒绝旧服务和缺少里程计的旧帧；更新后先停止旧任务、重新构建，再启动服务。

odometry 包含 reading 和 quality。reading 为 null 或包含以下六个有限数字的完整对象：x_m、y_m、yaw_rad、linear_x_mps、linear_y_mps、angular_z_radps。数值允许零和负数，布尔值、缺字段、NaN及无穷会被拒绝；Valid/Stale 必须有样本。quality 仍沿用五种 DataQuality，与电池独立。

位置和朝向相对 odom 原点，速度在车体坐标系表示。页面显示 X/Y 位置、朝向、两项线速度、Z 角速度和独立质量。朝向额外换算为度并保留原始弧度说明；网页不计算新的时效质量。过期保留样本并标注历史读数；传输失效清空全部当前数值。

持续模拟每六秒重置：0秒无样本，1秒收到合法全零样本，2秒过期，3秒里程计恢复并模拟圆弧运动，4秒电池恢复。17帧回放文件来自本轮实际 C++ --stream-test 输出。

本轮代码基线af360ea52c57的Windows验收：CMake Debug 构建、CTest 6/6、独立服务测试15/15通过；Edge 155 实际验证17帧六字段/角度/质量与原始JSON一致、独立恢复、旧API及非法帧拒绝、两客户端、超时重连和真实模拟子进程退出。1440/768/390/320像素无横向溢出，截图已检查。临时服务、线程与模拟子进程均已清理。Linux同提交验证也已通过，结果见下；真实手机联网未验证。

Windows 正在运行的 mock_robot_state.exe 会阻止链接器覆盖它。编译前，在运行模拟程序/持续仪表盘的终端按 Ctrl+C；找不到终端时，使用“任务: 终止任务”选择对应任务，再按 Ctrl+Shift+B。终止构建任务本身不会停止另一个终端中的模拟程序。
本轮Linux验收基线：af360ea52c57a28ee3ad3596d291dd018dd694ff。2026-10-18，Ubuntu20.04.3/GCC9.3/Python3.8.10的独立公开源码副本完成Debug构建、CTest6/6和服务15/15。8个编译单元使用C++17、4个C++测试启用断言；源码前后干净。实际HTTP验证两个客户端共用一个C++生产者，9份状态中分别观察电池和里程计Unknown→Valid→Stale→Valid、有效零值和独立恢复；SIGINT后生产者及监听已结束。17帧fixture与Linux输出在1e-12容差内等价。Linux浏览器未运行，界面证据来自Windows Edge。