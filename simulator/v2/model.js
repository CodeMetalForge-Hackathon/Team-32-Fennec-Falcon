/* EagleModel v2: a clandestine find. The operator carries nothing.
 *   INPUT       thermal camera (primary) and microphone array (secondary) on a perched bird
 *   PROCESSING  onboard AI: detect, track, weigh evidence, decide WHEN a report is worth the risk
 *   OUTPUT      one 48-byte report, sent the least detectable way that is still fast enough
 * Pure functions, no DOM. Browser: window.EagleModel. Node: module.exports.
 * Every constant lives in PARAMS with its basis; "assumed" marks a model choice, not a measurement.
 */
(function (root) {
  'use strict';
  var log10 = Math.log10, exp = Math.exp, sqrt = Math.sqrt, atan = Math.atan, cos = Math.cos, PI = Math.PI;
  var D2R = PI / 180;

  var PARAMS = {
    climate: {   // July mean daily max/min and mean RH, CWA normals 1991-2020
      300:  { tMax: 33.3, tMin: 25.5, rh: 75 },     // Taichung (lowland)
      1000: { tMax: 28.3, tMin: 20.1, rh: 84.5 },   // Sun Moon Lake station, 1,015 m
      1500: { tMax: 25.3, tMin: 17.1, rh: 88 }      // estimate: Sun Moon Lake minus 6 C/km lapse
    },
    weatherTempOffset: { clear: 0, mist: -1.5, rain: -3.0, typhoon: -4.0 },
    noise: {     // ambient, dBA (biophony by hour + rain, combined by power sum)
      day: 62,       // 07-17 h: summer cicada chorus (estimate; one cicada is ~73 dB LAeq at 1 m, sings at 4.7-10 kHz)
      dusk: 56,      // 17-20 h (estimate)
      night: 48,     // 20-03 h: insects and frogs (estimate)
      predawn: 42,   // 03-06 h (estimate)
      dawn: 52,      // 06-07 h: dawn chorus (estimate)
      rain: { clear: 0, mist: 0, rain: 54, typhoon: 64 },   // Sato et al. 2021: ~54 dB at ~16 mm/h, 63.9 dB at 160 mm/h
      elevation: { 300: 2, 1000: 0, 1500: -3 }
    },
    acoustic: {
      alphaAir: { 1000: 0.005, 1500: 0.007, 2000: 0.009, 3000: 0.014, 4000: 0.020 },   // dB/m (ISO 9613-1 order)
      alphaVeg: { 1000: 0.06, 1500: 0.07, 2000: 0.08, 3000: 0.085, 4000: 0.09 },       // dB/m dense foliage (ISO 9613-2 Table A.1)
      vegFactor: 1.0,     // French Guiana rainforest measured ~0.5x this (Haupert 2023); other tropical sites several x
      detectSNR_dB: 6,
      signals: {          // SL = dB at 1 m; bandGain = in-band noise below the broadband dBA (insects sing mostly above 4 kHz)
        none:          { label: 'Silent', SL: null },
        tapCode:       { label: 'Briefed tap code on a trunk (3-3-1)', SL: 85, f: 1500, bandGainNight: 8, bandGainDay: 8, dur_s: 6 },   // assumed level
        softWhistle:   { label: 'Briefed soft whistle (3 short)', SL: 85, f: 2500, bandGainNight: 10, bandGainDay: 10, dur_s: 2.5 },     // assumed level
        cough:         { label: 'Cough or groan', SL: 70, f: 1000, bandGainNight: 8, bandGainDay: 8, dur_s: 1 },                        // assumed level
        voice:         { label: 'Normal voice', SL: 57, f: 1000, bandGainNight: 8, bandGainDay: 8, dur_s: 3 },                          // Pearsons 1977
        shout:         { label: 'Shout', SL: 85, f: 1000, bandGainNight: 8, bandGainDay: 8, dur_s: 2 },                                 // Pearsons 1977
        rescueWhistle: { label: 'Rescue whistle', SL: 104, f: 3000, bandGainNight: 10, bandGainDay: 10, dur_s: 3 }                      // Fox 40: 115 dB sound power
      },
      codeEvery_min: 30   // briefed: repeat the code every 30 min after dark (assumed plan)
    },
    thermal: {
      cameras: {
        mlx55:    { label: 'MLX90640 32x24, 55 deg', w: 32, h: 24, hfov: 55, netd: 0.10 },
        hti256:   { label: 'HTI HT-203 class 256x192, 56 deg', w: 256, h: 192, hfov: 56, netd: 0.05 },
        lepton35: { label: 'FLIR Lepton 3.5 160x120, 57 deg', w: 160, h: 120, hfov: 57, netd: 0.05 },
        boson640: { label: 'FLIR Boson+ 640x512, 32 deg', w: 640, h: 512, hfov: 32, netd: 0.02 }
      },
      target_m: 0.45, minPx: 3, shapePx: 8,
      skinC: 34.0,
      clothingK: { dry: 0.45, wet: 0.12 },   // clothing surface = amb + K * (skin - amb)
      bgOffsetC: { day: 1.0, night: -0.8 },  // forest background vs air
      clutterC: { day: 1.5, night: 0.4, wet: 0.3 },
      betaPerKm: 0.6, rainBetaPerKm: { clear: 0, mist: 1.0, rain: 2.0, typhoon: 6.0 },
      G: 0.5, clumping: 0.7,                 // Beer-Lambert canopy gap probability exp(-G*clumping*LAI/cos(zenith)); clumping assumed
      minPLOS: 0.3
    },
    radar: { vitals_m: 1.5, presence_m: 6.0 },   // Seeed MR60BHA2 wiki
    rf: {
      freqMHz: 923,                      // Taiwan AS923-1 band (920-925 MHz)
      radios: {
        sf7bw250:  { label: 'LoRa SF7 / 250 kHz', sf: 7, bw: 250000, sens: -121.5 },
        sf12bw125: { label: 'LoRa SF12 / 125 kHz', sf: 12, bw: 125000, sens: -137.0 }
      },
      txMin_dBm: -9, txMax_dBm: 22, powerMargin_dB: 10,
      veg: { A1: 0.18, alpha: 0.752, gamma: 0.3 },    // ITU-R P.833-10 one terminal in woodland (Rio de Janeiro fit); gamma assumed
      // both ends inside the forest: Weissberger + plane-earth gives ~136 dB at 250 m (0.3/1.5 m antennas) vs ~138 dB
      // measured for LoRa in Brazilian rainforest (SF12 max 250 m)
      enemy: { h_patrol: 1.5, h_target: 10.0, thr: { energy: -115, lora: -124 } }
    },
    fix: { doaSigma_deg: 3.0, headingSigma_deg: { mag: 8.0, vio: 1.0 }, tiltSigma_deg: 1.0,
           ownPos_m: { vio: 10, mag: 30 } },   // Eagle's own position without GPS: VIO + map matching vs dead reckoning (assumed)
    ai: {
      devices: {
        hailo8: { label: 'Pi 5 + Hailo-8 AI HAT+ (26 TOPS)', note: 'Thermal person detector and audio event classifier in real time' },
        orinNX: { label: 'Jetson Orin NX', note: 'Same models, plus hardware video encode' },
        pi5:    { label: 'Pi 5 CPU only', note: 'Background model and a small classifier at 1-2 frames/s' }
      },
      others: { none: 0, patrol: 4, target: 12, locals: 2 },   // other people who may come into view (assumed)
      lr: {   // likelihood ratios P(evidence | operator) / P(evidence | anyone or anything else); all assumed
        shapeClear: 3, shapeFaint: 1.5,   // human shape in thermal: >= 8 px / 3-8 px across
        alone: 2,                         // no other warm body within 30 m
        location: 2,                      // off-trail, inside the circle around the parachute
        still30: 4, still120: 3,          // lying still for 30 min, then 2 h
        code: 100,                        // briefed tap code or whistle pattern
        cough: 2,                         // cough or groan, per event (injured)
        breathing: 2                      // radar pellet: resting breathing rate
      },
      coughTimes_min: [20, 50, 80, 110]
    },
    output: { speed_mps: 12.5 },   // ~45 km/h; Hawk-Eagle airframe model flies 105 km in 2.2 h on the motor
    link: { iridium: { label: 'Iridium SBD', moMax: 340, mtMax: 270, latency_s: 30 } },   // 9603: latency under 1 minute
    perches: {
      snag:      { label: 'Dead snag on a ridge, above the canopy', h: 20 },
      subcanopy: { label: 'Branch under the canopy', h: 8 },
      stand:     { label: 'Demo stand', h: 3 }
    },
    search: { hop_min: 5, nightHours: 10 },
    sizes: { FIX: 48 }
  };

  var PRESETS = {
    night:     { label: '02:00 summer night', hour: 2, elevation_m: 1000, weather: 'clear' },
    cicadaDay: { label: '14:00 cicada chorus', hour: 14, elevation_m: 300, weather: 'clear' },
    typhoon:   { label: 'Typhoon rain', hour: 16, elevation_m: 1000, weather: 'typhoon' },
    preDawn:   { label: '05:30 pre-dawn mist', hour: 5.5, elevation_m: 1000, weather: 'mist' },
    target:    { label: 'Inside the target, 02:00', hour: 2, elevation_m: 1000, weather: 'clear', enemy: 'target', enemyDist_m: 120 }
  };

  function defaults() {
    return {
      hour: 2, elevation_m: 1000, weather: 'clear', LAI: 4.5, canopyH_m: 15, understoryVis_m: 20,
      perch: 'subcanopy', opDist_m: 20, opPosture: 'lying', opState: 'injured', opSignal: 'tapCode',
      camera: 'hti256', mics: 6, heading: 'vio', secondPerch: false, radarPellet: false,
      enemy: 'patrol', enemyDist_m: 250, enemyDetector: 'energy', locals: true, animals: true,
      ai: 'hailo8', decisionP: 0.95,
      output: 'auto', moveAway_km: 3, coast_km: 30, relayAlt_m: 300, relayDist_m: 2000, radio: 'sf7bw250',
      lklRadius_km: 1, birds: 5, dwell_min: 30
    };
  }

  function merge(s) { var d = defaults(), k; for (k in (s || {})) if (s[k] !== undefined && s[k] !== null) d[k] = s[k]; return d; }
  function clamp(x, a, b) { return Math.max(a, Math.min(b, x)); }
  function pow10(x) { return Math.pow(10, x / 10); }
  function sumDb() { var t = 0; for (var i = 0; i < arguments.length; i++) if (arguments[i] > 0) t += pow10(arguments[i]); return 10 * log10(t); }
  function interp(table, f) {
    var ks = Object.keys(table).map(Number).sort(function (a, b) { return a - b; });
    if (f <= ks[0]) return table[ks[0]];
    for (var i = 1; i < ks.length; i++) if (f <= ks[i]) {
      var a = ks[i - 1], b = ks[i]; return table[a] + (table[b] - table[a]) * (f - a) / (b - a);
    }
    return table[ks[ks.length - 1]];
  }
  function nearestElev(e) { return [300, 1000, 1500].reduce(function (p, c) { return Math.abs(c - e) < Math.abs(p - e) ? c : p; }); }
  function bisect(fn, lo, hi) {   // largest x in [lo, hi] with fn(x) true, fn monotone true->false
    if (!fn(lo)) return 0;
    if (fn(hi)) return hi;
    for (var i = 0; i < 60; i++) { var m = (lo + hi) / 2; if (fn(m)) lo = m; else hi = m; }
    return lo;
  }
  function r1(x) { return Math.round(x * 10) / 10; }
  function r2(x) { return Math.round(x * 100) / 100; }
  function rp(p) { return p >= 0.99 ? Math.min(0.999, Math.round(p * 1000) / 1000) : r2(p); }   // keep 0.99x visible

  /* ---------------- environment ---------------- */
  function env(s) {
    var c = PARAMS.climate[nearestElev(s.elevation_m)];
    var h = ((s.hour % 24) + 24) % 24, f;
    if (h >= 5 && h <= 14) f = (1 - cos(PI * (h - 5) / 9)) / 2;
    else { var hh = h < 5 ? h + 24 : h; f = (1 + cos(PI * (hh - 14) / 15)) / 2; }
    var off = PARAMS.weatherTempOffset[s.weather] || 0;
    var tAmb = c.tMin + (c.tMax - c.tMin) * f + off * f;
    var wet = s.weather === 'rain' || s.weather === 'typhoon';
    var rh = wet || s.weather === 'mist' ? 98 : Math.round(c.rh + 10 * (1 - f) - 10 * f);
    var n = PARAMS.noise, bio;
    if (h >= 7 && h < 17) bio = n.day; else if (h >= 17 && h < 20) bio = n.dusk;
    else if (h >= 3 && h < 6) bio = n.predawn; else if (h >= 6 && h < 7) bio = n.dawn; else bio = n.night;
    bio += n.elevation[nearestElev(s.elevation_m)] || 0;
    var noise = sumDb(bio, n.rain[s.weather] || 0);
    var sun = h < 6.5 || h > 19 ? 0 : (h < 9 ? (h - 6.5) / 2.5 : (h <= 16.5 ? 1 : (19 - h) / 2.5));
    if (wet || s.weather === 'mist') sun *= 0.3;
    return { tAmbC: r1(tAmb), rh: Math.min(100, rh), noiseDbA: r1(noise), isDay: h >= 5.5 && h < 18.75,   // July sunrise ~05:20, sunset ~18:45
      sun: r2(sun), wet: wet, hour: h };
  }

  /* ---------------- INPUT: acoustic ---------------- */
  function perchH(s) { return (PARAMS.perches[s.perch] || PARAMS.perches.subcanopy).h; }
  function acousticSNR(sig, r, s, e) {
    var A = PARAMS.acoustic, h = perchH(s);
    var d = sqrt(r * r + h * h);
    var aAir = interp(A.alphaAir, sig.f), a = aAir + interp(A.alphaVeg, sig.f) * A.vegFactor;
    var loss = 20 * log10(Math.max(d, 1)) + (d > 20 ? a * d : aAir * d + (d > 10 ? 1 : 0));   // ISO 9613-2: 10-20 m of foliage ~1 dB
    var gArray = 10 * log10(s.mics || 6);
    var gBand = e.isDay ? sig.bandGainDay : sig.bandGainNight;
    return sig.SL - loss + gArray + gBand - e.noiseDbA;
  }
  function acousticRange(key, s, e) {
    var sig = PARAMS.acoustic.signals[key];
    if (!sig || sig.SL == null) return 0;
    return bisect(function (r) { return acousticSNR(sig, r, s, e) >= PARAMS.acoustic.detectSNR_dB; }, 0.5, 5000);
  }
  function acoustic(s, e) {
    var S = PARAMS.acoustic.signals, dt = PARAMS.acoustic.detectSNR_dB;
    var canSignal = s.opState !== 'unconscious' && s.opSignal !== 'none';
    var key = canSignal ? s.opSignal : 'none';
    var sig = S[key] || S.none;
    var snr = sig.SL != null ? acousticSNR(sig, s.opDist_m, s, e) : -99;
    var codeDetected = canSignal && snr >= dt;
    var coughSnr = acousticSNR(S.cough, s.opDist_m, s, e);
    var coughDetected = s.opState === 'injured' && coughSnr >= dt;
    var note;
    if (codeDetected) note = sig.label + ' heard: SNR ' + r1(snr) + ' dB at ' + Math.round(s.opDist_m) + ' m (needs 6 dB)';
    else if (canSignal) note = sig.label + ' lost in ' + e.noiseDbA + ' dBA: SNR ' + r1(snr) + ' dB at ' + Math.round(s.opDist_m) + ' m';
    else if (s.opState === 'unconscious') note = 'Unconscious: no briefed signal';
    else note = 'He stays silent';
    if (coughDetected) note += '; coughs heard (SNR ' + r1(coughSnr) + ' dB)';
    return {
      signal: key, signalLabel: sig.label, signalRange_m: Math.round(acousticRange(key, s, e)),
      tapRange_m: Math.round(acousticRange('tapCode', s, e)), whistleRange_m: Math.round(acousticRange('softWhistle', s, e)),
      coughRange_m: Math.round(acousticRange('cough', s, e)), voiceRange_m: Math.round(acousticRange('voice', s, e)),
      shoutRange_m: Math.round(acousticRange('shout', s, e)), rescueWhistleRange_m: Math.round(acousticRange('rescueWhistle', s, e)),
      snrAtOp_dB: r1(snr), codeDetected: !!codeDetected, coughDetected: !!coughDetected,
      detected: !!(codeDetected || coughDetected), note: note
    };
  }

  /* ---------------- INPUT: thermal ---------------- */
  function thermal(s, e) {
    var T = PARAMS.thermal, cam = T.cameras[s.camera] || T.cameras.hti256, h = perchH(s);
    var ifov = cam.hfov * D2R / cam.w;
    var range3 = T.target_m / (T.minPx * ifov), range2 = T.target_m / (2 * ifov);
    var k = e.wet ? T.clothingK.wet : T.clothingK.dry;
    var surf = e.tAmbC + k * (T.skinC - e.tAmbC);
    var bg = e.tAmbC + T.bgOffsetC.night + (T.bgOffsetC.day - T.bgOffsetC.night) * e.sun;
    var dT = surf - bg;
    var clutter = e.wet ? T.clutterC.wet : r1(T.clutterC.night + (T.clutterC.day - T.clutterC.night) * e.sun);
    var thr = Math.max(4 * cam.netd, 1.5 * clutter);
    function slant(r) { return sqrt(r * r + h * h); }
    function px(r) { return T.target_m / (slant(r) * ifov); }
    var beta = T.betaPerKm + (T.rainBetaPerKm[s.weather] || 0);
    function app(r) { return dT * exp(-beta * slant(r) / 1000); }
    function plos(r) {
      if (h >= s.canopyH_m) { var cz = cos(atan(r / h)); return exp(-T.G * T.clumping * s.LAI / Math.max(cz, 0.02)); }
      return exp(-slant(r) / Math.max(s.understoryVis_m, 1));
    }
    function ok(r) { return px(r) >= T.minPx && Math.abs(app(r)) >= thr && plos(r) >= T.minPLOS; }
    var effRange = bisect(ok, 0.5, Math.max(range3, 1));
    var r = s.opDist_m, pl = plos(r), pxa = px(r), ca = app(r);
    var contrastOK = Math.abs(ca) >= thr, pxOK = pxa >= T.minPx;
    var pDetect = (contrastOK && pxOK) ? pl : 0;
    var note;
    if (!contrastOK) note = 'Contrast ' + r1(ca) + ' C below clutter threshold ' + r1(thr) + ' C' + (e.sun > 0.5 ? ' (sun-warmed leaves are as warm as he is)' : (e.wet ? ' (wet clothing and leaves)' : ''));
    else if (!pxOK) note = r1(pxa) + ' px on him, needs ' + T.minPx;
    else if (pl < T.minPLOS) note = 'Canopy blocks the view: ' + Math.round(pl * 100) + '% chance of a clear line';
    else note = r1(pxa) + ' px, contrast ' + r1(ca) + ' C, ' + Math.round(pl * 100) + '% clear line of sight';
    return {
      camera: cam.label, range3px_m: Math.round(range3), range2px_m: Math.round(range2),
      effectiveRange_m: Math.round(effRange), pxAtOp: r1(pxa), contrastC: r1(dT), contrastAppC: r1(ca),
      clutterC: clutter, thresholdC: r1(thr), pLOS: r2(pl), pDetect: r2(pDetect),
      detected: pDetect >= T.minPLOS && pxOK && contrastOK, note: note
    };
  }

  function radar(s) {
    var R = PARAMS.radar;
    if (!s.radarPellet) return { vitalsRange_m: R.vitals_m, presenceRange_m: R.presence_m, detected: false, note: 'No pellet dropped' };
    return { vitalsRange_m: R.vitals_m, presenceRange_m: R.presence_m, detected: true,
      note: 'Pellet within 1 m: breathing ~' + (s.opState === 'unconscious' ? 10 : 16) + '/min and heart rate (MR60BHA2, 1.5 m)' };
  }

  /* ---------------- radio propagation ---------------- */
  function vegLoss(dveg) {
    var V = PARAMS.rf.veg, Am = V.A1 * Math.pow(PARAMS.rf.freqMHz, V.alpha);
    return dveg > 0 ? Am * (1 - exp(-V.gamma * dveg / Am)) : 0;
  }
  function baseLoss(d, hT, hR) {
    var f = PARAMS.rf.freqMHz, lam = 299.792458 / f;
    d = Math.max(d, 1);
    var fspl = 20 * log10(d) + 20 * log10(f) - 27.55;
    var db = 4 * hT * hR / lam;
    return d > db ? Math.max(fspl, 40 * log10(d) - 20 * log10(hT * hR)) : fspl;
  }
  function weissberger(d) {   // dB through d metres of trees (Weissberger 1982), F in GHz
    var F = PARAMS.rf.freqMHz / 1000;
    return d <= 14 ? 0.45 * Math.pow(F, 0.284) * d : 1.33 * Math.pow(F, 0.284) * Math.pow(d, 0.588);
  }
  function linkLoss(r, hA, hB, canopyH, bInForest) {
    var d = sqrt(r * r + (hB - hA) * (hB - hA));
    if (bInForest || hB < canopyH) return baseLoss(d, hA, hB) + weissberger(d);
    var cz = cos(atan(r / Math.max(hB - hA, 0.1)));
    return baseLoss(d, hA, hB) + vegLoss(Math.min(d, canopyH / Math.max(cz, 0.02)));
  }

  /* ---------------- PROCESSING: onboard AI decision gate ---------------- */
  function processing(s, e, inp) {
    var A = PARAMS.ai, LR = A.lr, th = inp.thermal, ac = inp.acoustic;
    var nOthers = (A.others[s.enemy] || 0) + (s.locals ? A.others.locals : 0);
    var prior = 1 / (1 + nOthers);
    var seen = th.detected, still = s.opPosture !== 'standing';
    var ev = [];
    if (seen) {
      var clear = th.pxAtOp >= PARAMS.thermal.shapePx;
      ev.push({ key: 'shape', label: clear ? 'Thermal: warm, human-sized, lying (clear shape)' : 'Thermal: warm, human-sized (few pixels)', lr: clear ? LR.shapeClear : LR.shapeFaint, t_min: 1 });
      if (s.enemy === 'none' || s.enemyDist_m > 30) ev.push({ key: 'alone', label: 'Alone: no other warm body within 30 m', lr: LR.alone, t_min: 1 });
      ev.push({ key: 'location', label: 'Off-trail, inside the circle around the parachute', lr: LR.location, t_min: 1 });
      if (still) {
        ev.push({ key: 'still30', label: 'Still for 30 min', lr: LR.still30, t_min: 30 });
        ev.push({ key: 'still120', label: 'Still for 2 h', lr: LR.still120, t_min: 120 });
      }
      if (s.radarPellet) ev.push({ key: 'breathing', label: 'Radar pellet: resting breathing rate', lr: LR.breathing, t_min: 45 });
    }
    if (ac.codeDetected) ev.push({ key: 'code', label: 'Acoustic: ' + ac.signalLabel, lr: LR.code, t_min: 10 });
    if (ac.coughDetected) A.coughTimes_min.forEach(function (t) { ev.push({ key: 'cough', label: 'Acoustic: cough or groan', lr: LR.cough, t_min: t }); });
    if (!seen && ac.detected) ev.push({ key: 'location', label: 'Sound comes from off-trail, inside the parachute circle', lr: LR.location, t_min: ac.codeDetected ? 10 : A.coughTimes_min[0] });
    ev.sort(function (a, b) { return a.t_min - b.t_min; });
    var lo = Math.log(prior / (1 - prior)), p = prior, decisionAt = null, pAt = null, usedCode = false, usedCough = false;
    var post = [{ t_min: 0, p: r2(prior), event: 'Prior: he is 1 of ' + (nOthers + 1) + ' people who may come into view' }];
    ev.forEach(function (x) {
      lo += Math.log(x.lr); p = 1 / (1 + exp(-lo));
      if (decisionAt === null) { if (x.key === 'code') usedCode = true; if (x.key === 'cough') usedCough = true; }
      post.push({ t_min: x.t_min, p: rp(p), event: x.label + ' (x' + x.lr + ')' });
      if (decisionAt === null && p >= s.decisionP) { decisionAt = x.t_min; pAt = rp(p); }
    });
    var fa, faNote;
    if (decisionAt === null) { fa = 'LOW'; faNote = 'Nothing sent. The gate keeps watching until P reaches ' + s.decisionP + '.'; }
    else if (usedCode) { fa = 'LOW'; faNote = 'Fired on the briefed code, which only he knows.'; }
    else if (usedCough) { fa = 'MEDIUM'; faNote = 'Fired on stillness and coughs; a sick or wounded enemy soldier could look the same.'; }
    else { fa = 'HIGH'; faNote = 'Fired on warm, still, alone and off-trail; a resting sentry or a sleeping farmer looks the same.'; }
    var tracks = [{ id: 1, kind: 'operator', label: 'Downed aviator', p_operator: rp(p), status: decisionAt !== null ? 'PROBABLE OPERATOR' : (seen || ac.detected ? 'CANDIDATE' : 'NOT DETECTED') }];
    var n = 2, i;
    var nPatrol = s.enemy === 'target' ? 8 : (s.enemy === 'patrol' ? 4 : 0);
    for (i = 0; i < nPatrol; i++) tracks.push({ id: n++, kind: 'patrol', label: 'Patrol member ' + (i + 1), p_operator: 0.01, status: 'UNKNOWN: moving with others on the trail' });
    if (s.locals) for (i = 0; i < 2; i++) tracks.push({ id: n++, kind: 'local', label: 'Person at the farm ' + (i + 1), p_operator: 0.02, status: 'UNKNOWN: near the farm' });
    if (s.animals) [['muntjac', 'Muntjac'], ['macaque', 'Macaque troop'], ['boar', 'Wild boar']].forEach(function (a) {
      tracks.push({ id: n++, kind: a[0], label: a[1], p_operator: 0, status: 'ANIMAL: size and gait' });
    });
    var dev = A.devices[s.ai] || A.devices.hailo8;
    return {
      device: { label: dev.label, note: dev.note }, prior: r2(prior),
      priorNote: nOthers + ' other people may come into view (patrol, troops, farmers); animals are ruled out by shape and gait',
      evidence: ev, posterior: post, decision: decisionAt !== null ? 'TRANSMIT' : 'KEEP WATCHING',
      decisionAt_min: decisionAt, pAtDecision: pAt, threshold: s.decisionP, finalP: rp(p), falseAlarm: fa, falseAlarmNote: faNote, tracks: tracks
    };
  }

  /* ---------------- OUTPUT: what to send and how ---------------- */
  function airtime(bytes, sf, bwHz, crDenom) {   // Semtech AN1200.13
    sf = sf || 7; bwHz = bwHz || 250000; var cr = (crDenom || 5) - 4;
    var tsym = Math.pow(2, sf) / bwHz * 1000, de = (tsym > 16) ? 1 : 0;
    var nPay = 8 + Math.max(Math.ceil((8 * bytes - 4 * sf + 28 + 16) / (4 * (sf - 2 * de))) * (cr + 4), 0);
    return Math.round(((8 + 4.25) * tsym + nPay * tsym) * 10) / 10;
  }
  function accuracy(s, inp) {
    var F = PARAMS.fix, h = perchH(s), r = s.opDist_m, th = inp.thermal, ac = inp.acoustic;
    var head = F.headingSigma_deg[s.heading] || 8;
    var sAz = sqrt(F.doaSigma_deg * F.doaSigma_deg + head * head) * D2R;
    var method, rangeErr, crossErr;
    if (s.secondPerch && (th.detected || ac.detected)) { method = 'Two perches, crossing bearings'; rangeErr = r * sAz; crossErr = r * sAz; }
    else if (th.detected) { method = 'Thermal pixel to ground'; rangeErr = F.tiltSigma_deg * D2R * (r * r + h * h) / h; crossErr = r * head * D2R; }
    else if (ac.detected) { method = 'Sound bearing + downward angle from ' + h + ' m'; rangeErr = F.doaSigma_deg * D2R * (r * r + h * h) / h; crossErr = r * sAz; }
    else return { method: 'No fix', ce_m: null, relErr_m: null, ownPosErr_m: null, meets30m: false };
    var rel = sqrt(rangeErr * rangeErr + crossErr * crossErr), own = F.ownPos_m[s.heading] || 30;
    var ce = sqrt(rel * rel + own * own);
    return { method: method, relErr_m: r1(rel), ownPosErr_m: own, ce_m: r1(ce), meets30m: ce <= 30 };
  }
  function outputs(s, e, proc) {
    var R = PARAMS.rf, radio = R.radios[s.radio] || R.radios.sf7bw250, h = perchH(s), O = PARAMS.output;
    var hE = s.enemy === 'target' ? R.enemy.h_target : R.enemy.h_patrol;
    var thr = R.enemy.thr[s.enemyDetector] || R.enemy.thr.energy;
    var under = h < s.canopyH_m;
    function heardTo(hTx, tx, inForest) { return bisect(function (D) { return tx - linkLoss(D, hE, hTx, s.canopyH_m, inForest) >= thr; }, 1, 50000); }
    var at48 = airtime(PARAMS.sizes.FIX, radio.sf, radio.bw, 5), sbd = PARAMS.link.iridium.latency_s;
    // Eagle -> relay bird loitering relayDist away at relayAlt
    var dRel = sqrt(s.relayDist_m * s.relayDist_m + s.relayAlt_m * s.relayAlt_m);
    var Lrel = baseLoss(dRel, h, s.relayAlt_m);
    if (under) Lrel += vegLoss((s.canopyH_m - h) / Math.max(Math.sin(atan(s.relayAlt_m / Math.max(s.relayDist_m, 1))), 0.02));
    var txRel = clamp(radio.sens + R.powerMargin_dB + Lrel, R.txMin_dBm, R.txMax_dBm);
    // straight up to the relay bird when it comes overhead
    var Lup = baseLoss(s.relayAlt_m - h, h, s.relayAlt_m) + (under ? vegLoss(s.canopyH_m - h) : 0);
    var txUp = clamp(radio.sens + R.powerMargin_dB + Lup, R.txMin_dBm, R.txMax_dBm);
    var enemyToPerch = s.enemy === 'none' ? Infinity : Math.max(s.enemyDist_m - s.opDist_m, 1);
    var perchR = heardTo(h, txRel, under), upR = heardTo(h, txUp, under);
    var away = s.moveAway_km * 1000, flyAway = away / O.speed_mps;
    var awayR = heardTo(20, clamp(radio.sens + R.powerMargin_dB + baseLoss(dRel, 20, s.relayAlt_m), R.txMin_dBm, R.txMax_dBm), false);
    var emitNear = Math.round(sqrt(s.opDist_m * s.opDist_m + h * h));
    var opts = [
      { key: 'perch', label: 'Burst from the perch', latency_s: r1(0.5 + at48 / 1000 + sbd + 1), emitDistFromOp_m: emitNear,
        interceptRadius_m: Math.round(perchR), tx_dBm: r1(txRel), enemyHears: enemyToPerch <= perchR,
        note: 'FIX at ' + r1(txRel) + ' dBm to the relay ' + (s.relayDist_m / 1000) + ' km away; heard on the ground to ' + Math.round(perchR) + ' m' },
      { key: 'whisper', label: 'Whisper to the relay bird overhead', latency_s: r1(0.5 + at48 / 1000 + flyAway + sbd + 1), emitDistFromOp_m: emitNear,
        interceptRadius_m: Math.round(upR), tx_dBm: r1(txUp), enemyHears: enemyToPerch <= upR,
        note: 'Minimum power straight up (' + r1(txUp) + ' dBm); the relay flies ' + s.moveAway_km + ' km before its satellite burst' },
      { key: 'moveAway', label: 'Fly ' + s.moveAway_km + ' km, then burst', latency_s: r1(flyAway + 0.5 + at48 / 1000 + sbd + 1), emitDistFromOp_m: Math.round(away),
        interceptRadius_m: Math.round(awayR), tx_dBm: null, enemyHears: false,
        note: 'Any direction finding points ' + s.moveAway_km + ' km from him' },
      { key: 'courier', label: 'Courier to the coast, no radio over land', latency_s: Math.round(s.coast_km * 1000 / O.speed_mps), emitDistFromOp_m: null,
        interceptRadius_m: 0, tx_dBm: null, enemyHears: false, note: 'Flies ' + s.coast_km + ' km to the Nest; nothing to intercept' }
    ];
    opts.forEach(function (o) {
      if (o.key === 'courier') o.risk = 'NONE';
      else if (o.key === 'moveAway') o.risk = 'LOW';
      else o.risk = o.enemyHears ? 'HIGH' : (s.enemy === 'none' ? 'LOW' : (o.key === 'whisper' ? 'LOW' : 'MEDIUM'));
    });
    var rank = { NONE: 0, LOW: 1, MEDIUM: 2, HIGH: 3 };
    var urgent = s.opState !== 'conscious', maxLatency = urgent ? 15 * 60 : 120 * 60;
    var chosen = null, reason;
    if (proc.decision !== 'TRANSMIT') reason = 'Nothing to send yet: the AI keeps watching until P reaches ' + s.decisionP;
    else if (s.output !== 'auto') { chosen = opts.filter(function (o) { return o.key === s.output; })[0]; reason = 'Chosen by hand'; }
    else {
      var fits = opts.filter(function (o) { return o.latency_s <= maxLatency; });
      fits.sort(function (a, b) { return rank[a.risk] - rank[b.risk] || (b.emitDistFromOp_m || 1e9) - (a.emitDistFromOp_m || 1e9) || a.latency_s - b.latency_s; });
      chosen = fits[0] || opts[2];
      reason = (urgent ? 'He is hurt, so the report must leave within 15 min' : 'He is mobile, so up to 2 h is acceptable') +
        '; lowest risk that fits: ' + chosen.label.toLowerCase() +
        (opts[0].enemyHears ? '. A burst from the perch would reach the enemy ' + Math.round(enemyToPerch) + ' m away' : '');
    }
    return {
      chosen: chosen ? chosen.key : null, chosenReason: reason, options: opts,
      message: { name: 'FIX', bytes: PARAMS.sizes.FIX, airtime_ms: at48,
        fields: ['latitude', 'longitude', 'altitude', 'circular error', 'how found (thermal / sound)', 'P(operator)', 'state (still / moving)', 'time'] }
    };
  }

  /* ---------------- SEARCH: how many perch stops to cover the circle ---------------- */
  function search(s, inp) {
    var r = Math.max(inp.acoustic.codeDetected || (s.opSignal !== 'none' && s.opState !== 'unconscious') ? inp.acoustic.signalRange_m : 0,
                     inp.thermal.effectiveRange_m);
    if (r <= 0) return { perchRadius_m: 0, perchesNeeded: null, hoursToCover: null, note: 'No sensor reaches him from a perch in these conditions' };
    var need = Math.ceil(Math.pow(s.lklRadius_km * 1000 / r, 2));
    var perHour = 60 / (s.dwell_min + PARAMS.search.hop_min);
    var hours = need / (s.birds * perHour);
    var nights = hours / PARAMS.search.nightHours;
    return { perchRadius_m: Math.round(r), perchesNeeded: need, hoursToCover: r1(hours),
      note: need + ' perch stops of ' + s.dwell_min + ' min for a ' + s.lklRadius_km + ' km circle; ' + s.birds + ' birds need ' + r1(hours) + ' h (' + (nights <= 1 ? 'one night' : r1(nights) + ' nights') + ')' };
  }

  /* ---------------- evaluate ---------------- */
  function evaluate(input) {
    var s = merge(input), e = env(s);
    var inp = { thermal: thermal(s, e), acoustic: acoustic(s, e), radar: radar(s) };
    var proc = processing(s, e, inp);
    var out = outputs(s, e, proc);
    out.accuracy = accuracy(s, inp);
    var srch = search(s, inp);
    var by = [];
    if (inp.thermal.detected) by.push('Thermal');
    if (inp.acoustic.detected) by.push('Acoustic');
    if (inp.radar.detected) by.push('Radar');
    var status = proc.decision === 'TRANSMIT' ? 'REPORTED' : (by.length ? 'DETECTED' : 'NOT FOUND');
    var ch = out.options.filter(function (o) { return o.key === out.chosen; })[0];
    var tl = [{ step: 'Listen', t_s: 0, note: 'Perched, motor off: thermal and microphones only, nothing transmitted' }];
    var dec = proc.decisionAt_min;
    var upTo = proc.evidence.filter(function (x) { return dec == null || x.t_min <= dec; });
    if (upTo.length) tl.push({ step: 'Detect', t_s: upTo[0].t_min * 60, note: upTo[0].label });
    var mids = upTo.filter(function (x) { return x.t_min > upTo[0].t_min && (dec == null || x.t_min < dec); });
    tl.push({ step: 'Track', t_s: mids.length ? mids[mids.length - 1].t_min * 60 : (upTo.length ? upTo[0].t_min * 60 : null),
      note: 'Tracks every warm body and sound' + (proc.tracks.length > 1 ? ' (' + proc.tracks.length + ' tracks)' : '') });
    var key = dec != null ? upTo.filter(function (x) { return x.t_min === dec; }).slice(-1)[0] : proc.evidence.slice(-1)[0];
    tl.push({ step: 'Evidence', t_s: key ? key.t_min * 60 : null, note: key ? key.label + ' (x' + key.lr + ')' : 'Nothing yet' });
    tl.push({ step: 'Decide', t_s: dec != null ? dec * 60 : null,
      note: dec != null ? 'TRANSMIT: P ' + proc.pAtDecision + ' reached ' + proc.threshold : 'KEEP WATCHING: P ' + proc.finalP + ' is below ' + proc.threshold });
    var tReport = null;
    if (ch) {
      var t0 = proc.decisionAt_min * 60, fly = Math.round(s.moveAway_km * 1000 / PARAMS.output.speed_mps);
      var burst = 0.5 + out.message.airtime_ms / 1000, sbd = PARAMS.link.iridium.latency_s;
      var fixNote = 'One ' + PARAMS.sizes.FIX + '-byte FIX, ' + out.message.airtime_ms + ' ms';
      tReport = Math.round(t0 + ch.latency_s);
      if (ch.key === 'perch') {
        tl.push({ step: 'Transmit', t_s: r1(t0 + burst), note: fixNote + ' from the perch at ' + ch.tx_dBm + ' dBm' });
      } else if (ch.key === 'whisper') {
        tl.push({ step: 'Transmit', t_s: r1(t0 + burst), note: fixNote + ' straight up at ' + ch.tx_dBm + ' dBm' });
        tl.push({ step: 'Move away', t_s: r1(t0 + burst + fly), note: 'The relay bird flies ' + s.moveAway_km + ' km before it transmits' });
      } else if (ch.key === 'moveAway') {
        tl.push({ step: 'Move away', t_s: t0 + fly, note: 'Eagle flies ' + s.moveAway_km + ' km before it transmits' });
        tl.push({ step: 'Transmit', t_s: r1(t0 + fly + burst), note: fixNote + ', ' + s.moveAway_km + ' km from him' });
      } else {
        tl.push({ step: 'Move away', t_s: tReport, note: 'Courier reaches the Nest after ' + s.coast_km + ' km; nothing transmitted over land' });
      }
      if (ch.key !== 'courier') tl.push({ step: 'Relay', t_s: tReport - 1, note: 'Iridium SBD burst from the relay, ~' + sbd + ' s' });
      tl.push({ step: 'Marker', t_s: tReport, note: 'Marker on ATAK at the JPRC / Nest, error ' + out.accuracy.ce_m + ' m' });
    } else {
      ['Transmit', 'Relay', 'Marker'].forEach(function (k) { tl.push({ step: k, t_s: null, note: 'Not yet: below the decision threshold' }); });
    }
    return {
      settings: s, env: e, input: inp, processing: proc, output: out, search: srch,
      find: { status: status, by: by, identity: proc.decision === 'TRANSMIT' ? 'PROBABLE OPERATOR (P ' + proc.finalP + ')' : 'UNCONFIRMED' },
      timeline: tl, timeToReport_s: tReport
    };
  }

  var EagleModel = { version: 2, PARAMS: PARAMS, presets: PRESETS, defaults: defaults, evaluate: evaluate, airtime: airtime,
    _internals: { env: env, acousticRange: acousticRange, linkLoss: linkLoss, vegLoss: vegLoss, baseLoss: baseLoss, weissberger: weissberger } };
  if (typeof module !== 'undefined' && module.exports) module.exports = EagleModel;
  if (root) root.EagleModel = EagleModel;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
