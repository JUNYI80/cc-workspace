import * as THREE from 'three';
import { RoundedBoxGeometry } from 'three/examples/jsm/geometries/RoundedBoxGeometry.js';
import { MAT_KINDS, type Mat, type PieceKind, type Side, type WeaponKind } from './config';
import { makeMaterialTextures } from './textures';

export const TEAM_COLOR: Record<Side, number> = { 0: 0x39d0ff, 1: 0xff4f6a };

/** Height of a weapon's physics box; its mesh origin is the box center. */
export const WEAPON_HALF_H = 0.4;

function crackedCopy(src: THREE.Texture, cracks: number, seed: number) {
  const img = src.image as HTMLCanvasElement;
  const c = document.createElement('canvas');
  c.width = img.width;
  c.height = img.height;
  const g = c.getContext('2d')!;
  g.drawImage(img, 0, 0);
  let s = seed;
  const rnd = () => ((s = (s * 16807) % 2147483647) / 2147483647);
  g.strokeStyle = 'rgba(20,14,10,0.85)';
  g.lineCap = 'round';
  for (let k = 0; k < cracks; k++) {
    let x = rnd() * c.width;
    let y = rnd() * c.height;
    let a = rnd() * Math.PI * 2;
    g.lineWidth = 2.5;
    g.beginPath();
    g.moveTo(x, y);
    for (let n = 0; n < 7; n++) {
      a += (rnd() - 0.5) * 1.4;
      x += Math.cos(a) * 12;
      y += Math.sin(a) * 12;
      g.lineTo(x, y);
      g.lineWidth = Math.max(0.8, g.lineWidth - 0.25);
    }
    g.stroke();
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 4;
  return t;
}

export class PieceFactory {
  readonly blockGeo = new RoundedBoxGeometry(0.98, 0.98, 0.98, 2, 0.07);
  /** blockMats[mat][damageLevel] with damage levels 0..2 */
  readonly blockMats = {} as Record<Mat, THREE.MeshStandardMaterial[]>;
  readonly woodMat: THREE.MeshStandardMaterial;
  readonly ironMat: THREE.MeshStandardMaterial;
  readonly darkIronMat = new THREE.MeshStandardMaterial({ color: 0x3a4250, roughness: 0.38, metalness: 0.65 });
  readonly ghostOk = new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.38, depthWrite: false });
  readonly ghostBad = new THREE.MeshBasicMaterial({ color: 0xff5a4a, transparent: true, opacity: 0.38, depthWrite: false });

  constructor() {
    const tex = makeMaterialTextures();
    const props: Record<Mat, { roughness: number; metalness: number }> = {
      wood: { roughness: 0.85, metalness: 0 },
      stone: { roughness: 0.92, metalness: 0 },
      iron: { roughness: 0.42, metalness: 0.55 },
    };
    MAT_KINDS.forEach((m, mi) => {
      const maps = [tex[m], crackedCopy(tex[m], 3, 11 + mi), crackedCopy(tex[m], 8, 97 + mi)];
      this.blockMats[m] = maps.map((map) => new THREE.MeshStandardMaterial({ map, ...props[m] }));
    });
    this.woodMat = this.blockMats.wood[0];
    this.ironMat = this.blockMats.iron[0];
  }

  block(mat: Mat) {
    const m = new THREE.Mesh(this.blockGeo, this.blockMats[mat][0]);
    m.castShadow = m.receiveShadow = true;
    return m;
  }

  setBlockDamage(mesh: THREE.Mesh, mat: Mat, frac: number) {
    const level = frac > 0.66 ? 0 : frac > 0.33 ? 1 : 2;
    mesh.material = this.blockMats[mat][level];
  }

  core(side: Side) {
    const g = new THREE.Group();
    const team = TEAM_COLOR[side];
    const base = new THREE.Mesh(new THREE.CylinderGeometry(0.44, 0.48, 0.22, 16), this.darkIronMat);
    base.position.y = -0.38;
    base.castShadow = base.receiveShadow = true;
    const ring = new THREE.Mesh(
      new THREE.TorusGeometry(0.4, 0.035, 8, 32),
      new THREE.MeshStandardMaterial({ color: team, emissive: team, emissiveIntensity: 1.6 }),
    );
    ring.rotation.x = Math.PI / 2;
    ring.position.y = -0.26;
    const crystal = new THREE.Mesh(
      new THREE.OctahedronGeometry(0.27, 0),
      new THREE.MeshStandardMaterial({
        color: team,
        emissive: team,
        emissiveIntensity: 2.4,
        roughness: 0.15,
        metalness: 0.1,
        flatShading: true,
      }),
    );
    crystal.scale.set(1, 1.45, 1);
    crystal.position.y = 0.04;
    crystal.castShadow = true;
    // corner posts so the core reads as one grid cell
    const postGeo = new THREE.BoxGeometry(0.07, 0.86, 0.07);
    for (const [x, z] of [
      [-0.42, -0.42],
      [0.42, -0.42],
      [-0.42, 0.42],
      [0.42, 0.42],
    ]) {
      const p = new THREE.Mesh(postGeo, this.darkIronMat);
      p.position.set(x, 0.02, z);
      p.castShadow = true;
      g.add(p);
    }
    const light = new THREE.PointLight(team, 4, 5, 2);
    light.position.y = 0.05;
    g.add(base, ring, crystal, light);
    g.userData.crystal = crystal;
    g.userData.light = light;
    return g;
  }

  weapon(kind: WeaponKind) {
    return kind === 'catapult' ? this.catapult() : this.cannon();
  }

  /** Catapult: faces local +x. `turret` yaws, `arm` swings when firing. */
  private catapult() {
    const g = new THREE.Group();
    const turret = new THREE.Group();
    const y0 = -WEAPON_HALF_H;
    const add = (geo: THREE.BufferGeometry, mat: THREE.Material, x: number, y: number, z: number, parent: THREE.Object3D = turret) => {
      const m = new THREE.Mesh(geo, mat);
      m.position.set(x, y, z);
      m.castShadow = m.receiveShadow = true;
      parent.add(m);
      return m;
    };
    add(new THREE.BoxGeometry(0.92, 0.16, 0.78), this.woodMat, 0, y0 + 0.08, 0);
    for (const z of [-0.3, 0.3]) {
      add(new THREE.BoxGeometry(0.12, 0.62, 0.1), this.woodMat, 0.02, y0 + 0.45, z);
    }
    const axle = add(new THREE.CylinderGeometry(0.05, 0.05, 0.72, 8), this.darkIronMat, 0.02, y0 + 0.72, 0);
    axle.rotation.x = Math.PI / 2;
    const arm = new THREE.Group();
    arm.position.set(0.02, y0 + 0.72, 0);
    add(new THREE.BoxGeometry(1.15, 0.08, 0.1), this.woodMat, -0.18, 0, 0, arm);
    const bucketMat = this.woodMat.clone();
    bucketMat.side = THREE.DoubleSide;
    add(new THREE.CylinderGeometry(0.17, 0.12, 0.12, 10, 1, true), bucketMat, -0.72, 0.08, 0, arm);
    add(new THREE.BoxGeometry(0.24, 0.24, 0.24), this.ironMat, 0.36, -0.12, 0, arm);
    const ammo = add(
      new THREE.SphereGeometry(0.13, 10, 8),
      new THREE.MeshStandardMaterial({ color: 0x9a9ca3, roughness: 0.9 }),
      -0.72,
      0.14,
      0,
      arm,
    );
    arm.rotation.z = 0.45;
    turret.add(arm);
    g.add(turret);
    g.userData = { kind: 'catapult', turret, arm, ammo };
    return g;
  }

  /** Cannon: faces local +x. `turret` yaws, `barrel` pitches. */
  private cannon() {
    const g = new THREE.Group();
    const turret = new THREE.Group();
    const y0 = -WEAPON_HALF_H;
    const carriage = new THREE.Mesh(new THREE.BoxGeometry(0.72, 0.24, 0.46), this.woodMat);
    carriage.position.y = y0 + 0.3;
    carriage.castShadow = carriage.receiveShadow = true;
    turret.add(carriage);
    const wheelGeo = new THREE.CylinderGeometry(0.25, 0.25, 0.08, 14);
    for (const z of [-0.29, 0.29]) {
      const w = new THREE.Mesh(wheelGeo, this.darkIronMat);
      w.rotation.x = Math.PI / 2;
      w.position.set(-0.02, y0 + 0.25, z);
      w.castShadow = true;
      turret.add(w);
    }
    const barrel = new THREE.Group();
    barrel.position.set(-0.05, y0 + 0.5, 0);
    const tube = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.17, 1.0, 14), this.darkIronMat);
    tube.rotation.z = -Math.PI / 2;
    tube.position.x = 0.32;
    tube.castShadow = true;
    const lip = new THREE.Mesh(new THREE.TorusGeometry(0.125, 0.035, 8, 16), this.darkIronMat);
    lip.rotation.y = Math.PI / 2;
    lip.position.x = 0.82;
    const knob = new THREE.Mesh(new THREE.SphereGeometry(0.1, 10, 8), this.darkIronMat);
    knob.position.x = -0.22;
    barrel.add(tube, lip, knob);
    turret.add(barrel);
    g.add(turret);
    g.userData = { kind: 'cannon', turret, barrel };
    return g;
  }

  /** Translucent preview of a piece for the build cursor. */
  ghost(kind: PieceKind) {
    let obj: THREE.Object3D;
    if (kind === 'core') obj = this.core(0);
    else if (kind === 'catapult' || kind === 'cannon') obj = this.weapon(kind);
    else obj = new THREE.Mesh(this.blockGeo);
    obj.traverse((o) => {
      if (o instanceof THREE.Mesh) {
        o.material = this.ghostOk;
        o.castShadow = false;
        o.receiveShadow = false;
      }
      if (o instanceof THREE.PointLight) o.visible = false;
    });
    return obj;
  }

  setGhostValid(obj: THREE.Object3D, ok: boolean) {
    obj.traverse((o) => {
      if (o instanceof THREE.Mesh) o.material = ok ? this.ghostOk : this.ghostBad;
    });
  }
}

/** Points a weapon's moving parts at the given yaw (radians, relative to facing) and pitch (degrees). */
export function poseWeapon(obj: THREE.Object3D, yaw: number, pitchDeg: number) {
  const ud = obj.userData;
  ud.turret.rotation.y = -yaw;
  if (ud.kind === 'cannon') ud.barrel.rotation.z = THREE.MathUtils.degToRad(pitchDeg);
}
