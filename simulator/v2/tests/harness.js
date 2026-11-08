// Shared helpers for the headless simulator checks (run from simulator/v2/tests after `npm install`): local server, CDN routes to the npm copy of three.js r128,
// a stand-in for the claude.ai room (BroadcastChannel between pages of one browser context), error capture.
const { chromium } = require('playwright');
const path = require('path');
const { spawn } = require('child_process');

const THREE_DIR = path.dirname(require.resolve('three/package.json'));
const SIM_DIR = process.env.SIM_DIR || path.join(__dirname, '..');
const SHOTS = process.env.SHOTS || path.join(__dirname, 'shots');

async function startServer(port) {
  const p = spawn('node', [path.join(SIM_DIR, 'serve.mjs'), String(port)], { stdio: ['ignore', 'pipe', 'pipe'] });
  await new Promise((res, rej) => { p.stdout.on('data', d => { if (/port/.test(String(d))) res(); }); p.on('exit', c => rej(new Error('server exited ' + c))); });
  return p;
}

async function launch() {
  return chromium.launch({ args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist',
    '--disable-features=WebRtcHideLocalIpsWithMdns', '--autoplay-policy=no-user-gesture-required'] });
}

async function routeCdn(ctx) {
  await ctx.route(/cdnjs\.cloudflare\.com\/ajax\/libs\/three\.js\/r128\/three\.min\.js/, r => r.fulfill({ path: path.join(THREE_DIR, 'build/three.min.js'), contentType: 'text/javascript' }));
  await ctx.route(/cdn\.jsdelivr\.net\/npm\/three@0\.128\.0\/examples\/js\/(.*)$/, r => { const m = /examples\/js\/(.*)$/.exec(r.request().url()); r.fulfill({ path: path.join(THREE_DIR, 'examples/js', m[1]), contentType: 'text/javascript' }); });
  await ctx.route(/fonts\.googleapis\.com/, r => r.fulfill({ body: '', contentType: 'text/css' }));
  await ctx.route(/fonts\.gstatic\.com/, r => r.fulfill({ status: 404, body: '' }));
}

const FAKE_ROOM = `(() => {
  const ch = new BroadcastChannel('fake-room'), me = Math.random().toString(36).slice(2, 10), subs = {}, peerFns = [], others = new Map(); let pres = {};
  const snap = () => [{ peer: me, sameTab: true, isMe: true, kind: 'viewer', guest: false, by: null, presence: pres, updatedAt: Date.now() }]
    .concat([...others].map(([id, p]) => ({ peer: id, sameTab: false, isMe: true, kind: 'viewer', guest: false, by: null, presence: p, updatedAt: Date.now() })));
  const fire = () => { const s = snap(); peerFns.forEach(fn => fn({ peers: s, joined: [], left: [], updated: [] })); };
  ch.onmessage = e => { const m = e.data;
    if (m.k === 'msg') (subs[m.topic] || []).forEach(fn => fn({ peer: m.from, sameTab: false, isMe: true, kind: 'viewer', guest: false, by: null, topic: m.topic, data: JSON.parse(m.json) }));
    else if (m.k === 'pres') { others.set(m.from, m.p); fire(); if (m.ask) ch.postMessage({ k: 'pres', from: me, p: pres }); }
    else if (m.k === 'bye') { others.delete(m.from); fire(); } };
  addEventListener('pagehide', () => ch.postMessage({ k: 'bye', from: me }));
  window.__roomStats = { emits: 0, maxBytes: 0 };
  const room = {
    emit: async (topic, data) => { const json = JSON.stringify(data); window.__roomStats.emits++; window.__roomStats.maxBytes = Math.max(window.__roomStats.maxBytes, new Blob([json]).size);
      if (new Blob([json]).size > 4096) throw { code: 'invalid_argument', message: 'over 4 KiB' };
      ch.postMessage({ k: 'msg', from: me, topic, json }); (subs[topic] || []).forEach(fn => fn({ peer: me, sameTab: true, isMe: true, topic, data: JSON.parse(json) })); },
    on: (topic, fn) => { (subs[topic] = subs[topic] || []).push(fn); return () => {}; },
    presence: async p => { pres = Object.assign({}, pres, p); Object.keys(pres).forEach(k => { if (pres[k] === null) delete pres[k]; }); ch.postMessage({ k: 'pres', from: me, p: pres }); fire(); },
    peers: () => snap(),
    onPeers: fn => { peerFns.push(fn); setTimeout(() => fn({ peers: snap(), joined: snap(), left: [], updated: [] }), 0); ch.postMessage({ k: 'pres', from: me, p: pres, ask: true }); return () => {}; },
    connected: () => true, onConnection: fn => { setTimeout(() => fn(true), 0); return () => {}; },
  };
  window.claude = { use: async name => name === 'room' ? room : null };
})();`;

function watch(page, tag) {
  const errs = [];
  page.on('pageerror', e => errs.push(`[${tag}] pageerror ${e.message}`));
  page.on('console', m => { if (m.type() === 'error' || (m.type() === 'warning' && !/GPU stall|ReadPixels|Automatic fallback|swiftshader|WebGL/i.test(m.text()))) errs.push(`[${tag}] ${m.type()} ${m.text()}`); });
  return errs;
}
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function ready(page, ms = 90000) { await page.waitForFunction(() => window.__sim && window.__sim.ready, null, { timeout: ms }); }

module.exports = { startServer, launch, routeCdn, FAKE_ROOM, watch, sleep, ready, SHOTS, SIM_DIR };
