// Prints the v2 model's numbers for the doc tab: node model_table.js
const M = require('./model.js');
const base = M.defaults();
const fmtT = s => s == null ? 'never' : (s < 120 ? s.toFixed(0) + ' s' : (s / 60).toFixed(1) + ' min');
function line(label, over) {
  const o = M.evaluate(Object.assign({}, base, over));
  const i = o.input, p = o.processing, out = o.output, ch = out.options.find(x => x.key === out.chosen);
  return [
    `== ${label}`,
    `  env ${o.env.tAmbC}C ${o.env.rh}% ${o.env.noiseDbA} dBA sun ${o.env.sun}`,
    `  thermal eff ${i.thermal.effectiveRange_m} m, px ${i.thermal.pxAtOp}, dT ${i.thermal.contrastAppC} vs ${i.thermal.thresholdC}, pLOS ${i.thermal.pLOS}, seen ${i.thermal.detected}`,
    `  acoustic: tap ${i.acoustic.tapRange_m} whistle ${i.acoustic.whistleRange_m} cough ${i.acoustic.coughRange_m} voice ${i.acoustic.voiceRange_m} shout ${i.acoustic.shoutRange_m} | code ${i.acoustic.codeDetected} cough ${i.acoustic.coughDetected}`,
    `  processing: prior ${p.prior} -> ${p.finalP}; ${p.decision} at ${p.decisionAt_min} min; FA ${p.falseAlarm}`,
    `    ` + p.posterior.map(x => `${x.t_min}m:${x.p}`).join('  '),
    `  output: ${out.chosen} | ${out.chosenReason}`,
    `    ` + out.options.map(x => `${x.key}: ${fmtT(x.latency_s)}, emit ${x.emitDistFromOp_m} m from him, heard to ${x.interceptRadius_m} m, ${x.risk}${x.tx_dBm != null ? ', ' + x.tx_dBm + ' dBm' : ''}`).join('\n    '),
    `  accuracy ${out.accuracy.ce_m} m (rel ${out.accuracy.relErr_m}, own ${out.accuracy.ownPosErr_m}) ${out.accuracy.method} meets30=${out.accuracy.meets30m}`,
    `  search: ${o.search.note}`,
    `  find ${o.find.status} by ${o.find.by.join('+')} ${o.find.identity}; report at ${fmtT(o.timeToReport_s)}`
  ].join('\n');
}
for (const [k, p] of Object.entries(M.presets)) console.log(line(p.label, p));
console.log(line('silent, injured', { opSignal: 'none' }));
console.log(line('unconscious, silent', { opState: 'unconscious' }));
console.log(line('unconscious + radar pellet', { opState: 'unconscious', radarPellet: true }));
console.log(line('conscious, hiding, tap code', { opState: 'conscious' }));
console.log(line('snag perch, 60 m, tap code', { perch: 'snag', opDist_m: 60 }));
console.log(line('snag perch, 100 m, tap code', { perch: 'snag', opDist_m: 100 }));
console.log(line('magnetometer heading', { heading: 'mag' }));
console.log('--- timeline (defaults)');
M.evaluate(base).timeline.forEach(t => console.log(`  ${t.step.padEnd(10)} ${fmtT(t.t_s).padStart(9)}  ${t.note}`));
