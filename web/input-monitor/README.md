# 真实输入监视器

在仓库根目录运行 `python tools/serve_input_monitor.py`，在浏览器打开终端打印的地址。默认端口 8767；`--port 0` 由操作系统分配空闲端口。终端 Ctrl+C 关闭服务及自有 C++ 子进程。服务只绑定 127.0.0.1，固定白名单文件，无目录浏览、代理、SSH、ROS、串口、控制器或机器人写接口。浏览器须用终端打印的 127.0.0.1 地址，其他 Host 会被拒绝。

## 可选 C++ 计算

Windows PowerShell 中先让当前进程找到既有 MinGW 运行库（不安装或复制 DLL），再显式选择程序：

```powershell
$env:PATH = 'C:/msys64/ucrt64/bin;' + $env:PATH
python tools/serve_input_monitor.py --port 8767 --cpp-preview .local/build/cmake-debug/input_preview.exe
```

不传 `--cpp-preview` 时保留浏览器预览。启用时只用数组启动指定且已存在、名为 input_preview(.exe) 的程序，无 shell。GET /api/capabilities 返回当前可用性及 CSRF token；POST /api/preview 只接收严格 keys/pad 字段，验证固定 Host、同源 Origin、token 和 512 字节上限。服务自己分配原生 request ID，串行计算，核对回包并以 0.5 秒截止时间清理故障子进程；故障后重启服务恢复。

页面单独标明 C++ 结果，原始相反方向键也会如实交给核心计算。只有实际采集中且已回中才提交，失焦、暂停、断开、隐藏、切换立即撤销结果。结果代次、采样序号与本机 250 ms 时效用于丢弃旧回包，HTTP 失败显示不可用，不以 JavaScript 预览代替 C++ 结果。C++ 示例仅计算既有映射，无 ROS/机器人连接或运动使能。

先连接真实设备，必要时实际按一下手柄让浏览器暴露设备；手动选择设备，点击开始，松开 RB 并使摇杆回中。界面显示浏览器报告的 id/index/mapping、所有原始 axes/buttons、timestamp 和 RB 值。standard 只是浏览器映射，不能证明实体 Xbox 身份或 USB/蓝牙连接方式。虚拟设备不能当作实体手柄验收通过。

也可切换键盘 / 虚拟键位，点击开始，松开控制键，再用 WASD / 方向键与 Shift 观察预览。Space、暂停、失焦、隐藏、退出、所选设备断开均清零；返回后必须再次点击开始并回中。断开后同 index 设备不会自动继承选择。预览只复用既有 core.mjs 的 readPad / scaledAxis，不创建 InputSession，不访问旧 API。死区 0.15，示例限幅 0.25 / 0.6，不是 M4 实机参数。

报告仅在用户点击导出时下载到本机。保留最近 1200 个采样、最高 10 Hz；时间字段标明实际采样窗口，连续采样可观察轴、按钮与键位变化。勾选的物理操作及连接方式均为用户自报，不能替代物理验收。不会采集全局文字或发送报告到服务。

M3-003 验证含语法审查、实际原生程序启动与真实 Edge 无窗口 capabilities 初始化；未向计算接口提交合成输入，未注入 Gamepad、键盘或按钮。M3-002 既有 USB 证据保持原样；本增量的物理键盘端到端、断开与失焦恢复仍需用户实际验证，蓝牙待办。未连接机器人。
