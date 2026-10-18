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

当前实测为 Windows；Linux 命令用于后续同版本验收，不能视为已经运行通过。

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

C++ 负责状态规则和电池质量。网页不使用页面刷新时间重算传感器质量；连接和电池质量分开显示，过期读数保持可见并标记 Stale。持续页面最多保留 120 份收到的状态用于曲线，不是持久历史数据库。

## 持续接口约定

GET /api/state 返回 api_version=1、stream 元数据和最后一份 state。接口正常响应为 HTTP 200；上游健康通过 stream.status 表示。

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

可用 --mock-executable 指定测试使用的 C++ 程序。此脚本独立运行，不改变 CMake 或原 CTest 五项登记。

2026-10-18 Windows 本机：11 项服务测试通过；Edge 浏览器验证两客户端、实际 C++ 电池正常/过期/恢复、上游进程退出、传输超时与自动重连，原离线模式回归通过。1440/768/390/320 像素无横向溢出，桌面和手机宽度截图已检查。临时测试进程和服务已结束。

当前结果针对工作区，提交后仍需 Linux 同版本验证。真实手机、局域网、远程认证、真实设备与完整属性尚待对应任务，手机宽度测试不等于手机联网验收。
