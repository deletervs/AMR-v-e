// Trusted local providers only. Adding a page never imports a URL or evaluates code.
import {renderUsbObserver} from './usb-observer.mjs';
import {renderKeyboardControl} from './keyboard-control.mjs';
import {renderProjectServices} from './project-services.mjs';
const registry=new Map();
export function registerPage(provider){
  if(!provider||typeof provider.id!=='string'||!/^([a-z][a-z0-9-]{0,31})$/.test(provider.id)||registry.has(provider.id)||typeof provider.title!=='string'||!(typeof provider.cards==='function'||typeof provider.render==='function')||!Object.prototype.hasOwnProperty.call(ICONS,provider.icon))throw new Error('Invalid or duplicate panel page');
  if(provider.layout!==undefined&&!['default','compact','single-column'].includes(provider.layout))throw new Error('Invalid page layout');
  if(provider.independent!==undefined&&(provider.independent!==true||typeof provider.render!=='function'))throw new Error('Independent page requires trusted render');
  registry.set(provider.id,Object.freeze({...provider}));
}
export const pages=()=>Array.from(registry.values());
const ICONS={overview:'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',devices:'M6 7h12l3 10-3 2-4-4h-4l-4 4-3-2z M6 11h5 M8.5 8.5v5 M16 10h.1 M18 13h.1',system:'M6 6h12v12H6z M9 1v5 M15 1v5 M9 18v5 M15 18v5 M1 9h5 M1 15h5 M18 9h5 M18 15h5',tasks:'M5 4h14v17H5z M9 4V2h6v2 M8 10h8 M8 15h8',power:'M12 2v10 M6 5a9 9 0 1 0 12 0'};
export function icon(name){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',ICONS[name]||ICONS.overview);svg.append(path);return svg;}
const value=(v,suffix='')=>v===null||v===undefined?'—':v.toFixed(1)+suffix;
const card=(title,value,detail)=>({title,value,detail});
registerPage({id:'overview',title:'概览',icon:'overview',cards:s=>{
  const usb=s?s.gamepads.filter(p=>p.transport==='usb'):[];
  return [
    card('电池电压',s?s.capabilities.battery&&s.battery.quality!=='Unknown'?value(s.battery.voltage_v,' V'):s.capabilities.battery?'—':'待接入电压数据':'—',s?s.capabilities.battery?'电压来源质量：'+s.battery.quality:'尚无电池电压来源':'没有当前数据'),
    card('CPU / 内存',value(s?.system.cpu_percent,' %')+' / '+value(s?.system.memory_used_percent,' %'),'CPU / 内存使用率 · 本机采样'),
    card('USB 手柄',s?usb.length+' 个已识别节点':'—',s?usb.slice(0,2).map(p=>`${p.name} · ${p.readable?'可读':'不可读'} · ${p.path}`).join('\n')||'未报告 USB 节点；不代表已完成物理排查':'没有当前数据'),
    card('本机网络',s?.network.ipv4[0]||'—',s?(s.network.ipv4.slice(1).join(' · ')||'本机 IPv4')+'；不代表远程链路通过':'没有当前数据')
  ];
}});
registerPage({id:'devices',title:'设备',icon:'devices',cards:s=>[
  card('输入节点',s?String(s.gamepads.length):'—',s?s.gamepads.slice(0,2).map(p=>`${p.name} · ${p.transport} · ${p.readable?'可读':'不可读'} · ${p.path}`).join('\n')||'未发现输入节点；不代表已完成物理排查':'没有当前数据'),
  card('蓝牙',s?s.bluetooth.available?(s.bluetooth.powered===null?'电源未知':s.bluetooth.powered?'已开启':'已关闭'):'不可用':'—',s?s.bluetooth.devices.slice(0,2).map(d=>`${d.name} · ${d.connected?'已连接':'未连接'} · ${d.paired?'已配对':'未配对'}`).join('\n')||'没有报告设备；此页不能配对':'没有当前数据'),
  card('ROS master',s?s.ros.master_available===null?'—':s.ros.master_available?'可访问':'不可访问':'—','仅可达性，不代表驱动或运动已验证'),
  card('本页元数据权限',s?'只读采集':'—','仅指本页元数据；执行模式见手柄/键盘页，不由采集器false推断')
]});
registerPage({id:'usb-observer',title:'手柄与安全层',icon:'devices',render:renderUsbObserver,independent:true});
registerPage({id:'system',title:'系统',icon:'system',cards:s=>[
  card('CPU 使用率',value(s?.system.cpu_percent,' %'),'本机采样'),card('内存使用率',value(s?.system.memory_used_percent,' %'),'本机采样'),
  card('CPU 温度',value(s?.system.cpu_temp_c,' °C'),'不可读时显示 —'),card('运行时间',s?Math.floor(s.uptime_s/60)+' 分钟':'—','工控机系统运行时间')
]});
registerPage({id:'tasks',title:'任务',icon:'tasks',cards:()=>[
  card('任务数据','尚未接入','没有当前任务、进度或完成结果'),card('具身智能','扩展预留','后续通过受信任本地页面 provider 接入；当前未实现'),
  card('权限边界','只读状态','新增页面不获得执行器或系统操作权限'),card('控制状态','—','此页不推断任务运行或控制链验收通过')
]});
registerPage({id:'keyboard-control',title:'键盘控制',icon:'devices',render:renderKeyboardControl,independent:true});
registerPage({id:'project-services',title:'项目服务',icon:'system',render:renderProjectServices,independent:true});
