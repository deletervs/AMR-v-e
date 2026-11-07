# 本地集成启动说明

当前默认关闭真实输出的面板已经安装，机器人桌面“机器人状态面板”手动打开。构建、只读采集与访问检查见 FINAL_INTEGRATION_REPORT.md；真实控制仍待现场验收。不创建开机自启，旧发布目录保留。

## 配置与启动顺序

1. 先集中编译/核对各项目程序，保留原厂环境；编译完成不自动运行ROS、采集器或执行器。
2. 参考robot/panel/sources-config.example.json，填写实际已审核的robot_state_agent与robot_camera_stream路径、SHA256。占位值不可直接用。源必须属于当前handsfree用户的rs-项目目录，不允许软链/可被其它用户改写的执行文件。
3. 安装器新增--sources-manifest参数，仅写入私有配置；没有源配置时，服务页显示未配置，不能伪装成采集正常。
4. 机器人桌面快捷方式手动启动面板；“项目服务”明确启动或停止本项目输入、遥测与相机进程。不会代替启动roscore、厂商底盘/雷达/相机驱动，不会配对蓝牙或操作电源。
5. 概览汇聚有效且未过期电压。完整仪表盘链接/dashboard/?mode=live&source=robot复用已有五路页面，GET /api/state、/api/camera分别读取项目源，不能将设备枚举当成有效测量。

## PC与手机

tools/serve_robot_console.py是手动本地代理，不是公网部署。默认绑定PC loopback且只读；手机需要显式绑定实验室私有IPv4，并提供TLS证书/私钥。需要本地随机访问口令文件，不能放公共仓库或打印到日志。

代理CLI需要明确robot-port（本次机器人面板端口）、local-port（PC上已建立或由--ssh-forward显式创建的SSH转发端口）、access-token-file；不会扫描任意机器人或接受任意代理URL。--ssh-forward只在人工运行命令时创建固定严格主机校验的两段SSH，进程退出时关闭，不恢复自动SSH/keepalive。

控制转发须显式--allow-control；它只开放已存在的同源输入/项目服务接口，不打开真实电机。手机/PC经过本代理的请求仍受原生250ms有效期，延迟太大就拒绝，不自动重发。/api/power-off始终不可转发。Cookie登录有效期1小时，退出代理清空内存会话。

## 执行器

默认SingleExecutor仍处于禁用路径，不创建输出线程或ROS发布者。构建工具--with-ros-executor可编译显式启用路径；不会创建授权。面板安装器--execution-profile-path只允许固定机器人私有路径/home/handsfree/.local/state/rs-executor/authorization.json。未配置路径或profile.enabled=false均无真实输出。

ROS适配仅允许/cmd_vel，需显式提供实际厂商订阅节点，取得当前用户项目输出锁，并持续刷新短期graph许可；遇未知/冲突即失去许可。输出线程保留安全层原截止时间，不通过重复发送更新旧指令有效期。这个机制不能阻止任意外部ROS节点自行控制，且不等于物理停止验收。显式本机授权接线已编译，但未启用、未进行物理验收。

## 最终授权工具（本阶段不执行）

tools/execution_authorization.py部署到机器人后人工运行。prepare-disabled只写关闭配置；authorize需显式二进制路径、厂商订阅节点、1..300秒期限，以及confirm-motion-authorization和confirm-on-floor-and-watched两个确认参数。工具从现场读取开机ID和程序SHA，不打印口令，不启动输入/申请控制权。授权后仍需手动刷新输入、回中和明确申请，使能规则保持。

profile失效、删除、撤销或改动时，已有运行时结束，不自动续期或恢复；若要新授权，必须重新确认并手动刷新。revoke会禁用文件并请求本项目输入停止，失败只报告未确认，不能称物理停车。

API3只在该本机授权实例报告执行器；API2继续严格禁止真实输出。普通网页/模型接口不能创建或延长profile。私有管道与页面请求仍只提交给同一个MotionGuard。

## PC访问材料与保存启动配置

tools/prepare_console_access.py根据明确私有IPv4和本次端口创建.local/console-access内的随机访问文件与launch.json，LAN需要OpenSSL生成7天IP证书；不会覆盖已有材料、打印秘密或启动服务。手机须手动信任经现场确认的证书，证书/端口/IP变更后重建配置，不关闭TLS检查作替代。

tools/launch_robot_console.py --config指向该私有配置，仅在人工运行时启动前台代理。默认allow_control=false、ssh_forward=false；需要转发或候选控制时由操作者明确修改本地配置，不自动提权。Ctrl+C清理自有子进程，不创建开机项。

## 回退（本阶段不执行）

tools/rollback_robot_panel.py --installation-record指向此前成功安装记录。工具只恢复有源码指纹、配置无execution_profile的旧项目release；先核对当前面板身份并请求退出，再备份/修改自有桌面入口。不会删除源码、改动驱动或重启Linux，不自动启动旧程序。桌面信任状态如被系统撤销须按原桌面规则重新确认，不绕过系统权限。

## 已接通的构建与采集适配

PC项目根目录为 D:\repositories\RS。tools/build_robot_control.py 默认构建无ROS输出版本；--with-ros-executor 只增加可选输出构建，不产生授权。tools/build_robot_telemetry.py --with-ros 构建项目只读源并写入 .local/robot-telemetry/sources-manifest.json；不会启动ROS或厂商驱动。--with-ros 表示编译订阅支持，不代表传感器此刻有数据。

tools/install_robot_panel.py --sources-manifest .local/robot-telemetry/sources-manifest.json --launch 将审核过的路径/哈希写入新的项目发布并手动打开面板。--sources-manifest 指定来源清单；--launch 本次打开，不设自启。更新前应退出自己当前面板，不能覆盖正在运行的发布文件。

“后组 → 项目服务”启动C++遥测与相机，“完整实时仪表盘”打开五路页面。它不会启动原厂节点；没有ROS消息的电池、里程计、雷达保持Unknown。采集停止会撤下当前帧；来源异常显示error，不自动重启。

相机沿用已存在的Python2 Pillow做项目隔离解码，Python3主服务不安装新系统依赖。项目管理模式使用 --continuous 持续读取，启动和卡读有看门狗；旧 --duration-seconds 有限采集用法保留。持续读取不等于长期稳定性已经验收。

PC生成LAN证书时可用 --openssl-config 明确指定已有的openssl.cnf；工具也会寻找OpenSSL安装旁的ssl/openssl.cnf，仅影响本次子进程，不修改系统环境。需使用实际PC私有IP和当前面板端口；本轮192.168.50.99证书仅用于隔离的本机HTTPS检查，不是可供手机访问的部署地址。一次性测试证书/口令已清理；复现时另建私有目录，禁止上传凭据。


## 从Git检出后的构建前置

这些工具使用本实验室的固定SSH别名/机器人路径，需要先配置自己的可信SSH环境，不附带密钥或主机信任文件。build_robot_control.py现在同时构建robot_gamepad_info并写入native-build.json；安装器优先读取该清单，也可用--gamepad-info显式指定已有项目识别器路径。旧历史验证文件仅作为本机兼容回退，新检出不需要该文件。构建清单、发布记录和私有源配置由本机生成，不上传。此交付修正只检查了Python3.6语法及CLI参数解析，未重新部署机器人，不计为新增实机验收。
