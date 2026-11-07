// Trusted local interaction only; HTTP enqueue is never guard acceptance.
import {mountControlHistory} from './control-history.mjs';
export function renderKeyboardControl(container){
  const create=(tag,value)=>{const n=document.createElement(tag);n.textContent=value;return n;};
  const set=(n,value)=>{if(n.textContent!==value)n.textContent=value;};
  const heading=create('p','本机键盘候选控制 · 等待执行模式状态。WASD / 方向键，按住 Space 使能；只有实体 USB A 可以清停止锁存。');container.append(heading);
  const modeWarning=create('p','真实输出模式已由本机配置启用；需现场看护。候选、写入或发布结果不是物理运动/停车反馈。');modeWarning.className='execution-mode-warning';modeWarning.hidden=true;container.append(modeWarning);
  const modeKey=s=>(s.native_api_version??'legacy')+':'+s.actuator_attached+':'+s.motion_enabled+':'+(s.executor?.output_enabled??false);
  function modeUncertain(){if(!modeWarning.hidden){set(modeWarning,'此前已报告真实输出模式；需现场看护。输出状态暂不可确认，物理停止未确认。');set(heading,'本机键盘候选控制 · 输出状态暂不可确认；旧意图不恢复');}}
  function observeMode(s){
    const real=s.native_api_version===3||s.actuator_attached||s.motion_enabled;
    if(real)modeWarning.hidden=false;
    else if(s.available&&s.execution_state_current!==false)modeWarning.hidden=true;
    if(!s.available||s.execution_state_current===false){modeUncertain();return;}
    if(real){set(modeWarning,'真实输出模式已由本机配置启用；需现场看护。候选、写入或发布结果不是物理运动/停车反馈。');set(heading,'本机键盘候选控制 · 真实输出模式；Space使能，只有实体USB A可清锁存');}
    else set(heading,'本机键盘候选控制 · 无执行器、无电机输出；Space使能，只有实体USB A可清锁存');
  }
  const message=create('p','等待真实状态；请先松开全部控制键。'),report=create('pre','—');message.className='keyboard-message';report.className='keyboard-report';
  const actions=create('div','');actions.className='keyboard-actions';
  const buttons={};for(const [id,label]of [['open','取得会话'],['arm','申请控制权'],['release','释放'],['stop','停止']]){const b=create('button',label);b.type='button';buttons[id]=b;actions.append(b);}
  const pad=create('div','');pad.className='keyboard-pad';
  container.append(actions,message,report,pad);
  const cleanupHistory=mountControlHistory(container);
  const keys=new Set(),pointers=new Map(),codes=new Set(['KeyW','KeyA','KeyS','KeyD','ArrowUp','ArrowDown','ArrowLeft','ArrowRight','Space']);
  let disposed=false,epoch=0,state=null,seenAt=0,ticket=null,csrf=null,control=null,sequence=0,postBusy=false,getBusy=false,getAbort=null,lastMapped=-1;
  const safetyQueue=[];
  let ownedSession=null,armPending=0,neutralExpected=null,enableReady=false,enableHeld=false,lastSend=0;
  const uint=v=>typeof v==='string'&&/^(0|[1-9][0-9]{0,19})$/.test(v)&&BigInt(v)<=18446744073709551615n;
  const int=v=>Number.isSafeInteger(v)&&v>=0,hex=v=>typeof v==='string'&&/^[0-9a-f]{32}$/.test(v);
  const focus=()=>document.hasFocus()&&!document.hidden;
  const fresh=()=>state&&state.available&&performance.now()-seenAt<200;
  const down=(...names)=>names.some(k=>keys.has(k)||[...pointers.values()].includes(k));
  const raw=()=>[down('KeyW','ArrowUp'),down('KeyS','ArrowDown'),down('KeyA','ArrowLeft'),down('KeyD','ArrowRight'),down('Space')];
  const neutral=()=>!raw().some(Boolean);
  const owns=()=>fresh()&&control&&state.owner==='keyboard'&&state.armed&&ownedSession===state.session_id;
  function clearHeld(){keys.clear();pointers.clear();enableReady=enableHeld=false;neutralExpected=null;}
  function update(){
    buttons.open.disabled=!!control||postBusy||!fresh()||!focus()||!neutral();
    buttons.arm.disabled=!control||postBusy||!fresh()||!focus()||!neutral()||state.stop_latched||owns()||!!armPending;
    buttons.release.disabled=!control;buttons.stop.disabled=!control;
    buttons.arm.title=!neutral()?'请先松开方向与 Space，再申请控制权':state?.stop_latched?'仅实体 USB A 可清停止锁存':'实际授权后须原始中立确认，再重新按使能';
    for(const b of pad.children)b.setAttribute('aria-pressed',String(down(b.dataset.key)));
  }
  function freeze(action,values=null){
    const now=performance.now();if(!control||sequence>=Number.MAX_SAFE_INTEGER)return null;
    if(action==='close')return {body:{action,control_token:control.token,sequence:String(++sequence),clock_ticket:null,elapsed_ms:0,session_id:state?.session_id||'0',keys:null},sampledAt:now};
    if(!ticket||now-ticket.at>=1000)return null;
    const mapped=ticket.server+Math.floor(now-ticket.at);
    if(mapped<lastMapped)return null;
    lastMapped=mapped;
    return {body:{action,control_token:control.token,sequence:String(++sequence),clock_ticket:ticket.id,elapsed_ms:Math.floor(now-ticket.at),session_id:state?.session_id||'0',keys:values},sampledAt:now};
  }
  async function request(body,signal){
    const response=await fetch('/api/keyboard',{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json','X-Panel-Token':csrf},body:JSON.stringify(body),signal});
    if(!response.ok)throw Error('键盘请求 HTTP '+response.status);
    const value=await response.json();return {code:response.status,value};
  }
  async function drainSafety(){
    if(postBusy||!safetyQueue.length)return;
    const packet=safetyQueue.shift();
    if(packet.body.action!=='close'&&performance.now()-packet.sampledAt>=250){if(!disposed)set(message,'安全动作采样已过期，未确认；仍将撤销页面会话。');void drainSafety();return;}
    postBusy=true;const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),180);
    try{const {code,value}=await request(packet.body,abort.signal);if(packet.body.action==='close'){if(code!==200||value.status!=='closed')throw Error('关闭未确认');}else{if(code!==202||value.status!=='enqueued')throw Error('安全动作未入队');if(!disposed)set(message,(packet.body.action==='stop'?'停止':'释放')+'已入队；以实际原生结果为准。');}}
    catch(error){if(!disposed)set(message,'安全动作/关闭请求未确认：'+error.message+'；本地意图已撤销。');}
    finally{clearTimeout(timer);postBusy=false;void drainSafety();if(!disposed)update();}
  }
  function invalidate(reason,action='close'){
    const packet=freeze(action),close=action!=='close'?freeze('close'):null;
    epoch++;control=null;ownedSession=null;armPending=0;clearHeld();set(report,'— 当前候选已撤销');set(message,reason+'；需手动重新取得会话。');
    modeUncertain();
    if(packet)safetyQueue.push(packet);if(close)safetyQueue.push(close);
    if(!packet&&action!=='close')set(message,'安全动作未能采样提交；本地已撤销，正在关闭会话，停止尚未确认。');
    void drainSafety();update();
  }
  async function post(action,values=null){
    if(postBusy||!focus()||!fresh())return;
    const packet=freeze(action,values);if(!packet){if(ticket&&performance.now()-ticket.at<1000){if(action==='arm')armPending=0;set(message,'采样时钟映射倒退，已丢弃；等待新的真实采样，不自动申请。');}else invalidate('票据失效');return;}
    const generation=epoch,abort=new AbortController();postBusy=true;update();const timer=setTimeout(()=>abort.abort(),180);
    try{
      if(performance.now()-packet.sampledAt>=250)throw Error('采样已过期');
      const {code,value}=await request(packet.body,abort.signal);
      if(disposed||epoch!==generation)return;
      if(code!==202||value.status!=='enqueued'||!uint(value.sequence)||value.generation!==control.generation)throw Error('入队响应无效');
      if(action==='keys'&&values.every(v=>!v)&&neutral()&&neutralExpected===null)neutralExpected=value.sequence;
      set(message,'已入队；实际是否接受请看原生结果与 owner。');
    }catch(error){if(epoch===generation&&!disposed)invalidate(error.message);}
    finally{clearTimeout(timer);postBusy=false;void drainSafety();update();}
  }
  function validate(s){
    if(!s||s.keyboard_api_version!==1||typeof s.available!=='boolean'||typeof s.reason!=='string'||typeof s.motion_enabled!=='boolean'||typeof s.actuator_attached!=='boolean'||s.motion_enabled&&!s.actuator_attached||!int(s.server_clock_ms)||!(s.clock_ticket===null||hex(s.clock_ticket))||!(s.generation===null||int(s.generation))||!(s.boot_id===null||uint(s.boot_id))||!['none','gamepad','keyboard'].includes(s.owner)||!uint(s.session_id)||!uint(s.keyboard_sequence)||typeof s.keyboard_result!=='string')throw Error('键盘状态协议无效');
    for(const k of ['armed','stop_latched','neutral_confirmed'])if(typeof s[k]!=='boolean')throw Error('守卫字段无效');
    if(s.execution_state_current!==undefined&&typeof s.execution_state_current!=='boolean')throw Error('执行状态新鲜度字段无效');
    if(s.native_api_version!==undefined&&s.native_api_version!==null){
      if(![2,3].includes(s.native_api_version))throw Error('原生执行协议版本无效');
      if(s.native_api_version===2&&(s.motion_enabled||s.actuator_attached||s.executor?.output_enabled===true))throw Error('API2不得启用输出');
      if(s.native_api_version===3){const e=s.executor;if(!e||!['disabled','waiting','zero_requested','target_requested','fault','closed'].includes(e.state)||typeof e.output_enabled!=='boolean'||typeof e.last_write_ok!=='boolean'||e.physical_stop_confirmed!==false||e.output_enabled&&!s.actuator_attached||e.state==='disabled'&&e.output_enabled)throw Error('API3执行器字段无效');}
    }
    if(!s.available&&s.candidate!==null)throw Error('不可用状态不得携带当前候选');
    if(s.available&&s.execution_state_current===false)throw Error('可用输入与过期执行状态冲突');
    if(s.available&&(!int(s.generation)||!uint(s.boot_id)||!hex(s.clock_ticket)))throw Error('可用状态缺少实例或票据');
    for(const k of ['lease_remaining_ms','command_remaining_ms'])if(!int(s[k]))throw Error('租约字段无效');
    if(s.candidate!==null&&(!s.candidate||!Number.isFinite(s.candidate.linear_mps)||!Number.isFinite(s.candidate.angular_radps)||Math.abs(s.candidate.linear_mps)>.25||Math.abs(s.candidate.angular_radps)>.6))throw Error('候选字段无效');
    return s;
  }
  async function poll(){
    if(disposed||document.hidden||getBusy)return;getBusy=true;const abort=new AbortController();getAbort=abort;const timer=setTimeout(()=>abort.abort(),180),generation=epoch,start=performance.now();
    try{
      const response=await fetch('/api/keyboard',{cache:'no-store',credentials:'same-origin',signal:abort.signal});if(!response.ok)throw Error('状态 HTTP '+response.status);
      const s=validate(await response.json()),received=performance.now();if(disposed||document.hidden||epoch!==generation)return;
      if(received-start>=200)throw Error('状态响应过期');
      if(s.native_api_version===3||s.motion_enabled||s.actuator_attached)modeWarning.hidden=false;
      if(state&&s.generation===state.generation&&s.boot_id===state.boot_id&&(s.server_clock_ms<state.server_clock_ms||BigInt(s.keyboard_sequence)<BigInt(state.keyboard_sequence)))throw Error('源时钟/序号倒退');
      observeMode(s);
      if(state&&modeKey(s)!==modeKey(state)){state=s;seenAt=start;invalidate('执行模式改变，旧键盘意图已撤销');return;}
      if(control&&(s.generation!==control.generation||s.boot_id!==control.boot)){invalidate('运行时 generation / boot 已改变');return;}
      state=s;seenAt=start;ticket=hex(s.clock_ticket)?{id:s.clock_ticket,at:received,server:s.server_clock_ms}:null;
      if(!s.available||!ticket){if(control)invalidate(s.reason||'输入不可用');else {set(report,'— 无有效输入');set(message,s.reason||'输入不可用');}update();return;}
      if(control&&armPending&&s.owner==='keyboard'&&s.armed){ownedSession=s.session_id;armPending=0;neutralExpected=null;enableReady=enableHeld=false;}
      if(control&&ownedSession&&(s.owner!=='keyboard'||!s.armed||s.session_id!==ownedSession)){invalidate('控制权已释放、过期或被切换');return;}
      if(owns()&&neutralExpected!==null&&BigInt(s.keyboard_sequence)>=BigInt(neutralExpected)&&s.keyboard_result==='Accepted'&&s.neutral_confirmed&&neutral())enableReady=true;
      if(armPending&&received-armPending>500){invalidate('未获得实际控制权');return;}
      const candidate=s.candidate?`${s.candidate.linear_mps.toFixed(3)} m/s / ${s.candidate.angular_radps.toFixed(3)} rad/s`:'—';
      set(report,`实际 owner：${s.owner} · armed：${s.armed}\nsession：${s.session_id} · 回中：${s.neutral_confirmed} · 停止锁存：${s.stop_latched}\n租约：${(s.lease_remaining_ms/1000).toFixed(1)} 秒 · 命令：${s.command_remaining_ms} ms\n候选：${candidate}\n原生结果：${s.keyboard_result} · 序号 ${s.keyboard_sequence}`);
      update();
    }catch(error){if(!disposed&&epoch===generation){if(control)invalidate(error.message);else{state=null;ticket=null;modeUncertain();set(report,'— 当前状态无效');set(message,error.message);}update();}}
    finally{clearTimeout(timer);getBusy=false;if(getAbort===abort)getAbort=null;}
  }
  buttons.open.addEventListener('click',async e=>{
    if(!e.isTrusted||disposed||control||postBusy||!fresh()||!focus())return;
    if(!neutral()){set(message,'请先松开方向和 Space，再取得会话。');return;}
    const generation=epoch,abort=new AbortController();postBusy=true;update();const timer=setTimeout(()=>abort.abort(),500);
    try{
      const response=await fetch('/api/session',{cache:'no-store',signal:abort.signal});if(!response.ok)throw Error('无法取得CSRF令牌');csrf=(await response.json()).csrf_token;if(typeof csrf!=='string'||!csrf)throw Error('令牌无效');
      if(disposed||epoch!==generation||!focus())return;
      const {code,value}=await request({action:'open'},abort.signal);
      if(code!==200||value.status!=='opened'||!hex(value.control_token)||!int(value.generation)||!uint(value.boot_id)||value.control_expires_in_ms!==30000)throw Error('会话响应无效');
      if(disposed||epoch!==generation||!focus()||!neutral()||!fresh()||value.generation!==state.generation||value.boot_id!==state.boot_id){
        safetyQueue.push({body:{action:'close',control_token:value.control_token,sequence:'1',clock_ticket:null,elapsed_ms:0,session_id:'0',keys:null},sampledAt:performance.now()});return;
      }
      control={token:value.control_token,generation:value.generation,boot:value.boot_id,openedAt:performance.now()};sequence=0;lastMapped=-1;clearHeld();set(message,'会话已取得；松开全部控制键，再手动申请控制权。');
    }catch(error){if(epoch===generation&&!disposed)set(message,error.message);}
    finally{clearTimeout(timer);postBusy=false;void drainSafety();update();}
  });
  buttons.arm.addEventListener('click',e=>{
    if(!e.isTrusted||!control)return;
    if(!neutral()){set(message,'请先松开方向和 Space；按住旧键时不能申请控制权。');return;}
    if(!fresh()||state.stop_latched||owns()||armPending||postBusy){set(message,'当前状态不允许申请；停止锁存须用实体 USB A 清除。');return;}
    clearHeld();armPending=performance.now();void post('arm');
  });
  for(const action of ['release','stop'])buttons[action].addEventListener('click',e=>{
    if(!e.isTrusted||!control)return;
    invalidate(action==='stop'?'本地意图已撤销，停止请求待原生确认':'本地意图已撤销，正在释放',action);
  });
  function changed(){set(report,'— 输入已改变，等待原生新状态');update();}
  function enablePress(){if(owns()&&enableReady&&state.neutral_confirmed)enableHeld=true;else set(message,'尚未完成实际授权与原始回中；完成后重新按下 Space/使能。');}
  function keyEvent(e){
    if(!e.isTrusted||!focus()||!codes.has(e.code)||/^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName))return;
    e.preventDefault();if(e.type==='keydown'){if(e.repeat||keys.has(e.code))return;keys.add(e.code);if(e.code==='Space')enablePress();}else{keys.delete(e.code);if(e.code==='Space')enableHeld=false;}changed();
  }
  for(const [code,label]of [['KeyW','前 W'],['KeyA','左 A'],['Space','按住使能'],['KeyD','右 D'],['KeyS','后 S']]){
    const b=create('button',label);b.type='button';b.dataset.key=code;pad.append(b);
    b.addEventListener('pointerdown',e=>{if(!e.isTrusted||!focus())return;e.preventDefault();b.setPointerCapture(e.pointerId);pointers.set(e.pointerId,code);if(code==='Space')enablePress();changed();});
    b.addEventListener('pointerup',e=>{if(!e.isTrusted)return;pointers.delete(e.pointerId);if(code==='Space')enableHeld=false;changed();});
    for(const event of ['pointercancel','lostpointercapture'])b.addEventListener(event,e=>{if(pointers.has(e.pointerId))invalidate('指针捕获中断，已清全部按住态');});
  }
  window.addEventListener('keydown',keyEvent);window.addEventListener('keyup',keyEvent);
  const blur=()=>invalidate('失焦/隐藏，当前意图已撤销');
  const visibility=()=>{if(document.hidden){getAbort?.abort();blur();}else void poll();};
  window.addEventListener('blur',blur);document.addEventListener('visibilitychange',visibility);
  const ticker=setInterval(()=>{
    if(disposed)return;
    if(control&&(!focus()||!fresh()||performance.now()-control.openedAt>=30000)){invalidate('页面状态或会话有效期失效');return;}
    if(!fresh()){set(report,'— 无新鲜状态');modeUncertain();}
    if(owns()&&!postBusy&&performance.now()-lastSend>=80){lastSend=performance.now();const values=raw();if(!neutralExpected||!enableReady){if(values.some(Boolean))return;void post('keys',[false,false,false,false,false]);}else void post('keys',[...values.slice(0,4),values[4]&&enableHeld]);}
    update();
  },25);
  const polling=setInterval(()=>void poll(),80);update();void poll();
  return ()=>{cleanupHistory();invalidate('已离开键盘控制页');disposed=true;getAbort?.abort();clearInterval(ticker);clearInterval(polling);window.removeEventListener('keydown',keyEvent);window.removeEventListener('keyup',keyEvent);window.removeEventListener('blur',blur);document.removeEventListener('visibilitychange',visibility);};
}
