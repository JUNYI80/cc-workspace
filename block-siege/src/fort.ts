import * as THREE from 'three';
import {
  GRID,
  MAX_H,
  WEAPONS,
  isMat,
  isWeapon,
  islandCenterX,
  sideDir,
  type PieceKind,
  type Side,
  type Stock,
} from './config';

const HALF = (GRID - 1) / 2;

/** Center of the cell (i, j) at stack level `level`, in world space. i grows toward the enemy. */
export function cellToWorld(side: Side, i: number, j: number, level: number, out = new THREE.Vector3()) {
  const d = sideDir(side);
  return out.set(islandCenterX(side) + d * (i - HALF), level + 0.5, d * (j - HALF));
}

export function worldToCell(side: Side, x: number, z: number): [number, number] | null {
  const d = sideDir(side);
  const i = Math.round((x - islandCenterX(side)) / d + HALF);
  const j = Math.round(z / d + HALF);
  if (i < 0 || j < 0 || i >= GRID || j >= GRID) return null;
  return [i, j];
}

export interface PlaceCheck {
  ok: boolean;
  reason?: string;
}

/** A fort as a grid of column stacks plus the materials left in hand. */
export class FortPlan {
  columns: PieceKind[][] = [];
  stock: Stock;

  constructor(pool: Stock) {
    this.stock = { ...pool };
    for (let k = 0; k < GRID * GRID; k++) this.columns.push([]);
  }

  col(i: number, j: number) {
    return this.columns[i * GRID + j];
  }

  get hasCore() {
    return this.columns.some((c) => c.includes('core'));
  }

  canPlace(kind: PieceKind, i: number, j: number): PlaceCheck {
    const c = this.col(i, j);
    if (c.length >= MAX_H) return { ok: false, reason: '太高了' };
    const top = c[c.length - 1];
    if (top && isWeapon(top)) return { ok: false, reason: '武器上面不能再放东西' };
    if (kind === 'core') {
      if (this.hasCore) return { ok: false, reason: '核心只有一个' };
      return { ok: true };
    }
    if (isMat(kind)) {
      if (this.stock[kind] <= 0) return { ok: false, reason: '材料用完了' };
      return { ok: true };
    }
    const cost = WEAPONS[kind].cost;
    for (const m of Object.keys(cost) as (keyof typeof cost)[]) {
      if (this.stock[m] < (cost[m] ?? 0)) return { ok: false, reason: '材料不够' };
    }
    return { ok: true };
  }

  place(kind: PieceKind, i: number, j: number): boolean {
    if (!this.canPlace(kind, i, j).ok) return false;
    this.spend(kind, -1);
    this.col(i, j).push(kind);
    return true;
  }

  removeTop(i: number, j: number): PieceKind | null {
    const k = this.col(i, j).pop() ?? null;
    if (k) this.spend(k, 1);
    return k;
  }

  /** sign = -1 spends the piece's cost, +1 refunds it. */
  private spend(kind: PieceKind, sign: number) {
    if (isMat(kind)) this.stock[kind] += sign;
    else if (isWeapon(kind)) {
      const cost = WEAPONS[kind].cost;
      for (const m of Object.keys(cost) as (keyof typeof cost)[]) this.stock[m] += sign * (cost[m] ?? 0);
    }
  }

  /** Puts the core on the lowest free column near the middle (used when time runs out). */
  autoPlaceCore() {
    if (this.hasCore) return;
    let best: [number, number] | null = null;
    let bestScore = Infinity;
    for (let i = 0; i < GRID; i++)
      for (let j = 0; j < GRID; j++) {
        if (!this.canPlace('core', i, j).ok) continue;
        const score = this.col(i, j).length * 3 + Math.abs(i - HALF) + Math.abs(j - HALF);
        if (score < bestScore) {
          bestScore = score;
          best = [i, j];
        }
      }
    if (best) this.place('core', best[0], best[1]);
  }

  count(kind: PieceKind) {
    let n = 0;
    for (const c of this.columns) for (const k of c) if (k === kind) n++;
    return n;
  }
}
