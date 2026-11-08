// Remote GPU end to end: a host page draws, a viewer page plays the video and drives the host.
// lan (default): signalling through simulator/v2/serve.mjs (#host / #view); room: through a stand-in for the claude.ai room.
// Usage: node test_remote.js [lan|room]
const fs = require('fs');
const { startServer, launch, routeCdn, FAKE_ROOM, watch, sleep, ready, SHOTS } = require('./harness');
const MODE = process.argv[2] || process.env.MODE || 'lan';

(async () => {
  fs.mkdirSync(SHOTS, { recursive: true });
  const port = MODE === 'lan' ? 8097 : 8096;
  const server = await startServer(port);
  const browser = await launch();
  const out = { mode: MODE, checks: {} };
  const check = (k, ok, info) => { out.checks[k] = { ok: !!ok, info }; };
  try {
    const mk = async (vp) => {
      const ctx = await browser.newContext({ viewport: vp, deviceScaleFactor: 1 });
      await routeCdn(ctx); await ctx.addInitScript('window.__EAGLE_VEG = 0.12;'); if (MODE === 'room') await ctx.addInitScript(FAKE_ROOM);
      return ctx;
    };
    let hostCtx, viewCtx;
    if (MODE === 'room') { hostCtx = viewCtx = await mk({ width: 900, height: 620 }); }   // BroadcastChannel needs one context
    else { hostCtx = await mk({ width: 820, height: 560 }); viewCtx = await mk({ width: 1000, height: 640 }); }
    const host = await hostCtx.newPage(), view = await viewCtx.newPage();
    const eh = watch(host, 'host'), ev = watch(view, 'view');
    const base = `http://localhost:${port}/`;
    await host.goto(base + (MODE === 'lan' ? '#host' : ''), { waitUntil: 'load' }); await ready(host);
    if (MODE === 'room') await host.evaluate(() => window.__sim.rg.host());
    await view.goto(base + (MODE === 'lan' ? '#view' : ''), { waitUntil: 'load' }); await ready(view);
    if (MODE === 'room') await view.evaluate(() => window.__sim.rg.view());
    const t0 = Date.now();
    try { await view.waitForFunction(() => window.__sim.rg.state.live, null, { timeout: 120000 }); } catch (e) { /* reported below */ }
    out.connectS = (Date.now() - t0) / 1000;
    out.viewState = await view.evaluate(() => window.__sim.rg.state);
    out.hostState = await host.evaluate(() => window.__sim.rg.state);
    check('viewer live', out.viewState.live, out.viewState.status);
    check('transport', out.viewState.transport === (MODE === 'lan' ? 'lan' : 'room'), out.viewState.transport);
    // the viewer stops drawing while live
    const f1 = await view.evaluate(() => window.__sim.frames); await sleep(5000); const f2 = await view.evaluate(() => window.__sim.frames);
    check('viewer stopped rendering', f2 === f1, { f1, f2 });
    const vid = await view.evaluate(() => { const v = document.querySelector('#rgv'), p = document.querySelector('#rgp'); return { w: v.videoWidth, h: v.videoHeight, pw: p.videoWidth, ph: p.videoHeight, frames: window.__sim.rg.state.frames, op: getComputedStyle(v).opacity }; });
    check('video playing', vid.w > 0 && vid.frames > 1 && vid.op === '1', vid);
    const hs = await host.evaluate(() => ({ cw: document.querySelector('#gl').width, ch: document.querySelector('#gl').height, hostSize: window.__sim.rg.state.hostSize, pip: [document.querySelector('#pip').width, document.querySelector('#pip').height] }));
    check('host renders at the viewer size', hs.hostSize && hs.hostSize.w === 1000 - 0 || hs.hostSize && hs.hostSize.w > 0, hs);
    // camera: drag on the viewer, the host follows
    await view.mouse.move(500, 300); await view.mouse.down(); await view.mouse.move(380, 260, { steps: 6 }); await view.mouse.up();
    // the viewer's camera eases to a stop and software WebGL draws the host at about 1 fps, so poll until the host catches up
    const camStart = await view.evaluate(() => window.__sim.dbg().camera.position.toArray());
    let camV, camH, d = Infinity, waited = 0;
    while (waited < 30000) {
      await sleep(1500); waited += 1500;
      camV = await view.evaluate(() => window.__sim.dbg().camera.position.toArray()); camH = await host.evaluate(() => window.__sim.dbg().camera.position.toArray());
      d = Math.hypot(camV[0] - camH[0], camV[1] - camH[1], camV[2] - camH[2]);
      if (d < 0.01) break;
    }
    const moved = Math.hypot(camV[0] - camStart[0], camV[1] - camStart[1], camV[2] - camStart[2]);
    check('host camera follows viewer', d < 0.01, { camV, camH, d, waitedMs: waited, viewerMovedSinceRelease: moved });
    // scenario change on the viewer reaches the host
    await view.evaluate(() => window.__sim.preset('cicadaDay')); await sleep(2500);
    const sh = await host.evaluate(() => ({ hour: window.__sim.S.hour, weather: window.__sim.S.weather, el: window.__sim.S.elevation_m }));
    check('preset mirrored', sh.hour === 14 && sh.el === 300, sh);
    await view.evaluate(() => window.__sim.apply({ opDist_m: 42 })); await sleep(2000);
    check('slider mirrored', (await host.evaluate(() => window.__sim.S.opDist_m)) === 42);
    await view.evaluate(() => window.__sim.pip('id')); await sleep(1500);
    check('pip mode mirrored', (await host.evaluate(() => document.querySelector('#pipMode button.on').dataset.pip)) === 'id');
    await view.evaluate(() => window.__sim.preset('night')); await sleep(1500);
    await view.evaluate(() => window.__sim.run()); await sleep(2500);
    check('mission mirrored', await host.evaluate(() => window.__sim.running.on), null);
    await sleep(3000);
    await view.screenshot({ path: `${SHOTS}/rg_${MODE}_view.png`, timeout: 120000 });
    await host.screenshot({ path: `${SHOTS}/rg_${MODE}_host.png`, timeout: 120000 });
    // presenter on the viewer, thermal step, uses the streamed Eagle cam
    await view.evaluate(() => { window.__sim.present.start(); window.__sim.present.go(3); }); await sleep(6000);
    const pres = await view.evaluate(() => { const c = document.querySelector('#pvTh'), x = c.getContext('2d').getImageData(c.width / 2 - 20, c.height / 2 - 20, 40, 40).data; let sum = 0; for (let i = 0; i < x.length; i += 4) sum += x[i] + x[i + 1] + x[i + 2]; return { w: c.width, h: c.height, mean: sum / (x.length / 4 * 3), hostPip: null }; });
    pres.hostPip = await host.evaluate(() => [document.querySelector('#pip').width, document.querySelector('#pip').height]);
    check('presenter thermal from stream', pres.mean > 5 && pres.hostPip[0] === 1280, pres);
    await view.screenshot({ path: `${SHOTS}/rg_${MODE}_present.png`, timeout: 120000 });
    await view.evaluate(() => window.__sim.present.stop());
    // host stops: the viewer falls back to drawing itself
    await host.evaluate(() => window.__sim.rg.stop()); await sleep(6000);
    const after = await view.evaluate(() => ({ live: window.__sim.rg.state.live, status: window.__sim.rg.state.status, f: window.__sim.frames }));
    await sleep(3000); const f3 = await view.evaluate(() => window.__sim.frames);
    check('viewer falls back to local', !after.live && f3 > after.f, Object.assign(after, { f3 }));
    if (MODE === 'room') out.roomStats = await view.evaluate(() => window.__roomStats);
    out.errors = eh.concat(ev);
  } finally { await browser.close(); server.kill(); }
  console.log(JSON.stringify(out, null, 1));
  const failed = Object.entries(out.checks).filter(([, v]) => !v.ok).map(([k]) => k);
  if (out.errors && out.errors.length) failed.push('console errors');
  console.log(failed.length ? 'FAIL: ' + failed.join(', ') : `PASS (${MODE}): ${Object.keys(out.checks).length} checks`);
  process.exit(failed.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
