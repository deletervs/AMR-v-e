// Project-owned lifecycle controls only. Never starts anything through GET.
export function renderProjectServices(container){
  const make=(tag,value)=>{const node=document.createElement(tag);node.textContent=value;return node;};
  const set=(node,value)=>{if(node.textContent!==value)node.textContent=value;};
  container.append(make('p','仅管理本项目输入与只读采集进程，不启动原厂 ROS、驱动或系统服务。启动返回只表示进程启动，不等于传感器可用；停止不代表机器人已物理停车。'));
  const modeWarning=make('p','真实输出模式已由本机配置启用；需现场看护。此页仅报告模式，不提供执行器启用/续期或授权文件编辑。');modeWarning.className='execution-mode-warning';modeWarning.hidden=true;container.prepend(modeWarning);
  const dashboard=make('a','完整实时仪表盘');dashboard.href='/dashboard/?mode=live&source=robot';container.append(dashboard);
  const status=make('p','等待项目服务状态'),outcome=make('p','尚未执行服务操作');status.className='services-status';outcome.className='services-outcome';outcome.setAttribute('role','status');
  const list=make('div','');list.className='project-services';container.append(status,outcome,list);
  const rows=new Map(),buttons=new Map();
  for(const [id,title]of [['input','项目输入'],['telemetry','C++只读遥测源'],['camera','项目相机采集'],['executor','执行器代码状态']]){
    const section=make('section',''),heading=make('h2',title),value=make('p','— 当前状态未知'),message=make('p','等待查询');section.append(heading,value,message);
    if(id!=='executor'){
      const actions=make('div','');actions.className='services-actions';
      for(const [action,label]of [['start',id==='input'?'启动 / 重新发现':'启动项目采集'],['stop',id==='input'?'停止项目输入':'停止项目采集']]){const button=make('button',label);button.type='button';button.disabled=true;if(id!=='input')button.hidden=true;buttons.set(id+'/'+action,{service:id,action,button});button.addEventListener('click',event=>{if(event.isTrusted)void operate(id,action);});actions.append(button);}
      section.append(actions,make('p',id==='input'?'保留 B 停止锁存；多设备请到“手柄与安全层”选择。不会自动取得控制权。':id==='telemetry'?'独立的 C++ 只读遥测源，与普通本机系统采样线程分开；仅在后端明确配置时提供启停。':'仅本项目相机只读采集进程，启停能力由后端配置决定，不管理原厂驱动。'));
    }else section.append(make('p','仅报告后端 configured / active / fault 等真实状态；本页不提供执行器启用、续期或授权文件编辑。'));
    rows.set(id,{heading,value,message});list.append(section);
  }
  const string=v=>typeof v==='string'&&v.length<=2048;
  let disposed=false,generation=0,state=null,instance=null,seenAt=0,queryBusy=false,actionBusy=false,queryAbort=null,actionAbort=null,pollTimer=null,watchTimer=null;
  const fresh=()=>state&&performance.now()-seenAt<3000;
  function updateButtons(){for(const {service,action,button}of buttons.values()){const current=state?.services.find(s=>s.id===service),allowed=current?.actions.includes(action);button.hidden=service!=='input'&&!allowed;button.disabled=disposed||document.hidden||actionBusy||!fresh()||!allowed;}}
  function clear(reason){state=null;for(const row of rows.values()){set(row.value,'— 当前状态未知');set(row.message,'当前数据不可用');}if(!modeWarning.hidden)set(modeWarning,'此前已报告真实输出模式；需现场看护。输出状态暂不可确认，物理停止未确认。');set(status,reason);updateButtons();}
  function validate(data){
    if(!data||data.services_api_version!==1||data.source!=='robot_project_services'||!string(data.instance_id)||!data.instance_id||typeof data.motion_enabled!=='boolean'||!(typeof data.actuator_attached==='boolean'||data.actuator_attached===undefined&&data.motion_enabled===false)||data.motion_enabled&&data.actuator_attached!==true||!Array.isArray(data.services)||data.services.length>4)throw Error('项目服务协议无效');
    const ids=new Set();
    for(const service of data.services){
      if(!service||!rows.has(service.id)||ids.has(service.id)||!string(service.title)||!string(service.lifecycle)||typeof service.running!=='boolean'||typeof service.available!=='boolean'||!string(service.message)||!Array.isArray(service.actions)||service.actions.length>2||new Set(service.actions).size!==service.actions.length||service.actions.some(action=>!['input','telemetry','camera'].includes(service.id)||!['start','stop'].includes(action)))throw Error('项目服务字段无效');
      ids.add(service.id);
    }
    return data;
  }
  function show(data){
    if(instance!==null&&instance!==data.instance_id){generation++;actionAbort?.abort();clear('面板实例已改变；旧状态已清除');set(outcome,'实例已改变；先前操作结果不能用于当前实例，不自动重试。');}
    instance=data.instance_id;state=data;seenAt=performance.now();
    modeWarning.hidden=!(data.motion_enabled||data.actuator_attached);
    if(!modeWarning.hidden)set(modeWarning,'真实输出模式已由本机配置启用；需现场看护。此页仅报告模式，不提供执行器启用/续期或授权文件编辑。');
    for(const [id,row]of rows){const service=data.services.find(s=>s.id===id);if(service){set(row.heading,service.title);set(row.value,`生命周期：${service.lifecycle} · 运行：${service.running?'是':'否'} · 可用：${service.available?'是':'否'}`);set(row.message,service.message);}else{set(row.value,'— 后端未提供状态');set(row.message,'无可用操作');}}
    set(status,data.motion_enabled||data.actuator_attached?'项目服务状态已读取 · 真实输出配置在本机管理，本页不可启用':'项目服务状态已读取 · 输出模式未启用，本页无启用入口');updateButtons();
  }
  async function query(){
    if(disposed||document.hidden||queryBusy||actionBusy)return;queryBusy=true;const epoch=generation,abort=new AbortController();queryAbort=abort;const timer=setTimeout(()=>abort.abort(),1500);
    try{const response=await fetch('/api/services',{method:'GET',credentials:'same-origin',cache:'no-store',signal:abort.signal});if(!response.ok)throw Error('HTTP '+response.status);const data=validate(await response.json());if(disposed||document.hidden||epoch!==generation)return;show(data);}
    catch(error){if(!disposed&&epoch===generation)clear('项目服务暂不可读：'+error.message);}
    finally{clearTimeout(timer);if(queryAbort===abort)queryAbort=null;queryBusy=false;}
  }
  async function operate(service,action){
    const current=state?.services.find(s=>s.id===service);
    if(!['input','telemetry','camera'].includes(service)||disposed||document.hidden||actionBusy||!fresh()||!current?.actions.includes(action))return;
    const title=current.title;
    actionBusy=true;generation++;queryAbort?.abort();const epoch=generation,expectedInstance=instance,abort=new AbortController();actionAbort=abort;const timer=setTimeout(()=>abort.abort(),1500);
    clear('正在等待项目服务操作响应；当前状态待重新读取');set(outcome,title+'：'+(action==='start'?service==='input'?'正在请求重新发现并建立未授权输入；不自动授权。':'正在请求启动只读采集进程；不表示传感器已可用。':'正在请求停止本项目进程；不表示机器人已物理停车。'));
    try{
      const session=await fetch('/api/session',{method:'GET',credentials:'same-origin',cache:'no-store',signal:abort.signal});if(!session.ok)throw Error('会话 HTTP '+session.status);const sessionData=await session.json(),token=sessionData.csrf_token;if(!string(token)||!token)throw Error('会话令牌无效');
      if(sessionData.instance_id!==expectedInstance)throw Error('面板实例已改变，未提交操作；请重新读取当前状态');
      if(disposed||document.hidden||epoch!==generation||instance!==expectedInstance)return;
      const response=await fetch('/api/services',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Panel-Token':token},body:JSON.stringify({service,action}),signal:abort.signal});
      if(!response.ok)throw Error(response.status===409?'动作冲突 HTTP 409':response.status===503?'服务未启动或不可用 HTTP 503':'操作 HTTP '+response.status);
      if(response.status!==200)throw Error('操作响应状态无效');const data=await response.json();
      if(disposed||document.hidden||epoch!==generation||instance!==expectedInstance)return;
      if(data.status!=='completed'||data.service!==service||data.action!==action||!data.detail||typeof data.detail!=='object'||Array.isArray(data.detail))throw Error('操作结果协议无效');
      const detail=[data.detail.lifecycle,data.detail.refresh_result,data.detail.message].filter(string).join(' · ');
      set(outcome,title+'：'+(action==='start'?service==='input'?'建立未授权输入操作已返回；不自动授权。':'启动操作已返回，仅表示进程启动，不等于传感器可用。':'项目进程停止操作已返回；不代表机器人已物理停车。')+(detail?' 后端：'+detail:''));
    }catch(error){if(!disposed&&epoch===generation)set(outcome,'操作未确认：'+error.message+'。不自动重试；请求中断不代表后端已回滚，请等待重新读取状态。');}
    finally{clearTimeout(timer);if(actionAbort===abort)actionAbort=null;actionBusy=false;updateButtons();if(!disposed&&!document.hidden)void query();}
  }
  function pause(){generation++;queryAbort?.abort();actionAbort?.abort();if(actionBusy)set(outcome,'页面离开或隐藏，操作结果未确认；请求取消不代表后端回滚，不会自动重试。');clearInterval(pollTimer);clearInterval(watchTimer);pollTimer=watchTimer=null;clear('页面不可见；当前服务状态已清除');}
  function resume(){if(disposed||document.hidden)return;pollTimer=setInterval(()=>void query(),1000);watchTimer=setInterval(()=>{if(state&&!fresh())clear('服务状态已过期；等待重新读取');},250);void query();}
  function visibility(){pause();if(!document.hidden)resume();}
  document.addEventListener('visibilitychange',visibility);resume();
  return ()=>{disposed=true;pause();document.removeEventListener('visibilitychange',visibility);};
}
