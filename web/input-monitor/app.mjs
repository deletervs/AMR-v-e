import {readPad, scaledAxis} from '/core.mjs';
const $ = id => document.getElementById(id);
const controls = new Set(['KeyW','KeyA','KeyS','KeyD','ArrowUp','ArrowDown','ArrowLeft','ArrowRight','ShiftLeft','ShiftRight']);
const held = new Set(), virtual = new Set(), samples = [];
let active = false, centered = false, chosen = null, last = 0, frame = 0;
let devices = new Map(), identities = new Map(), serial = 0;
let generation=0, sampleSequence=0, cppToken=null, cppBusy=false, cppAbort=null, cppShownAt=0;
function clearCpp(message='C++ 结果已撤销') {
  generation++; cppAbort?.abort(); cppShownAt=0; $('cpp').textContent=message;
}
async function capabilities() {
  try {
    const response=await fetch('/api/capabilities',{cache:'no-store',signal:AbortSignal.timeout(1000)});
    if(!response.ok) throw new Error('HTTP');
    const capability=await response.json();
    cppToken=capability.available && typeof capability.token==='string' ? capability.token : null;
    $('cpp').textContent=cppToken?'C++ 已就绪；等待真实采集并回中':'C++ 未启用；保留浏览器预览';
  } catch {cppToken=null;clearCpp('C++ 接口不可用');}
}
async function cppPreview(data) {
  const sequence=++sampleSequence;
  if(!active || !centered) {clearCpp('C++ 等待开始并回中');return;}
  if(!cppToken || cppBusy) return;
  const epoch=generation, sentAt=performance.now();
  const abort=new AbortController();cppAbort=abort;cppBusy=true;
  const timer=setTimeout(()=>abort.abort(),450);
  let payload;
  if($('source').value==='gamepad') {
    payload={source:'pad',deadman:data.buttons[5].pressed,axis_x:data.axes[0],axis_y:data.axes[1]};
  } else {
    const keys=new Set([...data.keys,...data.virtual_keys]);
    const down=(...codes)=>codes.some(c=>keys.has(c));
    payload={source:'keys',deadman:down('ShiftLeft','ShiftRight'),forward:down('KeyW','ArrowUp'),backward:down('KeyS','ArrowDown'),left:down('KeyA','ArrowLeft'),right:down('KeyD','ArrowRight')};
  }
  try {
    const response=await fetch('/api/preview',{method:'POST',headers:{'Content-Type':'application/json','X-Preview-Token':cppToken},body:JSON.stringify(payload),signal:abort.signal,cache:'no-store'});
    if(!response.ok) throw new Error('HTTP');
    const result=await response.json();
    if(epoch!==generation || sequence!==sampleSequence || !active || !centered || document.hidden || !document.hasFocus() || performance.now()-sentAt>250) return;
    if(payload.source==='pad') {
      const current=pads().find(p=>p.index===data.index && p.id===data.id);
      const input=readPad(current);
      if(!input || input.x!==payload.axis_x || input.y!==payload.axis_y || input.deadman!==payload.deadman) {clearCpp('C++ 输入已变化；等待新采样');return;}
    }
    if(result.source!=='cpp_input_preview' || result.result!=='Mapped' || result.robot_connected!==false || result.motion_enabled!==false || ![result.linear_mps,result.angular_radps].every(Number.isFinite)) throw new Error('invalid result');
    cppShownAt=performance.now();
    $('cpp').textContent=`cpp_input_preview #${result.request_id} · 线速度 ${result.linear_mps.toFixed(3)} m/s · 角速度 ${result.angular_radps.toFixed(3)} rad/s`;
  } catch {
    if(epoch===generation) clearCpp('C++ 本次计算不可用；无有效结果');
  } finally {clearTimeout(timer);if(cppAbort===abort)cppAbort=null;cppBusy=false;}
}
const zero = () => { $('preview').textContent = '线速度 0.000 m/s · 角速度 0.000 rad/s'; };
function stop(reason) {
  clearCpp();
  active = false; centered = false; held.clear(); virtual.clear(); zero();
  $('raw').textContent = '未采集'; $('status').textContent = reason + '；需点击开始并回中';
}
function pads() { return navigator.getGamepads ? Array.from(navigator.getGamepads()).filter(p => p?.connected) : []; }
function refresh() {
  const list = pads(), next = new Map();
  for (const p of list) {
    const signature = JSON.stringify([p.id,p.mapping,p.axes.length,p.buttons.length]);
    let identity = identities.get(p.index);
    if (!identity || identity.signature !== signature) identity = {signature, token: String(++serial)};
    identities.set(p.index, identity); next.set(identity.token, p);
  }
  for (const index of identities.keys()) if (!list.some(p => p.index === index)) identities.delete(index);
  if (chosen && !next.has(chosen)) { stop('所选设备断开或身份变化'); chosen = null; }
  devices = next;
  const old = $('device').value;
  if (Array.from($('device').options).slice(1).map(o=>o.value).join(',') !== [...next.keys()].join(',')) {
    $('device').replaceChildren(new Option('请选择设备',''));
    for (const [token,p] of next) $('device').add(new Option(`[${p.index}] ${p.id} · ${p.mapping || '无映射'}`,token));
    $('device').value = next.has(old) ? old : '';
  }
}
function record(data) {
  samples.push({wall_time:new Date().toISOString(),performance_ms:performance.now(),...data});
  if(samples.length>1200) samples.shift();
  $('count').textContent = `${samples.length} 条（最近窗口）`;
}
function tick(now) {
  if(cppShownAt && performance.now()-cppShownAt>250) clearCpp('C++ 结果已过期');
  refresh();
  if(active && (!document.hasFocus() || document.hidden)) stop('页面未聚焦或不可见');
  if(active && now-last>=100) {
    last=now;
    let x=0,y=0,enabled=false,data;
    if($('source').value==='gamepad') {
      const p=devices.get(chosen);
      if(!p) stop('无所选设备');
      else {
        data={id:p.id,index:p.index,mapping:p.mapping,timestamp:p.timestamp,axes:Array.from(p.axes),buttons:p.buttons.map(b=>({value:b.value,pressed:b.pressed,touched:b.touched})),RB:p.buttons[5]?.value ?? null};
        const input=readPad(p);
        if(!input) { centered=false; $('status').textContent='原始采集中；映射不兼容，速度预览为零'; }
        else {
          if(!centered && !input.deadman && Math.abs(input.x)<=0.15 && Math.abs(input.y)<=0.15) centered=true;
          if(centered) {x=scaledAxis(input.x);y=scaledAxis(input.y);enabled=input.deadman;}
          $('status').textContent=centered?'采集中（仅浏览器）':'等待摇杆回中并松开 RB';
        }
      }
    } else {
      const down=(...keys)=>keys.some(k=>held.has(k)||virtual.has(k));
      if(!centered && held.size===0 && virtual.size===0) centered=true;
      x=Number(down('KeyD','ArrowRight'))-Number(down('KeyA','ArrowLeft'));
      y=Number(down('KeyS','ArrowDown'))-Number(down('KeyW','ArrowUp'));
      enabled=centered && down('ShiftLeft','ShiftRight');
      data={keys:[...held],virtual_keys:[...virtual]};
      $('status').textContent=centered?'键盘采集中（仅浏览器）':'等待松开所有控制键';
    }
    if(active && data) {
      const preview={linear_mps:enabled?-y*0.25:0,angular_radps:enabled?-x*0.6:0};
      $('raw').textContent=JSON.stringify(data,null,2);
      $('preview').textContent=`线速度 ${preview.linear_mps.toFixed(3)} m/s · 角速度 ${preview.angular_radps.toFixed(3)} rad/s`;
      record({source:$('source').value,centered,data,preview});
      void cppPreview(data);
    }
  }
  frame=requestAnimationFrame(tick);
}
$('device').addEventListener('change',()=>{stop('设备选择改变');chosen=$('device').value||null;});
$('source').addEventListener('change',()=>stop('来源改变'));
$('start').addEventListener('click',()=>{
  stop('准备开始');refresh();
  if(document.hidden || !document.hasFocus()) return;
  if($('source').value==='gamepad' && !devices.has(chosen)) { $('status').textContent='请先选择浏览器实际设备；必要时实际按一下手柄使浏览器识别';return; }
  active=true; $('status').textContent='等待回中 / 松键';
});
$('pause').addEventListener('click',()=>stop('已暂停'));
for(const type of ['keydown','keyup']) window.addEventListener(type,e=>{
  if(e.isTrusted && type==='keyup' && controls.has(e.code) && held.delete(e.code)) clearCpp('C++ 键位已变化；等待新采样');
  if(!e.isTrusted || !active || document.hidden || !document.hasFocus() || $('source').value!=='keyboard' || /^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)) return;
  if(e.code==='Space') {e.preventDefault();stop('Space 清零');return;}
  if(controls.has(e.code)) {e.preventDefault();if(type==='keydown' && !held.has(e.code)) {held.add(e.code);clearCpp('C++ 键位已变化；等待新采样');}}
});
for(const b of $('virtual').querySelectorAll('button')) {
  b.addEventListener('pointerdown',e=>{if(e.isTrusted && active && $('source').value==='keyboard'){e.preventDefault();b.setPointerCapture(e.pointerId);virtual.add(b.dataset.key);clearCpp('C++ 虚拟键位已变化；等待新采样');}});
  for(const event of ['pointerup','pointercancel','lostpointercapture']) b.addEventListener(event,()=>{if(virtual.delete(b.dataset.key))clearCpp('C++ 虚拟键位已变化；等待新采样');});
}
window.addEventListener('blur',()=>stop('失焦'));
document.addEventListener('visibilitychange',()=>{if(document.hidden)stop('页面隐藏');});
window.addEventListener('gamepaddisconnected',e=>{
  identities.delete(e.gamepad.index);
  if(devices.get(chosen)?.index===e.gamepad.index){stop('设备断开');chosen=null;}
});
window.addEventListener('pagehide',()=>{stop('页面退出');cancelAnimationFrame(frame);});
window.addEventListener('pageshow',()=>{cancelAnimationFrame(frame);frame=requestAnimationFrame(tick);});
$('export').addEventListener('click',()=>{
  const report={schema:1,exported_at:new Date().toISOString(),scope:'browser-only; no robot; no physical acceptance',user_self_report:{physical_device:$('physical').checked,connection:$('connection').value,operation:$('mark').value},retention:{max_samples:1200,max_hz:10},interval:{first:samples[0]?.wall_time??null,last:samples.at(-1)?.wall_time??null},samples};
  const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'}));
  const a=document.createElement('a');a.href=url;a.download='input-observation.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
$('status').textContent='初始化完成；已暂停，选择来源与设备后开始';
void capabilities();
frame=requestAnimationFrame(tick);
