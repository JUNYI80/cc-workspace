import * as THREE from 'three';
import { GRAVITY, WEAPONS, sideDir, type Side, type WeaponKind } from './config';
import { WEAPON_HALF_H } from './pieces';

export interface Aim {
  yaw: number; // radians, 0 = straight at the enemy, positive = toward +z on the player's side
  pitch: number; // degrees
  power: number; // 0..1
}

export const YAW_LIMIT = THREE.MathUtils.degToRad(35);

export function speedFor(kind: WeaponKind, power: number) {
  const w = WEAPONS[kind];
  return w.speedMin + (w.speedMax - w.speedMin) * power;
}

export function powerFor(kind: WeaponKind, speed: number) {
  const w = WEAPONS[kind];
  return (speed - w.speedMin) / (w.speedMax - w.speedMin);
}

export function aimDirection(side: Side, yaw: number, pitchDeg: number, out = new THREE.Vector3()) {
  const d = sideDir(side);
  const p = THREE.MathUtils.degToRad(pitchDeg);
  return out.set(d * Math.cos(yaw) * Math.cos(p), Math.sin(p), d * Math.sin(yaw) * Math.cos(p));
}

/** Where the ball leaves the weapon, given the weapon body's center. */
export function launchOrigin(kind: WeaponKind, side: Side, center: THREE.Vector3, aim: Aim, out = new THREE.Vector3()) {
  const dir = aimDirection(side, aim.yaw, aim.pitch);
  if (kind === 'cannon') {
    return out.copy(center).add(new THREE.Vector3(0, -WEAPON_HALF_H + 0.5, 0)).addScaledVector(dir, 1.0);
  }
  const flat = new THREE.Vector3(dir.x, 0, dir.z).normalize();
  return out.copy(center).add(new THREE.Vector3(0, 1.25, 0)).addScaledVector(flat, 0.15);
}

/** Integrates the same way the physics engine does (semi-implicit Euler). */
export function trajectory(origin: THREE.Vector3, vel: THREE.Vector3, maxHorizontal: number, dt = 1 / 60) {
  const pts: THREE.Vector3[] = [origin.clone()];
  const p = origin.clone();
  const v = vel.clone();
  for (let k = 0; k < 400; k++) {
    v.y -= GRAVITY * dt;
    p.addScaledVector(v, dt);
    pts.push(p.clone());
    const dx = p.x - origin.x;
    const dz = p.z - origin.z;
    if (dx * dx + dz * dz > maxHorizontal * maxHorizontal || p.y < -6) break;
  }
  return pts;
}

/** Launch speed that lands a ball from `o` on `t` at the given pitch, or null if unreachable. */
export function solveSpeed(o: THREE.Vector3, t: THREE.Vector3, pitchDeg: number) {
  const th = THREE.MathUtils.degToRad(pitchDeg);
  const dh = Math.hypot(t.x - o.x, t.z - o.z);
  const dy = t.y - o.y;
  const denom = 2 * Math.cos(th) ** 2 * (dh * Math.tan(th) - dy);
  if (denom <= 0) return null;
  return Math.sqrt((GRAVITY * dh * dh) / denom);
}

/** Yaw (relative to the side's facing) that points from `o` toward `t`. */
export function yawToward(side: Side, o: THREE.Vector3, t: THREE.Vector3) {
  const d = sideDir(side);
  return Math.atan2(d * (t.z - o.z), d * (t.x - o.x));
}
