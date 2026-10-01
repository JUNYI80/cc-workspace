import * as THREE from 'three';
import { GRID, ISLAND_HALF, SEA_Y, islandCenterX, type Side } from './config';
import { TEAM_COLOR } from './pieces';
import { grassTexture } from './textures';

const HORIZON = new THREE.Color(0xf3cfa6);

export interface WorldScene {
  scene: THREE.Scene;
  sun: THREE.DirectionalLight;
  grid: THREE.LineSegments;
  dome: THREE.Mesh;
  flags: THREE.Mesh[];
  update(t: number): void;
}

function skyDome() {
  const geo = new THREE.SphereGeometry(400, 32, 16);
  const mat = new THREE.ShaderMaterial({
    side: THREE.BackSide,
    depthWrite: false,
    uniforms: {
      top: { value: new THREE.Color(0x3f78c9) },
      mid: { value: new THREE.Color(0x9cc3e8) },
      horizon: { value: HORIZON },
      sunDir: { value: new THREE.Vector3(-0.5, 0.35, -0.8).normalize() },
    },
    vertexShader: /* glsl */ `
      varying vec3 vDir;
      void main() {
        vDir = normalize(position);
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }`,
    fragmentShader: /* glsl */ `
      uniform vec3 top; uniform vec3 mid; uniform vec3 horizon; uniform vec3 sunDir;
      varying vec3 vDir;
      void main() {
        float h = vDir.y;
        vec3 c = mix(horizon, mid, smoothstep(0.0, 0.22, h));
        c = mix(c, top, smoothstep(0.2, 0.75, h));
        float s = max(dot(normalize(vDir), sunDir), 0.0);
        c += vec3(1.0, 0.85, 0.6) * (pow(s, 600.0) * 2.0 + pow(s, 12.0) * 0.25);
        if (h < 0.0) c = horizon;
        gl_FragColor = vec4(c, 1.0);
      }`,
  });
  const m = new THREE.Mesh(geo, mat);
  m.renderOrder = -1;
  return m;
}

function sea() {
  const geo = new THREE.PlaneGeometry(500, 500, 120, 120);
  geo.rotateX(-Math.PI / 2);
  const mat = new THREE.ShaderMaterial({
    fog: true,
    uniforms: THREE.UniformsUtils.merge([
      THREE.UniformsLib.fog,
      {
        time: { value: 0 },
        deep: { value: new THREE.Color(0x1d6f86) },
        shallow: { value: new THREE.Color(0x4fb7bf) },
        foam: { value: new THREE.Color(0xeafcff) },
        islands: { value: new THREE.Vector2(islandCenterX(0), islandCenterX(1)) },
      },
    ]),
    vertexShader: /* glsl */ `
      uniform float time;
      varying vec3 vWorld;
      varying float vWave;
      #include <fog_pars_vertex>
      void main() {
        vec3 p = position;
        float w = sin(p.x * 0.18 + time * 1.1) * 0.18 + sin(p.z * 0.23 + time * 0.9) * 0.14
                + sin((p.x + p.z) * 0.41 + time * 1.7) * 0.06;
        p.y += w;
        vWave = w;
        vec4 wp = modelMatrix * vec4(p, 1.0);
        vWorld = wp.xyz;
        vec4 mvPosition = viewMatrix * wp;
        gl_Position = projectionMatrix * mvPosition;
        #include <fog_vertex>
      }`,
    fragmentShader: /* glsl */ `
      uniform float time; uniform vec3 deep; uniform vec3 shallow; uniform vec3 foam; uniform vec2 islands;
      varying vec3 vWorld;
      varying float vWave;
      #include <fog_pars_fragment>
      float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
      float vnoise(vec2 p) {
        vec2 i = floor(p); vec2 f = fract(p); f = f * f * (3.0 - 2.0 * f);
        return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), f.x), mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), f.x), f.y);
      }
      void main() {
        float d1 = length(vWorld.xz - vec2(islands.x, 0.0));
        float d2 = length(vWorld.xz - vec2(islands.y, 0.0));
        float d = min(d1, d2);
        vec3 c = mix(shallow, deep, smoothstep(4.0, 20.0, d));
        c = mix(c, deep * 0.8, vnoise(vWorld.xz * 0.04 + time * 0.02) * 0.35);
        c += vec3(0.06, 0.08, 0.08) * vWave * 2.0;
        float ring = smoothstep(0.8, 0.0, abs(d - 5.6 - sin(time * 1.3 + vWorld.x) * 0.25));
        c = mix(c, foam, ring * 0.5);
        float n = vnoise(vWorld.xz * 0.8 + vec2(time * 0.4, time * 0.25)) * vnoise(vWorld.xz * 1.6 - vec2(time * 0.3, -time * 0.5));
        float fade = smoothstep(90.0, 15.0, length(vWorld.xz - cameraPosition.xz));
        c += smoothstep(0.5, 0.78, n) * 0.22 * fade;
        gl_FragColor = vec4(c, 1.0);
        #include <fog_fragment>
      }`,
  });
  const m = new THREE.Mesh(geo, mat);
  m.position.y = SEA_Y;
  return m;
}

function island(side: Side, grass: THREE.Texture) {
  const g = new THREE.Group();
  const cx = islandCenterX(side);
  const top = new THREE.Mesh(
    new THREE.BoxGeometry(ISLAND_HALF * 2, 0.5, ISLAND_HALF * 2),
    [
      new THREE.MeshStandardMaterial({ color: 0x8a6a4a, roughness: 1 }),
      new THREE.MeshStandardMaterial({ color: 0x8a6a4a, roughness: 1 }),
      new THREE.MeshStandardMaterial({ map: grass, roughness: 1 }),
      new THREE.MeshStandardMaterial({ color: 0x6d5038, roughness: 1 }),
      new THREE.MeshStandardMaterial({ color: 0x8a6a4a, roughness: 1 }),
      new THREE.MeshStandardMaterial({ color: 0x8a6a4a, roughness: 1 }),
    ],
  );
  top.position.set(cx, -0.25, 0);
  top.receiveShadow = true;
  g.add(top);
  // jagged rock pillar under the island, rising out of the sea
  const rockGeo = new THREE.CylinderGeometry(4.7, 2.6, 9, 9, 4);
  const pos = rockGeo.attributes.position;
  const seed = side === 0 ? 1.7 : 4.1;
  for (let k = 0; k < pos.count; k++) {
    const y = pos.getY(k);
    if (y > 4.4) continue;
    const a = Math.atan2(pos.getZ(k), pos.getX(k));
    const n = 1 + 0.18 * Math.sin(a * 3 + seed + y) + 0.1 * Math.sin(a * 7 - y * 2 + seed);
    pos.setX(k, pos.getX(k) * n);
    pos.setZ(k, pos.getZ(k) * n);
  }
  rockGeo.computeVertexNormals();
  const rock = new THREE.Mesh(rockGeo, new THREE.MeshStandardMaterial({ color: 0x9a8170, roughness: 1, flatShading: true }));
  rock.position.set(cx, -5, 0);
  rock.rotation.y = seed;
  rock.receiveShadow = true;
  g.add(rock);
  return g;
}

function flag(side: Side) {
  const g = new THREE.Group();
  const pole = new THREE.Mesh(
    new THREE.CylinderGeometry(0.04, 0.05, 3.2, 6),
    new THREE.MeshStandardMaterial({ color: 0x3a2a1c }),
  );
  pole.position.y = 1.6;
  pole.castShadow = true;
  const clothGeo = new THREE.PlaneGeometry(1.1, 0.65, 10, 4);
  clothGeo.translate(0.55, 0, 0);
  const cloth = new THREE.Mesh(
    clothGeo,
    new THREE.MeshStandardMaterial({ color: TEAM_COLOR[side], side: THREE.DoubleSide, roughness: 0.8 }),
  );
  cloth.position.y = 2.8;
  cloth.castShadow = true;
  cloth.userData.base = clothGeo.attributes.position.array.slice();
  g.add(pole, cloth);
  const cx = islandCenterX(side);
  g.position.set(cx + Math.sign(cx) * (ISLAND_HALF - 0.25), 0, side === 0 ? -ISLAND_HALF + 0.25 : ISLAND_HALF - 0.25);
  return { group: g, cloth };
}

function cloud(rng: () => number) {
  const g = new THREE.Group();
  const mat = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 1, flatShading: true, emissive: 0xfff1e4, emissiveIntensity: 0.6 });
  const n = 4 + Math.floor(rng() * 4);
  for (let k = 0; k < n; k++) {
    const s = new THREE.Mesh(new THREE.IcosahedronGeometry(1.2 + rng() * 1.6, 0), mat);
    s.position.set(k * 1.6 - n * 0.8, rng() * 0.8, (rng() - 0.5) * 1.6);
    s.scale.y = 0.65;
    g.add(s);
  }
  return g;
}

function buildGrid(side: Side) {
  const pts: number[] = [];
  const cx = islandCenterX(side);
  const h = GRID / 2;
  for (let k = 0; k <= GRID; k++) {
    const o = k - h;
    pts.push(cx - h, 0.02, o, cx + h, 0.02, o);
    pts.push(cx + o, 0.02, -h, cx + o, 0.02, h);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
  return new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.35 }));
}

/** Soft glowing dome that hides the enemy fort while it is being built. */
function fogDome() {
  const mat = new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    uniforms: { time: { value: 0 }, opacity: { value: 1 } },
    vertexShader: /* glsl */ `
      varying vec3 vN; varying vec3 vP; varying vec3 vView;
      void main() {
        vN = normalize(normalMatrix * normal);
        vP = position;
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        vView = normalize(-mv.xyz);
        gl_Position = projectionMatrix * mv;
      }`,
    fragmentShader: /* glsl */ `
      uniform float time; uniform float opacity;
      varying vec3 vN; varying vec3 vP; varying vec3 vView;
      void main() {
        float rim = 1.0 - abs(dot(vN, vView));
        float bands = 0.5 + 0.5 * sin(vP.y * 3.0 - time * 2.0 + sin(vP.x * 0.8 + time) * 1.5);
        vec3 c = mix(vec3(1.0, 0.93, 0.86), vec3(1.0, 0.55, 0.6), rim);
        float a = (0.55 + rim * 0.4 + bands * 0.08) * opacity;
        gl_FragColor = vec4(c, a);
      }`,
  });
  const m = new THREE.Mesh(new THREE.SphereGeometry(5.6, 40, 20, 0, Math.PI * 2, 0, Math.PI / 2), mat);
  m.position.set(islandCenterX(1), 0, 0);
  m.renderOrder = 2;
  return m;
}

export function createWorld(): WorldScene {
  const scene = new THREE.Scene();
  scene.fog = new THREE.Fog(HORIZON, 70, 260);
  scene.background = HORIZON.clone();
  scene.add(skyDome());

  const hemi = new THREE.HemisphereLight(0xcfe6ff, 0x5a7a6a, 0.75);
  scene.add(hemi);
  const sun = new THREE.DirectionalLight(0xffe2bd, 2.6);
  sun.position.set(-14, 26, 18);
  sun.castShadow = true;
  const sc = sun.shadow.camera;
  sc.left = -26;
  sc.right = 26;
  sc.top = 14;
  sc.bottom = -14;
  sc.near = 1;
  sc.far = 80;
  sun.shadow.bias = -0.0008;
  sun.shadow.normalBias = 0.03;
  scene.add(sun, sun.target);

  const seaMesh = sea();
  scene.add(seaMesh);
  const grass = grassTexture();
  scene.add(island(0, grass), island(1, grass));

  const flags: THREE.Mesh[] = [];
  for (const s of [0, 1] as Side[]) {
    const f = flag(s);
    scene.add(f.group);
    flags.push(f.cloth);
  }

  let seed = 7;
  const rng = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  const clouds: THREE.Group[] = [];
  for (let k = 0; k < 9; k++) {
    const c = cloud(rng);
    c.position.set(-90 + rng() * 180, 22 + rng() * 14, -60 - rng() * 70);
    c.scale.setScalar(1 + rng() * 1.5);
    c.userData.speed = 0.4 + rng() * 0.6;
    clouds.push(c);
    scene.add(c);
  }

  const grid = buildGrid(0);
  scene.add(grid);
  const dome = fogDome();
  scene.add(dome);

  return {
    scene,
    sun,
    grid,
    dome,
    flags,
    update(t: number) {
      (seaMesh.material as THREE.ShaderMaterial).uniforms.time.value = t;
      (dome.material as THREE.ShaderMaterial).uniforms.time.value = t;
      for (const c of clouds) {
        c.position.x += c.userData.speed * 0.016;
        if (c.position.x > 110) c.position.x = -110;
      }
      for (const cloth of flags) {
        const pos = (cloth.geometry as THREE.BufferGeometry).attributes.position as THREE.BufferAttribute;
        const base = cloth.userData.base as Float32Array;
        for (let k = 0; k < pos.count; k++) {
          const x = base[k * 3];
          pos.setZ(k, Math.sin(x * 4 - t * 6) * 0.1 * x);
        }
        pos.needsUpdate = true;
      }
    },
  };
}
