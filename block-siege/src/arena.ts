import * as CANNON from 'cannon-es';
import * as THREE from 'three';
import { Sfx } from './audio';
import { type Aim, aimDirection, launchOrigin, speedFor } from './ballistics';
import {
  CORE_BLAST_TAKEN,
  CORE_HP,
  CORE_MASS,
  GRID,
  ISLAND_HALF,
  MATS,
  PHYS_DT,
  SEA_Y,
  WEAPONS,
  isMat,
  isWeapon,
  islandCenterX,
  sideDir,
  type Mat,
  type PieceKind,
  type Side,
  type Stock,
  type WeaponKind,
} from './config';
import { FortPlan, cellToWorld } from './fort';
import { Effects } from './fx';
import { PieceFactory, TEAM_COLOR, WEAPON_HALF_H, poseWeapon } from './pieces';

const GROUP_SOLID = 1;
const GROUP_BALL = 2;

export interface Entity {
  id: number;
  side: Side;
  kind: PieceKind;
  mesh: THREE.Object3D;
  body: CANNON.Body;
  hp: number;
  maxHp: number;
  alive: boolean;
  disabled: boolean;
  aim: Aim;
  /** Visual-only offset applied while the reveal animation runs. */
  dropY: number;
}

export interface Projectile {
  body: CANNON.Body;
  mesh: THREE.Mesh;
  kind: WeaponKind;
  side: Side;
  shooter: Entity;
  age: number;
  exploded: boolean;
  done: boolean;
  removeAt: number;
  impact: THREE.Vector3 | null;
}

export interface ArenaEvents {
  onImpact(pos: THREE.Vector3, size: number): void;
  onCoreHit(core: Entity, amount: number): void;
  onCoreDestroyed(core: Entity): void;
  onWeaponLost(w: Entity): void;
}

type Owner = { type: 'entity'; e: Entity } | { type: 'ball'; p: Projectile } | { type: 'ground' };

interface HitEvent {
  a: Owner;
  b: Owner;
  v: number;
}

const tv = new THREE.Vector3();
const tq = new THREE.Quaternion();

export class Arena {
  readonly world: CANNON.World;
  readonly entities: Entity[] = [];
  readonly projectiles: Projectile[] = [];
  readonly stock: Record<Side, Stock> = { 0: { wood: 0, stone: 0, iron: 0 }, 1: { wood: 0, stone: 0, iron: 0 } };
  readonly broken: Record<Side, number> = { 0: 0, 1: 0 };
  private readonly owners = new WeakMap<CANNON.Body, Owner>();
  private readonly hits: HitEvent[] = [];
  private readonly root = new THREE.Group();
  private acc = 0;
  private stepCount = 0;
  private nextId = 1;
  private readonly ballMat = new THREE.MeshStandardMaterial({ color: 0x9a9ca3, roughness: 0.85 });
  private readonly ironBallMat = new THREE.MeshStandardMaterial({ color: 0x2a2f36, roughness: 0.3, metalness: 0.8 });
  private smokeTimer = 0;
  gameOver = false;

  constructor(
    scene: THREE.Scene,
    private readonly factory: PieceFactory,
    private readonly fx: Effects,
    private readonly sfx: Sfx,
    private readonly events: ArenaEvents,
  ) {
    scene.add(this.root);
    this.world = new CANNON.World({ gravity: new CANNON.Vec3(0, -9.82, 0), allowSleep: true });
    this.world.broadphase = new CANNON.SAPBroadphase(this.world);
    (this.world.solver as CANNON.GSSolver).iterations = 14;
    this.world.defaultContactMaterial.friction = 0.65;
    this.world.defaultContactMaterial.restitution = 0.04;
    this.world.defaultContactMaterial.contactEquationRelaxation = 3;
    for (const s of [0, 1] as Side[]) {
      const ground = new CANNON.Body({ mass: 0, collisionFilterGroup: GROUP_SOLID });
      ground.addShape(new CANNON.Box(new CANNON.Vec3(ISLAND_HALF, 0.5, ISLAND_HALF)));
      ground.position.set(islandCenterX(s), -0.5, 0);
      this.world.addBody(ground);
      this.owners.set(ground, { type: 'ground' });
    }
  }

  reset() {
    for (const e of this.entities) this.removeEntity(e);
    for (const p of this.projectiles) this.removeBall(p);
    this.entities.length = 0;
    this.projectiles.length = 0;
    this.hits.length = 0;
    this.broken[0] = this.broken[1] = 0;
    this.gameOver = false;
    this.acc = 0;
  }

  /** Turns a finished plan into meshes and sleeping rigid bodies. */
  spawnFort(side: Side, plan: FortPlan) {
    this.stock[side] = { ...plan.stock };
    const created: Entity[] = [];
    for (let i = 0; i < GRID; i++)
      for (let j = 0; j < GRID; j++) {
        plan.col(i, j).forEach((kind, level) => {
          created.push(this.spawnPiece(side, kind, cellToWorld(side, i, j, level)));
        });
      }
    return created;
  }

  private spawnPiece(side: Side, kind: PieceKind, cellCenter: THREE.Vector3): Entity {
    let mesh: THREE.Object3D;
    let shape: CANNON.Shape;
    let mass: number;
    let hp: number;
    const pos = cellCenter.clone();
    if (isMat(kind)) {
      mesh = this.factory.block(kind);
      shape = new CANNON.Box(new CANNON.Vec3(0.5, 0.5, 0.5));
      mass = MATS[kind].mass;
      hp = MATS[kind].hp;
    } else if (kind === 'core') {
      mesh = this.factory.core(side);
      shape = new CANNON.Box(new CANNON.Vec3(0.47, 0.5, 0.47));
      mass = CORE_MASS;
      hp = CORE_HP;
    } else {
      mesh = this.factory.weapon(kind);
      shape = new CANNON.Box(new CANNON.Vec3(0.46, WEAPON_HALF_H, 0.46));
      mass = WEAPONS[kind].mass;
      hp = WEAPONS[kind].hp;
      pos.y -= 0.5 - WEAPON_HALF_H;
    }
    if (side === 1) mesh.rotation.y = Math.PI;
    mesh.position.copy(pos);
    this.root.add(mesh);
    const body = new CANNON.Body({
      mass,
      position: new CANNON.Vec3(pos.x, pos.y, pos.z),
      collisionFilterGroup: GROUP_SOLID,
      collisionFilterMask: GROUP_SOLID | GROUP_BALL,
      allowSleep: true,
      sleepSpeedLimit: 0.22,
      sleepTimeLimit: 0.45,
      angularDamping: 0.12,
      linearDamping: 0.02,
    });
    body.addShape(shape);
    if (side === 1) body.quaternion.setFromEuler(0, Math.PI, 0);
    this.world.addBody(body);
    body.sleep();
    const e: Entity = {
      id: this.nextId++,
      side,
      kind,
      mesh,
      body,
      hp,
      maxHp: hp,
      alive: true,
      disabled: false,
      aim: { yaw: 0, pitch: isWeapon(kind) ? WEAPONS[kind].pitchDefault : 0, power: 0.55 },
      dropY: 0,
    };
    if (isWeapon(kind)) poseWeapon(mesh, 0, e.aim.pitch);
    this.owners.set(body, { type: 'entity', e });
    body.addEventListener('collide', (ev: { body: CANNON.Body; contact: CANNON.ContactEquation }) => {
      const other = this.owners.get(ev.body);
      if (!other) return;
      this.hits.push({ a: { type: 'entity', e }, b: other, v: Math.abs(ev.contact.getImpactVelocityAlongNormal()) });
    });
    this.entities.push(e);
    return e;
  }

  core(side: Side) {
    return this.entities.find((e) => e.side === side && e.kind === 'core' && e.alive);
  }

  /** Weapons that can still shoot (standing, intact, and with ammo left). */
  usableWeapons(side: Side) {
    return this.entities.filter(
      (e) => e.side === side && e.alive && !e.disabled && isWeapon(e.kind) && this.stock[side][WEAPONS[e.kind as WeaponKind].ammo] > 0,
    );
  }

  fire(w: Entity, aim: Aim) {
    const kind = w.kind as WeaponKind;
    const def = WEAPONS[kind];
    this.stock[w.side][def.ammo] -= 1;
    w.aim = { ...aim };
    const center = tv.set(w.body.position.x, w.body.position.y, w.body.position.z);
    const origin = launchOrigin(kind, w.side, center, aim);
    const vel = aimDirection(w.side, aim.yaw, aim.pitch).multiplyScalar(speedFor(kind, aim.power));
    const body = new CANNON.Body({
      mass: def.ballMass,
      position: new CANNON.Vec3(origin.x, origin.y, origin.z),
      velocity: new CANNON.Vec3(vel.x, vel.y, vel.z),
      linearDamping: 0,
      angularDamping: 0.2,
      allowSleep: false,
      collisionFilterGroup: GROUP_BALL,
      collisionFilterMask: GROUP_SOLID,
    });
    body.addShape(new CANNON.Sphere(def.ballRadius));
    this.world.addBody(body);
    const mesh = new THREE.Mesh(new THREE.SphereGeometry(def.ballRadius, 14, 10), kind === 'cannon' ? this.ironBallMat : this.ballMat);
    mesh.castShadow = true;
    mesh.position.copy(origin);
    this.root.add(mesh);
    const p: Projectile = { body, mesh, kind, side: w.side, shooter: w, age: 0, exploded: false, done: false, removeAt: Infinity, impact: null };
    this.owners.set(body, { type: 'ball', p });
    body.addEventListener('collide', (ev: { body: CANNON.Body; contact: CANNON.ContactEquation }) => {
      const other = this.owners.get(ev.body);
      if (!other) return;
      this.hits.push({ a: { type: 'ball', p }, b: other, v: Math.abs(ev.contact.getImpactVelocityAlongNormal()) });
    });
    this.projectiles.push(p);
    const dir = aimDirection(w.side, aim.yaw, aim.pitch);
    this.fx.muzzle(origin, dir, kind === 'cannon');
    this.sfx.fire(kind === 'cannon');
    w.body.wakeUp();
    // recoil
    w.body.applyImpulse(new CANNON.Vec3(-dir.x * 2, 0, -dir.z * 2));
    return p;
  }

  step(dt: number) {
    this.acc += dt;
    let n = 0;
    while (this.acc >= PHYS_DT && n < 8) {
      this.world.step(PHYS_DT);
      this.acc -= PHYS_DT;
      n++;
      this.stepCount++;
      this.processHits();
      this.updateBalls(PHYS_DT);
      if (this.stepCount % 6 === 0) this.propagateWake();
    }
    if (n === 8) this.acc = 0;
  }

  private processHits() {
    for (const h of this.hits.splice(0)) {
      if (h.a.type === 'ball') this.ballHit(h.a.p, h.b);
      else if (h.a.type === 'entity' && h.b.type !== 'ball') this.impactDamage(h.a.e, h.b, h.v);
    }
  }

  private ballHit(p: Projectile, other: Owner) {
    if (p.exploded) return;
    if (other.type === 'entity' && other.e === p.shooter && p.age < 0.3) return;
    p.exploded = true;
    const def = WEAPONS[p.kind];
    const pos = new THREE.Vector3(p.body.position.x, p.body.position.y, p.body.position.z);
    p.impact = pos.clone();
    if (p.kind === 'catapult') {
      // the stone ball shatters
      this.fx.debrisBurst(pos, 0x9a9ca3, 10, 5);
      p.removeAt = p.age;
    } else {
      p.removeAt = p.age + 2.5;
    }
    this.explode(pos, def.blastRadius, def.blastDamage, def.blastImpulse);
    this.events.onImpact(pos, p.kind === 'cannon' ? 1.1 : 1);
  }

  private impactDamage(e: Entity, other: Owner, v: number) {
    const core = e.kind === 'core';
    const threshold = core ? 5 : 4;
    if (!e.alive || v < threshold) return;
    let factor = 1;
    if (other.type === 'entity') factor = THREE.MathUtils.clamp(other.e.body.mass / e.body.mass, 0.5, 2);
    // the core is sturdier against knocks than against direct blasts
    this.damage(e, (v - threshold) * 5 * factor * (core ? 0.4 : 1));
  }

  explode(pos: THREE.Vector3, radius: number, dmg: number, impulse: number) {
    this.fx.explosion(pos, radius / 2);
    this.sfx.boom(radius / 2);
    const from = new CANNON.Vec3(pos.x, pos.y, pos.z);
    for (const e of this.entities.slice()) {
      if (!e.alive) continue;
      const c = e.body.position;
      const dx = c.x - pos.x;
      const dy = c.y - pos.y;
      const dz = c.z - pos.z;
      const dist = Math.sqrt(dx * dx + dy * dy + dz * dz);
      const d = Math.max(0, dist - 0.5);
      if (d > radius) continue;
      const f = 1 - d / radius;
      let amount = dmg * f;
      if ((e.kind === 'core' || isWeapon(e.kind)) && this.occluded(from, e)) amount *= 0.45;
      if (e.kind === 'core') amount *= CORE_BLAST_TAKEN;
      const dir = new CANNON.Vec3(dx, dy, dz);
      if (dist < 1e-3) dir.set(0, 1, 0);
      dir.normalize();
      dir.y += 0.35;
      dir.normalize();
      const j = impulse * f * (0.7 + Math.random() * 0.5) * (e.kind === 'core' ? 0.5 : 1);
      e.body.wakeUp();
      e.body.applyImpulse(dir.scale(j), new CANNON.Vec3((Math.random() - 0.5) * 0.5, (Math.random() - 0.5) * 0.5, (Math.random() - 0.5) * 0.5));
      this.damage(e, amount);
    }
    this.wakeAround(pos, radius + 1.5);
  }

  private occluded(from: CANNON.Vec3, target: Entity) {
    const result = new CANNON.RaycastResult();
    this.world.raycastClosest(from, target.body.position, { collisionFilterMask: GROUP_SOLID, skipBackfaces: true }, result);
    return result.hasHit && result.body !== target.body && result.distance > 0.3;
  }

  damage(e: Entity, amount: number) {
    if (!e.alive || amount < 0.5 || this.gameOver) return;
    e.hp -= amount;
    if (isMat(e.kind)) this.factory.setBlockDamage(e.mesh as THREE.Mesh, e.kind, e.hp / e.maxHp);
    if (e.kind === 'core') this.events.onCoreHit(e, Math.min(amount, e.hp + amount));
    if (e.hp <= 0) this.destroy(e, true);
  }

  destroy(e: Entity, burst: boolean) {
    if (!e.alive) return;
    e.alive = false;
    const pos = new THREE.Vector3(e.body.position.x, e.body.position.y, e.body.position.z);
    this.removeEntity(e);
    if (isMat(e.kind)) {
      this.broken[e.side]++;
      if (burst) {
        this.fx.debrisBurst(pos, MATS[e.kind].color, 9, 4);
        this.sfx.crunch(e.kind);
      }
    } else if (isWeapon(e.kind)) {
      if (burst) {
        this.fx.debrisBurst(pos, 0xc48a4f, 12, 5);
        this.fx.explosion(pos, 0.6);
        this.sfx.crunch('wood');
      }
      if (!e.disabled) this.events.onWeaponLost(e);
    } else if (e.kind === 'core') {
      this.fx.coreBlast(pos, TEAM_COLOR[e.side]);
      this.sfx.coreBreak();
      this.events.onCoreDestroyed(e);
    }
    this.wakeAround(pos, 1.9);
  }

  private removeEntity(e: Entity) {
    this.world.removeBody(e.body);
    this.root.remove(e.mesh);
  }

  private removeBall(p: Projectile) {
    this.world.removeBody(p.body);
    this.root.remove(p.mesh);
    p.mesh.geometry.dispose();
  }

  private wakeAround(pos: THREE.Vector3, r: number) {
    for (const e of this.entities) {
      if (!e.alive) continue;
      const c = e.body.position;
      if ((c.x - pos.x) ** 2 + (c.y - pos.y) ** 2 + (c.z - pos.z) ** 2 < r * r) e.body.wakeUp();
    }
  }

  /** Bodies stacked on something that moved must not stay asleep in mid-air. */
  private propagateWake() {
    const movers = this.entities.filter((e) => e.alive && e.body.sleepState !== CANNON.Body.SLEEPING && e.body.velocity.length() > 0.5);
    if (!movers.length) return;
    for (const e of this.entities) {
      if (!e.alive || e.body.sleepState !== CANNON.Body.SLEEPING) continue;
      for (const m of movers) {
        if (e.body.position.distanceTo(m.body.position) < 1.6) {
          e.body.wakeUp();
          break;
        }
      }
    }
  }

  private updateBalls(dt: number) {
    for (const p of this.projectiles) {
      if (p.done) continue;
      p.age += dt;
      const y = p.body.position.y;
      if (!p.exploded && y < SEA_Y) {
        p.exploded = true;
        p.impact = new THREE.Vector3(p.body.position.x, SEA_Y, p.body.position.z);
        this.fx.splash(p.impact, 1.4);
        this.sfx.splash();
        p.removeAt = p.age;
      }
      if (p.age > 9 && !p.exploded) {
        p.exploded = true;
        p.impact = new THREE.Vector3(p.body.position.x, p.body.position.y, p.body.position.z);
        p.removeAt = p.age;
      }
      if (p.age >= p.removeAt) {
        p.done = true;
        this.removeBall(p);
      }
    }
  }

  /** Everything has stopped moving and every ball has landed. */
  isSettled() {
    if (this.projectiles.some((p) => !p.exploded)) return false;
    for (const e of this.entities) {
      if (!e.alive || e.body.sleepState === CANNON.Body.SLEEPING) continue;
      if (e.body.velocity.length() > 0.3 || e.body.angularVelocity.length() > 0.5) return false;
    }
    return true;
  }

  /** Copies physics state onto meshes and checks for fallen or toppled pieces. */
  sync(dt: number, time: number) {
    this.smokeTimer += dt;
    const puff = this.smokeTimer > 0.12;
    if (puff) this.smokeTimer = 0;
    for (const e of this.entities) {
      if (!e.alive) continue;
      const b = e.body;
      e.mesh.position.set(b.position.x, b.position.y + e.dropY, b.position.z);
      e.mesh.quaternion.set(b.quaternion.x, b.quaternion.y, b.quaternion.z, b.quaternion.w);
      if (b.position.y < SEA_Y) {
        this.fx.splash(tv.set(b.position.x, SEA_Y, b.position.z), 0.8);
        this.sfx.splash();
        this.destroy(e, false);
        continue;
      }
      if (e.kind === 'core') {
        const crystal = e.mesh.userData.crystal as THREE.Mesh;
        crystal.rotation.y = time * 1.2;
        crystal.position.y = 0.04 + Math.sin(time * 2.2) * 0.05;
        if (Math.random() < dt * 4) this.fx.sparkle(e.mesh.getWorldPosition(tv), TEAM_COLOR[e.side]);
        if (!this.gameOver && this.fellOff(e)) this.destroy(e, true);
      } else if (isWeapon(e.kind) && !e.disabled) {
        const up = tv.set(0, 1, 0).applyQuaternion(tq.set(b.quaternion.x, b.quaternion.y, b.quaternion.z, b.quaternion.w));
        if (up.y < 0.75 || this.fellOff(e)) {
          e.disabled = true;
          e.mesh.traverse((o) => {
            if (o instanceof THREE.Mesh) {
              o.material = (o.material as THREE.MeshStandardMaterial).clone();
              (o.material as THREE.MeshStandardMaterial).color.multiplyScalar(0.45);
            }
          });
          this.events.onWeaponLost(e);
        }
      }
      if (e.disabled && puff) this.fx.smokePuff(tv.set(b.position.x, b.position.y + 0.5, b.position.z));
    }
    for (const p of this.projectiles) {
      if (p.done) continue;
      p.mesh.position.set(p.body.position.x, p.body.position.y, p.body.position.z);
      if (!p.exploded) this.fx.trail(p.mesh.position, p.kind === 'cannon');
    }
  }

  private fellOff(e: Entity) {
    const c = e.body.position;
    return c.y < -0.6 || Math.abs(c.x - islandCenterX(e.side)) > ISLAND_HALF + 0.3 || Math.abs(c.z) > ISLAND_HALF + 0.3;
  }

  /** Remaining ammo (shots) for one weapon kind. */
  ammoFor(side: Side, kind: WeaponKind) {
    return this.stock[side][WEAPONS[kind].ammo as Mat];
  }

  facing(side: Side) {
    return sideDir(side);
  }
}
