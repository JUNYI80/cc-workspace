export type Mat = 'wood' | 'stone' | 'iron';
export type WeaponKind = 'catapult' | 'cannon';
export type PieceKind = Mat | 'core' | WeaponKind;
export type Side = 0 | 1; // 0 = player, 1 = enemy

export const MAT_KINDS: Mat[] = ['wood', 'stone', 'iron'];
export const WEAPON_KINDS: WeaponKind[] = ['catapult', 'cannon'];

// Fort grid: GRID x GRID columns, each column a stack of pieces.
export const GRID = 7;
export const MAX_H = 8;
export const ISLAND_X = 15; // island centers sit at x = -15 (player) and +15 (enemy)
export const ISLAND_HALF = 4.5; // island top is 9 x 9
export const SEA_Y = -4.2;
export const BUILD_TIME = 120; // seconds
export const GRAVITY = 9.82;
export const PHYS_DT = 1 / 120;

export interface MatDef {
  name: string;
  hp: number;
  mass: number;
  color: number;
}

export const MATS: Record<Mat, MatDef> = {
  wood: { name: '木', hp: 40, mass: 1.2, color: 0xc48a4f },
  stone: { name: '石', hp: 85, mass: 2.6, color: 0x9a9ca3 },
  iron: { name: '铁', hp: 170, mass: 5, color: 0x5d6b7c },
};

export interface WeaponDef {
  name: string;
  cost: Partial<Record<Mat, number>>;
  ammo: Mat;
  ammoName: string;
  hp: number;
  mass: number;
  pitchMin: number;
  pitchMax: number;
  pitchDefault: number;
  speedMin: number;
  speedMax: number;
  ballRadius: number;
  ballMass: number;
  blastRadius: number;
  blastDamage: number;
  blastImpulse: number;
  blurb: string;
}

export const WEAPONS: Record<WeaponKind, WeaponDef> = {
  catapult: {
    name: '投石机',
    cost: { wood: 4, stone: 1 },
    ammo: 'stone',
    ammoName: '石弹',
    hp: 60,
    mass: 3,
    pitchMin: 30,
    pitchMax: 72,
    pitchDefault: 52,
    speedMin: 12,
    speedMax: 26,
    ballRadius: 0.34,
    ballMass: 5,
    blastRadius: 2.4,
    blastDamage: 75,
    blastImpulse: 9,
    blurb: '高抛，能越过城墙砸屋顶',
  },
  cannon: {
    name: '加农炮',
    cost: { iron: 3, wood: 1 },
    ammo: 'iron',
    ammoName: '铁弹',
    hp: 80,
    mass: 4,
    pitchMin: -4,
    pitchMax: 32,
    pitchDefault: 9,
    speedMin: 16,
    speedMax: 36,
    ballRadius: 0.25,
    ballMass: 7,
    blastRadius: 1.6,
    blastDamage: 100,
    blastImpulse: 15,
    blurb: '平射，专门轰墙',
  },
};

export const CORE_HP = 100;
/** Share of blast damage the core takes, so one clean hit can't end the match. */
export const CORE_BLAST_TAKEN = 0.85;
export const CORE_MASS = 6;

export type Stock = Record<Mat, number>;

/** Both sides receive the same random pool, so the match is decided by building, not luck. */
export function rollPool(rng: () => number): Stock {
  const r = (a: number, b: number) => a + Math.floor(rng() * (b - a + 1));
  return { wood: r(22, 30), stone: r(16, 24), iron: r(10, 14) };
}

export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function isMat(k: string): k is Mat {
  return k === 'wood' || k === 'stone' || k === 'iron';
}

export function isWeapon(k: string): k is WeaponKind {
  return k === 'catapult' || k === 'cannon';
}

/** Facing direction along x: the player faces +x, the enemy faces -x. */
export function sideDir(side: Side): number {
  return side === 0 ? 1 : -1;
}

export function islandCenterX(side: Side): number {
  return side === 0 ? -ISLAND_X : ISLAND_X;
}
