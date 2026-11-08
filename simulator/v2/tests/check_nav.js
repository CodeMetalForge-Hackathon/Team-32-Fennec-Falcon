// Navigation in the merged simulator: every flight case and position source plans (or aborts) as main's model says,
// the route the Eagle flies is collision-checked, and the mission player flies it. Usage: node check_nav.js
const { startServer, launch, routeCdn, watch, sleep, ready, SHOTS } = require('./harness');
const fs = require('fs');
(async () => {
  fs.mkdirSync(SHOTS, { recursive: true });
  const server = await startServer(8097), browser = await launch(), out = { cases: [] };
  try {
    const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, deviceScaleFactor: 1 });
    await routeCdn(ctx); await ctx.addInitScript('window.__EAGLE_VEG = 0.15;');
    const page = await ctx.newPage(); const errs = watch(page, 'nav');
    await page.goto('http://localhost:8097/', { waitUntil: 'load' }); await ready(page); await sleep(1500);
    out.boot = await page.evaluate(() => window.__sim.nav.res);
    const grid = [];
    for (const perch of ['subcanopy', 'snag']) for (const navCase of ['branch', 'moving', 'grip-failure', 'blocked', 'sensor-loss']) grid.push({ perch, navCase, navPos: 'jamming' });
    for (const navPos of ['clear', 'spoofing', 'canopy', 'backup-loss']) grid.push({ perch: 'subcanopy', navCase: 'branch', navPos });
    for (const g of grid) {
      const r = await page.evaluate(p => { window.__sim.apply(p); window.__sim.nav.run(); return window.__sim.nav.res; }, g);
      out.cases.push(Object.assign({}, g, { ok: r.ok, support: r.support, aborted: r.aborted, routeLen_m: r.routeLen_m, boxes: r.boxes, ms: r.ms, legs: r.legs.map(l => l.name + (l.failed ? '(failed)' : '')).join(' > '), pos: r.pos.mode, last: r.events.slice(-3) }));
    }
    out.errors = errs.slice();
    console.log(JSON.stringify(out, null, 1));
  } finally { await browser.close(); server.kill(); }
})().catch(e => { console.error(e); process.exit(1); });
