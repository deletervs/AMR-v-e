# 机器人本机小屏面板

执行状态失效补充：键盘API接收native_api_version及execution_state_current。API2仍不得报告真实输出，API3携带最近executor元数据且physical_stop_confirmed必须false；available=false时candidate必须null。缺乏新鲜输入或execution_state_current=false不等于执行配置已关闭：已经见到或保守元数据仍报告的真实输出警示继续显示“输出状态暂不可确认 / 物理停止未确认”，照常撤销按住态和本页会话。只有新鲜明确关闭状态才恢复默认文案；当前未定义单独的服务退出确认字段时保守保留提示。手柄及服务页读取失败也不抹去已见真实输出提示。执行器最近write元数据不当作当前输出/停车证明。本增量只写代码、静态阅读，未执行检查/测试/部署。

执行模式兼容增量（代码阶段，尚未验证）：原生robot_usb_guard API2仍要求两个运动/执行器标志false；API3只在本机执行配置路径启用时使用，必须携带executor的合法state、output_enabled/last_write_ok布尔及physical_stop_confirmed:false。版本外拒绝；motion_enabled或output_enabled为true时必须有actuator_attached，disabled状态不能自称output_enabled。候选保持“候选”，zero_requested/target_requested/last_write_ok只是请求或写入状态，不是物理运动/停车确认。

键盘API1及服务API1按各自新契约接收真实布尔模式（motion_enabled要求actuator_attached）；服务旧false响应可省略actuator_attached，但不能凭缺失字段接受true。手柄API3和键盘真实输出标志会显示红色常驻“真实输出模式已由本机配置启用；需现场看护”，数据失效不抹去此前真实输出提示；API2/有效关闭状态才恢复无执行器文字。键盘模式变化连同generation/boot变化都会撤销本页会话、旧按住态及候选，不自动arm，晚open继续补close。手柄模式变化先清旧当前值、等新采样后显示。

项目服务executor仅显示后端configured/active/fault等状态，actions必须为空；页面没有启用、续期、修改execution_profile或生成授权文件的入口。输入启动/刷新仍仅为明确手动操作，不能把后台私有配置解释为页面授权。普通/api/panel和robot_state_agent标志仍仅描述其只读采集权限；系统模式以各自版本化/api/system、键盘与原生观测为准，不把遥测false当全局关闭。没有新增OS电源或配对入口，关机继续强制隐藏。

本轮只写上述前端代码并静态阅读现有协议。未运行语法检查、测试、浏览器、SSH、部署或任何POST；所有兼容性、实际模式警示、会话撤销与执行器验证统一留到M7。本机授权文件、ROS sink、profile校验及实际现场看护由主线独立管理，页面兼容不代表已启用或已验证真实输出。

新增独立“项目服务”页（project-services.mjs）：每秒GET /api/services，1500ms AbortController截止，稳定DOM展示input、telemetry、camera、executor四项；运行与可用分别显示，不授予运动权限。input按真实actions提供启动/重新发现和停止按钮；telemetry/camera只有后端明确配置并返回对应actions时才显示启停按钮。telemetry是独立C++只读遥测源，与普通本机系统采样线程分开；camera仅本项目相机采集。executor仅显示代码状态，绝无操作按钮。真实点击从/api/session取令牌，再精准POST {service:'input'|'telemetry'|'camera',action:'start'|'stop'}，无设备ID、无自动start/restart/重试。多设备仍须到原手柄页选择，输入停止保留B锁存。只管理项目进程，不启动原厂ROS、厂商驱动或系统服务；不提供OS电源或配对入口。

页面提供普通链接“完整实时仪表盘”，固定指向/dashboard/?mode=live&source=robot，原仪表盘静态资源及/api/state、/api/camera由主线接入；本页不复制五路图表、不生成或加载mock数据。启动返回仅说明进程启动，不等于传感器可用；停止项目进程不代表机器人已物理停车。

服务GET的instance_id须与现有/api/session的instance_id一致；操作前核对，避免旧页面意图提交给已重启的新面板实例。原始返回detail只作后端结果文字，当前状态仍由下一次GET读取，不以完成响应推断获得控制权或采集可用。相机/遥测增量仅完成代码和静态阅读，未执行语法检查、测试、浏览器或SSH，验证统一留到M7。

请求失败或状态过期清当前值并禁用按钮，instance变化清旧状态；离页/隐藏清理轮询、请求和晚回包。操作结果独立显示，不被GET覆盖，POST中断不推断后端回滚；完成后重新GET真实状态。当前仅代码实现及静态阅读，按用户要求所有测试、语法检查、浏览器及实机验证统一留到M7。本轮未运行任何检查程序、服务、SSH/Git或控制请求。主线须在固定静态白名单/安装清单增加project-services.mjs，后端与部署由主线负责；原键盘/历史流程保持。

M4-016在键盘控制页增加“最近指令结果”原生折叠区，默认仅小块summary占位，展开使用现有main滚动；不改控制流程。新增control-history.mjs独立每500ms GET /api/control-events，1200ms AbortController截止，单请求在途，兼容Chromium99。仅按后端最旧→最新顺序显示最近20条、200容量及丢弃计数；DOM按事件ID复用并以textContent更新。实例变化清旧历史；错误显示“历史暂不可读”，不清核心状态。隐藏/离页取消轮询和在途请求，代次阻止晚回包恢复。

enqueued仅进入管道，native_result是MotionGuard结果而非电机反馈；unconfirmed为未知，receipt_gap不能推断缺失结果，guard_state不能当键盘请求确认。历史command_usable必须为false，不被用作当前候选、使能或授权依据；无按钮/POST/自动会话。安装器及固定静态资源白名单需主线增加control-history.mjs；接口和部署由主线负责。本轮仅JS语法及静态检查，无服务启动、合成输入或控制POST。

M4-015新增独立“键盘控制”页（keyboard-control.mjs），现有分组导航注册、内容区滚动，1024×600使用至少64px动作与触控按钮，兼容Chromium99。只显示共享原生守卫的候选值，无执行器。页面GET /api/keyboard约80ms只读查询；初始化、切页不open/arm。先真实点击取得30秒页面会话，再松开所有控制键手动申请；看到实际keyboard owner/session后发送原始全松开中性帧，待原生Accepted/neutral确认后，必须新按一次Space或触控使能。提前按住Space不会因状态更新自动使能。WASD/方向及触控原始方向值保留，只有受信事件改变本页按住集合；只实体USB A可清锁存。

POST只有一个在途；普通输入约80ms采样，不排队。票据收到时记录performance.now，采样冻结elapsed_ms后不重打；比较server_clock_ms+elapsed_ms，映射倒退的帧直接丢弃，等真实新采样，不自动arm。200ms页面有效期与250ms命令窗口不放宽。HTTP202只显示enqueued，真实owner/armed/candidate/keyboard_result来自GET，不能据入队宣称接受或移动。

失焦、隐藏、离页、源generation/boot变化、状态/权限失效立即清全部按住态并撤销本页会话。独立安全FIFO仅容纳停止/释放及随后close，不积压keys：先停止/释放，完成或失败后再close，不会先close或覆盖安全动作；安全动作过期不重新采样，明确未确认后继续撤销会话。close是当前生命周期撤销，固定clock_ticket:null、elapsed_ms:0、keys:null，不受旧票据/250ms输入年龄限制；序号仍递增。晚open拿到token后即使离页/无票据也补close，绝不恢复旧实例。网络中断导致open响应丢失、token未知时无法保证补close，仍依靠后端30秒页面租约和原生250ms超时。

本轮仅JS语法/静态审查，没有构造输入、发送控制POST、启动服务或SSH。主线须将keyboard-control.mjs加入固定静态资源/安装清单，并独立进行真实渲染、原始中立、双源仲裁及故障验收；后端与安装器由主线负责。关机UI继续强制隐藏，M5已移出、M6暂缓。

当前关机按钮和确认框在前端明确隐藏且禁用，不占底栏空间；后台关机实现保留，认证框被全屏遮挡的问题尚未解决。退出面板与手柄功能继续使用原入口。蓝图现为M1原型→M2监控→M3输入→M4系统整合（当前）→M7整体验收；M5移出当前开发，M6暂缓。

选择器仅允许supported设备，未适配/蓝牙预留节点仍列出但禁选。保留用户已选支持设备，否则优先service当前设备，再默认选中唯一支持设备；不会因蓝牙残留节点误选。当前设备只取service.current_device，没有活动设备时明确显示“暂无活动手柄”，历史单列“最后设备”；枚举节点不等于蓝牙已配对/已连接，supported也不等于型号实测通过。

“手柄与安全层”保留id usb-observer，位置在设备之后。新增独立设备区约1Hz读取/api/input-devices，不随原生无源把设备信息全清成—；明确区分当前、未连接及最后设备。接口读取失败时保留明确标注“上次信息（非当前）”的记录。显示名称、transport、VID/PID、轴/按钮数与适配器，supported只代表语义布局接口，不能当型号/控制实测通过。蓝牙和其它型号按真实适配器描述显示预留，页面不扫描配对或安装驱动。

用户可在真实设备列表选择；仅一设备时自动选中，多设备保留明确选择。真实点击“刷新设备”先撤销raw/candidate并取消旧回包，再GET /api/session取令牌、POST /api/input-refresh（刷新总截止5秒、防重复、不重试）。无设备也允许在service可刷新时手动扫描；未配置运行时/不支持/外部实例占用等显示真实refresh_result。刷新不会自动Start/arm，不把旧按键复制回运行时；须等待新原生状态并使用实体手柄授权。刷新错误和结果单独保留，不能被1Hz列表覆盖。离页/隐藏取消本页所有设备/观测/刷新请求及计时器；已经提交给后端的刷新不能因浏览器取消而推断回滚。原50ms观测轮询、250ms TTL、180ms截止、稳定DOM与分组导航保持。

本轮仅语法和静态检查；没有SSH、服务启动、输入注入或关机请求。主线将独立实测刷新、拔插、设备信息与面板退出清理。

新增“USB安全层”页，位于设备之后、第一组导航内；概览仍是默认首页。该页读取机器人端API2观测，不使用PC Gamepad API。可信render provider的independent:true使该页独立轮询50ms，busy防止并发请求，主面板1Hz刷新不拆建其DOM；离页cleanup与隐藏事件取消请求/计时器并清值。每次请求180ms截止，另以25ms检查剩余250ms源有效期，包含请求RTT保守年龄；同elapsed不续进度，来源变更/序号倒退/异常立即撤销当前raw及candidate。按钮只在既有底栏切页；此页没有控制/授权/停止/测试动作。

显示来源、原始轴/按钮、会话/回中/锁存/租约、候选值、源年龄及观测阶段和原因。未知、未运行、结束、无本地输入与过期时显示原因和“—”，不把候选零值当停止验收。现有系统、关机/退出能力与面板USB观测新鲜度各自独立。仅完成语法及静态检查，主线须用真实观测验证文件身份、250ms失效、离页/隐藏及1024x600显示；未合成输入、SSH或启动观测。

底栏“退出面板”真实点击后请求本实例退出，启动器随之清理自有面板服务、只读采集子进程及独立浏览器进程组，保留原桌面浏览器和其他机器人程序。退出不会关闭Linux；关机入口当前隐藏。退出不依赖传感器采样新鲜，但必须通过本机同源令牌校验。Chromium99使用AbortController+setTimeout管理请求截止，不使用AbortSignal.timeout。界面恒false字段仅代表面板只读权限，不推断机器人整体停止或执行器接入状态。

总蓝图：M1 原型 → M2 监控 → M3 输入 → M4 系统整合（当前）→ M7 整体验收；M5移出当前开发，M6暂缓。局部：页面实现 → 静态检查 → 主线 API/启动/桌面快捷方式 → 实机小屏观察。此目录仅页面，未操作硬件或启动任何服务。

以实机1024×600为主尺寸，兼容800×480，固定顶部和底部导航、两列四卡，无页面滚动或自动聚焦/跳页。图标30px、导航触控区至少64px。过长设备描述截断，完整文字保留title；实机可读性由主线检查。

同源 GET /api/panel 每秒一次，1.5秒请求截止；严格校验 API1/source、字段类型及恒false的运动/执行器标志。源 sampled_elapsed_ms 倒退拒绝；相同值不刷新进度，3秒无源进度或请求失败清除当前值。未知显示—；设备节点、ROS可达、电池quality均不等于物理验收通过。电池capability未接入时不展示电压；任务页明确未接入。

pages.mjs提供registerPage/provider静态注册表，ID须匹配小写字母起始的字母/数字/短横线（最多32字符）且唯一；建议模块名前缀以免冲突。默认注册{id,title,icon,cards(state)}，cards(null)提供未知占位；默认四卡只是模板，不限制新页数量，所有返回的卡片均渲染、不静默截断。可选layout为default（未配置时保持当前四页样子）、compact（较紧凑流式卡片）、single-column（单列内容）。卡片可选span:2跨整行；只接受受信任的枚举/数字元数据，不拼接任意CSS。默认超过四个网格位或显式选择流式布局时，只有内容区滚动，顶部/底部固定；采样更新保留本页滚动位置。新页可自行选择布局，不能把四卡当作全站限制。

非卡片布局继续使用同步render(container,state)，两者同时提供时render优先，溢出仅在内容容器滚动。每次采样、失效或切页前清除旧容器，可返回cleanup函数用于释放图片对象URL、监听器等；退出也调用cleanup。render收到null必须立即撤销旧数据，容器尺寸固定、不可自动聚焦/跳页。例如新页可配置layout:'compact'，并返回{title,value,detail,span:2}的宽卡及任意数量普通卡片；这只是扩展契约，不新增虚构页面或数据。

导航自动按每组最多四页分页，超过四页才显示前组/后组64px按钮；翻组不自动切换当前内容。页面标题过长截断，保留title。静态布局预算覆盖1024×600及800×480，但真实小屏点击效果仍需实机验证。静态provider须在app初始化前注册，不使用eval、任意URL导入、模型生成HTML/script或给扩展授予系统/执行器权限。图片源须另定义受信任的同源只读API及失效契约，不将任意URL作为运行入口。

以下仅为未来扩展写法，未注册或展示虚构数据（数据接入前只显示未接入）：

```javascript
registerPage({
  id: 'embodiment-view', title: '具身视图', icon: 'tasks',
  render(container, state) {
    const message = document.createElement('p');
    message.textContent = state ? '该扩展数据尚未接入' : '— 当前数据不可用';
    container.append(message);
    // 后续接入真实数据前，先定义并校验对应API字段及新鲜度。
    // 如创建对象URL或监听器，在返回的cleanup内释放。
  }
});
```

关机界面当前强制隐藏、禁用；以下为保留实现的历史说明，并非当前可用入口：原设计要求新鲜API power.can_power_off=true及csrf_token，并显示后端reason；确认后仅一次 POST /api/power-off，失败不重试。全屏系统认证可见性尚未解决，恢复入口前须另行处理。后端接口保留。本页无运动、配对、驱动、固件或网络配置操作。

仅执行语法和静态审查，不生成输入或测试实际关机。API时钟应在该后端生命周期单调推进；后端重启导致倒退时用户须手动刷新。关机HTTP202仅表示请求已提交到系统，界面提示等待系统身份验证/安全关机，绝不显示“已关闭”。其他响应显示未确认；不会自动重试。
