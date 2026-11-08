const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const cp = require('node:child_process');
const N = require('./navigation-model.js');
function run(scenario, seconds=180, originKnown=true) {
  let s=N.create(); for(let i=0;i<seconds*10;i++)s=N.step(s,0.1,{scenario,originKnown}); return s;
}
for(const scenario of ['clear','jamming','spoofing']) {
  const s=run(scenario);assert(s.complete,scenario);assert(!s.stopped);assert(Math.hypot(s.x,s.y)<0.001);
}
const loss=run('backup-loss');assert(loss.stopped);assert(!loss.complete);assert(loss.t>=29.9&&loss.t<30.3);
assert.equal(loss.mode,'Navigation unavailable');assert.deepEqual(N.step(loss,0.1),loss);
assert(run('canopy').stopped);assert.equal(run('jamming',15,false).georeferenced,false);
assert.equal(run('spoofing',15).gps,'inconsistent');assert(run('jamming',15).distance>run('jamming',10).distance);
assert.throws(()=>N.step(N.create(),NaN));
// Ensure the embedded navigation addition has not changed the existing physics model.
const base=cp.execFileSync('git',['show','HEAD:simulator/index.html'],{encoding:'utf8'});
const current=fs.readFileSync(__dirname+'/index.html','utf8');
const block=s=>s.replace(/\r\n/g,'\n').match(/<script id="eagle-model">([\s\S]*?)<\/script>/)[1];
assert.equal(block(current),block(base));
const sandbox={module:{exports:{}}};vm.runInNewContext(block(current),sandbox);
const M=sandbox.module.exports;for(const p of Object.values(M.presets))assert(M.evaluate({...M.defaults(),...p}).find);
console.log('PASS: route completion, GPS fallback, spoofing scenario, backup-loss stop, canopy degradation, local origin, invalid dt and existing sensor-model regression');
