// Actual headless Edge + actual C++ child. Injected Gamepad API is synthetic.
import { createRequire } from 'node:module';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdir, writeFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
import path from 'node:path';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(import.meta.dirname, '../../..');
const output = path.join(root, '.local/learning-checks/M3-001');
await mkdir(output, { recursive: true });
const wrapper = `
import importlib.util,sys,threading,json
spec=importlib.util.spec_from_file_location('demo','tools/serve_mock_teleop.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def open_demo(port=0):
 b=m.AgentBridge([sys.argv[1]])
 s=m.TeleopServer(port,b)
 t=threading.Thread(target=s.serve_forever,daemon=True);t.start()
 return b,s,t
def close_demo(b,s,t):
 s.shutdown();s.server_close();t.join();b.close()
b,s,t=open_demo()
print(json.dumps({'url':s.origin,'agent_pid':b.process.pid}),flush=True)
try:
 for command in sys.stdin:
  if command.strip()=='rotate-csrf': s.csrf=m.secrets.token_urlsafe(32)
  elif command.strip()=='restart-service':
   port=s.server_port;close_demo(b,s,t);b,s,t=open_demo(port)
   print(json.dumps({'event':'restarted','agent_pid':b.process.pid}),flush=True)
  else: break
finally: close_demo(b,s,t)
`;
const python = process.env.PYTHON_EXECUTABLE || (process.platform === 'win32' ? 'python' : 'python3');
const agentName = process.platform === 'win32' ? 'mock_teleop.exe' : 'mock_teleop';
const child = spawn(python, ['-u', '-c', wrapper, process.env.MOCK_TELEOP_AGENT || path.join(root, '.local/build/cmake-debug', agentName)], { cwd: root, stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
let stderr = ''; child.stderr.on('data', data => stderr += data);
const agentPids = []; let restartInfo = null;
const startup = await new Promise((resolve, reject) => {
  let buffer = ''; const timer = setTimeout(() => reject(new Error(`service startup timeout: ${stderr}`)), 5000);
  child.stdout.on('data', chunk => {
    buffer += chunk;
    while (buffer.includes('\n')) {
      const line = buffer.slice(0, buffer.indexOf('\n')); buffer = buffer.slice(buffer.indexOf('\n') + 1);
      const info = JSON.parse(line); agentPids.push(info.agent_pid);
      if (info.event === 'restarted') restartInfo = info;
      else { clearTimeout(timer); resolve(info); }
    }
  });
  child.once('exit', code => { clearTimeout(timer); reject(new Error(`service exit ${code}: ${stderr}`)); });
});
const evidence = { browser: 'headless Microsoft Edge', actual_cpp_agent: true, synthetic_gamepad: true, startup, owned_agent_pids: agentPids, checks: [], screenshots: [], console_errors: [] };
let browser, expectNetworkErrors = false;
async function check(name, fn) { await fn(); evidence.checks.push({ name, passed: true }); }
async function state() { const response = await fetch(startup.url + '/api/state'); return (await response.json()).state; }
async function until(predicate, description, timeout = 2500) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) { const current = await state(); if (predicate(current)) return current; await new Promise(r => setTimeout(r, 20)); }
  throw new Error(`${description}: ${JSON.stringify(await state())}`);
}
async function arm(page) {
  await page.bringToFront(); await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await page.locator('#arm').click(); await until(s => s.armed && s.neutral_confirmed, 'arm and neutral');
}
async function unarm(page) { await page.locator('#release').click(); await until(s => !s.armed, 'release'); }
try {
  browser = await chromium.launch(process.env.BROWSER_EXECUTABLE
    ? { executablePath: process.env.BROWSER_EXECUTABLE, headless: true }
    : { channel: 'msedge', headless: true });
  evidence.browser_version = browser.version();
  const context = await browser.newContext({ viewport: { width: 1280, height: 960 } });
  const page = await context.newPage();
  page.on('pageerror', error => evidence.console_errors.push(error.message));
  page.on('console', message => { if (!expectNetworkErrors && message.type() === 'error' && !message.text().includes('404')) evidence.console_errors.push(message.text()); });
  await page.goto(startup.url); await page.locator('#arm').waitFor({ state: 'visible' });
  await page.waitForFunction(() => !document.querySelector('#arm').disabled);
  await check('desktop OFFLINE label, real C++ status, no horizontal overflow', async () => {
    assert.match(await page.locator('header').innerText(), /OFFLINE/);
    assert.match(await page.locator('#transport').innerText(), /C\+\+/);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  });
  const desktop = path.join(output, 'teleop-desktop.png'); await page.screenshot({ path: desktop, fullPage: true }); evidence.screenshots.push(desktop);
  await check('neutral arm, keyboard forward, opposite cancellation, turn and deadman release', async () => {
    await arm(page);
    await page.keyboard.down('Shift'); await page.keyboard.down('w'); await until(s => s.linear_mps === .25, 'keyboard forward');
    await page.keyboard.down('s'); await until(s => s.linear_mps === 0, 'opposite keys cancel'); await page.keyboard.up('s');
    await page.keyboard.down('a'); await until(s => s.angular_radps === .6, 'left turn');
    await page.keyboard.up('Shift'); await until(s => s.linear_mps === 0 && s.angular_radps === 0, 'deadman released');
    await page.keyboard.up('w'); await page.keyboard.up('a'); await unarm(page);
  });
  await check('virtual pointer enable plus direction and pointercancel revokes', async () => {
    await arm(page); const box = await page.locator('#enable').boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2); await page.mouse.down();
    await page.keyboard.down('ArrowUp'); await until(s => s.linear_mps === .25, 'pointer deadman');
    await page.locator('#enable').dispatchEvent('pointercancel', { pointerId: 1 });
    await until(s => !s.armed && s.linear_mps === 0, 'pointercancel release'); await page.mouse.up(); await page.keyboard.up('ArrowUp');
    assert.equal(await page.locator('.pressed').count(), 0);
  });
  await check('window blur clears held inputs and focus cannot auto arm', async () => {
    await arm(page); await page.keyboard.down('Shift'); await page.keyboard.down('w'); await until(s => s.linear_mps > 0, 'moving before blur');
    await page.evaluate(() => window.dispatchEvent(new Event('blur'))); await until(s => !s.armed && s.linear_mps === 0, 'blur release');
    await page.keyboard.up('Shift'); await page.keyboard.up('w');
    await page.evaluate(() => window.dispatchEvent(new Event('focus'))); await page.waitForTimeout(180); assert.equal((await state()).armed, false);
  });
  await check('late prior release response cannot revoke a new session or overwrite its display', async () => {
    await arm(page);
    let releaseReply, finishRelease;
    const gate = new Promise(resolve => { finishRelease = resolve; });
    await page.route('**/api/command', async route => {
      if (!releaseReply && route.request().postDataJSON()?.action === 'release') {
        const response = await route.fetch(); releaseReply = await response.json();
        await gate; await route.fulfill({ response });
      } else await route.continue();
    });
    await page.locator('#release').click(); await until(s => !s.armed, 'release is processed before its delayed response');
    await arm(page); const session = (await state()).session_id;
    finishRelease(); await page.waitForTimeout(150); await page.unroute('**/api/command');
    assert.ok(releaseReply); assert.equal((await state()).session_id, session);
    assert.match(await page.locator('#arm').innerText(), /本窗口持有/);
    await unarm(page);
  });
  await check('full loopback service and C++ restart change boot, clear control and require manual rearm', async () => {
    await arm(page); const before = await state();
    await page.keyboard.down('Shift'); await page.keyboard.down('w'); await until(s => s.linear_mps > 0, 'movement before owned service restart');
    expectNetworkErrors = true; child.stdin.write('restart-service\n');
    const deadline = Date.now() + 5000;
    while (!restartInfo && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 20));
    assert.ok(restartInfo, 'restarted service acknowledged by test harness');
    await page.keyboard.up('Shift'); await page.keyboard.up('w');
    await page.waitForFunction(() => !document.querySelector('#arm').disabled);
    const after = await state(); assert.notEqual(after.boot_id, before.boot_id);
    assert.equal(after.armed, false); assert.equal(after.linear_mps, 0); assert.equal(after.angular_radps, 0);
    assert.equal(await page.locator('.pressed').count(), 0); expectNetworkErrors = false;
    await arm(page); await unarm(page);
  });
  await check('Space stop latches, clear leaves disarmed', async () => {
    await arm(page); await page.keyboard.press('Space'); await until(s => s.stop_latched && !s.armed, 'software stop');
    await page.locator('#clear').click(); await until(s => !s.stop_latched && !s.armed, 'clear stop');
  });
  await check('two windows do not steal a live lease', async () => {
    await arm(page); const owner = (await state()).controller_id;
    const other = await context.newPage(); await other.goto(startup.url);
    const session = await (await fetch(startup.url + '/api/session')).json();
    const post = async body => (await fetch(startup.url + '/api/command', { method: 'POST', headers: { Origin: startup.url, 'Content-Type': 'application/json', 'X-CSRF-Token': session.csrf }, body: JSON.stringify(body) })).json();
    // Headless contexts can report both pages focused; test arbitration while
    // the first page is still actively renewing, without assuming tab focus.
    const first = await state(); assert.equal(first.controller_id, owner);
    assert.equal(await other.locator('#arm').isDisabled(), true);
    const second = (await post({ action: 'arm', controller_id: '123', source: 'gamepad' })).state;
    assert.equal(second.controller_id, first.controller_id); assert.equal(second.session_id, first.session_id);
    await post({ action: 'release', controller_id: first.controller_id, boot_id: first.boot_id, session_id: first.session_id });
    await other.close(); await page.bringToFront();
  });
  await check('source switch releases active keyboard session', async () => {
    await arm(page); await page.locator('#gamepad-tab').click(); await until(s => !s.armed, 'source switch'); assert.equal(await page.locator('#arm').isDisabled(), true);
  });
  await check('synthetic standard Gamepad left stick / RB, deadzone, disconnect', async () => {
    await page.evaluate(() => {
      window.__testPad = { connected: true, mapping: 'standard', index: 0, axes: [0, 0], buttons: Array.from({ length: 17 }, () => ({ pressed: false, value: 0 })) };
      Object.defineProperty(navigator, 'getGamepads', { value: () => window.__testPad ? [window.__testPad] : [] });
    });
    await page.waitForFunction(() => !document.querySelector('#arm').disabled); await arm(page);
    await page.evaluate(() => { window.__testPad.axes = [0, -1]; window.__testPad.buttons[5].pressed = true; });
    await until(s => s.linear_mps === .25, 'gamepad forward');
    await page.evaluate(() => { window.__testPad.axes = [.15, -.15]; }); await until(s => s.linear_mps === 0 && s.angular_radps === 0, 'deadzone boundary');
    await page.evaluate(() => { window.__testPad.axes = [1, 0]; }); await until(s => s.angular_radps === -.6, 'gamepad right turn');
    await page.evaluate(() => { window.__testPad = null; window.dispatchEvent(Object.assign(new Event('gamepaddisconnected'), { gamepad: { index: 0 } })); });
    await until(s => !s.armed && s.angular_radps === 0, 'gamepad disconnect');
    assert.equal(await page.locator('#arm').isDisabled(), true);
  });
  await check('unknown Gamepad mapping cannot arm', async () => {
    await page.evaluate(() => { window.__testPad = { connected: true, mapping: '', index: 0, axes: [0, 0], buttons: Array.from({ length: 6 }, () => ({ pressed: false })) }; });
    await page.waitForTimeout(150); assert.equal(await page.locator('#arm').isDisabled(), true);
  });
  await page.locator('#keyboard-tab').click();
  await check('hidden lifecycle clears session; visible return stays disarmed', async () => {
    await arm(page);
    await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, get: () => true }); document.dispatchEvent(new Event('visibilitychange')); });
    await until(s => !s.armed, 'hidden release');
    await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, get: () => false }); document.dispatchEvent(new Event('visibilitychange')); });
    await page.waitForTimeout(150); assert.equal((await state()).armed, false);
  });
  await check('request failure clears active control; recovery never auto arms', async () => {
    await arm(page); expectNetworkErrors = true; await page.route('**/api/command', route => route.abort());
    await until(s => !s.armed && s.linear_mps === 0, 'request failure expires');
    await page.unroute('**/api/command'); await page.waitForTimeout(200); expectNetworkErrors = false; assert.equal((await state()).armed, false);
  });
  await check('delayed command retains old issued timestamp and cannot revive expired session', async () => {
    await arm(page);
    let delayed = false;
    await page.route('**/api/command', async route => {
      if (!delayed && ['keys', 'pad'].includes(route.request().postDataJSON()?.action)) { delayed = true; await new Promise(resolve => setTimeout(resolve, 350)); }
      await route.continue();
    });
    await until(s => !s.armed && s.linear_mps === 0, 'delayed command expiration');
    await page.waitForTimeout(300); await page.unroute('**/api/command');
    assert.equal(delayed, true); assert.equal((await state()).armed, false);
  });
  await check('changed service CSRF token reacquires observation without restoring control', async () => {
    await arm(page); expectNetworkErrors = true;
    const oldSession = await (await fetch(startup.url + '/api/session')).json();
    child.stdin.write('rotate-csrf\n');
    await until(s => !s.armed && s.linear_mps === 0, 'CSRF change must end control');
    await page.waitForFunction(() => !document.querySelector('#arm').disabled);
    const newSession = await (await fetch(startup.url + '/api/session')).json();
    assert.notEqual(newSession.csrf, oldSession.csrf);
    assert.equal((await state()).armed, false);
    expectNetworkErrors = false;
    await arm(page); await unarm(page); // verifies that the refreshed token works
  });
  await check('stalled fetch cannot keep old motion displayed while awaiting its timeout', async () => {
    await arm(page); await page.keyboard.down('Shift'); await page.keyboard.down('w');
    await until(s => s.linear_mps > 0, 'moving before stalled request');
    let blocked = false, unblock;
    const gate = new Promise(resolve => { unblock = resolve; });
    await page.route('**/api/command', async route => {
      if (!blocked && route.request().postDataJSON()?.action === 'keys') { blocked = true; await gate; }
      await route.continue();
    });
    await until(s => !s.armed && s.linear_mps === 0, 'stale watchdog releases while request is pending');
    assert.equal(blocked, true); assert.match(await page.locator('#linear').innerText(), /^0\.00/);
    assert.equal(await page.locator('.pressed').count(), 0);
    unblock(); await page.waitForTimeout(100); await page.unroute('**/api/command');
    await page.keyboard.up('Shift'); await page.keyboard.up('w');
    assert.equal((await state()).armed, false);
  });
  await check('ten second fixed lease ends despite continued neutral renewal', async () => {
    await arm(page);
    // Observe a 10s property at 8Hz. A 50Hz Node observer needlessly competes
    // with the browser heartbeat and the Python thread-per-request server.
    const samples = []; const deadline = Date.now() + 11500; let expired = null;
    while (Date.now() < deadline) {
      const s = await state();
      samples.push({ elapsed_ms: s.elapsed_ms, sequence: s.sequence, lease_remaining_ms: s.lease_remaining_ms,
        command_remaining_ms: s.command_remaining_ms, armed: s.armed, reason: s.stop_reason });
      if (!s.armed) { expired = s; break; }
      await page.waitForTimeout(120);
    }
    evidence.lease_timing = samples;
    assert.ok(expired, 'lease must end within bounded observation');
    assert.equal(expired.stop_reason, 'LeaseExpired', 'earlier command timeout is distinct from lease expiry');
    assert.ok(samples.some(s => BigInt(s.sequence) >= 50n), 'fresh neutral commands must continue throughout the lease');
    assert.ok(expired.elapsed_ms >= samples[0].elapsed_ms + samples[0].lease_remaining_ms);
  });
  const mobileContext = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });
  const mobile = await mobileContext.newPage(); await mobile.goto(startup.url); await mobile.waitForFunction(() => !document.querySelector('#arm').disabled);
  await check('390px mobile layout and touch buttons remain usable without overflow', async () => {
    assert.equal(await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    const box = await mobile.locator('[data-input="forward"]').boundingBox(); assert.ok(box.width >= 44 && box.height >= 44);
    await mobile.locator('#arm').tap(); await until(s => s.armed && s.neutral_confirmed, 'mobile arm');
    await mobile.locator('#enable').scrollIntoViewIfNeeded();
    const enable = await mobile.locator('#enable').boundingBox();
    const forward = await mobile.locator('[data-input="forward"]').boundingBox();
    const touch = (rect, id) => ({ x: rect.x + rect.width / 2, y: rect.y + rect.height / 2, id });
    const cdp = await mobileContext.newCDPSession(mobile);
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [touch(enable, 1)] });
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [touch(enable, 1), touch(forward, 2)] });
    await until(s => s.linear_mps === .25, 'emulated multitouch enable and forward');
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [touch(enable, 1)] });
    await until(s => s.linear_mps === 0, 'touch direction release');
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await mobile.locator('#stop').tap(); await until(s => s.stop_latched && !s.armed, 'mobile stop');
  });
  const screenshot = path.join(output, 'teleop-390px.png'); await mobile.screenshot({ path: screenshot, fullPage: true }); evidence.screenshots.push(screenshot);
  await check('initial handshake failures clear display and retry observation without reload or arm', async () => {
    const recovering = await mobileContext.newPage(); let failures = 0;
    await recovering.route('**/api/session', route => ++failures <= 2 ? route.abort() : route.continue());
    await recovering.goto(startup.url); await recovering.bringToFront();
    await recovering.waitForFunction(() => document.querySelector('#owner').textContent === '无有效控制权');
    assert.equal(await recovering.locator('#lease').innerText(), '—');
    assert.equal(await recovering.locator('#ttl').innerText(), '—');
    assert.equal(await recovering.locator('#release').isDisabled(), true);
    await recovering.waitForFunction(() => document.querySelector('#transport').textContent.includes('C++'));
    assert.ok(failures >= 3); assert.equal((await state()).armed, false);
    await recovering.close();
  });
  assert.deepEqual(evidence.console_errors, []);
  evidence.passed = true;
} catch (error) {
  evidence.passed = false; evidence.error = error.stack; throw error;
} finally {
  if (browser) await browser.close();
  child.stdin.end('quit\n');
  const exited = await Promise.race([once(child, 'exit').then(([code]) => code), new Promise(resolve => setTimeout(() => resolve('timeout'), 5000))]);
  evidence.service_exit_code = exited;
  if (exited === 'timeout') child.kill();
  evidence.agent_reaped = agentPids.every(pid => { try { process.kill(pid, 0); return false; } catch (error) { return error.code === 'ESRCH'; } });
  evidence.browser_closed = !!browser;
  evidence.stderr = stderr;
  await writeFile(path.join(output, 'web-browser-result.json'), JSON.stringify(evidence, null, 2));
  process.stdout.write(JSON.stringify(evidence, null, 2) + '\n');
}
