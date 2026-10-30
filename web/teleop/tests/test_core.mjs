import test from 'node:test';
import assert from 'node:assert/strict';
import { InputSession, readPad, scaledAxis, randomController, KEY_CODES, ResponseOrder } from '../core.mjs';
test('responses apply once in normal order', () => {
  const order = new ResponseOrder(); const a = order.begin(); const b = order.begin();
  assert.equal(order.accept(a), true); assert.equal(order.accept(b), true); assert.equal(order.accept(b), false);
});
test('late release response cannot supersede a newer arm response', () => {
  const order = new ResponseOrder(); const release = order.begin(); const arm = order.begin();
  assert.equal(order.accept(arm), true); assert.equal(order.current(release), false); assert.equal(order.accept(release), false);
});
test('disconnect retires every pending response and allows a new handshake', () => {
  const order = new ResponseOrder(); const old = order.begin(); order.reset(); const handshake = order.begin();
  assert.equal(order.accept(old), false); assert.equal(order.accept(handshake), true);
});
test('late error from prior service epoch cannot interrupt renewed observation', () => {
  const order = new ResponseOrder(); const old = order.begin(); order.reset(); order.accept(order.begin());
  assert.equal(order.current(old), false);
});
test('multiple connection losses never reuse retired request identity', () => {
  const order = new ResponseOrder(); const tickets = [];
  for (let i = 0; i < 10; i++) { tickets.push(order.begin()); order.reset(); }
  const latest = order.begin(); for (const ticket of tickets) assert.equal(order.accept(ticket), false);
  assert.equal(order.accept(latest), true);
});
const state = (values = {}) => ({ source: 'mock_teleop', elapsed_ms: 1000, boot_id: '18446744073709551615', session_id: '9007199254740993', controller_id: '9007199254740997', sequence: '0', armed: true, owner: 'keyboard', linear_mps: 0, angular_radps: 0, ...values });
function ready(source = 'keyboard') {
  let time = 10;
  const input = new InputSession('9007199254740997', () => time);
  input.source = source;
  if (source === 'gamepad') input.pad = { x: 0, y: 0, deadman: false };
  assert.equal(input.activate(state({ owner: source }), input.generation), true);
  return { input, age: value => { time += value; } };
}
test('random controller retains uint64 decimal precision and is nonzero', () => {
  assert.equal(randomController({ getRandomValues: () => [0xffffffff, 0xffffffff] }), '18446744073709551615');
  assert.equal(randomController({ getRandomValues: () => [0, 0] }), '1');
});
test('initial and observed ownership cannot auto arm', () => {
  const input = new InputSession('9007199254740997'); input.observe(state());
  assert.equal(input.active, false); assert.equal(input.packet(), null);
});
test('first packet is neutral and uses last observed clock without restamping', () => {
  const { input, age } = ready(); age(40); const packet = input.packet();
  assert.equal(packet.issued_ms, 1000); assert.equal(packet.valid_until_ms, 1250);
  assert.equal(packet.boot_id, '18446744073709551615'); assert.equal(packet.session_id, '9007199254740993');
  assert.equal(packet.sequence, '1'); assert.equal(packet.deadman, false);
  assert.equal(packet.forward, false);
});
test('stale observed clock stops producing commands', () => { const { input, age } = ready(); age(151); assert.equal(input.packet(), null); });
test('sequence increments beyond JS numeric precision', () => { const { input } = ready(); input.seq = 9007199254740992n; assert.equal(input.packet().sequence, '9007199254740993'); });
test('uint64 sequence exhaustion and clock overflow refuse packets', () => { const { input } = ready(); input.seq = 18446744073709551615n; assert.equal(input.packet(), null); input.seq = 0n; input.state.elapsed_ms = Number.MAX_SAFE_INTEGER; assert.equal(input.packet(), null); });
test('keyboard aliases and opposite directions preserve both flags for C++ cancellation', () => {
  const { input } = ready(); for (const k of ['KeyW', 'ArrowDown', 'KeyA', 'ArrowRight', 'ShiftLeft']) input.held.add(k);
  assert.deepEqual(Object.values(input.packet()).slice(-5), [true, true, true, true, true]);
});
test('virtual direction with held enable and release to zero', () => {
  const { input } = ready(); input.virtual.add('forward'); input.virtual.add('enable'); assert.equal(input.packet().deadman, true);
  input.virtual.delete('enable'); assert.equal(input.packet().deadman, false);
});
test('cannot arm with key held or gamepad off center / RB down', () => {
  const input = new InputSession('9007199254740997'); input.held.add('ShiftLeft'); assert.equal(input.activate(state(), 0), false);
  input.held.clear(); input.source = 'gamepad';
  for (const pad of [null, { x: .151, y: 0, deadman: false }, { x: 0, y: 0, deadman: true }]) { input.pad = pad; assert.equal(input.activate(state({ owner: 'gamepad' }), 0), false); }
});
test('all lifecycle invalidations clear keyboard, pointer and gamepad and need manual rearm', () => {
  for (const reason of ['blur', 'hidden', 'pagehide', 'pointercancel', 'disconnect', 'request-failure', 'source-switch']) {
    const { input } = ready(); input.held.add('KeyW'); input.virtual.add('enable'); input.pad = { x: 1, y: 1, deadman: true };
    const release = input.invalidate(reason); assert.equal(release.controller_id, input.id); assert.equal(input.held.size, 0); assert.equal(input.virtual.size, 0); assert.equal(input.pad, null);
    input.observe(state()); assert.equal(input.active, false); assert.equal(input.packet(), null);
  }
});
test('late arm response after lifecycle invalidation cannot activate', () => { const { input } = ready(); const generation = input.generation; input.invalidate(); assert.equal(input.activate(state(), generation), false); });
test('boot/session change or ownership loss revokes local control', () => {
  for (const change of [{ boot_id: '2' }, { session_id: '4' }, { controller_id: '5' }, { armed: false }, { owner: 'gamepad' }]) { const { input } = ready(); input.observe(state(change)); assert.equal(input.active, false); }
});
test('other window ownership cannot be released or acquired locally', () => { const input = new InputSession('5'); assert.equal(input.activate(state(), 0), false); assert.equal(input.release(), null); });
test('standard pad mapping accepts only finite bounded left axes and RB', () => {
  const pad = { mapping: 'standard', connected: true, index: 2, axes: [-1, 1], buttons: Array.from({ length: 6 }, (_, i) => ({ pressed: i === 5 })) };
  assert.deepEqual(readPad(pad), { x: -1, y: 1, deadman: true, index: 2 });
  for (const change of [{ mapping: '' }, { connected: false }, { axes: [0] }, { axes: [NaN, 0] }, { axes: [1.01, 0] }, { buttons: [] }]) assert.equal(readPad({ ...pad, ...change }), null);
});
test('gamepad command uses unscaled axes for C++ deadzone mapping', () => { const { input } = ready('gamepad'); input.pad = { x: .6, y: -.8, deadman: true }; const p = input.packet(); assert.equal(p.action, 'pad'); assert.equal(p.axis_x, .6); assert.equal(p.axis_y, -.8); });
test('deadzone boundary and full deflection expectations', () => { for (const v of [-.15, 0, .15]) assert.equal(scaledAxis(v), 0); assert.equal(scaledAxis(1), 1); assert.equal(scaledAxis(-1), -1); assert.ok(Math.abs(scaledAxis(.575) - .5) < 1e-12); assert.throws(() => scaledAxis(Infinity)); });
test('invalid state IDs, times and velocities are rejected', () => { const input = new InputSession('2'); for (const invalid of [{ boot_id: 123 }, { sequence: '18446744073709551616' }, { session_id: '-1' }, { elapsed_ms: NaN }, { linear_mps: Infinity }, { source: 'robot' }]) assert.throws(() => input.observe(state(invalid))); });
test('supported keyboard set excludes shortcuts and stop key', () => { assert.equal(KEY_CODES.has('KeyW'), true); assert.equal(KEY_CODES.has('Space'), false); assert.equal(KEY_CODES.has('KeyR'), false); });
