// A read-only collector's motion_enabled=false is not the system output mode.
(function(){
    const node=document.createElement('p');node.className='execution-mode-notice';node.setAttribute('role','status');
    node.textContent='本页仅监控；采集器自身不控制运动，不能据此确认整机输出模式。';
    (document.querySelector('main')||document.body).prepend(node);
    let activeSeen=false,busy=false;
    async function query(){
        if(busy||document.hidden)return;busy=true;
        const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),1200);
        try {
            const response=await fetch('/api/system',{cache:'no-store',signal:abort.signal});
            if(!response.ok)throw Error('unavailable');
            const state=await response.json();
            if(state.source!=='robot_local_system'||typeof state.motion_enabled!=='boolean'||typeof state.actuator_attached!=='boolean')throw Error('protocol');
            if(state.motion_enabled||state.actuator_attached)activeSeen=true;
            else if(state.execution_state_current===true)activeSeen=false;
            node.classList.toggle('execution-mode-active',activeSeen);
            node.textContent=activeSeen?
                (state.execution_state_current===true?'真实输出模式已由本机授权配置启用；需现场看护。本页只读，写出成功不等于物理运动或停车。':'输出状态暂不可确认；此前存在真实输出模式，物理停止未确认。'):
                (state.execution_state_current===true?'当前采样显示项目真实输出未启用；本页只读，不代表其它程序不能控制底盘。':'项目输出状态未确认；本页仅监控，不判断物理停车。');
        }catch(error){
            node.textContent=activeSeen?'输出状态暂不可确认；物理停止未确认。':'本页仅监控；整机输出模式未接入或暂不可读。';
        }finally{clearTimeout(timer);busy=false;}
    }
    setInterval(query,1000);query();
}());
