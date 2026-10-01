import * as THREE from 'three';
import type { Mat } from './config';

function canvas(size: number) {
  const c = document.createElement('canvas');
  c.width = c.height = size;
  const g = c.getContext('2d')!;
  return { c, g };
}

function finish(c: HTMLCanvasElement) {
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 4;
  return t;
}

function speckle(g: CanvasRenderingContext2D, size: number, n: number, alpha: number, rng = Math.random) {
  for (let k = 0; k < n; k++) {
    const v = rng() < 0.5 ? 0 : 255;
    g.fillStyle = `rgba(${v},${v},${v},${alpha * rng()})`;
    g.fillRect(rng() * size, rng() * size, 1 + rng() * 2, 1 + rng() * 2);
  }
}

function woodTexture() {
  const S = 128;
  const { c, g } = canvas(S);
  const planks = 4;
  const ph = S / planks;
  const tones = ['#c98f55', '#bd8249', '#d19a5f', '#b97c45'];
  for (let p = 0; p < planks; p++) {
    g.fillStyle = tones[p % tones.length];
    g.fillRect(0, p * ph, S, ph);
    // grain
    for (let k = 0; k < 14; k++) {
      g.strokeStyle = `rgba(90,50,20,${0.08 + Math.random() * 0.12})`;
      g.lineWidth = 1;
      g.beginPath();
      const y = p * ph + Math.random() * ph;
      g.moveTo(0, y);
      for (let x = 0; x <= S; x += 16) g.lineTo(x, y + Math.sin(x * 0.05 + k) * 1.5);
      g.stroke();
    }
    // seam
    g.fillStyle = 'rgba(70,38,14,0.55)';
    g.fillRect(0, p * ph, S, 2);
    // nails
    g.fillStyle = 'rgba(60,60,70,0.8)';
    for (const x of [8, S - 10]) {
      g.beginPath();
      g.arc(x, p * ph + ph / 2, 2, 0, Math.PI * 2);
      g.fill();
    }
  }
  speckle(g, S, 300, 0.12);
  // frame
  g.strokeStyle = 'rgba(70,38,14,0.7)';
  g.lineWidth = 4;
  g.strokeRect(2, 2, S - 4, S - 4);
  return finish(c);
}

function stoneTexture() {
  const S = 128;
  const { c, g } = canvas(S);
  g.fillStyle = '#7d7f86';
  g.fillRect(0, 0, S, S);
  const rows = 3;
  const rh = S / rows;
  for (let r = 0; r < rows; r++) {
    const off = r % 2 ? S / 4 : 0;
    for (let k = -1; k < 3; k++) {
      const x = off + k * (S / 2);
      const shade = 150 + Math.floor(Math.random() * 30);
      g.fillStyle = `rgb(${shade},${shade + 2},${shade + 8})`;
      g.beginPath();
      g.roundRect(x + 3, r * rh + 3, S / 2 - 6, rh - 6, 6);
      g.fill();
      g.fillStyle = 'rgba(255,255,255,0.12)';
      g.fillRect(x + 6, r * rh + 5, S / 2 - 14, 3);
    }
  }
  speckle(g, S, 700, 0.18);
  return finish(c);
}

function ironTexture() {
  const S = 128;
  const { c, g } = canvas(S);
  const grad = g.createLinearGradient(0, 0, S, S);
  grad.addColorStop(0, '#7b8796');
  grad.addColorStop(1, '#5b6674');
  g.fillStyle = grad;
  g.fillRect(0, 0, S, S);
  for (let y = 0; y < S; y += 2) {
    g.fillStyle = `rgba(255,255,255,${Math.random() * 0.05})`;
    g.fillRect(0, y, S, 1);
  }
  g.strokeStyle = 'rgba(30,36,44,0.8)';
  g.lineWidth = 6;
  g.strokeRect(3, 3, S - 6, S - 6);
  g.strokeStyle = 'rgba(255,255,255,0.18)';
  g.lineWidth = 2;
  g.strokeRect(9, 9, S - 18, S - 18);
  for (const [x, y] of [
    [14, 14],
    [S - 14, 14],
    [14, S - 14],
    [S - 14, S - 14],
    [S / 2, 14],
    [S / 2, S - 14],
  ]) {
    g.fillStyle = '#3b4450';
    g.beginPath();
    g.arc(x, y, 4.5, 0, Math.PI * 2);
    g.fill();
    g.fillStyle = 'rgba(255,255,255,0.45)';
    g.beginPath();
    g.arc(x - 1, y - 1, 1.8, 0, Math.PI * 2);
    g.fill();
  }
  speckle(g, S, 200, 0.1);
  return finish(c);
}

export function grassTexture() {
  const S = 256;
  const { c, g } = canvas(S);
  g.fillStyle = '#79b85a';
  g.fillRect(0, 0, S, S);
  for (let k = 0; k < 2200; k++) {
    const h = 85 + Math.random() * 30;
    const l = 38 + Math.random() * 18;
    g.fillStyle = `hsla(${h},45%,${l}%,0.5)`;
    g.fillRect(Math.random() * S, Math.random() * S, 2, 3 + Math.random() * 3);
  }
  return finish(c);
}

export function makeMaterialTextures(): Record<Mat, THREE.Texture> {
  return { wood: woodTexture(), stone: stoneTexture(), iron: ironTexture() };
}

/** Soft round sprite used by the particle systems. */
export function softDotTexture() {
  const S = 64;
  const { c, g } = canvas(S);
  const grad = g.createRadialGradient(S / 2, S / 2, 0, S / 2, S / 2, S / 2);
  grad.addColorStop(0, 'rgba(255,255,255,1)');
  grad.addColorStop(0.4, 'rgba(255,255,255,0.6)');
  grad.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = grad;
  g.fillRect(0, 0, S, S);
  return new THREE.CanvasTexture(c);
}
