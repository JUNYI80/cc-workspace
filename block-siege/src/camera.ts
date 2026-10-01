import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

/**
 * Two modes: free orbit (player drags the view) and directed (the game moves the camera
 * toward a goal pose with damping). Screen shake is layered on top of both.
 */
export class CameraRig {
  readonly camera: THREE.PerspectiveCamera;
  readonly controls: OrbitControls;
  private directed = false;
  private readonly goalPos = new THREE.Vector3();
  private readonly goalTarget = new THREE.Vector3();
  private readonly target = new THREE.Vector3();
  private stiffness = 3;
  private trauma = 0;
  private shakeT = 0;
  private readonly shakeOffset = new THREE.Vector3();
  private w = 1;
  private h = 1;
  private baseFov = 50;
  private inset = 0;
  private insetGoal = 0;

  constructor(dom: HTMLElement) {
    this.camera = new THREE.PerspectiveCamera(50, 1, 0.1, 900);
    this.controls = new OrbitControls(this.camera, dom);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.enablePan = false;
    this.controls.minDistance = 7;
    this.controls.maxDistance = 46;
    this.controls.maxPolarAngle = Math.PI * 0.47;
  }

  get isDirected() {
    return this.directed;
  }

  /** Free orbit around `target`, optionally jumping the camera to `pos`. */
  orbit(target: THREE.Vector3, pos?: THREE.Vector3) {
    this.directed = false;
    this.controls.enabled = true;
    this.controls.target.copy(target);
    if (pos) this.camera.position.copy(pos);
    this.controls.update();
  }

  /** Glide toward a pose; higher stiffness arrives faster. */
  direct(pos: THREE.Vector3, target: THREE.Vector3, stiffness = 3) {
    if (!this.directed) this.target.copy(this.controls.target);
    this.directed = true;
    this.controls.enabled = false;
    this.goalPos.copy(pos);
    this.goalTarget.copy(target);
    this.stiffness = stiffness;
  }

  /** Teleport while directed (used for the opening shot). */
  snap(pos: THREE.Vector3, target: THREE.Vector3) {
    this.camera.position.copy(pos);
    this.target.copy(target);
    this.goalPos.copy(pos);
    this.goalTarget.copy(target);
    this.controls.target.copy(target);
  }

  /** Jumps straight to the current goal pose (used by tests). */
  arrive() {
    if (!this.directed) return;
    this.camera.position.copy(this.goalPos);
    this.target.copy(this.goalTarget);
    this.camera.lookAt(this.target);
  }

  shake(amount: number) {
    this.trauma = Math.min(1, this.trauma + amount);
  }

  resize(w: number, h: number) {
    this.w = w;
    this.h = h;
    // keep both islands in frame on tall phone screens
    this.baseFov = w / h < 0.8 ? 62 : 50;
    this.applyView();
  }

  /** Focal length in CSS pixels (screen pixels per unit of size at unit distance). */
  get focalPx() {
    return this.h / (2 * Math.tan(THREE.MathUtils.degToRad(this.baseFov / 2)));
  }

  /** Pixels covered by the bottom control tray; the view recenters on the area above it. */
  setBottomInset(px: number) {
    this.insetGoal = px;
  }

  private applyView() {
    const s = Math.min(this.inset, this.h * 0.5) / 2;
    const fullH = this.h + 2 * s;
    this.camera.aspect = this.w / fullH;
    const t = Math.tan(THREE.MathUtils.degToRad(this.baseFov / 2)) * (fullH / this.h);
    this.camera.fov = THREE.MathUtils.radToDeg(2 * Math.atan(t));
    if (s < 0.5) this.camera.clearViewOffset();
    else this.camera.setViewOffset(this.w, fullH, 0, 2 * s, this.w, this.h);
    this.camera.updateProjectionMatrix();
  }

  update(dt: number) {
    if (Math.abs(this.inset - this.insetGoal) > 0.5) {
      this.inset += (this.insetGoal - this.inset) * (1 - Math.exp(-8 * dt));
      this.applyView();
    }
    this.camera.position.sub(this.shakeOffset);
    if (this.directed) {
      const k = 1 - Math.exp(-this.stiffness * dt);
      this.camera.position.lerp(this.goalPos, k);
      this.target.lerp(this.goalTarget, k);
      this.camera.lookAt(this.target);
      this.controls.target.copy(this.target);
    } else {
      this.controls.update();
    }
    this.shakeT += dt;
    this.trauma = Math.max(0, this.trauma - dt * 1.4);
    const s = this.trauma * this.trauma * 0.7;
    const t = this.shakeT * 28;
    this.shakeOffset.set(
      s * (Math.sin(t * 1.1) + Math.sin(t * 2.3 + 1.3)) * 0.5,
      s * (Math.sin(t * 1.7 + 2.1) + Math.sin(t * 2.9)) * 0.5,
      s * (Math.sin(t * 1.3 + 4.2) + Math.sin(t * 3.1 + 0.7)) * 0.5,
    );
    this.camera.position.add(this.shakeOffset);
  }
}
