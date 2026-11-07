import {pages,icon} from './pages.mjs';
const POWER_UI_ENABLED=false;
const $=id=>document.getElementById(id),text=(node,value)=>{if(node.textContent!==value)node.textContent=value;};
let current=pages()[0].id,state=null,lastElapsed=-1,progressAt=0,busy=false,requestAbort=null,powerBusy=false,powerSent=false;
let exitBusy=false,exiting=false,exitToken=null;
let navigationGroup=0,pageCleanup=null,lastRenderedPage=null;
function cleanupPage(){const cleanup=pageCleanup;pageCleanup=null;if(cleanup){try{cleanup();}catch{ /* A provider cleanup must not prevent panel exit. */ }}}
function deadline(milliseconds){const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),milliseconds);return {signal:controller.signal,clear:()=>clearTimeout(timer)};}
const finite=v=>typeof v==='number'&&Number.isFinite(v),nullable=v=>v===null||finite(v),boolOrNull=v=>v===null||typeof v==='boolean',str=v=>typeof v==='string'&&v.length<=512;
const array=(v,check)=>Array.isArray(v)&&v.length<=128&&v.every(check);
function validate(s){
  // /api/panel control describes this telemetry provider only. System/runtime
  // execution flags come from their own versioned APIs, never from these flags.
  if(!s||s.panel_api_version!==1||s.source!=='robot_local_panel'||!Number.isSafeInteger(s.sampled_elapsed_ms)||s.sampled_elapsed_ms<0||!finite(s.uptime_s)||s.uptime_s<0)throw Error('接口版本或源时钟无效');
  if(!s.system||!['cpu_percent','memory_used_percent','cpu_temp_c'].every(k=>nullable(s.system[k]))||['cpu_percent','memory_used_percent'].some(k=>s.system[k]!==null&&(s.system[k]<0||s.system[k]>100)))throw Error('系统字段无效');
  if(!s.network||!array(s.network.ipv4,v=>str(v)&&/^\d{1,3}(\.\d{1,3}){3}$/.test(v)&&v.split('.').every(n=>Number(n)<=255)))throw Error('地址字段无效');
  if(!array(s.gamepads,p=>p&&str(p.path)&&str(p.name)&&typeof p.readable==='boolean'&&['usb','bluetooth','unknown'].includes(p.transport)))throw Error('输入设备字段无效');
  if(!s.bluetooth||typeof s.bluetooth.available!=='boolean'||!boolOrNull(s.bluetooth.powered)||!array(s.bluetooth.devices,d=>d&&str(d.name)&&typeof d.connected==='boolean'&&typeof d.paired==='boolean'))throw Error('蓝牙字段无效');
  if(!s.battery||!nullable(s.battery.voltage_v)||!str(s.battery.quality)||!s.ros||!boolOrNull(s.ros.master_available)||!s.control||s.control.motion_enabled!==false||s.control.actuator_attached!==false||!s.capabilities||typeof s.capabilities.battery!=='boolean'||typeof s.capabilities.tasks!=='boolean')throw Error('状态或权限字段无效');
  if(s.power!==undefined&&(!s.power||typeof s.power.can_power_off!=='boolean'||!str(s.power.reason)))throw Error('关机能力字段无效');
  if(s.csrf_token!==undefined&&!str(s.csrf_token))throw Error('关机令牌无效');
  return s;
}
function fresh(){return state&&performance.now()-progressAt<3000;}
function render(){
  const provider=pages().find(p=>p.id===current),s=fresh()?state:null;
  const previousScroll=lastRenderedPage===current?$('cards').scrollTop:0;
  text($('page-title'),provider.title);
  if(provider.independent&&lastRenderedPage===current&&pageCleanup){updatePower();return;}
  cleanupPage();
  $('cards').classList.toggle('custom-page',typeof provider.render==='function');
  $('cards').classList.remove('flow-cards','compact-cards','single-column');
  if(typeof provider.render==='function'){
    $('cards').replaceChildren();
    try{const cleanup=provider.render($('cards'),s);if(typeof cleanup==='function')pageCleanup=cleanup;}
    catch{const message=document.createElement('p');message.textContent='本地页面扩展渲染失败；未显示当前数据';$('cards').replaceChildren(message);}
  }else{
  const cards=provider.cards(s),layout=provider.layout||'default';
  const slots=cards.reduce((sum,card)=>card.span===2?sum+(sum%2)+2:sum+1,0);
  $('cards').classList.toggle('flow-cards',slots>4||layout!=='default');
  $('cards').classList.toggle('compact-cards',layout==='compact');
  $('cards').classList.toggle('single-column',layout==='single-column');
  const fragment=document.createDocumentFragment();
  for(const card of cards){
    const article=document.createElement('article');if(card.span===2)article.classList.add('wide-card');for(const [tag,cls,value]of [['h2','',card.title],['p','value',card.value],['p','detail',card.detail]]){const node=document.createElement(tag);node.className=cls;node.textContent=String(value??'—');node.title=String(value??'—');article.append(node);}fragment.append(article);
  }
  $('cards').replaceChildren(fragment);
  }
  $('cards').scrollTop=previousScroll;lastRenderedPage=current;
  for(const button of $('navigation').children){if(button.dataset.page===current)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');}
  updatePower();
}
function clear(reason){state=null;text($('freshness'),'数据过期 · '+reason);$('freshness').dataset.fresh='false';render();}
function canPower(){return POWER_UI_ENABLED&&!exitBusy&&!exiting&&fresh()&&state.power?.can_power_off===true&&typeof state.csrf_token==='string'&&state.csrf_token.length>0&&!powerBusy&&!powerSent;}
function updatePower(){
  $('power').hidden=!POWER_UI_ENABLED;
  if(!POWER_UI_ENABLED)$('power-dialog').hidden=true;
  $('power').disabled=!canPower();$('confirm-power').disabled=!canPower();
  $('power').title=canPower()?state.power.reason||'请求 Linux 安全关机':(fresh()?state.power?.reason||'后端未允许关机':'当前数据不可用，不能请求关机');
  if(!$('power-dialog').hidden&&!canPower()&&!powerBusy&&!powerSent)text($('power-result'),$('power').title);
}
async function sample(){
  if(busy||exiting)return;busy=true;requestAbort=new AbortController();const timeout=setTimeout(()=>requestAbort.abort(),1500);
  try{
    const response=await fetch('/api/panel',{cache:'no-store',credentials:'same-origin',signal:requestAbort.signal});if(!response.ok)throw Error('HTTP '+response.status);
    const next=validate(await response.json());
    if(exiting)return;
    if(next.csrf_token)exitToken=next.csrf_token;
    if(next.sampled_elapsed_ms<lastElapsed)throw Error('源时钟倒退；需手动刷新');
    if(next.sampled_elapsed_ms>lastElapsed){lastElapsed=next.sampled_elapsed_ms;progressAt=performance.now();}
    if(performance.now()-progressAt>=3000)throw Error('源进度停滞');
    state=next;text($('freshness'),'本机系统采样有效 · 只读采集');$('freshness').dataset.fresh='true';render();
  }catch(error){if(!exiting)clear(error.message);}finally{clearTimeout(timeout);busy=false;}
}
function renderNavigation(){
  const registered=pages(),groups=Math.ceil(registered.length/4),fragment=document.createDocumentFragment();
  navigationGroup=Math.max(0,Math.min(navigationGroup,groups-1));
  function groupButton(delta,label){const button=document.createElement('button');button.className='nav-group';button.textContent=label;button.title='导航第 '+(navigationGroup+1)+' / '+groups+' 组';button.setAttribute('aria-label',label+'；当前第 '+(navigationGroup+1)+' / '+groups+' 组');button.disabled=delta<0?navigationGroup===0:navigationGroup===groups-1;button.addEventListener('click',()=>{navigationGroup+=delta;renderNavigation();});return button;}
  if(groups>1)fragment.append(groupButton(-1,'‹ 前组'));
  for(const provider of registered.slice(navigationGroup*4,navigationGroup*4+4)){
    const button=document.createElement('button');button.dataset.page=provider.id;button.title=provider.title;button.append(icon(provider.icon));const label=document.createElement('span');label.textContent=provider.title;button.append(label);if(provider.id===current)button.setAttribute('aria-current','page');button.addEventListener('click',()=>{current=provider.id;render();});fragment.append(button);
  }
  if(groups>1)fragment.append(groupButton(1,'后组 ›'));
  $('navigation').replaceChildren(fragment);
}
renderNavigation();
$('power-icon').append(icon('power'));
$('power').addEventListener('click',e=>{if(!e.isTrusted||!canPower())return;text($('power-result'),'尚未提交。'+(state.power.reason||'确认后请求 Linux 安全关机。'));$('power-dialog').hidden=false;updatePower();});
$('cancel-power').addEventListener('click',()=>{if(!powerBusy)$('power-dialog').hidden=true;});
$('confirm-power').addEventListener('click',async e=>{
  if(!e.isTrusted||$('power-dialog').hidden||!canPower())return;
  const token=state.csrf_token;powerBusy=true;updatePower();text($('power-result'),'正在提交一次关机请求，请勿重复操作');
  const timeout=deadline(5000);
  try{const response=await fetch('/api/power-off',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Panel-Token':token},body:JSON.stringify({action:'power_off'}),signal:timeout.signal});if(response.status!==202)throw Error('非预期响应 HTTP '+response.status);powerSent=true;text($('power-result'),'请求已提交到系统，请等待身份验证及安全关机；如系统要求，请输入密码。');}
  catch(error){powerSent=true;text($('power-result'),'关机请求未确认：'+error.message+'。不会自动重试；请核实实际状态后手动刷新。');}
  finally{timeout.clear();powerBusy=false;updatePower();}
});
async function sessionToken(){
  const timeout=deadline(2000);
  try{const response=await fetch('/api/session',{cache:'no-store',credentials:'same-origin',signal:timeout.signal});if(!response.ok)throw Error('会话 HTTP '+response.status);const data=await response.json();if(!str(data.csrf_token)||!data.csrf_token.length)throw Error('会话令牌无效');exitToken=data.csrf_token;}
  finally{timeout.clear();}
}
$('exit').addEventListener('click',async e=>{
  if(!e.isTrusted||exitBusy||exiting||powerBusy)return;
  exitBusy=true;$('exit').disabled=true;updatePower();
  const timeout=deadline(5000);
  try{
    if(!exitToken)await sessionToken();
    const response=await fetch('/api/exit',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Panel-Token':exitToken},body:JSON.stringify({action:'exit_panel'}),signal:timeout.signal});
    if(response.status!==202)throw Error('HTTP '+response.status);
    exiting=true;clearInterval(poll);clearInterval(watch);requestAbort?.abort();state=null;render();text($('freshness'),'正在退出面板并清理自有进程');$('freshness').dataset.fresh='false';
  }catch(error){text($('freshness'),'退出未确认：'+error.message+'；可关闭面板窗口退出');exitBusy=false;$('exit').disabled=false;updatePower();}
  finally{timeout.clear();}
});
const poll=setInterval(()=>void sample(),1000),watch=setInterval(()=>{if(state&&!fresh())clear('源进度停滞');},250);
window.addEventListener('pagehide',()=>{clearInterval(poll);clearInterval(watch);requestAbort?.abort();cleanupPage();});
text(document.querySelector('header small'),'机器人 · 项目本机面板');
render();void sessionToken().catch(()=>{});void sample();
