// The mission player on the merged simulator: the Eagle flies main's planned route, checks the support, then the
// sensing timeline runs from whichever support it holds; aborts end with nothing sent. Usage: node check_mission.js [shots]
const { startServer, launch, routeCdn, watch, sleep, ready, SHOTS } = require('./harness');
const fs = require('fs');
const SHOT = process.argv[2] === 'shots';
(async () => {
  fs.mkdirSync(SHOTS, { recursive: true });
  const server = await startServer(8093), browser = await launch(), out = { runs: [], views: [] };
  try {
    const ctx = await browser.newContext({ viewport: { width: 1500, height: 950 }, deviceScaleFactor: 1, reducedMotion: SHOT ? 'no-preference' : 'reduce' });
    await routeCdn(ctx); await ctx.addInitScript('window.__EAGLE_VEG = 0.15;');
    const page = await ctx.newPage(); const errs = watch(page, 'mission');
    await page.goto('http://localhost:8093/', { waitUntil: 'load' }); await ready(page); await sleep(1500);
    await page.evaluate(() => window.__sim.quality('low'));
    const cases = [{ navCase: 'branch' }, { navCase: 'grip-failure' }, { navCase: 'moving', perch: 'snag' }, { navCase: 'blocked' }, { navCase: 'branch', navPos: 'backup-loss' }];
    for (const c of cases) {
      await page.evaluate(p => { window.__sim.preset('night'); window.__sim.apply(p); window.__sim.nav.run(); }, c);
      const plan = await page.evaluate(() => window.__sim.nav.res);
      const t0 = Date.now(); await page.evaluate(() => { window.__sim.run(); });
      const seen = new Set(); let i = 0;
      while (Date.now() - t0 < (SHOT ? 180000 : 150000)) {
        await sleep(SHOT ? 1000 : 250);
        const st = await page.evaluate(() => ({ on: window.__sim.running.on, step: window.__sim.running.step, tick: document.querySelector('#ticker').textContent, perch: window.__sim.S.perch }));
        seen.add(st.step + ':' + st.tick.slice(0, 70));
        if (SHOT && c.navCase !== 'blocked' && i++ % 3 === 0) await page.screenshot({ path: `${SHOTS}/m_${c.navCase}_${String(i).padStart(2, '0')}.png`, timeout: 120000 });
        if (!st.on) break;
      }
      const end = await page.evaluate(() => { const s = window.__sim, e = s.eagle.object.position, dbg = s.nav.dbg(x => x), sup = s.nav.res && s.nav.res.support;
        const pp = sup ? dbg.perchPos(sup) : null;
        return { perch: s.S.perch, steps: [...document.querySelectorAll('#steps li')].map(li => li.className.replace(/\s+/g, ' ').trim() + '|' + li.textContent), tick: document.querySelector('#ticker').textContent,
          eagleMode: s.eagle.mode, distToSupport: pp ? +Math.hypot(e.x - pp.x, e.z - pp.z).toFixed(2) : null, report: s.R.timeToReport_s, msg: s.R.output.message.type + ' ' + s.R.output.message.bytes + ' B' }; });
      out.runs.push({ case: c, ms: Date.now() - t0, plan: { ok: plan.ok, support: plan.support, legs: plan.legs.map(l => l.name + (l.failed ? '(failed)' : '')), aborted: plan.aborted }, end, ticks: [...seen].slice(0, 30) });
      await sleep(SHOT ? 3500 : 3500);   // the perch is restored 3 s after the end
      out.runs[out.runs.length - 1].restoredPerch = await page.evaluate(() => window.__sim.S.perch);
    }
    // the viewpoints from main, perched
    await page.evaluate(() => { window.__sim.preset('night'); });
    for (const v of await page.evaluate(() => window.__sim.nav.views)) {
      await page.evaluate(v => window.__sim.cam(v), v); await sleep(700);
      const cam = await page.evaluate(() => { const d = window.__sim.dbg(); return { p: d.camera.position.toArray().map(x => +x.toFixed(1)), fov: d.camera.fov, sel: document.querySelector('#moreViews').value }; });
      out.views.push(Object.assign({ v }, cam));
      if (SHOT) await page.screenshot({ path: `${SHOTS}/v_${v}.png`, timeout: 120000 });
    }
    await page.evaluate(() => window.__sim.cam('orbit')); await sleep(400);
    out.errors = errs.slice();
  } finally { await browser.close(); server.kill(); }
  console.log(JSON.stringify(out, null, 1));
  const fails = [];
  const r = out.runs;
  if (!r[0].plan.ok || r[0].plan.support !== 'subcanopy' || r[0].end.report == null) fails.push('nominal run did not perch on the branch and report');
  if (!r[1].plan.ok || !r[1].plan.legs[0].includes('failed')) fails.push('grip failure did not retry');
  if (r[1].restoredPerch !== 'subcanopy') fails.push('perch not restored after the alternate support');
  if (r[3].plan.ok || r[4].plan.ok) fails.push('blocked / backup loss should abort');
  if (!/withheld|lost/.test(r[3].end.tick + r[4].end.tick)) fails.push('abort runs should say why');
  if (out.views.length !== 9 || out.views.some(v => v.sel !== v.v)) fails.push('viewpoints');
  if (out.errors.length) fails.push('console errors: ' + out.errors.join(' | '));
  console.log(fails.length ? 'FAIL\n' + fails.join('\n') : 'PASS: nominal, grip failure, moving support, blocked and position loss; 9 viewpoints; no console errors');
  process.exit(fails.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
