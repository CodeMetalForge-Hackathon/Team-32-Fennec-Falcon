/* Eagle drone: a flapping-wing drone built to the size of a Mountain Hawk-Eagle (Nisaetus nipalensis, 熊鷹).
 *
 * One skeleton, three skins:
 *   airframe      the bare drone from the reference render (carbon pod, chrome nose, wing linkage, diamond tail)
 *   hawkEagle     full disguise: adult Mountain Hawk-Eagle plumage (default)
 *   serpentEagle  full disguise: Crested Serpent Eagle (the livery simulator v2 used before)
 *
 * The wings are fans of separate feathers (lamellae on the airframe) hung on shoulder, elbow and wrist joints, so the same
 * mechanism spreads them for flight and folds them along the body like a perched eagle. Each bird is one skinned mesh
 * (one draw call per material slot) with procedural animation: flap, glide, soar, land on a perch, fold, scan, take off.
 *
 * Works with three.js r128 (simulator v2) and newer; no other dependencies. Units: metres, +Y up, +Z forward (glTF).
 * Browser: load three.min.js, then eagle_drone.js -> window.EagleDrone
 * Node:    const lib = require('./eagle_drone.js')(THREE)
 */
(function (root) {
'use strict';

function makeEagleDrone(THREE) {
  const DEG = Math.PI / 180;
  const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
  const lerp = (a, b, t) => a + (b - a) * t;
  const smooth = (a, b, x) => { const t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); };
  const damp = (cur, target, rate, dt) => lerp(cur, target, 1 - Math.exp(-rate * dt));
  const wrapPi = a => Math.atan2(Math.sin(a), Math.cos(a));
  function rng(seed) { let s = seed >>> 0; return () => { s += 0x6D2B79F5; let t = s; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }

  // ---------- size: Mountain Hawk-Eagle ----------
  // Length 71-76 cm, wing (folded) 49-52 cm, tail 34-36 cm, tarsus 11-12 cm, 2.2-3.1 kg (Suzuka, Japan sample; Bird Research News),
  // span 134-175 cm (BirdA). Wingbeat: golden eagles 2.2-2.8 Hz (arXiv 2412.00231); a lighter hawk-eagle sits near 3 Hz.
  const SPEC = {
    species: 'Mountain Hawk-Eagle', latin: 'Nisaetus nipalensis', local: '熊鷹',
    span_m: 1.60, length_m: 0.75, wingFolded_m: 0.50, tail_m: 0.335, tarsus_m: 0.115, mass_kg: 2.6,
    flapHz: 3.0, cruise_ms: 11, glide_ms: 10,
  };

  // ---------- rig dimensions (body frame, flight posture, origin = body centre) ----------
  const RIG = {
    shoulder: [0.050, 0.036, 0.064], hum: 0.142, fore: 0.174, hand: 0.130,
    hip: [0.034, -0.042, -0.026], tibia: 0.100, tarsus: 0.112,
    tail: [0, 0.010, -0.150], neck: [0, 0.024, 0.138], head: [0, 0.008, 0.038],
  };
  // primaries P1 (inner, at the wrist) .. P10 (outer): place on the hand, length, vane widths, angle from the hand spread / folded (deg),
  // outer- and inner-vane notches (the "fingers": a hawk-eagle shows six)
  const PRIM = [
    [0.05, 0.278, 0.020, 0.040, 97, 9, 0, 0], [0.15, 0.288, 0.020, 0.040, 92, 8.5, 0, 0], [0.25, 0.303, 0.019, 0.039, 85, 8, 0, 0],
    [0.35, 0.323, 0.019, 0.038, 77, 7.5, 0, 0], [0.45, 0.348, 0.018, 0.037, 68, 7, 0.64, 0], [0.55, 0.376, 0.017, 0.036, 58, 6.5, 0.58, 0.62],
    [0.65, 0.400, 0.016, 0.034, 47, 6, 0.55, 0.57], [0.75, 0.414, 0.015, 0.032, 35, 5.5, 0.52, 0.53], [0.85, 0.404, 0.014, 0.030, 22, 5, 0.50, 0.51],
    [0.95, 0.330, 0.012, 0.026, 10, 4.5, 0.50, 0.53],
  ].map(([t, L, wo, wi, a, f, eo, ei], i) => ({ t, L, wo, wi, a, f, eo, ei, i }));
  // secondaries S1 (outer, at the wrist) .. S13 (tertials at the elbow): place on the forearm, length, angle spread / folded
  const SEC = [
    [0.97, 0.245, 90, 186], [0.885, 0.252, 91, 186], [0.80, 0.257, 92, 185.5], [0.715, 0.260, 93, 185.5], [0.63, 0.262, 94, 185],
    [0.545, 0.262, 95, 185], [0.46, 0.260, 96, 184.5], [0.375, 0.257, 97, 184.5], [0.29, 0.252, 98, 184], [0.205, 0.246, 99, 184],
    [0.12, 0.232, 101, 183.5], [0.04, 0.214, 104, 183], [-0.03, 0.195, 108, 183],
  ].map(([t, L, a, f], i) => ({ t, L, a, f, i, wo: i > 9 ? 0.026 : 0.022, wi: i > 9 ? 0.036 : 0.045 }));
  // humerals: inner-wing feathers on the upper arm that close the gap between the tertials and the body
  const HUM = [[0.30, 0.150, 112, 6], [0.58, 0.172, 108, 4], [0.86, 0.188, 105, 2]].map(([t, L, a, f], i) => ({ t, L, a, f, i }));
  // folded-wing geometry (deg): humerus swept back, wing plane rolled so it drapes over the back and flank, Z-fold at elbow and wrist
  const FOLD = { sweep: 86, roll: -54, elbow: -168, wrist: 166 };
  // tail: rectrices R1 (central) .. R6 (outer), each side
  const RECT = [0.335, 0.333, 0.330, 0.326, 0.321, 0.315];
  // toes: [yaw (deg, + = inward), length of the two segments, talon]
  const TOES = [[18, 0.026, 0.020, 0.018], [0, 0.032, 0.024, 0.021], [-24, 0.026, 0.018, 0.017], [180, 0.024, 0.016, 0.026]];

  // ---------- liveries ----------
  const LIVERIES = {
    hawkEagle: { label: 'Disguise · Mountain Hawk-Eagle', disguise: true, feathered: true },
    serpentEagle: { label: 'Disguise · Crested Serpent Eagle', disguise: true, feathered: false },
    airframe: { label: 'Airframe (reference)', disguise: false },
  };

  // ---------- atlas layout (UV space, origin bottom-left) ----------
  const STRIP = 1 / 16;
  const A = {
    body: [0, 0.5, 0.5, 0.5], legs: [0.5, 0.75, 0.25, 0.25], tarsus: [0.5, 0.5, 0.125, 0.25],
    bill: [0.75, 0.875, 0.125, 0.125], cere: [0.875, 0.875, 0.125, 0.0625], eye: [0.875, 0.9375, 0.0625, 0.0625],
    toe: [0.75, 0.75, 0.125, 0.125], talon: [0.875, 0.8125, 0.0625, 0.0625], metal: [0.9375, 0.8125, 0.0625, 0.0625],
    led: [0.875, 0.75, 0.0625, 0.0625], dark: [0.9375, 0.75, 0.0625, 0.0625], crest: [0.625, 0.5, 0.125, 0.25],
    carbon: [0.75, 0.5, 0.25, 0.25], brow: [0.02, 0.5 + 0.5 * 0.88, 0.1, 0.5 * 0.06],
  };
  const S_ = k => [k * STRIP, 0, STRIP, 0.5];
  const FS = { primD: S_(0), primV: S_(1), secD: S_(2), secV: S_(3), tailD: S_(4), tailV: S_(5), covD: S_(6), covV: S_(7),
    armD: S_(8), armV: S_(9), pcovD: S_(10), terD: S_(11), uwV: S_(12), lamD: S_(13), lamV: S_(14), kite: S_(15) };
  const uvIn = (r, u, v) => [r[0] + clamp(u, 0, 1) * r[2], r[1] + clamp(v, 0, 1) * r[3]];

  // ---------- painting ----------
  function makeCanvas(n) {
    if (typeof document !== 'undefined') { const c = document.createElement('canvas'); c.width = c.height = n; return c; }
    if (typeof OffscreenCanvas !== 'undefined') return new OffscreenCanvas(n, n);
    throw new Error('EagleDrone: no canvas available to paint the atlas');
  }
  function paintAtlas(livery, n) {
    const cv = makeCanvas(n), x = cv.getContext('2d'), r = rng(livery === 'airframe' ? 7 : livery === 'serpentEagle' ? 11 : 3);
    const px = (u, v) => [u * n, (1 - v) * n];
    // fill a UV rect with a function painter(ctx, w, h) drawn in that rect's pixel space (y down = v from 1 to 0)
    const region = (rect, painter) => { const [u0, v0, w, h] = rect, [X, Y] = px(u0, v0 + h); x.save(); x.beginPath(); x.rect(X, Y, w * n, h * n); x.clip(); x.translate(X, Y); painter(x, w * n, h * n); x.restore(); };
    const fill = (rect, col) => region(rect, (c, w, h) => { c.fillStyle = col; c.fillRect(0, 0, w, h); });
    const pal = livery === 'hawkEagle' ? {
      back: '#3e2c1f', back2: '#523b28', fringe: '#7b5d40', belly: '#ece4d6', bar: '#8a4a26', streak: '#22160f', head: '#7a4c2b', headStreak: '#2a1a10',
      throat: '#f0ebe2', mesial: '#1d140e', primDBase: '#4a3624', primDBar: '#2b1e14', primVBase: '#e9e5dd', primVBar: '#3a2c22', primVTip: '#2a221d',
      secVBase: '#d5d2cc', secVBar: '#3b2d23', tailD: '#6a543f', tailBand: '#2c2018', tailV: '#ddd8cf', tailVBand: '#3b2e25', uw: '#b0683a', uwBar: '#f0e6d6',
      leg: '#efe7da', legBar: '#9a5a33', bill: '#2f2f33', billTip: '#121214', cere: '#4a4a4c', iris: '#e8a21c', toe: '#e1c56a', talon: '#141414' }
      : livery === 'serpentEagle' ? {
      back: '#3a2a1d', back2: '#4a3524', fringe: '#5d4630', belly: '#6b4d33', bar: '#e8dcc4', streak: '#2a1d14', head: '#1d1712', headStreak: '#e6dfd2',
      throat: '#5b4430', mesial: '#2a1d14', primDBase: '#2a1f17', primDBar: '#cfc6b6', primVBase: '#1d1814', primVBar: '#efe8dc', primVTip: '#141110',
      secVBase: '#1e1915', secVBar: '#efe8dc', tailD: '#1c1713', tailBand: '#e9e1d2', tailV: '#1e1915', tailVBand: '#f0e9dd', uw: '#5a3f2a', uwBar: '#e8dcc4',
      leg: '#e8c23a', legBar: '#c9a12a', bill: '#5d6670', billTip: '#2a2d31', cere: '#f0c23a', iris: '#f2c21c', toe: '#e8c23a', talon: '#141414' }
      : { carbon: '#2a2c30', carbon2: '#33363b', seam: '#1b1c1f', slit: '#cfd3d8', metal: '#c9ced4', led: '#ff7a1a' };

    // generic feather strip: dorsal/ventral, bars, tip colour, pale fringe, rachis at u = 0.35
    const feather = (rect, o) => region(rect, (c, w, h) => {
      const g = c.createLinearGradient(0, h, 0, 0); g.addColorStop(0, o.base0 || o.base); g.addColorStop(1, o.base1 || o.base); c.fillStyle = g; c.fillRect(0, 0, w, h);
      if (o.bars) for (let k = 0; k < o.bars.n; k++) { const v = o.bars.v0 + (o.bars.v1 - o.bars.v0) * (k + 0.5) / o.bars.n, th = o.bars.th * (1 + (r() - 0.5) * 0.3);
        c.fillStyle = o.bars.col; c.globalAlpha = o.bars.a == null ? 1 : o.bars.a; c.beginPath();
        const y = (1 - v) * h, wob = h * 0.006; c.moveTo(0, y - th * h / 2 + wob); c.quadraticCurveTo(w * 0.5, y - th * h / 2 - wob * 2, w, y - th * h / 2 + wob * 3);
        c.lineTo(w, y + th * h / 2 + wob * 3); c.quadraticCurveTo(w * 0.5, y + th * h / 2 - wob * 2, 0, y + th * h / 2 + wob); c.fill(); c.globalAlpha = 1; }
      if (o.band) { const [v0, v1, col] = o.band; c.fillStyle = col; c.fillRect(0, (1 - v1) * h, w, (v1 - v0) * h); }
      if (o.tip) { const g2 = c.createLinearGradient(0, (1 - o.tip[0]) * h, 0, 0); g2.addColorStop(0, 'rgba(0,0,0,0)'); g2.addColorStop(0.25, o.tip[1]); g2.addColorStop(1, o.tip[1]); c.fillStyle = g2; c.fillRect(0, 0, w, (1 - o.tip[0]) * h); }
      if (o.base2) { const g3 = c.createLinearGradient(0, h, 0, h * (1 - o.base2[0])); g3.addColorStop(0, o.base2[1]); g3.addColorStop(1, 'rgba(0,0,0,0)'); c.fillStyle = g3; c.fillRect(0, h * (1 - o.base2[0]), w, h * o.base2[0]); }
      if (o.fringe) { c.strokeStyle = o.fringe; c.lineWidth = w * 0.09; c.globalAlpha = 0.85; c.strokeRect(0, 0, w, h); c.globalAlpha = 1; }
      if (o.spots) for (let k = 0; k < o.spots.n; k++) { c.fillStyle = o.spots.col; c.beginPath(); c.ellipse(w * (0.2 + r() * 0.6), h * (0.15 + r() * 0.75), w * o.spots.r, w * o.spots.r * 1.4, 0, 0, 7); c.fill(); }
      if (o.slits) { c.fillStyle = o.slits; for (const v of [0.32, 0.63]) { c.fillRect(w * 0.42, (1 - v) * h, w * 0.06, h * 0.035); } }
      // vane texture: fine barbs and the rachis
      c.globalAlpha = o.barbA == null ? 0.10 : o.barbA; c.strokeStyle = o.barb || '#000'; c.lineWidth = Math.max(1, w * 0.012);
      for (let y = 0; y < h; y += Math.max(2, h / 180)) { c.beginPath(); c.moveTo(w * 0.35, y); c.lineTo(0, y - h * 0.03); c.moveTo(w * 0.35, y); c.lineTo(w, y - h * 0.05); c.stroke(); }
      c.globalAlpha = o.rachA == null ? 0.6 : o.rachA; c.strokeStyle = o.rach || '#d9cbb3'; c.lineWidth = Math.max(1, w * 0.035); c.beginPath(); c.moveTo(w * 0.35, h); c.lineTo(w * 0.35, 0); c.stroke(); c.globalAlpha = 1;
    });

    if (livery === 'airframe') {
      // carbon twill in every region, then the special bits
      const twill = (c, w, h, s, col1, col2) => { c.fillStyle = col1; c.fillRect(0, 0, w, h); c.fillStyle = col2; const k = Math.max(3, s);
        for (let y = 0; y < h; y += k) for (let xx = (y / k) % 2 ? 0 : k; xx < w; xx += 2 * k) { c.fillRect(xx, y, k, k * 0.55); } };
      x.fillStyle = pal.carbon; x.fillRect(0, 0, n, n);
      region(A.body, (c, w, h) => { twill(c, w, h, n / 512, pal.carbon, pal.carbon2); c.fillStyle = pal.seam;
        for (const u of [0.5]) c.fillRect(u * w - w * 0.004, 0, w * 0.008, h);            // dorsal spine
        for (const v of [0.18, 0.47, 0.74]) c.fillRect(0, (1 - v) * h, w, h * 0.004);      // panel seams
        c.fillStyle = pal.slit; for (const [u, v] of [[0.28, 0.6], [0.72, 0.6], [0.3, 0.35], [0.7, 0.35], [0.25, 0.8], [0.75, 0.8]]) c.fillRect(u * w, (1 - v) * h, w * 0.02, h * 0.006); });
      region(A.carbon, (c, w, h) => twill(c, w, h, n / 512, pal.carbon, pal.carbon2));
      for (const k of ['lamD', 'lamV', 'primD', 'primV', 'secD', 'secV', 'covD', 'covV', 'armD', 'armV', 'pcovD', 'terD', 'uwV', 'tailD', 'tailV', 'kite'])
        feather(FS[k], { base0: k.endsWith('V') ? '#2f3237' : '#34373c', base1: k.endsWith('V') ? '#26282c' : '#2b2e32', barbA: 0.05, barb: '#000', rachA: 0.5, rach: '#1d1f22',
          fringe: '#1f2124', slits: /prim|sec|lam/.test(k) && !k.endsWith('V') ? pal.slit : null });
      region(A.metal, (c, w, h) => { const g = c.createLinearGradient(0, 0, w, h); g.addColorStop(0, '#f2f4f6'); g.addColorStop(0.45, '#9aa1a8'); g.addColorStop(1, '#dfe3e7'); c.fillStyle = g; c.fillRect(0, 0, w, h); });
      fill(A.led, pal.led); fill(A.dark, '#151618'); fill(A.talon, '#1a1b1d'); fill(A.toe, '#3b3e43'); fill(A.eye, '#0c0d0f');
      fill(A.legs, '#2c2f33'); fill(A.tarsus, '#2c2f33'); fill(A.bill, '#c9ced4'); fill(A.cere, '#c9ced4');
      return cv;
    }

    // ----- disguise liveries -----
    const P = pal, serp = livery === 'serpentEagle';
    // body: u around (0 = belly midline, 0.5 = back), v along (0 = tail end, 1 = bill)
    region(A.body, (c, w, h) => {
      const Y = v => (1 - v) * h;
      for (let i = 0; i < 64; i++) { const u0 = i / 64, d = (1 - Math.cos(2 * Math.PI * (u0 + 0.5 / 64))) / 2;
        // dorsal brown, ventral pale (hawk-eagle) or brown (serpent)
        c.fillStyle = mix(P.belly, P.back, smooth(0.32, 0.62, d)); c.fillRect(u0 * w, 0, w / 64 + 1, h); }
      // head and neck colour
      const gh = c.createLinearGradient(0, Y(0.70), 0, Y(0.80)); gh.addColorStop(0, 'rgba(0,0,0,0)'); gh.addColorStop(1, P.head); c.fillStyle = gh; c.fillRect(0, Y(0.80), w, Y(0.70) - Y(0.80)); c.fillStyle = P.head; c.fillRect(0, 0, w, Y(0.80));
      if (!serp) {
        // throat: white with a black mesial stripe and malar stripes (around u = 0)
        for (const u of [0, 1]) { const g = c.createRadialGradient(u * w, Y(0.80), 1, u * w, Y(0.80), w * 0.17); g.addColorStop(0, P.throat); g.addColorStop(0.8, P.throat); g.addColorStop(1, 'rgba(240,235,226,0)'); c.fillStyle = g; c.fillRect(u * w - w * 0.17, Y(0.92), w * 0.34, Y(0.68) - Y(0.92)); }
        c.fillStyle = P.mesial; for (const u of [0, 1]) { c.fillRect(u * w - w * 0.012, Y(0.90), w * 0.024, Y(0.70) - Y(0.90)); }
        for (const u of [0.11, 0.89, 1.11, -0.11]) { c.fillRect(u * w - w * 0.008, Y(0.92), w * 0.016, Y(0.76) - Y(0.92)); }
        // breast: heavy dark streaks; belly to vent: rufous bars
        for (let k = 0; k < 520; k++) { const u = r(), v = 0.52 + r() * 0.2, d = (1 - Math.cos(2 * Math.PI * u)) / 2; if (d > 0.42) continue;
          c.fillStyle = P.streak; c.globalAlpha = 0.85; c.beginPath(); c.ellipse(u * w, Y(v), w * (0.004 + r() * 0.004), h * (0.012 + r() * 0.012), 0, 0, 7); c.fill(); }
        c.globalAlpha = 1;
        for (let v = 0.04; v < 0.53; v += 0.022) for (let k = 0; k < 40; k++) { const u = (k + r() * 0.5) / 40, d = (1 - Math.cos(2 * Math.PI * u)) / 2; if (d > 0.45) continue;
          c.fillStyle = P.bar; c.globalAlpha = 0.9; c.fillRect(u * w, Y(v + r() * 0.004), w / 40 * (0.8 + r() * 0.5), h * (0.006 + r() * 0.003)); }
        c.globalAlpha = 1;
        // head: rusty with black streaks; darker crown
        for (let k = 0; k < 700; k++) { const u = r(), v = 0.80 + r() * 0.2; c.fillStyle = P.headStreak; c.globalAlpha = 0.7; c.fillRect(u * w, Y(v), w * 0.004, h * (0.008 + r() * 0.01)); }
        c.globalAlpha = 1; const gc = c.createLinearGradient(0.35 * w, 0, 0.65 * w, 0); gc.addColorStop(0, 'rgba(25,16,10,0)'); gc.addColorStop(0.5, 'rgba(25,16,10,0.75)'); gc.addColorStop(1, 'rgba(25,16,10,0)'); c.fillStyle = gc; c.fillRect(0.3 * w, 0, 0.4 * w, Y(0.84));
      } else {
        // serpent eagle: dark crown and crest, small white spots on belly and flanks, grey-brown face
        for (let k = 0; k < 900; k++) { const u = r(), v = 0.05 + r() * 0.62, d = (1 - Math.cos(2 * Math.PI * u)) / 2; if (d > 0.55) continue;
          c.fillStyle = P.bar; c.globalAlpha = 0.9; c.beginPath(); c.arc(u * w, Y(v), w * (0.003 + r() * 0.003), 0, 7); c.fill(); }
        c.globalAlpha = 1; for (let k = 0; k < 160; k++) { const u = 0.3 + r() * 0.4, v = 0.84 + r() * 0.12; c.fillStyle = P.headStreak; c.beginPath(); c.arc(u * w, Y(v), w * 0.003, 0, 7); c.fill(); }
        // bare yellow facial skin between eye and bill
        for (const u of [0.25, 0.75]) { c.fillStyle = '#e9bd2c'; c.beginPath(); c.ellipse(u * w, Y(0.975), w * 0.05, h * 0.018, 0, 0, 7); c.fill(); }
      }
      // back: feather scallops
      for (let v = 0.0; v < 0.9; v += 0.016) for (let k = 0; k < 44; k++) { const u = (k + (Math.round(v / 0.016) % 2) * 0.5) / 44, d = (1 - Math.cos(2 * Math.PI * u)) / 2; if (d < 0.6) continue;
        c.strokeStyle = serp ? P.fringe : P.fringe; c.globalAlpha = 0.45 * smooth(0.6, 0.85, d); c.lineWidth = w * 0.0025; c.beginPath(); c.arc(u * w, Y(v), w * 0.011, 0.15 * Math.PI, 0.85 * Math.PI); c.stroke(); }
      c.globalAlpha = 1;
    });
    // feathered legs ("trousers") and tarsus
    region(A.legs, (c, w, h) => { c.fillStyle = P.leg; c.fillRect(0, 0, w, h);
      for (let y = 0; y < h; y += h / 18) for (let k = 0; k < 12; k++) { c.fillStyle = P.legBar; c.globalAlpha = serp ? 0.25 : 0.9; c.fillRect(k * w / 12 + r() * 3, y + r() * 3, w / 13, h / 38); }
      if (serp) { c.globalAlpha = 1; c.fillStyle = '#5d4330'; c.fillRect(0, 0, w, h); for (let k = 0; k < 180; k++) { c.fillStyle = P.bar; c.beginPath(); c.arc(r() * w, r() * h, w * 0.012, 0, 7); c.fill(); } }
      c.globalAlpha = 1; });
    region(A.tarsus, (c, w, h) => { if (serp) { c.fillStyle = P.toe; c.fillRect(0, 0, w, h); c.strokeStyle = 'rgba(120,90,20,0.6)'; for (let y = 0; y < h; y += h / 24) { c.beginPath(); c.moveTo(0, y); c.lineTo(w, y); c.stroke(); } }
      else { c.fillStyle = P.leg; c.fillRect(0, 0, w, h); for (let y = 0; y < h; y += h / 16) for (let k = 0; k < 6; k++) { c.fillStyle = P.legBar; c.globalAlpha = 0.75; c.fillRect(k * w / 6 + r() * 2, y + r() * 2, w / 7, h / 50); } c.globalAlpha = 1; } });
    region(A.crest, (c, w, h) => { c.fillStyle = serp ? '#141110' : '#2a1a10'; c.fillRect(0, 0, w, h); if (serp) for (let k = 0; k < 40; k++) { c.fillStyle = '#efe8dc'; c.beginPath(); c.arc(r() * w, r() * h, w * 0.05, 0, 7); c.fill(); } });
    region(A.bill, (c, w, h) => { const g = c.createLinearGradient(0, h, 0, 0); g.addColorStop(0, P.bill); g.addColorStop(0.6, P.bill); g.addColorStop(1, P.billTip); c.fillStyle = g; c.fillRect(0, 0, w, h); });
    fill(A.cere, P.cere);
    region(A.eye, (c, w, h) => { c.fillStyle = '#111'; c.fillRect(0, 0, w, h); const g = c.createRadialGradient(w / 2, h / 2, w * 0.12, w / 2, h / 2, w * 0.5); g.addColorStop(0, '#000'); g.addColorStop(0.40, '#000'); g.addColorStop(0.45, P.iris); g.addColorStop(0.82, mix(P.iris, '#8a4a10', 0.35)); g.addColorStop(0.9, '#2a1a08'); g.addColorStop(1, '#120c06'); c.fillStyle = g; c.beginPath(); c.arc(w / 2, h / 2, w / 2, 0, 7); c.fill(); });
    region(A.toe, (c, w, h) => { c.fillStyle = P.toe; c.fillRect(0, 0, w, h); c.strokeStyle = 'rgba(110,80,20,0.55)'; c.lineWidth = Math.max(1, h / 60); for (let y = 0; y < h; y += h / 14) { c.beginPath(); c.moveTo(0, y); c.lineTo(w, y + h / 40); c.stroke(); } });
    fill(A.talon, P.talon); fill(A.dark, '#120d0a'); fill(A.metal, '#777'); fill(A.led, '#000');

    // flight feathers
    if (!serp) {
      feather(FS.primD, { base0: P.primDBase, base1: '#3a2a1c', bars: { n: 6, v0: 0.12, v1: 0.82, th: 0.035, col: P.primDBar, a: 0.55 }, tip: [0.82, '#20160f'], barb: '#000', rach: '#c7b79c', rachA: 0.45 });
      feather(FS.primV, { base0: '#f3efe8', base1: '#bdb7ad', bars: { n: 6, v0: 0.2, v1: 0.76, th: 0.03, col: P.primVBar, a: 0.85 }, tip: [0.74, P.primVTip], barb: '#444', barbA: 0.06, rach: '#fff', rachA: 0.5 });
      feather(FS.secD, { base0: '#4f3a27', base1: '#3e2d1f', bars: { n: 6, v0: 0.18, v1: 0.88, th: 0.04, col: '#2b1f15', a: 0.5 }, tip: [0.9, '#2a1e15'], rach: '#bba78a', rachA: 0.35 });
      feather(FS.secV, { base0: P.secVBase, base1: '#c9c6c0', bars: { n: 6, v0: 0.12, v1: 0.74, th: 0.035, col: P.secVBar, a: 0.9 }, band: [0.82, 0.96, '#3a2c22'], barb: '#333', barbA: 0.05, rach: '#fff', rachA: 0.4 });
      feather(FS.tailD, { base0: '#6f5944', base1: '#5f4a36', bars: { n: 4, v0: 0.22, v1: 0.86, th: 0.07, col: P.tailBand, a: 0.9 }, tip: [0.95, '#c9b99c'], rach: '#d6c6a8', rachA: 0.4 });
      feather(FS.tailV, { base0: P.tailV, base1: '#cbc4b8', bars: { n: 4, v0: 0.22, v1: 0.86, th: 0.07, col: P.tailVBand, a: 0.85 }, tip: [0.96, '#efe9df'], barb: '#555', barbA: 0.05, rach: '#fff', rachA: 0.4 });
      feather(FS.covD, { base0: '#4d3826', base1: '#3f2d1f', fringe: P.fringe, rach: '#8a7356', rachA: 0.3 });
      feather(FS.pcovD, { base0: '#3f2e20', base1: '#2f2218', fringe: '#5e4630', rach: '#8a7356', rachA: 0.3 });
      feather(FS.terD, { base0: '#4a3624', base1: '#3a2a1d', bars: { n: 4, v0: 0.2, v1: 0.85, th: 0.05, col: '#2a1e14', a: 0.45 }, fringe: '#6d5238', rachA: 0.3 });
      feather(FS.covV, { base0: P.uw, base1: '#a35e33', bars: { n: 3, v0: 0.2, v1: 0.85, th: 0.08, col: P.uwBar, a: 0.75 }, barbA: 0.05, rachA: 0.2 });
      feather(FS.uwV, { base0: '#efe7da', base1: '#c58b5c', bars: { n: 4, v0: 0.15, v1: 0.9, th: 0.07, col: P.uw, a: 0.8 }, barbA: 0.05, rachA: 0.2 });
      feather(FS.armD, { base0: '#4a3524', base1: '#3a2a1c', spots: { n: 26, col: '#5f4630', r: 0.06 }, barbA: 0.05, rachA: 0 });
      feather(FS.armV, { base0: '#b06a3b', base1: '#c27a45', spots: { n: 30, col: '#efe3cf', r: 0.05 }, barbA: 0.04, rachA: 0 });
    } else {
      feather(FS.primD, { base0: '#2b2119', base1: '#1c1712', band: [0.36, 0.5, '#5a4a3a'], tip: [0.86, '#120f0c'], rach: '#6b5a48', rachA: 0.4 });
      feather(FS.primV, { base0: '#1d1814', base1: '#16120f', band: [0.34, 0.52, '#efe8dc'], tip: [0.85, '#0e0c0a'], barb: '#000', barbA: 0.06, rach: '#ddd', rachA: 0.35 });
      feather(FS.secD, { base0: '#2b2119', base1: '#221a14', band: [0.34, 0.5, '#5a4a3a'], tip: [0.9, '#5a4a3a'], rach: '#6b5a48', rachA: 0.35 });
      feather(FS.secV, { base0: '#1e1915', base1: '#18140f', band: [0.36, 0.56, '#efe8dc'], tip: [0.9, '#d8d0c2'], barb: '#000', barbA: 0.05, rach: '#ddd', rachA: 0.35 });
      feather(FS.tailD, { base0: '#1d1814', base1: '#16120f', band: [0.38, 0.6, '#e9e1d2'], tip: [0.95, '#d8d0c2'], rach: '#777', rachA: 0.35 });
      feather(FS.tailV, { base0: '#1e1915', base1: '#18140f', band: [0.38, 0.6, '#f0e9dd'], tip: [0.95, '#f0e9dd'], barb: '#000', barbA: 0.05, rach: '#ddd', rachA: 0.35 });
      feather(FS.covD, { base0: '#3a2a1d', base1: '#30231a', spots: { n: 3, col: '#efe8dc', r: 0.07 }, rachA: 0.2 });
      feather(FS.pcovD, { base0: '#2a2018', base1: '#221a14', rachA: 0.2 });
      feather(FS.terD, { base0: '#33261b', base1: '#2a1f17', spots: { n: 2, col: '#e8dcc4', r: 0.06 }, rachA: 0.2 });
      feather(FS.covV, { base0: '#4a3424', base1: '#3a281c', spots: { n: 6, col: '#e8dcc4', r: 0.06 }, rachA: 0.2 });
      feather(FS.uwV, { base0: '#4a3424', base1: '#3a281c', spots: { n: 8, col: '#e8dcc4', r: 0.05 }, rachA: 0.2 });
      feather(FS.armD, { base0: '#3a2a1d', base1: '#30231a', spots: { n: 22, col: '#e8dcc4', r: 0.035 }, rachA: 0 });
      feather(FS.armV, { base0: '#4a3424', base1: '#3a281c', spots: { n: 30, col: '#e8dcc4', r: 0.035 }, rachA: 0 });
    }
    return cv;
  }
  function mix(a, b, t) {
    const pa = parseInt(a.slice(1), 16), pb = parseInt(b.slice(1), 16), f = (p, s) => (p >> s) & 255;
    const c = s => Math.round(lerp(f(pa, s), f(pb, s), t));
    return '#' + ((1 << 24) | (c(16) << 16) | (c(8) << 8) | c(0)).toString(16).slice(1);
  }

  // ---------- geometry builder: model-space vertices with up to two bone weights, three material slots ----------
  class Builder {
    constructor() { this.p = []; this.n = []; this.uv = []; this.c = []; this.si = []; this.sw = []; this.ix = [[], [], []]; this.k = 0; }
    v(p, nrm, uv, b0, b1 = b0, w1 = 0, col = 1) {
      this.p.push(p.x, p.y, p.z); this.n.push(nrm.x, nrm.y, nrm.z); this.uv.push(uv[0], uv[1]);
      const cc = Array.isArray(col) ? col : [col, col, col]; this.c.push(cc[0], cc[1], cc[2]);
      this.si.push(b0, w1 > 0 ? b1 : 0, 0, 0); this.sw.push(1 - w1, w1, 0, 0); return this.k++;
    }
    tri(slot, a, b, c) { this.ix[slot].push(a, b, c); }
    // copy another builder mirrored across x = 0, remapping bones (left -> right)
    mirror(src, map) {
      const o = this.k;
      for (let i = 0; i < src.k; i++) {
        this.p.push(-src.p[i * 3], src.p[i * 3 + 1], src.p[i * 3 + 2]); this.n.push(-src.n[i * 3], src.n[i * 3 + 1], src.n[i * 3 + 2]);
        this.uv.push(src.uv[i * 2], src.uv[i * 2 + 1]); this.c.push(src.c[i * 3], src.c[i * 3 + 1], src.c[i * 3 + 2]);
        this.si.push(map(src.si[i * 4]), map(src.si[i * 4 + 1]), 0, 0); this.sw.push(src.sw[i * 4], src.sw[i * 4 + 1], 0, 0);
      }
      for (let s = 0; s < 3; s++) { const a = src.ix[s]; for (let i = 0; i < a.length; i += 3) this.ix[s].push(o + a[i], o + a[i + 2], o + a[i + 1]); }
      this.k += src.k;
    }
    append(src) { const o = this.k; this.p.push(...src.p); this.n.push(...src.n); this.uv.push(...src.uv); this.c.push(...src.c); this.si.push(...src.si); this.sw.push(...src.sw);
      for (let s = 0; s < 3; s++) for (const i of src.ix[s]) this.ix[s].push(o + i); this.k += src.k; }
    build() {
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.Float32BufferAttribute(this.p, 3));
      g.setAttribute('normal', new THREE.Float32BufferAttribute(this.n, 3));
      g.setAttribute('uv', new THREE.Float32BufferAttribute(this.uv, 2));
      g.setAttribute('color', new THREE.Float32BufferAttribute(this.c, 3));
      g.setAttribute('skinIndex', new THREE.Uint16BufferAttribute(this.si, 4));
      g.setAttribute('skinWeight', new THREE.Float32BufferAttribute(this.sw, 4));
      const all = []; let start = 0;
      for (let s = 0; s < 3; s++) { const a = this.ix[s]; if (!a.length) continue; g.addGroup(start, a.length, s); for (let i = 0; i < a.length; i++) all.push(a[i]); start += a.length; }
      g.setIndex(all); g.computeBoundingSphere(); g.computeBoundingBox(); return g;
    }
  }

  // ---------- skeleton ----------
  // Left-side bones are built and posed; right-side bones are their mirror images (x -> -x).
  function mirrorQ(q) { q.y = -q.y; q.z = -q.z; return q; }
  const AX = new THREE.Vector3(1, 0, 0), AY = new THREE.Vector3(0, 1, 0), AZ = new THREE.Vector3(0, 0, 1);
  const _q = new THREE.Quaternion(), _q2 = new THREE.Quaternion(), _v = new THREE.Vector3(), _v2 = new THREE.Vector3(), _m = new THREE.Matrix4();
  const qa = (q, axis, a) => q.setFromAxisAngle(axis, a);
  // q = Rz(z) * Ry(y) * Rx(x)
  function qZYX(out, z, y, x) { qa(out, AZ, z); out.multiply(qa(_q2, AY, y)); return out.multiply(qa(_q2, AX, x)); }

  class Rig {
    constructor() {
      this.bones = []; this.by = {}; this.pairs = [];   // pairs: [leftIndex, rightIndex]
      const B = (name, parent, pos) => { const b = new THREE.Bone(); b.name = name; b.position.set(pos[0], pos[1], pos[2]); if (parent) parent.add(b); b.userData.i = this.bones.length; this.bones.push(b); this.by[name] = b; return b; };
      const root = B('root', null, [0, 0, 0]), body = B('body', root, [0, 0, 0]);
      const neck = B('neck', body, RIG.neck), head = B('head', neck, RIG.head);
      const tail = B('tail', body, RIG.tail);
      this.root = root;
      // sided chains, built for both sides at once
      for (const s of ['L', 'R']) {
        const m = s === 'L' ? 1 : -1, sh = RIG.shoulder;
        const hum = B('hum' + s, body, [sh[0] * m, sh[1], sh[2]]);
        const fore = B('fore' + s, hum, [RIG.hum * m, 0, 0]);
        const hand = B('hand' + s, fore, [RIG.fore * m, 0, 0]);
        PRIM.forEach((f, i) => B('P' + (i + 1) + s, hand, [f.t * RIG.hand * m, 0, 0]));
        SEC.forEach((f, i) => B('S' + (i + 1) + s, fore, [f.t * RIG.fore * m, 0, 0]));
        HUM.forEach((f, i) => B('H' + (i + 1) + s, hum, [f.t * RIG.hum * m, 0, 0]));
        RECT.forEach((L, i) => B('T' + (i + 1) + s, tail, [0, 0, 0]));
        const thigh = B('thigh' + s, body, [RIG.hip[0] * m, RIG.hip[1], RIG.hip[2]]);
        const tars = B('tarsus' + s, thigh, [0, -RIG.tibia, 0]);
        TOES.forEach((t, i) => { const a = B('toe' + i + 'a' + s, tars, [0, -RIG.tarsus, 0]); B('toe' + i + 'b' + s, a, [0, 0, t[1]]); });
      }
      for (const b of this.bones) if (b.name.endsWith('L')) this.pairs.push([b.userData.i, this.by[b.name.slice(0, -1) + 'R'].userData.i]);
      this.mapLR = new Map(this.pairs);
      this.skeleton = null;
    }
    // set a left-side bone and its mirrored right-side twin (the right side may get its own rotation)
    setL(name, pos, q, qR) {
      const L = this.by[name + 'L'], R = this.by[name + 'R'];
      if (pos) { L.position.set(pos.x, pos.y, pos.z); R.position.set(-pos.x, pos.y, pos.z); }
      L.quaternion.copy(q); R.quaternion.copy(qR || q); mirrorQ(R.quaternion);
    }
  }

  // ---------- pose ----------
  // Every number the animation drives. Angles in radians.
  function basePose() {
    return { bodyPitch: 0, bodyRoll: 0, bodyY: 0, neckPitch: 0, headPitch: 0, headYaw: 0, headRoll: 0,
      fold: 0, flex: 0, flap: 8 * DEG, sweep: 0, twist: 0, handTwist: 0, slot: 0.2, cup: 0, primLift: 0, asym: 0,
      tailSpread: 0.1, tailPitch: 0, legTuck: 1, legReach: 0, toeCurl: 1, crouch: 0, toeOpen: 0 };
  }
  const POSES = {
    // cruise flapping: amplitude and mean are added by the stroke cycle
    fly: { fold: 0, flap: 10 * DEG, slot: 0.2, tailSpread: 0.08, tailPitch: 0, legTuck: 1, bodyPitch: 4 * DEG, neckPitch: -4 * DEG, headPitch: 0 },
    glide: { fold: 0.04, flap: 5 * DEG, sweep: 5 * DEG, slot: 0.25, tailSpread: 0.12, legTuck: 1, bodyPitch: 2 * DEG, neckPitch: -4 * DEG, primLift: 4 * DEG },
    soar: { fold: 0, flap: 9 * DEG, sweep: -3 * DEG, slot: 1, tailSpread: 0.75, legTuck: 1, bodyPitch: 3 * DEG, neckPitch: -6 * DEG, primLift: 9 * DEG },
    perched: { fold: 1, flap: 0, sweep: 0, slot: 0, tailSpread: 0, tailPitch: -22 * DEG, legTuck: 0, toeCurl: 1, bodyPitch: 52 * DEG, neckPitch: -34 * DEG, headPitch: -16 * DEG },
    // wings out but still on the perch (drying / mantling, used while landing and before take-off)
    spread: { fold: 0.05, flap: 22 * DEG, sweep: -10 * DEG, slot: 0.8, tailSpread: 0.6, tailPitch: -18 * DEG, legTuck: 0, toeCurl: 1, bodyPitch: 40 * DEG, neckPitch: -28 * DEG, headPitch: -10 * DEG },
  };

  // ---------- the bird ----------
  const CACHE = new Map();   // livery|quality -> { geometry, materials, atlas }

  class EagleDrone {
    constructor(opt = {}) {
      this.opt = Object.assign({ livery: 'hawkEagle', quality: 'high', shadows: true }, opt);
      this.rig = new Rig();
      this.object = new THREE.Group(); this.object.name = 'EagleDrone';
      this.pose = basePose(); this.target = basePose();
      this.phase = 0; this.flapHz = SPEC.flapHz; this.flapAmp = 0; this.time = 0;
      this.mode = 'fly'; this.track = null; this.speedScale = 1;
      this.look = { yaw: 0, pitch: 0, tYaw: 0, tPitch: 0, next: 0, target: null };
      this._prev = null; this._vel = new THREE.Vector3(); this._heading = 0; this._turn = 0;
      this.bindPose();
      this.meshes = {}; this._solid = {}; this._ghost = {};
      this.setLivery(this.opt.livery);
      // measure the perched stance once, then take the requested mode
      this.setMode('perched', true); this.applyPose();
      this.perchOffset = this.measurePerchOffset();
      this.eyeHeight = this.headWorld().y + this.perchOffset;   // eye above the branch while perched
      this.setMode(this.opt.mode || 'perched', true); this.applyPose();
    }

    // ----- building -----
    bindPose() {
      // flight posture, wings spread flat, tail closed, legs half down: the pose all geometry is authored in
      const p = basePose(); p.flap = 0; p.slot = 0; p.tailSpread = 0; p.legTuck = 0.35; p.toeCurl = 0.2;
      this.pose = p; this.phase = 0; this.flapAmp = 0; this.applyPose(true);
      this.rig.root.updateMatrixWorld(true);
      this.bind = this.rig.bones.map(b => b.matrixWorld.clone());
    }
    setLivery(name) {
      if (!LIVERIES[name]) name = 'hawkEagle';
      this.livery = name;
      if (!this.meshes[name]) this.meshes[name] = this.makeMesh(name);
      for (const k in this.meshes) this.meshes[k].visible = k === name || (!!this.xray && k === 'airframe');
      if (this.xray) this.setXray(true);
      return this;
    }
    setXray(on) {
      this.xray = !!on;
      if (on && !this.meshes.airframe) this.meshes.airframe = this.makeMesh('airframe');
      for (const k in this.meshes) {
        const m = this.meshes[k], dis = LIVERIES[k].disguise;
        m.visible = k === this.livery || (!!on && k === 'airframe');
        if (dis) { if (on && k === this.livery) { if (!this._ghost[k]) this._ghost[k] = this._solid[k].map(mm => { const g = mm.clone(); g.transparent = true; g.opacity = 0.22; g.depthWrite = false; g.skinning = true; return g; }); m.material = this._ghost[k]; }
          else m.material = this._solid[k]; }
      }
      return this;
    }
    makeMesh(livery) {
      const key = livery + '|' + this.opt.quality;
      let c = CACHE.get(key);
      if (!c) { c = buildLivery(this, livery, this.opt.quality); CACHE.set(key, c); }
      const mesh = new THREE.SkinnedMesh(c.geometry, c.materials);
      mesh.name = 'EagleDrone_' + livery; mesh.frustumCulled = false; mesh.castShadow = !!this.opt.shadows; mesh.receiveShadow = true;
      this._solid[livery] = c.materials;   // kept off userData so exporters do not serialise it
      if (!this.rig.skeleton) {
        // bones live under the group; bind every livery to the same skeleton with the bind-pose matrices
        this.object.add(this.rig.root);
        this.rig.skeleton = new THREE.Skeleton(this.rig.bones, this.bind.map(m => m.clone().invert ? m.clone().invert() : new THREE.Matrix4().getInverse(m)));
      }
      this.object.add(mesh);
      mesh.bind(this.rig.skeleton, new THREE.Matrix4());
      return mesh;
    }
    get mesh() { return this.meshes[this.livery]; }

    // ----- pose -> bones -----
    applyPose(binding) {
      const P = this.pose, rig = this.rig, by = rig.by;
      // stroke cycle: phase 0 = top of the upstroke, PI = bottom of the downstroke
      const ph = this.phase, amp = binding ? 0 : this.flapAmp, an = clamp(amp / (40 * DEG), 0, 1.4);
      const strokeFlap = amp * Math.cos(ph), up = Math.max(0, -Math.sin(ph)), down = Math.max(0, Math.sin(ph));
      // body
      by.body.position.set(0, P.bodyY + (binding ? 0 : 0.012 * an * -Math.cos(ph) * (1 - P.fold)), -0.02 * P.crouch);
      qZYX(by.body.quaternion, P.bodyRoll, 0, -P.bodyPitch - 3 * DEG * an * Math.sin(ph) * (1 - P.fold));
      // neck and head: the head stays steady while the body bobs
      qa(by.neck.quaternion, AX, -P.neckPitch + 3 * DEG * an * Math.sin(ph) * (1 - P.fold)); by.neck.quaternion.multiply(qa(_q, AY, P.headYaw * 0.35));
      by.head.position.set(0, RIG.head[1], RIG.head[2]);
      qa(by.head.quaternion, AY, P.headYaw * 0.65); by.head.quaternion.multiply(qa(_q, AX, -P.headPitch)); by.head.quaternion.multiply(qa(_q, AZ, P.headRoll));
      // wings
      for (const side of [0, 1]) {
        const sg = side ? -1 : 1, asym = P.asym * sg;
        const f = clamp(P.fold, 0, 1), fe = clamp(Math.max(f, (P.flex + 0.55 * up * an * 0.6) * (1 - f)), 0, 1);
        const sweep = lerp(18 * DEG + P.sweep - 10 * DEG * P.cup - 8 * DEG * an * (down - up) * 0.5, FOLD.sweep * DEG, smooth(0, 0.75, f));
        const roll = FOLD.roll * DEG * smooth(0.25, 1, f) + 22 * DEG * Math.sin(Math.PI * smooth(0, 1, f)) * (1 - f);
        const flap = (P.flap + strokeFlap + asym) * (1 - smooth(0, 0.6, f));
        const twist = (P.twist + 10 * DEG * an * Math.sin(ph) + 8 * DEG * P.cup) * (1 - f);
        const qS = qZYX(new THREE.Quaternion(), flap + roll, sweep, twist);
        const elbow = lerp(-30 * DEG, FOLD.elbow * DEG, smooth(0, 1, fe)), wrist = lerp(22 * DEG, FOLD.wrist * DEG, smooth(0, 1, fe));
        const qE = qa(new THREE.Quaternion(), AY, elbow).multiply(qa(_q, AZ, lerp(0, 5 * DEG, f)));
        const qW = qa(new THREE.Quaternion(), AY, wrist).multiply(qa(_q, AX, (P.handTwist + 14 * DEG * an * Math.sin(ph)) * (1 - f)));
        const qW2 = qW.clone().multiply(qa(_q, AZ, lerp(0, 3 * DEG, f) + 7 * DEG * an * -Math.cos(ph) * (1 - f)));
        if (side === 0) { this._qS = qS; this._qE = qE; this._qW = qW2; this._fe = fe; this._f = f; }
        else { rig.setL('hum', null, this._qS, qS); rig.setL('fore', null, this._qE, qE); rig.setL('hand', null, this._qW, qW2); this._qSR = qS; this._feR = fe; }
      }
      // feathers (left computed; right mirrors left unless asymmetric)
      const fe = this._fe, f = this._f, slot = P.slot * (1 - f), sF = smooth(0, 1, fe), lift = (P.primLift + 6 * DEG * an * up) * (1 - f);
      PRIM.forEach((pr, i) => {
        const spread = pr.a * DEG + slot * (i - 4.5) * 2.2 * DEG + (up * an * 6 * DEG) * (i / 9);
        const th = lerp(spread, pr.f * DEG, sF), y = 0.0007 * (9 - i) + lerp(0, 0.0007 * (9 - i), f);
        qa(_q, AY, th).multiply(qa(_q2, AZ, lift * (0.3 + 0.7 * i / 9) * (1 - sF)));
        rig.setL('P' + (i + 1), _v.set(pr.t * RIG.hand, y, -0.006), _q);
      });
      SEC.forEach((s, i) => {
        const th = lerp(s.a * DEG, s.f * DEG, sF), y = 0.0082 + 0.0007 * i + lerp(0, 0.0008 * i + 0.004, f);
        qa(_q, AY, th).multiply(qa(_q2, AX, lerp(0, -4 * DEG, f)));
        rig.setL('S' + (i + 1), _v.set(s.t * RIG.fore, y, -0.008), _q);
      });
      HUM.forEach((h, i) => {
        const th = lerp(h.a * DEG, h.f * DEG, smooth(0, 1, f)), y = 0.026 + 0.0008 * i + lerp(0, 0.004 + 0.002 * i, f);
        qa(_q, AY, th); rig.setL('H' + (i + 1), _v.set(h.t * RIG.hum, y, -0.010), _q);
      });
      // tail fan
      qa(by.tail.quaternion, AX, -P.tailPitch);
      RECT.forEach((L, i) => {
        const a = P.tailSpread * (4 + 8.6 * i) * DEG, x = 0.004 + 0.0062 * i * (1 - 0.25 * P.tailSpread);
        qa(_q, AY, 90 * DEG - a).multiply(qa(_q2, AX, (3.2 - 1.6 * P.tailSpread) * DEG * i));
        rig.setL('T' + (i + 1), _v.set(x, 0.006 - (0.0024 - 0.0012 * P.tailSpread) * i, 0.012), _q);
      });
      // legs: angles in the world's vertical plane, corrected for body pitch
      const tuck = smooth(0, 1, P.legTuck), reach = P.legReach, crouch = P.crouch;
      const thighW = lerp(lerp(26 * DEG, -38 * DEG, reach) - 22 * DEG * crouch, 80 * DEG, tuck);
      const tarsW = lerp(lerp(-8 * DEG, -55 * DEG, reach) + 30 * DEG * crouch, -100 * DEG, tuck);
      const pitchCorr = P.bodyPitch * (1 - tuck) + P.bodyPitch * tuck * 0;
      const qT = qa(new THREE.Quaternion(), AX, thighW + pitchCorr).multiply(qa(_q, AZ, lerp(-6 * DEG, -2 * DEG, tuck)));
      rig.setL('thigh', null, qT);
      const qTa = qa(new THREE.Quaternion(), AX, tarsW - thighW);
      rig.setL('tarsus', null, qTa);
      const curl = clamp(P.toeCurl, 0, 1), open = P.toeOpen;
      TOES.forEach((t, i) => {
        const back = t[0] === 180, yaw = (back ? 180 : t[0] * (1 + 0.5 * open)) * DEG;
        // toe base: level in the world when perched, folded under when tucked
        const level = -tarsW + lerp(0, -70 * DEG, tuck);
        const qa_ = qa(new THREE.Quaternion(), AX, level * (back ? 1 : 1)).multiply(qa(_q, AY, yaw)).multiply(qa(_q2, AX, (curl * 52 - open * 18) * DEG));
        rig.setL('toe' + i + 'a', null, qa_);
        rig.setL('toe' + i + 'b', null, qa(new THREE.Quaternion(), AX, (curl * 64 - open * 10) * DEG));
      });
    }

    // ----- modes and maneuvers -----
    setMode(mode, instant) {
      this.mode = mode;
      const t = Object.assign(basePose(), POSES[mode] || POSES.fly);
      this.target = t;
      this.targetAmp = mode === 'fly' ? 36 * DEG : mode === 'takeoff' ? 46 * DEG : 0;
      if (instant) { Object.assign(this.pose, t); this.flapAmp = this.targetAmp; }
      return this;
    }
    // stand on a perch: pos = where the toes grip (top of the branch), heading in radians (0 = facing +Z)
    perch(pos, heading = 0) {
      this.track = null; this.setMode('perched', true); this.applyPose();
      this.object.position.copy(pos).add(_v.set(0, this.perchOffset, 0)); this.object.rotation.set(0, heading, 0); this._heading = heading;
      this._prev = null; return this;
    }
    // fly along a path of points; opts: { duration (s) | speed (m/s), takeoff: bool, land: { pos, heading }, onDone }
    follow(points, opts = {}) {
      const pts = points.map(p => p.clone());
      if (opts.land) pts.push(opts.land.pos.clone().add(_v.set(0, this.perchOffset, 0)));
      if (opts.takeoff) pts[0] = this.object.position.clone();
      const segs = []; let total = 0;
      for (let i = 1; i < pts.length; i++) { const d = pts[i].distanceTo(pts[i - 1]); segs.push(d); total += d; }
      const dur = opts.duration || total / (opts.speed || SPEC.cruise_ms);
      this.track = { pts, segs, total: Math.max(total, 1e-3), dur: Math.max(dur, 0.3), t: 0, takeoff: !!opts.takeoff, land: opts.land || null, onDone: opts.onDone || null,
        ease: opts.ease || 'auto', landFold: 0, glide: !!opts.glide };
      this._prev = null;
      if (opts.takeoff) this.setMode('takeoff'); else this.setMode(opts.glide ? 'glide' : 'fly');
      return this;
    }
    // approach and land on a perch from a point in the air
    land(perchPos, heading, from, opts = {}) {
      const end = perchPos.clone(), dir = new THREE.Vector3(Math.sin(heading), 0, Math.cos(heading));
      const a = end.clone().addScaledVector(dir, -14).add(_v.set(0, -2.2, 0)), b = end.clone().addScaledVector(dir, -5).add(_v.set(0, -0.9, 0));
      const pts = from ? [from.clone(), ...(opts.via || []).map(p => p.clone()), a, b] : [a, b];
      if (!from) this.object.position.copy(a);
      return this.follow(pts, Object.assign({ land: { pos: perchPos.clone(), heading } }, opts));
    }
    takeoff(points, opts = {}) { return this.follow(points, Object.assign({ takeoff: true }, opts)); }
    // on the perch: open the wings (mantling, drying) or fold them again
    spreadWings(on) { if (this.mode !== 'perched') return this; this._settle = null; this.target = Object.assign(basePose(), on ? POSES.spread : POSES.perched); this.targetAmp = 0; return this; }
    // hold still in the air (a test stand) and flap, glide or soar
    hover(mode = 'fly') { this.track = null; this._settle = null; this.setMode(mode); return this; }
    // cancel a maneuver (its onDone is not called)
    stop() { this.track = null; this._settle = null; return this; }
    setHeading(h) { this._heading = h; this.object.rotation.y = h; return this; }
    // look at a world point while perched (null = scan)
    lookAt(p) { this.look.target = p ? p.clone() : null; return this; }

    // positional helpers for cameras
    headWorld(out = new THREE.Vector3()) { this.object.updateMatrixWorld(true); return out.setFromMatrixPosition(this.rig.by.head.matrixWorld); }
    measurePerchOffset() {
      this.object.position.set(0, 0, 0); this.object.rotation.set(0, 0, 0); this.object.updateMatrixWorld(true);
      let low = Infinity; for (let i = 0; i < 4; i++) for (const s of ['L', 'R']) { const b = this.rig.by['toe' + i + 'a' + s]; _v.setFromMatrixPosition(b.matrixWorld); low = Math.min(low, _v.y); }
      return -low + 0.012;
    }

    update(dt) {
      dt = Math.min(dt, 0.25) * this.speedScale; this.time += dt;
      const tr = this.track, P = this.pose;
      let T = this.target, ampT = this.targetAmp, hz = this.flapHz;
      if (tr) {
        tr.t += dt; const k = clamp(tr.t / tr.dur, 0, 1);
        // speed profile: slow start after take-off, slow finish into a landing
        const easeIn = (x, a) => x < a ? 0.5 * x * x / a / (1 - a / 2) : (x - a / 2) / (1 - a / 2);
        const ek = tr.takeoff && tr.land ? smooth(0, 1, k) : tr.takeoff ? easeIn(k, 0.3) : tr.land ? 1 - easeIn(1 - k, 0.35) : k;
        const s = ek * tr.total; let acc = 0, i = 0; while (i < tr.segs.length - 1 && acc + tr.segs[i] < s) { acc += tr.segs[i]; i++; }
        const f = tr.segs[i] > 0 ? (s - acc) / tr.segs[i] : 0, pos = tr.pts[i].clone().lerp(tr.pts[i + 1], clamp(f, 0, 1));
        const dir = tr.pts[i + 1].clone().sub(tr.pts[i]);
        this.object.position.copy(pos);
        const tleft = (1 - k) * tr.dur, telapsed = tr.t;
        // heading follows the path (turn rate gives the bank); on the last metres of a landing it settles on the perch heading
        let hd = Math.hypot(dir.x, dir.z) > 0.25 * dir.length() ? Math.atan2(dir.x, dir.z) : this._heading; if (tr.land && tleft < 1.2) hd = this._heading + wrapPi(tr.land.heading - this._heading) * smooth(1.2, 0.2, tleft);
        const dh = wrapPi(hd - this._heading); this._turn = damp(this._turn, dh / Math.max(dt, 1e-3), 3, dt); this._heading = wrapPi(this._heading + dh * (1 - Math.exp(-8 * dt)));
        this.object.rotation.set(0, this._heading, 0);
        const horiz = Math.hypot(dir.x, dir.z) || 1e-6, climb = Math.atan2(dir.y, horiz);
        const spd = tr.total / tr.dur;
        if (tr.takeoff && telapsed < 0.9) {
          // crouch, spring, unfold, first hard strokes
          const k2 = telapsed / 0.9; T = Object.assign(basePose(), POSES.spread, { crouch: smooth(0, 0.25, k2) * (1 - smooth(0.25, 0.45, k2)), fold: 1 - smooth(0.1, 0.5, k2), legTuck: smooth(0.45, 1, k2), bodyPitch: lerp(48, 18, smooth(0.2, 0.9, k2)) * DEG, flap: 20 * DEG, slot: 0.6, tailSpread: 0.7, neckPitch: -14 * DEG });
          ampT = 48 * DEG * smooth(0.3, 0.55, k2); hz = 3.4;
        } else if (tr.land && tleft < 1.15) {
          // flare: body up, wings cupped forward braking, tail fanned down, feet thrown forward, toes open
          const k3 = 1 - tleft / 1.15;
          T = Object.assign(basePose(), { fold: 0, flap: 20 * DEG, sweep: -14 * DEG, cup: smooth(0, 0.6, k3), slot: 1, tailSpread: 1, tailPitch: -28 * DEG * smooth(0, 0.5, k3), legTuck: 1 - smooth(0, 0.35, k3),
            legReach: smooth(0.1, 0.6, k3) * (1 - smooth(0.85, 1, k3)), toeCurl: 1 - smooth(0, 0.3, k3), toeOpen: smooth(0.1, 0.4, k3), bodyPitch: lerp(10, 58, smooth(0, 0.8, k3)) * DEG, neckPitch: -lerp(6, 34, smooth(0, 0.8, k3)) * DEG, headPitch: -lerp(0, 22, k3) * DEG });
          ampT = 30 * DEG * smooth(0.25, 0.55, k3) * (1 - smooth(0.9, 1, k3)); hz = 3.3;
        } else {
          const glideNow = tr.glide || (tr.land && tleft < 3.2) || (!tr.takeoff && climb < -0.12);
          T = Object.assign(basePose(), glideNow ? POSES.glide : POSES.fly, { bodyPitch: (glideNow ? 2 : 4) * DEG + climb * 0.7 });
          ampT = glideNow ? 0 : lerp(30, 44, clamp(climb / 0.5, 0, 1)) * DEG; hz = SPEC.flapHz * (1 + 0.12 * clamp(climb / 0.5, 0, 1));
        }
        T.bodyRoll = clamp(-this._turn * spd / 9.81, -0.75, 0.75);
        if (k >= 1) {
          const done = tr.onDone, land = tr.land; this.track = null;
          if (land) { this.object.position.copy(land.pos).add(_v.set(0, this.perchOffset, 0)); this._heading = land.heading; this.object.rotation.set(0, land.heading, 0); this.setMode('perched'); this._settle = 0; }
          else this.setMode(tr.takeoff || this.mode === 'fly' ? 'fly' : this.mode);
          if (done) done();
        }
      } else if (this.mode === 'perched') {
        // after touch-down: two balancing beats with the wings out, then fold
        if (this._settle != null) { this._settle += dt; const k4 = this._settle;
          if (k4 < 0.55) { T = Object.assign(basePose(), POSES.spread, { flap: 18 * DEG }); ampT = 24 * DEG * (1 - k4 / 0.55); hz = 3.2; }
          else { T = Object.assign(basePose(), POSES.perched); if (k4 > 2.5) this._settle = null; } }
        else T = this.target;
        // scanning head: quick turns, then holds (raptors move the head, not the eyes)
        const L = this.look; if (this.time > L.next) { L.next = this.time + 0.9 + Math.random() * 2.4;
          if (L.target) { this.object.updateMatrixWorld(true); const lp = this.object.worldToLocal(L.target.clone()); L.tYaw = clamp(Math.atan2(lp.x, lp.z), -1.6, 1.6) + (Math.random() - 0.5) * 0.25; L.tPitch = clamp(-Math.atan2(lp.y, Math.hypot(lp.x, lp.z)), -0.6, 0.7); }
          else { L.tYaw = (Math.random() - 0.5) * 2.6; L.tPitch = (Math.random() - 0.3) * 0.5; } }
        L.yaw = damp(L.yaw, L.tYaw, 14, dt); L.pitch = damp(L.pitch, L.tPitch, 14, dt);
        T = Object.assign({}, T, { headYaw: L.yaw, headPitch: (T.headPitch || 0) + L.pitch * 0.5, headRoll: L.yaw * 0.08 });
      } else if (this.mode === 'auto') {
        // externally moved (the simulator sets the position): bank from turn rate, flap when climbing, glide otherwise
        const p = this.object.position; if (this._prev) { this._vel.subVectors(p, this._prev).divideScalar(Math.max(dt, 1e-3)); } this._prev = (this._prev || new THREE.Vector3()).copy(p);
        const v = this._vel, hs = Math.hypot(v.x, v.z), hd = hs > 0.5 ? Math.atan2(v.x, v.z) : this._heading, dh = wrapPi(hd - this._heading);
        this._turn = damp(this._turn, dh / Math.max(dt, 1e-3), 2.5, dt); this._heading = hd;
        const climb = Math.atan2(v.y, hs || 1);
        const flapping = climb > 0.06 || (this.time % 9) < 2.2;
        T = Object.assign(basePose(), flapping ? POSES.fly : POSES.soar, { bodyPitch: 3 * DEG + climb * 0.6, bodyRoll: clamp(-this._turn * hs / 9.81, -0.7, 0.7) });
        ampT = flapping ? 32 * DEG : 0;
      } else { T = this.target; }
      // ease every pose number toward its target, and the stroke amplitude and rate
      const fast = tr && (tr.takeoff || tr.land) ? 9 : 4;
      for (const key in T) { if (typeof T[key] === 'number' && key in P) P[key] = damp(P[key], T[key], key === 'fold' ? (this.mode === 'perched' ? 3.2 : 6) : key === 'headYaw' || key === 'headPitch' ? 20 : fast, dt); }
      this.flapAmp = damp(this.flapAmp, ampT, 5, dt); this.flapHz = damp(this.flapHz, hz || SPEC.flapHz, 3, dt);
      if (this.flapAmp > 0.01 * DEG) this.phase = (this.phase + 2 * Math.PI * this.flapHz * dt) % (2 * Math.PI);
      else this.phase = damp(this.phase, Math.PI * 0.5, 2, dt);
      this.applyPose();
      return this;
    }

    // sampled clips for export: { name: [ { t, bones: [ {p, q} ] } ] }
    sampleClip(name, seconds, fps, setup) {
      const frames = [], dt = 1 / fps; setup(this);
      for (let i = 0; i <= Math.round(seconds * fps); i++) {
        if (i) this.update(dt);
        frames.push({ t: i * dt, root: { p: this.object.position.clone(), q: this.object.quaternion.clone() }, bones: this.rig.bones.map(b => ({ p: b.position.clone(), q: b.quaternion.clone() })) });
      }
      return frames;
    }
    dispose() { this.object.removeFromParent && this.object.removeFromParent(); }
  }

  // ---------- livery geometry ----------
  function buildLivery(bird, livery, quality) {
    const rig = bird.rig, bind = bird.bind, I = n => rig.by[n].userData.i, hi = quality !== 'low';
    const L = new Builder(), M = new Builder();   // L: left side (mirrored to make the right), M: midline
    const air = livery === 'airframe', serp = livery === 'serpentEagle';
    const tmpP = new THREE.Vector3(), tmpN = new THREE.Vector3(), nm = new THREE.Matrix3();
    // push a local-space vertex for bone b
    const V = (B, b, p, n, uv, col, b1, w1) => {
      tmpP.copy(p).applyMatrix4(bind[b]); nm.setFromMatrix4(bind[b]); tmpN.copy(n).applyMatrix3(nm).normalize();
      return B.v(tmpP, tmpN, uv, b, b1 == null ? b : b1, w1 || 0, col);
    };

    // a feather: base at the bone origin, rachis along +X, outer vane toward +Z, dorsal +Y.
    // o: { L, wo, wi, eo, ei, droop, layers: 'both'|'top'|'bottom', d: rect, vRect: rect, col, colV, y, x0, seg, slot }
    const _fq = new THREE.Quaternion(), _fp = new THREE.Vector3(), _fn = new THREE.Vector3();
    function feather(B, b, o) {
      const seg = o.seg || (hi ? 10 : 6), rows = [];
      const FV = (p, n, uv, c) => { _fp.copy(p); _fn.copy(n); if (o.q) { _fp.applyQuaternion(o.q); _fn.applyQuaternion(o.q); } if (o.at) _fp.add(o.at); return V(B, b, _fp, _fn, uv, c); };
      for (let i = 0; i <= seg; i++) {
        const t = i / seg, base = 0.5 + 0.5 * smooth(0, 0.12, t), tc = o.square ? 0.94 : 0.82, tipR = t > tc ? Math.sqrt(Math.max(0, 1 - Math.pow((t - tc) / (1 - tc), 2))) : 1;
        let wo = o.wo * base * tipR, wi = o.wi * base * tipR;
        if (o.eo) wo *= lerp(1, 0.62, smooth(o.eo - 0.05, o.eo + 0.05, t));
        if (o.ei) wi *= lerp(1, 0.74, smooth(o.ei - 0.05, o.ei + 0.05, t));
        const x = (o.x0 || 0) + t * o.L, y = (o.y || 0) - (o.droop || 0) * t * t * o.L;
        rows.push({ t, x, y, wo, wi });
      }
      const layer = (top) => {
        const idx = [], ny = top ? 1 : -1, dy = top ? 0 : -0.0007, rect = top ? o.d : o.vRect;
        for (const r of rows) {
          const ridge = top ? 0.0012 * (1 - r.t) : 0;
          const col = (top ? o.col : o.colV) || 1, shade = Array.isArray(col) ? col : [col, col, col];
          const dim = 0.82 + 0.18 * smooth(0, 0.3, r.t);
          const c = shade.map(v => v * dim);
          idx.push([
            FV(_v.set(r.x, r.y + dy, r.wo), _v2.set(0, ny, 0.22), uvIn(rect, 0, r.t), c),
            FV(_v.set(r.x, r.y + dy + ridge, 0), _v2.set(0, ny, 0), uvIn(rect, 0.35, r.t), c),
            FV(_v.set(r.x, r.y + dy, -r.wi), _v2.set(0, ny, -0.22), uvIn(rect, 1, r.t), c),
          ]);
        }
        const sl = o.slot || 0;
        for (let i = 0; i < seg; i++) for (let j = 0; j < 2; j++) {
          const a = idx[i][j], bb = idx[i][j + 1], c = idx[i + 1][j], d = idx[i + 1][j + 1];
          if (top) { B.tri(sl, a, c, bb); B.tri(sl, bb, c, d); } else { B.tri(sl, a, bb, c); B.tri(sl, bb, d, c); }
        }
      };
      if (o.layers !== 'bottom') layer(true);
      if (o.layers !== 'top') layer(false);
    }

    // generic loft along a list of rings; each ring: { b, b1, w1, c:[x,y,z] centre in that bone's frame, rx, ryT, ryB, v }
    // the ring is built in bone b's frame; vertices take bone b (and optionally blend to b1)
    function loft(B, rings, rect, o = {}) {
      const segs = o.segs || (hi ? 28 : 16), grid = [];
      rings.forEach(R => {
        const row = [];
        for (let j = 0; j <= segs; j++) {
          const u = j / segs, a = u * 2 * Math.PI, sx = Math.sin(a), cy = -Math.cos(a);
          const ry = cy < 0 ? R.ryB : R.ryT, flat = R.flat || 0;
          const px = R.c[0] + R.rx * sx, py = R.c[1] + ry * cy * (1 - flat * Math.abs(sx) * 0.3), pz = R.c[2];
          const nn = new THREE.Vector3(sx / R.rx, cy / ry, R.nz || 0).normalize();
          const uu = o.uMap ? o.uMap(u) : u;
          row.push(V(B, R.b, new THREE.Vector3(px, py, pz), nn, uvIn(rect, uu, R.v), R.col || 1, R.b1, R.w1));
        }
        grid.push(row);
      });
      const sl = o.slot || 0;
      for (let i = 0; i < grid.length - 1; i++) for (let j = 0; j < segs; j++) {
        const a = grid[i][j], b = grid[i][j + 1], c = grid[i + 1][j], d = grid[i + 1][j + 1];
        B.tri(sl, a, b, c); B.tri(sl, b, d, c);
      }
      // normals from the surface itself so tapering parts (head, tail end) shade correctly
      const P_ = k => new THREE.Vector3(B.p[k * 3], B.p[k * 3 + 1], B.p[k * 3 + 2]);
      for (let i = 0; i < grid.length; i++) {
        const ctr = new THREE.Vector3(); for (let j = 0; j < segs; j++) ctr.add(P_(grid[i][j])); ctr.divideScalar(segs);
        for (let j = 0; j <= segs; j++) {
          const jm = (j - 1 + segs) % segs, jp = (j + 1) % segs, im = Math.max(0, i - 1), ip = Math.min(grid.length - 1, i + 1);
          const tu = P_(grid[i][jp]).sub(P_(grid[i][jm])), tv = P_(grid[ip][j % segs]).sub(P_(grid[im][j % segs]));
          const n = new THREE.Vector3().crossVectors(tv, tu); if (n.lengthSq() < 1e-14) continue; n.normalize();
          if (n.dot(P_(grid[i][j]).sub(ctr)) < 0) n.negate();
          const k = grid[i][j]; B.n[k * 3] = n.x; B.n[k * 3 + 1] = n.y; B.n[k * 3 + 2] = n.z;
        }
      }
      if (o.capStart) { const c0 = grid[0]; for (let j = 1; j < segs - 1; j++) B.tri(sl, c0[0], c0[j + 1], c0[j]); }
      if (o.capEnd) { const c1 = grid[grid.length - 1]; for (let j = 1; j < segs - 1; j++) B.tri(sl, c1[0], c1[j], c1[j + 1]); }
      return grid;
    }
    // a small closed ellipsoid (eyes, joints, LEDs)
    function blob(B, b, c, r, rect, slot = 0, col = 1, uvFn) {
      const n1 = hi ? 10 : 6, n2 = hi ? 14 : 8, rows = [];
      for (let i = 0; i <= n1; i++) { const th = i / n1 * Math.PI, row = [];
        for (let j = 0; j <= n2; j++) { const ph = j / n2 * 2 * Math.PI, nx = Math.sin(th) * Math.cos(ph), ny = Math.cos(th), nz = Math.sin(th) * Math.sin(ph);
          const uv = uvFn ? uvFn(nx, ny, nz) : uvIn(rect, j / n2, 1 - i / n1);
          row.push(V(B, b, new THREE.Vector3(c[0] + r[0] * nx, c[1] + r[1] * ny, c[2] + r[2] * nz), new THREE.Vector3(nx / r[0], ny / r[1], nz / r[2]).normalize(), uv, col)); }
        rows.push(row); }
      for (let i = 0; i < n1; i++) for (let j = 0; j < n2; j++) { const a = rows[i][j], bb = rows[i][j + 1], cc = rows[i + 1][j], d = rows[i + 1][j + 1]; B.tri(slot, a, bb, cc); B.tri(slot, bb, d, cc); }
    }
    // a tapered tube along a list of local points for bone b (toes, talons, struts, the bill)
    function tube(B, b, pts, radii, rect, slot = 0, col = 1, flatten = 1, close = true) {
      const segs = hi ? 8 : 5, rings = [];
      for (let i = 0; i < pts.length; i++) {
        const p = pts[i], q = pts[Math.min(i + 1, pts.length - 1)], pr = pts[Math.max(i - 1, 0)];
        const t = new THREE.Vector3().subVectors(q, pr).normalize(), up = Math.abs(t.y) > 0.9 ? new THREE.Vector3(1, 0, 0) : new THREE.Vector3(0, 1, 0);
        const s1 = new THREE.Vector3().crossVectors(t, up).normalize(), s2 = new THREE.Vector3().crossVectors(s1, t).normalize(), row = [];
        for (let j = 0; j <= segs; j++) { const a = j / segs * 2 * Math.PI, d = s1.clone().multiplyScalar(Math.cos(a)).addScaledVector(s2, Math.sin(a) * flatten);
          row.push(V(B, b, p.clone().addScaledVector(d, radii[i]), d.clone().normalize(), uvIn(rect, j / segs, i / (pts.length - 1)), col)); }
        rings.push(row);
      }
      for (let i = 0; i < rings.length - 1; i++) for (let j = 0; j < segs; j++) { const a = rings[i][j], bb = rings[i][j + 1], c = rings[i + 1][j], d = rings[i + 1][j + 1]; B.tri(slot, a, c, bb); B.tri(slot, bb, c, d); }
      if (close) { const e = rings[rings.length - 1]; const tip = V(B, b, pts[pts.length - 1].clone().addScaledVector(new THREE.Vector3().subVectors(pts[pts.length - 1], pts[pts.length - 2]).normalize(), radii[radii.length - 1]), new THREE.Vector3().subVectors(pts[pts.length - 1], pts[pts.length - 2]).normalize(), uvIn(rect, 0.5, 1), col);
        for (let j = 0; j < segs; j++) B.tri(slot, e[j], tip, e[j + 1]); }
    }
    const v3 = (x, y, z) => new THREE.Vector3(x, y, z);

    // ===== wings (left) =====
    const hum = I('humL'), fore = I('foreL'), hand = I('handL');
    // leading edge: a flattened airfoil tube along each arm bone, wider toward the body
    const armRect = air ? FS.lamD : FS.armD, armRectV = air ? FS.lamV : FS.armV;
    const armSeg = (b, len, th0, th1, f0, f1, back0, back1) => {
      const n = hi ? 6 : 4, segs = hi ? 12 : 8, rows = [];
      for (let i = 0; i <= n; i++) {
        const t = i / n, x = -0.012 + t * (len + 0.024), th = lerp(th0, th1, t), front = lerp(f0, f1, t), back = lerp(back0, back1, t), row = [];
        for (let j = 0; j <= segs; j++) {
          const a = j / segs * 2 * Math.PI, ca = Math.cos(a), sa = Math.sin(a);
          // airfoil-ish: round nose at +Z, thin tail at -Z
          const z = ca > 0 ? front * ca : back * ca, y = th * sa * (ca > 0 ? 1 : 0.35 + 0.65 * (1 + ca));
          const top = sa >= 0, rect = top ? armRect : armRectV;
          row.push(V(L, b, v3(x, y, z), v3(0, sa, ca * 0.6).normalize(), uvIn(rect, (z + back) / (front + back), t), air ? 1 : top ? 0.95 : 1));
        }
        rows.push(row);
      }
      for (let i = 0; i < n; i++) for (let j = 0; j < segs; j++) { const a = rows[i][j], bb = rows[i][j + 1], c = rows[i + 1][j], d = rows[i + 1][j + 1]; L.tri(0, a, c, bb); L.tri(0, bb, c, d); }
    };
    armSeg(hum, RIG.hum, 0.013, 0.010, 0.028, 0.024, 0.030, 0.022);
    armSeg(fore, RIG.fore, 0.0095, 0.0075, 0.024, 0.020, 0.024, 0.016);
    armSeg(hand, RIG.hand, 0.009, 0.005, 0.020, 0.010, 0.016, 0.008);
    blob(L, fore, [0, 0, 0.006], [0.016, 0.0105, 0.022], air ? A.carbon : FS.armD, 0, 0.95);   // elbow
    blob(L, hand, [0, 0, 0.005], [0.013, 0.0085, 0.017], air ? A.carbon : FS.armD, 0, 0.95);   // wrist
    // alula (bastard wing) at the wrist
    if (!air) feather(L, hand, { L: 0.075, wo: 0.006, wi: 0.012, x0: 0.004, y: 0.006, d: FS.pcovD, vRect: FS.uwV, layers: 'both', seg: 4, droop: 0.02 });

    // primaries
    PRIM.forEach((pr, i) => {
      const b = I('P' + (i + 1) + 'L'), shade = 0.9 + 0.1 * (i / 9);
      if (air) {
        feather(L, b, { L: pr.L * 0.97, wo: 0.028, wi: 0.046, d: FS.lamD, vRect: FS.lamV, droop: 0.02, col: 1, square: i < 8 });
      } else {
        feather(L, b, { L: pr.L, wo: pr.wo, wi: pr.wi, eo: pr.eo, ei: pr.ei, d: FS.primD, vRect: FS.primV, droop: 0.035, col: shade, colV: 1 });
        // primary covert on top, underwing covert below
        feather(L, b, { L: pr.L * 0.36, wo: 0.016, wi: 0.030, x0: -0.004, y: 0.0035, d: FS.pcovD, layers: 'top', seg: 4, droop: 0.02, col: 0.95 });
        feather(L, b, { L: pr.L * 0.42, wo: 0.016, wi: 0.032, x0: -0.004, y: -0.0035, vRect: FS.uwV, layers: 'bottom', seg: 4, col: 1 });
      }
    });
    // secondaries and tertials
    SEC.forEach((s, i) => {
      const b = I('S' + (i + 1) + 'L'), ter = i > 9;
      if (air) {
        feather(L, b, { L: 0.236, wo: 0.032, wi: 0.042, d: FS.lamD, vRect: FS.lamV, droop: 0.01, square: true });
        if (i % 2 === 0) feather(L, b, { L: 0.115, wo: 0.034, wi: 0.040, x0: -0.01, y: 0.004, d: A.carbon, layers: 'top', seg: 3 });
        feather(L, b, { L: 0.10, wo: 0.034, wi: 0.040, x0: -0.01, y: -0.004, vRect: FS.lamV, layers: 'bottom', seg: 3 });
      } else {
        feather(L, b, { L: s.L, wo: s.wo, wi: s.wi, d: ter ? FS.terD : FS.secD, vRect: FS.secV, droop: 0.02, col: ter ? 0.95 : 1 });
        feather(L, b, { L: s.L * (ter ? 0.55 : 0.43), wo: 0.020, wi: 0.034, x0: -0.004, y: 0.0035, d: FS.covD, layers: 'top', seg: 4, droop: 0.02 });
        feather(L, b, { L: s.L * 0.26, wo: 0.022, wi: 0.032, x0: -0.008, y: 0.0065, d: FS.covD, layers: 'top', seg: 3, col: 0.9 });
        feather(L, b, { L: s.L * 0.40, wo: 0.020, wi: 0.034, x0: -0.004, y: -0.0035, vRect: FS.covV, layers: 'bottom', seg: 4 });
        feather(L, b, { L: s.L * 0.24, wo: 0.022, wi: 0.032, x0: -0.010, y: -0.0062, vRect: FS.uwV, layers: 'bottom', seg: 3 });
      }
    });
    // humerals (on their own bones) and scapulars (on the back, bound to the body)
    HUM.forEach((h, i) => {
      const b = I('H' + (i + 1) + 'L');
      if (air) feather(L, b, { L: h.L, wo: 0.034, wi: 0.04, d: FS.lamD, vRect: FS.lamV, droop: 0.01 });
      else feather(L, b, { L: h.L, wo: 0.026, wi: 0.04, d: FS.terD, vRect: FS.secV, droop: 0.02, col: 0.93 });
    });
    if (!air) { const bodyB = I('body');
      for (let k = 0; k < 5; k++) {
        const q = new THREE.Quaternion().setFromAxisAngle(AY, (90 + 6 + 3 * k) * DEG).multiply(new THREE.Quaternion().setFromAxisAngle(AZ, -6 * DEG));
        feather(L, bodyB, { L: 0.115 + 0.012 * k, wo: 0.016, wi: 0.026, d: FS.covD, layers: 'top', seg: 4, droop: 0.04, q, at: v3(0.022 + 0.0075 * k, 0.062 - 0.0035 * k, 0.085 - 0.004 * k), col: 0.92 + 0.02 * k });
      }
    }

    // ===== tail (left half, mirrored) =====
    RECT.forEach((len, i) => {
      const b = I('T' + (i + 1) + 'L');
      if (air) { if (i === 0) {
        // kite panel: one half of the diamond tail, bound to the tail bone (tail-bone frame: -Z = back)
        const tb = I('tail'), c = [v3(0, 0.006, 0.07), v3(0.003, 0.006, 0.07), v3(0.17, 0.0, -0.06), v3(0.004, 0.0, -0.31), v3(0, 0.0, -0.31)], uvs = [[0.0, 1], [0.05, 1], [1, 0.5], [0.05, 0], [0, 0]];
        const top = c.map((p, j) => V(L, tb, p, v3(0, 1, 0), uvIn(FS.kite, uvs[j][0], uvs[j][1])));
        const bot = c.map((p, j) => V(L, tb, p.clone().add(v3(0, -0.002, 0)), v3(0, -1, 0), uvIn(FS.kite, uvs[j][0], uvs[j][1])));
        L.tri(0, top[0], top[1], top[2]); L.tri(0, top[0], top[2], top[3]); L.tri(0, top[0], top[3], top[4]);
        L.tri(0, bot[0], bot[2], bot[1]); L.tri(0, bot[0], bot[3], bot[2]); L.tri(0, bot[0], bot[4], bot[3]);
      } return; }
      feather(L, b, { L: len, wo: i === 0 ? 0.027 : 0.022, wi: i === 0 ? 0.027 : 0.035, d: FS.tailD, vRect: FS.tailV, droop: -0.005, col: 1 - 0.02 * i, seg: hi ? 9 : 6 });
    });
    if (!air) {
      // upper and under tail coverts on the tail bone
      const tb = I('tail');
      for (let k = 0; k < 3; k++) {
        const q = new THREE.Quaternion().setFromAxisAngle(AY, (90 - 7 * k) * DEG);
        feather(L, tb, { L: 0.13 - 0.018 * k, wo: 0.016, wi: 0.024, d: FS.covD, layers: 'top', seg: 4, droop: 0.02, q, at: v3(0.004 + 0.011 * k, 0.013 - 0.001 * k, 0.03), col: 0.95 });
        feather(L, tb, { L: 0.12 - 0.014 * k, wo: 0.016, wi: 0.024, vRect: serp ? FS.covV : FS.uwV, layers: 'bottom', seg: 4, droop: -0.03, q, at: v3(0.004 + 0.011 * k, -0.010 + 0.001 * k, 0.035) });
      }
    }

    // ===== legs (left) =====
    const th = I('thighL'), ta = I('tarsusL');
    if (air) {
      tube(L, th, [v3(0, 0.01, 0), v3(0, -RIG.tibia, 0)], [0.007, 0.005], A.carbon, 0);
      blob(L, ta, [0, 0, 0], [0.008, 0.008, 0.008], A.metal, 1);
      tube(L, ta, [v3(0, 0, 0), v3(0, -RIG.tarsus, 0)], [0.0055, 0.0045], A.carbon, 0);
    } else {
      // feathered thigh ("trousers"): full at the top, tapering to the ankle
      const rings = [];
      for (let i = 0; i <= 7; i++) { const t = i / 7, y = 0.035 - t * (RIG.tibia + 0.03), r = (t < 0.85 ? lerp(0.040, 0.028, t / 0.85) * (1 + 0.12 * Math.sin(t / 0.85 * Math.PI)) : lerp(0.028, 0.015, (t - 0.85) / 0.15));
        rings.push({ b: th, c: [0.004, y, -0.008 - 0.012 * Math.sin(t * Math.PI)], rx: r, ryT: r, ryB: r, v: 1 - t }); }
      // a ring loft along -Y: build as tube with rx/rz by rotating the loft frame: emulate with tube()
      const pts = rings.map(r => v3(r.c[0], r.c[1], r.c[2])), rad = rings.map(r => r.rx);
      tube(L, th, pts, rad, A.legs, 0, 1, 1.1, false);
      // tarsus: feathered (hawk-eagle) or bare and scaled (serpent eagle)
      tube(L, ta, [v3(0, 0.004, 0), v3(0, -RIG.tarsus * 0.5, 0.002), v3(0, -RIG.tarsus + 0.004, 0)], serp ? [0.0085, 0.0075, 0.0075] : [0.0155, 0.0145, 0.012], A.tarsus, 0, 1, 1, false);
    }
    TOES.forEach((t, i) => {
      const a = I('toe' + i + 'aL'), b = I('toe' + i + 'bL'), r0 = i === 3 ? 0.0062 : 0.0058;
      const rect = air ? A.carbon : A.toe;
      tube(L, a, [v3(0, 0, -0.004), v3(0, -0.001, t[1] * 0.5), v3(0, 0, t[1])], [r0 * (air ? 0.7 : 1), r0 * 0.95 * (air ? 0.7 : 1), r0 * 0.85 * (air ? 0.7 : 1)], rect, 0, 1, 0.8, false);
      tube(L, b, [v3(0, 0, -0.002), v3(0, 0, t[2])], [r0 * 0.85 * (air ? 0.7 : 1), r0 * 0.7 * (air ? 0.7 : 1)], rect, 0, 1, 0.8, false);
      // talon: a curved cone hooking down
      const tl = t[3];
      tube(L, b, [v3(0, 0.001, t[2] - 0.002), v3(0, -tl * 0.18, t[2] + tl * 0.55), v3(0, -tl * 0.62, t[2] + tl * 0.9), v3(0, -tl, t[2] + tl * 0.95)], [0.0042, 0.0034, 0.002, 0.0006], air ? A.metal : A.talon, 1, 1, 0.85, true);
    });

    // ===== shoulder linkage on the airframe (moves with the wing) =====
    if (air) {
      tube(L, hum, [v3(-0.02, 0.006, 0.02), v3(0.06, 0.004, 0.02)], [0.004, 0.0035], A.metal, 1);
      blob(L, hum, [-0.016, 0.006, 0.02], [0.0075, 0.0075, 0.0075], A.led, 2);
    }

    // ===== midline: body, neck, head =====
    const body = I('body'), neck = I('neck'), head = I('head');
    const nb = bind[neck], hb = bind[head];
    const toLocal = (b, x, y, z) => { const m = new THREE.Matrix4().copy(bind[b]).invert ? new THREE.Matrix4().copy(bind[b]).invert() : new THREE.Matrix4().getInverse(bind[b]); return v3(x, y, z).applyMatrix4(m); };
    if (air) {
      // carbon pod (body bone) from the tail to the nose ring, then the chrome nose cone
      const st = [[-0.172, 0.012, 0.010, 0.010], [-0.15, 0.010, 0.022, 0.022], [-0.10, 0.006, 0.036, 0.034], [-0.04, 0.004, 0.048, 0.046], [0.02, 0.004, 0.054, 0.052],
        [0.07, 0.006, 0.055, 0.053], [0.11, 0.008, 0.052, 0.050], [0.14, 0.010, 0.046, 0.044]];
      loft(M, st.map(([z, y, rx, ry], i) => ({ b: body, c: [0, y, z], rx, ryT: ry * 0.95, ryB: ry, v: i / (st.length - 1) })), A.body, { capStart: true });
      const cone = [[0.14, 0.010, 0.046], [0.17, 0.010, 0.043], [0.20, 0.010, 0.036], [0.23, 0.010, 0.026], [0.255, 0.010, 0.015], [0.272, 0.010, 0.006], [0.281, 0.010, 0.0012]];
      loft(M, cone.map(([z, y, r], i) => ({ b: body, c: [0, y, z], rx: r, ryT: r, ryB: r, v: i / (cone.length - 1) })), A.metal, { slot: 1, capEnd: true });
      // dorsal spine
      tube(M, body, [v3(0, 0.058, 0.13), v3(0, 0.061, 0.0), v3(0, 0.047, -0.12), v3(0, 0.03, -0.17)], [0.0035, 0.004, 0.0035, 0.003], A.dark, 0, 1, 1, false);
      // front cross-bar of the flapping mechanism, orange caps, rear warning light
      tube(M, body, [v3(-0.062, 0.03, 0.075), v3(0.062, 0.03, 0.075)], [0.0045, 0.0045], A.metal, 1, 1, 1, false);
      for (const sx of [-1, 1]) blob(M, body, [0.066 * sx, 0.03, 0.075], [0.006, 0.006, 0.006], A.led, 2);
      blob(M, body, [0.03, 0.05, -0.02], [0.004, 0.004, 0.006], A.led, 2);
      // camera window under the nose (pans with the head bone)
      blob(M, head, [0, -0.02, 0.03], [0.012, 0.012, 0.012], A.dark, 1);
    } else {
      // feathered body from the tail coverts to the forehead, with the neck blended between body, neck and head bones
      const st = [
        // [bone, z (body frame), cy, rx, ryT, ryB]
        [body, -0.172, 0.014, 0.020, 0.012, 0.012], [body, -0.15, 0.008, 0.038, 0.026, 0.028], [body, -0.115, 0.002, 0.054, 0.038, 0.048], [body, -0.07, -0.002, 0.068, 0.048, 0.062],
        [body, -0.02, 0.000, 0.078, 0.055, 0.071], [body, 0.03, 0.004, 0.080, 0.057, 0.073], [body, 0.075, 0.008, 0.074, 0.055, 0.068], [body, 0.11, 0.013, 0.061, 0.048, 0.058],
        [neck, 0.138, 0.020, 0.050, 0.043, 0.048], [neck, 0.158, 0.025, 0.043, 0.039, 0.042],
      ];
      const rings = st.map(([b, z, cy, rx, ryT, ryB], i) => {
        const c = toLocal(b, 0, cy, z);
        const w1 = b === neck ? (i === st.length - 1 ? 0.55 : 0.0) : 0, b1 = b === neck ? head : b;
        const wB = b === neck && i === st.length - 2 ? 0.0 : 0;
        return { b, b1, w1, c: [c.x, c.y, c.z], rx, ryT, ryB, v: lerp(0, 0.80, (z + 0.172) / (0.158 + 0.172)) };
      });
      // the first neck ring blends back into the body
      rings[st.length - 2].b1 = body; rings[st.length - 2].w1 = 0.35;
      // head rings (head-bone frame)
      const hr = [[-0.002, 0.004, 0.036, 0.036, 0.034], [0.018, 0.009, 0.037, 0.039, 0.033], [0.036, 0.009, 0.034, 0.035, 0.029], [0.05, 0.006, 0.027, 0.028, 0.023],
        [0.060, 0.003, 0.019, 0.019, 0.016], [0.067, 0.001, 0.010, 0.010, 0.009], [0.070, 0.000, 0.003, 0.003, 0.003]];
      hr.forEach(([z, cy, rx, ryT, ryB], i) => rings.push({ b: head, c: [0, cy, z], rx, ryT, ryB, v: lerp(0.84, 1, i / (hr.length - 1)) }));
      loft(M, rings, A.body, { capStart: true, capEnd: true });
      // eyes and brow ridges
      for (const sx of [-1, 1]) {
        const ax = v3(Math.sin(52 * DEG) * sx, 0.08, Math.cos(52 * DEG)).normalize(), e1 = v3(0, 1, 0).sub(ax.clone().multiplyScalar(ax.y)).normalize(), e2 = ax.clone().cross(e1);
        blob(M, head, [0.0262 * sx, 0.0125, 0.037], [0.0092, 0.0092, 0.0092], A.eye, 1, 1, (nx, ny, nz) => { const n = v3(nx, ny, nz), d = n.dot(ax); return d > 0 ? uvIn(A.eye, 0.5 + 0.48 * n.dot(e2), 0.5 + 0.48 * n.dot(e1)) : uvIn(A.eye, 0.03, 0.03); });
        tube(M, head, [v3(0.020 * sx, 0.0255, 0.020), v3(0.0275 * sx, 0.0248, 0.035), v3(0.025 * sx, 0.0215, 0.047)], [0.0042, 0.0048, 0.003], A.brow, 0, 0.8, 0.5, false);
      }
      // bill: hooked upper mandible with a cere band, small lower mandible
      const bill = [v3(0, 0.006, 0.058), v3(0, 0.0075, 0.067), v3(0, 0.005, 0.076), v3(0, -0.0005, 0.083), v3(0, -0.008, 0.087), v3(0, -0.0145, 0.0855)];
      tube(M, head, bill, [0.0125, 0.0118, 0.0092, 0.0062, 0.0032, 0.0009], A.bill, 1, 1, 0.62, true);
      tube(M, head, [v3(0, 0.008, 0.056), v3(0, 0.008, 0.066)], [0.0128, 0.0122], A.cere, 1, 1, 0.8, false);
      tube(M, head, [v3(0, -0.006, 0.058), v3(0, -0.008, 0.070), v3(0, -0.009, 0.077)], [0.0072, 0.0052, 0.002], A.bill, 1, 0.8, 0.62, true);
      // crest: vestigial on the hawk-eagle (Taiwan/Japan race), a short fan on the serpent eagle
      const crestN = serp ? 7 : 3;
      for (let k = 0; k < crestN; k++) {
        const ang = (k - (crestN - 1) / 2) * (serp ? 0.22 : 0.3), len = serp ? 0.05 : 0.022;
        const base = v3(Math.sin(ang) * 0.012, 0.030, -0.006), tip = base.clone().add(v3(Math.sin(ang) * len * 0.35, len * (serp ? 0.35 : 0.2), -len));
        tube(M, head, [base, base.clone().lerp(tip, 0.5).add(v3(0, 0.004, 0)), tip], [0.005, 0.004, 0.0012], A.crest, 0, 1, 0.45, true);
      }
    }
    // assemble: left side, its mirror, then the midline
    const out = new Builder(); out.append(L); out.mirror(L, i => rig.mapLR.get(i) != null ? rig.mapLR.get(i) : i); out.append(M);
    const geometry = out.build();

    // ----- atlas and materials -----
    const size = quality === 'low' ? 1024 : 2048, canvas = paintAtlas(livery, size);
    const tex = new THREE.CanvasTexture(canvas);
    if ('colorSpace' in tex && THREE.SRGBColorSpace) tex.colorSpace = THREE.SRGBColorSpace; else tex.encoding = THREE.sRGBEncoding;
    tex.anisotropy = 4; tex.flipY = true; tex.needsUpdate = true;
    const mk = o => { const m = new THREE.MeshStandardMaterial(Object.assign({ map: tex, vertexColors: true }, o)); m.skinning = true; return m; };
    const materials = air
      ? [mk({ roughness: 0.48, metalness: 0.25 }), mk({ roughness: 0.18, metalness: 0.95 }), mk({ roughness: 0.4, metalness: 0, emissive: new THREE.Color(0xff6a10), emissiveIntensity: 2.2 })]
      : [mk({ roughness: 0.86, metalness: 0 }), mk({ roughness: 0.28, metalness: 0 }), mk({ roughness: 0.4, metalness: 0 })];
    materials.forEach((m, i) => { m.name = 'EagleDrone_' + livery + '_' + ['main', 'gloss', 'glow'][i]; });
    return { geometry, materials, canvas, texture: tex };
  }

  return { EagleDrone, SPEC, RIG, PRIM, SEC, LIVERIES, POSES, paintAtlas, version: '1.0' };
}

if (typeof module !== 'undefined' && module.exports) module.exports = makeEagleDrone;
if (root && root.THREE) { const lib = makeEagleDrone(root.THREE); root.EagleDroneLib = lib; root.EagleDrone = lib.EagleDrone; }
else if (root) root.makeEagleDrone = makeEagleDrone;
})(typeof window !== 'undefined' ? window : globalThis);
