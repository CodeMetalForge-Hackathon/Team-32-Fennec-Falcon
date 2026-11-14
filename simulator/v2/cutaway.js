// Eagle drone cutaway: a still, to-scale section through the bird with every part numbered.
// Inlined into eagle_viewer.html by build.js. Needs three.js r128 and eagle_drone.js (window.EagleDroneLib).
// The page shows the flight view by default and this view at #cutaway; the 3D renderer is made for one still and
// released straight after, so the picture cannot go blank and costs no graphics memory while you read it.

function initCutaway() {
  'use strict';
  const $ = s => document.querySelector(s);
  const lib = window.EagleDroneLib, THREE = window.THREE;
  const wait = $('#cutWait'), img = $('#cutImg');
  if (!THREE || !lib || !lib.BOM) { wait.textContent = 'Could not load three.js. Check the connection and reload.'; wait.dataset.err = '1'; return { show() {} }; }
  const W = 2400, H = 1500, DEG = Math.PI / 180;
  const GROUPS = { Disguise: '#8a5a2b', Airframe: '#3c4247', Drive: '#b4602b', Sensors: '#16807a', Compute: '#2e7d3a', Comms: '#2c5fa8', Power: '#5b4bb0' };
  // Cuts are in the body frame (+X = the bird's left, +Y = its back). On the near (left) side the disguise is cut away in steps,
  // outer layers deepest, so feathers, skin, foam and carbon each show as a band. 'top' removes everything above the step
  // heights (looking down into the back); 'bottom' removes everything below them (looking into the breast). wingX stops the
  // feather cut at the wrist, podX stops the airframe cut at the pod's side so the lamellae and legs keep their carbon.
  const STACK = lib.STACK, BODY = lib.BODY_RINGS.filter(r => r[0] === 'body').map(([, z, cy, rx, ryT, ryB]) => ({ z, cy, rx, ryT, ryB }));
  const VIEWS = {
    open: { title: 'Wings open', dir: [0.80, 0.52, 0.30], frame: [1240, 1060],
      cut: { side: 'top', feathers: -0.034, skin: -0.024, foam: -0.014, pod: -0.005, wingX: 0.36, podX: 0.078 },
      frameBones: /^(root|body|neck|head|tail|T\d|thigh|tarsus|toe|hum|H\d|S(9|1\d))/,
      setup(b) { b.setMode('soar', true); Object.assign(b.pose, { flap: 52 * DEG, sweep: -6 * DEG, slot: 1, primLift: 4 * DEG, tailSpread: 0.85, legTuck: 0.18, legReach: 0.1, toeCurl: 0.25, toeOpen: 0.5, crouch: 0, neckPitch: -4 * DEG, headPitch: 4 * DEG, headYaw: 0, bodyPitch: 6 * DEG }); } },
    perched: { title: 'Perched, wings folded', dir: [0.66, 0.16, 0.74], frame: [820, 930], branch: true,
      cut: { side: 'bottom', feathers: 0.026, skin: 0.016, foam: 0.006, pod: -0.003, wingX: 0.36, podX: 0.078 },
      frameBones: /./,
      setup(b) { b.setMode('perched', true); Object.assign(b.pose, { headYaw: 0, headPitch: -12 * DEG }); } },
  };
  let view = 'open', labels = true, busy = false; const cache = {};

  // ---------- parts list table ----------
  (function table() {
    const t = $('#bom'); let html = '<thead><tr><th scope="col">#</th><th scope="col">Part</th><th scope="col">What it is, where it goes</th><th scope="col">Size</th><th scope="col">Mass</th><th scope="col">Basis</th></tr></thead><tbody>';
    let g = '', tot = 0;
    for (const b of lib.BOM) {
      if (b.g !== g) { g = b.g; html += `<tr class="grp"><td colspan="6">${g}</td></tr>`; }
      tot += b.mass;
      html += `<tr><td class="no"><span style="background:${GROUPS[b.g]}">${b.n}</span></td><td class="nm">${b.name}</td><td class="wh">${b.line}</td><td class="sz">${b.size || '—'}</td><td class="ms">${b.mass} g</td><td class="bs">${b.basis}</td></tr>`;
    }
    html += `</tbody><tfoot><tr><td></td><td class="nm" colspan="3">Total, estimated, against the ${lib.SPEC.mass_kg} kg target (the species weighs 2.2–3.9 kg)</td><td class="ms">${(tot / 1000).toFixed(2)} kg</td><td class="bs">est.</td></tr></tfoot>`;
    t.innerHTML = html;
  })();

  // ---------- one still ----------
  function studioEnv(r) {
    const pm = new THREE.PMREMGenerator(r), es = new THREE.Scene(), sg = new THREE.SphereGeometry(10, 32, 16), pp = sg.attributes.position, cols = [];
    for (let i = 0; i < pp.count; i++) { const y = pp.getY(i) / 10, c = new THREE.Color(0xf4f2ee).lerp(new THREE.Color(0x9aa3a8), Math.max(0, -y) * 0.9 + 0.05); if (y > 0.55) c.set(0xffffff); cols.push(c.r, c.g, c.b); }
    sg.setAttribute('color', new THREE.Float32BufferAttribute(cols, 3)); es.add(new THREE.Mesh(sg, new THREE.MeshBasicMaterial({ side: THREE.BackSide, vertexColors: true })));
    const rt = pm.fromScene(es, 0.03); pm.dispose(); return rt;
  }
  function cutMat(m, planes, back) {
    const c = m.clone(); c.skinning = m.skinning; c.clippingPlanes = planes; c.clipIntersection = true; c.clipShadows = true; c.side = THREE.DoubleSide;
    const k = back == null ? 0.45 : back;
    c.onBeforeCompile = sh => { sh.fragmentShader = sh.fragmentShader.replace('#include <dithering_fragment>', '#include <dithering_fragment>\n\tif (!gl_FrontFacing) gl_FragColor.rgb *= ' + k.toFixed(2) + ';'); };
    c.customProgramCacheKey = () => 'cut' + k; return c;
  }
  function layerPoint(cut, layer, z) {
    // a point on the left flank, in the middle of that layer's visible band, on that layer's surface (body frame)
    const d = { feathers: 0, skin: STACK.feathers, foam: STACK.feathers + STACK.skin, pod: STACK.feathers + STACK.skin + STACK.foam }[layer];
    const out = cut.side === 'top' ? -0.02 : 0.02, nxt = { feathers: null, skin: 'feathers', foam: 'skin', pod: 'foam' }[layer];
    const y = layer === 'feathers' ? cut.feathers + out / 2 : (cut[layer] + cut[nxt]) / 2;
    let a = BODY[0], b = BODY[BODY.length - 1];
    for (let i = 0; i < BODY.length - 1; i++) if (z >= BODY[i].z && z <= BODY[i + 1].z) { a = BODY[i]; b = BODY[i + 1]; }
    const t = (z - a.z) / (b.z - a.z || 1), L = k => a[k] + (b[k] - a[k]) * t, ins = r => r - Math.min(d, 0.45 * r);
    const cy = L('cy'), rx = ins(L('rx')), ry = ins(L(y >= cy ? 'ryT' : 'ryB'));
    return new THREE.Vector3(rx * Math.sqrt(Math.max(0, 1 - Math.pow((y - cy) / ry, 2))), y, z);
  }

  async function render(v) {
    const V = VIEWS[v], canvas = document.createElement('canvas'); canvas.width = W; canvas.height = H;
    let r;
    try { r = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, preserveDrawingBuffer: true }); }
    catch (e) { throw new Error('This browser could not start a 3D view for the cutaway. Close other 3D tabs and reload.'); }
    try {
      r.setPixelRatio(1); r.setSize(W, H, false); r.setClearColor(0x000000, 0);
      r.outputEncoding = THREE.sRGBEncoding; r.toneMapping = THREE.ACESFilmicToneMapping; r.toneMappingExposure = 0.95;
      r.localClippingEnabled = true; r.shadowMap.enabled = true; r.shadowMap.type = THREE.PCFSoftShadowMap;
      const scene = new THREE.Scene(), env = studioEnv(r); scene.environment = env.texture;

      // the bird: disguise, layers under it, the airframe and every part, all at once
      const bird = new lib.EagleDrone({ livery: 'airframe', quality: 'high' }); bird.setLivery('hawkEagle');
      bird.meshes.airframe.visible = true; bird.showLayers(true); bird.showParts(true);
      scene.add(bird.object);
      V.setup(bird); bird.flapAmp = 0; bird.phase = Math.PI / 2; bird.applyPose();
      if (V.branch) bird.object.position.set(0, bird.perchOffset, 0);
      bird.object.updateMatrixWorld(true); bird.parts.update();
      const BM = bird.rig.by.body.matrixWorld, C = V.cut, sgn = C.side === 'top' ? -1 : 1;
      const P = (nx, ny, nz, c) => new THREE.Plane(new THREE.Vector3(nx, ny, nz), c).applyMatrix4(BM);
      // removed where x > 0, y beyond y0 (above for 'top', below for 'bottom') and x < xMax, all at once
      const quarter = (y0, xMax) => [P(-1, 0, 0, 0), P(0, sgn, 0, -sgn * y0), P(1, 0, 0, -xMax)];
      // the airframe cut opens the pod only: it stops at the pod's floor, so the carbon legs below stay whole
      const planes = { disguise: quarter(C.feathers, C.wingX), skin: quarter(C.skin, C.wingX), foam: quarter(C.foam, C.wingX), airframe: [...quarter(C.pod, C.podX), P(0, -1, 0, -0.06)] };
      bird.meshes.hawkEagle.material = bird.meshes.hawkEagle.material.map(m => cutMat(m, planes.disguise, 0.5));
      const lm = bird.layerMesh.material; bird.layerMesh.material = [cutMat(lm[0], planes.skin, 0.7), cutMat(lm[1], planes.foam, 0.75), lm[2]];
      const am = bird.meshes.airframe.material; bird.meshes.airframe.material = [cutMat(am[0], planes.airframe, 0.55), cutMat(am[1], planes.airframe, 0.6), am[2]];
      bird.meshes.airframe.material[0].color.multiplyScalar(0.55);   // carbon reads as carbon against the feathers
      bird.parts.list.forEach(m => m.traverse(o => { if (o.isMesh) o.material.envMapIntensity = 0.45; }));
      bird.object.traverse(o => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; } });

      // framing: orthographic, the bird inside the middle of the sheet, label columns either side
      const dir = new THREE.Vector3(...V.dir).normalize(), cam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.05, 20);
      const ctr = new THREE.Vector3(); bird.meshes.hawkEagle.skeleton.update();
      const pts = [], g = bird.meshes.hawkEagle.geometry.attributes.position, tv = new THREE.Vector3(), box = new THREE.Box3();
      const si = bird.meshes.hawkEagle.geometry.attributes.skinIndex, bones = bird.rig.bones;
      for (let i = 0; i < g.count; i += 2) { if (!V.frameBones.test(bones[si.getX(i)].name)) continue; tv.fromBufferAttribute(g, i); bird.meshes.hawkEagle.boneTransform(i, tv); tv.applyMatrix4(bird.meshes.hawkEagle.matrixWorld); pts.push(tv.clone()); box.expandByPoint(tv); }
      box.getCenter(ctr);
      cam.position.copy(ctr).addScaledVector(dir, 6); cam.up.set(0, 1, 0); cam.lookAt(ctr); cam.updateMatrixWorld(true);
      const vb = new THREE.Box2(); for (const p of pts) { const q = p.clone().applyMatrix4(cam.matrixWorldInverse); vb.expandByPoint(new THREE.Vector2(q.x, q.y)); }
      const bw = vb.max.x - vb.min.x, bh = vb.max.y - vb.min.y, ppm = Math.min(V.frame[0] / bw, V.frame[1] / bh);
      const bcx = (vb.min.x + vb.max.x) / 2, bcy = (vb.min.y + vb.max.y) / 2, cy = 70 + V.frame[1] / 2;
      cam.left = bcx - W / 2 / ppm; cam.right = bcx + W / 2 / ppm; cam.top = bcy + cy / ppm; cam.bottom = bcy - (H - cy) / ppm; cam.updateProjectionMatrix();

      // light: soft key from the camera side, cool fill, rim; a shadow on the floor
      scene.add(new THREE.HemisphereLight(0xffffff, 0x8f897c, 0.5));
      const key = new THREE.DirectionalLight(0xfff2e0, 2.1); key.position.copy(ctr).add(new THREE.Vector3(dir.x * 1.5 - 0.4, 2.6, dir.z * 1.5 + 0.6)); key.target.position.copy(ctr);
      key.castShadow = true; key.shadow.mapSize.set(2048, 2048); { const c = key.shadow.camera; c.left = c.bottom = -1.1; c.right = c.top = 1.1; c.near = 0.1; c.far = 8; } key.shadow.bias = -0.0004; key.shadow.normalBias = 0.01;
      scene.add(key, key.target);
      const fill = new THREE.DirectionalLight(0xdbe6ff, 0.6); fill.position.copy(ctr).add(new THREE.Vector3(-2, 0.4, -dir.z * 2)); scene.add(fill);
      const rim = new THREE.DirectionalLight(0xffffff, 0.8); rim.position.copy(ctr).add(new THREE.Vector3(-dir.x * 2, 1.2, -dir.z * 2.5)); scene.add(rim);
      const floor = new THREE.Mesh(new THREE.PlaneGeometry(6, 6), new THREE.ShadowMaterial({ opacity: 0.16 })); floor.rotation.x = -Math.PI / 2; floor.position.y = box.min.y - 0.03; floor.receiveShadow = true; scene.add(floor);
      if (V.branch) {
        const br = new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.045, 1.0, 20), new THREE.MeshStandardMaterial({ color: new THREE.Color(0x6b5947).convertSRGBToLinear(), roughness: 0.95 }));
        br.rotation.z = Math.PI / 2; br.position.set(0.05, -0.045, 0); br.scale.y = 0.42; br.castShadow = br.receiveShadow = true; br.name = 'branch'; scene.add(br);
        floor.position.y = -0.26;
      }

      r.render(scene, cam);
      const shot = document.createElement('canvas'); shot.width = W; shot.height = H; shot.getContext('2d').drawImage(canvas, 0, 0);

      // ID pass: which surface is in front at every pixel, so each callout dot lands on a part you can see
      const ids = new Map(), idOf = k => { if (!ids.has(k)) ids.set(k, ids.size + 1); return ids.get(k); };
      const REGION = n => /^(hum|fore|hand|P\d|S\d|H\d)/.test(n) ? 2 : /^(tail|T\d)/.test(n) ? 3 : /^(thigh|tarsus|toe)/.test(n) ? 4 : 1;
      const regionOf = bone => REGION(bone);
      const tagRegions = mesh => {   // per-vertex region in the red channel (a copy of the shared geometry)
        const g = mesh.geometry.clone(), si = g.attributes.skinIndex, n = si.count, col = new Float32Array(n * 3);
        for (let i = 0; i < n; i++) { col[i * 3] = REGION(bird.rig.bones[si.getX(i)].name) / 255; col[i * 3 + 1] = 1; col[i * 3 + 2] = 1; }
        g.setAttribute('color', new THREE.Float32BufferAttribute(col, 3)); mesh.geometry = g;
      };
      const idMat = (k, skin, pl, regions) => { const id = idOf(k), m = new THREE.MeshBasicMaterial({ color: new THREE.Color(regions ? 1 : 0, id / 255, 0), side: THREE.DoubleSide, vertexColors: !!regions }); m.toneMapped = false; m.skinning = !!skin; if (pl) { m.clippingPlanes = pl; m.clipIntersection = true; } return m; };
      [bird.meshes.hawkEagle, bird.meshes.airframe, bird.layerMesh].forEach(tagRegions);
      bird.meshes.hawkEagle.material = [0, 1, 2].map(() => idMat('disguise', true, planes.disguise, true));
      bird.layerMesh.material = [idMat('skin', true, planes.skin, true), idMat('foam', true, planes.foam, true), idMat('none', true, null, true)];
      bird.meshes.airframe.material = [idMat('airframe', true, planes.airframe, true), idMat('chrome', true, planes.airframe, true), idMat('led', true, null, true)];
      for (const [pid, ms] of Object.entries(bird.parts.byId)) ms.forEach(m => m.traverse(o => { if (o.isMesh) o.material = idMat('part:' + pid); }));
      scene.traverse(o => { if (o.name === 'branch') o.material = idMat('branch'); }); floor.visible = false;
      const rt = new THREE.WebGLRenderTarget(W, H), buf = new Uint8Array(W * H * 4);
      r.shadowMap.enabled = false; r.setRenderTarget(rt); r.setClearColor(0x000000, 1); r.clear(); r.render(scene, cam); r.readRenderTargetPixels(rt, 0, 0, W, H, buf); r.setRenderTarget(null); rt.dispose();
      const idAt = (x, y) => { x = Math.round(x); y = Math.round(y); if (x < 0 || y < 0 || x >= W || y >= H) return 0; const k = ((H - 1 - y) * W + x) * 4; return buf[k] * 256 + buf[k + 1]; };
      const idKey = (k, region) => (region || 0) * 256 + idOf(k);   // matches idAt: R = region, G = surface
      const snap = (p, want) => {   // nearest pixel (within 44 px) showing one of the wanted surfaces
        for (let rad = 0; rad <= 44; rad += 2) for (let a = 0; a < (rad ? 24 : 1); a++) { const x = p[0] + rad * Math.cos(a / 24 * Math.PI * 2), y = p[1] + rad * Math.sin(a / 24 * Math.PI * 2); if (want.has(idAt(x, y))) return [x, y]; }
        return null;
      };

      // callout anchors, projected to the sheet and checked against the ID pass
      const toPx = p => { const q = p.clone().project(cam); return [(q.x + 1) / 2 * W, (1 - q.y) / 2 * H]; };
      const wp = new THREE.Vector3(), LAYER_ID = { feathers: 'disguise', skin: 'skin', foam: 'foam', pod: 'airframe' };
      const anchor = a => {
        let p = null, want = null;
        if (a.part) { const ms = bird.parts.byId[a.part]; if (!ms) return null; const bb = new THREE.Box3().setFromObject(ms[0]); p = toPx(bb.getCenter(wp)); want = new Set([idKey('part:' + a.part)]); }
        else if (a.bone) { const b = bird.rig.by[a.bone]; if (!b) return null; p = toPx(new THREE.Vector3(...a.at).applyMatrix4(b.matrixWorld)); want = a.on ? new Set([idKey(a.on, regionOf(a.bone))]) : null; }
        else if (a.layer) { p = toPx(layerPoint(C, a.layer, a.z).applyMatrix4(BM)); want = new Set([idKey(LAYER_ID[a.layer], 1)]); }
        if (!p) return null;
        if (!want) return idAt(p[0], p[1]) ? p : null;
        return snap(p, want);
      };
      const items = lib.BOM.filter(b => b.a.length).map(b => ({ b, pts: b.a.map(anchor).filter(Boolean).slice(0, 3) })).filter(it => it.pts.length);
      const hidden = lib.BOM.filter(b => b.a.length && !items.find(it => it.b === b)).map(b => b.n);
      cache[v] = { shot, items, hidden, ppm, title: V.title };
      bird.parts.list.forEach(m => m.geometry.dispose()); env.dispose();
    } finally { r.dispose(); r.forceContextLoss(); }
  }

  // ---------- the sheet: paper, the render, callouts, title block ----------
  function wrapText(x, text, maxW, maxLines) {
    const words = text.split(' '), lines = []; let cur = '';
    for (const w of words) { const t = cur ? cur + ' ' + w : w; if (x.measureText(t).width > maxW && cur) { lines.push(cur); cur = w; } else cur = t; }
    if (cur) lines.push(cur);
    if (lines.length > maxLines) { lines.length = maxLines; let l = lines[maxLines - 1]; while (x.measureText(l + '…').width > maxW && l.length) l = l.slice(0, -1); lines[maxLines - 1] = l.replace(/[ ,;]+$/, '') + '…'; }
    return lines;
  }
  function compose(v) {
    const c = cache[v], cv = document.createElement('canvas'); cv.width = W; cv.height = H; const x = cv.getContext('2d');
    x.fillStyle = '#f2f0ea'; x.fillRect(0, 0, W, H);
    // 10 mm grid (20 mm if it would be too dense), faint
    const mm10 = c.ppm * 0.01, step = mm10 >= 14 ? mm10 : mm10 * 2;
    x.strokeStyle = 'rgba(40,60,50,0.055)'; x.lineWidth = 1; x.beginPath();
    for (let gx = W / 2 % step; gx < W; gx += step) { x.moveTo(Math.round(gx) + 0.5, 0); x.lineTo(Math.round(gx) + 0.5, H); }
    for (let gy = H / 2 % step; gy < H; gy += step) { x.moveTo(0, Math.round(gy) + 0.5); x.lineTo(W, Math.round(gy) + 0.5); }
    x.stroke();
    x.drawImage(c.shot, 0, 0);

    if (labels) {
      const colW = 455, top = 64, bottom = H - 64, L = [], R = [];
      for (const it of c.items) { if (!it.pts.length) continue; it.ax = it.pts.reduce((s, p) => s + p[0], 0) / it.pts.length; it.ay = it.pts.reduce((s, p) => s + p[1], 0) / it.pts.length; (it.ax < W / 2 ? L : R).push(it); }
      // balance the columns: move the items nearest the middle across
      const bal = (a, b) => { while (a.length > b.length + 1) { a.sort((p, q) => Math.abs(p.ax - W / 2) - Math.abs(q.ax - W / 2)); b.push(a.shift()); } };
      bal(L, R); bal(R, L);
      const place = (col, left) => {
        col.sort((p, q) => p.ay - q.ay);
        const n = col.length, gap = Math.min(118, (bottom - top) / Math.max(1, n));
        col.forEach((it, i) => { it.ly = Math.max(top + gap * i, Math.min(it.ay - 18, bottom - gap * (n - i))); });
        for (let i = 1; i < n; i++) col[i].ly = Math.max(col[i].ly, col[i - 1].ly + gap);
        for (let i = n - 2; i >= 0; i--) col[i].ly = Math.min(col[i].ly, col[i + 1].ly - gap);
        const bxOf = () => left ? 40 + colW + 26 : W - 40 - colW - 26;
        const segs = it => { const bx = bxOf(), by = it.ly + 18, out = []; for (const p of it.pts) { const ex = left ? Math.min(p[0] - 40, bx + 90) : Math.max(p[0] + 40, bx - 90); out.push([[bx, by], [ex, by]], [[ex, by], p]); } return out; };
        const cross = (s1, s2) => { const o = (a, b, c) => Math.sign((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])); return o(s1[0], s1[1], s2[0]) * o(s1[0], s1[1], s2[1]) < 0 && o(s2[0], s2[1], s1[0]) * o(s2[0], s2[1], s1[1]) < 0; };
        const count = () => { let k = 0; const all = col.map(segs); for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) for (const a of all[i]) for (const b of all[j]) if (cross(a, b)) k++; return k; };
        let best = count();
        for (let pass = 0; pass < 12 && best > 0; pass++) {   // try every pair of slots; keep a swap when it removes crossings
          let improved = false;
          for (let i = 0; i < n - 1; i++) for (let j = i + 1; j < n; j++) {
            [col[i].ly, col[j].ly] = [col[j].ly, col[i].ly]; const k = count();
            if (k < best) { best = k; improved = true; [col[i], col[j]] = [col[j], col[i]]; } else [col[i].ly, col[j].ly] = [col[j].ly, col[i].ly];
          }
          if (!improved) break;
        }
        col.forEach(it => {
          const color = GROUPS[it.b.g], bx = left ? 40 + colW + 26 : W - 40 - colW - 26, by = it.ly + 18;
          // leaders
          x.strokeStyle = 'rgba(28,34,32,0.72)'; x.lineWidth = 2;
          for (const p of it.pts) { x.beginPath(); x.moveTo(bx + (left ? 20 : -20), by); const ex = left ? Math.min(p[0] - 40, bx + 90) : Math.max(p[0] + 40, bx - 90); x.lineTo(ex, by); x.lineTo(p[0], p[1]); x.stroke(); }
          for (const p of it.pts) { x.beginPath(); x.arc(p[0], p[1], 7.5, 0, Math.PI * 2); x.fillStyle = '#fff'; x.fill(); x.beginPath(); x.arc(p[0], p[1], 5, 0, Math.PI * 2); x.fillStyle = color; x.fill(); }
          // number badge
          x.beginPath(); x.arc(bx, by, 20, 0, Math.PI * 2); x.fillStyle = color; x.fill();
          x.fillStyle = '#fff'; x.font = '600 22px "IBM Plex Mono", ui-monospace, monospace'; x.textAlign = 'center'; x.textBaseline = 'middle'; x.fillText(String(it.b.n), bx, by + 1);
          // name and one line
          x.textAlign = left ? 'right' : 'left'; x.textBaseline = 'alphabetic';
          const tx = left ? bx - 32 : bx + 32;
          x.fillStyle = '#18211e'; x.font = '600 27px "Source Sans 3", "Segoe UI", sans-serif'; x.fillText(it.b.name, tx, by + 6);
          x.fillStyle = '#56655f'; x.font = '400 20px "Source Sans 3", "Segoe UI", sans-serif';
          wrapText(x, it.b.cap || it.b.line, colW - 20, 2).forEach((ln, k) => x.fillText(ln, tx, by + 34 + 23 * k));
        });
      };
      place(L, true); place(R, false);
    }

    // title block, scale bar and legend, bottom centre
    const cx = W / 2, by = H - 150;
    x.textAlign = 'center'; x.fillStyle = '#18211e'; x.font = '700 40px "Saira Condensed", "Arial Narrow", sans-serif';
    x.fillText('EAGLE-1 CUTAWAY  ·  ' + c.title.toUpperCase(), cx, by);
    x.fillStyle = '#56655f'; x.font = '400 21px "IBM Plex Mono", ui-monospace, monospace';
    const tot = lib.BOM.reduce((s, b) => s + b.mass, 0) / 1000;
    x.fillText(`span ${lib.SPEC.span_m.toFixed(2)} m · length ${lib.SPEC.length_m.toFixed(2)} m · est. ${tot.toFixed(1)} kg of a ${lib.SPEC.mass_kg} kg target · orthographic`, cx, by + 34);
    if (labels && c.hidden.length) { x.font = '400 18px "IBM Plex Mono", ui-monospace, monospace'; x.fillStyle = '#7a857f'; x.fillText(`not visible from this side: ${c.hidden.join(', ')}; see the parts list`, cx, by - 52); }
    const bar = c.ppm * 0.1, bx0 = cx - bar / 2, yb = by + 70;
    x.fillStyle = '#18211e'; x.fillRect(bx0, yb, bar / 2, 9); x.strokeStyle = '#18211e'; x.lineWidth = 2; x.strokeRect(bx0 + 1, yb + 1, bar - 2, 7);
    x.font = '500 20px "IBM Plex Mono", ui-monospace, monospace'; x.textAlign = 'right'; x.fillText('0', bx0 - 10, yb + 10); x.textAlign = 'left'; x.fillText('100 mm', bx0 + bar + 10, yb + 10);
    const gs = Object.keys(GROUPS); x.font = '500 19px "IBM Plex Mono", ui-monospace, monospace';
    const widths = gs.map(g => x.measureText(g).width + 44), total = widths.reduce((a, b) => a + b, 0); let lx = cx - total / 2;
    gs.forEach((g, i) => { x.beginPath(); x.arc(lx + 9, yb + 46, 9, 0, Math.PI * 2); x.fillStyle = GROUPS[g]; x.fill(); x.fillStyle = '#3b4642'; x.textAlign = 'left'; x.fillText(g, lx + 24, yb + 52); lx += widths[i]; });
    return cv;
  }

  async function show() {
    if (busy) return; busy = true;
    try {
      if (!cache[view]) {
        wait.hidden = false; delete wait.dataset.err; wait.textContent = 'Cutting the eagle open…';
        await (document.fonts && document.fonts.load ? Promise.race([Promise.all(['600 27px "Source Sans 3"', '400 20px "Source Sans 3"', '500 20px "IBM Plex Mono"', '700 40px "Saira Condensed"'].map(f => document.fonts.load(f))), new Promise(r => setTimeout(r, 2500))]) : null);
        await new Promise(r => setTimeout(r, 30));   // let the message paint
        await render(view);
      }
      const cv = compose(view);
      img.src = cv.toDataURL('image/jpeg', 0.9); img.hidden = false; wait.hidden = true;
      img.alt = `Cutaway of the Eagle drone, ${VIEWS[view].title.toLowerCase()}, with ${lib.BOM.filter(b => b.a.length).length} numbered parts`;
    } catch (err) {
      console.error(err); img.hidden = true; wait.hidden = false; wait.dataset.err = '1'; wait.textContent = (err && err.message) || String(err);
    } finally { busy = false; }
  }
  const setPressed = (sel, val) => document.querySelectorAll(sel + ' button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.v === val)));
  $('#cutPose').addEventListener('click', e => { const b = e.target.closest('button'); if (!b || busy) return; view = b.dataset.v; setPressed('#cutPose', view); show(); });
  $('#cutLabels').addEventListener('click', () => { labels = !labels; $('#cutLabels').setAttribute('aria-pressed', String(labels)); if (cache[view]) show(); });
  return { show };
}

// ---------- which view: the flying model, or the cutaway at #cutaway ----------
(() => {
  const vv = document.getElementById('viewerView'), cv = document.getElementById('cutView');
  let viewer = null, cut = null;
  function route() {
    const isCut = location.hash === '#cutaway';
    vv.hidden = isCut; cv.hidden = !isCut;
    document.title = isCut ? 'Hawk-Eagle Drone · Cutaway' : 'Hawk-Eagle Drone';
    if (isCut) { if (viewer) viewer.pause(true); (cut = cut || initCutaway()).show(); window.scrollTo(0, 0); }
    else if (!viewer) viewer = initViewer(); else viewer.pause(false);
  }
  addEventListener('hashchange', route); route();
})();
