// Loads the simulator, checks it renders without errors, then walks the presenter through every step, checks the
// cards' live numbers against the model and screenshots each step (simulator/v2/tests/shots/).
// Usage: node test_present.js
const fs = require('fs');
const { startServer, launch, routeCdn, watch, sleep, ready, SHOTS } = require('./harness');

(async () => {
  fs.mkdirSync(SHOTS, { recursive: true });
  const server = await startServer(8099);
  const browser = await launch();
  const out = { steps: [] };
  try {
    const ctx = await browser.newContext({ viewport: { width: 1500, height: 1000 }, deviceScaleFactor: 1 });
    await routeCdn(ctx); await ctx.addInitScript('window.__EAGLE_VEG = 0.15;');
    const page = await ctx.newPage(); const errs = watch(page, 'main');
    const t0 = Date.now();
    await page.goto('http://localhost:8099/', { waitUntil: 'load' });
    await ready(page);
    out.bootMs = Date.now() - t0;
    await sleep(6000);
    out.perf = await page.evaluate(() => window.__sim.perf);
    out.frames6s = await page.evaluate(() => window.__sim.frames);
    await page.evaluate(() => window.__sim.quality('low'));   // fixed resolution for comparable screenshots
    await page.screenshot({ path: `${SHOTS}/00_main.png`, timeout: 120000 });
    // presenter
    await page.keyboard.press('p');
    await sleep(500);
    const n = await page.evaluate(() => window.__sim.present.state.n);
    for (let i = 0; i < n; i++) {
      if (i > 0) await page.keyboard.press('ArrowRight');
      await sleep(i === 6 ? 9000 : 3800);   // step 7 runs the mission; give it time to fly
      const st = await page.evaluate(() => Object.assign(window.__sim.present.state, { body: document.querySelector('#pBody').textContent, stats: [...document.querySelectorAll('.pstat')].map(e => e.innerText.replace(/\n/g, ' | ')) }));
      out.steps.push(st);
      await page.screenshot({ path: `${SHOTS}/p${String(i + 1).padStart(2, '0')}.png`, timeout: 120000 });
    }
    await page.keyboard.press('Escape');
    await sleep(800);
    out.afterExit = await page.evaluate(() => ({ present: window.__sim.present.state.on, bodyClass: document.body.className, hour: window.__sim.S.hour }));
    out.errors = errs;
  } finally { await browser.close(); server.kill(); }
  // the cards must carry the model's numbers for the night and 14:00 presets (see docs/simulator-v2.md, Results)
  const has = (i, s) => out.steps[i] && (out.steps[i].stats.join(' ') + ' ' + out.steps[i].body).includes(s);
  const expect = [[0, '250 m'], [1, '246 m'], [3, '5.5 px'], [3, '+6.6 °C'], [4, '94 m'], [5, '0.14'], [5, '0.99'], [6, '3.0 km'], [7, '10 m'], [7, '8.3 h'], [8, '-0.7 °C'], [8, '108 h']];
  const fails = expect.filter(([i, s]) => !has(i, s)).map(([i, s]) => `step ${i + 1}: "${s}" missing`);
  if (out.steps.length !== 10) fails.push(`expected 10 steps, saw ${out.steps.length}`);
  if (out.errors.length) fails.push('console errors: ' + out.errors.join(' | '));
  if (!out.afterExit || out.afterExit.present || out.afterExit.hour !== 2) fails.push('exit did not restore the settings');
  console.log(JSON.stringify(out, null, 1));
  console.log(fails.length ? 'FAIL\n' + fails.join('\n') : `PASS: 10 steps, ${expect.length} numbers checked, no console errors`);
  process.exit(fails.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
