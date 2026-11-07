#!/usr/bin/env node
/*
 * run-model.mjs: run the Eagle physics model headless, straight out of index.html.
 *
 * The simulator keeps all of its physics in one pure-function block
 * (<script id="eagle-model">, no DOM). This script pulls that block out of
 * index.html and evaluates it in Node, so the numbers in the docs can be
 * reproduced and checked without opening a browser.
 *
 * Usage (Node 18+):
 *   node simulator/run-model.mjs                     # table for all four presets
 *   node simulator/run-model.mjs typhoon             # one preset
 *   node simulator/run-model.mjs night opDist_m=80   # preset + overrides
 *   node simulator/run-model.mjs night --json        # full result object
 *
 * Override keys are the same as the settings panel (see docs/simulator.md, "Inputs").
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, 'index.html'), 'utf8');
const block = html.match(/<script id="eagle-model">([\s\S]*?)<\/script>/);
if (!block) { console.error('eagle-model block not found in index.html'); process.exit(1); }

const sandbox = { module: { exports: {} } };
vm.createContext(sandbox);
vm.runInContext(block[1], sandbox);
const M = sandbox.module.exports;

const args = process.argv.slice(2);
const asJson = args.includes('--json');
const overrides = {};
const names = [];
for (const a of args) {
  if (a === '--json') continue;
  const eq = a.indexOf('=');
  if (eq > 0) {
    const k = a.slice(0, eq), v = a.slice(eq + 1);
    overrides[k] = v === 'true' ? true : v === 'false' ? false : (v !== '' && !isNaN(+v) ? +v : v);
  } else names.push(a);
}
const presetKeys = names.length && names[0] !== 'all' ? names : Object.keys(M.presets);
for (const k of presetKeys) if (!M.presets[k]) { console.error(`Unknown preset "${k}". Presets: ${Object.keys(M.presets).join(', ')}`); process.exit(1); }

const runs = presetKeys.map(k => {
  const { label, ...p } = M.presets[k];
  return { key: k, label, r: M.evaluate({ ...p, ...overrides }) };
});

if (asJson) {
  console.log(JSON.stringify(runs.length === 1 ? runs[0].r : Object.fromEntries(runs.map(x => [x.key, x.r])), null, 2));
  process.exit(0);
}

const v = x => (x === null || x === undefined ? '-' : String(x));
const rows = [
  ['Ambient', r => `${r.env.tAmbC} °C, ${r.env.rh}% RH`],
  ['Ambient noise', r => `${r.env.noiseDbA} dBA`],
  ['Result', r => `**${r.find.status}**${r.find.by.length ? ' (' + r.find.by.join(' + ') + ')' : ''}`],
  ['Identity', r => r.find.identity],
  ['Acoustic: signal heard at operator?', r => `${r.acoustic.detected ? 'yes' : 'no'} (SNR ${r.acoustic.snrAtOp_dB} dB)`],
  ['Acoustic: soft-whistle range', r => `${r.acoustic.whistleRange_m} m`],
  ['Acoustic: rescue-whistle range', r => `${r.acoustic.rescueWhistleRange_m} m`],
  ['Thermal: apparent contrast vs threshold', r => `${r.thermal.contrastAppC} °C vs ${r.thermal.thresholdC} °C`],
  ['Thermal: P(clear line of sight)', r => `${Math.round(r.thermal.pLOS * 100)}%`],
  ['Thermal: detected?', r => (r.thermal.detected ? 'yes' : 'no')],
  ['RF: tag reply range (Eagle poll)', r => `${r.rf.replyRange_m} m`],
  ['RF: tag reply power', r => `${r.rf.txForEagle_dBm} dBm`],
  ['RF: enemy intercept radius', r => `${r.rf.interceptRange_m} m`],
  ['RF: EMCON hold?', r => (r.rf.emconHold ? `yes, ${r.rf.holdFor_s} s` : 'no')],
  ['Fix method', r => r.fix.method],
  ['Fix error (CE)', r => (r.fix.ce_m == null ? '-' : `${r.fix.ce_m} m`)],
  ['Time to ATAK marker', r => (r.timeToMarker_s == null ? '-' : `${r.timeToMarker_s} s`)],
];

const ov = Object.keys(overrides).length ? ` · overrides: ${Object.entries(overrides).map(([k, x]) => `${k}=${x}`).join(', ')}` : '';
console.log(`Eagle model · defaults + preset${ov}\n`);
console.log(`| Output | ${runs.map(x => x.label).join(' | ')} |`);
console.log(`|---|${runs.map(() => '---').join('|')}|`);
for (const [name, f] of rows) console.log(`| ${name} | ${runs.map(x => v(f(x.r))).join(' | ')} |`);
