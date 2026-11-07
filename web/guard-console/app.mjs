const $=id=>document.getElementById(id);
function text(id,value){const node=$(id);if(node.textContent!==value)node.textContent=value;}
function candidateValidity(valid){const node=$('candidate'),value=String(valid);if(node.dataset.valid!==value)node.dataset.valid=value;}
const codes=new Set(['KeyW','KeyA','KeyS','KeyD','ArrowUp','ArrowDown','ArrowLeft','ArrowRight','ShiftLeft','ShiftRight']);
const held=new Set(),virtual=new Set();
// Display-only observation; never copied into held/virtual or command packets.
const observedKeys=new Set();
let rawUnknown=false,healthySince=null,linkFault=false,linkDisconnected=false,initializationFailure='';
let state=null,token=null,controller=null,active=false,epoch=0,busy=false,polling=false,seenAt=0,progressAt=0,lastElapsed=-1,lastSequence=-1,sequence=0n,binding=null,abort=null,candidateAt=0,lastSent=-Infinity,nextSubmit=null;
let hasObservedState=false,stopRequest=0;
const focused=()=>document.hasFocus()&&!document.hidden;
function hide(message){candidateAt=0;candidateValidity(false);text('candidate','暂无有效候选值');if($('candidate').title!==message)$('candidate').title=message;}
function reset(message){epoch++;active=false;clearTimeout(nextSubmit);held.clear();virtual.clear();abort?.abort();hide(message);text('status',message);text('keys','按键：无');updateOperation();}
function fresh(){return state&&performance.now()-seenAt<200&&performance.now()-progressAt<200;}
function owns(s=state){return s?.guard_armed&&s.controller_id===controller&&s.owner==='keyboard'&&binding===`${s.boot_id}/${s.session_id}`;}
function healthyForArm(){return fresh()&&healthySince!==null&&performance.now()-healthySince>=1000;}
function rawDisplay(){text('raw-keys',rawUnknown?'原始按键观察：未知（失焦/隐藏已清除提交意图；不代表物理已松开）':'本页观察到的控制键：'+([...observedKeys].join(', ')||'无；仅代表本页已观察事件'));}
function loseRawObservation(){observedKeys.clear();rawUnknown=true;rawDisplay();}
function armBlockReason(){
  if(!token||!controller)return '本窗口身份未就绪';
  if(linkDisconnected)return '连接已断开；需主线恢复后端，再连续健康观测约1秒并手动取得';
  if(!healthyForArm())return '等待连续约1秒新鲜网关状态；期间任一过期都会重新计时，不恢复旧会话';
  if(!focused())return '请返回并聚焦本页面';
  if(state.stop_latched)return '策略停止已锁存，请先手动清除锁存';
  if(active)return '本窗口已取得会话；到期后需手动重新取得';
  if(held.size||virtual.size||observedKeys.size)return '请先松开全部已观察到的原始控制键（包括 Shift）';
  if(busy)return '正在等待请求确认';
  return '';
}
function updateOperation(){
  if(!fresh())healthySince=null;
  else if(healthySince===null)healthySince=performance.now();
  const reason=armBlockReason(),blocked=Boolean(reason),button=$('arm');
  if(button.disabled!==blocked)button.disabled=blocked;
  const explanation=reason||'可以手动取得10秒策略会话；取得后仍须原始回中确认';
  text('arm-reason',explanation);if(button.title!==explanation)button.title=explanation;
  let phase;
  if(linkDisconnected)phase='已断开；等待后端恢复';
  else if(!healthyForArm()&&!active)phase=hasObservedState||linkFault?'链路不稳定；等待连续健康观测':'连接未就绪；等待连续健康观测';
  else if(!fresh())phase='链路不稳定；状态已失效';
  else if(active&&owns())phase=state.neutral_confirmed?'输入已就绪':'已取会话，等待原始回中';
  else if(!blocked)phase='可取得会话';
  else phase='连接已就绪，取会话条件未满足';
  text('phase',phase);
  const lease=fresh()&&active&&owns()&&Number.isSafeInteger(state.lease_remaining_ms)&&state.lease_remaining_ms>=0 ? (state.lease_remaining_ms/1000).toFixed(1)+' 秒' : '—';
  text('lease',lease);
}
async function command(payload,signal){
  const sentAt=performance.now();
  const response=await fetch('/api/command',{method:'POST',headers:{'Content-Type':'application/json','X-Guard-Token':token},body:JSON.stringify(payload),signal:signal??AbortSignal.timeout(650),cache:'no-store'});
  if(!response.ok)throw new Error('command HTTP');
  const data=await response.json();return {ack:data.ack,ageMs:performance.now()-sentAt};
}
// ACK elapsed is the gateway's original clock. RTT is a conservative age upper
// bound, not a new issue time. Equal source elapsed never refreshes progress.
function adoptAck(ack,ageMs){
  if(!Number.isFinite(ageMs)||ageMs>=200||ack.source!=='robot_control_gateway'||ack.api_version!==2||!Number.isSafeInteger(ack.state_sequence)||!Number.isSafeInteger(ack.elapsed_ms)||ack.actuator_attached!==false||ack.motion_enabled!==false)throw new Error('ACK clock');
  if(state&&ack.boot_id!==state.boot_id)throw new Error('ACK boot');
  if(ack.state_sequence<lastSequence||(state&&ack.state_sequence<state.state_sequence))return false;
  if(ack.elapsed_ms<lastElapsed)throw new Error('ACK regression');
  const observedAt=performance.now()-ageMs;
  if(ack.elapsed_ms>lastElapsed){lastElapsed=ack.elapsed_ms;progressAt=observedAt;}
  lastSequence=ack.state_sequence;seenAt=observedAt;state=ack;
  hasObservedState=true;
  text('state',JSON.stringify(ack,null,2));
  return true;
}
function release(message){
  const old=state,wasOwn=owns(old);reset(message);binding=null;
  if(wasOwn&&token)void command({action:'release',boot_id:old.boot_id,session_id:old.session_id}).catch(()=>{});
}
function keyChanged(){epoch++;abort?.abort();hide('输入已变化，等待网关新确认');text('keys','按键：'+([...held,...virtual].join(', ')||'无'));updateOperation();}
async function poll(){
  if(polling)return;polling=true;
  let backendError=null;
  try{
    const response=await fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(250)});
    if(!response.ok)throw new Error('状态接口 HTTP '+response.status);
    const envelope=await response.json(),s=envelope.state;
    backendError=typeof envelope.error==='string'?envelope.error:null;
    text('diagnostic',initializationFailure+(backendError?'后端诊断：'+backendError:'后端未报告错误'));
    linkDisconnected=Boolean(envelope.failure_detail);
    if(!s||s.source!=='robot_control_gateway'||s.api_version!==2||s.actuator_attached!==false||s.motion_enabled!==false||!Number.isSafeInteger(s.state_sequence)||!Number.isSafeInteger(s.elapsed_ms)||!Number.isFinite(envelope.age_ms)||envelope.age_ms>200)throw new Error('invalid state');
    if(state&&s.boot_id===state.boot_id&&s.state_sequence<state.state_sequence)return;
    if(state&&s.boot_id!==state.boot_id){release('网关重启；需手动重取');lastSequence=-1;lastElapsed=-1;}
    if(s.state_sequence<lastSequence||s.elapsed_ms<lastElapsed)throw new Error('state regression');
    if(s.elapsed_ms>lastElapsed){lastElapsed=s.elapsed_ms;progressAt=performance.now()-envelope.age_ms;}
    lastSequence=s.state_sequence;seenAt=performance.now()-envelope.age_ms;state=s;
    hasObservedState=true;
    linkFault=false;linkDisconnected=false;
    if(active&&!owns())release('会话已撤销或到期；需手动重取');
    text('state',JSON.stringify(s,null,2));
    if(!active&&healthyForArm())text('status',s.stop_latched?'策略停止已锁存；清除后仍需手动取会话':'连续健康观测已满足；未取得本地策略会话');
  }catch(error){linkFault=true;healthySince=null;reset('状态失效；旧意图已撤销，等待连续健康观测后手动重新取得');state=null;binding=null;text('state','无有效状态');if(!backendError)text('diagnostic',initializationFailure+'页面诊断：'+(error.message==='invalid state'?'网关状态缺失、协议校验失败或年龄超过200ms':error.message));}
  finally{polling=false;updateOperation();}
}
async function submit(){
  if(!active||busy||!fresh()||!focused()||!owns()||performance.now()-lastSent<50)return;
  const down=(...keys)=>keys.some(k=>held.has(k)||virtual.has(k));
  const flags={deadman:down('ShiftLeft','ShiftRight'),forward:down('KeyW','ArrowUp'),backward:down('KeyS','ArrowDown'),left:down('KeyA','ArrowLeft'),right:down('KeyD','ArrowRight')};
  if(!state.neutral_confirmed&&Object.values(flags).some(Boolean)){hide('等待所有原始键松开并回中');return;}
  const start=performance.now(),generation=epoch,s=state,session=binding;
  lastSent=start;
  sequence=sequence>BigInt(s.command_sequence)?sequence+1n:BigInt(s.command_sequence)+1n;
  const payload={action:'keys',boot_id:s.boot_id,session_id:s.session_id,sequence:String(sequence),issued_ms:s.elapsed_ms,observed_sequence:s.state_sequence,...flags};
  const requestAbort=new AbortController();abort=requestAbort;busy=true;
  const timer=setTimeout(()=>requestAbort.abort(),200);
  try{
    const {ack,ageMs}=await command(payload,requestAbort.signal);
    if(generation!==epoch||!active||!focused()||binding!==session||!owns()||performance.now()-start>200)return;
    if(ack.boot_id!==s.boot_id||ack.session_id!==s.session_id||ack.controller_id!==controller||ack.command_sequence!==payload.sequence||ack.actuator_attached!==false||ack.motion_enabled!==false||ack.result!=='Accepted')throw new Error('ACK invalid');
    if(!Object.values(flags).some(Boolean)&&!ack.neutral_confirmed)throw new Error('neutral unconfirmed');
    if(!adoptAck(ack,ageMs))return;
    if(!fresh()){hide('候选确认已过期');return;}
    text('candidate',`网关候选：线 ${ack.candidate.linear_mps.toFixed(3)} m/s · 角 ${ack.candidate.angular_radps.toFixed(3)} rad/s`);
    candidateValidity(true);
    if($('candidate').title!=='新鲜 Accepted ACK；仅网关候选，无执行器')$('candidate').title='新鲜 Accepted ACK；仅网关候选，无执行器';
    candidateAt=performance.now();
    text('status',ack.neutral_confirmed?'策略会话采集中；无执行器':'等待原始回中');
  }catch{if(generation===epoch)release('命令确认失效；需手动重取');}
  finally{
    clearTimeout(timer);if(abort===requestAbort)abort=null;busy=false;
    updateOperation();
    if(generation===epoch&&active){clearTimeout(nextSubmit);nextSubmit=setTimeout(()=>void submit(),Math.max(0,50-(performance.now()-lastSent)));}
  }
}
$('arm').addEventListener('click',async()=>{
  const reason=armBlockReason();if(reason){text('status',reason);updateOperation();return;}
  reset('请求策略会话');const generation=epoch;busy=true;
  updateOperation();
  try{
    const {ack,ageMs}=await command({action:'arm',boot_id:state.boot_id});
    if(generation!==epoch||!focused()){
      if(ack.controller_id===controller)void command({action:'release',boot_id:ack.boot_id,session_id:ack.session_id}).catch(()=>{});
      return;
    }
    if(ack.result!=='Armed'||!ack.guard_armed||ack.controller_id!==controller||ack.actuator_attached!==false||ack.motion_enabled!==false)throw new Error('arm rejected');
    const adopted=adoptAck(ack,ageMs);
    if((!adopted&&!(state?.boot_id===ack.boot_id&&state.session_id===ack.session_id&&state.controller_id===controller&&state.guard_armed&&state.owner==='keyboard'))||!fresh())throw new Error('arm state stale');
    binding=`${ack.boot_id}/${ack.session_id}`;sequence=BigInt(ack.command_sequence);active=true;hide('等待原始回中确认');
  }catch{reset('取会话失败；请检查网关状态');}finally{busy=false;updateOperation();if(active)nextSubmit=setTimeout(()=>void submit(),0);}
});
$('release').addEventListener('click',()=>release('已释放'));
function policyStop(){
  const request=++stopRequest;reset('策略停止请求；本地已撤销');binding=null;updateOperation();
  text('stop-status','本地已撤销；等待网关策略停止确认');
  if(!token){text('stop-status','策略停止未确认：窗口身份不可用；本地已撤销，网关独立超时');return;}
  void command({action:'stop'}).then(({ack})=>{
    if(request!==stopRequest)return;
    if(ack.source!=='robot_control_gateway'||ack.result!=='Stopped'||ack.stop_latched!==true||ack.actuator_attached!==false||ack.motion_enabled!==false)throw new Error('stop unconfirmed');
    text('stop-status','网关已确认策略停止（无执行器，不代表物理停机验收）');
  }).catch(()=>{if(request===stopRequest)text('stop-status','策略停止未确认：请求失败或超时；本地已撤销，网关独立超时');});
}
$('stop').addEventListener('click',policyStop);
$('clear').addEventListener('click',()=>{release('清除停止锁存；不会自动取得会话');if(token)void command({action:'clear'}).catch(()=>{});});
$('mode').addEventListener('change',()=>release('来源已切换；需重新取得'));
for(const event of ['keydown','keyup'])window.addEventListener(event,e=>{
  if(!e.isTrusted||!focused())return;
  if(codes.has(e.code)){
    rawUnknown=false;
    if(event==='keydown')observedKeys.add(e.code);else observedKeys.delete(e.code);
    rawDisplay();updateOperation();
  }
  if(event==='keyup'&&held.delete(e.code))keyChanged();
  if(e.code==='Space'&&!/INPUT|SELECT|TEXTAREA/.test(e.target.tagName)){e.preventDefault();if(event==='keydown')policyStop();return;}
  if(!codes.has(e.code)||$('mode').value!=='keyboard'||/INPUT|SELECT|TEXTAREA/.test(e.target.tagName))return;
  e.preventDefault();if(event==='keydown'&&!held.has(e.code)){held.add(e.code);keyChanged();}
});
for(const b of $('virtual').querySelectorAll('button')){
  b.addEventListener('pointerdown',e=>{if(e.isTrusted&&active&&focused()&&$('mode').value==='virtual'){e.preventDefault();b.setPointerCapture(e.pointerId);virtual.add(b.dataset.key);keyChanged();}});
  for(const event of ['pointerup','pointercancel','lostpointercapture'])b.addEventListener(event,()=>{if(virtual.delete(b.dataset.key))keyChanged();});
}
window.addEventListener('blur',()=>{loseRawObservation();release('失焦已清意图；物理按键未知，需重新取得');});
document.addEventListener('visibilitychange',()=>{if(document.hidden){loseRawObservation();release('隐藏已清意图；物理按键未知，需重新取得');}});
window.addEventListener('pagehide',()=>{release('页面退出');clearInterval(loop);});
const loop=setInterval(()=>{
  if(candidateAt&&performance.now()-candidateAt>200)hide('候选确认已过期');
  if(active&&(!fresh()||!focused()))release('状态过期或失焦；需重新取得');
  if(!fresh())hide('无新鲜网关候选值');
  updateOperation();
  void poll();void submit();
},50);
async function initialize(){
  text('status','页面脚本已初始化；等待本窗口身份与真实网关');
  try{
    const response=await fetch('/api/session',{cache:'no-store',signal:AbortSignal.timeout(1000)});
    if(!response.ok)throw new Error('窗口身份接口 HTTP '+response.status);
    const session=await response.json();token=session.token;controller=session.controller_id;
    text('status','本窗口身份已初始化；等待真实网关');
    await poll();
  }catch(error){initializationFailure='窗口初始化失败：'+error.message+'；';text('diagnostic',initializationFailure);reset('本窗口初始化失败；请手动刷新');}
}
rawDisplay();updateOperation();void initialize();
