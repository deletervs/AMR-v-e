// Independent GET-only history. Never used as current state or a control receipt gate.
export function mountControlHistory(container){
  const details=document.createElement('details');details.className='control-history';
  const summary=document.createElement('summary');summary.textContent='最近指令结果（只读）';
  const explanation=document.createElement('p');explanation.textContent='入队仅表示进入管道；原生结果是守卫结果，不是电机反馈。未知或接收缺口不能推断缺失结果；守卫状态变化不等于键盘请求确认。';
  const status=document.createElement('p');status.textContent='等待历史数据';
  const list=document.createElement('ol');list.className='control-history-list';
  details.append(summary,explanation,status,list);container.append(details);
  const rows=new Map(),stages={enqueued:'入队',native_result:'原生守卫结果',unconfirmed:'未确认',receipt_gap:'接收缺口',guard_state:'守卫状态变化'};
  const int=v=>Number.isSafeInteger(v)&&v>=0;
  const decimal=v=>typeof v==='string'&&/^(0|[1-9][0-9]{0,19})$/.test(v)&&BigInt(v)<=18446744073709551615n;
  const string=v=>typeof v==='string'&&v.length<=1024;
  const set=(node,value)=>{if(node.textContent!==value)node.textContent=value;};
  let disposed=false,busy=false,generation=0,controller=null,timer=null,instance=null;
  function empty(){rows.clear();list.replaceChildren();}
  function validate(data){
    if(!data||data.history_api_version!==1||data.source!=='robot_control_history'||!string(data.instance_id)||!data.instance_id||data.capacity!==200||!int(data.total_retained)||data.total_retained>200||!int(data.dropped)||data.returned_limit!==20||data.command_usable!==false||!Array.isArray(data.events)||data.events.length>20||data.events.length>data.total_retained)throw Error('历史协议无效');
    let previous=-1,elapsed=-1;
    for(const event of data.events){
      if(!event||!int(event.id)||event.id<=previous||!int(event.elapsed_ms)||event.elapsed_ms<elapsed||!Object.prototype.hasOwnProperty.call(stages,event.stage)||!int(event.generation)||!decimal(event.boot_id)||!(event.sequence===null||decimal(event.sequence))||!string(event.action)||!(event.result===null||string(event.result))||!(event.native_elapsed_ms===null||int(event.native_elapsed_ms)))throw Error('历史事件字段或顺序无效');
      previous=event.id;elapsed=event.elapsed_ms;
    }
    return data;
  }
  function render(data){
    if(instance!==data.instance_id){empty();instance=data.instance_id;}
    const ids=new Set(data.events.map(event=>event.id));
    for(const [id,node]of rows)if(!ids.has(id)){node.remove();rows.delete(id);}
    for(const event of data.events){
      let node=rows.get(event.id);if(!node){node=document.createElement('li');rows.set(event.id,node);list.append(node);}
      set(node,`#${event.id} · 本次历史 ${event.elapsed_ms} ms · ${stages[event.stage]}\n动作 ${event.action} · 结果 ${event.result??'—'} · 序号 ${event.sequence??'—'}\ngeneration ${event.generation} · boot ${event.boot_id} · 原生时间 ${event.native_elapsed_ms===null?'—':event.native_elapsed_ms+' ms'}`);
    }
    set(status,`仅历史：显示最近 ${data.events.length} 条（最旧→最新）；保留 ${data.total_retained}/200 条，已丢弃 ${data.dropped} 条。${data.events.length?'':' 暂无历史事件。'}`);
  }
  async function poll(){
    if(disposed||document.hidden||busy)return;busy=true;const epoch=generation,abort=new AbortController();controller=abort;
    const timeout=setTimeout(()=>abort.abort(),1200);
    try{
      const response=await fetch('/api/control-events',{method:'GET',cache:'no-store',credentials:'same-origin',signal:abort.signal});
      if(!response.ok)throw Error('HTTP '+response.status);
      const data=validate(await response.json());if(disposed||document.hidden||epoch!==generation)return;render(data);
    }catch(error){if(!disposed&&epoch===generation){empty();set(status,'历史暂不可读：'+error.message+'。不影响当前控制状态的独立判断。');}}
    finally{clearTimeout(timeout);if(controller===abort)controller=null;busy=false;}
  }
  function pause(){generation++;controller?.abort();clearInterval(timer);timer=null;empty();set(status,'历史读取已暂停');}
  function resume(){if(disposed||document.hidden)return;timer=setInterval(()=>void poll(),500);void poll();}
  function visibility(){pause();if(!document.hidden)resume();}
  document.addEventListener('visibilitychange',visibility);resume();
  return ()=>{disposed=true;pause();document.removeEventListener('visibilitychange',visibility);details.remove();};
}
