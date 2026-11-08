// Exports the eagle drone as skinned glTF binaries (.glb) with animation clips, one file per skin.
// Runs eagle_drone.js in headless Chromium so the files come from exactly the code the simulator uses.
//
//   cd simulator/v2/tests && npm install        (playwright + three r128, same as the browser checks)
//   npx playwright install chromium             (once, if Chromium is missing)
//   node ../tools/export_glb.js                 -> ../assets/eagle_drone_<skin>.glb
//
// Textures are embedded as JPEG. Clips (30 fps): flap, glide, soar (in place, loop), perch_idle (head scans, loops), fold, unfold,
// land (root motion: approach, flare, touch-down, balance beats, fold; ends at the origin),
// takeoff (root motion: crouch, spring, climb; starts at the origin).
// Origin = the body centre while perched; +Y up, +Z forward, metres (glTF). Unreal converts to cm on import.
const path = require('path'), fs = require('fs');
const TESTS = path.join(__dirname, '..', 'tests');
const { chromium } = require(path.join(TESTS, 'node_modules', 'playwright'));
const THREE_DIR = path.dirname(require.resolve('three/package.json', { paths: [TESTS] }));
const OUT = path.join(__dirname, '..', 'assets');
const SKINS = (process.argv[2] || 'hawkEagle,airframe,serpentEagle').split(',');

const PAGE = `<!doctype html><meta charset="utf-8"><body>
<script src="three.min.js"></script><script src="GLTFExporter.js"></script><script src="eagle_drone.js"></script>
<script>
const FPS = 30, DEG = Math.PI / 180;
function seeded(seed) { let s = seed >>> 0; return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; }; }
function sample(bird, seconds, step) {
  const frames = [], dt = 1 / FPS, n = Math.round(seconds * FPS);
  bird.applyPose();
  for (let i = 0; i <= n; i++) {
    if (i) { if (step) step(i * dt, bird); bird.update(dt); }
    bird.object.updateMatrixWorld(true);
    frames.push({ op: bird.object.position.clone(), oq: bird.object.quaternion.clone(), bones: bird.rig.bones.map(b => ({ p: b.position.clone(), q: b.quaternion.clone() })) });
  }
  return frames;
}
function isConstant(arr, stride) { for (let i = stride; i < arr.length; i++) if (Math.abs(arr[i] - arr[i % stride]) > 1e-5) return false; return true; }
function sameAs(arr, ref) { for (let i = 0; i < ref.length; i++) if (Math.abs(arr[i] - ref[i]) > 1e-5) return false; return true; }
// every bone gets a track unless it sits at its rest (bind) pose for the whole clip; a bone that holds still gets two keys
let REST = null;
function clip(bird, name, frames, root) {
  const times = frames.map((f, i) => i / FPS), tracks = [];
  bird.rig.bones.forEach((b, bi) => {
    const q = [], p = [];
    frames.forEach(f => {
      let bp = f.bones[bi].p, bq = f.bones[bi].q;
      if (root && b.name === 'root') { bp = f.op.clone().sub(root.origin); bq = f.oq.clone(); }
      q.push(bq.x, bq.y, bq.z, bq.w); p.push(bp.x, bp.y, bp.z);
    });
    // keep quaternions continuous so interpolation never takes the long way round
    for (let i = 4; i < q.length; i += 4) { const d = q[i] * q[i - 4] + q[i + 1] * q[i - 3] + q[i + 2] * q[i - 2] + q[i + 3] * q[i - 1]; if (d < 0) for (let k = 0; k < 4; k++) q[i + k] = -q[i + k]; }
    const rest = REST[bi], ends = [0, times[times.length - 1]];
    if (!isConstant(q, 4)) tracks.push(new THREE.QuaternionKeyframeTrack(b.name + '.quaternion', times, q));
    else if (!(sameAs(q, rest.q) || sameAs(q, rest.q.map(v => -v)))) tracks.push(new THREE.QuaternionKeyframeTrack(b.name + '.quaternion', ends, q.slice(0, 4).concat(q.slice(0, 4))));
    if (!isConstant(p, 3)) tracks.push(new THREE.VectorKeyframeTrack(b.name + '.position', times, p));
    else if (!sameAs(p, rest.p)) tracks.push(new THREE.VectorKeyframeTrack(b.name + '.position', ends, p.slice(0, 3).concat(p.slice(0, 3))));
  });
  return new THREE.AnimationClip(name, times[times.length - 1], tracks);
}
window.exportSkin = function (skin) {
  Math.random = seeded(7);
  const bird = new EagleDrone({ livery: skin, quality: 'high' }), O = new THREE.Vector3(0, 0, 0), perchTop = new THREE.Vector3(0, -bird.perchOffset, 0);
  const reset = () => { bird.stop(); bird.object.position.set(0, 0, 0); bird.object.rotation.set(0, 0, 0); bird.setHeading(0); bird._heading = 0; bird.look.yaw = bird.look.pitch = bird.look.tYaw = bird.look.tPitch = 0; bird.look.next = Infinity; bird.phase = 0; };
  const clips = [];
  bird.bindPose(); REST = bird.rig.bones.map(b => ({ p: b.position.toArray(), q: b.quaternion.toArray() }));
  // loops in place
  reset(); bird.setMode('fly', true); bird.flapHz = 3.0; clips.push(clip(bird, 'flap', sample(bird, 1.0)));
  reset(); bird.setMode('glide', true); clips.push(clip(bird, 'glide', sample(bird, 2.0)));
  reset(); bird.setMode('soar', true); clips.push(clip(bird, 'soar', sample(bird, 2.0)));
  // perched: scripted head turns that come back to centre so the loop is seamless
  reset(); bird.perch(perchTop, 0); bird.look.next = Infinity;
  const turns = [[0.0, 0, 0], [0.9, 0.85, 0.1], [2.1, -0.55, -0.05], [3.3, 0.3, 0.15], [4.6, 0, 0]];
  clips.push(clip(bird, 'perch_idle', sample(bird, 6.0, t => { for (const [at, y, p] of turns) if (Math.abs(t - at) < 0.5 / FPS) { bird.look.tYaw = y; bird.look.tPitch = p; } })));
  // fold and unfold on the perch
  reset(); bird.perch(perchTop, 0); bird.spreadWings(true); Object.assign(bird.pose, bird.target); bird.applyPose(); bird.spreadWings(false); bird.look.next = Infinity;
  clips.push(clip(bird, 'fold', sample(bird, 1.8)));
  reset(); bird.perch(perchTop, 0); bird.look.next = Infinity; bird.spreadWings(true);
  clips.push(clip(bird, 'unfold', sample(bird, 1.4)));
  // land: approach from 16 m behind and 3 m up, flare, perch, settle and fold; root motion ends at the origin
  reset(); bird.perch(perchTop, 0); const end = bird.object.position.clone();
  const from = new THREE.Vector3(0, 3, -18); bird.object.position.copy(from); bird.setMode('fly', true);
  let landed = false; bird.land(perchTop, 0, from, { duration: 3.2, onDone: () => { landed = true; } });
  const lf = sample(bird, 3.2 + 2.6, () => { if (landed) bird.look.next = Infinity; });
  clips.push(clip(bird, 'land', lf, { origin: end }));
  // takeoff: from the perch along a climbing path; root motion starts at the origin
  reset(); bird.perch(perchTop, 0); const start = bird.object.position.clone();
  bird.takeoff([start.clone(), start.clone().add(new THREE.Vector3(0, 0.6, 2.5)), start.clone().add(new THREE.Vector3(0, 2.2, 8)), start.clone().add(new THREE.Vector3(0, 4.5, 18))], { duration: 2.4 });
  clips.push(clip(bird, 'takeoff', sample(bird, 2.4), { origin: start }));
  // textures as JPEG (opaque; a quarter of the PNG size)
  bird.mesh.material.forEach(m => { if (m.map) m.map.format = THREE.RGBFormat; if (m.emissiveIntensity > 1) m.emissiveIntensity = 1; });   // glTF caps emissive at 1
  // rest pose = the bind pose (wings spread); only this skin's mesh is exported
  reset(); bird.bindPose(); bird.object.position.set(0, 0, 0); bird.object.updateMatrixWorld(true);
  bird.object.name = 'EagleDrone_' + skin;
  return new Promise((res, rej) => new THREE.GLTFExporter().parse(bird.object, glb => {
    const u8 = new Uint8Array(glb); let s = ''; for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
    res({ b64: btoa(s), clips: clips.map(c => ({ name: c.name, s: +c.duration.toFixed(2), tracks: c.tracks.length })), bones: bird.rig.bones.length, tris: bird.mesh.geometry.index.count / 3, perchOffset: bird.perchOffset });
  }, { binary: true, animations: clips, onlyVisible: true, truncateDrawRange: true, embedImages: true, maxTextureSize: 2048 }));
};
window.ready = true;
</script>`;

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const tmp = fs.mkdtempSync(path.join(require('os').tmpdir(), 'eagle-glb-'));
  fs.writeFileSync(path.join(tmp, 'index.html'), PAGE);
  fs.copyFileSync(path.join(THREE_DIR, 'build/three.min.js'), path.join(tmp, 'three.min.js'));
  fs.copyFileSync(path.join(THREE_DIR, 'examples/js/exporters/GLTFExporter.js'), path.join(tmp, 'GLTFExporter.js'));
  fs.copyFileSync(path.join(__dirname, '..', 'eagle_drone.js'), path.join(tmp, 'eagle_drone.js'));
  const browser = await chromium.launch({ args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
  try {
    const page = await browser.newPage(); const errs = [];
    page.on('pageerror', e => errs.push(e.message)); page.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
    await page.goto('file://' + path.join(tmp, 'index.html')); await page.waitForFunction(() => window.ready, null, { timeout: 60000 });
    for (const skin of SKINS) {
      const r = await page.evaluate(s => window.exportSkin(s), skin);
      const file = path.join(OUT, `eagle_drone_${skin}.glb`); fs.writeFileSync(file, Buffer.from(r.b64, 'base64'));
      console.log(`${path.relative(process.cwd(), file)}  ${(fs.statSync(file).size / 1048576).toFixed(1)} MB  ${r.bones} bones  ${r.tris} tris  perch offset ${r.perchOffset.toFixed(3)} m`);
      console.log('  clips: ' + r.clips.map(c => `${c.name} ${c.s}s/${c.tracks}tr`).join(', '));
    }
    if (errs.length) { console.error('page errors:\n' + errs.join('\n')); process.exitCode = 1; }
  } finally { await browser.close(); fs.rmSync(tmp, { recursive: true, force: true }); }
})().catch(e => { console.error(e); process.exit(1); });
