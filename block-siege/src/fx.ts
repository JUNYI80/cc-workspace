import * as THREE from 'three';
import { ISLAND_HALF, SEA_Y, islandCenterX } from './config';
import { softDotTexture } from './textures';

interface SpawnOpts {
  pos: THREE.Vector3;
  vel?: THREE.Vector3;
  color: THREE.ColorRepresentation;
  size: number;
  sizeEnd?: number;
  life: number;
  drag?: number;
  gravity?: number;
  alpha?: number;
}

const tmpColor = new THREE.Color();

/** CPU-driven point sprites. One instance per blend mode. */
class Particles {
  readonly points: THREE.Points;
  private readonly max: number;
  private readonly pos: Float32Array;
  private readonly col: Float32Array;
  private readonly size: Float32Array;
  private readonly alpha: Float32Array;
  private readonly vel: Float32Array;
  private readonly life: Float32Array;
  private readonly maxLife: Float32Array;
  private readonly s0: Float32Array;
  private readonly s1: Float32Array;
  private readonly a0: Float32Array;
  private readonly drag: Float32Array;
  private readonly grav: Float32Array;
  private cursor = 0;
  private readonly geo: THREE.BufferGeometry;

  constructor(max: number, additive: boolean, map: THREE.Texture) {
    this.max = max;
    this.pos = new Float32Array(max * 3);
    this.col = new Float32Array(max * 3);
    this.size = new Float32Array(max);
    this.alpha = new Float32Array(max);
    this.vel = new Float32Array(max * 3);
    this.life = new Float32Array(max);
    this.maxLife = new Float32Array(max);
    this.s0 = new Float32Array(max);
    this.s1 = new Float32Array(max);
    this.a0 = new Float32Array(max);
    this.drag = new Float32Array(max);
    this.grav = new Float32Array(max);
    this.geo = new THREE.BufferGeometry();
    this.geo.setAttribute('position', new THREE.BufferAttribute(this.pos, 3).setUsage(THREE.DynamicDrawUsage));
    this.geo.setAttribute('color', new THREE.BufferAttribute(this.col, 3).setUsage(THREE.DynamicDrawUsage));
    this.geo.setAttribute('size', new THREE.BufferAttribute(this.size, 1).setUsage(THREE.DynamicDrawUsage));
    this.geo.setAttribute('alpha', new THREE.BufferAttribute(this.alpha, 1).setUsage(THREE.DynamicDrawUsage));
    const mat = new THREE.ShaderMaterial({
      transparent: true,
      depthWrite: false,
      blending: additive ? THREE.AdditiveBlending : THREE.NormalBlending,
      uniforms: { map: { value: map }, scale: { value: 400 } },
      vertexShader: /* glsl */ `
        attribute float size; attribute float alpha; attribute vec3 color;
        uniform float scale;
        varying vec3 vColor; varying float vAlpha;
        void main() {
          vColor = color; vAlpha = alpha;
          vec4 mv = modelViewMatrix * vec4(position, 1.0);
          gl_PointSize = size * scale / max(-mv.z, 0.1);
          gl_Position = projectionMatrix * mv;
        }`,
      fragmentShader: /* glsl */ `
        uniform sampler2D map;
        varying vec3 vColor; varying float vAlpha;
        void main() {
          vec4 t = texture2D(map, gl_PointCoord);
          if (t.a * vAlpha < 0.01) discard;
          gl_FragColor = vec4(vColor * t.rgb, t.a * vAlpha);
        }`,
    });
    this.points = new THREE.Points(this.geo, mat);
    this.points.frustumCulled = false;
    this.points.renderOrder = additive ? 4 : 3;
  }

  setScale(px: number) {
    (this.points.material as THREE.ShaderMaterial).uniforms.scale.value = px;
  }

  spawn(o: SpawnOpts) {
    const k = this.cursor;
    this.cursor = (this.cursor + 1) % this.max;
    this.pos[k * 3] = o.pos.x;
    this.pos[k * 3 + 1] = o.pos.y;
    this.pos[k * 3 + 2] = o.pos.z;
    this.vel[k * 3] = o.vel?.x ?? 0;
    this.vel[k * 3 + 1] = o.vel?.y ?? 0;
    this.vel[k * 3 + 2] = o.vel?.z ?? 0;
    tmpColor.set(o.color);
    this.col[k * 3] = tmpColor.r;
    this.col[k * 3 + 1] = tmpColor.g;
    this.col[k * 3 + 2] = tmpColor.b;
    this.life[k] = this.maxLife[k] = o.life;
    this.s0[k] = o.size;
    this.s1[k] = o.sizeEnd ?? o.size;
    this.a0[k] = o.alpha ?? 1;
    this.drag[k] = o.drag ?? 0;
    this.grav[k] = o.gravity ?? 0;
  }

  update(dt: number) {
    for (let k = 0; k < this.max; k++) {
      if (this.life[k] <= 0) {
        this.alpha[k] = 0;
        continue;
      }
      this.life[k] -= dt;
      const t = 1 - Math.max(this.life[k], 0) / this.maxLife[k];
      const d = Math.exp(-this.drag[k] * dt);
      this.vel[k * 3] *= d;
      this.vel[k * 3 + 1] = this.vel[k * 3 + 1] * d - this.grav[k] * dt;
      this.vel[k * 3 + 2] *= d;
      this.pos[k * 3] += this.vel[k * 3] * dt;
      this.pos[k * 3 + 1] += this.vel[k * 3 + 1] * dt;
      this.pos[k * 3 + 2] += this.vel[k * 3 + 2] * dt;
      this.size[k] = this.s0[k] + (this.s1[k] - this.s0[k]) * t;
      this.alpha[k] = this.a0[k] * (t < 0.1 ? t / 0.1 : 1 - (t - 0.1) / 0.9);
    }
    for (const n of ['position', 'size', 'alpha', 'color']) this.geo.attributes[n].needsUpdate = true;
  }
}

interface Chunk {
  p: THREE.Vector3;
  v: THREE.Vector3;
  r: THREE.Euler;
  w: THREE.Vector3;
  s: number;
  life: number;
}

/** Small tumbling fragments with their own cheap physics; they never touch the rigid-body world. */
class Debris {
  readonly mesh: THREE.InstancedMesh;
  private readonly chunks: Chunk[] = [];
  private cursor = 0;
  private readonly m = new THREE.Matrix4();
  private readonly q = new THREE.Quaternion();
  private readonly sv = new THREE.Vector3();
  onSplash: (p: THREE.Vector3) => void = () => {};

  constructor(max: number) {
    this.mesh = new THREE.InstancedMesh(
      new THREE.BoxGeometry(1, 1, 1),
      new THREE.MeshStandardMaterial({ roughness: 0.9 }),
      max,
    );
    this.mesh.castShadow = true;
    this.mesh.frustumCulled = false;
    for (let k = 0; k < max; k++) {
      this.chunks.push({ p: new THREE.Vector3(), v: new THREE.Vector3(), r: new THREE.Euler(), w: new THREE.Vector3(), s: 0, life: 0 });
      this.mesh.setColorAt(k, new THREE.Color(1, 1, 1));
      this.mesh.setMatrixAt(k, this.m.makeScale(0, 0, 0));
    }
  }

  spawn(p: THREE.Vector3, v: THREE.Vector3, color: THREE.ColorRepresentation, size: number) {
    const k = this.cursor;
    this.cursor = (this.cursor + 1) % this.chunks.length;
    const c = this.chunks[k];
    c.p.copy(p);
    c.v.copy(v);
    c.r.set(Math.random() * 6, Math.random() * 6, Math.random() * 6);
    c.w.set((Math.random() - 0.5) * 14, (Math.random() - 0.5) * 14, (Math.random() - 0.5) * 14);
    c.s = size;
    c.life = 3 + Math.random() * 2;
    this.mesh.setColorAt(k, tmpColor.set(color));
    if (this.mesh.instanceColor) this.mesh.instanceColor.needsUpdate = true;
  }

  update(dt: number) {
    for (let k = 0; k < this.chunks.length; k++) {
      const c = this.chunks[k];
      if (c.life <= 0) continue;
      c.life -= dt;
      c.v.y -= 9.8 * dt;
      c.p.addScaledVector(c.v, dt);
      c.r.x += c.w.x * dt;
      c.r.y += c.w.y * dt;
      c.r.z += c.w.z * dt;
      const onIsland = [0, 1].some((s) => Math.abs(c.p.x - islandCenterX(s as 0 | 1)) < ISLAND_HALF && Math.abs(c.p.z) < ISLAND_HALF);
      if (onIsland && c.p.y < c.s / 2 && c.p.y > -0.6) {
        c.p.y = c.s / 2;
        c.v.y = Math.abs(c.v.y) * 0.3;
        c.v.x *= 0.6;
        c.v.z *= 0.6;
        c.w.multiplyScalar(0.6);
      } else if (c.p.y < SEA_Y) {
        this.onSplash(c.p);
        c.life = 0;
      }
      const s = c.life <= 0 ? 0 : c.s * Math.min(1, c.life / 0.6);
      this.q.setFromEuler(c.r);
      this.m.compose(c.p, this.q, this.sv.set(s, s, s));
      this.mesh.setMatrixAt(k, this.m);
    }
    this.mesh.instanceMatrix.needsUpdate = true;
  }
}

interface Ring {
  mesh: THREE.Mesh;
  t: number;
  dur: number;
  r0: number;
  r1: number;
}

export class Effects {
  readonly group = new THREE.Group();
  private readonly glow: Particles;
  private readonly smoke: Particles;
  private readonly debris = new Debris(420);
  private readonly rings: Ring[] = [];
  private readonly flash = new THREE.PointLight(0xffc27a, 0, 18, 2);
  private flashT = 0;
  private readonly p = new THREE.Vector3();

  constructor() {
    const tex = softDotTexture();
    this.glow = new Particles(1400, true, tex);
    this.smoke = new Particles(1400, false, tex);
    this.group.add(this.smoke.points, this.glow.points, this.debris.mesh, this.flash);
    this.debris.onSplash = (p) => this.splash(p, 0.4);
    const ringGeo = new THREE.RingGeometry(0.85, 1, 48);
    ringGeo.rotateX(-Math.PI / 2);
    for (let k = 0; k < 8; k++) {
      const mesh = new THREE.Mesh(
        ringGeo,
        new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide }),
      );
      mesh.visible = false;
      this.group.add(mesh);
      this.rings.push({ mesh, t: 0, dur: 1, r0: 0, r1: 1 });
    }
  }

  setViewportHeight(h: number) {
    this.glow.setScale(h * 0.9);
    this.smoke.setScale(h * 0.9);
  }

  private rand(s: number) {
    return new THREE.Vector3((Math.random() - 0.5) * s, (Math.random() - 0.5) * s, (Math.random() - 0.5) * s);
  }

  private dir() {
    // random unit vector biased upward
    const u = Math.random() * 2 - 1;
    const a = Math.random() * Math.PI * 2;
    const r = Math.sqrt(1 - u * u);
    return new THREE.Vector3(r * Math.cos(a), Math.abs(u) * 0.8 + 0.2, r * Math.sin(a));
  }

  explosion(pos: THREE.Vector3, scale: number, tint: THREE.ColorRepresentation = 0xffa040) {
    for (let k = 0; k < 26 * scale; k++) {
      const d = this.dir().multiplyScalar(2 + Math.random() * 6 * scale);
      this.glow.spawn({ pos: this.p.copy(pos).add(this.rand(0.4 * scale)), vel: d.clone(), color: k % 3 ? tint : 0xfff0c0, size: 0.9 * scale, sizeEnd: 0.1, life: 0.35 + Math.random() * 0.35, drag: 4 });
    }
    for (let k = 0; k < 14 * scale; k++) {
      const d = this.dir().multiplyScalar(14 + Math.random() * 10);
      this.glow.spawn({ pos: pos, vel: d.clone(), color: 0xffd27a, size: 0.12, sizeEnd: 0.04, life: 0.6 + Math.random() * 0.5, drag: 1.5, gravity: 14 });
    }
    for (let k = 0; k < 16 * scale; k++) {
      const d = this.dir().multiplyScalar(1 + Math.random() * 2.5);
      this.smoke.spawn({ pos: this.p.copy(pos).add(this.rand(0.8 * scale)), vel: d.clone(), color: Math.random() < 0.5 ? 0x5a5550 : 0x8a847c, size: 1 * scale, sizeEnd: 2.6 * scale, life: 1.4 + Math.random() * 1.2, drag: 1.6, gravity: -0.6, alpha: 0.55 });
    }
    this.ring(pos, 0xffc070, 0.3, 3.2 * scale, 0.45);
    this.flashAt(pos, 30 * scale);
  }

  coreBlast(pos: THREE.Vector3, color: THREE.ColorRepresentation) {
    this.explosion(pos, 2.2, color);
    for (let k = 0; k < 160; k++) {
      const d = this.dir().multiplyScalar(6 + Math.random() * 16);
      this.glow.spawn({ pos, vel: d.clone(), color: k % 2 ? color : 0xffffff, size: 0.35, sizeEnd: 0.05, life: 1 + Math.random() * 1.2, drag: 1.2, gravity: 4 });
    }
    this.ring(pos, color, 0.4, 10, 1.1);
    this.ring(this.p.copy(pos).setY(pos.y + 0.6), 0xffffff, 0.4, 7, 0.8);
    this.flashAt(pos, 90);
  }

  debrisBurst(pos: THREE.Vector3, color: THREE.ColorRepresentation, n: number, power: number, push?: THREE.Vector3) {
    for (let k = 0; k < n; k++) {
      const d = this.dir().multiplyScalar(power * (0.4 + Math.random()));
      if (push) d.addScaledVector(push, 0.6);
      this.debris.spawn(this.p.copy(pos).add(this.rand(0.6)), d.clone(), color, 0.12 + Math.random() * 0.22);
    }
    this.dust(pos, Math.ceil(n / 2), 0xb8ad9c);
  }

  dust(pos: THREE.Vector3, n: number, color: THREE.ColorRepresentation = 0xd8ccb8) {
    for (let k = 0; k < n; k++) {
      const a = Math.random() * Math.PI * 2;
      const vel = new THREE.Vector3(Math.cos(a), 0.25 + Math.random() * 0.4, Math.sin(a)).multiplyScalar(1 + Math.random() * 1.5);
      this.smoke.spawn({ pos: this.p.copy(pos).add(this.rand(0.5)), vel, color, size: 0.45, sizeEnd: 1.3, life: 0.7 + Math.random() * 0.6, drag: 2.5, alpha: 0.6 });
    }
  }

  splash(pos: THREE.Vector3, scale: number) {
    const base = this.p.set(pos.x, SEA_Y + 0.1, pos.z).clone();
    for (let k = 0; k < 18 * scale + 4; k++) {
      const a = Math.random() * Math.PI * 2;
      const vel = new THREE.Vector3(Math.cos(a) * 1.5, 4 + Math.random() * 5 * scale, Math.sin(a) * 1.5);
      this.smoke.spawn({ pos: base, vel, color: 0xf2fbff, size: 0.35 * scale + 0.15, sizeEnd: 0.1, life: 0.9, gravity: 12, alpha: 0.9 });
    }
    this.ring(base, 0xe8fbff, 0.2, 1.6 * scale + 0.6, 0.7);
  }

  muzzle(pos: THREE.Vector3, dir: THREE.Vector3, big: boolean) {
    for (let k = 0; k < (big ? 18 : 8); k++) {
      const vel = dir.clone().multiplyScalar(4 + Math.random() * 6).add(this.rand(2));
      this.glow.spawn({ pos, vel, color: k % 2 ? 0xffb347 : 0xfff3c4, size: 0.5, sizeEnd: 0.05, life: 0.25, drag: 6 });
    }
    for (let k = 0; k < 12; k++) {
      const vel = dir.clone().multiplyScalar(1 + Math.random() * 3).add(this.rand(1.2));
      this.smoke.spawn({ pos, vel, color: 0xb9b3aa, size: 0.5, sizeEnd: 1.6, life: 1.2 + Math.random() * 0.6, drag: 2, gravity: -0.4, alpha: 0.5 });
    }
    if (big) this.flashAt(pos, 14);
  }

  trail(pos: THREE.Vector3, hot: boolean) {
    this.smoke.spawn({ pos, vel: this.rand(0.3).clone(), color: hot ? 0x6b6560 : 0xcfc6b8, size: 0.28, sizeEnd: 0.9, life: 0.9, drag: 1, alpha: 0.45 });
    if (hot) this.glow.spawn({ pos, color: 0xffa040, size: 0.45, sizeEnd: 0.05, life: 0.18 });
  }

  smokePuff(pos: THREE.Vector3) {
    this.smoke.spawn({ pos, vel: new THREE.Vector3((Math.random() - 0.5) * 0.3, 1.2 + Math.random(), (Math.random() - 0.5) * 0.3), color: 0x3c3a38, size: 0.4, sizeEnd: 1.6, life: 2, drag: 0.5, alpha: 0.5 });
  }

  sparkle(pos: THREE.Vector3, color: THREE.ColorRepresentation) {
    const vel = this.rand(0.6).clone();
    vel.y = 0.5 + Math.random() * 0.6;
    this.glow.spawn({ pos: this.p.copy(pos).add(this.rand(0.5)), vel, color, size: 0.12, sizeEnd: 0, life: 0.9, drag: 1 });
  }

  ring(pos: THREE.Vector3, color: THREE.ColorRepresentation, r0: number, r1: number, dur: number) {
    const r = this.rings.find((x) => !x.mesh.visible) ?? this.rings[0];
    r.mesh.visible = true;
    r.mesh.position.copy(pos);
    (r.mesh.material as THREE.MeshBasicMaterial).color.set(color);
    r.t = 0;
    r.dur = dur;
    r.r0 = r0;
    r.r1 = r1;
  }

  flashAt(pos: THREE.Vector3, intensity: number) {
    this.flash.position.copy(pos);
    this.flash.intensity = intensity;
    this.flashT = 1;
  }

  update(dt: number) {
    this.glow.update(dt);
    this.smoke.update(dt);
    this.debris.update(dt);
    for (const r of this.rings) {
      if (!r.mesh.visible) continue;
      r.t += dt / r.dur;
      if (r.t >= 1) {
        r.mesh.visible = false;
        continue;
      }
      const e = 1 - Math.pow(1 - r.t, 3);
      const s = r.r0 + (r.r1 - r.r0) * e;
      r.mesh.scale.set(s, 1, s);
      (r.mesh.material as THREE.MeshBasicMaterial).opacity = (1 - r.t) * 0.9;
    }
    if (this.flashT > 0) {
      this.flashT = Math.max(0, this.flashT - dt * 4);
      this.flash.intensity *= Math.pow(0.02, dt);
      if (this.flashT === 0) this.flash.intensity = 0;
    }
  }
}
