import type { Aim } from './ballistics';
import { MATS, WEAPONS, type Mat, type PieceKind, type Side, type Stock, type WeaponKind } from './config';

const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;

const ICONS: Record<string, string> = {
  catapult:
    '<svg viewBox="0 0 24 24" fill="none" stroke="#f5d7a8" stroke-width="2" stroke-linecap="round"><path d="M3 20h18M6 20l3-8 3 8M9 12l9-7"/><circle cx="18.5" cy="4.5" r="2" fill="#f5d7a8"/></svg>',
  cannon:
    '<svg viewBox="0 0 24 24" fill="none" stroke="#cfd6e2" stroke-width="2" stroke-linecap="round"><path d="M4 14l13-6 2 4-13 6z" fill="#cfd6e2"/><circle cx="8" cy="18" r="3"/></svg>',
  erase:
    '<svg viewBox="0 0 24 24" fill="none" stroke="#ffb3a8" stroke-width="2" stroke-linecap="round"><path d="M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13"/></svg>',
};

export type Tool = PieceKind | 'erase';

const TOOL_ORDER: Tool[] = ['wood', 'stone', 'iron', 'core', 'catapult', 'cannon', 'erase'];

function costText(kind: WeaponKind) {
  const c = WEAPONS[kind].cost;
  return (Object.keys(c) as Mat[]).map((m) => `${c[m]}${MATS[m].name}`).join(' ');
}

export interface WeaponTab {
  id: number;
  label: string;
  sub: string;
  usable: boolean;
}

export class UI {
  readonly canvas = $<HTMLCanvasElement>('scene');
  private toastTimer = 0;
  private readonly yaw = $<HTMLInputElement>('s-yaw');
  private readonly pitch = $<HTMLInputElement>('s-pitch');
  private readonly power = $<HTMLInputElement>('s-power');
  onAim: (a: Aim) => void = () => {};

  constructor() {
    for (const el of [this.yaw, this.pitch, this.power]) {
      el.addEventListener('input', () => {
        this.refreshAimLabels();
        this.onAim(this.readAim());
      });
    }
    this.yaw.min = '-35';
    this.yaw.max = '35';
    this.yaw.step = '0.5';
    this.power.min = '0';
    this.power.max = '100';
    this.power.step = '0.5';
    this.pitch.step = '0.5';
  }

  loaded() {
    $('loading').hidden = true;
  }

  showTitle(onStart: () => void) {
    const s = $('title');
    s.hidden = false;
    $('btn-start').onclick = () => {
      s.hidden = true;
      onStart();
    };
  }

  setMode(mode: 'build' | 'battle' | 'none') {
    $('phase').hidden = mode !== 'build';
    $('hp').hidden = mode !== 'battle';
    $('top-spacer').hidden = mode === 'battle';
    $('build-ui').hidden = mode !== 'build';
    if (mode !== 'battle') $('aim-ui').hidden = true;
  }

  /** Height of the visible bottom tray, measured from the bottom of the viewport. */
  trayInset() {
    for (const id of ['build-ui', 'aim-ui']) {
      const el = $(id);
      if (!el.hidden) return Math.max(0, innerHeight - el.getBoundingClientRect().top);
    }
    return 0;
  }

  showAim(show: boolean) {
    $('aim-ui').hidden = !show;
  }

  setTimer(sec: number) {
    const t = $('timer');
    const s = Math.max(0, Math.ceil(sec));
    t.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
    t.classList.toggle('hurry', s <= 10);
  }

  renderTools(stock: Stock, hasCore: boolean, selected: Tool, canAfford: (t: Tool) => boolean, onSelect: (t: Tool) => void) {
    const box = $('tools');
    box.innerHTML = '';
    for (const t of TOOL_ORDER) {
      const b = document.createElement('button');
      b.className = 'tool' + (t === selected ? ' sel' : '') + (canAfford(t) ? '' : ' off');
      b.id = `tool-${t}`;
      let name = '';
      let sub = '';
      if (t === 'wood' || t === 'stone' || t === 'iron') {
        name = `${MATS[t].name}块`;
        sub = `剩 ${stock[t]}`;
      } else if (t === 'core') {
        name = '核心';
        sub = hasCore ? '已放置' : '必须放';
      } else if (t === 'catapult' || t === 'cannon') {
        name = WEAPONS[t].name;
        sub = costText(t);
      } else {
        name = '拆除';
        sub = '拿掉顶层';
      }
      b.innerHTML = `<span class="swatch ${t}">${ICONS[t] ?? ''}</span><span class="nm">${name}</span><span class="sub">${sub}</span>`;
      b.onclick = () => onSelect(t);
      box.appendChild(b);
    }
  }

  onBuildButtons(onAuto: () => void, onDone: () => void) {
    $('btn-auto').onclick = onAuto;
    $('btn-done').onclick = onDone;
  }

  renderWeapons(tabs: WeaponTab[], selected: number | null, onSelect: (id: number) => void) {
    const box = $('weapons');
    box.innerHTML = '';
    for (const t of tabs) {
      const b = document.createElement('button');
      b.className = 'wtab' + (t.id === selected ? ' sel' : '') + (t.usable ? '' : ' off');
      b.innerHTML = `${t.label}<small>${t.sub}</small>`;
      b.onclick = () => onSelect(t.id);
      box.appendChild(b);
    }
  }

  setAim(kind: WeaponKind, a: Aim) {
    const w = WEAPONS[kind];
    this.pitch.min = String(w.pitchMin);
    this.pitch.max = String(w.pitchMax);
    this.yaw.value = String((a.yaw * 180) / Math.PI);
    this.pitch.value = String(a.pitch);
    this.power.value = String(a.power * 100);
    this.refreshAimLabels();
  }

  readAim(): Aim {
    return {
      yaw: (Number(this.yaw.value) * Math.PI) / 180,
      pitch: Number(this.pitch.value),
      power: Number(this.power.value) / 100,
    };
  }

  /** Nudges a slider from the keyboard. */
  nudge(which: 'yaw' | 'pitch' | 'power', delta: number) {
    const el = which === 'yaw' ? this.yaw : which === 'pitch' ? this.pitch : this.power;
    el.value = String(Number(el.value) + delta);
    this.refreshAimLabels();
    this.onAim(this.readAim());
  }

  private refreshAimLabels() {
    const y = Number(this.yaw.value);
    $('o-yaw').textContent = Math.abs(y) < 0.25 ? '正前' : `${y < 0 ? '左' : '右'} ${Math.abs(y).toFixed(1)}°`;
    $('o-pitch').textContent = `${Number(this.pitch.value).toFixed(1)}°`;
    $('o-power').textContent = `${Math.round(Number(this.power.value))}%`;
  }

  onFire(cb: () => void) {
    $('btn-fire').onclick = cb;
  }

  setFireEnabled(on: boolean) {
    $<HTMLButtonElement>('btn-fire').disabled = !on;
  }

  onView(cb: () => void) {
    $('btn-view').onclick = cb;
  }

  setViewLabel(free: boolean) {
    $('btn-view').textContent = free ? '回到瞄准' : '自由视角';
  }

  setTurn(text: string, sub: string) {
    $('turn').innerHTML = `${text}<small>${sub}</small>`;
  }

  setHP(side: Side, hp: number, max: number) {
    const box = $(`hp-${side}`);
    box.querySelector('b')!.textContent = String(Math.max(0, Math.round(hp)));
    (box.querySelector('.bar i') as HTMLElement).style.width = `${Math.max(0, (hp / max) * 100)}%`;
  }

  banner(title: string, sub = '') {
    const b = $('banner');
    b.querySelector('h2')!.textContent = title;
    b.querySelector('p')!.textContent = sub;
    b.classList.remove('show');
    void b.offsetWidth;
    b.classList.add('show');
  }

  flash() {
    const f = $('flash');
    f.classList.remove('go');
    void f.offsetWidth;
    f.classList.add('go');
  }

  toast(msg: string, ms = 1600) {
    const t = $('toast');
    t.textContent = msg;
    t.classList.add('show');
    clearTimeout(this.toastTimer);
    this.toastTimer = window.setTimeout(() => t.classList.remove('show'), ms);
  }

  floater(text: string, x: number, y: number, color: string) {
    const f = document.createElement('div');
    f.className = 'floater';
    f.textContent = text;
    f.style.left = `${x}px`;
    f.style.top = `${y}px`;
    f.style.color = color;
    $('floaters').appendChild(f);
    setTimeout(() => f.remove(), 1300);
  }

  enemyTag(x: number, y: number, show: boolean) {
    const t = $('enemy-tag');
    t.hidden = !show;
    if (show) {
      t.style.left = `${x}px`;
      t.style.top = `${y}px`;
    }
  }

  onMute(cb: () => boolean) {
    $('mute').onclick = () => {
      const muted = cb();
      $('mute').querySelector('.wave')!.toggleAttribute('hidden', muted);
      $('mute').querySelector('.x')!.toggleAttribute('hidden', !muted);
    };
  }

  showEnd(result: 'win' | 'lose' | 'draw', reason: string, stats: { shots: number; broke: number; lost: number }, onAgain: () => void) {
    const t = $('end-title');
    t.textContent = result === 'win' ? '胜利' : result === 'lose' ? '失败' : '平局';
    t.className = `result ${result}`;
    $('end-reason').textContent = reason;
    $('st-shots').textContent = String(stats.shots);
    $('st-broke').textContent = String(stats.broke);
    $('st-lost').textContent = String(stats.lost);
    const s = $('end');
    s.hidden = false;
    $('btn-again').onclick = () => {
      s.hidden = true;
      onAgain();
    };
  }
}
