import { InputSession, KEY_CODES, randomController, readPad, ResponseOrder } from './core.mjs';
const $ = id => document.getElementById(id);
const input = new InputSession(randomController(crypto));
const responses = new ResponseOrder();
let csrf = '', online = false, inflight = false, actionBusy = false, suspended = false;
let nextConnectAt = 0;
let padIndex = null, animationTime = 0, heading = 0, offset = 0;
const pointers = new Map();
const reasons = { none: '无', startup: '初始零速', released: '已释放', release: '已释放', software_stop: '软件停止', stopped: '软件停止', stop_latched: '软件停止锁存', command_expired: '命令已过期', lease_expired: '租约已到期', deadman_released: '使能已松开', waiting_neutral: '等待回中', neutral_required: '等待回中', agent_unavailable: '假控制器不可用', link_unavailable: '链路不可用', link_lost: '链路断开', not_armed: '未取得控制权' };
Object.assign(reasons, { Disarmed: '未取得控制权', WaitingForNeutral: '等待回中', DeadmanReleased: '使能已松开', None: '无', CommandExpired: '命令已过期', LeaseExpired: '租约已到期', SoftwareStop: '软件停止', LinkUnavailable: '链路不可用', InvalidCommand: '非法命令', ClockRegression: '时钟回退', InvalidConfig: '配置无效' });
const current = () => !document.hidden && document.hasFocus() && !suspended;
async function request(body = null, keepalive = false) {
  const ticket = responses.begin();
  try {
    const response = await fetch(body ? '/api/command' : '/api/state', { method: body ? 'POST' : 'GET', cache: 'no-store',
      headers: body ? { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf } : {},
      body: body ? JSON.stringify(body) : undefined, keepalive, signal: keepalive ? undefined : AbortSignal.timeout(900) });
    if (!responses.current(ticket)) return null;
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const result = await response.json();
    if (!responses.current(ticket)) return null;
    if (result.offline !== true || !result.transport_available) throw new Error('假控制器不可用');
    return responses.accept(ticket) ? result.state : null;
  } catch (error) {
    if (!responses.current(ticket)) return null;
    throw error;
  }
}
function notice(text) { $('notice').textContent = text; }
function speed(id, value, unit) {
  const label = document.createElement('small'); label.textContent = unit;
  $(id).replaceChildren(document.createTextNode(`${value} `), label);
}
function clearLocal() {
  pointers.clear();
  document.querySelectorAll('[data-input]').forEach(el => el.classList.remove('pressed'));
}
function releaseLocal(message, keepalive = false) {
  const release = input.invalidate();
  clearLocal();
  notice(message);
  render();
  if (release && csrf) request(release, keepalive).then(state => { if (state) { input.observe(state); render(); } }).catch(() => {});
}
function fail(error) {
  online = false;
  responses.reset();
  releaseLocal('请求失败，已清空输入。恢复连接后请重新取得控制权。');
  // A restarted service has a new CSRF token. Reconnect only to observe it;
  // acquiring control remains an explicit user action after every failure.
  csrf = '';
  nextConnectAt = performance.now() + 500;
}
async function connect() {
  const ticket = responses.begin();
  try {
    const response = await fetch('/api/session', { cache: 'no-store', signal: AbortSignal.timeout(900) });
    if (!responses.current(ticket)) return;
    if (!response.ok) throw new Error('连接失败');
    const session = await response.json();
    if (!responses.current(ticket)) return;
    if (typeof session.csrf !== 'string' || session.csrf.length < 32 || session.offline !== true || !session.transport_available) throw new Error('假控制器不可用');
    input.observe(session.state);
    responses.accept(ticket);
    csrf = session.csrf;
    online = true;
  } catch (error) {
    if (responses.current(ticket)) throw error;
  }
}
function updatePad() {
  const pads = navigator.getGamepads?.() ?? [];
  const candidates = Array.from(pads).filter(Boolean);
  let pad = candidates.find(p => p.index === padIndex);
  if (!pad && padIndex === null) pad = candidates.find(p => readPad(p));
  const value = readPad(pad);
  if (input.source === 'gamepad' && input.active && (!value || value.index !== padIndex)) {
    releaseLocal('手柄断开或映射无效，已释放。回中后重新取得控制权。');
  }
  padIndex = value?.index ?? null;
  input.pad = value;
  $('pad-status').textContent = value ? `已识别标准手柄 · 左摇杆 ${value.x.toFixed(2)}, ${value.y.toFixed(2)} · RB ${value.deadman ? '按下' : '松开'}` : candidates.length ? '手柄映射不受支持，不能使能。需要 standard 映射。' : '连接标准手柄，并按任意键让浏览器识别。';
}
function render() {
  const state = input.state;
  const stale = state && performance.now() - input.seenAt > 150;
  if (!online || !state || stale) {
    for (const id of ['arm', 'release', 'clear']) $(id).disabled = true;
    $('arm').textContent = '取得控制权';
    speed('linear', '0.00', 'm/s'); speed('angular', '0.00', 'rad/s');
    $('owner').textContent = '无有效控制权';
    $('reason').textContent = online && stale ? '本机状态已过期' : '本机连接不可用';
    $('lease').textContent = '—'; $('ttl').textContent = '—';
    $('status').textContent = '状态不可用 · 本地显示零速';
    $('transport').textContent = online && stale ? '· 等待本机新状态' : '· 正在重新连接本机服务';
    $('status-dot').className = 'status-dot stopped';
    return;
  }
  const owned = input.owns();
  const canArm = online && current() && !state.armed && !state.stop_latched && input.neutral();
  $('arm').disabled = !canArm || actionBusy;
  $('release').disabled = !owned;
  $('clear').disabled = !online || !state.stop_latched;
  $('arm').textContent = owned && input.active ? '本窗口持有控制权' : state.armed ? '另一窗口持有控制权' : '取得控制权';
  speed('linear', online ? state.linear_mps.toFixed(2) : '0.00', 'm/s');
  speed('angular', online ? state.angular_radps.toFixed(2) : '0.00', 'rad/s');
  $('owner').textContent = state.owner === 'none' ? '无' : `${state.owner === 'keyboard' ? '键盘 / 虚拟按键' : '标准手柄'} · ${owned ? '本窗口' : '其他窗口'}`;
  $('reason').textContent = reasons[state.stop_reason] ?? state.stop_reason;
  $('lease').textContent = `${(state.lease_remaining_ms / 1000).toFixed(1)} s`;
  $('ttl').textContent = `${state.command_remaining_ms} ms`;
  $('elapsed').textContent = (state.elapsed_ms / 1000).toFixed(1);
  $('transport').textContent = online ? '· C++ 假控制器在线' : '· 本机连接中断';
  $('status').textContent = !online ? '连接中断 · 本地显示零速' : state.stop_latched ? '软件停止已锁存' : owned && input.active ? state.neutral_confirmed ? '已就绪 · 按住使能与方向' : '等待中立输入确认' : state.armed ? '其他窗口正在控制' : '零速待命 · 需手动取得控制权';
  $('status-dot').className = `status-dot ${state.stop_latched || !online ? 'stopped' : owned && input.active ? 'active' : ''}`;
  document.querySelectorAll('[data-input]').forEach(el => el.classList.toggle('pressed', input.virtual.has(el.dataset.input)));
}
async function pump() {
  // These checks must run even while a fetch is outstanding. The C++ command
  // deadline remains authoritative; the page must not animate old motion.
  if (input.active && !current()) releaseLocal('页面已离开，输入已清空。返回后请重新取得控制权。');
  if (input.active && performance.now() - input.seenAt > 150) releaseLocal('状态已过时，已释放控制权。请重新取得。');
  if (inflight || actionBusy) return;
  if (!csrf) {
    if (performance.now() < nextConnectAt) return;
    inflight = true;
    try { await connect(); }
    catch (error) { fail(error); }
    finally { inflight = false; render(); }
    return;
  }
  updatePad();
  inflight = true;
  try {
    const packet = current() ? input.packet() : null;
    const wasActive = input.active;
    const state = await request(packet);
    if (!state) return;
    input.observe(state);
    online = true;
    if (wasActive && !input.active) { clearLocal(); notice('控制权已结束。松开所有输入后重新取得控制权。'); }
  } catch (error) { fail(error); }
  finally { inflight = false; render(); }
}
$('arm').addEventListener('click', async () => {
  if (!online || !current() || !input.neutral() || input.state?.armed || actionBusy) return;
  const generation = input.generation;
  // Reserve the next transaction immediately; a polling request must never
  // make an otherwise enabled arm button silently drop a user's click.
  actionBusy = true;
  render();
  try {
    while (inflight) await new Promise(resolve => setTimeout(resolve, 5));
    if (generation !== input.generation || !current() || !input.neutral() || input.state?.armed) return;
    const state = await request({ action: 'arm', controller_id: input.id, source: input.source });
    if (!state) return;
    if (!current() || !input.activate(state, generation)) {
      const release = input.release(state);
      if (release) { const released = await request(release); if (released) input.observe(released); }
      notice('控制权未取得。先回中，再手动重试。');
    } else notice('请先保持回中。确认后按住使能与方向；松开使能即归零。');
  } catch (error) { fail(error); }
  finally { actionBusy = false; render(); }
});
$('release').addEventListener('click', () => releaseLocal('已主动释放。松开所有输入后可再次取得控制权。'));
async function softwareStop() {
  releaseLocal('软件停止已请求。解除后仍需重新取得控制权。');
  try { const state = await request({ action: 'stop' }); if (state) { input.observe(state); online = true; render(); } }
  catch (error) { fail(error); }
}
$('stop').addEventListener('click', softwareStop);
$('clear').addEventListener('click', async () => {
  releaseLocal('正在解除停止；不会自动取得控制权。');
  try { const state = await request({ action: 'clear' }); if (state) { input.observe(state); notice('停止已解除。松开所有输入，手动取得控制权。'); render(); } }
  catch (error) { fail(error); }
});
function switchSource(source) {
  if (input.source === source) return;
  releaseLocal('输入来源已切换。回中后手动取得控制权。');
  input.source = source;
  padIndex = null;
  for (const name of ['keyboard', 'gamepad']) {
    $(`${name}-panel`).hidden = name !== source;
    $(`${name}-tab`).classList.toggle('selected', name === source);
    $(`${name}-tab`).setAttribute('aria-pressed', String(name === source));
  }
  updatePad(); render();
}
$('keyboard-tab').addEventListener('click', () => switchSource('keyboard'));
$('gamepad-tab').addEventListener('click', () => switchSource('gamepad'));
document.addEventListener('keydown', event => {
  if (event.code === 'Space') { event.preventDefault(); if (!event.repeat) softwareStop(); return; }
  if (KEY_CODES.has(event.code)) {
    event.preventDefault();
    if (current() && input.source === 'keyboard' && !event.repeat) input.held.add(event.code);
    render();
  }
});
document.addEventListener('keyup', event => {
  if (KEY_CODES.has(event.code)) { event.preventDefault(); input.held.delete(event.code); render(); }
});
for (const el of document.querySelectorAll('[data-input]')) {
  el.addEventListener('pointerdown', event => {
    event.preventDefault();
    if (!current() || input.source !== 'keyboard' || event.button !== 0) return;
    el.setPointerCapture(event.pointerId);
    pointers.set(event.pointerId, el.dataset.input);
    input.virtual.add(el.dataset.input); render();
  });
  el.addEventListener('pointerup', event => {
    pointers.delete(event.pointerId);
    if (![...pointers.values()].includes(el.dataset.input)) input.virtual.delete(el.dataset.input);
    render();
  });
  el.addEventListener('pointercancel', () => releaseLocal('触控已取消，已释放。请回中后重新取得控制权。'));
  el.addEventListener('lostpointercapture', event => {
    if (pointers.has(event.pointerId)) releaseLocal('指针捕获已丢失，已释放控制权。');
  });
}
window.addEventListener('blur', () => { suspended = true; releaseLocal('窗口已失焦，已释放。返回后需手动取得控制权。', true); });
window.addEventListener('focus', () => { suspended = false; render(); });
document.addEventListener('visibilitychange', () => {
  if (document.hidden) { suspended = true; releaseLocal('页面进入后台，已释放。返回后需手动取得控制权。', true); }
  else { suspended = false; render(); }
});
window.addEventListener('pagehide', () => { suspended = true; releaseLocal('页面已离开。', true); });
window.addEventListener('gamepaddisconnected', event => {
  if (input.source === 'gamepad' && (!event.gamepad || event.gamepad.index === padIndex)) releaseLocal('手柄已断开。回中后重新取得控制权。');
  padIndex = null;
});
function animate(time) {
  const dt = Math.min((time - animationTime) / 1000, 0.05); animationTime = time;
  if (online && input.state && performance.now() - input.seenAt <= 150) { heading -= input.state.angular_radps * dt * 80; offset += input.state.linear_mps * dt * 45; }
  offset = Math.max(-45, Math.min(45, offset));
  $('robot').style.transform = `translateY(${-offset}px) rotate(${heading}deg)`;
  requestAnimationFrame(animate);
}
async function start() {
  await pump();
  setInterval(pump, 50);
  requestAnimationFrame(animate);
}
start();
