// Screenshot every page (and tab) of a running Streamlit app.
// Usage: node tools/shots.js <baseUrl> <outDir> <pages-json>
// pages-json: [["", "overview", []], ["stocks", "stocks", ["历史回测"]], ...]
const { chromium } = require('playwright');
const [base, out, pagesJson] = process.argv.slice(2);
const pages = JSON.parse(pagesJson);

async function settle(page) {
  await page.waitForSelector('h1', { timeout: 600000 });
  await page.waitForFunction(() => !document.querySelector('[data-testid="stStatusWidget"]'),
    null, { timeout: 900000 }).catch(() => console.log('  (still running after wait)'));
  await page.waitForTimeout(3000);
}

async function snap(page, name) {
  const exc = await page.$$eval('[data-testid="stException"]', els => els.map(e => e.innerText.slice(0, 500)));
  const errs = await page.$$eval('[data-testid="stAlertContentError"]', els => els.map(e => e.innerText.slice(0, 300)));
  console.log(`${name} | exceptions: ${exc.length ? JSON.stringify(exc) : 'none'} | error boxes: ${errs.length ? JSON.stringify(errs) : 'none'}`);
  await page.screenshot({ path: `${out}/${name}.png`, fullPage: true });
}

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1400, height: 2400 } });
  let failures = 0;
  for (let i = 0; i < 60; i++) {
    try { await page.goto(base, { timeout: 5000 }); break; } catch (e) { await new Promise(r => setTimeout(r, 2000)); }
  }
  for (const [path, name, tabs] of pages) {
    await page.goto(`${base}/${path}`);
    await settle(page);
    await snap(page, name);
    for (const t of tabs) {
      await page.getByRole('tab', { name: t }).click();
      await page.waitForTimeout(2000);
      await settle(page);
      await snap(page, `${name}-${t.replace(/\s/g, '')}`);
    }
    failures += (await page.$$('[data-testid="stException"]')).length;
  }
  await browser.close();
  process.exit(failures ? 1 : 0);
})();
