import type { Mat } from './config';

/** Tiny procedural sound kit; everything is synthesized, no audio files. */
export class Sfx {
  private ctx: AudioContext | null = null;
  private master: GainNode | null = null;
  private noise: AudioBuffer | null = null;
  muted = false;

  /** Must be called from a user gesture. */
  unlock() {
    if (this.ctx) {
      void this.ctx.resume();
      return;
    }
    try {
      const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      this.ctx = new Ctx();
      this.master = this.ctx.createGain();
      this.master.gain.value = 0.55;
      const comp = this.ctx.createDynamicsCompressor();
      this.master.connect(comp).connect(this.ctx.destination);
      const len = this.ctx.sampleRate * 2;
      this.noise = this.ctx.createBuffer(1, len, this.ctx.sampleRate);
      const d = this.noise.getChannelData(0);
      for (let k = 0; k < len; k++) d[k] = Math.random() * 2 - 1;
    } catch {
      this.ctx = null;
    }
  }

  setMuted(m: boolean) {
    this.muted = m;
    if (this.master && this.ctx) this.master.gain.setTargetAtTime(m ? 0 : 0.55, this.ctx.currentTime, 0.02);
  }

  private ready() {
    return this.ctx && this.master && this.noise && !this.muted ? this.ctx : null;
  }

  private noiseBurst(dur: number, freq: number, freqEnd: number, gain: number, q = 0.8, delay = 0) {
    const ctx = this.ready();
    if (!ctx) return;
    const t = ctx.currentTime + delay;
    const src = ctx.createBufferSource();
    src.buffer = this.noise;
    src.playbackRate.value = 0.7 + Math.random() * 0.6;
    const f = ctx.createBiquadFilter();
    f.type = 'lowpass';
    f.Q.value = q;
    f.frequency.setValueAtTime(freq, t);
    f.frequency.exponentialRampToValueAtTime(Math.max(40, freqEnd), t + dur);
    const g = ctx.createGain();
    g.gain.setValueAtTime(gain, t);
    g.gain.exponentialRampToValueAtTime(0.001, t + dur);
    src.connect(f).connect(g).connect(this.master!);
    src.start(t, Math.random(), dur + 0.05);
  }

  private tone(type: OscillatorType, f0: number, f1: number, dur: number, gain: number, delay = 0) {
    const ctx = this.ready();
    if (!ctx) return;
    const t = ctx.currentTime + delay;
    const o = ctx.createOscillator();
    o.type = type;
    o.frequency.setValueAtTime(f0, t);
    o.frequency.exponentialRampToValueAtTime(Math.max(20, f1), t + dur);
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(gain, t + 0.01);
    g.gain.exponentialRampToValueAtTime(0.001, t + dur);
    o.connect(g).connect(this.master!);
    o.start(t);
    o.stop(t + dur + 0.05);
  }

  place(m: Mat | 'core' | 'weapon') {
    const f = { wood: 520, stone: 300, iron: 900, core: 1200, weapon: 420 }[m];
    this.tone(m === 'iron' ? 'square' : 'triangle', f, f * 0.6, 0.09, 0.22);
    this.noiseBurst(0.06, 3000, 600, 0.15);
    if (m === 'core') this.tone('sine', 1320, 1760, 0.3, 0.12, 0.05);
  }

  remove() {
    this.tone('triangle', 380, 220, 0.08, 0.15);
  }

  click() {
    this.tone('triangle', 900, 700, 0.04, 0.08);
  }

  deny() {
    this.tone('square', 180, 140, 0.12, 0.08);
  }

  fire(big: boolean) {
    if (big) {
      this.tone('sine', 140, 40, 0.5, 0.6);
      this.noiseBurst(0.5, 2400, 120, 0.7);
    } else {
      this.noiseBurst(0.18, 900, 200, 0.25);
      this.tone('triangle', 220, 90, 0.2, 0.25);
    }
  }

  boom(size: number) {
    this.tone('sine', 110, 30, 0.6 + size * 0.3, Math.min(0.9, 0.4 + size * 0.3));
    this.noiseBurst(0.7 + size * 0.4, 1800, 60, Math.min(1, 0.5 + size * 0.3));
    this.noiseBurst(0.25, 5000, 800, 0.25, 0.5, 0.02);
  }

  crunch(m: Mat) {
    const f = { wood: 1400, stone: 2200, iron: 3600 }[m];
    this.noiseBurst(0.22, f, 200, 0.35, 2);
    if (m === 'iron') this.tone('square', 760, 300, 0.15, 0.06);
  }

  splash() {
    this.noiseBurst(0.5, 3500, 300, 0.3, 0.3);
  }

  coreBreak() {
    this.boom(2.5);
    [0, 0.08, 0.16, 0.3].forEach((d, k) => this.tone('sine', 1760 - k * 220, 400, 0.9, 0.12, d));
  }

  tick() {
    this.tone('square', 1200, 1200, 0.03, 0.05);
  }

  fanfare(win: boolean) {
    const notes = win ? [523, 659, 784, 1047] : [440, 392, 349, 262];
    notes.forEach((n, k) => this.tone('triangle', n, n, 0.35, 0.18, k * 0.14));
  }
}
