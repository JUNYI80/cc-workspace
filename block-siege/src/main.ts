import * as THREE from 'three';
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js';
import { OutputPass } from 'three/examples/jsm/postprocessing/OutputPass.js';
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';
import { buildAiFort, planAiShot } from './ai';
import { Arena, type Entity, type Projectile } from './arena';
import { Sfx } from './audio';
import { type Aim, aimDirection, launchOrigin, speedFor, trajectory } from './ballistics';
import { CameraRig } from './camera';
import {
  BUILD_TIME,
  CORE_HP,
  GRID,
  WEAPONS,
  isMat,
  isWeapon,
  islandCenterX,
  mulberry32,
  rollPool,
  sideDir,
  type PieceKind,
  type Side,
  type Stock,
  type WeaponKind,
} from './config';
import { FortPlan, cellToWorld, worldToCell } from './fort';
import { Effects } from './fx';
import { PieceFactory, WEAPON_HALF_H, poseWeapon } from './pieces';
import { type Tool, UI } from './ui';
import { createWorld, type WorldScene } from './world';

type Phase = 'title' | 'build' | 'reveal' | 'aim' | 'ai-aim' | 'flight' | 'settle' | 'wait' | 'end';

interface Tween {
  t: number;
  dur: number;
  delay: number;
  fn: (k: number) => void;
  done?: () => void;
}

interface Timer {
  at: number;
  fn: () => void;
}

const MAX_SHOTS = 40;
const easeOutBack = (k: number) => 1 + 2.70158 * (k - 1) ** 3 + 1.70158 * (k - 1) ** 2;
const easeOutBounce = (k: number) => {
  const n = 7.5625;
  const d = 2.75;
  if (k < 1 / d) return n * k * k;
  if (k < 2 / d) return n * (k -= 1.5 / d) * k + 0.75;
  if (k < 2.5 / d) return n * (k -= 2.25 / d) * k + 0.9375;
  return n * (k -= 2.625 / d) * k + 0.984375;
};
const easeInOut = (k: number) => (k < 0.5 ? 2 * k * k : 1 - (-2 * k + 2) ** 2 / 2);
const v3 = (x = 0, y = 0, z = 0) => new THREE.Vector3(x, y, z);
const bodyPos = (e: Entity) => v3(e.body.position.x, e.body.position.y, e.body.position.z);

class Game {
  readonly ui = new UI();
  readonly renderer: THREE.WebGLRenderer;
  readonly composer: EffectComposer;
  readonly bloom: UnrealBloomPass;
  readonly world: WorldScene;
  readonly scene: THREE.Scene;
  readonly rig: CameraRig;
  readonly factory = new PieceFactory();
  readonly fx = new Effects();
  readonly sfx = new Sfx();
  readonly arena: Arena;
  readonly mobile = matchMedia('(pointer: coarse)').matches;

  phase: Phase = 'title';
  phaseT = 0;
  private time = 0;
  private realTime = 0;
  private last = performance.now();
  private hitStop = 0;
  private slowMo = 0;
  private tweens: Tween[] = [];
  private timers: Timer[] = [];

  // build state
  rng = mulberry32(1);
  pool: Stock = { wood: 0, stone: 0, iron: 0 };
  plan = new FortPlan(this.pool);
  private tool: Tool = 'core';
  private readonly buildRoot = new THREE.Group();
  private colMeshes: THREE.Object3D[][] = [];
  private ghost: THREE.Object3D | null = null;
  private ghostKind: Tool | null = null;
  private readonly pickPlane: THREE.Mesh;
  private buildLeft = BUILD_TIME;
  private lastTick = 0;
  private confirmNoWeapon = false;

  // battle state
  turnSide: Side = 0;
  shots = 0;
  private aiTurn = 0;
  private selected: Entity | null = null;
  private aim: Aim = { yaw: 0, pitch: 45, power: 0.5 };
  private freeView = false;
  private activeBall: Projectile | null = null;
  private winner: Side | 'draw' | null = null;
  private readonly dots: THREE.InstancedMesh;
  private readonly marker: THREE.Mesh;
  private readonly selRing: THREE.Mesh;

  // input
  private readonly raycaster = new THREE.Raycaster();
  private readonly pointer = new THREE.Vector2();
  private hovering = false;
  private down: { x: number; y: number; t: number } | null = null;

  constructor() {
    this.renderer = new THREE.WebGLRenderer({ canvas: this.ui.canvas, antialias: false, powerPreference: 'high-performance' });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, this.mobile ? 1.75 : 2));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.05;

    this.world = createWorld();
    this.scene = this.world.scene;
    const pmrem = new THREE.PMREMGenerator(this.renderer);
    this.scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    this.scene.environmentIntensity = 0.5;
    const ms = this.mobile ? 1024 : 2048;
    this.world.sun.shadow.mapSize.set(ms, ms);
    this.scene.add(this.fx.group, this.buildRoot);

    this.rig = new CameraRig(this.ui.canvas);
    this.arena = new Arena(this.scene, this.factory, this.fx, this.sfx, {
      onImpact: (_p, size) => {
        this.rig.shake(0.45 * size);
        this.hitStop = 0.07;
      },
      onCoreHit: (core, amount) => this.coreHit(core, amount),
      onCoreDestroyed: (core) => this.coreDestroyed(core),
      onWeaponLost: (w) => {
        const who = w.side === 0 ? '我方' : '敌方';
        this.ui.toast(`${who}${WEAPONS[w.kind as WeaponKind].name}${w.alive ? '倒了，不能再用' : '被摧毁'}`);
        if (w.side === 0 && this.phase === 'aim') this.renderWeaponTabs();
      },
    });

    const rt = new THREE.WebGLRenderTarget(1, 1, { type: THREE.HalfFloatType, samples: this.mobile ? 0 : 4 });
    this.composer = new EffectComposer(this.renderer, rt);
    this.composer.addPass(new RenderPass(this.scene, this.rig.camera));
    this.bloom = new UnrealBloomPass(new THREE.Vector2(256, 256), 0.5, 0.35, 0.95);
    this.composer.addPass(this.bloom);
    this.composer.addPass(new OutputPass());

    // invisible plane over the player's grid, used to pick columns
    this.pickPlane = new THREE.Mesh(new THREE.PlaneGeometry(GRID, GRID), new THREE.MeshBasicMaterial({ visible: false }));
    this.pickPlane.rotation.x = -Math.PI / 2;
    this.pickPlane.position.set(islandCenterX(0), 0.001, 0);
    this.scene.add(this.pickPlane);

    this.dots = new THREE.InstancedMesh(
      new THREE.SphereGeometry(0.075, 8, 6),
      new THREE.MeshBasicMaterial({ color: 0xffffff, toneMapped: false }),
      32,
    );
    this.dots.visible = false;
    this.dots.frustumCulled = false;
    this.scene.add(this.dots);
    const ringGeo = new THREE.RingGeometry(0.32, 0.5, 32);
    ringGeo.rotateX(-Math.PI / 2);
    this.marker = new THREE.Mesh(
      ringGeo,
      new THREE.MeshBasicMaterial({ color: 0xff4f6a, toneMapped: false, transparent: true, opacity: 0.9, depthWrite: false }),
    );
    this.marker.visible = false;
    this.marker.renderOrder = 5;
    this.scene.add(this.marker);
    const selGeo = new THREE.RingGeometry(0.62, 0.74, 40);
    selGeo.rotateX(-Math.PI / 2);
    this.selRing = new THREE.Mesh(
      selGeo,
      new THREE.MeshBasicMaterial({ color: 0x39d0ff, toneMapped: false, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending }),
    );
    this.selRing.visible = false;
    this.scene.add(this.selRing);

    this.bindInput();
    this.bindUI();
    this.onResize();
    addEventListener('resize', () => this.onResize());

    this.rig.direct(v3(0, 12, 34), v3(0, 0, 0), 1);
    this.rig.snap(v3(4, 16, 40), v3(0, 0, 0));
    this.world.grid.visible = false;
    this.ui.loaded();
    this.ui.showTitle(() => {
      this.sfx.unlock();
      this.startBuild();
    });
    requestAnimationFrame((t) => this.frame(t));
  }

  // ---------------------------------------------------------------- setup

  private syncInset() {
    requestAnimationFrame(() => this.rig.setBottomInset(this.ui.trayInset()));
  }

  private onResize() {
    const w = innerWidth;
    const h = innerHeight;
    this.renderer.setSize(w, h, false);
    this.composer.setSize(w, h);
    this.bloom.resolution.set(w / 2, h / 2);
    this.rig.resize(w, h);
    this.syncInset();
    const pr = this.renderer.getPixelRatio();
    this.fx.setViewportHeight((this.rig.focalPx * pr) / 0.9);
  }

  private bindUI() {
    const ui = this.ui;
    ui.onBuildButtons(
      () => this.autoBuild(),
      () => this.finishBuild(false),
    );
    ui.onAim = (a) => {
      this.aim = a;
      if (this.selected) this.selected.aim = { ...a };
      this.refreshAim();
    };
    ui.onFire(() => this.playerFire());
    ui.onView(() => {
      if (this.phase !== 'aim') return;
      this.freeView = !this.freeView;
      ui.setViewLabel(this.freeView);
      if (this.freeView) this.rig.orbit(v3(islandCenterX(1), 1, 0));
      else this.aimCamera();
    });
    ui.onMute(() => {
      this.sfx.unlock();
      this.sfx.setMuted(!this.sfx.muted);
      return this.sfx.muted;
    });
  }

  private bindInput() {
    const c = this.ui.canvas;
    const setPointer = (e: PointerEvent) => {
      const r = c.getBoundingClientRect();
      this.pointer.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    };
    c.addEventListener('pointerdown', (e) => {
      this.down = { x: e.clientX, y: e.clientY, t: performance.now() };
      setPointer(e);
    });
    c.addEventListener('pointermove', (e) => {
      setPointer(e);
      this.hovering = e.pointerType === 'mouse';
    });
    c.addEventListener('pointerleave', () => (this.hovering = false));
    c.addEventListener('pointerup', (e) => {
      const d = this.down;
      this.down = null;
      if (!d) return;
      if (Math.hypot(e.clientX - d.x, e.clientY - d.y) < 9 && performance.now() - d.t < 500) {
        setPointer(e);
        this.tap();
      }
    });
    addEventListener('keydown', (e) => {
      if (this.phase !== 'aim') return;
      const k = e.key.toLowerCase();
      const map: Record<string, ['yaw' | 'pitch' | 'power', number]> = {
        a: ['yaw', -1],
        d: ['yaw', 1],
        arrowleft: ['yaw', -1],
        arrowright: ['yaw', 1],
        w: ['pitch', 1],
        s: ['pitch', -1],
        arrowup: ['pitch', 1],
        arrowdown: ['pitch', -1],
        q: ['power', -1],
        e: ['power', 1],
      };
      if (map[k]) {
        e.preventDefault();
        this.ui.nudge(map[k][0], map[k][1] * (e.shiftKey ? 0.25 : 1));
      } else if (k === ' ' || k === 'enter') {
        e.preventDefault();
        this.playerFire();
      }
    });
  }

  // ---------------------------------------------------------------- helpers

  private animate(dur: number, delay: number, fn: (k: number) => void, done?: () => void) {
    this.tweens.push({ t: 0, dur, delay, fn, done });
  }

  private after(sec: number, fn: () => void) {
    this.timers.push({ at: this.realTime + sec, fn });
  }

  private screenOf(p: THREE.Vector3) {
    const v = p.clone().project(this.rig.camera);
    const r = this.ui.canvas.getBoundingClientRect();
    return { x: ((v.x + 1) / 2) * r.width + r.left, y: ((1 - v.y) / 2) * r.height + r.top, visible: v.z < 1 };
  }

  // ---------------------------------------------------------------- build phase

  startBuild() {
    this.tweens = [];
    this.timers = [];
    this.arena.reset();
    this.hitStop = this.slowMo = 0;
    this.activeBall = null;
    this.winner = null;
    this.shots = 0;
    this.aiTurn = 0;
    this.selected = null;
    this.marker.visible = false;
    this.dots.visible = false;
    this.rng = mulberry32((Date.now() ^ (Math.random() * 1e9)) >>> 0);
    this.pool = rollPool(this.rng);
    this.plan = new FortPlan(this.pool);
    this.clearBuildMeshes();
    this.tool = 'core';
    this.buildLeft = BUILD_TIME;
    this.lastTick = BUILD_TIME;
    this.confirmNoWeapon = false;
    const dome = this.world.dome;
    dome.visible = true;
    dome.scale.setScalar(1);
    (dome.material as THREE.ShaderMaterial).uniforms.opacity.value = 1;
    this.world.grid.visible = true;
    this.setPhase('build');
    this.ui.setMode('build');
    this.syncInset();
    this.ui.setHP(0, CORE_HP, CORE_HP);
    this.ui.setHP(1, CORE_HP, CORE_HP);
    this.refreshTools();
    this.ui.banner('搭建阶段', `这局的材料：木 ${this.pool.wood} · 石 ${this.pool.stone} · 铁 ${this.pool.iron}`);
    this.ui.toast('先放核心：点一下格子', 2600);
    const cx = islandCenterX(0);
    const portrait = innerWidth / innerHeight < 0.8;
    this.rig.orbit(v3(cx + 1, 0.5, 0), portrait ? v3(cx - 14, 18, 14) : v3(cx - 10, 12, 10));
  }

  private clearBuildMeshes() {
    this.buildRoot.clear();
    this.colMeshes = [];
    for (let k = 0; k < GRID * GRID; k++) this.colMeshes.push([]);
    this.ghost = null;
    this.ghostKind = null;
  }

  private canAfford = (t: Tool) => {
    if (t === 'erase') return true;
    if (t === 'core') return !this.plan.hasCore;
    if (isMat(t)) return this.plan.stock[t] > 0;
    const cost = WEAPONS[t].cost;
    return (Object.keys(cost) as (keyof typeof cost)[]).every((m) => this.plan.stock[m] >= (cost[m] ?? 0));
  };

  private refreshTools() {
    this.ui.renderTools(this.plan.stock, this.plan.hasCore, this.tool, this.canAfford, (t) => {
      this.tool = t;
      this.sfx.click();
      this.refreshTools();
      if (isWeapon(t)) this.ui.toast(`${WEAPONS[t].name}：${WEAPONS[t].blurb}`);
    });
  }

  private makeBuildMesh(kind: PieceKind) {
    if (isMat(kind)) return this.factory.block(kind);
    if (kind === 'core') return this.factory.core(0);
    const m = this.factory.weapon(kind);
    poseWeapon(m, 0, WEAPONS[kind].pitchDefault);
    return m;
  }

  /** The column a screen point refers to; tapping the side of a block targets the neighbor. */
  private pickColumn(): [number, number] | null {
    this.raycaster.setFromCamera(this.pointer, this.rig.camera);
    const hits = this.raycaster.intersectObjects([this.pickPlane, ...this.buildRoot.children], true);
    const hit = hits.find((h) => !this.ghost || !isDescendant(h.object, this.ghost));
    if (!hit) return null;
    if (hit.object === this.pickPlane) return worldToCell(0, hit.point.x, hit.point.z);
    let o: THREE.Object3D | null = hit.object;
    while (o && !o.userData.cell) o = o.parent;
    if (!o) return null;
    const [i, j, level] = o.userData.cell as [number, number, number];
    if (hit.face && this.tool !== 'erase') {
      const n = hit.face.normal.clone().transformDirection(hit.object.matrixWorld);
      if (Math.abs(n.y) < 0.5) {
        const nb = worldToCell(0, hit.point.x + n.x * 0.6, hit.point.z + n.z * 0.6);
        if (nb && (nb[0] !== i || nb[1] !== j) && this.plan.col(nb[0], nb[1]).length <= level) return nb;
      }
    }
    return [i, j];
  }

  private tap() {
    if (this.phase !== 'build') return;
    this.sfx.unlock();
    const cell = this.pickColumn();
    if (!cell) return;
    this.placeAt(cell[0], cell[1]);
  }

  private placeAt(i: number, j: number, quiet = false, delay = 0) {
    const meshes = this.colMeshes[i * GRID + j];
    if (this.tool === 'erase') {
      const k = this.plan.removeTop(i, j);
      if (!k) return this.sfx.deny();
      const m = meshes.pop()!;
      this.fx.dust(m.position, 5);
      this.animate(0.14, 0, (t) => m.scale.setScalar(1 - t), () => this.buildRoot.remove(m));
      this.sfx.remove();
      this.refreshTools();
      return;
    }
    const kind: PieceKind = this.tool;
    const check = this.plan.canPlace(kind, i, j);
    if (!check.ok) {
      if (!quiet) {
        this.ui.toast(check.reason ?? '放不下');
        this.sfx.deny();
      }
      return;
    }
    this.plan.place(kind, i, j);
    this.addBuildMesh(kind, i, j, this.plan.col(i, j).length - 1, delay, quiet);
    if (kind === 'core') {
      this.tool = 'stone';
      this.ui.toast('核心放好了！用方块把它围起来', 2200);
    }
    this.refreshTools();
  }

  private addBuildMesh(kind: PieceKind, i: number, j: number, level: number, delay: number, quiet: boolean) {
    const m = this.makeBuildMesh(kind);
    const pos = cellToWorld(0, i, j, level);
    if (isWeapon(kind)) pos.y -= 0.5 - WEAPON_HALF_H;
    m.position.copy(pos);
    m.userData.cell = [i, j, level];
    m.visible = delay === 0;
    this.buildRoot.add(m);
    this.colMeshes[i * GRID + j].push(m);
    const y0 = pos.y;
    this.animate(
      0.34,
      delay,
      (k) => {
        m.visible = true;
        m.position.y = y0 + (1 - easeOutBounce(k)) * 1.6;
        const sq = k > 0.55 ? Math.sin(((k - 0.55) / 0.45) * Math.PI) * 0.14 : 0;
        m.scale.set(1 + sq, 1 - sq, 1 + sq);
      },
      () => {
        m.scale.setScalar(1);
        this.fx.dust(v3(pos.x, pos.y - 0.45, pos.z), quiet ? 2 : 5);
        if (!quiet || Math.random() < 0.25) this.sfx.place(isMat(kind) ? kind : kind === 'core' ? 'core' : 'weapon');
      },
    );
  }

  autoBuild() {
    if (this.phase !== 'build') return;
    this.sfx.unlock();
    this.plan = buildAiFort(this.pool, this.rng);
    this.clearBuildMeshes();
    let n = 0;
    for (let level = 0; level < 10; level++)
      for (let i = 0; i < GRID; i++)
        for (let j = 0; j < GRID; j++) {
          const kind = this.plan.col(i, j)[level];
          if (!kind) continue;
          this.addBuildMesh(kind, i, j, level, 0.02 + n++ * 0.018, true);
        }
    this.tool = 'stone';
    this.refreshTools();
    this.ui.toast('电脑帮你搭了一座，可以接着改');
  }

  finishBuild(force: boolean) {
    if (this.phase !== 'build') return;
    const weapons = this.plan.count('catapult') + this.plan.count('cannon');
    if (!force && weapons === 0 && !this.confirmNoWeapon) {
      this.confirmNoWeapon = true;
      this.ui.toast('还没放武器，没法还手。再点一次就直接开打', 2600);
      return;
    }
    if (!this.plan.hasCore) {
      this.plan.autoPlaceCore();
      this.ui.toast('核心已经帮你自动放好');
    }
    this.setPhase('reveal');
    this.ui.setMode('none');
    this.syncInset();
    this.ui.enemyTag(0, 0, false);
    this.world.grid.visible = false;
    this.clearBuildMeshes();
    this.arena.reset();
    this.arena.spawnFort(0, this.plan);
    const enemy = this.arena.spawnFort(1, buildAiFort(this.pool, this.rng));
    let landed = 0;
    for (const e of enemy) {
      const y = e.body.position.y;
      e.dropY = 10 + y * 0.8;
      const start = e.dropY;
      this.animate(
        0.6,
        0.55 + y * 0.16 + Math.random() * 0.35,
        (k) => (e.dropY = (1 - easeOutBounce(k)) * start),
        () => {
          if (landed++ % 4 === 0) {
            this.fx.dust(v3(e.body.position.x, e.body.position.y - 0.45, e.body.position.z), 3);
            this.sfx.place('stone');
          }
        },
      );
    }
    const dome = this.world.dome;
    const mat = dome.material as THREE.ShaderMaterial;
    this.animate(
      0.8,
      0.1,
      (k) => {
        mat.uniforms.opacity.value = 1 - k;
        dome.scale.setScalar(1 + easeOutBack(k) * 0.25);
      },
      () => (dome.visible = false),
    );
    this.rig.direct(v3(3, 8.5, 17), v3(islandCenterX(1), 1.8, 0), 1.6);
    this.ui.banner('揭晓！', '看看对手搭了什么');
    this.after(3.6, () => {
      this.ui.setMode('battle');
    this.syncInset();
      this.ui.setHP(0, CORE_HP, CORE_HP);
      this.ui.setHP(1, CORE_HP, CORE_HP);
      this.startTurn(0);
    });
  }

  // ---------------------------------------------------------------- battle

  private setPhase(p: Phase) {
    this.phase = p;
    this.phaseT = 0;
  }

  private startTurn(side: Side): void {
    if (this.arena.gameOver) return;
    this.turnSide = side;
    const usable = this.arena.usableWeapons(side);
    const other: Side = side === 0 ? 1 : 0;
    if (!usable.length) {
      if (!this.arena.usableWeapons(other).length) return this.endByHP('双方都没有能开火的武器了');
      this.ui.banner(side === 0 ? '你没法开火' : '敌方没法开火', '武器全毁，或者炮弹打光了');
      this.setPhase('wait');
      this.after(1.8, () => this.startTurn(other));
      return;
    }
    this.ui.setTurn(side === 0 ? '你的回合' : '敌方回合', `第 ${this.shots + 1} 发`);
    if (side === 0) {
      this.setPhase('aim');
      this.freeView = false;
      this.ui.setViewLabel(false);
      if (!this.selected || !usable.includes(this.selected)) this.selected = usable[0];
      this.selectWeapon(this.selected);
      this.ui.showAim(true);
      this.syncInset();
      this.ui.setFireEnabled(true);
      this.ui.banner('你的回合');
    } else {
      this.setPhase('ai-aim');
      this.ui.showAim(false);
      this.syncInset();
      this.marker.visible = false;
      this.dots.visible = false;
      this.ui.banner('敌方回合');
      const shot = planAiShot(this.arena, 1, this.aiTurn++, this.rng);
      if (!shot) return this.startTurn(0);
      const w = shot.weapon;
      const c = bodyPos(w);
      const flat = aimDirection(1, shot.aim.yaw, 0);
      this.rig.direct(...this.behindWeapon(c, flat, v3(-flat.z, 0, flat.x)), 2.2);
      const from = { ...w.aim };
      this.animate(0.9, 0.9, (k) => {
        const e = easeInOut(k);
        poseWeapon(w.mesh, from.yaw + (shot.aim.yaw - from.yaw) * e, from.pitch + (shot.aim.pitch - from.pitch) * e);
      });
      this.after(2.2, () => this.fire(w, shot.aim));
    }
  }

  private selectWeapon(w: Entity) {
    this.selected = w;
    this.aim = { ...w.aim };
    this.ui.setAim(w.kind as WeaponKind, this.aim);
    this.renderWeaponTabs();
    this.refreshAim();
    this.aimCamera();
  }

  private renderWeaponTabs() {
    const mine = this.arena.entities.filter((e) => e.side === 0 && isWeapon(e.kind));
    const count: Record<string, number> = {};
    const tabs = mine.map((e) => {
      const kind = e.kind as WeaponKind;
      count[kind] = (count[kind] ?? 0) + 1;
      const ammo = this.arena.ammoFor(0, kind);
      const ok = e.alive && !e.disabled && ammo > 0;
      return {
        id: e.id,
        label: `${WEAPONS[kind].name} ${count[kind]}`,
        sub: !e.alive || e.disabled ? '已损坏' : `${WEAPONS[kind].ammoName} ×${ammo}`,
        usable: ok,
      };
    });
    this.ui.renderWeapons(tabs, this.selected?.id ?? null, (id) => {
      const w = mine.find((e) => e.id === id);
      if (w && this.phase === 'aim') {
        this.sfx.click();
        this.selectWeapon(w);
      }
    });
  }

  private refreshAim() {
    const w = this.selected;
    if (!w || this.phase !== 'aim') {
      this.dots.visible = false;
      return;
    }
    const kind = w.kind as WeaponKind;
    poseWeapon(w.mesh, this.aim.yaw, this.aim.pitch);
    const origin = launchOrigin(kind, 0, bodyPos(w), this.aim);
    const vel = aimDirection(0, this.aim.yaw, this.aim.pitch).multiplyScalar(speedFor(kind, this.aim.power));
    const pts = trajectory(origin, vel, kind === 'cannon' ? 8.5 : 7.5, 1 / 120);
    const m = new THREE.Matrix4();
    const n = this.dots.count;
    const stride = Math.max(1, Math.floor(pts.length / n));
    for (let k = 0; k < n; k++) {
      const p = pts[Math.min(pts.length - 1, k * stride)];
      const s = 1 - (k / n) * 0.6;
      m.makeScale(s, s, s).setPosition(p);
      this.dots.setMatrixAt(k, m);
    }
    this.dots.instanceMatrix.needsUpdate = true;
    this.dots.visible = true;
    if (!this.freeView) this.aimCamera();
  }

  private aimCamera() {
    const w = this.selected;
    if (!w || this.freeView) return;
    const c = bodyPos(w);
    const flat = aimDirection(0, this.aim.yaw, 0);
    const side = v3(-flat.z, 0, flat.x);
    this.rig.direct(...this.behindWeapon(c, flat, side), 3.2);
  }

  /** Over-the-shoulder pose: weapon in the lower middle, the enemy fort above it. */
  private behindWeapon(c: THREE.Vector3, flat: THREE.Vector3, side: THREE.Vector3): [THREE.Vector3, THREE.Vector3] {
    const portrait = innerWidth / innerHeight < 0.8;
    const back = portrait ? 9.5 : 8.5;
    const up = portrait ? 3.2 : 2.8;
    return [
      c.clone().addScaledVector(flat, -back).addScaledVector(side, 1.2).add(v3(0, up, 0)),
      c.clone().addScaledVector(flat, 12).add(v3(0, 0.2, 0)),
    ];
  }

  playerFire() {
    if (this.phase !== 'aim' || !this.selected) return;
    if (!this.arena.usableWeapons(0).includes(this.selected)) {
      this.ui.toast('这门武器用不了了，换一个');
      return;
    }
    this.ui.setFireEnabled(false);
    this.ui.showAim(false);
    this.syncInset();
    this.dots.visible = false;
    this.marker.visible = false;
    this.fire(this.selected, this.aim);
  }

  private fire(w: Entity, aim: Aim) {
    if (this.arena.gameOver || !w.alive) return;
    this.shots++;
    const p = this.arena.fire(w, aim);
    this.activeBall = p;
    this.setPhase('flight');
    const ud = w.mesh.userData;
    if (ud.kind === 'catapult') {
      ud.ammo.visible = false;
      this.animate(0.16, 0, (k) => (ud.arm.rotation.z = 0.45 - k * 1.7));
      this.animate(0.9, 0.25, (k) => (ud.arm.rotation.z = -1.25 + easeInOut(k) * 1.7), () => (ud.ammo.visible = true));
      this.rig.shake(0.12);
    } else {
      this.animate(0.5, 0, (k) => (ud.barrel.position.x = -0.05 - Math.sin(Math.min(1, k * 4) * Math.PI * 0.5) * 0.18 * (1 - k)));
      this.rig.shake(0.3);
    }
  }

  private coreHit(core: Entity, amount: number) {
    this.ui.setHP(core.side, core.hp, CORE_HP);
    const s = this.screenOf(bodyPos(core).add(v3(0, 0.8, 0)));
    if (s.visible) this.ui.floater(`-${Math.round(amount)}`, s.x, s.y, core.side === 0 ? '#39d0ff' : '#ff8a9a');
    const crystal = core.mesh.userData.crystal as THREE.Mesh;
    this.animate(0.35, 0, (k) => crystal.scale.set(1 + (1 - k) * 0.5, 1.45 + (1 - k) * 0.6, 1 + (1 - k) * 0.5));
    this.rig.shake(0.25);
  }

  private coreDestroyed(core: Entity) {
    if (this.winner !== null) return;
    this.arena.gameOver = true;
    this.winner = core.side === 0 ? 1 : 0;
    this.ui.setHP(core.side, 0, CORE_HP);
    this.ui.showAim(false);
    this.dots.visible = false;
    this.slowMo = 2.6;
    this.hitStop = 0;
    this.rig.shake(1);
    this.ui.flash();
    const p = bodyPos(core);
    const d = sideDir(core.side);
    this.rig.direct(p.clone().add(v3(d * 5.5, 3.5, 6.5)), p, 2.4);
    this.setPhase('end');
    this.after(3.4, () => this.showEnd(this.winner === 0 ? '敌方核心被打碎了' : '你的核心被打碎了'));
  }

  private endByHP(reason: string) {
    const h0 = this.arena.core(0)?.hp ?? 0;
    const h1 = this.arena.core(1)?.hp ?? 0;
    this.winner = h0 > h1 ? 0 : h1 > h0 ? 1 : 'draw';
    this.arena.gameOver = true;
    this.setPhase('end');
    this.ui.showAim(false);
    this.showEnd(`${reason}，按核心剩余血量判定（${Math.round(h0)} : ${Math.round(h1)}）`);
  }

  private showEnd(reason: string) {
    const result = this.winner === 0 ? 'win' : this.winner === 1 ? 'lose' : 'draw';
    this.sfx.fanfare(result !== 'lose');
    this.ui.showEnd(result, reason, { shots: this.shots, broke: this.arena.broken[1], lost: this.arena.broken[0] }, () => this.startBuild());
  }

  // ---------------------------------------------------------------- frame

  private frame(now: number) {
    requestAnimationFrame((t) => this.frame(t));
    const realDt = Math.min(0.05, (now - this.last) / 1000);
    this.last = now;
    this.realTime += realDt;
    let scale = 1;
    if (this.hitStop > 0) {
      this.hitStop -= realDt;
      scale = 0.06;
    } else if (this.slowMo > 0) {
      this.slowMo -= realDt;
      scale = 0.3;
    }
    const dt = realDt * scale;
    this.time += dt;
    this.phaseT += realDt;

    for (const t of this.timers.splice(0)) {
      if (this.realTime >= t.at) t.fn();
      else this.timers.push(t);
    }
    this.tweens = this.tweens.filter((t) => {
      if (t.delay > 0) {
        t.delay -= realDt;
        return true;
      }
      t.t += realDt;
      const k = Math.min(1, t.t / t.dur);
      t.fn(k);
      if (k >= 1) {
        t.done?.();
        return false;
      }
      return true;
    });

    const battle = this.phase !== 'title' && this.phase !== 'build';
    if (battle) {
      if (this.phase !== 'reveal') this.arena.step(dt);
      this.arena.sync(dt, this.time);
    }
    this.updatePhase(realDt);
    this.updateSelRing();
    this.fx.update(dt);
    this.world.update(this.time);
    this.rig.update(realDt);
    this.composer.render(realDt);
  }

  private updateSelRing() {
    const w = this.selected;
    const show = this.phase === 'aim' && !!w && w.alive;
    this.selRing.visible = show;
    if (!show || !w) return;
    this.selRing.position.set(w.body.position.x, w.body.position.y - WEAPON_HALF_H + 0.03, w.body.position.z);
    const pulse = 1 + Math.sin(this.realTime * 5) * 0.06;
    this.selRing.scale.setScalar(pulse);
    (this.selRing.material as THREE.MeshBasicMaterial).opacity = 0.65 + Math.sin(this.realTime * 5) * 0.25;
  }

  private updatePhase(realDt: number) {
    switch (this.phase) {
      case 'build':
        return this.updateBuild(realDt);
      case 'flight': {
        const p = this.activeBall;
        if (!p) return this.setPhase('settle');
        if (!p.exploded) {
          const pos = p.mesh.position;
          const v = v3(p.body.velocity.x, p.body.velocity.y, p.body.velocity.z);
          const flat = v3(v.x, 0, v.z).normalize();
          const side = v3(-flat.z, 0, flat.x);
          this.rig.direct(
            pos.clone().addScaledVector(flat, -7).addScaledVector(side, 2.5).add(v3(0, 2.6, 0)),
            pos.clone().addScaledVector(v, 0.12),
            5,
          );
          return;
        }
        const impact = p.impact ?? pos3(p);
        if (p.side === 0) {
          this.marker.position.copy(impact).setY(Math.max(impact.y, 0) + 0.04);
          this.marker.visible = true;
        }
        const d = sideDir(p.side);
        this.rig.direct(impact.clone().add(v3(-d * 5.5, 4.8, 7.5)), impact.clone().add(v3(0, 0.4, 0)), 2.2);
        this.setPhase('settle');
        return;
      }
      case 'settle':
        if ((this.phaseT > 1.6 && this.arena.isSettled()) || this.phaseT > 7) {
          if (this.arena.gameOver) return;
          if (this.shots >= MAX_SHOTS) return this.endByHP(`${MAX_SHOTS} 发炮弹打完了`);
          this.setPhase('wait');
          this.startTurn(this.turnSide === 0 ? 1 : 0);
        }
        return;
    }
  }

  private updateBuild(realDt: number) {
    this.buildLeft -= realDt;
    this.ui.setTimer(this.buildLeft);
    const whole = Math.ceil(this.buildLeft);
    if (whole <= 10 && whole < this.lastTick && whole > 0) this.sfx.tick();
    this.lastTick = whole;
    if (this.buildLeft <= 0) {
      this.ui.toast('时间到！');
      this.finishBuild(true);
      return;
    }
    // crystal idle animation for placed cores
    for (const m of this.buildRoot.children) {
      const c = m.userData.crystal as THREE.Mesh | undefined;
      if (c) c.rotation.y = this.time * 1.2;
    }
    // enemy label over the fog dome
    const s = this.screenOf(v3(islandCenterX(1), 6.4, 0));
    this.ui.enemyTag(s.x, s.y, s.visible);
    // hover ghost (mouse only)
    const want = this.hovering && this.tool !== 'erase' ? this.tool : null;
    if (want !== this.ghostKind) {
      if (this.ghost) this.buildRoot.remove(this.ghost);
      this.ghost = want ? this.factory.ghost(want) : null;
      this.ghostKind = want;
      if (this.ghost) this.buildRoot.add(this.ghost);
    }
    if (this.ghost && want) {
      const cell = this.pickColumn();
      if (!cell) {
        this.ghost.visible = false;
      } else {
        const [i, j] = cell;
        const pos = cellToWorld(0, i, j, this.plan.col(i, j).length);
        if (isWeapon(want)) pos.y -= 0.5 - WEAPON_HALF_H;
        this.ghost.position.copy(pos);
        this.ghost.visible = true;
        this.factory.setGhostValid(this.ghost, this.plan.canPlace(want, i, j).ok);
      }
    }
  }

  // ---------------------------------------------------------------- test hooks

  debugInfo() {
    return {
      phase: this.phase,
      turnSide: this.turnSide,
      shots: this.shots,
      hp: [this.arena.core(0)?.hp ?? 0, this.arena.core(1)?.hp ?? 0],
      winner: this.winner,
      stock: this.arena.stock,
      entities: this.arena.entities.filter((e) => e.alive).length,
      broken: this.arena.broken,
    };
  }

  /** Aims the player's weapon with the AI's solver (used by the smoke test). */
  debugAutoAim() {
    if (this.phase !== 'aim') return false;
    const shot = planAiShot(this.arena, 0, 6, this.rng);
    if (!shot) return false;
    this.selectWeapon(shot.weapon);
    this.aim = { ...shot.aim };
    shot.weapon.aim = { ...shot.aim };
    this.ui.setAim(shot.weapon.kind as WeaponKind, this.aim);
    this.refreshAim();
    return true;
  }
}

function isDescendant(o: THREE.Object3D, root: THREE.Object3D) {
  for (let p: THREE.Object3D | null = o; p; p = p.parent) if (p === root) return true;
  return false;
}

function pos3(p: Projectile) {
  return v3(p.body.position.x, p.body.position.y, p.body.position.z);
}

const game = new Game();
(window as unknown as { __siege: unknown }).__siege = {
  info: () => game.debugInfo(),
  autoBuild: () => game.autoBuild(),
  finish: () => game.finishBuild(true),
  autoAim: () => game.debugAutoAim(),
  fire: () => game.playerFire(),
  arrive: () => game.rig.arrive(),
  start: () => {
    document.getElementById('title')!.hidden = true;
    game.startBuild();
  },
};
