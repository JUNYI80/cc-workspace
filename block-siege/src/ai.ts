import * as THREE from 'three';
import type { Arena, Entity } from './arena';
import { type Aim, YAW_LIMIT, launchOrigin, powerFor, solveSpeed, yawToward } from './ballistics';
import { GRID, MAX_H, WEAPONS, type Mat, type PieceKind, type Side, type Stock, type WeaponKind } from './config';
import { FortPlan } from './fort';

/**
 * Builds a fort with the same pool the player got: core in the middle under an iron roof,
 * stone ring around it, a wooden front wall, one catapult and one cannon, and some ammo kept back.
 */
export function buildAiFort(pool: Stock, rng: () => number): FortPlan {
  const plan = new FortPlan(pool);
  const reserve: Stock = { wood: 0, stone: 5 + Math.floor(rng() * 3), iron: 4 + Math.floor(rng() * 2) };
  const left = (m: Mat) => plan.stock[m] - reserve[m];
  const put = (k: PieceKind, i: number, j: number) => {
    if (i < 0 || j < 0 || i >= GRID || j >= GRID) return false;
    if (k === 'wood' || k === 'stone' || k === 'iron') {
      if (left(k) <= 0) return false;
    }
    return plan.place(k, i, j);
  };
  const ci = 2 + Math.floor(rng() * 2); // core a bit behind the middle (front is i = GRID - 1)
  const cj = 2 + Math.floor(rng() * 3);

  // weapons first so they are guaranteed
  const catJ = cj <= 3 ? cj + 2 : cj - 2;
  put('stone', 1, catJ);
  put('stone', 1, catJ);
  put('catapult', 1, catJ);
  const canJ = THREE.MathUtils.clamp(cj + (rng() < 0.5 ? -1 : 1), 0, GRID - 1);
  put('wood', 5, canJ);
  put('wood', 5, canJ);
  put('cannon', 5, canJ);

  put('core', ci, cj);
  put('iron', ci, cj);
  if (rng() < 0.6) put('iron', ci, cj);

  // stone ring around the core
  const ring: [number, number][] = [];
  for (let di = -1; di <= 1; di++)
    for (let dj = -1; dj <= 1; dj++) if (di || dj) ring.push([ci + di, cj + dj]);
  for (let h = 0; h < 3; h++) {
    for (const [i, j] of ring) {
      if (plan.col(i, j).some((k) => k === 'catapult' || k === 'cannon')) continue;
      if (plan.col(i, j).length < h + 1) put(h === 2 ? 'wood' : 'stone', i, j);
    }
  }
  // wooden front wall, two high, leaving the cannon column clear
  for (let h = 0; h < 2; h++)
    for (let j = 0; j < GRID; j++) {
      if (j === canJ) continue;
      if (plan.col(GRID - 1, j).length <= h) put('wood', GRID - 1, j);
    }
  // spend what is left on random thickening near the core
  for (let tries = 0; tries < 200; tries++) {
    const m: Mat = left('stone') > 0 && rng() < 0.6 ? 'stone' : left('wood') > 0 ? 'wood' : left('iron') > 0 ? 'iron' : 'stone';
    if (left(m) <= 0) break;
    const i = THREE.MathUtils.clamp(ci + Math.round((rng() - 0.6) * 4), 0, ci + 1);
    const j = THREE.MathUtils.clamp(cj + Math.round((rng() - 0.5) * 4), 0, GRID - 1);
    const col = plan.col(i, j);
    if (col.length >= MAX_H - 3 || col.some((k) => k === 'catapult' || k === 'cannon')) continue;
    put(m, i, j);
  }
  return plan;
}

export interface AiShot {
  weapon: Entity;
  aim: Aim;
}

/** Picks a weapon and a firing solution; accuracy improves each turn, like a gunner finding the range. */
export function planAiShot(arena: Arena, side: Side, turn: number, rng: () => number): AiShot | null {
  const weapons = arena.usableWeapons(side);
  if (!weapons.length) return null;
  const foe: Side = side === 0 ? 1 : 0;
  const core = arena.core(foe);
  const target = core
    ? new THREE.Vector3(core.body.position.x, core.body.position.y, core.body.position.z)
    : new THREE.Vector3(side === 0 ? 15 : -15, 1, 0);
  // the AI favors the catapult when the core sits under cover
  const weapon = weapons[Math.floor(rng() * weapons.length)];
  const kind = weapon.kind as WeaponKind;
  const def = WEAPONS[kind];
  const center = new THREE.Vector3(weapon.body.position.x, weapon.body.position.y, weapon.body.position.z);
  const spread = Math.max(0.6, 2.6 - turn * 0.45);
  const aimPoint = target.clone().add(new THREE.Vector3((rng() - 0.5) * spread, 0.3 + rng() * 0.5, (rng() - 0.5) * spread));

  const yaw = THREE.MathUtils.clamp(yawToward(side, center, aimPoint), -YAW_LIMIT, YAW_LIMIT);
  const pitches =
    kind === 'catapult'
      ? [48 + rng() * 10, 55, 62, 68]
      : [8 + rng() * 6, 14, 20, 26, 31];
  for (const pitch of pitches) {
    const origin = launchOrigin(kind, side, center, { yaw, pitch, power: 0.5 });
    const v = solveSpeed(origin, aimPoint, pitch);
    if (v === null || v < def.speedMin || v > def.speedMax) continue;
    const noise = 1 + (rng() - 0.5) * Math.max(0.02, 0.09 - turn * 0.015);
    const power = THREE.MathUtils.clamp(powerFor(kind, v * noise), 0, 1);
    return { weapon, aim: { yaw, pitch: THREE.MathUtils.clamp(pitch, def.pitchMin, def.pitchMax), power } };
  }
  return { weapon, aim: { yaw, pitch: def.pitchDefault, power: 0.9 } };
}
