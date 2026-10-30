// Pure input/session policy, shared by the page and Node boundary tests.
export const KEY_CODES = new Set(['KeyW', 'KeyA', 'KeyS', 'KeyD', 'ArrowUp', 'ArrowLeft', 'ArrowDown', 'ArrowRight', 'ShiftLeft', 'ShiftRight']);
export function randomController(crypto) {
  const words = crypto.getRandomValues(new Uint32Array(2));
  return (((BigInt(words[0]) << 32n) | BigInt(words[1])) || 1n).toString();
}
export function readPad(pad) {
  if (!pad || !pad.connected || pad.mapping !== 'standard' || pad.axes.length < 2 || pad.buttons.length < 6) return null;
  const [x, y] = pad.axes;
  if (![x, y].every(v => Number.isFinite(v) && Math.abs(v) <= 1)) return null;
  return { x, y, deadman: !!pad.buttons[5].pressed, index: pad.index };
}
export function scaledAxis(value) {
  if (!Number.isFinite(value) || Math.abs(value) > 1) throw new Error('invalid axis');
  return Math.abs(value) <= 0.15 ? 0 : Math.sign(value) * (Math.abs(value) - 0.15) / 0.85;
}
export class InputSession {
  constructor(id, clock = () => performance.now()) {
    this.id = id;
    this.clock = clock;
    this.source = 'keyboard';
    this.state = null;
    this.seenAt = -Infinity;
    this.active = false;
    this.held = new Set();
    this.virtual = new Set();
    this.pad = null;
    this.seq = 0n;
    this.generation = 0;
  }
  owns(state = this.state) {
    return !!state?.armed && state.controller_id === this.id && state.owner === this.source;
  }
  observe(state) {
    if (!state || state.source !== 'mock_teleop' || !Number.isSafeInteger(state.elapsed_ms) || state.elapsed_ms < 0 ||
        !['boot_id', 'session_id', 'controller_id', 'sequence'].every(k => typeof state[k] === 'string' && /^(0|[1-9][0-9]{0,19})$/.test(state[k]) && BigInt(state[k]) <= 18446744073709551615n) ||
        ![state.linear_mps, state.angular_radps].every(Number.isFinite)) throw new Error('invalid controller state');
    if (this.active && (!this.owns(state) || state.boot_id !== this.state.boot_id || state.session_id !== this.state.session_id)) this.invalidate();
    this.state = state;
    this.seenAt = this.clock();
  }
  activate(state, generation) {
    this.observe(state);
    if (generation !== this.generation || !this.owns() || !this.neutral()) return false;
    this.active = true;
    this.seq = BigInt(state.sequence);
    return true;
  }
  neutral() {
    if (this.source === 'gamepad') return !!this.pad && !this.pad.deadman && Math.abs(this.pad.x) <= 0.15 && Math.abs(this.pad.y) <= 0.15;
    return this.held.size === 0 && this.virtual.size === 0;
  }
  release(state = this.state) {
    return this.owns(state) ? { action: 'release', controller_id: this.id, boot_id: state.boot_id, session_id: state.session_id } : null;
  }
  invalidate() {
    const release = this.release();
    this.active = false;
    this.held.clear();
    this.virtual.clear();
    this.pad = null;
    this.generation++;
    return release;
  }
  packet() {
    if (!this.active || !this.owns() || this.clock() - this.seenAt > 150) return null;
    const state = this.state;
    this.seq++;
    if (this.seq > 18446744073709551615n || state.elapsed_ms > Number.MAX_SAFE_INTEGER - 250) return null;
    const packet = { action: this.source === 'keyboard' ? 'keys' : 'pad', controller_id: this.id,
      boot_id: state.boot_id, session_id: state.session_id, sequence: this.seq.toString(),
      issued_ms: state.elapsed_ms, valid_until_ms: state.elapsed_ms + 250 };
    if (this.source === 'gamepad') {
      if (!this.pad) return null;
      return { ...packet, deadman: this.pad.deadman, axis_x: this.pad.x, axis_y: this.pad.y };
    }
    const down = (...keys) => keys.some(key => this.held.has(key) || this.virtual.has(key));
    return { ...packet, deadman: down('ShiftLeft', 'ShiftRight', 'enable'), forward: down('KeyW', 'ArrowUp', 'forward'),
      backward: down('KeyS', 'ArrowDown', 'backward'), left: down('KeyA', 'ArrowLeft', 'left'), right: down('KeyD', 'ArrowRight', 'right') };
  }
}
