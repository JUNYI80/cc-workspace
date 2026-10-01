// Headless smoke test: loads the built page, builds a fort, plays a few turns with the
// auto-aimer and saves screenshots to test-output/. Usage: npm run build && npm run smoke
import { mkdirSync, readdirSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { chromium } from 'playwright-core';

const OUT = process.env.OUT ?? 'test-output';
mkdirSync(OUT, { recursive: true });
const maxShots = Number(process.env.SHOTS ?? 8);
const mobile = process.env.MOBILE === '1';

function findChrome() {
  const base = process.env.PLAYWRIGHT_BROWSERS_PATH ?? '/opt/pw-browsers';
  for (const d of readdirSync(base)) {
    const p = `${base}/${d}/chrome-linux/chrome`;
    if (d.startsWith('chromium-') && existsSync(p)) return p;
  }
  throw new Error('chromium not found');
}

const browser = await chromium.launch({
  executablePath: findChrome(),
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
});
const page = await browser.newPage(
  mobile
    ? { viewport: { width: 390, height: 780 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true }
    : process.env.SMALL
      ? { viewport: { width: 640, height: 400 } }
      : { viewport: { width: 1280, height: 760 } },
);
const errors = [];
page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
page.on('console', (m) => {
  if (m.type() === 'error' && !m.text().includes('fonts.g')) errors.push(`console: ${m.text()}`);
});

const tag = mobile ? 'm-' : '';
const shot = (name) => page.screenshot({ path: `${OUT}/${tag}${name}.png` });
const info = () => page.evaluate(() => window.__siege.info());
const wait = (ms) => page.waitForTimeout(ms);

await page.goto(pathToFileURL(resolve('dist/index.html')).href);
await wait(2500);
await shot('01-title');
await page.click('#btn-start');
await wait(1800);
await shot('02-build-empty');

// place the core and a few blocks by tapping the grid
const box = await page.locator('#scene').boundingBox();
for (const [fx, fy] of [
  [0.5, 0.5],
  [0.52, 0.5],
  [0.48, 0.52],
]) {
  if (mobile) await page.touchscreen.tap(box.x + box.width * fx, box.y + box.height * fy);
  else await page.mouse.click(box.x + box.width * fx, box.y + box.height * fy);
  await wait(400);
}
await shot('03-build-taps');
await page.click('#btn-auto');
await wait(3500);
await shot('04-build-auto');
await page.evaluate(() => window.__siege.finish());
await wait(1600);
await shot('05-reveal');
await wait(3000);

let shotsTaken = 0;
let lastPhase = '';
const t0 = Date.now();
while (Date.now() - t0 < Number(process.env.MAXTIME ?? 240) * 1000) {
  const s = await info();
  if (s.phase === 'end' || s.winner !== null) break;
  if (s.phase === 'aim' && lastPhase !== 'aim') {
    await page.evaluate(() => window.__siege.autoAim());
    await wait(600);
    if (shotsTaken < 2) {
      await page.evaluate(() => window.__siege.arrive());
      await wait(300);
      await shot(`10-aim-${shotsTaken}`);
    }
    await page.evaluate(() => window.__siege.fire());
    shotsTaken++;
    if (shotsTaken === 1) {
      for (let k = 0; k < 10; k++) {
        await wait(350);
        await shot(`11-seq-${String(k).padStart(2, '0')}`);
      }
    }
  }
  if (s.phase === 'ai-aim' && lastPhase !== 'ai-aim' && s.shots < 4) {
    await wait(1200);
    await page.evaluate(() => window.__siege.arrive());
    await wait(300);
    await shot(`20-ai-${s.shots}`);
  }
  lastPhase = s.phase;
  if (s.shots >= maxShots) break;
  await wait(250);
}
await wait(4000);
await shot('30-final');
console.log(JSON.stringify(await info()));
console.log(errors.length ? errors.join('\n') : 'no errors');
await browser.close();
