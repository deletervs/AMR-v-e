# M3 离线遥控实验台

仓库根目录启动（先由主线构建 C++ `mock_teleop.exe`）：

```powershell
python tools/serve_mock_teleop.py
```

浏览器访问终端返回的 `http://127.0.0.1:8766`。可用 `--port 0` 分配空闲端口，`--agent PATH` 指定假控制器。服务没有 LAN host 参数；与现有只读仪表盘完全独立。

1. 方向和使能全部松开。点击“取得控制权”。
2. 键盘用 WASD / 方向键，按住 Shift 使能；也可按住页面使能键，再按虚拟方向键（多指触控）或键盘方向键。
3. 标准 Gamepad 用左摇杆和 RB，切换前先释放，摇杆回中。非 standard 映射不允许使能。
4. 松开使能即归零；Space / 软件停止按钮锁存停止。解除停止后必须手动重新取得控制权。

单个窗口随机分配 uint64 controller ID，不保存在本地存储。活动租约不抢占；固定租约 10 秒。每条命令使用浏览器最近看到的 agent `elapsed_ms`，有效期加 250ms；不以收到 HTTP 的时间续期。服务每 50ms tick C++ 假控制器。失焦、hidden、pagehide、pointercancel、捕获丢失、切换来源、断手柄或请求失败都会清空输入并尝试绑定当前 owner 的 release；失败时由固定命令有效期撤销控制权。回来不会自动 arm。

首次连接失败或服务会话变化时，页面清空当前控制权与租约显示，每隔至少 500ms 重试只读握手并获取新 CSRF 令牌；连接恢复后仍需手动取得控制权。该重试不会启动或重启服务、假控制器或实机连接。

响应按本窗口的请求顺序与连接代次筛选：旧释放回复不能覆盖更新的授权状态，断开前的迟到回复也不能恢复旧页面状态。浏览器验收会通过专用测试进程实际关闭并重建 loopback 服务及 C++ 子进程，确认 boot 改变、输入清空和手动重新授权；该测试能力没有开放为 HTTP 接口。

即使 HTTP 请求尚未结束，页面仍检查最近状态的本地年龄；超过 150ms 就清空输入、停止旧速度动画并尝试释放。C++ 的 250ms 命令截止规则保持独立，浏览器调度不提供实时保证。

HTTP 使用固定静态文件允许列表、精确 Host / Origin、JSON 类型与字段、CSRF、限长及禁跨源响应。子进程使用参数数组启动，锁保护整条 stdin/JSON 事务，读取有截止时间；异常显示本地零状态并终止自有假控制器。退出发送 stop / quit 并回收子进程。无 ROS、SSH、机器人话题、真实控制入口。

验证：

```powershell
python -m unittest tools.tests.test_mock_teleop_service -v
node --test web/teleop/tests/test_core.mjs
node web/teleop/tests/browser.mjs
```

浏览器验证脚本需要 Playwright 与 Edge，创建并清理自己的本地服务、C++ 子进程与 headless 浏览器。默认从 Node 模块路径载入 `playwright`；可用 `PLAYWRIGHT_MODULE`、`PYTHON_EXECUTABLE`、`BROWSER_EXECUTABLE`、`MOCK_TELEOP_AGENT` 指定本机安装位置，不把个人路径写进源码。报告与截图在 `.local/learning-checks/M3-001/`。Gamepad 测试注入的是合成的浏览器 API 输入，手机测试为 Edge 390px 触控模拟；真实手柄、手机实触摸、真实运动、硬件急停、避障均未验证。动画只根据目标速度绘制，不是物理仿真。
