# 本机面板 API 与手动启动器

当前前端关机入口强制隐藏且禁用；后台实现保留，全屏下系统认证框不可见的问题尚未解决。蓝图为M1原型→M2监控→M3输入→M4系统整合（当前）→M7整体验收；M5移出当前开发，M6暂缓。

静态robot目录在导入InputObserver/InputService之前加入sys.path，以支持主线control/input_protocol.py共享协议校验；安装器须包含该新模块。停止锁存由主线service/runtime跨刷新保留，刷新不等于清除B停止。

手柄生命周期接入固定项目模块control.input_service.InputService。GET /api/input-devices仅devices()；POST /api/input-refresh使用既有严格Host/Origin/CSRF/128字节/重复字段拒绝规则，精准JSON为{action:'refresh_input'}及可选device_id（null或32hex）。刷新单独串行锁，不与power/exit动作解析混用；只显式POST调用refresh，不自动刷新/授权/重连。构造与GET不启动输入运行时，server退出调用close清理本service拥有的单实例及读取器。

server支持成对--input-runtime与--input-runtime-sha256（64hex）；受信路径与二进制身份由固定InputService校验，不从HTTP传路径或命令。start.py同目录config.json新增input_runtime:null或{path,sha256}，原gamepad_info/probe_ros保留；启动器仅传配置，不发刷新。未配置运行时仍可查看设备，刷新能力以真实service状态为准。

部署需主线新模块robot/control/{__init__.py,input_service.py,input_adapters.py}和已构建运行时，同时更新本轮server.py、start.py、web/robot-panel/{usb-observer.mjs,pages.mjs,style.css}及说明。原InputObserver标记契约和250ms失效规则保持；系统采样、安全关机及退出路由保持原入口。这里只完成Python3.6/JS语法及静态审查，尚未导入运行新service、启动硬件或验证刷新/拔插生命周期；主线负责实机独立部署。

新增GET /api/input-observer，只读固定/home/handsfree/.local/state/rs-usb-observer目录。input_observer.py检查当前UID、0700目录、0600常规文件、无符号链接、唯一32hex文件名、16KiB边界、系统boot及/proc进程UID/start_ticks/exe身份。缺少标记为not_running，多标记/协议异常为invalid；不启动、不发送信号、不改变控制状态。原始API2字段不改写，返回observer_api_version=1、source=robot_input_observer、status/fresh/phase/run_id/total_age_ms/snapshot。总年龄严格按source_age_ms加当前单调时间与received_monotonic_ms之差，未来/负时钟拒绝；达到250ms、running=false或local_input_available=false时snapshot为null，不制造零候选。设备采样/退出/关机接口保持原入口。

部署必须同时带上新增input_observer.py及web/robot-panel/usb-observer.mjs（已列静态白名单）。本增量仅Python3.6及JS语法/静态核对，无新的实机/人工故障验收；观测器启动及身份标记维护仍由主线独立完成。

同一整组run_id可跨三个独立原生进程，instance_id以run_id:pid:start_ticks构成；每次都核对当前系统boot及/proc身份。新进程先返回source_changed且snapshot=null以撤销旧值，再接受该实例后续新鲜快照；同一实例内boot变化或时钟/序号倒退才拒绝。前端也按instance_id/native boot切换基线，不续授权、不发任何控制动作。安装清单须新增robot/panel/input_observer.py和web/robot-panel/usb-observer.mjs，并更新现有server.py、app.mjs、pages.mjs、style.css及说明文件。

底栏“退出面板”仅退出面板，不是Linux关机。POST /api/exit使用与关机相同的严格Host/Origin/CSRF/JSON检查，精准action为exit_panel；GET /api/session允许在设备采样失败时取得退出令牌。收到退出请求后server正常退出，启动器清理仅由自己创建的新session服务/浏览器进程组（TERM后有界等待，必要时KILL），释放实例锁并清理匹配当前instance的状态文件。无按名称批量杀进程，不动原桌面浏览器、ROS或其他机器人程序。直接单独运行server时退出仅清理server；完整浏览器清理由start.py负责。

总蓝图：M1原型 → M2监控 → M3输入 → M4系统整合（当前）→ M7整体验收；M5移出当前开发，M6暂缓。本轮没有SSH、安装、服务启动、硬件操作或关机POST。

Python3.6，使用原机已有python3-dbus及chromium-browser，不安装依赖、不修改驱动/固件、PolKit或sudo规则。项目ROOT从文件位置解析，assets仅web/robot-panel五个固定资源；仅127.0.0.1、默认port0，无目录或路径/命令代理。server.py导入无采样或关机副作用，运行后后台约1Hz采样，GET不更新源时间。

手动运行 `python3 robot/panel/start.py --gamepad-info /home/handsfree/.local/share/rs-control-gateway/builds/561e8246087d7fb7/build/robot_gamepad_info`。识别器路径必须已存在、非重定向到其他位置，且位于上述固定项目builds结构；只数组运行只读程序，2秒截止。可选同目录config.json仅接受gamepad_info与probe_ros布尔值，不接受命令或任意provider代码。未配置识别器则记录provider_errors并返回空列表，不认作没有物理设备。

采集/proc/stat两次增量（首样本CPU未知）、meminfo、CPU/package thermal type、uptime、接口IPv4；BlueZ只GetManagedObjects，不配对/配置。ROS可选只对本机11311调用getPid，默认不探测。电池Unknown/null、任务capability=false。control恒false仅说明本面板没有运动/执行器权限，不说明整机已停止。provider失败保留默认未知及provider_errors，扩展只允许唯一ext_name静态注册，不能覆盖核心API字段。

CanPowerOff只读查询yes/challenge，challenge提示可能需要系统密码。固定POST /api/power-off必须满足精确Host、同源Origin、常量时间token核对、JSON精准action及128字节上限。只有该POST通过校验且允许时才启动独立线程调用login1.PowerOff(True)；HTTP202仅表示已提交，失败写入power.reason，无重试、无凭据存储。请求一旦提交后拒绝重复，需人工核实。不允许用测试请求验证关机。GET或模块导入不会触发PowerOff。

启动器使用当前用户私有~/.local/state/rs-panel(0700)、日志/锁/状态文件(0600)；flock防重复，第二次明确提示返回现有窗口，不信任任意现存PID/URL，也不创建第二实例。启动自有server并核对新随机instance、子进程PID、owner与loopback /api/instance，然后打开独立Chromium profile全屏app窗口。关闭窗口或启动器退出时只清理自有server/browser进程组。保留桌面原浏览器，不安装开机自启；桌面快捷方式由主线安装。

server.py也支持显式--port、--state-file、--instance-id、--gamepad-info、--probe-ros；state-file父目录必须当前用户私有，状态仅含pid/port/instance_id/uid，不含token。CSP、nosniff、same-origin资源策略、SAMEORIGIN及no-store启用。当前仅做Python3.6语法检查，尚未验证设备采样/DBus授权代理交互/真实浏览器生命周期。若uptime不可读返回null及错误，旧页面严格API校验会清空当前值，主线应决定是否单独容忍该字段未知。


2026-11-05 M4-012新增GET /api/system：统一只读系统/输入服务状态与能力，模块system_status.py聚合现有数据，不新增控制入口。遥测3000ms、输入250ms独立时效，过期不返回当前值，GET不启动/扫描/重连输入。服务存活与数据有效分开；此接口不接通PC五路监控或运动输出。详细契约见docs/SYSTEM_ARCHITECTURE.md。
