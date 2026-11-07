// Read-only robot-side USB evidence, not the browser Gamepad API.
export function renderUsbObserver(container){
  const fields=new Map();
  const heading=document.createElement('p');heading.textContent='机器人 robot_usb_guard · 等待执行模式状态';container.append(heading);
  const modeWarning=document.createElement('p');modeWarning.className='execution-mode-warning';modeWarning.hidden=true;modeWarning.textContent='真实输出模式已由本机配置启用；需现场看护。写入/发布不是物理运动或停车确认。';container.append(modeWarning);
  const status=document.createElement('p');status.className='usb-status';container.append(status);
  const deviceBox=document.createElement('section');deviceBox.className='input-devices';container.append(deviceBox);
  const controls=document.createElement('div');controls.className='input-refresh-controls';
  const select=document.createElement('select');select.setAttribute('aria-label','选择实际输入设备');
  const refresh=document.createElement('button');refresh.textContent='刷新设备';refresh.type='button';controls.append(select,refresh);deviceBox.append(controls);
  const deviceInfo=document.createElement('p'),deviceMessage=document.createElement('p'),adapterInfo=document.createElement('p'),refreshMessage=document.createElement('p'),deviceDetail=document.createElement('p');
  const details=document.createElement('details'),summary=document.createElement('summary');summary.textContent='设备与适配器详情';details.append(summary,deviceDetail,adapterInfo);
  refreshMessage.setAttribute('role','status');refreshMessage.textContent='拔插后请手动刷新；刷新不自动授权，也不清除B停止锁存';
  deviceBox.append(deviceInfo,details,deviceMessage,refreshMessage);deviceInfo.textContent='等待真实输入设备列表';adapterInfo.textContent='蓝牙与其它型号适配以注册状态为准，不进行配对';
  const grid=document.createElement('dl');grid.className='usb-fields';container.append(grid);
  const labels={phase:'观测阶段',source:'来源 / boot',axes:'原始 X / Y',buttons:'RB / Start / B / A / Back',guard:'授权 / session',neutral:'回中 / 停止锁存',lease:'剩余租约',candidate:'候选线 / 角',executor:'执行器状态（非物理反馈）',age:'源年龄 / 总年龄',reason:'停止 / 过期原因'};
  for(const [key,label]of Object.entries(labels)){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent='—';grid.append(dt,dd);fields.set(key,dd);}
  const set=(node,value)=>{if(node.textContent!==value)node.textContent=value;};
  function clear(reason){for(const node of fields.values())set(node,'—');set(status,reason);if(!modeWarning.hidden){set(modeWarning,'此前已报告真实输出模式；需现场看护。输出状态暂不可确认，物理停止未确认。');set(heading,'机器人 robot_usb_guard · 输出状态暂不可确认');}}
  const int=v=>Number.isSafeInteger(v)&&v>=0,id=v=>typeof v==='string'&&/^(0|[1-9][0-9]{0,19})$/.test(v)&&BigInt(v)<=18446744073709551615n;
  let stopped=false,busy=false,abort=null,poll=null,watch=null,expires=0,progressExpires=0,last=null,generation=0;
  let devicePoll=null,deviceAbort=null,refreshAbort=null,deviceBusy=false,refreshBusy=false,deviceEpoch=0,deviceData=null,deviceGeneration=-1,optionsSignature='';
  function deviceValid(d){return d&&typeof d.id==='string'&&/^[0-9a-f]{32}$/.test(d.id)&&typeof d.name==='string'&&typeof d.path==='string'&&/^\/dev\/input\/js[0-9]+$/.test(d.path)&&['usb','bluetooth','unknown'].includes(d.transport)&&['vendor_id','product_id','version'].every(k=>d[k]===null||typeof d[k]==='string'&&/^[0-9a-fA-F]{4}$/.test(d[k]))&&(d.axis_count===null||int(d.axis_count))&&(d.button_count===null||int(d.button_count))&&typeof d.supported==='boolean'&&typeof d.readable==='boolean'&&(d.adapter_id===null||typeof d.adapter_id==='string')&&typeof d.reason==='string';}
  function devicesValid(d){
    if(!d||d.source!=='robot_input_devices'||d.input_devices_api_version!==1||d.actuator_attached!==false||d.motion_enabled!==false||!int(d.generation)||d.generation<deviceGeneration||!['idle','starting','monitoring','disconnected','stopped','error','ambiguous','not_configured'].includes(d.lifecycle)||!Array.isArray(d.devices)||d.devices.length>128||!d.devices.every(deviceValid)||![d.current_device,d.last_device].every(v=>v===null||deviceValid(v))||!Array.isArray(d.adapters)||!d.adapters.every(a=>a&&typeof a.id==='string'&&typeof a.label==='string'&&typeof a.transport==='string'&&typeof a.implemented==='boolean')||typeof d.runtime_configured!=='boolean'||typeof d.refresh_available!=='boolean'||typeof d.message!=='string')throw Error('设备列表协议无效或代次倒退');
    return d;
  }
  function describe(d){return `${d.name} · ${d.transport} · ${d.path}\nVID/PID ${d.vendor_id??'—'}/${d.product_id??'—'} · 轴/按钮 ${d.axis_count??'—'}/${d.button_count??'—'}\n适配器 ${d.adapter_id??'未适配'} · ${d.readable?'可读':'不可读'} · ${d.supported?'语义布局已适配（非实测通过）':d.reason}`;}
  function showDevices(data){
    deviceData=data;deviceGeneration=data.generation;
    const signature=JSON.stringify(data.devices.map(d=>[d.id,d.name,d.transport,d.supported,d.readable]));
    if(signature!==optionsSignature||!select.options.length){
      const old=select.value;select.replaceChildren(new Option('请选择实际设备',''));
      for(const d of data.devices){const option=new Option(d.name+' · '+d.transport+' · '+d.path+(d.supported?'':' · 未适配，仅节点'),d.id);option.disabled=!d.supported;option.title=d.reason;select.add(option);}
      const supported=data.devices.filter(d=>d.supported),current=supported.find(d=>d.id===data.current_device?.id);
      select.value=supported.some(d=>d.id===old)?old:current?current.id:supported.length===1?supported[0].id:'';optionsSignature=signature;
    }
    const shown=data.current_device||data.last_device;
    set(deviceInfo,data.current_device?'当前设备：'+data.current_device.name+' · '+data.current_device.transport:'暂无活动手柄'+(data.last_device?'；最后设备：'+data.last_device.name+' · '+data.last_device.transport:''));
    set(deviceDetail,(data.current_device?'当前设备详情：':data.last_device?'最后设备详情：':'设备详情：')+(shown?'\n'+describe(shown):'暂无记录')+'\n已枚举 '+data.devices.length+' 个输入节点（节点不等于配对或连接，也不代表控制通过）');
    set(deviceMessage,data.lifecycle+' · '+data.message+(data.refresh_result?' · 刷新结果：'+data.refresh_result:''));
    set(adapterInfo,data.adapters.map(a=>a.label+' / '+a.transport+'：'+(a.implemented?'适配接口已实现':'预留，尚未实现')).join('；')+'。其它型号需独立适配和实测，蓝牙不配对。');
    refresh.disabled=refreshBusy||!data.refresh_available;select.disabled=refreshBusy;
  }
  async function devices(){
    if(stopped||document.hidden||deviceBusy||refreshBusy)return;deviceBusy=true;const epoch=deviceEpoch,controller=new AbortController();deviceAbort=controller;const timer=setTimeout(()=>controller.abort(),1500);
    try{const response=await fetch('/api/input-devices',{cache:'no-store',credentials:'same-origin',signal:controller.signal});if(!response.ok)throw Error('设备接口 HTTP '+response.status);const data=devicesValid(await response.json());if(stopped||document.hidden||epoch!==deviceEpoch)return;showDevices(data);}
    catch(error){if(!stopped&&epoch===deviceEpoch){const previous=deviceData&&(deviceData.current_device||deviceData.last_device);set(deviceInfo,previous?'上次设备信息（非当前）：'+previous.name+' · '+previous.transport:'设备信息不可用');set(deviceDetail,previous?'上次设备详情（非当前）：\n'+describe(previous):'暂无记录');set(deviceMessage,error.message);}}
    finally{clearTimeout(timer);if(deviceAbort===controller)deviceAbort=null;deviceBusy=false;}
  }
  refresh.addEventListener('click',async event=>{
    if(!event.isTrusted||stopped||document.hidden||refreshBusy)return;
    refreshBusy=true;deviceEpoch++;deviceAbort?.abort();generation++;abort?.abort();expires=0;clear('刷新设备中；旧原始值/候选已撤销，不自动授权');refresh.disabled=true;select.disabled=true;
    set(refreshMessage,'正在刷新设备；旧实例和会话将结束，不自动授权');
    const epoch=deviceEpoch,controller=new AbortController();refreshAbort=controller;const timer=setTimeout(()=>controller.abort(),5000);
    try{
      const session=await fetch('/api/session',{cache:'no-store',credentials:'same-origin',signal:controller.signal});if(!session.ok)throw Error('会话 HTTP '+session.status);const token=(await session.json()).csrf_token;if(typeof token!=='string'||!token.length)throw Error('会话令牌无效');
      if(stopped||document.hidden||epoch!==deviceEpoch)return;
      const response=await fetch('/api/input-refresh',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Panel-Token':token},body:JSON.stringify({action:'refresh_input',device_id:(deviceData?.devices.filter(d=>d.supported).length>1?select.value:null)||null}),signal:controller.signal});
      if(!response.ok)throw Error('刷新 HTTP '+response.status);const data=devicesValid(await response.json());
      if(!['started','no_device','unsupported','multiple_devices','not_found','busy_external','not_configured','error'].includes(data.refresh_result))throw Error('刷新结果无效');
      if(stopped||document.hidden||epoch!==deviceEpoch)return;showDevices(data);set(refreshMessage,'刷新结果：'+data.refresh_result+' · '+data.message);clear('刷新已返回；等待新的真实原生状态，仍需物理Start授权');
    }catch(error){if(!stopped&&epoch===deviceEpoch)set(refreshMessage,'刷新未确认：'+error.message+'；不自动重试，请核实设备状态');}
    finally{clearTimeout(timer);if(refreshAbort===controller)refreshAbort=null;refreshBusy=false;refresh.disabled=deviceData?!deviceData.refresh_available:false;select.disabled=false;}
  });
  function valid(s){
    if(!s||s.source!=='robot_usb_guard'||![2,3].includes(s.api_version)||typeof s.actuator_attached!=='boolean'||typeof s.motion_enabled!=='boolean'||s.motion_enabled&&!s.actuator_attached||s.running!==true||s.local_input_available!==true)throw Error('原生来源或状态无效');
    if(s.api_version===2&&(s.actuator_attached||s.motion_enabled||s.executor?.output_enabled===true))throw Error('API2不得启用输出');
    if(s.api_version===3){const e=s.executor;if(!e||!['disabled','waiting','zero_requested','target_requested','fault','closed'].includes(e.state)||typeof e.output_enabled!=='boolean'||typeof e.last_write_ok!=='boolean'||e.physical_stop_confirmed!==false||e.output_enabled&&!s.actuator_attached||e.state==='disabled'&&e.output_enabled)throw Error('API3执行模式字段无效');}
    for(const k of ['elapsed_ms','sequence','source_sequence','source_observed_ms','source_age_ms','lease_remaining_ms'])if(!int(s[k]))throw Error('原生时钟/序号无效');
    for(const k of ['boot_id','session_id','controller_id','command_sequence'])if(!id(s[k]))throw Error('原生ID无效');
    for(const k of ['guard_armed','neutral_confirmed','stop_latched'])if(typeof s[k]!=='boolean')throw Error('守卫状态无效');
    if(s.source_observed_ms>s.elapsed_ms||s.source_age_ms!==s.elapsed_ms-s.source_observed_ms)throw Error('源时钟关系无效');
    if(!s.raw||!s.candidate)throw Error('缺少原始值');
    for(const k of ['x','y'])if(s.raw[k]!==null&&(!Number.isInteger(s.raw[k])||s.raw[k]<-32768||s.raw[k]>32767))throw Error('原始轴无效');
    for(const k of ['rb','start','b_stop','a_clear','back'])if(s.raw[k]!==null&&typeof s.raw[k]!=='boolean')throw Error('原始按钮无效');
    if(!Number.isFinite(s.candidate.linear_mps)||!Number.isFinite(s.candidate.angular_radps)||Math.abs(s.candidate.linear_mps)>.25||Math.abs(s.candidate.angular_radps)>.6)throw Error('候选值无效');
  }
  async function sample(){
    if(stopped||document.hidden||busy||refreshBusy)return;busy=true;const epoch=generation,start=performance.now(),controller=new AbortController();abort=controller;
    const timeout=setTimeout(()=>controller.abort(),180);
    try{
      const response=await fetch('/api/input-observer',{cache:'no-store',credentials:'same-origin',signal:controller.signal});if(!response.ok)throw Error('观测接口 HTTP '+response.status);
      const data=await response.json();if(stopped||epoch!==generation||document.hidden)return;
      if(data.observer_api_version!==1||data.source!=='robot_input_observer')throw Error('面板观测协议无效');
      if(!data.fresh||data.status!=='fresh'||!data.snapshot){expires=0;clear((data.status||'无观测')+' · '+(data.reason||'没有当前值'));return;}
      if(typeof data.run_id!=='string'||!(/^[0-9a-f]{32}$/).test(data.run_id)||typeof data.instance_id!=='string'||!(/^[0-9a-f]{32}:[1-9][0-9]*:[0-9]+$/).test(data.instance_id)||!data.instance_id.startsWith(data.run_id+':')||!Number.isFinite(data.total_age_ms)||data.total_age_ms<0)throw Error('观测身份/年龄无效');
      const s=data.snapshot;valid(s);const mode=String(s.api_version)+':'+s.actuator_attached+':'+s.motion_enabled+':'+(s.executor?.output_enabled??false);
      if(s.api_version===3)modeWarning.hidden=false;
      const now=performance.now(),age=data.total_age_ms+now-start,key=data.instance_id+'/'+s.boot_id+'/'+mode;
      if(last&&last.key!==key){last={key,seq:s.sequence,elapsed:s.elapsed_ms,observed:s.source_observed_ms};progressExpires=now+250-age;expires=0;clear('观测来源已变更；已撤销旧值，等待新采样');return;}
      if(last&&(s.sequence<last.seq||s.elapsed_ms<last.elapsed||s.source_observed_ms<last.observed))throw Error('来源时钟/序号倒退');
      if(!last||s.elapsed_ms>last.elapsed)progressExpires=now+250-age;
      last={key,seq:s.sequence,elapsed:s.elapsed_ms,observed:s.source_observed_ms};
      if(age>=250||now>=progressExpires)throw Error('观测数据过期');
      modeWarning.hidden=s.api_version!==3;set(heading,s.api_version===3?'机器人 robot_usb_guard API3 · 本页只读，真实输出模式':'机器人 robot_usb_guard API2 · 候选观测，无执行器');
      if(s.api_version===3)set(modeWarning,'真实输出模式已由本机配置启用；需现场看护。写入/发布不是物理运动或停车确认。');
      expires=now+250-age;set(status,s.api_version===3?'当前观测有效；写入状态不等于物理反馈':'当前观测有效；该观测器无执行器输出');
      const show=v=>v===null?'—':typeof v==='boolean'?v?'按下':'松开':String(v);
      set(fields.get('phase'),String(data.phase??'—'));set(fields.get('source'),'robot_usb_guard / '+s.boot_id);
      set(fields.get('axes'),show(s.raw.x)+' / '+show(s.raw.y));set(fields.get('buttons'),['rb','start','b_stop','a_clear','back'].map(k=>show(s.raw[k])).join(' / '));
      set(fields.get('guard'),(s.guard_armed?'已授权':'未授权')+' / '+s.session_id);set(fields.get('neutral'),(s.neutral_confirmed?'已确认':'未确认')+' / '+(s.stop_latched?'已锁存':'未锁存'));
      set(fields.get('lease'),(s.lease_remaining_ms/1000).toFixed(1)+' 秒');set(fields.get('candidate'),s.candidate.linear_mps.toFixed(3)+' m/s / '+s.candidate.angular_radps.toFixed(3)+' rad/s');
      set(fields.get('executor'),s.api_version===3?`${s.executor.state} · output_enabled=${s.executor.output_enabled} · last_write_ok=${s.executor.last_write_ok} · 未确认物理停止`:'disabled · 无执行器');
      set(fields.get('age'),s.source_age_ms+' ms / '+Math.ceil(age)+' ms（含请求往返上界）');set(fields.get('reason'),[s.stop_reason,s.guard_expiry_reason,s.runtime_reason].join(' / '));
    }catch(error){if(!stopped&&epoch===generation){expires=0;clear(error.message);}}
    finally{clearTimeout(timeout);if(abort===controller)abort=null;busy=false;}
  }
  function pause(){generation++;deviceEpoch++;abort?.abort();deviceAbort?.abort();refreshAbort?.abort();clearInterval(poll);clearInterval(watch);clearInterval(devicePoll);poll=watch=devicePoll=null;expires=0;clear('页面不可见；当前值已清除');}
  function resume(){if(stopped||document.hidden)return;poll=setInterval(()=>void sample(),50);devicePoll=setInterval(()=>void devices(),1000);watch=setInterval(()=>{if(expires&&performance.now()>=Math.min(expires,progressExpires)){expires=0;clear('观测数据过期；当前值已清除');}},25);void sample();void devices();}
  function visibility(){pause();if(!document.hidden)resume();}
  document.addEventListener('visibilitychange',visibility);clear('等待机器人端真实USB观测');resume();
  return ()=>{stopped=true;pause();document.removeEventListener('visibilitychange',visibility);};
}
